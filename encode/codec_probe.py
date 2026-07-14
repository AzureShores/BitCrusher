from __future__ import annotations

import math
import os
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Callable

RunFn = Callable[[List[str]], subprocess.CompletedProcess]

E_IO_PROBE = "E_IO_PROBE"
E_RUN_FIT = "E_RUN_FIT"
E_VAL_PROBE = "E_VAL_PROBE"


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _default_run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _codec_args(encoder: str) -> List[str]:
    enc = (encoder or "").lower()
    if "svt" in enc and "av1" in enc:
        return ["-c:v", "libsvtav1", "-preset", "10", "-b:v", "0"]
    if ("aom" in enc and "av1" in enc) or "libaom-av1" in enc:
        return ["-c:v", "libaom-av1", "-cpu-used", "8", "-row-mt", "1", "-tile-columns", "1", "-tile-rows", "0", "-b:v", "0"]
    if "265" in enc or "x265" in enc or "hevc" in enc:
        return ["-c:v", "libx265", "-preset", "veryfast"]
    return ["-c:v", "libx264", "-preset", "veryfast"]


def _encode_segment_bytes_per_s(
    *,
    ffmpeg: str,
    path: str,
    t0: float,
    dur: float,
    vf: Optional[str],
    crf: int,
    encoder: str,
    run: RunFn,
) -> float:
    """Encode a short segment to a temp file and return bytes-per-second."""
    enc = (encoder or "").lower()
    suffix = ".mp4" if ("264" in enc or "x264" in enc or "265" in enc or "x265" in enc or "hevc" in enc) else ".mkv"
    with tempfile.NamedTemporaryFile(prefix="bc_cprobe_", suffix=suffix, delete=False) as tf:
        outp = tf.name
    try:
        cmd = [ffmpeg, "-hide_banner", "-y", "-v", "error",
               "-ss", f"{t0:.3f}", "-t", f"{max(0.1, float(dur)):.3f}", "-i", path]
        if vf:
            cmd += ["-vf", vf]
        cmd += _codec_args(encoder)
        cmd += ["-crf", str(int(crf)), "-an"]
        if suffix == ".mp4":
            cmd += ["-movflags", "+faststart"]
        cmd += [outp]
        p = run(cmd)
        if p.returncode != 0:
            raise RuntimeError(f"{E_IO_PROBE}: ffmpeg probe failed: {p.stderr[:2000]}")
        try:
            size = int(os.path.getsize(outp))
        except Exception:
            raise RuntimeError(f"{E_IO_PROBE}: could not read output file size")
        return float(size) / max(0.01, float(dur))
    finally:
        try:
            if os.path.exists(outp):
                os.remove(outp)
        except Exception:
            pass


def _fit_log_linear(points: List[Tuple[int, float]]) -> Tuple[float, float, float]:
    """
    Fit ln(y)=a*crf+b. Returns (a,b,r2). Deterministic and stable for small sample sizes.
    """
    if len(points) < 2:
        return (-0.25, math.log(max(1e-9, points[0][1] if points else 1.0)), 0.0)

    xs = [float(c) for c, _ in points]
    ys = [math.log(max(1e-9, float(v))) for _, v in points]
    n = float(len(xs))
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 1e-12:
        a = -0.25
    else:
        a = sxy / sxx
    b = my - a * mx

    # r2
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys))
    r2 = 0.0 if ss_tot <= 1e-12 else max(0.0, min(1.0, 1.0 - (ss_res / ss_tot)))
    return (float(a), float(b), float(r2))


def _confidence(r2: float, n: int, spread: float) -> float:
    # deterministic composite: more points + higher r2 + moderate spread => higher confidence
    n_term = max(0.0, min(1.0, (n - 1) / 5.0))
    r_term = max(0.0, min(1.0, r2))
    s_term = max(0.0, min(1.0, spread / 2.0))
    conf = 0.15 + 0.55 * r_term + 0.20 * n_term + 0.10 * s_term
    return float(max(0.0, min(1.0, conf)))


