from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from fractions import Fraction

import encode.ffmpeg_exec as ffmpeg_exec
from encode.ffmpeg_exec import si, NO_WIN, _ffmpeg_has_filter, probe_video_stream_dims

LOG = logging.getLogger("BitCrusher")


def vmaf_quality_label(score: float) -> str:
    """Human-readable bucket for a VMAF score (0-100)."""
    s = float(score)
    if s >= 95.0:  return "visually lossless"
    if s >= 90.0:  return "excellent"
    if s >= 80.0:  return "good"
    if s >= 70.0:  return "fair"
    if s >= 60.0:  return "mediocre"
    return "poor"



# VMAF model resolution. libvmaf's v1 identifier varies by build and older
# builds don't embed it, so resolve the best available model once instead of
# hardcoding one. Pref (env BC_VMAF_MODEL or module pref, default "auto"):
# raw "model=..." used verbatim | "default"/"v0.6.1" | "neg" | "4k" |
# "v1" (require, warn if absent) | "auto" = v1 if available else v0.6.1.
# Drop a model .json into tools/vmaf_models/ to enable v1 offline.
_VMAF_V1_CANDIDATES = ["vmaf_v1", "vmaf_v1neg", "vmaf_v1_neg",
                       "vmaf_v1_hd", "vmaf_v1_1080p", "vmaf_v1_4k", "vmaf_4k_v1"]
_VMAF_MODEL_PREF = None            # GUI/CLI may set this; env still wins
_VMAF_MODEL_CACHE = {"resolved": None, "logged": False}


def set_vmaf_model_pref(pref: str | None):
    """Set the preferred VMAF model ('auto'|'v1'|'neg'|'4k'|'default'|raw). Resets
    the resolution cache so the next measurement re-resolves."""
    global _VMAF_MODEL_PREF
    _VMAF_MODEL_PREF = (str(pref).strip() if pref else None)
    _VMAF_MODEL_CACHE["resolved"] = None
    _VMAF_MODEL_CACHE["logged"] = False


def _vmaf_models_dir() -> str:
    try:
        _script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(_script_dir, "tools", "vmaf_models")
    except Exception:
        return os.path.join("tools", "vmaf_models")


def _first_v1_model_file() -> str | None:
    """A locally-dropped VMAF v1 model .json (offline enable path), or None."""
    try:
        d = _vmaf_models_dir()
        if os.path.isdir(d):
            cands = [f for f in os.listdir(d)
                     if f.lower().endswith(".json") and "v1" in f.lower()]
            if cands:
                return os.path.join(d, sorted(cands)[0])
    except Exception:
        pass
    return None


def _escape_vmaf_opt_path(p: str) -> str:
    # ffmpeg's filter-option parser treats ':' and '\\' specially; normalise and
    # escape the drive-letter colon so a Windows path survives model=path=...
    return os.path.abspath(p).replace("\\", "/").replace(":", "\\:")


def _vmaf_model_loads(model_arg: str, ff: str) -> bool:
    """Quick 1-frame probe: does libvmaf actually accept this model= value on the
    current build? Empty arg == build default, always OK. File models are probed
    with cwd set to the model's own directory and a bare-filename reference —
    Windows drive-colon paths break the filter-option parser no matter how they
    are escaped (same reason log_path runs from a temp cwd)."""
    if not model_arg:
        return True
    if not ff:
        return False
    run_cwd = None
    if model_arg.startswith("path="):
        p = model_arg[len("path="):]
        if not os.path.isfile(p):
            return False
        run_cwd = os.path.dirname(os.path.abspath(p))
        model_arg = f"path={os.path.basename(p)}"
    _null_dev = "NUL" if os.name == "nt" else "/dev/null"
    # 320x180 x 3 frames, explicit yuv420p: VMAF v1's SpEED/CAMBI features have
    # minimum-size requirements a 64x64 single-frame probe fails ("image too
    # small"), which would wrongly mark a perfectly good model as unloadable.
    _src = "testsrc2=size=320x180:rate=5:duration=1"
    filt = (f"[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];"
            f"[a][b]libvmaf=model={model_arg}:n_threads=1")
    cmd = [ff, "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", _src, "-f", "lavfi", "-i", _src,
           "-lavfi", filt, "-frames:v", "3", "-f", "null", _null_dev]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           cwd=run_cwd, startupinfo=si, creationflags=NO_WIN)
        return int(getattr(r, "returncode", 1) or 0) == 0
    except Exception:
        return False


