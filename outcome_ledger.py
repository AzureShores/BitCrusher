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


# Parse cache: the ledger is append-only, so a (size, mtime) key is a safe
# invalidation signal. A single pre-flight fans out to predict_quality once per
# candidate codec family; without this, each call re-read and re-parsed the
# whole jsonl (up to _MAX_RECORDS) from disk. Key on the resolved path so
# different stats dirs don't collide.
_PARSE_CACHE: dict[str, tuple[tuple[int, int], list[dict]]] = {}


def _load_all_records(p: str) -> list[dict]:
    """Parse every valid record in the ledger file at path `p`, memoized by the
    file's (size, mtime_ns). Returns the shared list — callers must not mutate."""
    try:
        st = os.stat(p)
        key = (st.st_size, st.st_mtime_ns)
    except OSError:
        _PARSE_CACHE.pop(p, None)
        return []
    cached = _PARSE_CACHE.get(p)
    if cached is not None and cached[0] == key:
        return cached[1]
    recs: list[dict] = []
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
                recs.append(d)
    except OSError:
        return []
    _PARSE_CACHE[p] = (key, recs)
    return recs


def ledger_load(stats_dir: str, encoder_fam: str | None = None,
                max_records: int = _MAX_RECORDS) -> list[dict]:
    recs = _load_all_records(ledger_path(stats_dir))
    if encoder_fam:
        recs = [d for d in recs if encoder_family(
            (d.get("op") or {}).get("encoder_eff")) == encoder_fam]
    return recs[-max_records:]


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


# -------------------------------------------------------- codec-winner prior --
# Quality floors used by the pre-flight guardrail (VMAF v1 scale). Below the
# collapse floor a delivery is visibly broken; the warn floor is "gritty but
# watchable". Tuned against the cold-start corpus (betty_boop @3MB x265 landed
# min_window ~44 = collapse; ~70 worst = warn).
QUALITY_COLLAPSE_WORST = 60.0
QUALITY_WARN_WORST = 75.0
OVERSHOOT_WARN_RATIO = 1.06     # predicted achieved-size / target above this = cap risk
_MIN_PRIOR_N = 3                # neighbors required before the prior will speak


def _scale_key(vmaf_model: str) -> str:
    """Collapse a vmaf_model tag to its quality SCALE. v0.6.1 and v1 numbers are
    not interchangeable (the poisoned-scale scar), so quality predictions may
    only borrow from records measured on the same scale."""
    m = str(vmaf_model or "").lower()
    if "v0.6.1" in m or "0.6.1" in m:
        return "v0.6.1"
    if "v1" in m or "_v1" in m or "vmaf_v1" in m:
        return "v1"
    return m or "unknown"


def _op_feature_vector(r: dict) -> list[float]:
    """Feature vector for a stored record at its own operating point."""
    op = r.get("op") or {}
    try:
        r_bpp = float(op.get("v_bps") or 0.0) / max(
            1.0, float(op.get("width") or 1) * float(op.get("height") or 1)
            * max(1.0, float(op.get("fps") or 30.0)))
    except Exception:
        r_bpp = 0.05
    return feature_vector(r.get("features") or {}, op.get("width") or 0,
                          op.get("height") or 0, op.get("fps") or 30.0, r_bpp)


