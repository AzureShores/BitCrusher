from __future__ import annotations

import os

# =====================================================================
# Discord inline-playback verdict.
#
# `--discord-compat` already PINS an output to H.264 + AAC in MP4. What was
# missing is the read-only other half: telling the user what the settings they
# actually chose will do on Discord, whether or not they opted in.
#
# Pure derivation - no ffmpeg, no I/O, no Tkinter - so both the GUI chip and
# the CLI summary read the same table. `verdict_for_output()` is the one
# adapter that touches ffprobe, and it imports lazily.
# =====================================================================

VERDICT_YES = "yes"
VERDICT_MAYBE = "maybe"
VERDICT_NO = "no"

_RANK = {VERDICT_YES: 2, VERDICT_MAYBE: 1, VERDICT_NO: 0}
_UNRANK = {2: VERDICT_YES, 1: VERDICT_MAYBE, 0: VERDICT_NO}

# Containers Discord will render in an inline player at all. Anything else is
# offered as a plain download link no matter how compatible the streams are.
_CONTAINER_INLINE = {"mp4", "m4v", "mov", "webm"}
_CONTAINER_ALIASES = {
    "matroska": "mkv", "quicktime": "mov", "m4a": "mp4",
    "mpegts": "ts", "mp2t": "ts", "asf": "wmv",
}

# Video codecs, by how reliably Discord's players decode them.
_VIDEO_YES = {"h264"}
_VIDEO_MAYBE = {"hevc", "av1", "vp9", "vp8"}
_VIDEO_ALIASES = {
    "x264": "h264", "libx264": "h264", "avc": "h264", "avc1": "h264",
    "h264_nvenc": "h264", "h264_qsv": "h264", "h264_amf": "h264",
    "x265": "hevc", "libx265": "hevc", "h265": "hevc", "hvc1": "hevc",
    "hevc_nvenc": "hevc", "hevc_qsv": "hevc", "hevc_amf": "hevc",
    "svt-av1": "av1", "libsvtav1": "av1", "aom-av1": "av1", "libaom-av1": "av1",
    "av1_nvenc": "av1", "av1_qsv": "av1", "av1_amf": "av1", "av01": "av1",
    "libvpx-vp9": "vp9", "libvpx": "vp8",
    "vvc": "vvc", "libvvenc": "vvc", "h266": "vvc",
}

_AUDIO_YES = {"aac", "mp3"}
_AUDIO_MAYBE = {"opus", "vorbis"}
_AUDIO_ALIASES = {
    "libfdk_aac": "aac", "aac_at": "aac", "m4a": "aac",
    "libmp3lame": "mp3", "mp3float": "mp3",
    "libopus": "opus", "libvorbis": "vorbis",
    "eac3": "ac3", "ac-3": "ac3", "dts": "dts", "dca": "dts",
    "truehd": "truehd", "flac": "flac", "alac": "alac", "pcm_s16le": "pcm",
}

# Human labels for the resolved codec ids.
_VIDEO_NAMES = {"h264": "H.264", "hevc": "H.265", "av1": "AV1",
                "vp9": "VP9", "vp8": "VP8", "vvc": "VVC"}
_AUDIO_NAMES = {"aac": "AAC", "mp3": "MP3", "opus": "Opus", "vorbis": "Vorbis",
                "ac3": "AC3", "dts": "DTS", "truehd": "TrueHD",
                "flac": "FLAC", "alac": "ALAC", "pcm": "PCM", "none": "silent"}


def normalize_video_codec(name) -> str:
    """Encoder name or ffprobe codec_name -> a canonical codec id ('h264',
    'hevc', 'av1', ...). Unknown values come back lowercased, not blanked, so
    an unrecognised encoder reads as unknown rather than as compatible."""
    v = str(name or "").strip().lower()
    if not v:
        return ""
    return _VIDEO_ALIASES.get(v, v)


def normalize_audio_codec(name) -> str:
    """Audio format preference or ffprobe codec_name -> canonical id. 'auto'
    is unresolved by design; callers pass the container-resolved value."""
    a = str(name or "").strip().lower()
    if not a or a == "auto":
        return ""
    return _AUDIO_ALIASES.get(a, a)


