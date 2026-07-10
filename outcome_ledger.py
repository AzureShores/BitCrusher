"""
outcome_ledger.py — unified per-encode outcome ledger + shadow predictors.

Stage 1 of BitCrusher's learning system. Every completed video encode appends
ONE rich record (content features, the full operating point, every retry-loop
observation, codec-race scores, VMAF v1 outcomes) to
user_settings/stats/ledger.jsonl. Predictors built on top start in SHADOW MODE:
they predict and log, but never act, until their logged accuracy demonstrably
beats the shipping heuristics (same trust-nothing pattern as the preprocessing
validator and the codec race).

Hard rules learned from past scars:
  - Records tag the VMAF model in use (v0.6.1-scale numbers must never train
    v1-scale quality predictions).
  - Only EFFECTIVE settings are recorded (a raced-away encoder must not be
    attributed to the requested one — the old poisoned-cache bug).
  - Fully offline: local jsonl, no network, ever.
"""
from __future__ import annotations

import json
import math
import os
import time

SCHEMA_VERSION = 1
_LEDGER_NAME = "ledger.jsonl"

# Feature keys used for neighbor distance (all numeric, from
# ml_heuristics.extract_media_features). Scales differ, so each has a rough
# normalizer to bring it to ~0..1.
_FEATURE_NORM = {
    "entropy_p95": 8.0,
    "spatial_complexity": 10.0,
    "graininess": 1.0,
    "text_edge_density": 1.0,
    "blockiness": 24.0,
    "edge_p95": 255.0,
    "scene_rate": 1.0,
    "motion_mad": 1.0,
}

_SHRINK_K = 3.0          # pseudo-samples pulling predictions toward the prior
_MAX_RECORDS = 5000      # newest records kept in memory when loading


def encoder_family(name: str) -> str:
    e = str(name or "").lower()
    if "265" in e or "hevc" in e:
        return "x265"
    if "av1" in e:
        return "av1"
    if "vp9" in e or "vpx" in e:
        return "vp9"
    if "264" in e or "avc" in e or e in ("x264", "h264"):
        return "x264"
    return e or "other"


def ledger_path(stats_dir: str) -> str:
    return os.path.join(stats_dir, _LEDGER_NAME)