def resolve_vmaf_model(ff: str | None = None) -> str:
    """
    Resolve the libvmaf `model=...` argument to use (or '' for the build default
    vmaf_v0.6.1). Validated against the real ffmpeg and cached per process, so we
    never inject a model that would make VMAF calls fail.
    """
    if _VMAF_MODEL_CACHE["resolved"] is not None:
        return _VMAF_MODEL_CACHE["resolved"]
    ff = ff or ffmpeg_exec.FFMPEG
    pref = str(os.environ.get("BC_VMAF_MODEL") or _VMAF_MODEL_PREF or "auto").strip()
    low = pref.lower()
    resolved, note = "", None

    def _ok(arg: str) -> bool:
        return _vmaf_model_loads(arg, ff)

    def _try_v1() -> str | None:
        f = _first_v1_model_file()
        if f:
            # Raw absolute path; per-call plumbing rewrites it to a bare-filename
            # reference from a safe cwd (Windows drive colons break the parser).
            arg = f"path={os.path.abspath(f)}"
            if _ok(arg):
                return arg
        for name in _VMAF_V1_CANDIDATES:
            if _ok(f"version={name}"):
                return f"version={name}"
        return None

    if "=" in pref:
        if _ok(pref):
            resolved, note = pref, f"custom ({pref})"
        else:
            note = f"custom model '{pref}' not accepted by this build; using v0.6.1"
    elif low in ("default", "v0.6.1", "vmaf_v0.6.1", ""):
        resolved = ""
    elif low == "neg":
        if _ok("version=vmaf_v0.6.1neg"):
            resolved, note = "version=vmaf_v0.6.1neg", "v0.6.1 NEG (no-enhancement-gain)"
        else:
            note = "NEG model unavailable; using v0.6.1"
    elif low == "4k":
        if _ok("version=vmaf_4k_v0.6.1"):
            resolved, note = "version=vmaf_4k_v0.6.1", "4K v0.6.1"
    elif low in ("v1", "vmaf_v1"):
        v1 = _try_v1()
        if v1:
            resolved, note = v1, f"VMAF v1 ({v1})"
        else:
            note = ("VMAF v1 requested but not available in this ffmpeg/libvmaf build — "
                    "update ffmpeg or drop the v1 model .json into tools/vmaf_models/. "
                    "Using v0.6.1 for now.")
    else:  # "auto"
        v1 = _try_v1()
        if v1:
            resolved, note = v1, f"VMAF v1 ({v1})"
        else:
            resolved = ""  # safe: keep the calibrated v0.6.1 scale until v1 exists

    _VMAF_MODEL_CACHE["resolved"] = resolved
    if note and not _VMAF_MODEL_CACHE["logged"]:
        _VMAF_MODEL_CACHE["logged"] = True
        try:
            logging.getLogger("BitCrusher").info("VMAF model: %s", note)
            _cb = globals().get("status_callback")
            if callable(_cb):
                # A non-default model shifts the score scale — flag it once so the
                # user knows reference numbers / min-VMAF thresholds may need a nudge.
                lvl = "WARNING" if ("using v0.6.1" in note and "v1" in note.lower()) else "INFO"
                _cb(f"[VMAF] {note}", level=lvl)
        except Exception:
            pass
    return resolved


