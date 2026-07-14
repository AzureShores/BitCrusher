from __future__ import annotations

import json
import logging
import os
import subprocess

import ffmpeg_exec
from ffmpeg_exec import si, NO_WIN, _sp_run
from media_math import get_media_type
from media_probe import _probe_video_stream
from encoder_caps import best_av1_encoder
from remux import _privacy_args
from text_utils import format_bytes

LOG = logging.getLogger("BitCrusher")


def _prepare_cover_file(input_path: str, work_dir: str, max_bytes: int) -> tuple[str, int, int] | None:
    """
    Extract the source album art and, if it's bigger than max_bytes, downscale it
    so embedding it doesn't blow a small size target. Album art is routinely
    3000x3000 / multiple MB — embedding that verbatim into a 2 MB target made the
    output overshoot no matter how low the audio bitrate went (the size search
    only tunes audio). Returns (jpg_path, width, height) or None (no cover / too
    tight to bother). A cover already under budget is kept as-is.
    """
    ff = ffmpeg_exec.FFMPEG
    if not ff or max_bytes < 8 * 1024:
        return None
    try:
        vst = _probe_video_stream(input_path) or {}
    except Exception:
        vst = {}
    codec = str(vst.get("codec_name") or "").lower()
    if codec not in ("mjpeg", "png", "jpeg", "jpg", "bmp"):
        return None
    W0, H0 = int(vst.get("width") or 0), int(vst.get("height") or 0)
    if W0 <= 0 or H0 <= 0:
        return None
    # Extract to a REAL extension — ffmpeg can't guess a muxer from ".img", so a
    # generic name silently produces nothing (that was an earlier bug here).
    src_ext = "png" if codec == "png" else "jpg"
    src = os.path.join(work_dir, f"cover_src.{src_ext}")
    try:
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", input_path, "-an", "-map", "0:v:0", "-c", "copy", src],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       startupinfo=si, creationflags=NO_WIN)
        if not (os.path.exists(src) and os.path.getsize(src) > 0):
            return None
        # Already small enough: use verbatim.
        if os.path.getsize(src) <= max_bytes:
            return (src, W0, H0)
        # Downscale to meet the byte budget (JPEG; drops alpha, fine for art).
        for dim, q in ((1000, 4), (800, 4), (640, 5), (500, 6), (400, 7), (300, 8)):
            if dim >= max(W0, H0) and dim != 300:
                continue
            out = os.path.join(work_dir, f"cover_{dim}.jpg")
            subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
                            "-vf", f"scale={dim}:-1", "-q:v", str(q), out],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           startupinfo=si, creationflags=NO_WIN)
            if os.path.exists(out) and 0 < os.path.getsize(out) <= max_bytes:
                w = min(dim, W0)
                h = int(round(H0 * (w / float(W0)))) or 1
                return (out, w, h)
        return None
    except Exception:
        return None


def _probe_audio_meta(input_path: str, default_audio_bitrate: int = 128 * 1000) -> dict:
    """
    default_audio_bitrate mirrors BitCrusherV9.DEFAULT_AUDIO_BITRATE — kept as a
    literal-default parameter rather than importing the monolith's constant, so
    this module stays free of a back-import onto BitCrusherV9.
    """
    _fallback = {
        "duration": 0.0,
        "bitrate": default_audio_bitrate,
        "sr": 48000,
        "ch": 2,
        "codec": "",
    }

    if get_media_type(input_path) != "audio":
        return dict(_fallback)

    FFPROBE = ffmpeg_exec.FFPROBE
    if not FFPROBE:
        return dict(_fallback)

    cmd = [
        FFPROBE, "-v", "error",
        "-select_streams", "a:0",
        "-show_entries",
        "format=duration,bit_rate:stream=codec_name,bit_rate,sample_rate,channels",
        "-of", "json", input_path
    ]

    try:
        p = _sp_run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=si,
            creationflags=NO_WIN
        )
        if p.returncode != 0:
            LOG.error("ffprobe failed (rc=%s): %s", p.returncode,
                     p.stderr.strip() if p.stderr else "no stderr")
            return dict(_fallback)
        js = json.loads(p.stdout or "{}")
    except Exception as e:
        LOG.error("ffprobe exec failed: %s", e)
        return dict(_fallback)

    fmt = (js.get("format") or {}) if isinstance(js, dict) else {}
    streams = (js.get("streams") or []) if isinstance(js, dict) else []
    s = streams[0] if streams else {}

    def _int(x, d):
        try:
            return int(x)
        except Exception:
            try:
                return int(float(x))
            except Exception:
                return d

    def _float(x, d):
        try:
            return float(x)
        except Exception:
            return d

    return {
        "duration": _float(fmt.get("duration"), 0.0),
        "bitrate":  _int(fmt.get("bit_rate") or s.get("bit_rate"), default_audio_bitrate),
        "sr":       _int(s.get("sample_rate"), 48000),
        "ch":       _int(s.get("channels"), 2),
        "codec":    (s.get("codec_name") or "").lower(),
    }


