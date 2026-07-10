"""Tests for the XPSNR perceptual cross-check helper (Spec 2)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import BitCrusherV9 as bc


def test_xpsnr_quality_label_bands():
    assert bc.xpsnr_quality_label(45) == "visually lossless"
    assert bc.xpsnr_quality_label(38) == "excellent"
    assert bc.xpsnr_quality_label(33) == "good"
    assert bc.xpsnr_quality_label(28) == "fair"
    assert bc.xpsnr_quality_label(20) == "poor"
    assert bc.xpsnr_quality_label(None) == "unknown"


def test_xpsnr_absent_binary_is_graceful(monkeypatch):
    # When the xpsnr filter isn't available, compute_xpsnr must no-op (None),
    # never raise — the cross-check is optional.
    monkeypatch.setattr(bc, "_ffmpeg_has_filter", lambda name: False)
    assert bc.compute_xpsnr("ref.mp4", "dist.mp4", duration_s=5.0) is None
