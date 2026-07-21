"""Animated GIF/WebP export ladder tests (encode/gif_export.py)."""
from encode.gif_export import plan_animated_ladder


def test_gif_ladder_descends_quality():
    ladder = plan_animated_ladder(10.0, 1920, 1080, "gif", target_mb=10)
    assert ladder, "ladder must not be empty"
    assert all(set(a) == {"fps", "width", "colors"} for a in ladder)
    # First attempt is the best one; last is the most aggressive.
    assert ladder[0]["width"] >= ladder[-1]["width"]
    assert ladder[0]["colors"] >= ladder[-1]["colors"]
    # Widths never upscale and step down over the ladder.
    widths = [a["width"] for a in ladder]
    assert max(widths) <= 480
    assert sorted(set(widths), reverse=True) == sorted(
        {w for w in widths}, reverse=True)


def test_webp_ladder_uses_quality():
    ladder = plan_animated_ladder(10.0, 1280, 720, "webp", target_mb=8)
    assert all(set(a) == {"fps", "width", "quality"} for a in ladder)
    assert ladder[0]["quality"] >= ladder[-1]["quality"]


def test_small_source_never_upscaled():
    ladder = plan_animated_ladder(10.0, 300, 200, "gif", target_mb=5)
    assert all(a["width"] <= 300 for a in ladder)


def test_no_target_single_best_attempt():
    ladder = plan_animated_ladder(10.0, 1920, 1080, "gif", target_mb=None)
    assert len(ladder) == 1
    assert ladder[0]["colors"] == 256


def test_subsecond_source_short_circuits():
    ladder = plan_animated_ladder(0.5, 1920, 1080, "webp", target_mb=10)
    assert len(ladder) == 1


def test_unknown_format_empty():
    assert plan_animated_ladder(10.0, 640, 480, "avif", 5) == []
    assert plan_animated_ladder(10.0, 640, 480, "", 5) == []