def predict_quality(stats_dir: str, features: dict, encoder: str,
                    width: int, height: int, fps: float, v_bps: float,
                    target_bytes: float, *, vmaf_model: str | None = None,
                    k_neighbors: int = 5) -> dict:
    """
    Predict how a given encoder family will fare on this content at this
    operating point, from the k nearest same-family, same-scale ledger records.
    Returns {"worst", "mean", "size_ratio", "n"} — distance-weighted means of
    neighbour worst-window VMAF, mean VMAF, and achieved-size/target ratio.
    Values are None (and n == 0) when there is no comparable history; the
    caller must treat that as "no opinion", never as a good score.
    """
    fam = encoder_family(encoder)
    scale = _scale_key(vmaf_model) if vmaf_model else None
    recs = ledger_load(stats_dir, encoder_fam=fam)
    try:
        bpp = float(v_bps) / max(1.0, float(width) * float(height) * max(1.0, float(fps)))
    except Exception:
        bpp = 0.05
    q = feature_vector(features, width, height, fps, bpp)

    cands = []
    for r in recs:
        if scale and _scale_key(r.get("vmaf_model")) != scale:
            continue
        outc = r.get("outcome") or {}
        worst = outc.get("min_window")
        mean = outc.get("vmaf")
        if worst is None and mean is None:
            continue
        op = r.get("op") or {}
        size = outc.get("size")
        tgt = op.get("target_bytes")
        try:
            ratio = (float(size) / float(tgt)) if size and tgt else None
        except Exception:
            ratio = None
        # encode time normalised per source-second, so neighbours of different
        # clip lengths can be blended and re-scaled to this job's duration.
        secs = outc.get("encode_seconds")
        try:
            rdur = float(op.get("dur") or (r.get("src") or {}).get("dur") or 0.0)
            secs_per_s = (float(secs) / rdur) if secs and rdur > 0 else None
        except Exception:
            secs_per_s = None
        cands.append((_dist(q, _op_feature_vector(r)), worst, mean, ratio, secs_per_s))
    if not cands:
        return {"worst": None, "mean": None, "size_ratio": None,
                "secs_per_src_s": None, "n": 0}
    cands.sort(key=lambda t: t[0])
    top = cands[:max(1, int(k_neighbors))]

    def _wmean(idx):
        wsum = vsum = 0.0
        for row in top:
            val = row[idx]
            if val is None:
                continue
            w = 1.0 / (0.05 + row[0])
            wsum += w
            vsum += w * float(val)
        return (vsum / wsum) if wsum > 0 else None

    worst_p, mean_p, ratio_p, secs_p = _wmean(1), _wmean(2), _wmean(3), _wmean(4)
    return {"worst": (round(worst_p, 2) if worst_p is not None else None),
            "mean": (round(mean_p, 2) if mean_p is not None else None),
            "size_ratio": (round(ratio_p, 3) if ratio_p is not None else None),
            "secs_per_src_s": (round(secs_p, 3) if secs_p is not None else None),
            "n": len(top)}


def estimate_encode(stats_dir: str, features: dict, encoder: str,
                    width: int, height: int, fps: float, v_bps: float,
                    target_bytes: float, duration_s: float, *,
                    vmaf_model: str | None = None) -> dict:
    """
    Pre-flight estimate for ONE operating point, for the prediction panel /
    CLI --estimate: predicted delivered size, mean & worst-scene VMAF, and
    encode time, from the ledger — no encoding performed. Size is the target
    scaled by the learned achieved/target ratio (falls back to the target when
    unknown). Fields are None when history is too thin; n is the neighbour count
    behind the estimate so the UI can show confidence.
    """
    pq = predict_quality(stats_dir, features, encoder, width, height, fps,
                         v_bps, target_bytes, vmaf_model=vmaf_model)
    ratio = pq.get("size_ratio")
    try:
        size_bytes = int(float(target_bytes) * float(ratio)) if ratio else int(target_bytes)
    except Exception:
        size_bytes = int(target_bytes)
    spp = pq.get("secs_per_src_s")
    try:
        secs = round(float(spp) * float(duration_s), 1) if spp and duration_s else None
    except Exception:
        secs = None
    return {"encoder": encoder_family(encoder), "size_bytes": size_bytes,
            "size_ratio": ratio, "mean": pq.get("mean"), "worst": pq.get("worst"),
            "seconds": secs, "n": pq.get("n")}


