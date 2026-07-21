"""Tests for the result-dashboard view-model (dashboard.py, Feature 3)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import learning.dashboard as db


def test_sparkline_points_map_into_box_and_flip_y():
    # rising series -> y should DECREASE (higher VMAF = higher on screen).
    pts = db.sparkline_points([50, 60, 70, 80, 90], 100, 40)
    assert len(pts) == 5
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert xs == sorted(xs)                     # x monotonic increasing
    assert ys[0] > ys[-1]                       # rising quality -> y goes up (down in px)
    assert all(0 <= x <= 100 for x in xs)
    assert all(0 <= y <= 40 for y in ys)


def test_sparkline_empty_series():
    assert db.sparkline_points([], 100, 40) == []
    assert db.sparkline_points(None, 100, 40) == []


def test_worst_marker_prefers_measured_time():
    series = [95, 96, 40, 95, 96]               # dip at index 2
    # 5 points spanning 4.0s -> 1 pt/s; min_window_at 2.0s -> index 2
    m = db.worst_window_marker(series, min_window_at=2.0, series_span_s=4.0)
    assert m["index"] == 2 and m["value"] == 40


def test_worst_marker_falls_back_to_min():
    series = [95, 96, 40, 95, 96]
    m = db.worst_window_marker(series)          # no measured time
    assert m["index"] == 2


def test_scoreboard_sorts_and_flags_winner():
    race = {"scores": {"x264": 89.5, "x265": 91.3, "av1": 94.3}}
    rows = db.scoreboard(race, winner="av1")
    assert [r["encoder"] for r in rows] == ["av1", "x265", "x264"]   # best first
    assert rows[0]["is_winner"] and rows[0]["delta"] == 0.0
    assert rows[-1]["delta"] < 0                                     # x264 below best
    assert rows[0]["bar"] == 1.0 and rows[-1]["bar"] == 0.0


def test_scoreboard_empty():
    assert db.scoreboard(None) == []
    assert db.scoreboard({"scores": {}}) == []


def test_quality_band_thresholds():
    assert db.quality_band(44) == "collapsed"
    assert db.quality_band(70) == "gritty"
    assert db.quality_band(85) == "good"
    assert db.quality_band(95) == "excellent"
    assert db.quality_band(None) == "unknown"


def test_build_model_from_record():
    rec = {
        "op": {"encoder_eff": "av1", "target_bytes": 3 * 1024 * 1024},
        "race": {"scores": {"x264": 89.5, "x265": 91.3, "av1": 94.3}},
        "outcome": {"vmaf": 94.3, "min_window": 83.3, "min_window_at": 2.0,
                    "series_span_s": 4.0, "series": [95, 94, 83, 96, 95],
                    "size": int(2.93 * 1024 * 1024), "encode_seconds": 47.0},
    }
    m = db.build_dashboard_model(rec)
    assert m["encoder"] == "av1"
    assert m["band"] == "good"
    assert m["worst_marker"]["value"] == 83
    assert m["scoreboard"][0]["encoder"] == "av1"
    assert m["under_target"] is True
    assert 0.9 < m["size_ratio"] < 1.0


def test_heatmap_bands_merge_runs():
    bands = db.heatmap_bands([96, 97, 90, 88, 70, 60], series_span_s=60.0)
    assert [b["level"] for b in bands] == ["good", "ok", "poor"]
    assert bands[0]["x0"] == 0.0 and bands[-1]["x1"] == 1.0
    # Slot edges are contiguous.
    for a, b in zip(bands, bands[1:]):
        assert a["x1"] == b["x0"]
    # Timestamps map through the span.
    assert bands[0]["t0"] == 0.0 and bands[-1]["t1"] == 60.0


def test_heatmap_bands_single_point_and_empty():
    assert db.heatmap_bands([]) == []
    assert db.heatmap_bands(None) == []
    one = db.heatmap_bands([80.0])
    assert len(one) == 1 and one[0]["level"] == "poor"
    assert one[0]["x0"] == 0.0 and one[0]["x1"] == 1.0
    assert one[0]["t0"] is None            # no span given


def test_heatmap_thresholds():
    bands = db.heatmap_bands([95.0, 94.99, 85.0, 84.99])
    assert [b["level"] for b in bands] == ["good", "ok", "poor"]


def test_model_includes_heatmap():
    rec = {"outcome": {"series": [96, 80], "series_span_s": 10.0}}
    m = db.build_dashboard_model(rec)
    assert [b["level"] for b in m["heatmap"]] == ["good", "poor"]
