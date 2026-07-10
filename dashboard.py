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
        "band": quality_band(worst),
        "spread": outc.get("spread"),
        "encoder": winner,
        "scoreboard": board,
        "size_bytes": size,
        "size_ratio": size_ratio,
        "under_target": (size_ratio is not None and size_ratio <= 1.0),
        "encode_seconds": outc.get("encode_seconds"),
    }
