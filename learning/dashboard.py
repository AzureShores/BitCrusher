"""
dashboard.py — pure, framework-agnostic view-model for the encode result
dashboard (Feature 3).

Turns one outcome-ledger record (or a live stats dict) into a small data model:
a normalized VMAF-over-time sparkline with the worst window flagged, and a codec
race scoreboard explaining why the winning codec won. No Tkinter, no I/O — so it
is unit-testable and can drive any renderer (the GUI canvas, an SVG, a CLI bar).
"""
from __future__ import annotations


def _f(x):
    try:
        return float(x)
    except Exception:
        return None


def sparkline_points(series, width: float, height: float, *,
                     y_min: float | None = None, y_max: float | None = None,
                     pad: float = 2.0) -> list[tuple[float, float]]:
    """Map a VMAF series to (x, y) pixel points in a width x height box. y is
    flipped so higher VMAF is higher on screen. The y-range defaults to the
    data's own span (clamped to a sane VMAF window) so dips are visible."""
    vals = [v for v in (_f(s) for s in (series or [])) if v is not None]
    if not vals:
        return []
    lo = y_min if y_min is not None else max(0.0, min(vals) - 3.0)
    hi = y_max if y_max is not None else min(100.0, max(vals) + 3.0)
    if hi - lo < 1.0:
        lo, hi = lo - 1.0, hi + 1.0
    n = len(vals)
    usable_w = max(1.0, width - 2 * pad)
    usable_h = max(1.0, height - 2 * pad)
    pts = []
    for i, v in enumerate(vals):
        x = pad + (usable_w * (i / max(1, n - 1)) if n > 1 else usable_w / 2.0)
        frac = (v - lo) / (hi - lo)
        frac = min(1.0, max(0.0, frac))
        y = pad + usable_h * (1.0 - frac)
        pts.append((round(x, 2), round(y, 2)))
    return pts


def worst_window_marker(series, min_window_at=None, series_span_s=None) -> dict | None:
    """Locate the worst point of the series for the dashboard's flag. Prefers the
    measured min_window_at (source seconds) mapped through the series' time span
    (series_span_s), which is downsample-independent; otherwise falls back to the
    lowest sampled value. Returns {"index", "frac", "value"} or None."""
    vals = [v for v in (_f(s) for s in (series or [])) if v is not None]
    if not vals:
        return None
    n = len(vals)
    idx = None
    at = _f(min_window_at)
    span = _f(series_span_s)
    if at is not None and span and span > 0 and n > 1:
        idx = int(round((at / span) * (n - 1)))
    if idx is None or not (0 <= idx < n):
        idx = min(range(n), key=lambda i: vals[i])
    idx = min(n - 1, max(0, idx))
    return {"index": idx, "frac": round(idx / max(1, n - 1), 4) if n > 1 else 0.0,
            "value": round(vals[idx], 2)}


HEATMAP_GOOD = 95.0
HEATMAP_OK = 85.0


def _heat_level(v: float) -> str:
    if v >= HEATMAP_GOOD:
        return "good"
    if v >= HEATMAP_OK:
        return "ok"
    return "poor"


def heatmap_bands(series, series_span_s=None) -> list[dict]:
    """Time-quality bands for the scene heatmap strip under the sparkline.

    Consecutive samples on the same semantic level merge into one band:
    {"x0", "x1" (0..1 fractions), "level" ("good"|"ok"|"poor"),
     "t0", "t1" (seconds, None when the span is unknown)}. The GUI maps
    levels to theme colors - no color decisions here.
    """
    vals = [v for v in (_f(s) for s in (series or [])) if v is not None]
    n = len(vals)
    if n == 0:
        return []
    span = _f(series_span_s)
    span = span if span and span > 0 else None

    def _mk(i0, i1, level):
        # Sample i covers slot [i/n, (i+1)/n) - slot edges, not point centers.
        x0, x1 = i0 / n, (i1 + 1) / n
        return {"x0": round(x0, 4), "x1": round(x1, 4), "level": level,
                "t0": round(x0 * span, 2) if span else None,
                "t1": round(x1 * span, 2) if span else None}

    bands = []
    run_start, run_level = 0, _heat_level(vals[0])
    for i in range(1, n):
        lvl = _heat_level(vals[i])
        if lvl != run_level:
            bands.append(_mk(run_start, i - 1, run_level))
            run_start, run_level = i, lvl
    bands.append(_mk(run_start, n - 1, run_level))
    return bands


def scoreboard(race: dict | None, winner: str | None = None) -> list[dict]:
    """Codec race scoreboard rows, sorted best-first. `race` is the ledger's
    race field: {"scores": {family: vmaf}, ...} (or a bare {family: vmaf} map).
    Each row: {"encoder", "score", "delta"(vs best), "is_winner", "bar"(0..1)}."""
    if not race:
        return []
    scores = race.get("scores") if isinstance(race, dict) and "scores" in race else race
    if not isinstance(scores, dict) or not scores:
        return []
    clean = {str(k): _f(v) for k, v in scores.items() if _f(v) is not None}
    if not clean:
        return []
    best = max(clean.values())
    worst = min(clean.values())
    span = max(1e-6, best - worst)
    rows = []
    for enc, sc in sorted(clean.items(), key=lambda kv: kv[1], reverse=True):
        rows.append({
            "encoder": enc,
            "score": round(sc, 2),
            "delta": round(sc - best, 2),                 # 0 for the winner, negative below
            "is_winner": (enc == winner) if winner else (sc >= best - 1e-9),
            "bar": round((sc - worst) / span, 3),         # 0..1 for a relative bar
        })
    return rows


