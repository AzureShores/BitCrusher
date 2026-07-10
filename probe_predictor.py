from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple
# Optional codec-aware probe core (pure Python fit + diagnostics).
try:
    from codec_probe import probe_rate_quality
except Exception:
    probe_rate_quality = None  # type: ignore


RunFn = Callable[[List[str]], subprocess.CompletedProcess]


def _default_run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _ffprobe_meta(ffprobe: str, path: str, run: RunFn) -> Tuple[float, int, int, float]:
    p = run([ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    data = json.loads(p.stdout or "{}")
    fmt = data.get("format", {}) or {}
    vstreams = [s for s in (data.get("streams") or []) if s.get("codec_type") == "video"]
    v = vstreams[0] if vstreams else {}

    dur = _safe_float(fmt.get("duration") or v.get("duration") or 0.0, 0.0)
    w = int(float(v.get("width") or 0) or 0)
    h = int(float(v.get("height") or 0) or 0)

    fr = 0.0
    afr = v.get("avg_frame_rate") or ""
    rfr = v.get("r_frame_rate") or ""
    for s in (afr, rfr):
        if isinstance(s, str) and "/" in s:
            num, den = s.split("/", 1)
            fr = _safe_float(num, 0.0) / max(1.0, _safe_float(den, 1.0))
            if fr > 0:
                break
    return float(dur), int(w), int(h), float(fr)


def _codec_probe_args(encoder: str) -> tuple[list[str], str]:
    """Return (ffmpeg_codec_args, output_suffix) for the given encoder."""
    enc = (encoder or "libx264").lower()
    if "svt" in enc and "av1" in enc:
        return ["-c:v", "libsvtav1", "-preset", "10", "-b:v", "0"], ".mkv"
    if ("aom" in enc and "av1" in enc) or "libaom-av1" in enc:
        return ["-c:v", "libaom-av1", "-cpu-used", "8", "-row-mt", "1",
                "-tile-columns", "1", "-tile-rows", "0", "-b:v", "0"], ".mkv"
    if "265" in enc or "x265" in enc or "hevc" in enc:
        return ["-c:v", "libx265", "-preset", "veryfast"], ".mp4"
    if "vp9" in enc or "libvpx" in enc:
        return ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "5",
                "-row-mt", "1", "-tile-columns", "1", "-b:v", "0"], ".mkv"
    # Default: libx264
    return ["-c:v", "libx264", "-preset", "veryfast"], ".mp4"


def _encode_probe(ffmpeg: str, path: str, t0: float, seg_s: float, vf: Optional[str], crf: int, run: RunFn,
                  encoder: str = "libx264") -> int:
    codec_args, suffix = _codec_probe_args(encoder)
    with tempfile.NamedTemporaryFile(prefix="bc_probe_", suffix=suffix, delete=False) as tf:
        outp = tf.name
    try:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{max(0.0, float(t0)):.6f}",
            "-t",
            f"{max(0.1, float(seg_s)):.6f}",
            "-i",
            path,
            "-map",
            "0:v:0",
            "-an",
        ]
        cmd += codec_args
        cmd += ["-crf", str(int(crf))]
        if vf:
            cmd += ["-vf", vf]
        if suffix == ".mp4":
            cmd += ["-movflags", "+faststart"]
        cmd += ["-y", outp]
        p = run(cmd)
        if p.returncode != 0:
            return -1
        try:
            return int(os.path.getsize(outp))
        except Exception:
            return -1
    finally:
        try:
            if os.path.exists(outp):
                os.remove(outp)
        except Exception:
            pass


def _fit_log_linear(points: List[Tuple[int, float]]) -> Tuple[float, float, float]:
    """
    Fit log(bytes_per_sec) = a*crf + b.
    Returns (a,b,r2). Deterministic.
    """
    xs: List[float] = []
    ys: List[float] = []
    for crf, bps in points:
        if bps > 0:
            xs.append(float(crf))
            ys.append(math.log(float(bps)))

    n = len(xs)
    if n < 2:
        return (-0.25, 0.0, 0.0)

    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx <= 1e-12:
        return (-0.25, ybar, 0.0)

    sxy = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    a = sxy / sxx
    b = ybar - a * xbar

    yhat = [a * x + b for x in xs]
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    ss_res = sum((y - yh) ** 2 for y, yh in zip(ys, yhat))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else 0.0
    r2 = float(max(0.0, min(1.0, r2)))
    return (float(a), float(b), float(r2))


def _confidence(r2: float, n: int, spread: float) -> float:
    n_factor = min(1.0, max(0.0, (n - 1) / 5.0))
    spread_factor = min(1.0, max(0.0, spread / 0.35))  # ln(bps) spread
    return float(max(0.0, min(1.0, (0.15 + 0.85 * r2) * n_factor * (0.4 + 0.6 * spread_factor))))


