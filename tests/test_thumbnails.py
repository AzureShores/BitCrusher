"""Contact-sheet grid math tests (encode/thumbnails.py)."""
from encode.thumbnails import build_sheet_filter, grid_timestamps


def test_grid_timestamps_even_and_centered():
    ts = grid_timestamps(16.0, 4, 4)
    assert len(ts) == 16
    assert ts[0] == 0.5 and ts[-1] == 15.5       # slot centers, not edges
    steps = [round(b - a, 3) for a, b in zip(ts, ts[1:])]
    assert all(s == 1.0 for s in steps)


def test_grid_timestamps_short_source():
    ts = grid_timestamps(1.0, 4, 4)
    assert len(ts) == 16
    assert 0.0 < ts[0] < ts[-1] < 1.0


def test_grid_timestamps_invalid():
    assert grid_timestamps(0) == []
    assert grid_timestamps(-3) == []
    assert grid_timestamps("junk") == []


def test_sheet_filter_shape():
    vf = build_sheet_filter(32.0, 4, 4, sheet_w=1280)
    assert vf is not None
    assert "tile=4x4" in vf
    assert "scale=320:-1" in vf                   # 1280/4 per tile
    assert vf.startswith("fps=0.5")               # 16 frames / 32s


def test_sheet_filter_min_tile_width():
    vf = build_sheet_filter(10.0, 8, 2, sheet_w=256)
    assert "scale=64:-1" in vf                    # floor at 64px


def test_sheet_filter_invalid():
    assert build_sheet_filter(0) is None
    assert build_sheet_filter(10, 0, 4) is None
    assert build_sheet_filter("junk") is None