def quality_band(worst) -> str:
    """Human label for a worst-scene VMAF, matching the guardrail's floors."""
    w = _f(worst)
    if w is None:
        return "unknown"
    if w < 60.0:
        return "collapsed"
    if w < 75.0:
        return "gritty"
    if w < 90.0:
        return "good"
    return "excellent"


def build_dashboard_model(record: dict) -> dict:
    """
    Assemble the full view-model from a ledger record (or a live stats dict with
    the same shape: {"outcome": {...}, "race": {...}, "op": {...}}). Returns
    everything a renderer needs; callers pick a width/height and call
    sparkline_points() on model["series"].
    """
    rec = record or {}
    outc = rec.get("outcome") or rec           # tolerate a flat stats dict
    op = rec.get("op") or {}
    series = outc.get("series") or []
    mean = outc.get("vmaf")
    worst = outc.get("min_window")
    marker = worst_window_marker(series, outc.get("min_window_at"),
                                 outc.get("series_span_s"))
    winner = op.get("encoder_eff") or outc.get("encoder")
    board = scoreboard(rec.get("race"), winner=winner)
    size = outc.get("size")
    target = op.get("target_bytes")
    try:
        size_ratio = round(float(size) / float(target), 3) if size and target else None
    except Exception:
        size_ratio = None
    return {
        "series": [v for v in series if v is not None],
        "mean": mean,
        "worst": worst,
        "worst_marker": marker,
        "heatmap": heatmap_bands(series, outc.get("series_span_s")),
        "band": quality_band(worst),
        "spread": outc.get("spread"),
        "encoder": winner,
        "scoreboard": board,
        "size_bytes": size,
        "size_ratio": size_ratio,
        "under_target": (size_ratio is not None and size_ratio <= 1.0),
        "encode_seconds": outc.get("encode_seconds"),
    }


def _trend_stats(vals: list[float]) -> dict:
    """First-half-vs-second-half mean comparison: cheap, order-preserving
    signal for whether a predictor's error is trending down over time,
    without needing a real time-series regression for what's still a small
    sample in most installs."""
    if not vals:
        return {"n": 0, "series": [], "first_half_mean": None,
               "second_half_mean": None, "improving": None}
    mid = len(vals) // 2
    first = vals[:mid] or vals
    second = vals[mid:] or vals
    fm = sum(first) / len(first)
    sm = sum(second) / len(second)
    return {"n": len(vals), "series": vals,
           "first_half_mean": round(fm, 4), "second_half_mean": round(sm, 4),
           "improving": bool(sm < fm)}


def build_trend_model(records: list[dict]) -> dict:
    """Prediction-error trend across MULTIPLE ledger records, in the ledger's
    own chronological (append) order -- unlike build_dashboard_model, which
    renders a single encode. One error point per record per predictor: the
    ledger's own kNN size-deviation predictor (dev_pred), probe_predictor's
    rate fit (probe_dev_pred/actual), and ai_advisor's Ridge quality model
    (advisor_q_pred vs the record's own measured VMAF) -- the same three
    predictors outcome_ledger.shadow_report() calibrates in aggregate, here
    broken out as a time series so "is it actually improving" is answerable,
    not just "what's the current error". Pure/no I/O: callers load records via
    outcome_ledger.ledger_load() first."""
    dev_err: list[float] = []
    probe_err: list[float] = []
    advisor_err: list[float] = []
    retries: list[float] = []
    for r in records or []:
        rec = r or {}
        sh = rec.get("shadow") or {}
        attempts = rec.get("attempts") or []
        op = rec.get("op") or {}
        src = rec.get("src") or {}
        if attempts:
            v_bps, got = attempts[0][0], attempts[0][1]
            dur = src.get("dur") or op.get("dur") or 0.0
            audio_bps = op.get("audio_bps") or 0.0
            try:
                expected = (float(v_bps) + float(audio_bps)) * max(0.1, float(dur)) / 8.0
                dev = float(got) / expected if expected > 0 and got else None
            except Exception:
                dev = None
            dp = sh.get("dev_pred")
            if dev and dp is not None:
                dev_err.append(round(abs(float(dp) - dev) / dev, 4))
        pp, pa = sh.get("probe_dev_pred"), sh.get("probe_dev_actual")
        if pp is not None and pa:
            probe_err.append(round(abs(float(pp) - float(pa)) / float(pa), 4))
        qp = sh.get("advisor_q_pred")
        qa = (rec.get("outcome") or {}).get("vmaf")
        if qp is not None and qa is not None:
            advisor_err.append(round(abs(float(qp) - float(qa)), 2))
        rpe = (rec.get("outcome") or {}).get("retries_per_encode")
        if rpe is not None:
            try:
                retries.append(float(rpe))
            except Exception:
                pass
    return {
        "ledger_dev": _trend_stats(dev_err),
        "probe": _trend_stats(probe_err),
        "advisor": _trend_stats(advisor_err),
        "retries_per_encode": _trend_stats(retries),
    }
