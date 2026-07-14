from __future__ import annotations

import os
import re

# =====================================================================
# Pure media/size math: byte-unit conversion, ceiling margin, resolution/
# bitrate/tune decisions, media-type sniffing, drag-and-drop path parsing.
# No I/O beyond os.path string ops; no ffmpeg/ffprobe calls.
# =====================================================================

TARGET_SIZE_MARGIN_RATIO = 0.005
TARGET_SIZE_MARGIN_MIN_BYTES = 16 * 1024
TARGET_SIZE_MARGIN_MAX_BYTES = 128 * 1024

_STD_WIDTHS = [3840, 2560, 1920, 1600, 1280, 1024, 960, 854, 640, 480, 426]


def bytes_from_value_unit(val, unit):

    try:
        v = float(str(val).strip())
    except Exception:
        v = 0.0
    u = (str(unit or "MB").strip().upper())
    mul = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }.get(u, 1024**2)

    b = int(max(0, v) * mul)
    return b


def apply_target_size_margin(target_bytes):
    """
    Keep outputs slightly below platform hard limits (e.g. 10 MB upload caps)
    to avoid "over the limit by a few bytes" rejections.
    """
    t = int(max(1, target_bytes))
    margin = int(max(
        TARGET_SIZE_MARGIN_MIN_BYTES,
        min(TARGET_SIZE_MARGIN_MAX_BYTES, float(t) * float(TARGET_SIZE_MARGIN_RATIO)),
    ))
    return max(1, t - margin)