def normalize_container(name) -> str:
    """Container name, ffprobe format_name list, or a file path/extension ->
    a canonical extension."""
    c = str(name or "").strip().lower()
    if not c:
        return ""
    # ffprobe format_name is a comma list, e.g. "mov,mp4,m4a,3gp,3g2,mj2".
    # Checked first: those lists never contain a dot or a separator.
    if "," in c:
        parts = [p.strip() for p in c.split(",") if p.strip()]
        for pref in ("mp4", "webm", "mov", "mkv"):
            if pref in parts:
                return pref
        c = parts[0] if parts else ""
    else:
        # Path, bare filename, or a plain ".ext" -- os.path.splitext treats a
        # leading dot as a hidden-file stem, so slice the extension by hand.
        base = c.replace("\\", "/").rsplit("/", 1)[-1]
        if base.startswith("."):
            c = base.lstrip(".")
        elif "." in base:
            c = base.rsplit(".", 1)[-1]
        else:
            c = base
    return _CONTAINER_ALIASES.get(c, c)


def _worsen(current: str, floor: str) -> str:
    """Clamp a verdict down to `floor` (never up)."""
    return _UNRANK[min(_RANK.get(current, 0), _RANK.get(floor, 0))]


def inline_playback_verdict(*, video_codec=None, audio_codec=None, container=None,
                            pix_fmt=None, bit_depth=None,
                            size_bytes=None, limit_bytes=None) -> dict:
    """
    Will Discord play this inline?

    Returns {"verdict": "yes"|"maybe"|"no", "label", "reasons", "video",
    "audio", "container"}. `reasons` lists every thing holding the verdict
    below "yes" and is empty exactly when the verdict is "yes".

    "maybe" is used deliberately rather than folding everything unplayable-
    somewhere into "no": AV1 and H.265 do inline-play on current desktop
    clients and refusing them outright would push users to bigger files for
    no reason.
    """
    v = normalize_video_codec(video_codec)
    a = normalize_audio_codec(audio_codec)
    c = normalize_container(container)
    reasons: list[str] = []
    verdict = VERDICT_YES

    if not c:
        verdict = _worsen(verdict, VERDICT_MAYBE)
        reasons.append("container unknown")
    elif c not in _CONTAINER_INLINE:
        verdict = VERDICT_NO
        reasons.append(f"{c.upper()} has no inline player - Discord shows a download link")

    if not v:
        verdict = _worsen(verdict, VERDICT_MAYBE)
        reasons.append("video codec unknown")
    elif v in _VIDEO_MAYBE:
        verdict = _worsen(verdict, VERDICT_MAYBE)
        reasons.append(f"{_VIDEO_NAMES.get(v, v.upper())} does not preview on older mobile clients")
    elif v not in _VIDEO_YES:
        verdict = VERDICT_NO
        reasons.append(f"{_VIDEO_NAMES.get(v, v.upper())} is not a codec Discord decodes")

    if a == "none":
        pass  # a silent file inline-plays fine; absence is not uncertainty
    elif not a:
        verdict = _worsen(verdict, VERDICT_MAYBE)
        reasons.append("audio codec unknown")
    elif a in _AUDIO_MAYBE:
        verdict = _worsen(verdict, VERDICT_MAYBE)
        reasons.append(f"{_AUDIO_NAMES.get(a, a.upper())} audio is not decoded by every client")
    elif a not in _AUDIO_YES:
        verdict = VERDICT_NO
        reasons.append(f"{_AUDIO_NAMES.get(a, a.upper())} audio will play silent or not at all")

    # Webm carries VP8/VP9/AV1 + Opus/Vorbis only; MP4 audio must not be Opus.
    if c == "webm" and v == "h264":
        verdict = VERDICT_NO
        reasons.append("H.264 in WebM is not a valid combination")
    if c in ("mp4", "m4v", "mov") and a in ("opus", "vorbis"):
        verdict = _worsen(verdict, VERDICT_MAYBE)
        reasons.append(f"{_AUDIO_NAMES.get(a, a.upper())} in MP4 is poorly supported")

    # 4:2:0 8-bit is what hardware decoders assume; anything else is a coin
    # flip even when the codec itself is fine.
    pf = str(pix_fmt or "").strip().lower()
    if pf and pf not in ("yuv420p", "yuvj420p", "nv12"):
        verdict = _worsen(verdict, VERDICT_MAYBE)
        reasons.append(f"{pf} is not 8-bit 4:2:0 - hardware decode may fail")
    else:
        try:
            if bit_depth is not None and int(bit_depth) > 8:
                verdict = _worsen(verdict, VERDICT_MAYBE)
                reasons.append(f"{int(bit_depth)}-bit video - hardware decode may fail")
        except (TypeError, ValueError):
            pass

    # Above the upload limit nothing else matters - it never gets posted.
    try:
        if size_bytes is not None and limit_bytes is not None and int(size_bytes) > int(limit_bytes):
            verdict = VERDICT_NO
            reasons.append(f"{int(size_bytes)} bytes is over the {int(limit_bytes)}-byte upload limit")
    except (TypeError, ValueError):
        pass

    return {"verdict": verdict, "label": _label(verdict, v, a, c),
            "reasons": reasons, "video": v, "audio": a, "container": c}


