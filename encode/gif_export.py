"""Animated GIF / WebP export with size targeting.

BitCrusher previously only consumed animated GIF/WebP as input (routed
to compress_video). This module exports them: a clip range becomes a
GIF (two-pass palettegen/paletteuse) or an animated WebP (libwebp_anim),
walked down a quality ladder until the result fits the size cap.

The ladder planner is pure and unit-tested; the runner shells out via
the shared ffmpeg_exec helpers and honors cancel_cb between attempts.
Size target is a ceiling: the first attempt that fits wins; if none
fits, the smallest result ships with a WARN (mirrors the encode path's
best-effort convention).
"""
from __future__ import annotations

import os
import subprocess
import tempfile

from encode.ffmpeg_exec import _sp_run, si, NO_WIN
from encode import ffmpeg_exec as _fx

_GIF_FPS_STEPS = (15, 12, 10)
_GIF_COLOR_STEPS = (256, 128, 64)
_WEBP_FPS_STEPS = (20, 15, 12, 10)
_WEBP_Q_STEPS = (75, 60, 45, 30)
_WIDTH_STEPS = (480, 400, 320, 256)


def plan_animated_ladder(duration_s: float, src_w: int, src_h: int,
                         fmt: str, target_mb: float | None) -> list[dict]:
    """Descending-quality attempt ladder for an animated export.

    Each attempt: {"fps", "width", "colors" (gif) | "quality" (webp)}.
    Without a size target only the best attempt is returned. Widths never
    upscale the source. Short-circuits to one attempt for sub-second
    sources (nothing to trade away).
    """
    fmt = str(fmt or "").lower()
    if fmt not in ("gif", "webp"):
        return []
    try:
        w = int(src_w) or 480
    except Exception:
        w = 480
    widths = [x for x in _WIDTH_STEPS if x <= w] or [w]

    attempts: list[dict] = []
    if fmt == "gif":
        for width in widths:
            for fps in _GIF_FPS_STEPS:
                for colors in _GIF_COLOR_STEPS:
                    attempts.append({"fps": fps, "width": width,
                                     "colors": colors})
    else:
        for width in widths:
            for fps in _WEBP_FPS_STEPS:
                for q in _WEBP_Q_STEPS:
                    attempts.append({"fps": fps, "width": width,
                                     "quality": q})

    try:
        if float(duration_s) < 1.0:
            return attempts[:1]
    except Exception:
        pass
    if not target_mb:
        return attempts[:1]
    return attempts


def _trim_args(start, end) -> list[str]:
    args: list[str] = []
    if start is not None:
        args += ["-ss", str(float(start))]
    if end is not None:
        args += ["-to", str(float(end))]
    return args


def _run(cmd) -> bool:
    try:
        r = _sp_run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    startupinfo=si, creationflags=NO_WIN)
        return r.returncode == 0
    except Exception:
        return False


def _attempt_gif(ffmpeg, input_path, out_path, att, start, end) -> bool:
    vf = f"fps={att['fps']},scale={att['width']}:-1:flags=lanczos"
    with tempfile.TemporaryDirectory(prefix="bc_gif_") as td:
        pal = os.path.join(td, "pal.png")
        if not _run([ffmpeg, "-y", *_trim_args(start, end), "-i", input_path,
                     "-vf", f"{vf},palettegen=max_colors={att['colors']}",
                     pal]):
            return False
        return _run([ffmpeg, "-y", *_trim_args(start, end), "-i", input_path,
                     "-i", pal,
                     "-lavfi", f"{vf}[x];[x][1:v]paletteuse=dither=sierra2_4a",
                     "-an", out_path])


def _attempt_webp(ffmpeg, input_path, out_path, att, start, end) -> bool:
    vf = f"fps={att['fps']},scale={att['width']}:-1:flags=lanczos"
    return _run([ffmpeg, "-y", *_trim_args(start, end), "-i", input_path,
                 "-vf", vf, "-c:v", "libwebp_anim", "-q:v", str(att["quality"]),
                 "-loop", "0", "-an", out_path])


def export_animated(input_path: str, out_path: str, fmt: str, *,
                    duration_s: float = 0.0, src_w: int = 0, src_h: int = 0,
                    start=None, end=None, target_mb: float | None = None,
                    cancel_cb=None, status_cb=None, ffmpeg=None) -> dict:
    """Export input as animated GIF/WebP, walking the ladder until it fits.

    Returns {"ok", "output_path", "final_size", "attempts", "fit"}.
    """
    status_cb = status_cb or (lambda *a, **k: None)
    ffmpeg = ffmpeg or _fx.FFMPEG or "ffmpeg"
    fmt = str(fmt or "").lower()
    ladder = plan_animated_ladder(duration_s, src_w, src_h, fmt, target_mb)
    if not ladder:
        status_cb(f"[AnimExport] Unsupported format: {fmt}", "ERROR")
        return {"ok": False, "output_path": None, "final_size": 0,
                "attempts": 0, "fit": False}

    cap = int(float(target_mb) * 1024 * 1024) if target_mb else None
    best_size = None
    best_kept = False
    tried = 0
    runner = _attempt_gif if fmt == "gif" else _attempt_webp

    for att in ladder:
        if callable(cancel_cb) and cancel_cb():
            status_cb("[AnimExport] Cancelled.", "WARNING")
            break
        tried += 1
        label = (f"fps={att['fps']} w={att['width']} "
                 + (f"colors={att['colors']}" if fmt == "gif"
                    else f"q={att['quality']}"))
        # Keep the real extension - ffmpeg infers the muxer from it, and a
        # bare ".attempt" suffix fails with AVERROR(EINVAL).
        tmp_out = f"{out_path}.attempt.{fmt}"
        if not runner(ffmpeg, input_path, tmp_out, att, start, end):
            status_cb(f"[AnimExport] Attempt failed ({label}).", "WARNING")
            continue
        size = os.path.getsize(tmp_out) if os.path.exists(tmp_out) else 0
        if size <= 0:
            continue
        if best_size is None or size < best_size:
            os.replace(tmp_out, out_path)
            best_size = size
            best_kept = True
        else:
            try:
                os.remove(tmp_out)
            except Exception:
                pass
        if cap is None or size <= cap:
            status_cb(f"[AnimExport] {fmt.upper()} landed at "
                      f"{size} bytes ({label}, attempt {tried}).", "INFO")
            return {"ok": True, "output_path": out_path, "final_size": size,
                    "attempts": tried, "fit": True}

    if best_kept and best_size:
        status_cb(f"[AnimExport] Ladder exhausted; best effort is "
                  f"{best_size} bytes (over the "
                  f"{cap} byte cap)." if cap else
                  f"[AnimExport] Kept best effort ({best_size} bytes).",
                  "WARNING")
        return {"ok": True, "output_path": out_path, "final_size": best_size,
                "attempts": tried, "fit": cap is None or best_size <= cap}
    return {"ok": False, "output_path": None, "final_size": 0,
            "attempts": tried, "fit": False}