def human_bytes(n: int) -> str:
    n = int(max(0, n))
    for unit, mul in [("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)]:
        if n >= mul:
            return f"{n / mul:.2f} {unit}"
    return f"{n} B"


def _sanitize_int(s, default=0):
    try:
        return int(float(str(s).strip()))
    except Exception:
        return int(default)


def next_lower_std_width(cur: int) -> int:
    """Largest standard delivery width strictly below `cur`; falls back to 80%
    (rounded down to even) and returns 0 once no sane lower width remains. Used
    by the ceiling downscale-retry to step resolution down when a target cannot
    be met at native size."""
    cur = int(cur or 0)
    for _sw in _STD_WIDTHS:
        if _sw < cur:
            return _sw
    nxt = int(cur * 0.8) & ~1
    return nxt if nxt >= 256 else 0


def determine_audio_bitrate(input_bitrate: int, default_audio_bitrate: int = 128 * 1000) -> int:
    """
    default_audio_bitrate mirrors BitCrusherV9.DEFAULT_AUDIO_BITRATE — kept as a
    literal-default parameter rather than importing the monolith's constant, so
    this module stays free of a back-import onto BitCrusherV9.
    """
    if input_bitrate < 1_000_000:
        return 64 * 1000
    elif input_bitrate < 1_500_000:
        return 96 * 1000
    return default_audio_bitrate


def determine_tune_profile(width: int, height: int, filename: str) -> str:
    lower = filename.lower()
    if "anime" in lower or "cartoon" in lower:
        return "animation"
    elif "cam" in lower or "grain" in lower:
        return "grain"
    return "film"


def determine_frame_rate(framerate: float, width: int, duration: float, target_bitrate: int | None = None):
    # Preserve source cadence unless bitrate is clearly too constrained for high-FPS output.
    if framerate <= 0:
        return None
    if target_bitrate is None or target_bitrate <= 0:
        return None
    if framerate < 50:
        return None

    if width >= 1920 and target_bitrate < 1_000_000:
        return 30
    if width >= 1280 and target_bitrate < 700_000:
        return 30
    if target_bitrate < 450_000 and duration > 15:
        return 24
    return None


def determine_resolution(width: int, height: int, target_bitrate: int, fps_hint: float | None = None,
                         encoder: str | None = None, complexity: float | None = None) -> int:
    if width <= 0:
        return width
    if target_bitrate <= 0:
        return width

    fps = float(fps_hint or 30.0)
    fps = max(12.0, min(120.0, fps))
    pix_rate = float(width * max(1, int(height))) * fps
    bpppf = float(target_bitrate) / max(1.0, pix_rate)

    # Content-aware: flat/simple content (screen recordings, UI, flat cartoons)
    # compresses CHEAPLY at native resolution — downscaling it only smears its
    # sharp text/edges for no real bitrate saving. The Shutter face-off caught
    # BitCrusher losing a screen rec (2166->1280, VMAF 81) to a native-res 2-pass
    # (VMAF 94). spatial_complexity separates it cleanly (~1 flat vs ~2.5+ natural),
    # so boost the effective bits/pixel for LOW-complexity content to keep its
    # resolution. Floored at 1.0 — never penalizes complex content.
    try:
        _cx = float(complexity) if complexity is not None else 0.0
    except Exception:
        _cx = 0.0
    # Cap 2.2 (numerator/denom-floored-at-1.0) topped out at 2.2x, which still left
    # a 2166-wide screen rec (cx~0.97, ~600 kbps) at 1600w and it LOST a face-off
    # to a native-res 2-pass (VMAF 94 vs 88.6 at a SMALLER size). Genuinely-flat
    # content (cx < 1.2: screen/UI/flat cartoon) gets a stronger fixed boost so it
    # reaches the native-res bpppf rung at realistic budgets. Gated at cx < 1.2 on
    # PURPOSE: raising the shared numerator would also boost mid-complexity natural
    # video (cx 2.2-3.0) and risk downscaling regressions on the 4K reference the
    # original fix protected. cx >= 1.2 keeps the exact old 2.2/cx curve. Still
    # floored by the bpppf ladder below, so a truly starved budget downscales anyway.
    _FLAT_KEEPRES_BOOST = 3.0
    if _cx > 0.0:
        if _cx < 1.2:
            bpppf *= _FLAT_KEEPRES_BOOST
        else:
            bpppf *= max(1.0, min(2.2, 2.2 / _cx))

    # Encoders differ hugely in how few bits/pixel they can survive on: x264
    # falls apart well before x265, and AV1 keeps working below both. Scale the
    # downscale thresholds accordingly (higher factor = downscales sooner).
    _e = (encoder or "x264").lower()
    if "av1" in _e:
        _f = 0.80
    elif any(k in _e for k in ("265", "hevc", "vp9", "vvc")):
        _f = 1.00
    else:  # x264 / h264 family
        _f = 1.40
    bpppf /= _f

    # Prefer keeping resolution unless compression pressure is severe.
    if width >= 3840:
        if bpppf < 0.022:
            return 1920
        if bpppf < 0.034:
            return 2560
    if width > 2560:
        if bpppf < 0.017:
            return 1920
        if bpppf < 0.025:
            return 2304
    if width > 1920:
        if bpppf < 0.010:
            return 1280
        if bpppf < 0.014:
            return 1600
    if width > 1280:
        if bpppf < 0.006:
            return 960
        if bpppf < 0.0085:
            return 1152
        if bpppf < 0.011:
            return 1280
    return width


def get_media_type(input_path: str) -> str:
    ext = os.path.splitext(input_path)[1].lower()
    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".3g2", ".mpeg", ".mpg"}
    audio_exts = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".wma", ".m4a", ".opus", ".alac", ".aiff", ".aif"}
    image_exts = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".heic", ".heif", ".jxl", ".raw", ".avif"}
    doc_exts   = {".pdf"}
    if ext in video_exts:  return "video"
    if ext in audio_exts:  return "audio"
    if ext in image_exts:  return "image"
    if ext in doc_exts:    return "document"
    return "unknown"


def parse_dnd_files(data: str) -> list:
    data = data.strip()
    files = re.findall(r'\{([^}]+)\}', data)
    if not files:
        files = data.replace("\r", "\n").split("\n")
    cleaned = []
    for f in files:
        f = f.strip().strip("{}")
        if f.startswith("file:///"):
            f = f[8:]
        elif f.startswith("file://"):
            f = f[7:]
        if os.name == "nt":
            f = f.replace("/", "\\")
        cleaned.append(os.path.normpath(f))
    return cleaned