def _vmaf_model_opt(ff: str | None = None, work_dir: str | None = None) -> str:
    """
    The 'model=...:' fragment to splice into a libvmaf filter, or '' for default.
    File-based models: the model json is COPIED into `work_dir` (the directory the
    ffmpeg VMAF call runs from, same as log_path) and referenced by bare filename —
    a Windows absolute path in the filter option breaks its parser at the drive
    colon regardless of escaping.
    """
    m = resolve_vmaf_model(ff)
    if not m:
        return ""
    if m.startswith("path="):
        src = m[len("path="):]
        base = os.path.basename(src)
        if work_dir:
            try:
                dst = os.path.join(work_dir, base)
                if not os.path.exists(dst):
                    shutil.copyfile(src, dst)
                return f"model=path={base}:"
            except Exception:
                return ""      # can't stage the model — measure with the default
        return ""              # no safe cwd available — default model
    return f"model={m}:"


def compute_vmaf(reference_path: str, distorted_path: str, *,
                 sample_fps: float = 12.0,
                 duration_s: float = 0.0,
                 ffmpeg: str | None = None) -> dict | None:
    """
    Measure the perceptual quality of `distorted_path` (the compressed output)
    against `reference_path` (the original) using libvmaf.

    Returns {"vmaf": float, "label": str, "harmonic": float|None} or None when
    VMAF can't be computed (filter missing, mismatch, error). The distorted
    stream is scaled to the reference resolution and both streams are resampled
    to `sample_fps` so frame counts align even when the encode changed
    resolution or frame rate — this also makes measurement several times faster
    than full-rate scoring with negligible accuracy loss.
    """
    ff = ffmpeg or ffmpeg_exec.FFMPEG
    if not ff or not _ffmpeg_has_filter("libvmaf"):
        return None
    try:
        if not (os.path.exists(reference_path) and os.path.exists(distorted_path)):
            return None
    except Exception:
        return None

    # Reference resolution + frame rate (VMAF needs both streams at the same size).
    ref_w = ref_h = 0
    ref_fps = 30.0
    try:
        _vst_ref = probe_video_stream_dims(reference_path)
        ref_w = int(_vst_ref.get("width") or 0)
        ref_h = int(_vst_ref.get("height") or 0)
        try:
            ref_fps = float(Fraction(_vst_ref.get("avg_frame_rate") or "30/1"))
        except Exception:
            ref_fps = 30.0
    except Exception:
        return None
    if ref_w <= 0 or ref_h <= 0:
        return None

    n_threads = max(1, min(16, (os.cpu_count() or 4)))
    fps = max(1.0, float(sample_fps))
    # Rebase both streams to t=0 (PTS-STARTPTS) before sampling: a start-PTS
    # offset from re-muxing otherwise misaligns libvmaf's framesync by one
    # frame, tanking VMAF 20-30pts on rapid-cut content. Then resample both to
    # the same fixed fps so VFR sources compare identical wall-clock frames.
    _sync = f"setpts=PTS-STARTPTS,fps={fps:g}"
    # Write the log to a bare filename inside a temp working dir: a full Windows
    # path like C:/... contains a colon that libvmaf's filter-option parser
    # treats as an option separator, so we run with cwd set to the temp dir.
    work_dir = tempfile.mkdtemp(prefix="bc_vmaf_")
    log_name = "vmaf.json"
    log_path = os.path.join(work_dir, log_name)
    ref_abs = os.path.abspath(reference_path)
    dist_abs = os.path.abspath(distorted_path)
    # 0 = distorted (first input), 1 = reference (second input).
    lavfi = (
        f"[0:v]scale={ref_w}:{ref_h}:flags=bicubic,{_sync},format=yuv420p[dist];"
        f"[1:v]{_sync},format=yuv420p[ref];"
        f"[dist][ref]libvmaf={_vmaf_model_opt(ff, work_dir)}n_threads={n_threads}:log_fmt=json:log_path={log_name}"
    )
    cmd = [ff, "-hide_banner", "-loglevel", "error"]
    # Cap measurement window on very long videos for speed (sampled, not full).
    # MUST cap BOTH inputs: capping only one made framesync repeat the shorter
    # stream's last frame against minutes of moving reference — VMAF collapsed
    # to ~20 on perfectly good encodes of long videos.
    _t_cap = (["-t", "120"] if (duration_s and duration_s > 180.0) else [])
    _null_dev = "NUL" if os.name == "nt" else "/dev/null"
    cmd += _t_cap + ["-i", dist_abs] + _t_cap + ["-i", ref_abs] \
        + ["-lavfi", lavfi, "-an", "-sn", "-f", "null", _null_dev]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             cwd=work_dir, startupinfo=si, creationflags=NO_WIN)
        if getattr(res, "returncode", 1) != 0:
            return None
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass

    # Parse mean VMAF across libvmaf JSON schema variants.
    mean = harmonic = None
    try:
        pooled = (data.get("pooled_metrics") or {}).get("vmaf") or {}
        if "mean" in pooled:
            mean = float(pooled["mean"])
        if "harmonic_mean" in pooled:
            harmonic = float(pooled["harmonic_mean"])
    except Exception:
        pass
    # Per-frame values (always, when present) so we can compute the FLOOR metrics
    # that beat the "average trap": a clip can average 97 while one hard scene
    # sits at 70 — the worst rolling window is what a viewer actually notices.
    vals = []
    try:
        for fr in (data.get("frames") or []):
            v = (fr.get("metrics") or {}).get("vmaf")
            if v is not None:
                vals.append(float(v))
    except Exception:
        vals = []
    if mean is None and vals:
        mean = sum(vals) / len(vals)
    if mean is None:
        try:
            mean = float(data.get("VMAF score"))
        except Exception:
            return None

    # Reliability guard: near-zero per-frame VMAF is almost never real (even a
    # heavily-crushed encode scores 20-40, not ~0). A non-trivial fraction of
    # ~0 frames means the two streams were misaligned (start-PTS offset, VFR
    # drift, wrong-length inputs) — a MEASUREMENT fault, not quality. Flag it so
    # callers can distrust the number and, crucially, keep it out of the learning
    # ledger instead of poisoning quality predictions with a phantom collapse.
    _zero_frac = (sum(1 for v in vals if v < 2.0) / len(vals)) if vals else 0.0
    _reliable = _zero_frac < 0.02

    # Worst rolling ~2s window + low percentiles (scene-floor proxies, no re-encode).
    p1 = p5 = min_window = None
    _mw_idx = None
    if vals:
        win = max(4, int(round(2.0 * max(1.0, float(sample_fps)))))
        p1, p5, min_window, _mw_idx = _vmaf_low_metrics(vals, win)
    _spread = (round(float(mean) - float(min_window), 2)
               if (min_window is not None and mean is not None) else None)
    # Locate the worst window in source time. compute_vmaf decimates to sample_fps,
    # so output-frame index i maps back to source time ≈ i / sample_fps (the select
    # keeps every step-th frame and setpts re-times to sample_fps). For sources
    # capped at 120s the offset is still within the measured window.
    _mw_at = (round(float(_mw_idx) / max(1.0, float(sample_fps)), 1)
              if _mw_idx is not None else None)

    # Downsampled VMAF-over-time series (<= 64 points) for the result-dashboard
    # sparkline. Averaged into buckets so a single dip isn't lost; kept small so
    # it's cheap to store in the ledger and render.
    _series = _downsample_series(vals, 64) if vals else None
    # Time the series spans (source seconds of the measured region), so a
    # renderer can map min_window_at back onto the downsampled points regardless
    # of how many buckets survived.
    _series_span = (round(len(vals) / max(1.0, float(sample_fps)), 3)
                    if _series else None)

    return {"vmaf": round(float(mean), 2),
            "harmonic": (round(float(harmonic), 2) if harmonic is not None else None),
            "p1": (round(float(p1), 2) if p1 is not None else None),
            "p5": (round(float(p5), 2) if p5 is not None else None),
            "min_window": (round(float(min_window), 2) if min_window is not None else None),
            "min_window_at": _mw_at,
            "spread": _spread,
            "series": _series,
            "series_span_s": _series_span,
            "reliable": bool(_reliable),
            "zero_frac": round(_zero_frac, 4),
            "label": vmaf_quality_label(mean)}