def codec_prior(stats_dir: str, features: dict, width: int, height: int,
                fps: float, v_bps: float, target_bytes: float,
                candidates, *, vmaf_model: str | None = None,
                k_neighbors: int = 5) -> dict:
    """
    Per-codec-family quality/size prediction for a set of candidate encoders,
    plus a recommendation. Feeds two callers: the race-skipped path (pick the
    likely winner without spending encodes) and the pre-flight guardrail.
    Returns {"scores": {family: predict_quality(...)},
             "recommended": family|None, "n_max": int}.
    'recommended' is the family with the highest predicted worst-window VMAF
    among those with >= _MIN_PRIOR_N neighbours and no predicted cap overshoot;
    None when no family has earned an opinion yet.
    """
    fams, scores = [], {}
    for c in (candidates or []):
        fam = encoder_family(c)
        if fam in scores:
            continue
        fams.append(fam)
        scores[fam] = predict_quality(stats_dir, features, fam, width, height,
                                      fps, v_bps, target_bytes,
                                      vmaf_model=vmaf_model, k_neighbors=k_neighbors)
    eligible = []
    for fam in fams:
        s = scores[fam]
        if s["n"] >= _MIN_PRIOR_N and s["worst"] is not None:
            if s["size_ratio"] is not None and s["size_ratio"] > OVERSHOOT_WARN_RATIO:
                continue          # would blow the size cap — not a valid winner
            eligible.append((s["worst"], fam))
    recommended = max(eligible)[1] if eligible else None
    return {"scores": scores, "recommended": recommended,
            "n_max": max((scores[f]["n"] for f in fams), default=0)}


def preflight_advice(stats_dir: str, features: dict, encoder: str,
                     width: int, height: int, fps: float, v_bps: float,
                     target_bytes: float, *, candidates=None,
                     vmaf_model: str | None = None,
                     encoder_locked: bool = False) -> dict:
    """
    Advisory-only pre-flight check (never acts on its own). Predicts the chosen
    encoder's worst-window quality and size feasibility from history and, when
    the codec is free to change, whether a different candidate is predicted to
    do better. Returns {"warnings": [str], "codec_suggestion": family|None,
    "chosen": predict_quality(...), "scores": {...}, "n": int}.
    """
    fam = encoder_family(encoder)
    cands = candidates or [fam]
    prior = codec_prior(stats_dir, features, width, height, fps, v_bps,
                        target_bytes, cands, vmaf_model=vmaf_model)
    chosen = prior["scores"].get(fam) or predict_quality(
        stats_dir, features, fam, width, height, fps, v_bps, target_bytes,
        vmaf_model=vmaf_model)

    warnings: list[str] = []
    if chosen["n"] >= _MIN_PRIOR_N:
        w = chosen["worst"]
        if w is not None and w < QUALITY_COLLAPSE_WORST:
            warnings.append(
                f"quality likely to COLLAPSE at this target: predicted worst-scene "
                f"VMAF ~{w:.0f} from {chosen['n']} similar encodes. Raise the target "
                f"or trim to the essential part.")
        elif w is not None and w < QUALITY_WARN_WORST:
            warnings.append(
                f"gritty result expected: predicted worst-scene VMAF ~{w:.0f} "
                f"from {chosen['n']} similar encodes.")
        sr = chosen["size_ratio"]
        if sr is not None and sr > OVERSHOOT_WARN_RATIO:
            warnings.append(
                f"this content/encoder tends to OVERSHOOT the size cap "
                f"(~{(sr - 1) * 100:.0f}% over on {chosen['n']} similar encodes); "
                f"a smaller resolution or different codec may be needed.")

    suggestion = None
    rec = prior["recommended"]
    if not encoder_locked and rec and rec != fam:
        rs = prior["scores"].get(rec) or {}
        cw = chosen["worst"]
        rw = rs.get("worst")
        # Only suggest when the alternative is meaningfully better on the floor.
        if rw is not None and (cw is None or rw >= cw + 2.0):
            suggestion = rec
    return {"warnings": warnings, "codec_suggestion": suggestion,
            "chosen": chosen, "scores": prior["scores"], "n": chosen["n"]}


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