def _should_copy_audio(target_bytes: int, dur: float, meta: dict) -> bool:

    try:
        if not target_bytes or not dur or dur <= 0:
            return False
        codec = (meta.get("codec") or "").lower()
        if codec not in {"aac", "opus", "vorbis"}:
            return False
        a_bits = int(meta.get("bitrate") or 0) * float(dur)
        return a_bits / 8.0 < 0.10 * target_bytes
    except Exception:
        return False


def _adaptive_two_pass(new_w: int, target_bitrate: int, force: bool = False) -> bool:
    # Prefer true 2-pass more broadly; it stabilizes size targeting on high-res screen captures.
    if force:
        return True
    if new_w >= 1920:          # 1080p+ (includes 1440p, 4K)
        return target_bitrate > 0 and target_bitrate <= 8_000_000
    elif new_w >= 1280:        # 720p-1080p
        return target_bitrate > 0 and target_bitrate <= 4_000_000
    else:                       # sub-720p
        return target_bitrate > 0 and target_bitrate <= 2_000_000


def _supports_true_two_pass(encoder: str) -> bool:
    """
    Only software encoders with real two-pass support.

    SVT-AV1 is included because its 1-pass VBR is too loose for size targeting
    on hard content — but only when the concrete AV1 encoder is actually SVT
    (libaom 2-pass exists but crawls, and the iterative pipeline runs many
    passes, so it stays on the single-shot path).
    """
    e = (encoder or "").lower()
    if any(k in e for k in ("x264", "libx264", "x265", "libx265", "libvpx-vp9", "vp9")):
        return True
    if any(k in e for k in ("svt-av1", "svtav1", "libsvtav1")):
        return True
    if "av1" in e and not any(k in e for k in ("aom", "nvenc", "qsv", "amf", "vaapi")):
        try:
            return best_av1_encoder() == "libsvtav1"
        except Exception:
            return False
    return False


def _build_opus_cover_meta(input_path: str, cover: tuple[str, int, int] | None, work_dir: str) -> str | None:
    """
    Build an ffmetadata file carrying the source's tags PLUS a (size-capped)
    album art as a base64 METADATA_BLOCK_PICTURE — the only way opus/ogg can hold
    cover art (it can't carry a copied picture stream like mp3/m4a). The base64
    blob is too large for a command-line argument, so it must ride in a file.
    """
    import base64, struct
    ff = ffmpeg_exec.FFMPEG
    if not ff or not cover:
        return None
    cover_path, W, H = cover
    meta_path = os.path.join(work_dir, "covermeta.txt")
    try:
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", input_path, "-f", "ffmetadata", meta_path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       startupinfo=si, creationflags=NO_WIN)
        if not os.path.exists(meta_path):
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write(";FFMETADATA1\n")
        with open(cover_path, "rb") as f:
            data = f.read()
        mime = "image/png" if cover_path.lower().endswith(".png") else "image/jpeg"
        u32 = lambda x: struct.pack(">I", int(x))
        # FLAC picture block: type=3 (front cover), mime, empty desc, w/h/depth/colors, data.
        block = (u32(3) + u32(len(mime)) + mime.encode("ascii") + u32(0)
                 + u32(W) + u32(H) + u32(24) + u32(0) + u32(len(data)) + data)
        b64 = base64.b64encode(block).decode("ascii")
        with open(meta_path, "a", encoding="utf-8") as f:
            f.write("METADATA_BLOCK_PICTURE=" + b64 + "\n")
        return meta_path
    except Exception:
        return None