def _downsample_series(vals: list, max_points: int) -> list:
    """Bucket-average a per-frame series down to <= max_points values (rounded),
    preserving overall shape for a sparkline without storing every frame."""
    try:
        n = len(vals)
        if n == 0:
            return []
        if n <= max_points:
            return [round(float(v), 1) for v in vals]
        step = n / float(max_points)
        out = []
        for i in range(max_points):
            a = int(i * step)
            b = max(a + 1, int((i + 1) * step))
            chunk = vals[a:b]
            if chunk:
                out.append(round(sum(float(x) for x in chunk) / len(chunk), 1))
        return out
    except Exception:
        return []


def compute_xpsnr(reference_path: str, distorted_path: str, *,
                  sample_fps: float = 12.0, duration_s: float = 0.0,
                  ffmpeg: str | None = None) -> dict | None:
    """
    Second-opinion perceptual metric: XPSNR (extended perceptually-weighted PSNR,
    ITU-T standardized, built into ffmpeg — no external binary). Orthogonal to
    VMAF: it weights error by local perceptual masking rather than a learned
    model, so it disagrees with VMAF exactly where VMAF is weakest/gameable
    (anime, screen/text, over-sharpening). Same stream alignment as compute_vmaf
    (setpts=PTS-STARTPTS + fps rebase — see that function's fix). Returns the
    luma channel as {"xpsnr", "min_window", "min_window_at", "spread", "series",
    "series_span_s", "reliable"} (dB; higher is better), or None.
    """
    ff = ffmpeg or ffmpeg_exec.FFMPEG
    if not ff or not _ffmpeg_has_filter("xpsnr"):
        return None
    try:
        if not (os.path.exists(reference_path) and os.path.exists(distorted_path)):
            return None
    except Exception:
        return None
    try:
        _vst = probe_video_stream_dims(reference_path)
        ref_w = int(_vst.get("width") or 0)
        ref_h = int(_vst.get("height") or 0)
    except Exception:
        return None
    if ref_w <= 0 or ref_h <= 0:
        return None

    fps = max(1.0, float(sample_fps))
    _sync = f"setpts=PTS-STARTPTS,fps={fps:g}"
    work = tempfile.mkdtemp(prefix="bc_xpsnr_")
    stats_name = "xpsnr.log"
    stats_path = os.path.join(work, stats_name)
    lavfi = (
        f"[0:v]scale={ref_w}:{ref_h}:flags=bicubic,{_sync},format=yuv420p[d];"
        f"[1:v]{_sync},format=yuv420p[r];"
        f"[d][r]xpsnr=stats_file={stats_name}"
    )
    _t_cap = (["-t", "120"] if (duration_s and duration_s > 180.0) else [])
    _null_dev = "NUL" if os.name == "nt" else "/dev/null"
    cmd = [ff, "-hide_banner", "-loglevel", "error"] \
        + _t_cap + ["-i", os.path.abspath(distorted_path)] \
        + _t_cap + ["-i", os.path.abspath(reference_path)] \
        + ["-lavfi", lavfi, "-an", "-sn", "-f", "null", _null_dev]
    vals: list[float] = []
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             cwd=work, startupinfo=si, creationflags=NO_WIN)
        if getattr(res, "returncode", 1) != 0:
            return None
        with open(stats_path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.search(r"XPSNR\s+y:\s*([0-9.]+|inf)", ln)
                if not m:
                    continue
                tok = m.group(1)
                # Identical frames report 'inf'; cap so they don't skew the mean.
                vals.append(60.0 if tok == "inf" else float(tok))
    except Exception:
        return None
    finally:
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass

    if not vals:
        return None
    mean = sum(vals) / len(vals)
    win = max(4, int(round(2.0 * max(1.0, float(sample_fps)))))
    _p1, _p5, min_window, _mw_idx = _vmaf_low_metrics(vals, win)
    _spread = (round(float(mean) - float(min_window), 2)
               if min_window is not None else None)
    _mw_at = (round(float(_mw_idx) / max(1.0, float(sample_fps)), 1)
              if _mw_idx is not None else None)
    _series = _downsample_series(vals, 64)
    _series_span = round(len(vals) / max(1.0, float(sample_fps)), 3) if _series else None
    # A block of ~0 dB frames is misalignment, same signature as the VMAF guard.
    _zero_frac = sum(1 for v in vals if v < 5.0) / len(vals)
    return {"xpsnr": round(float(mean), 2),
            "min_window": (round(float(min_window), 2) if min_window is not None else None),
            "min_window_at": _mw_at,
            "spread": _spread,
            "series": _series,
            "series_span_s": _series_span,
            "reliable": bool(_zero_frac < 0.02),
            "label": xpsnr_quality_label(mean)}


def xpsnr_quality_label(db: float) -> str:
    """Rough perceptual bucket for an XPSNR-Y value (dB). Calibrated loosely —
    XPSNR is used as a relative cross-check, not an absolute gate."""
    try:
        d = float(db)
    except Exception:
        return "unknown"
    if d >= 42.0:
        return "visually lossless"
    if d >= 37.0:
        return "excellent"
    if d >= 32.0:
        return "good"
    if d >= 27.0:
        return "fair"
    return "poor"


def _vmaf_low_metrics(vals: list, win: int):
    """From per-frame VMAF values return (p1, p5, worst_rolling_window_mean,
    worst_window_start_index). The window mean resists single-frame outliers
    that would tank a raw min; the start index locates the valley in the stream."""
    n = len(vals)
    if n == 0:
        return None, None, None, None

    def _pct(sorted_vals, q):
        if not sorted_vals:
            return None
        idx = int(max(0, min(len(sorted_vals) - 1, round(q / 100.0 * (len(sorted_vals) - 1)))))
        return sorted_vals[idx]

    sv = sorted(vals)
    p1 = _pct(sv, 1.0)
    p5 = _pct(sv, 5.0)
    w = max(1, min(int(win), n))
    if w >= n:
        return p1, p5, sum(vals) / n, 0
    # Rolling window mean via a running sum; keep the smallest and where it started.
    run = sum(vals[:w])
    min_window = run / w
    min_idx = 0
    for i in range(w, n):
        run += vals[i] - vals[i - w]
        m = run / w
        if m < min_window:
            min_window = m
            min_idx = i - w + 1
    return p1, p5, min_window, min_idx


def vmaf_floor_score(vmaf: dict | None, objective: str = "window") -> float | None:
    """
    Pick the scalar a size-target / quality-floor decision should optimize, per
    the configured objective, with a graceful fallback chain so it always yields
    a number when any VMAF exists:
      window  -> worst ~2s rolling window (default; attacks the average trap)
      p5/p1   -> low percentile of frame scores
      harmonic-> harmonic mean (dips toward low scores)
      mean    -> arithmetic mean (the classic "average trap" number)
    """
    if not vmaf:
        return None
    obj = str(objective or "window").strip().lower()
    order = {
        "window": ("min_window", "p5", "harmonic", "vmaf"),
        "p5":     ("p5", "p1", "min_window", "harmonic", "vmaf"),
        "p1":     ("p1", "p5", "min_window", "harmonic", "vmaf"),
        "harmonic": ("harmonic", "min_window", "vmaf"),
        "mean":   ("vmaf",),
    }.get(obj, ("min_window", "p5", "harmonic", "vmaf"))
    for k in order:
        v = vmaf.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


_VMAF_OBJECTIVE_PREF = None


def set_vmaf_objective_pref(pref: str | None):
    global _VMAF_OBJECTIVE_PREF
    _VMAF_OBJECTIVE_PREF = (str(pref).strip() if pref else None)


def resolve_vmaf_objective(advanced_options: dict | None = None) -> str:
    """Objective name from job opts > env BC_VMAF_OBJECTIVE > module pref > 'window'."""
    for src in ((advanced_options or {}).get("vmaf_objective"),
                os.environ.get("BC_VMAF_OBJECTIVE"),
                _VMAF_OBJECTIVE_PREF):
        s = str(src or "").strip().lower()
        if s in ("window", "p5", "p1", "harmonic", "mean"):
            return s
    return "window"
