from __future__ import annotations

import json
import os
import threading
from fractions import Fraction

import encode.ffmpeg_exec as ffmpeg_exec
from encode.ffmpeg_exec import si, NO_WIN, _sp_check_output, _ffmpeg_has_filter
from encode.media_math import get_media_type

# =====================================================================
# Cached ffprobe, HDR detection, per-encoder rate-control arg building,
# and the video-metadata/bitrate math that reads off the cached probe.
# =====================================================================

_MEDIA_PROBE_CACHE: dict = {}
_MEDIA_PROBE_LOCK = threading.Lock()


def _probe_media_cached(path: str) -> dict:
    """
    One full ffprobe (-show_streams -show_format) per (path, mtime, size),
    cached. Every metadata question in a job (dimensions, duration, HDR flags,
    codec name, audio meta) reads from this instead of re-running ffprobe —
    previously each retry re-probed the same file several times.
    """
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), int(st.st_mtime), int(st.st_size))
    except Exception:
        key = (str(path), 0, 0)
    with _MEDIA_PROBE_LOCK:
        hit = _MEDIA_PROBE_CACHE.get(key)
        if hit is not None:
            return hit
    data: dict = {}
    try:
        out = _sp_check_output(
            [ffmpeg_exec.FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
            text=True, startupinfo=si, creationflags=NO_WIN)
        data = json.loads(out or "{}") or {}
    except Exception:
        data = {}
    with _MEDIA_PROBE_LOCK:
        if len(_MEDIA_PROBE_CACHE) > 64:  # small LRU-ish cap
            _MEDIA_PROBE_CACHE.clear()
        _MEDIA_PROBE_CACHE[key] = data
    return data


def _probe_video_stream(path: str) -> dict:
    """First video stream dict from the cached probe (or {})."""
    for s in (_probe_media_cached(path).get("streams") or []):
        if (s or {}).get("codec_type") == "video":
            return s
    return {}


def get_video_metadata(filepath: str):

    if get_media_type(filepath) != "video":
        return 0.0, 0, 0, 0, 0.0

    data = _probe_media_cached(filepath)
    fmt_info = data.get("format", {}) or {}
    stream_info = _probe_video_stream(filepath) or {}
    try:
        duration = float(fmt_info.get("duration", 0) or 0)
    except Exception:
        duration = 0.0
    width = int(stream_info.get("width", 1280) or 1280)
    height = int(stream_info.get("height", 720) or 720)
    try:
        bitrate = int(stream_info.get("bit_rate") or fmt_info.get("bit_rate") or 5_000_000)
    except Exception:
        bitrate = 5_000_000
    framerate_raw = stream_info.get("avg_frame_rate", "30/1")
    try:
        framerate = float(Fraction(framerate_raw))
    except Exception:
        framerate = 30.0
    return duration, width, height, bitrate, framerate


def extract_video_duration(path):
    # Read duration from the shared cached probe (one ffprobe per file/mtime/size)
    # instead of spawning a fresh ffprobe on every call — this is hit repeatedly
    # in the encode path (metadata fallback, shrink probe, VMAF windows).
    try:
        fmt = (_probe_media_cached(path).get("format") or {})
        d = float(fmt.get("duration") or 0.0)
        return d if d > 0 else None
    except Exception:
        return None


def calculate_bitrate(duration, target_bytes, audio_bitrate, input_path=None):

    if not duration or duration <= 0:
        if input_path:
            probed = extract_video_duration(input_path)
            if probed and probed > 0:
                duration = probed

    if not duration or duration <= 0:
        duration = 60.0

    video_bits = (target_bytes * 8) - (audio_bitrate * duration)
    video_bits = max(video_bits, 0)

    return max(int(video_bits / duration), 100_000)


def _is_hdr_source(vstream: dict) -> bool:
    """
    Returns True if the video stream carries HDR or 10-bit metadata that should be preserved.
    Checks pixel format depth, BT.2020 color primaries, and HDR transfer functions (PQ/HLG).
    """
    pf = (vstream.get("pix_fmt") or "").lower()
    cp = (vstream.get("color_primaries") or "").lower()
    ct = (vstream.get("color_transfer") or "").lower()
    return bool(
        any(x in pf for x in ("10le", "10be", "12le", "12be", "p010", "p016")) or
        cp in ("bt2020", "bt2020nc", "bt2020c") or
        ct in ("smpte2084", "arib-std-b67", "smpte428", "bt2020-10", "bt2020-12")
    )


def _hdr_pixel_fmt(vcodec: str) -> str | None:
    """
    Return the appropriate 10-bit pixel format for encoders that support it,
    or None if the codec is 8-bit only (e.g. libx264).
    """
    _ten_bit_encoders = {"libx265", "libsvtav1", "libaom-av1", "libvpx-vp9"}
    return "yuv420p10le" if vcodec in _ten_bit_encoders else None


def _probe_is_hdr_path(input_path: str) -> bool:
    """Quick HDR/BT.2020 check for a file path (wraps _is_hdr_source)."""
    try:
        return _is_hdr_source(_probe_video_stream(input_path))
    except Exception:
        return False


# BT.2020 PQ/HLG -> BT.709 SDR tone-map. Needed when the target codec is 8-bit
# (e.g. x264): without it HDR sources come out washed-out/grey.
_HDR_TONEMAP_VF = (
    "zscale=transfer=linear:npl=100,format=gbrpf32le,"
    "tonemap=hable:desat=0,"
    "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv,format=yuv420p"
)


def _hdr_tonemap_vf(vcodec: str) -> str | None:
    """
    Return a tone-map filter string when downconverting HDR to an 8-bit codec,
    or None if the codec keeps 10-bit (HEVC/AV1/VP9 preserve HDR natively) or the
    zscale/tonemap filters aren't available in this ffmpeg build.
    """
    if _hdr_pixel_fmt(vcodec):  # codec can carry 10-bit -> preserve, don't tonemap
        return None
    try:
        if not (_ffmpeg_has_filter("zscale") and _ffmpeg_has_filter("tonemap")):
            return None
    except Exception:
        return None
    return _HDR_TONEMAP_VF


# Map x264-style preset names onto SVT-AV1's numeric scale (0=slowest/best .. 13=fastest).
_SVT_PRESET_MAP = {
    "placebo": "2", "veryslow": "2", "slower": "3", "slow": "4",
    "medium": "6", "fast": "8", "faster": "9", "veryfast": "10",
    "superfast": "11", "ultrafast": "12",
}


def _svt_preset_for_duration(preset_num, duration_s: float) -> int:
    """
    Clamp an SVT-AV1 preset (higher number = faster) to a speed FLOOR based on
    clip length. SVT preset semantics don't match x264/x265: a codec race that
    switches x265->AV1 carries x265's "slower" over, which maps to SVT preset 3 —
    ~9 fps on a 33-min 1440p clip, i.e. HOURS per pass, and the wall-clock budget
    can't interrupt a single in-progress pass. Long clips get a faster floor so
    one job stays tractable; short clips may still use the slow presets.
    """
    try:
        p = int(preset_num)
    except Exception:
        p = 6
    d = float(duration_s or 0.0)
    if d <= 0:
        return p
    if d > 1200:   floor = 7   # >20 min
    elif d > 600:  floor = 6   # 10-20 min
    elif d > 240:  floor = 5   # 4-10 min
    else:          floor = 2   # short clips: any preset is fine
    return max(p, floor)

# Map x264-style preset names onto libaom's cpu-used scale (0=slowest/best .. 8=fastest).
_AOM_CPU_USED_MAP = {
    "placebo": "2", "veryslow": "2", "slower": "3", "slow": "3",
    "medium": "4", "fast": "5", "faster": "6", "veryfast": "6",
    "superfast": "8", "ultrafast": "8",
}

_X265_VALID_TUNES = {"grain", "psnr", "ssim", "fastdecode", "zerolatency", "animation"}


def _codec_video_args(
    vcodec: str,
    *,
    preset: str,
    tune: str | None,
    crf: int | None,
    v_bitrate: int | None,
    fps: float | None = None,
    advanced_options: dict | None = None,
) -> tuple[list[str], list[str]]:
    """
    Build (video_args, ratecontrol_args) tuned per encoder so every codec is
    driven with its proper knobs:

      - software encoders get psy/AQ tuning in BOTH bitrate and CRF modes
      - libvpx-vp9 / libaom-av1 CRF mode gets the mandatory `-b:v 0` for true
        constant-quality (otherwise libvpx silently caps at its default bitrate)
      - libsvtav1 gets a numeric preset (string presets are rejected by SVT)
      - NVENC uses -cq, QSV uses -global_quality (ICQ), AMF uses CQP — none of
        them understand -crf
      - 10-bit encoding is enabled by default for HEVC/AV1 (better compression
        and less banding even for 8-bit sources); opt out via
        advanced_options["force_8bit"]
    """
    opts = advanced_options or {}
    use_bitrate = v_bitrate is not None and int(v_bitrate) > 0
    crf_i = int(crf if crf is not None else 22)
    g_frames = max(48, min(300, int(round(float(fps or 30.0) * 10))))

    def _abr(mult_max: float = 1.10, mult_buf: float = 2.0) -> list[str]:
        b = int(v_bitrate)
        return ["-b:v", str(b), "-maxrate", str(int(b * mult_max)), "-bufsize", str(int(b * mult_buf))]

    ten_bit = not bool(opts.get("force_8bit"))
    pix10 = ["-pix_fmt", "yuv420p10le"]

    if vcodec == "libx264":
        v = ["-preset", preset]
        if tune:
            v += ["-tune", str(tune)]
        params = str(opts.get("x264_params") or os.environ.get("BC_X264_PARAMS", "")).strip()
        if not params:
            params = ("aq-mode=3:aq-strength=1.00:mbtree=1:deblock=-1,-1:psy-rd=1.10,0.15"
                      ":rc-lookahead=60:qcomp=0.70:bframes=8:ref=5:trellis=2")
        v += ["-x264-params", params]
        return v, (_abr() if use_bitrate else ["-crf", str(crf_i)])

    if vcodec == "libx265":
        v = ["-preset", preset]
        _tn = str(tune or "").lower()
        if _tn in _X265_VALID_TUNES:
            v += ["-tune", _tn]
        params = (str(opts.get("x265_params") or "").strip()
                  or os.environ.get("BC_X265_PARAMS", "").strip())
        if not params:
            # Measured: x265's own defaults already maximise VMAF — psy-rd/aq
            # overrides (the old default here) traded ~2 VMAF for subjective
            # sharpness and LOST a head-to-head at equal size. Keep only the
            # neutral analysis-depth bumps that don't touch the psy/aq model.
            params = "rc-lookahead=60:bframes=8:ref=5"
        v += ["-x265-params", params]
        if ten_bit:
            v += pix10
        return v, (_abr() if use_bitrate else ["-crf", str(crf_i)])

    _max_mode = str(opts.get("quality_mode") or "").lower() == "max"

    if vcodec == "libsvtav1":
        _svt_p = _SVT_PRESET_MAP.get(str(preset).lower(), "6")
        if _max_mode:
            _svt_p = str(min(int(_svt_p), 4))
        # Never let a long clip run a glacial preset (see _svt_preset_for_duration).
        _svt_p = str(_svt_preset_for_duration(_svt_p, opts.get("duration_s")))
        _svt_params = "tune=0:enable-overlays=1:scd=1"
        # Film-grain synthesis: strip the grain before encoding (film-grain-denoise)
        # so bits go to the real signal, then re-synthesize grain from a model on
        # playback. Enabled by the MEASURED grain probe (_probe_film_grain) via
        # advanced_options["_film_grain"], not the unreliable graininess feature.
        _fg_lvl = int((opts.get("_film_grain") or {}).get("level") or 0)
        if _fg_lvl > 0:
            _svt_params += f":film-grain={_fg_lvl}:film-grain-denoise=1"
        v = ["-preset", _svt_p, "-g", str(g_frames), "-svtav1-params", _svt_params]
        if ten_bit:
            v += pix10
        if use_bitrate:
            # SVT-AV1 rejects -maxrate outside CRF mode ("Max Bitrate only
            # supported with CRF mode") — plain ABR only; retries handle size.
            return v, ["-b:v", str(int(v_bitrate))]
        return v, ["-crf", str(min(63, crf_i))]

    if vcodec == "libaom-av1":
        _cpu = _AOM_CPU_USED_MAP.get(str(preset).lower(), "4")
        if use_bitrate:
            # Size-targeting re-encodes the whole file repeatedly (seed, refine,
            # retries, packing). libaom below cpu-used 6 runs at ~1 fps for
            # 1080p, which turns one job into HOURS — keep the iterative path
            # fast; Max-mode's extra effort applies to single-shot CRF only.
            _cpu = str(max(int(_cpu), 6))
        elif _max_mode:
            _cpu = str(min(int(_cpu), 4))
        v = ["-cpu-used", _cpu,
             "-row-mt", "1", "-tile-columns", "1", "-lag-in-frames", "35",
             "-aq-mode", "1", "-g", str(g_frames)]
        # libaom denoises AND writes a grain table when -denoise-noise-level is
        # set (its own grain-synthesis path). Driven by the measured grain probe.
        _fg_aom = int((opts.get("_film_grain") or {}).get("level") or 0)
        if _fg_aom > 0:
            v += ["-denoise-noise-level", str(int(min(50, max(10, _fg_aom))))]
        if ten_bit:
            v += pix10
        if use_bitrate:
            return v, _abr()
        return v, ["-crf", str(min(63, crf_i)), "-b:v", "0"]

    if vcodec == "libvpx-vp9":
        # frame-parallel hurts quality; lag-in-frames + auto-alt-ref are big efficiency wins.
        v = ["-deadline", "good",
             "-cpu-used", ("1" if str(preset).lower() in ("slow", "slower", "veryslow", "placebo") else "2"),
             "-row-mt", "1", "-tile-columns", "1", "-frame-parallel", "0",
             "-lag-in-frames", "25", "-auto-alt-ref", "1", "-aq-mode", "0", "-g", str(g_frames)]
        if use_bitrate:
            return v, _abr()
        return v, ["-crf", str(min(63, crf_i)), "-b:v", "0"]

    if vcodec.endswith("_nvenc"):
        v = ["-preset", "p7", "-tune", "hq", "-multipass", "fullres", "-rc-lookahead", "32"]
        if vcodec in ("h264_nvenc", "hevc_nvenc"):
            v += ["-spatial-aq", "1", "-temporal-aq", "1", "-aq-strength", "8", "-bf", "4"]
        if vcodec == "hevc_nvenc" and ten_bit:
            v += ["-pix_fmt", "p010le"]
        if use_bitrate:
            return v, ["-rc", "vbr"] + _abr(1.15, 2.0)
        return v, ["-rc", "vbr", "-cq", str(min(51, crf_i)), "-b:v", "0"]

    if vcodec.endswith("_qsv"):
        v = ["-preset", "veryslow"]
        if vcodec in ("h264_qsv", "hevc_qsv"):
            v += ["-extbrc", "1"]
        if use_bitrate:
            return v, _abr(1.15, 2.0)
        # No bitrate => ICQ (intelligent constant quality) via -global_quality.
        return v, ["-global_quality", str(min(51, crf_i))]

    if vcodec.endswith("_amf"):
        # AMF has no -preset; it uses -quality plus pre-analysis/VBAQ for efficiency.
        v = ["-quality", ("high_quality" if vcodec == "av1_amf" else "quality")]
        if vcodec in ("h264_amf", "hevc_amf"):
            v += ["-preanalysis", "1", "-vbaq", "1"]
        if use_bitrate:
            return v, ["-rc", "vbr_peak"] + _abr(1.15, 2.0)
        return v, ["-rc", "cqp", "-qp_i", str(min(51, crf_i)), "-qp_p", str(min(51, crf_i))]

    if vcodec == "libvvenc":
        v = ["-preset", preset, "-pix_fmt", "yuv420p10le"]
        return v, (_abr() if use_bitrate else ["-qp", str(int(crf if crf is not None else 28))])

    # Unknown/VAAPI: keep it minimal and broadly compatible.
    v = []
    if use_bitrate:
        return v, _abr()
    return v, ["-qp", str(crf_i)]


def _strip_runtime_keys(adv: dict | None) -> dict:
    """
    Return a copy of advanced_options safe for JSON serialization / persistence:
    runtime-only keys (callbacks, job ids) are removed.
    """
    d = dict(adv or {})
    for k in ("progress_cb", "job_id", "status_cb", "cancel_cb"):
        d.pop(k, None)
    return d
