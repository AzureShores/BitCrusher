"""Pre-encode ETA estimator tests (learning/eta.py).

Throughput = median op.dur / outcome.encode_seconds over successful
ledger rows, per encoder family, falling back to conservative constants
when history is thin.
"""
from learning.eta import (_FALLBACK_SPEED, _DEFAULT_SPEED,
                          estimate_encode_seconds, throughput_from_ledger)


def _rec(dur, wall, enc="libx264", success=True):
    return {"op": {"dur": dur, "encoder_eff": enc},
            "outcome": {"success": success, "encode_seconds": wall}}


def test_median_throughput_per_family():
    rows = [_rec(10, 5), _rec(10, 10), _rec(10, 20)]   # speeds 2.0, 1.0, 0.5
    assert throughput_from_ledger(rows, "x264") == 1.0


def test_family_falls_back_to_overall():
    rows = [_rec(10, 10, enc="libsvtav1")] * 3         # av1 speed 1.0
    # No x264 history -> overall median used.
    assert throughput_from_ledger(rows, "x264") == 1.0


def test_min_samples_gate():
    rows = [_rec(10, 5)]                                # only 1 sample
    assert throughput_from_ledger(rows, "x264") is None


def test_failures_and_zero_durations_ignored():
    rows = [_rec(10, 5, success=False), _rec(0, 5), _rec(10, 0)]
    assert throughput_from_ledger(rows) is None


def test_estimate_uses_history():
    rows = [_rec(10, 20)] * 5                           # speed 0.5
    assert estimate_encode_seconds(60, rows, "x264") == 120.0


def test_estimate_fallback_constants():
    assert estimate_encode_seconds(60, [], "libx265") == round(
        60 / _FALLBACK_SPEED["x265"], 1)
    assert estimate_encode_seconds(60, [], "weird") == round(
        60 / _DEFAULT_SPEED, 1)


def test_invalid_duration_none():
    assert estimate_encode_seconds(0, []) is None
    assert estimate_encode_seconds(-5, []) is None
    assert estimate_encode_seconds("junk", []) is None
