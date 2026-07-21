"""Thumbnail + contact-sheet generation for encoded outputs.

Opt-in post-encode step: writes <output_stem>_sheet.jpg (grid of frames
sampled evenly across the clip) and/or a single poster thumbnail next to
the output. Failure is WARN-only - it never fails the encode.

The grid/timestamp math is pure and unit-tested; the runners shell out
through the shared ffmpeg_exec helpers.
"""
from __future__ import annotations

import os
import subprocess

from encode.ffmpeg_exec import _sp_run, si, NO_WIN
from encode import ffmpeg_exec as _fx


def grid_timestamps(duration_s: float, cols: int = 4, rows: int = 4) -> list[float]:
    """Evenly spaced sample times for a cols x rows sheet.

    Samples are centered in their slots (never 0.0 or the very last
    frame, which are often black/credits). Degenerates gracefully for
    short sources; empty for unknown durations.
    """
    try:
        dur = float(duration_s)
        n = max(1, int(cols) * int(rows))
    except Exception:
        return []
    if dur <= 0:
        return []
    step = dur / n
    return [round((i + 0.5) * step, 3) for i in range(n)]


def build_sheet_filter(duration_s: float, cols: int = 4, rows: int = 4,
                       sheet_w: int = 1280) -> str | None:
    """Single-run ffmpeg filter: sample n frames evenly, tile into a grid."""
    n = max(1, int(cols) * int(rows))
    try:
        dur = float(duration_s)
    except Exception:
        return None
    if dur <= 0 or cols < 1 or rows < 1:
        return None
    fps = n / dur
    tile_w = max(64, int(sheet_w) // int(cols))
    return (f"fps={fps:.6f},scale={tile_w}:-1:flags=lanczos,"
            f"tile={int(cols)}x{int(rows)}")


def make_contact_sheet(input_path: str, out_path: str, *,
                       duration_s: float, cols: int = 4, rows: int = 4,
                       sheet_w: int = 1280, ffmpeg=None) -> bool:
    """Write a cols x rows contact sheet JPEG. True on success."""
    vf = build_sheet_filter(duration_s, cols, rows, sheet_w)
    if not vf:
        return False
    ffmpeg = ffmpeg or _fx.FFMPEG or "ffmpeg"
    try:
        r = _sp_run([ffmpeg, "-y", "-i", input_path, "-vf", vf,
                     "-frames:v", "1", "-update", "1", "-q:v", "3", out_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    startupinfo=si, creationflags=NO_WIN)
        return r.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False


def make_thumbnail(input_path: str, out_path: str, *, t: float = 0.0,
                   width: int = 640, ffmpeg=None) -> bool:
    """Write a single poster frame at time t (mirrors visual_compare's
    extractor). True on success."""
    ffmpeg = ffmpeg or _fx.FFMPEG or "ffmpeg"
    try:
        r = _sp_run([ffmpeg, "-y", "-ss", f"{max(0.0, float(t)):.3f}",
                     "-i", input_path, "-frames:v", "1",
                     "-vf", f"scale={int(width)}:-2", "-q:v", "3", out_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    startupinfo=si, creationflags=NO_WIN)
        return r.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False
