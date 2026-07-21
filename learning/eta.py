"""Pre-encode wall-time estimates from the outcome ledger.

The ledger already records op.dur (source seconds) and
outcome.encode_seconds (wall seconds) on every successful encode, so a
per-encoder-family throughput (video-seconds per wall-second) falls out
of history for free. Deliberately dumb: median, no resolution/bitrate
matrix - good enough to order a queue shortest-first.

Framework-free and unit-tested; the GUI only calls
estimate_encode_seconds().
"""
from __future__ import annotations

from statistics import median

try:
    from learning.outcome_ledger import encoder_family
except Exception:  # pragma: no cover - flat-layout fallback
    from outcome_ledger import encoder_family

# Conservative video-seconds-per-wall-second fallbacks when history is
# empty (SW two-pass on desktop CPUs; hardware encoders are far faster
# but then history catches up quickly anyway).
_FALLBACK_SPEED = {
    "x264": 1.5,
    "x265": 0.6,
    "av1": 0.4,
    "vp9": 0.5,
    "nvenc": 6.0,
    "qsv": 4.0,
}
_DEFAULT_SPEED = 0.8


def _row_speed(rec: dict) -> tuple[str, float] | None:
    """(family, video-sec/wall-sec) from one ledger record, or None."""
    try:
        if not (rec.get("outcome") or {}).get("success"):
            return None
        dur = float((rec.get("op") or {}).get("dur") or 0.0)
        wall = float((rec.get("outcome") or {}).get("encode_seconds") or 0.0)
        if dur <= 0 or wall <= 0:
            return None
        fam = encoder_family(str((rec.get("op") or {}).get("encoder_eff") or ""))
        return fam, dur / wall
    except Exception:
        return None


def throughput_from_ledger(ledger_rows, family: str | None = None,
                           min_samples: int = 3) -> float | None:
    """Median throughput for a family (or overall), None below min_samples."""
    by_fam: dict[str, list[float]] = {}
    for rec in ledger_rows or []:
        rs = _row_speed(rec)
        if rs:
            by_fam.setdefault(rs[0], []).append(rs[1])
    if family:
        vals = by_fam.get(str(family), [])
        if len(vals) >= min_samples:
            return float(median(vals))
    all_vals = [v for vals in by_fam.values() for v in vals]
    if len(all_vals) >= min_samples:
        return float(median(all_vals))
    return None


def estimate_encode_seconds(duration_s: float, ledger_rows=None,
                            encoder: str | None = None) -> float | None:
    """Estimated wall seconds to encode duration_s of video. None if the
    duration is unknown/invalid."""
    try:
        dur = float(duration_s)
    except Exception:
        return None
    if dur <= 0:
        return None
    fam = encoder_family(str(encoder or ""))
    speed = throughput_from_ledger(ledger_rows, fam)
    if not speed or speed <= 0:
        speed = _FALLBACK_SPEED.get(fam, _DEFAULT_SPEED)
    return round(dur / speed, 1)
