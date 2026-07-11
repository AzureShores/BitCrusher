"""Tests for the outcome-ledger codec-winner prior + pre-flight guardrail
(outcome_ledger.predict_quality / codec_prior / preflight_advice)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import outcome_ledger as ol


# A grainy-old-film-ish feature point reused across the synthetic records.
_FEATS = {"entropy_p95": 6.5, "spatial_complexity": 5.0, "graininess": 0.7,
          "text_edge_density": 0.1, "blockiness": 4.0, "edge_p95": 120.0,
          "scene_rate": 0.2, "motion_mad": 0.3}


def _rec(encoder, worst, mean, size, target, *, vmaf_model="vmaf_v1.0.16"):
    """Minimal ledger record the prior can read."""
    return {
        "schema": ol.SCHEMA_VERSION,
        "vmaf_model": vmaf_model,
        "input": f"clip_{encoder}.mp4",
        "features": dict(_FEATS),
        "op": {"encoder_eff": encoder, "width": 640, "height": 480,
               "fps": 24.0, "v_bps": 180_000, "target_bytes": target},
        "attempts": [[180_000, size]],
        "outcome": {"size": size, "vmaf": mean, "min_window": worst},
    }


def _seed(stats_dir, records):
    for r in records:
        ol.ledger_append(stats_dir, r)


def test_codec_prior_ranks_by_worst_window(tmp_path):
    sd = str(tmp_path)
    tgt = 3 * 1024 * 1024
    # av1 holds the floor; x265 collapses; x264 middling. >=3 each so all speak.
    recs = []
    for _ in range(3):
        recs += [_rec("av1", worst=88.0, mean=94.0, size=int(tgt * 0.98), target=tgt),
                 _rec("x265", worst=45.0, mean=74.0, size=int(tgt * 1.30), target=tgt),
                 _rec("x264", worst=71.0, mean=90.0, size=int(tgt * 0.99), target=tgt)]
    _seed(sd, recs)

    prior = ol.codec_prior(sd, _FEATS, 640, 480, 24.0, 180_000, tgt,
                           ["x264", "x265", "av1"], vmaf_model="vmaf_v1.0.16")
    assert prior["scores"]["av1"]["n"] >= 3
    assert prior["scores"]["av1"]["worst"] > prior["scores"]["x264"]["worst"]
    # x265 overshoots the cap (size_ratio ~1.30) -> excluded from winners.
    assert prior["recommended"] == "av1"


def test_preflight_warns_on_quality_collapse_and_overshoot(tmp_path):
    sd = str(tmp_path)
    tgt = 3 * 1024 * 1024
    # Only x265 history, and it collapses + overshoots -> both warnings fire.
    _seed(sd, [_rec("x265", worst=44.0, mean=72.0, size=int(tgt * 1.35), target=tgt)
               for _ in range(4)])
    adv = ol.preflight_advice(sd, _FEATS, "x265", 640, 480, 24.0, 180_000, tgt,
                              candidates=["x265"], vmaf_model="vmaf_v1.0.16")
    joined = " ".join(adv["warnings"]).lower()
    assert "collapse" in joined
    assert "overshoot" in joined


def test_preflight_suggests_better_codec_when_unlocked(tmp_path):
    sd = str(tmp_path)
    tgt = 3 * 1024 * 1024
    recs = []
    for _ in range(3):
        recs += [_rec("x265", worst=44.0, mean=72.0, size=int(tgt * 1.30), target=tgt),
                 _rec("av1", worst=88.0, mean=94.0, size=int(tgt * 0.98), target=tgt)]
    _seed(sd, recs)
    adv = ol.preflight_advice(sd, _FEATS, "x265", 640, 480, 24.0, 180_000, tgt,
                              candidates=["x265", "av1"], vmaf_model="vmaf_v1.0.16",
                              encoder_locked=False)
    assert adv["codec_suggestion"] == "av1"
    # Locked encoder: honor the explicit choice, never suggest a swap.
    adv_locked = ol.preflight_advice(sd, _FEATS, "x265", 640, 480, 24.0, 180_000, tgt,
                                     candidates=["x265", "av1"], vmaf_model="vmaf_v1.0.16",
                                     encoder_locked=True)
    assert adv_locked["codec_suggestion"] is None


def test_prior_abstains_without_enough_history(tmp_path):
    sd = str(tmp_path)
    tgt = 3 * 1024 * 1024
    _seed(sd, [_rec("x265", worst=44.0, mean=72.0, size=tgt, target=tgt)])  # n=1
    prior = ol.codec_prior(sd, _FEATS, 640, 480, 24.0, 180_000, tgt,
                           ["x264", "x265", "av1"], vmaf_model="vmaf_v1.0.16")
    assert prior["recommended"] is None
    adv = ol.preflight_advice(sd, _FEATS, "x265", 640, 480, 24.0, 180_000, tgt,
                              candidates=["x265"], vmaf_model="vmaf_v1.0.16")
    assert adv["warnings"] == []          # too little data to warn


def test_estimate_encode_scales_size_and_time(tmp_path):
    sd = str(tmp_path)
    tgt = 3 * 1024 * 1024
    # Neighbours: 24s clips, av1 landed 0.98*target and took 35s (=> ~1.46 s/src-s).
    recs = []
    for _ in range(4):
        r = _rec("av1", worst=93.0, mean=95.0, size=int(tgt * 0.98), target=tgt)
        r["op"]["dur"] = 24.0
        r["src"] = {"dur": 24.0}
        r["outcome"]["encode_seconds"] = 35.0
        recs.append(r)
    _seed(sd, recs)
    est = ol.estimate_encode(sd, _FEATS, "av1", 640, 480, 24.0, 180_000, tgt,
                             duration_s=48.0, vmaf_model="vmaf_v1.0.16")
    assert est["n"] == 4
    assert abs(est["size_bytes"] - tgt * 0.98) < tgt * 0.01     # size from learned ratio
    assert est["worst"] == 93.0 and est["mean"] == 95.0
    # 48s clip is ~2x the 24s neighbours -> ~70s predicted.
    assert 60.0 <= est["seconds"] <= 80.0


def test_estimate_encode_abstains_without_history(tmp_path):
    est = ol.estimate_encode(str(tmp_path), _FEATS, "av1", 640, 480, 24.0,
                             180_000, 3 * 1024 * 1024, duration_s=24.0,
                             vmaf_model="vmaf_v1.0.16")
    assert est["n"] == 0 and est["worst"] is None and est["seconds"] is None


def test_predict_quality_ignores_black_frame_worst_window(tmp_path):
    """A min_window==0.0 (black leader/title-card frame tanking one VMAF
    window) must not drag down the worst-window prediction, but the record's
    mean VMAF - unaffected by a couple black frames - should still count."""
    sd = str(tmp_path)
    tgt = 3 * 1024 * 1024
    recs = [_rec("x265", worst=44.0, mean=72.0, size=int(tgt * 0.99), target=tgt)
            for _ in range(3)]
    recs.append(_rec("x265", worst=0.0, mean=85.0, size=int(tgt * 0.99), target=tgt))
    _seed(sd, recs)

    pq = ol.predict_quality(sd, _FEATS, "x265", 640, 480, 24.0, 180_000, tgt,
                            vmaf_model="vmaf_v1.0.16")
    assert pq["n"] == 4                       # the collapsed record still counts
    assert pq["worst"] == 44.0                # but doesn't pull the worst-window mean down
    assert pq["mean"] > 72.0                  # its (unaffected) mean VMAF still contributes


def test_scale_isolation_v06_not_used_for_v1(tmp_path):
    sd = str(tmp_path)
    tgt = 3 * 1024 * 1024
    # History is all v0.6.1 scale; a v1 query must NOT borrow it.
    _seed(sd, [_rec("x265", worst=44.0, mean=72.0, size=tgt, target=tgt,
                    vmaf_model="vmaf_v0.6.1") for _ in range(5)])
    pq = ol.predict_quality(sd, _FEATS, "x265", 640, 480, 24.0, 180_000, tgt,
                            vmaf_model="vmaf_v1.0.16")
    assert pq["n"] == 0 and pq["worst"] is None