def _encode_audio_once(input_path: str, output_path: str, *,
                       encoder: str, bitrate_bps: int, sr: int,
                       channels: int, vbr_mode: str, loudnorm: bool,
                       highpass_hz: int | None, lowpass_hz: int | None,
                       extra_filters: list[str] | None = None,
                       privacy_preset: str | None = None,
                       opus_cover_meta: str | None = None,
                       cover_file: str | None = None) -> tuple[bool, int, str]:

    af = []

    if highpass_hz and highpass_hz > 0:
        af.append(f"highpass=f={highpass_hz}")
    if lowpass_hz and lowpass_hz > 0:
        af.append(f"lowpass=f={lowpass_hz}")

    if loudnorm:
        af.append("dynaudnorm=p=1")

    if extra_filters:
        af.extend(extra_filters)

    af_chain = ",".join(af) if af else None

    # Metadata + cover art. The old path used "-vn" (drops the album cover, which
    # is stored as an attached-picture video stream) AND the privacy default
    # "-map_metadata -1" (strips title/artist/album) — so every compressed track
    # came out naked. For music that's a data-loss bug: tags/art are the point
    # and aren't privacy-sensitive. Preserve both by default; only "strict"
    # privacy strips. Cover art can only ride containers with a picture stream —
    # ogg/opus cannot carry a copied mjpeg, so opus keeps tags but not the image.
    _strict_privacy = str(privacy_preset or "").lower() == "strict"
    _out_ext = os.path.splitext(output_path)[1].lower().lstrip(".")
    _is_ogg = _out_ext in ("opus", "ogg")
    # Cover art as a copied picture stream works for mp3/m4a/flac; ogg/opus can't
    # carry it that way, so it rides as a base64 METADATA_BLOCK_PICTURE injected
    # from opus_cover_meta (built by _extract_opus_cover_meta) instead.
    _cover_ok = (not _strict_privacy) and (not _is_ogg)
    _use_opus_meta = bool(opus_cover_meta) and _is_ogg and (not _strict_privacy) \
        and os.path.exists(opus_cover_meta or "")
    # A prepared (size-capped) cover file is embedded as a second image input so
    # a small target isn't blown by a multi-MB source cover. Without one, the
    # source's own picture stream is copied verbatim (used by the FLAC path).
    _use_cover_file = _cover_ok and bool(cover_file) and os.path.exists(cover_file or "")

    cmd = [ffmpeg_exec.FFMPEG, "-y", "-i", input_path]
    if _use_opus_meta:
        cmd += ["-i", opus_cover_meta]        # tags + embedded picture live here
    elif _use_cover_file:
        cmd += ["-i", cover_file]             # input 1 = size-capped album art
    cmd += ["-map", "0:a:0"]
    if _use_cover_file:
        cmd += ["-map", "1:v:0"]
    elif _cover_ok:
        cmd += ["-map", "0:v?"]               # verbatim source cover (lossless/FLAC path)
    cmd += ["-c:a", encoder]

    if encoder == "libopus":
        if vbr_mode == "off":
            cmd += ["-vbr", "off"]
        elif vbr_mode == "constrained":
            cmd += ["-vbr", "constrained"]
        else:
            cmd += ["-vbr", "on"]
        cmd += ["-compression_level", "10", "-application", "audio"]
    elif encoder == "flac":
        cmd += ["-compression_level", "8"]    # lossless: max compression effort

    if channels in (1, 2):
        cmd += ["-ac", str(channels)]
    if sr:
        cmd += ["-ar", str(sr)]

    if encoder != "flac":                     # FLAC is lossless — bitrate is N/A
        cmd += ["-b:a", str(int(bitrate_bps))]

    if af_chain:
        cmd += ["-af", af_chain]

    if _use_cover_file:
        cmd += ["-c:v", "mjpeg"]              # re-encode the capped cover
        if _out_ext in ("m4a", "mp4", "aac"):
            cmd += ["-disposition:v:0", "attached_pic"]
    elif _cover_ok:
        cmd += ["-c:v", "copy"]
        if _out_ext in ("m4a", "mp4", "aac"):
            cmd += ["-disposition:v:0", "attached_pic"]

    if _strict_privacy:
        cmd += _privacy_args("strict")        # strips tags + cover (opt-in)
    elif _use_opus_meta:
        cmd += ["-map_metadata", "1"]         # tags + cover picture from the meta file
    else:
        cmd += ["-map_metadata", "0"]         # carry title/artist/album/...

    cmd += [output_path]

    proc = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, startupinfo=si, creationflags=NO_WIN)
    ok = (proc.returncode == 0) and os.path.exists(output_path)
    size = os.path.getsize(output_path) if ok else 0
    tail = "\n".join((proc.stderr or "").splitlines()[-15:])
    return ok, size, tail


def _best_audio_codec(container: str, audio_fmt_pref: str) -> str:
    """
    Returns the best audio codec for the given container and user preference.
    - "auto": picks Opus for MKV/WebM (where it has full support) and AAC for MP4.
    - Any explicit value (e.g. "aac", "opus", "mp3") is passed through unchanged.
    """
    pref = (audio_fmt_pref or "auto").strip().lower()
    if pref and pref != "auto":
        return pref
    if (container or "mp4").lower() in ("mkv", "webm"):
        return "opus"
    return "aac"


def binary_search_audio_bitrate(input_path: str, temp_output: str, audio_encoder: str,
                                low: int, high: int, target_size_bytes: int,
                                status_callback, cancel_callback) -> int:
    best_bitrate = None
    while low <= high:
        mid = (low + high) // 2
        if cancel_callback():
            status_callback("Audio compression cancelled during binary search.", level="WARNING")
            return None
        status_callback(f"Testing bitrate: {mid}bps...")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        cmd = [ffmpeg_exec.FFMPEG, "-y", "-i", input_path, "-vn", "-c:a", audio_encoder, "-b:a", str(mid), temp_output]
        result = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=si, creationflags=NO_WIN)
        if result.returncode != 0:
            status_callback(f"ffmpeg error at bitrate {mid}: {result.stderr.strip()}", level="ERROR")
            return None
        if not os.path.exists(temp_output):
            status_callback("No output produced for bitrate " + str(mid), level="ERROR")
            return None
        size = os.path.getsize(temp_output)
        status_callback(f"Bitrate {mid} produced {format_bytes(size)}")
        if size > target_size_bytes:
            high = mid - 1
        else:
            best_bitrate = mid
            if size >= target_size_bytes * 0.9:
                return mid
            low = mid + 1
    return best_bitrate