def ledger_append(stats_dir: str, record: dict) -> bool:
    try:
        os.makedirs(stats_dir, exist_ok=True)
        rec = dict(record)
        rec.setdefault("schema", SCHEMA_VERSION)
        rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        with open(ledger_path(stats_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def ledger_load(stats_dir: str, encoder_fam: str | None = None,
                max_records: int = _MAX_RECORDS) -> list[dict]:
    out: list[dict] = []
    p = ledger_path(stats_dir)
    try:
        with open(p, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                if not isinstance(d, dict) or d.get("schema") != SCHEMA_VERSION:
                    continue
                if encoder_fam and encoder_family(
                        (d.get("op") or {}).get("encoder_eff")) != encoder_fam:
                    continue
                out.append(d)
    except Exception:
        return out
    return out[-max_records:]


# ---------------------------------------------------------------- deviation --
def attempt_deviation(v_bps: float, actual_bytes: float,
                      duration_s: float, audio_bps: float) -> float | None:
    """
    How far the real file landed from the naive size model, as a ratio:
      dev = actual_bytes / (video_bytes_expected + audio_bytes_expected)
    dev > 1 means the encoder+container overshot the naive estimate; < 1 means
    it undershot. This is the number the bitrate predictor learns: knowing dev
    in advance means the FIRST attempt can aim straight at the cap.
    """
    try:
        dur = max(0.1, float(duration_s))
        expected = (float(v_bps) + float(audio_bps or 0.0)) * dur / 8.0
        if expected <= 0 or float(actual_bytes) <= 0:
            return None
        return float(actual_bytes) / expected
    except Exception:
        return None


def record_deviation(rec: dict) -> float | None:
    """The deviation of a ledger record's FIRST attempt (the learnable one —
    later attempts are already corrected by the size controller)."""
    try:
        attempts = rec.get("attempts") or []
        if not attempts:
            return None
        v_bps, got = attempts[0][0], attempts[0][1]
        op = rec.get("op") or {}
        src = rec.get("src") or {}
        return attempt_deviation(v_bps, got, src.get("dur") or op.get("dur") or 0.0,
                                 op.get("audio_bps") or 0.0)
    except Exception:
        return None


# --------------------------------------------------------------- similarity --
def feature_vector(features: dict, width: int, height: int, fps: float,
                   target_bpp: float) -> list[float]:
    v = []
    f = features or {}
    for k, norm in _FEATURE_NORM.items():
        try:
            v.append(min(2.0, max(0.0, float(f.get(k) or 0.0) / norm)))
        except Exception:
            v.append(0.0)
    try:
        v.append(min(2.0, math.log10(max(1.0, float(width) * float(height))) / 7.0))
    except Exception:
        v.append(0.0)
    try:
        v.append(min(2.0, float(fps or 30.0) / 60.0))
    except Exception:
        v.append(0.5)
    try:
        # log-scale bits-per-pixel: 0.01 -> ~0, 1.0 -> ~1
        v.append(min(2.0, max(0.0, (math.log10(max(1e-4, target_bpp)) + 2.0) / 2.0)))
    except Exception:
        v.append(0.5)
    return v


def _dist(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(n)) / max(1, n))


def predict_deviation(stats_dir: str, features: dict, encoder: str,
                      width: int, height: int, fps: float,
                      v_bps: float, k_neighbors: int = 5) -> tuple[float, int]:
    """
    SHADOW-MODE predictor: expected first-attempt deviation for this content at
    this operating point, from the k nearest ledger records of the same encoder
    family. Distance-weighted mean, shrunk toward 1.0 (the neutral prior) by
    sample count so tiny histories barely move the needle.
    Returns (dev_pred, n_neighbors_used). (1.0, 0) when there is no data.
    """
    fam = encoder_family(encoder)
    recs = ledger_load(stats_dir, encoder_fam=fam)
    try:
        bpp = float(v_bps) / max(1.0, float(width) * float(height) * max(1.0, float(fps)))
    except Exception:
        bpp = 0.05
    q = feature_vector(features, width, height, fps, bpp)

    cands = []
    for r in recs:
        dev = record_deviation(r)
        if dev is None or not (0.2 <= dev <= 5.0):
            continue
        op = r.get("op") or {}
        try:
            r_bpp = float(op.get("v_bps") or 0.0) / max(
                1.0, float(op.get("width") or 1) * float(op.get("height") or 1)
                * max(1.0, float(op.get("fps") or 30.0)))
        except Exception:
            r_bpp = 0.05
        rv = feature_vector(r.get("features") or {}, op.get("width") or 0,
                            op.get("height") or 0, op.get("fps") or 30.0, r_bpp)
        cands.append((_dist(q, rv), dev))
    if not cands:
        return 1.0, 0
    cands.sort(key=lambda t: t[0])
    top = cands[:max(1, int(k_neighbors))]
    wsum = vsum = 0.0
    for d, dev in top:
        w = 1.0 / (0.05 + d)
        wsum += w
        vsum += w * dev
    knn = vsum / max(1e-9, wsum)
    n = len(top)
    dev_pred = (n / (n + _SHRINK_K)) * knn + (_SHRINK_K / (n + _SHRINK_K)) * 1.0
    return round(dev_pred, 4), n


# ------------------------------------------------------------ shadow report --
def shadow_report(stats_dir: str) -> dict:
    """
    How well have shadow predictions matched reality so far? Compares each
    record's logged prediction to its actual first-attempt deviation, next to
    the do-nothing baseline (assume dev == 1.0). The predictor earns the right
    to act only when pred_err beats base_err convincingly.
    """
    recs = ledger_load(stats_dir)
    pairs = []
    for r in recs:
        dev = record_deviation(r)
        pred = ((r.get("shadow") or {}).get("dev_pred"))
        n = int((r.get("shadow") or {}).get("n") or 0)
        if dev is None or pred is None or n <= 0:
            continue
        pairs.append((float(pred), float(dev)))
    if not pairs:
        return {"n": 0}
    pred_err = sum(abs(p - a) / a for p, a in pairs) / len(pairs)
    base_err = sum(abs(1.0 - a) / a for p, a in pairs) / len(pairs)
    within5 = sum(1 for p, a in pairs if abs(p - a) / a <= 0.05) / len(pairs)
    base5 = sum(1 for p, a in pairs if abs(1.0 - a) / a <= 0.05) / len(pairs)
    return {"n": len(pairs),
            "pred_mean_abs_err": round(pred_err, 4),
            "baseline_mean_abs_err": round(base_err, 4),
            "pred_within_5pct": round(within5, 3),
            "baseline_within_5pct": round(base5, 3),
            "verdict": ("predictor beats baseline" if pred_err < base_err
                        else "baseline still wins - keep shadowing")}


def seed_adjust(v_bps: float, dev_pred: float, n: int, *, min_n: int = 3,
                clamp: tuple = (0.85, 1.25), cap_bps: float | None = None) -> tuple[int, bool]:
    """
    Stage 2a (LIVE): turn a deviation prediction into a first-attempt bitrate
    seed. Guardrails: acts only with >= min_n similar past encodes, the
    correction factor is clamped to a sane band, and an upward correction never
    exceeds the feasibility cap. The size controller still validates and
    corrects every attempt after this one, so the worst case of a wrong
    prediction is exactly the old behaviour (a retry).
    Returns (adjusted_v_bps, acted).
    """
    try:
        if int(n) < int(min_n) or not dev_pred or dev_pred <= 0:
            return int(v_bps), False
        dev = max(float(clamp[0]), min(float(clamp[1]), float(dev_pred)))
        adj = int(float(v_bps) / dev)
        if cap_bps and cap_bps > 0:
            adj = min(adj, int(cap_bps))
        adj = max(24_000, adj)
        if abs(adj - int(v_bps)) < int(v_bps) * 0.01:
            return int(v_bps), False        # sub-1% nudge: not worth logging
        return adj, True
    except Exception:
        return int(v_bps), False


def build_record(*, input_path: str, features: dict, src: dict, op: dict,
                 attempts: list, race: dict | None, outcome: dict,
                 shadow: dict | None, vmaf_model: str) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "vmaf_model": str(vmaf_model or "vmaf_v0.6.1"),
        "input": str(input_path),
        "features": {k: features.get(k) for k in
                     ("entropy_p95", "spatial_complexity", "graininess",
                      "text_edge_density", "blockiness", "edge_p95",
                      "scene_rate", "motion_mad", "banding_risk",
                      "temporal_ssim_std", "sparsity_mean")
                     if isinstance(features, dict) and features.get(k) is not None},
        "src": dict(src or {}),
        "op": dict(op or {}),
        "attempts": [[int(a), int(b)] for a, b in (attempts or []) if a and b],
        "race": (dict(race) if race else None),
        "outcome": dict(outcome or {}),
        "shadow": (dict(shadow) if shadow else None),
    }