def probe_rate_quality(
    *,
    path: str,
    encoder: str,
    segments: List[Tuple[float, float]],
    grid: List[int],
    ffmpeg: str,
    vf: Optional[str] = None,
    run: RunFn = _default_run,
) -> Dict[str, Any]:
    """
    Unified probing API.

    NOTE: This stage provides deterministic fitting + confidence structure.
    Segment encode sizing is expected to be handled by upstream legacy probe path until
    temp-file sizing is plumbed in (keeps pipeline intact and testable via mocks).
    """
    if not path:
        raise ValueError(f"{E_VAL_PROBE}: empty path")
    if not segments or not grid:
        raise ValueError(f"{E_VAL_PROBE}: empty segments/grid")

    diagnostics: Dict[str, Any] = {"encoder": str(encoder)}
    points: List[Tuple[int, float]] = []

    # Sample each CRF point at a different scene position (round-robin) so the
    # fitted curve reflects the whole video, not just a calm opening scene.
    # Normalise each point's size by segment complexity (a shared anchor CRF
    # encoded on every segment) to remove position bias.
    seg_list = [(float(s[0]), float(s[1])) for s in segments if float(s[1]) > 0.0]
    if not seg_list:
        seg_list = [(0.0, 1.0)]
    # Bound the number of anchor encodes (1 per segment) so probing stays fast on
    # long videos; pick up to 4 spread-out positions.
    if len(seg_list) > 4:
        step = len(seg_list) / 4.0
        seg_list = [seg_list[int(i * step)] for i in range(4)]

    anchor_crf = int(sorted(grid)[len(grid) // 2])  # median CRF as the complexity yardstick
    seg_complexity: List[float] = []
    anchor_vals: List[float] = []
    for (t0, dur) in seg_list:
        try:
            bps = _encode_segment_bytes_per_s(
                ffmpeg=ffmpeg, path=path, t0=t0, dur=dur,
                vf=vf, crf=anchor_crf, encoder=encoder, run=run,
            )
        except Exception as exc:
            diagnostics.setdefault("errors", []).append(str(exc)[:200])
            bps = 0.0
        anchor_vals.append(float(bps))
    _valid = [v for v in anchor_vals if v > 0.0]
    _anchor_mean = (sum(_valid) / len(_valid)) if _valid else 0.0
    for v in anchor_vals:
        # complexity factor = how this scene's size compares to the average scene
        seg_complexity.append((v / _anchor_mean) if (_anchor_mean > 0.0 and v > 0.0) else 1.0)
    diagnostics["segments_probed"] = len(seg_list)
    diagnostics["anchor_crf"] = anchor_crf
    diagnostics["seg_complexity"] = [round(c, 3) for c in seg_complexity]

    # The anchor encodes (one per segment) average to the whole-video size at the
    # anchor CRF — exactly the point we want on the curve. Add it once.
    if _anchor_mean > 0.0:
        points.append((int(anchor_crf), float(_anchor_mean)))

    # Spread the remaining CRF grid points across segments round-robin.
    remaining = [c for c in grid if int(c) != anchor_crf]
    for idx, crf in enumerate(remaining):
        t0, dur = seg_list[idx % len(seg_list)]
        cf = seg_complexity[idx % len(seg_complexity)] if seg_complexity else 1.0
        try:
            bps = _encode_segment_bytes_per_s(
                ffmpeg=ffmpeg, path=path, t0=t0, dur=dur,
                vf=vf, crf=int(crf), encoder=encoder, run=run,
            )
            if bps > 0:
                # Normalise to an "average scene" so the curve isn't skewed by
                # which scene each CRF happened to land on.
                points.append((int(crf), float(bps) / max(0.2, cf)))
        except Exception as exc:
            diagnostics.setdefault("errors", []).append(str(exc)[:200])

    if not points:
        diagnostics["note"] = "probe_failed_using_fallback"

    # Fit log-linear curve to gathered points (or use conservative fallback).
    if len(points) >= 2:
        a, b, r2 = _fit_log_linear(points)
    else:
        a, b, r2 = (-0.25, math.log(max(1e-9, points[0][1]) if points else 1e-6), 0.0)

    spread = 0.0
    if len(points) >= 2:
        ys = [math.log(max(1e-9, p[1])) for p in points]
        spread = max(ys) - min(ys)
    conf = _confidence(r2, len(points), spread)

    # Error bounds: conservative +/- based on low confidence.
    # upper_pct used by planner as overshoot guard.
    upper_pct = 0.30 - 0.20 * conf  # 30% at conf=0, 10% at conf=1
    lower_pct = 0.10 + 0.10 * (1.0 - conf)

    return {
        "points": points,
        "fit": {"a": float(a), "b": float(b), "r2": float(r2)},
        "confidence": float(conf),
        "bounds": {"lower_pct": float(lower_pct), "upper_pct": float(upper_pct)},
        "diagnostics": diagnostics,
    }
