from __future__ import annotations

import os

import encode.ffmpeg_exec as ffmpeg_exec
from encode.ffmpeg_exec import si, NO_WIN, _sp_check_output

_ENCODER_CANON = {
    "x264": "x264", "libx264": "x264", "h264": "x264", "avc": "x264",
    "x265": "x265", "libx265": "x265", "hevc": "x265", "h265": "x265",
    "av1": "av1", "svt-av1": "svt-av1", "svtav1": "svt-av1", "libsvtav1": "svt-av1",
    "aom-av1": "aom-av1", "libaom-av1": "aom-av1",
    "vp9": "vp9", "libvpx-vp9": "vp9",
    "vvc": "vvc", "libvvenc": "vvc", "h266": "vvc",
}


def _merge_params_string(base: str, overrides: str) -> str:
    """
    Merge a colon-separated key=value params string (x264-params/x265-params
    style) onto another, key-by-key: overrides replace matching keys in base
    and unmatched override keys are appended. Order of base is preserved.
    """
    def _parts(s: str):
        return [p for p in (s or "").split(":") if p]
    merged: dict[str, str] = {}
    order: list[str] = []
    for p in _parts(base):
        k = p.split("=", 1)[0]
        if k not in merged:
            order.append(k)
        merged[k] = p
    for p in _parts(overrides):
        k = p.split("=", 1)[0]
        if k not in merged:
            order.append(k)
        merged[k] = p
    return ":".join(merged[k] for k in order)


def _canonical_encoder(encoder: str | None) -> str:
    """
    Canonical tag for a requested encoder. The old _software_quality_encoder
    silently coerced EVERYTHING that wasn't x265 into x264 — including explicit
    svt-av1/vp9/vvc/NVENC requests, so picking SVT-AV1 in the GUI produced an
    x264 file. The user's choice is now honored; only unknown names fall back
    to x264.
    """
    e = (encoder or "").strip().lower()
    if e in _ENCODER_CANON:
        return _ENCODER_CANON[e]
    if e.endswith(("_nvenc", "_qsv", "_amf")):
        return e  # explicit hardware encoder: honored as-is
    if "265" in e or "hevc" in e:
        return "x265"
    if "av1" in e:
        return "av1"
    return "x264"


def _software_quality_encoder(encoder: str | None) -> str:
    # Back-compat alias; see _canonical_encoder.
    return _canonical_encoder(encoder)


def _ffmpeg_encoder_set() -> set:
    """All video encoder names the installed ffmpeg exposes (cached)."""
    cache = getattr(_ffmpeg_encoder_set, "_cache", None)
    if cache is None:
        cache = set()
        try:
            out = _sp_check_output([ffmpeg_exec.FFMPEG, "-hide_banner", "-encoders"],
                                   text=True, startupinfo=si, creationflags=NO_WIN)
            for line in (out or "").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].startswith("V"):
                    cache.add(parts[1].strip())
        except Exception:
            pass
        setattr(_ffmpeg_encoder_set, "_cache", cache)
    return cache


_HW_DECODE_BROKEN = False  # set on first hw-decode failure; all later jobs use software decode


def _mark_hw_decode_broken():
    global _HW_DECODE_BROKEN
    _HW_DECODE_BROKEN = True


def _available_hwaccels() -> set:
    """Hardware acceleration methods the installed ffmpeg exposes (cached)."""
    cache = getattr(_available_hwaccels, "_cache", None)
    if cache is None:
        cache = set()
        try:
            out = _sp_check_output([ffmpeg_exec.FFMPEG, "-hide_banner", "-hwaccels"], text=True,
                                   startupinfo=si, creationflags=NO_WIN)
            for line in (out or "").splitlines():
                s = line.strip().lower()
                if s and "acceleration" not in s:
                    cache.add(s)
        except Exception:
            pass
        setattr(_available_hwaccels, "_cache", cache)
    return cache


def _hw_decode_args(advanced_options: dict | None) -> list:
    """
    ['-hwaccel', X] input-side args for GPU decode, or [] when disabled or
    unavailable. Deliberately no -hwaccel_output_format: decoded frames come
    back to system memory so every software filter (scale, tonemap, ...) keeps
    working — only the H.264/HEVC/VP9/AV1 *decode* moves off the CPU.
    """
    if _HW_DECODE_BROKEN:
        return []
    if not bool((advanced_options or {}).get("hw_decode", True)):
        return []
    have = _available_hwaccels()
    if os.name == "nt" and "d3d11va" in have:
        return ["-hwaccel", "d3d11va"]
    if have:
        return ["-hwaccel", "auto"]
    return []


def _strip_hw_args(cmd: list) -> list:
    c = list(cmd)
    try:
        i = c.index("-hwaccel")
        del c[i:i + 2]
    except ValueError:
        pass
    return c


def best_av1_encoder() -> str | None:
    """
    The best AV1 encoder actually present in this ffmpeg build, preferring
    SVT-AV1 (fast + excellent) > libaom (slow + best) > hardware. BitCrusher used
    to hardcode libsvtav1, which is missing from many builds (e.g. gyan
    'essentials'), so AV1 would silently fail/fall back to HEVC.
    """
    have = _ffmpeg_encoder_set()
    for enc in ("libsvtav1", "libaom-av1", "av1_nvenc", "av1_qsv", "av1_amf"):
        if enc in have:
            return enc
    return None