def _label(verdict: str, v: str, a: str, c: str) -> str:
    """One-line chip text: what the streams are, then what Discord does."""
    parts = []
    if v:
        parts.append(_VIDEO_NAMES.get(v, v.upper()))
    if a:
        parts.append(_AUDIO_NAMES.get(a, a.upper()))
    streams = " + ".join(parts) if parts else "unknown streams"
    if c:
        streams = f"{streams} ({c.upper()})"
    if verdict == VERDICT_YES:
        return f"{streams} - will inline-play"
    if verdict == VERDICT_MAYBE:
        return f"{streams} - may not preview"
    return f"{streams} - Discord will not preview"


def verdict_text(res: dict) -> str:
    """Chip line plus the first reason, for a status log or a CLI summary."""
    if not isinstance(res, dict):
        return ""
    label = str(res.get("label") or "")
    reasons = list(res.get("reasons") or [])
    return f"{label} ({reasons[0]})" if reasons else label


def verdict_for_settings(advanced_options: dict | None, *,
                         source_audio_codec=None, size_bytes=None,
                         limit_bytes=None) -> dict:
    """
    Pre-flight verdict from the settings a job is about to run with.

    Resolves 'auto' audio the same way the encoder does (Opus for MKV/WebM,
    AAC otherwise). Audio may still be stream-copied at encode time, which no
    setting reveals - pass `source_audio_codec` when the caller has probed the
    input, and re-check with verdict_for_output() once the file exists.
    """
    adv = dict(advanced_options or {})
    container = normalize_container(adv.get("output_container") or "mp4")
    audio_pref = str(adv.get("audio_format") or "auto").strip().lower()
    if audio_pref in ("", "auto", "copy"):
        if audio_pref == "copy" and source_audio_codec:
            audio = normalize_audio_codec(source_audio_codec)
        else:
            audio = "opus" if container in ("mkv", "webm") else "aac"
    else:
        audio = normalize_audio_codec(audio_pref)
    return inline_playback_verdict(
        video_codec=adv.get("encoder") or "x264", audio_codec=audio,
        container=container, size_bytes=size_bytes, limit_bytes=limit_bytes)


def verdict_for_output(path: str, *, limit_bytes=None) -> dict:
    """
    Measured verdict for a file that exists: the streams that actually got
    written, not the ones that were requested. This is the one that respects
    the record-only-effective-settings rule - a codec race or an audio copy
    can make the delivered file differ from the plan.
    """
    try:
        from encode.media_probe import _probe_media_cached
    except ImportError:  # flat layout
        from media_probe import _probe_media_cached  # type: ignore
    data = _probe_media_cached(path) or {}
    vstream: dict = {}
    astream: dict = {}
    for s in (data.get("streams") or []):
        t = (s or {}).get("codec_type")
        if t == "video" and not vstream:
            vstream = s
        elif t == "audio" and not astream:
            astream = s
    container = normalize_container((data.get("format") or {}).get("format_name") or path)
    try:
        size = int((data.get("format") or {}).get("size") or os.path.getsize(path))
    except (OSError, TypeError, ValueError):
        size = None
    return inline_playback_verdict(
        video_codec=vstream.get("codec_name"),
        audio_codec=(astream.get("codec_name") if astream else "none"),
        container=container,
        pix_fmt=vstream.get("pix_fmt"),
        bit_depth=vstream.get("bits_per_raw_sample"),
        size_bytes=size, limit_bytes=limit_bytes)