def predict_crf_and_bitrate(
    ffmpeg: str,
    ffprobe: str,
    path: str,
    target_bytes: int,
    duration: float,
    width: int,
    height: int,
    fps: float,
    audio_bps: int,
    encoder: str = "libx264",
    container_overhead: float = 1.02,
    scale_width: Optional[int] = None,
    fps_out: Optional[float] = None,
    run: RunFn = _default_run,
) -> Dict[str, Any]:
    """
    Backwards-compatible keys:
      - crf
      - video_bps
      - dur,w,h,fps

    Extended keys (internal-only):
      - confidence
      - curve_a, curve_b, curve_r2
      - curve_points (list[(crf, bytes_per_sec)])
    """
    dur = float(duration) if duration and duration > 0 else _ffprobe_meta(ffprobe, path, run)[0]
    dur = max(0.1, float(dur))

    a_bps = int(max(0, int(audio_bps)))
    audio_bytes = (a_bps * dur) / 8.0
    core_budget = (float(target_bytes) / max(1.0, float(container_overhead))) - audio_bytes
    vid_budget_bytes_per_s = max(1.0, core_budget / dur)

    vf: List[str] = []
    sw = int(scale_width) if scale_width else int(width)
    if sw > 0 and int(width) > 0 and sw < int(width):
        vf.append(f"scale={sw}:-2")
    fo = float(fps_out) if fps_out else float(fps)
    if fo > 0 and float(fps) > 0 and abs(fo - float(fps)) > 0.2:
        vf.append(f"fps=fps={fo:.3f}")
    vf_str = ",".join(vf) if vf else None

    enc = (encoder or "libx264").strip()
    enc_l = enc.lower()
    # Deterministic, codec-specific probe CRF grids (conservative).
    if "svtav1" in enc_l or ("svt" in enc_l and "av1" in enc_l):
        crf_grid = [22, 26, 30, 34, 38]
    elif "aom" in enc_l and "av1" in enc_l:
        crf_grid = [20, 24, 28, 32, 36]
    elif "265" in enc_l or "x265" in enc_l or "hevc" in enc_l:
        crf_grid = [18, 20, 22, 24, 26, 28]
    else:
        crf_grid = [18, 20, 22, 24, 26, 28]


    # Scale probe segment with duration: 3% of video length, at least 1s, at most 5s.
    # Longer probes give more stable bitrate estimates and reduce retry count.
    seg_s = max(1.0, min(5.0, 0.03 * dur))
    ts = [max(0.0, (i + 1) * (dur / (len(crf_grid) + 1)) - seg_s / 2.0) for i in range(len(crf_grid))]

    points: List[Tuple[int, float]] = []

    # Prefer unified codec_probe if available; otherwise fall back to legacy per-segment encoding.
    if probe_rate_quality is not None:
        segments = [(float(t0), float(seg_s)) for t0 in ts]
        grid = [int(c) for c in crf_grid]
        try:
            pr = probe_rate_quality(
                path=path,
                encoder=enc,
                segments=segments,
                grid=grid,
                ffmpeg=ffmpeg,
                vf=vf_str,
                run=run,
            )
            # pr["points"] is list[(crf:int, bytes_per_s:float)]
            for crf_i, bps in pr.get("points", []):
                if bps and bps > 0:
                    points.append((int(crf_i), float(bps)))
            # Thread through richer signals
            fit_override = pr.get("fit", {})
            bounds_override = pr.get("bounds", {})
            diag_override = pr.get("diagnostics", {})
            conf_override = float(pr.get("confidence", 0.0) or 0.0)
        except Exception:
            fit_override = {}
            bounds_override = {}
            diag_override = {"note": "codec_probe_failed"}
            conf_override = 0.0
    else:
        fit_override = {}
        bounds_override = {}
        diag_override = {}
        conf_override = 0.0

        for crf, t0 in zip(crf_grid, ts):
            sz = _encode_probe(ffmpeg, path, t0, seg_s, vf_str, crf, run, encoder=enc)
            if sz and sz > 0:
                points.append((int(crf), float(sz) / float(seg_s)))


    a, b, r2 = _fit_log_linear(points)
    ys = [math.log(max(1e-9, p[1])) for p in points] if points else [0.0]
    spread = (max(ys) - min(ys)) if ys else 0.0
    conf = _confidence(r2, len(points), float(spread))

    denom = a if abs(a) > 1e-6 else -0.25
    crf_pred = float((math.log(max(1.0, vid_budget_bytes_per_s)) - b) / denom)
    crf_pred = float(min(34.0, max(14.0, crf_pred)))

    vbps_pred = float(vid_budget_bytes_per_s) * 8.0

    return {
        "crf": float(crf_pred),
        "video_bps": float(vbps_pred),
        "dur": float(dur),
        "w": float(width),
        "h": float(height),
        "fps": float(fps),
        "curve_a": float(a),
        "curve_b": float(b),
        "curve_r2": float(r2),
        "curve_points": points,
        # Codec-aware enrichments (backward-compatible).
        "encoder": str(enc),
        "error_bounds": bounds_override,
        "fit": fit_override,
        "diagnostics": diag_override,
        # If codec_probe provided a confidence, it supersedes computed confidence.
        "confidence": float(conf_override) if conf_override > 0.0 else float(conf),
    }
