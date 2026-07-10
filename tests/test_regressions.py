import os
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_advisor as adv
import ml_heuristics as mh
import probe_predictor as pp
import smart_rate as sr
from size_controller import SizeController


def test_probe_predictor_uses_codec_probe_without_legacy_loop(monkeypatch):
    def fake_probe_rate_quality(**_kwargs):
        return {
            "points": [(18, 12000.0), (24, 8000.0), (30, 5000.0)],
            "confidence": 0.9,
            "fit": {"a": -0.1, "b": 10.0},
            "bounds": {"low": 0.9, "high": 1.1},
            "diagnostics": {"ok": True},
        }

    monkeypatch.setattr(pp, "probe_rate_quality", fake_probe_rate_quality)

    def fake_run(_cmd):
        raise AssertionError("legacy _encode_probe path should not be called when codec_probe points exist")

    out = pp.predict_crf_and_bitrate(
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        path="dummy.mp4",
        target_bytes=5_000_000,
        duration=60.0,
        width=1920,
        height=1080,
        fps=30.0,
        audio_bps=128_000,
        run=fake_run,
    )
    assert out["curve_points"]
    assert out["confidence"] > 0.0


def test_smart_rate_auto_pick_has_no_speed_nameerror(monkeypatch):
    work = ROOT / "tmp" / "test_regressions"
    work.mkdir(parents=True, exist_ok=True)
    src = work / "in_autopick.mp4"
    src.write_bytes(b"x")

    monkeypatch.setattr(sr, "_extract_probe_segments", lambda *_a, **_k: ["c1", "c2", "c3"])
    monkeypatch.setattr(sr, "_probe_grid_for", lambda *_a, **_k: [20, 24, 28])

    def fake_metrics(_clips, enc, crf):
        base = {"x264": 0.94, "x265": 0.96, "av1": 0.95}.get(enc, 0.90)
        return 900.0 - crf * 5.0, base - crf * 0.001, 1.0

    monkeypatch.setattr(sr, "_probe_encode_metrics", fake_metrics)

    picked = sr._auto_pick_encoder_by_probe(
        src=str(src),
        target_kbps=700.0,
        candidates=["x264", "x265", "av1"],
    )
    assert picked in {"x264", "x265", "av1"}


def test_ai_advisor_exports_nonzero_difficulty(monkeypatch):
    work = ROOT / "tmp" / "test_regressions"
    work.mkdir(parents=True, exist_ok=True)
    src = work / "in_advisor.mp4"
    src.write_bytes(b"x")
    os.environ["BC_CURRENT_INPUT"] = str(src)

    import smart_rate

    monkeypatch.setattr(smart_rate, "choose_bitrates", lambda *a, **k: (600_000, 128_000, 1.02))
    monkeypatch.setattr(smart_rate, "estimate_mux_overhead", lambda **k: 0)

    monkeypatch.setattr(adv, "extract_media_features", lambda _p: {
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "spatial_complexity": 7.8,
        "entropy_p95": 7.6,
        "edge_p95": 6.2,
        "sparsity_mean": 0.08,
        "temporal_ssim_std": 0.05,
        "motion_mad": 0.04,
        "scene_rate": 0.15,
        "banding_risk": 0.20,
        "text_edge_density": 0.12,
        "graininess": 0.25,
        "blockiness": 2.0,
    })
    monkeypatch.setattr(adv._MODEL, "predict", lambda _x: 60.0)
    monkeypatch.setattr(adv, "analyze_scenes", lambda *_a, **_k: {"zones_str": "", "gop": 60, "aq_strength": 1.0})

    adv.choose_bitrates_advised(
        duration_s=60.0,
        target_bytes=8_000_000,
        encoder="x264",
        container="mp4",
    )

    assert float(os.environ.get("BC_CONTENT_DIFFICULTY", "0")) > 0.0


def test_build_scene_params_returns_joined_params(monkeypatch):
    monkeypatch.setattr(mh, "analyze_scenes", lambda *_a, **_k: {
        "gop": 60,
        "aq_strength": 1.1,
        "zones_str": "zones=0,120,q=-1",
    })

    params, qpfile = mh.build_scene_params("dummy.mp4", encoder="x264", fps_hint=30.0)
    assert "keyint=60" in params
    assert "aq-strength=1.10" in params
    assert "zones=0,120,q=-1" in params
    assert qpfile is None


def test_size_controller_retry_progresses_directionally():
    c = SizeController(target_bytes=1_000_000, duration_s=10.0, audio_bps=96_000, max_iter=4)
    c.set_initial(seed_v_bps=300_000, seed_bytes=700_000)  # undershoot => should push upward
    assert c.should_retry(700_000)
    v_next, _a_next = c.next(700_000)
    assert v_next > 300_000


def test_audio_encode_preserves_tags_and_cover(monkeypatch, tmp_path):
    """The old audio path used -vn (drops album art) + the privacy default
    -map_metadata -1 (drops title/artist/album), so every compressed track came
    out naked. Tags must survive on all containers; cover art survives where the
    container can hold a picture stream (mp3/m4a) but not opus/ogg; strict
    privacy still strips everything."""
    import BitCrusherV9 as bc

    captured = {}

    def fake_run(cmd, **_kw):
        captured["cmd"] = list(cmd)
        with open(cmd[-1], "wb") as f:  # last arg is the output path
            f.write(b"x")

        class _R:
            returncode = 0
            stderr = ""
        return _R()

    monkeypatch.setattr(bc, "_sp_run", fake_run)
    src = tmp_path / "in.flac"
    src.write_bytes(b"x")

    def _mval(cmd, flag):
        return cmd[cmd.index(flag) + 1]

    # mp3 keeps tags AND cover
    bc._encode_audio_once(str(src), str(tmp_path / "o.mp3"), encoder="libmp3lame",
                          bitrate_bps=192000, sr=48000, channels=2, vbr_mode="off",
                          loudnorm=False, highpass_hz=None, lowpass_hz=None)
    c = captured["cmd"]
    assert _mval(c, "-map_metadata") == "0"
    assert "-c:v" in c and "copy" in c and "0:v?" in c
    assert "-vn" not in c

    # opus keeps tags, but no cover stream (ogg cannot carry a copied picture)
    bc._encode_audio_once(str(src), str(tmp_path / "o.opus"), encoder="libopus",
                          bitrate_bps=192000, sr=48000, channels=2, vbr_mode="on",
                          loudnorm=False, highpass_hz=None, lowpass_hz=None)
    c = captured["cmd"]
    assert _mval(c, "-map_metadata") == "0"
    assert "0:v?" not in c

    # strict privacy strips everything
    bc._encode_audio_once(str(src), str(tmp_path / "p.mp3"), encoder="libmp3lame",
                          bitrate_bps=192000, sr=48000, channels=2, vbr_mode="off",
                          loudnorm=False, highpass_hz=None, lowpass_hz=None,
                          privacy_preset="strict")
    c = captured["cmd"]
    assert _mval(c, "-map_metadata") == "-1"


def test_audio_transparency_label():
    import BitCrusherV9 as bc
    assert "transparent" in bc._audio_transparency_label("libopus", 256000, 2)
    assert bc._audio_transparency_label("libopus", 40000, 2) == "compressed"


def test_audio_flac_lossless_and_opus_cover_command(monkeypatch, tmp_path):
    """FLAC output must not carry a lossy -b:a; opus cover art rides via a second
    ffmetadata input with -map_metadata 1 (base64 picture is too big for argv)."""
    import BitCrusherV9 as bc

    captured = {}

    def fake_run(cmd, **_kw):
        captured["cmd"] = list(cmd)
        with open(cmd[-1], "wb") as f:
            f.write(b"x")

        class _R:
            returncode = 0
            stderr = ""
        return _R()

    monkeypatch.setattr(bc, "_sp_run", fake_run)
    src = tmp_path / "in.wav"
    src.write_bytes(b"x")

    # FLAC: lossless — no bitrate flag, has a compression level, keeps source rate/ch
    bc._encode_audio_once(str(src), str(tmp_path / "o.flac"), encoder="flac",
                          bitrate_bps=0, sr=0, channels=0, vbr_mode="off",
                          loudnorm=False, highpass_hz=None, lowpass_hz=None)
    c = captured["cmd"]
    assert "-b:a" not in c
    assert "-compression_level" in c
    assert "-ar" not in c and "-ac" not in c  # sr=0/ch=0 keeps source (stays lossless)

    # Opus + cover meta: a second -i input, tags+picture pulled via -map_metadata 1
    meta = tmp_path / "m.txt"
    meta.write_text(";FFMETADATA1\n", encoding="utf-8")
    bc._encode_audio_once(str(src), str(tmp_path / "o.opus"), encoder="libopus",
                          bitrate_bps=128000, sr=48000, channels=2, vbr_mode="on",
                          loudnorm=False, highpass_hz=None, lowpass_hz=None,
                          opus_cover_meta=str(meta))
    c = captured["cmd"]
    assert c.count("-i") == 2
    assert c[c.index("-map_metadata") + 1] == "1"


def test_output_path_never_overwrites_source(tmp_path):
    """Empty prefix+suffix into the source folder at the same extension must
    NOT resolve to the input path — that overwrote originals in place and made
    VMAF compare the file to itself (observed VMAF 99.98, source destroyed)."""
    import BitCrusherV9 as bc

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    collide = bc._build_output_path(
        "video", str(src), str(tmp_path),
        {"output_prefix": "", "output_suffix": ""}, "mp4")
    assert os.path.abspath(collide) != os.path.abspath(str(src))

    # A normal suffix is preserved exactly (no spurious disambiguation).
    normal = bc._build_output_path(
        "video", str(src), str(tmp_path),
        {"output_prefix": "", "output_suffix": "_discord_ready"}, "mp4")
    assert normal.endswith("clip_discord_ready.mp4")


def test_audio_track_map_keepfirst_vs_mix():
    """Multi-track sources must not silently drop track 2. keep-first maps track 0
    explicitly; mix routes every audio track through amix and forces a re-encode
    (amix cannot stream-copy)."""
    import BitCrusherV9 as bc

    keep = bc._audio_map_ffmpeg_args({"mode": "keepfirst", "n": 3, "multi": True})
    assert keep == ["-map", "0:v:0?", "-map", "0:a:0?"]

    ref = {"audio_copy": True}
    mix = bc._audio_map_ffmpeg_args({"mode": "mix", "n": 3, "multi": True}, ref)
    assert "-filter_complex" in mix
    assert "amix=inputs=3" in mix[mix.index("-filter_complex") + 1]
    assert mix[-2:] == ["-map", "[bcaout]"]
    # mix can't stream-copy audio through the filter — force a re-encode.
    assert ref["audio_copy"] is False


def test_audio_track_plan_modes(monkeypatch):
    import BitCrusherV9 as bc

    monkeypatch.setattr(bc, "_count_audio_streams", lambda _p: 2)
    p_keep = bc._audio_track_plan("x.mp4", {"audio_track_mode": "keepfirst"})
    assert p_keep["multi"] and p_keep["mode"] == "keepfirst" and p_keep["notice"]
    p_mix = bc._audio_track_plan("x.mp4", {"audio_track_mode": "mix"})
    assert p_mix["mode"] == "mix" and "mix" in p_mix["notice"].lower()
    # bogus mode falls back to keepfirst
    assert bc._audio_track_plan("x.mp4", {"audio_track_mode": "bogus"})["mode"] == "keepfirst"

    # single-track sources are untouched (no notice, not "multi").
    monkeypatch.setattr(bc, "_count_audio_streams", lambda _p: 1)
    p_single = bc._audio_track_plan("x.mp4", {"audio_track_mode": "mix"})
    assert not p_single["multi"] and p_single["notice"] is None


def test_read_sibling_lrc(tmp_path):
    import BitCrusherV9 as bc

    src = tmp_path / "track.flac"
    src.write_bytes(b"x")
    assert bc._read_sibling_lrc(str(src)) is None  # none present yet

    (tmp_path / "track.lrc").write_text("[00:00.00]hello\n", encoding="utf-8")
    got = bc._read_sibling_lrc(str(src))
    assert got and "hello" in got


def test_embed_lyrics_builds_metadata_command(monkeypatch, tmp_path):
    """_embed_lyrics_into remuxes with -c copy and a lyrics metadata tag."""
    import BitCrusherV9 as bc

    out = tmp_path / "o.mp3"
    out.write_bytes(b"x")
    captured = {}

    def fake_run(cmd, **_kw):
        captured["cmd"] = list(cmd)
        with open(cmd[-1], "wb") as f:
            f.write(b"y")

        class _R:
            returncode = 0
            stderr = ""
        return _R()

    monkeypatch.setattr(bc, "_sp_run", fake_run)
    ok = bc._embed_lyrics_into(str(out), "[00:00.00]hi", status_cb=None)
    assert ok
    c = captured["cmd"]
    assert "-c" in c and "copy" in c
    assert any(a.startswith("lyrics=") for a in c)


def test_lifetime_stats_aggregates_encode_end(tmp_path):
    """Stats tab roll-up: only encode_end rows count; totals/ratio/buckets/encoders
    aggregate correctly and bad rows are ignored."""
    import BitCrusherV9 as bc

    rows = [
        {"event": "start_job", "type": "video"},                       # ignored
        {"event": "encode_end", "type": "video", "original_size": 1000,
         "compressed_size": 250, "vmaf": 96.0, "encoder": "x264", "time_taken": 5.0},
        {"event": "encode_end", "type": "audio", "original_size": 400,
         "compressed_size": 100, "encoder": "libopus"},
        {"event": "encode_end", "type": "video", "original_size": 0,
         "compressed_size": 0},                                        # ignored (zero)
        {"event": "encode_end", "type": "video", "original_size": 600,
         "compressed_size": 300, "vmaf": 82.0, "encoder": "x264"},
    ]
    with open(tmp_path / "run_20260101.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    a = bc.aggregate_lifetime_stats(str(tmp_path))
    assert a["count"] == 3
    assert a["total_original"] == 2000 and a["total_compressed"] == 650
    assert a["bytes_saved"] == 1350
    assert a["by_type"]["video"]["count"] == 2
    assert a["encoders"]["x264"] == 2 and a["encoders"]["libopus"] == 1
    assert a["vmaf"]["count"] == 2
    assert a["vmaf"]["buckets"]["95–98"] == 1 and a["vmaf"]["buckets"]["80–90"] == 1


def test_ipc_send_no_listener_is_false(tmp_path):
    """Send-To hand-off returns False when no instance is listening, so the CLI
    knows to launch the GUI itself instead of silently dropping the files."""
    import BitCrusherV9 as bc

    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    # Nothing is bound to the IPC port in a test process → must report False.
    assert bc._bc_ipc_send([str(f)], timeout=0.5) is False
    # Empty / nonexistent paths never attempt a connection.
    assert bc._bc_ipc_send([]) is False
    assert bc._bc_ipc_send([str(tmp_path / "nope.mp4")]) is False


def test_sendto_launch_target_uses_enqueue():
    import BitCrusherV9 as bc

    exe, args = bc._sendto_launch_target()
    assert isinstance(exe, str) and exe
    assert "--enqueue" in args


def _watch_stub(rules, size_mb, dur_min, monkeypatch):
    """Build a minimal object exposing the watcher-rule methods for testing."""
    import types
    import BitCrusherV9 as bc

    s = types.SimpleNamespace()
    s.settings = {"watch_rules": rules}
    s._WATCH_RULE_KEYS = bc.CompressorGUI._WATCH_RULE_KEYS
    s._wr_num = bc.CompressorGUI._wr_num
    s._probe_duration_seconds = lambda _p, _d=dur_min: _d * 60.0
    for nm in ("_gather_watch_rules", "_watch_rules_overrides"):
        setattr(s, nm, types.MethodType(getattr(bc.CompressorGUI, nm), s))
    monkeypatch.setattr(bc.os.path, "getsize", lambda _p, _s=size_mb: int(_s * 1024 * 1024))
    return s


def test_watch_rules_size_and_duration_overrides(monkeypatch):
    """Watcher rules: size picks the target, duration picks the encoder, blanks
    fall back to the watched defaults, and a rule-chosen encoder is pinned so the
    codec race can't override it."""
    rules = {
        "save_dir": "", "target_mb": "20", "encoder": "svt-av1",
        "big_mb": "50", "big_target": "25", "small_mb": "5", "small_target": "8",
        "long_min": "10", "long_enc": "x265", "short_min": "2", "short_enc": "x264",
    }
    # Large + long → big-target + long-encoder, and pinned.
    ov = _watch_stub(rules, size_mb=100, dur_min=20, monkeypatch=monkeypatch)._watch_rules_overrides("f.mp4")
    assert ov["_watch_target_bytes"] == 25 * 1024 * 1024
    assert ov["encoder"] == "x265" and ov["codec_pinned"] is True and ov["auto_codec"] is False

    # Small + short → small-target + short-encoder.
    ov = _watch_stub(rules, size_mb=3, dur_min=1, monkeypatch=monkeypatch)._watch_rules_overrides("f.mp4")
    assert ov["_watch_target_bytes"] == 8 * 1024 * 1024 and ov["encoder"] == "x264"

    # No condition matches → watched defaults.
    ov = _watch_stub(rules, size_mb=20, dur_min=5, monkeypatch=monkeypatch)._watch_rules_overrides("f.mp4")
    assert ov["_watch_target_bytes"] == 20 * 1024 * 1024 and ov["encoder"] == "svt-av1"


def test_watch_rules_all_blank_is_noop(monkeypatch):
    import BitCrusherV9 as bc

    blank = {k: "" for k in bc.CompressorGUI._WATCH_RULE_KEYS}
    ov = _watch_stub(blank, size_mb=100, dur_min=99, monkeypatch=monkeypatch)._watch_rules_overrides("f.mp4")
    assert ov == {}


def test_vmaf_model_resolution(monkeypatch):
    """The VMAF model resolver validates against the build and falls back safely:
    'default' → build default; 'neg' → the NEG model; 'v1' → v1 if a candidate
    loads, else v0.6.1; a model that won't load is never injected."""
    import BitCrusherV9 as bc

    # Pretend the build only accepts v0.6.1 + neg (no v1).
    def only_v0(model_arg, _ff):
        if not model_arg:
            return True
        return model_arg in ("version=vmaf_v0.6.1neg", "version=vmaf_4k_v0.6.1")
    monkeypatch.setattr(bc, "_vmaf_model_loads", only_v0)
    monkeypatch.setattr(bc, "_first_v1_model_file", lambda: None)

    bc.set_vmaf_model_pref("default"); assert bc.resolve_vmaf_model("ff") == ""
    bc.set_vmaf_model_pref("neg"); assert bc.resolve_vmaf_model("ff") == "version=vmaf_v0.6.1neg"
    bc.set_vmaf_model_pref("4k"); assert bc.resolve_vmaf_model("ff") == "version=vmaf_4k_v0.6.1"
    # v1 not available → safe fallback to build default (empty).
    bc.set_vmaf_model_pref("v1"); assert bc.resolve_vmaf_model("ff") == ""
    bc.set_vmaf_model_pref("auto"); assert bc.resolve_vmaf_model("ff") == ""
    # A model the build won't load must never be injected.
    bc.set_vmaf_model_pref("version=vmaf_bogus"); assert bc.resolve_vmaf_model("ff") == ""

    # Now pretend a v1 embedded model exists → auto/v1 pick it up.
    def with_v1(model_arg, _ff):
        return (not model_arg) or model_arg == "version=vmaf_v1"
    monkeypatch.setattr(bc, "_vmaf_model_loads", with_v1)
    bc.set_vmaf_model_pref("auto"); assert bc.resolve_vmaf_model("ff") == "version=vmaf_v1"
    bc.set_vmaf_model_pref("v1"); assert bc.resolve_vmaf_model("ff") == "version=vmaf_v1"

    # And the '<opt>:' fragment for the filter string.
    monkeypatch.setattr(bc, "_vmaf_model_loads", only_v0)
    bc.set_vmaf_model_pref("neg")
    assert bc._vmaf_model_opt("ff") == "model=version=vmaf_v0.6.1neg:"
    bc.set_vmaf_model_pref("default")
    assert bc._vmaf_model_opt("ff") == ""

    bc.set_vmaf_model_pref("auto")  # leave resolver in the default state


def test_vmaf_model_path_escaping():
    import BitCrusherV9 as bc

    esc = bc._escape_vmaf_opt_path("C:\\models\\vmaf_v1.json")
    assert ":" not in esc.replace("\\:", "")   # drive colon is escaped
    assert "\\\\" not in esc                    # backslashes normalised to '/'


def test_vmaf_floor_metrics_surface_the_valley():
    """The floor metrics must catch a bad scene the mean hides (the average trap):
    a clip that's mostly 98 with a short 72 valley should report a low worst-window
    and low percentiles even though the mean stays high."""
    import BitCrusherV9 as bc

    vals = [98.0] * 100 + [72.0] * 15 + [99.0] * 100
    p1, p5, mw, idx = bc._vmaf_low_metrics(vals, win=10)
    mean = sum(vals) / len(vals)
    assert mean > 95            # the trap: average looks great
    assert p5 <= 73 and p1 <= 73 and mw <= 73   # floors see the 72 scene
    assert 100 <= idx <= 115    # worst window is located inside the 72-valley

    # A fully uniform clip has floor ≈ mean (nothing hidden).
    p1u, p5u, mwu, idxu = bc._vmaf_low_metrics([95.0] * 50, win=10)
    assert abs(mwu - 95.0) < 0.01 and abs(p5u - 95.0) < 0.01

    # Empty input is safe.
    assert bc._vmaf_low_metrics([], win=10) == (None, None, None, None)


def test_vmaf_floor_score_objective_selection():
    import BitCrusherV9 as bc

    d = {"vmaf": 96.7, "harmonic": 95.0, "p5": 72.0, "p1": 70.0, "min_window": 72.0}
    assert bc.vmaf_floor_score(d, "mean") == 96.7
    assert bc.vmaf_floor_score(d, "harmonic") == 95.0
    assert bc.vmaf_floor_score(d, "p5") == 72.0
    assert bc.vmaf_floor_score(d, "window") == 72.0
    assert bc.vmaf_floor_score(None, "window") is None
    # Fallback chain: window falls back to p5 -> harmonic -> mean when keys missing.
    assert bc.vmaf_floor_score({"vmaf": 90.0}, "window") == 90.0


def test_preproc_candidate_gating(monkeypatch):
    """Artifact-aware preprocessing nominates filters from measured features +
    bit starvation: dense-texture starvation keys on ENTROPY (the pathological
    game capture read entropy 7.24 with grain 0.04), camera grain on graininess,
    and rich-bitrate or clean content nominates nothing."""
    import BitCrusherV9 as bc

    monkeypatch.setattr(bc, "_ffmpeg_has_filter", lambda _n: True)
    starved = dict(video_bps=800_000, width=1280, height=720, fps=30)
    rich = dict(video_bps=8_000_000, width=1280, height=720, fps=30)

    # Grain path (starved) → denoise.
    c = bc._preproc_candidates({"graininess": 0.5, "entropy_p95": 5.0}, **starved)
    assert any(x["name"].startswith("denoise") for x in c)
    # Entropy path (crushed bpp, dense texture, low grain) → denoise.
    c = bc._preproc_candidates({"graininess": 0.04, "entropy_p95": 7.3}, **starved)
    assert any(x["name"].startswith("denoise") for x in c)
    # Same features at a rich bitrate → no denoise.
    c = bc._preproc_candidates({"graininess": 0.5, "entropy_p95": 7.3}, **rich)
    assert not any(x["name"].startswith("denoise") for x in c)
    # Banding / blockiness gates.
    c = bc._preproc_candidates({"banding_risk": 0.6, "blockiness": 14.0}, **rich)
    assert {x["name"] for x in c} == {"deband", "deblock"}
    # Clean content → nothing.
    assert bc._preproc_candidates({"graininess": 0.02, "banding_risk": 0.05,
                                   "blockiness": 1.0, "entropy_p95": 4.0}, **rich) == []
    # Explicit user opt-out of denoise is respected.
    c = bc._preproc_candidates({"graininess": 0.5, "entropy_p95": 7.5},
                               allow_denoise=False, **starved)
    assert not any(x["name"].startswith("denoise") for x in c)


def test_preproc_chain_order(monkeypatch):
    """Combined chains must run deblock -> denoise -> deband (source artifact,
    then noise, then banding — which only shows once noise is gone)."""
    import BitCrusherV9 as bc

    cands = [{"name": "deband", "vf": "deband=x"},
             {"name": "denoise_med", "vf": "hqdn3d=x"},
             {"name": "deblock", "vf": "deblock=x"}]
    assert bc._preproc_chain(cands) == "deblock=x,hqdn3d=x,deband=x"


def test_decide_preprocessing_keep_and_reject(monkeypatch):
    """The probe A/B is the shipping gate: the best variant is kept only when it
    clears the margin; otherwise no filter ships."""
    import BitCrusherV9 as bc

    monkeypatch.setattr(bc, "_ffmpeg_has_filter", lambda _n: True)
    feats = {"graininess": 0.5, "entropy_p95": 7.5}
    kw = dict(encoder="x264", video_bps=800_000, scale_width=1280, width=1280,
              height=720, fps=30.0, duration_s=20.0, advanced_options={})

    # Best variant clears the margin → its chain ships.
    monkeypatch.setattr(bc, "_preproc_probe_variants",
                        lambda *_a, **_k: {"baseline": 80.0, "denoise_med": 80.2,
                                           "denoise_light": 81.5})
    chain, info = bc.decide_preprocessing("f.mp4", feats, **kw)
    assert info["kept"] == "denoise_light" and "hqdn3d" in chain

    # Under the margin → nothing ships.
    monkeypatch.setattr(bc, "_preproc_probe_variants",
                        lambda *_a, **_k: {"baseline": 80.0, "denoise_med": 80.2,
                                           "denoise_light": 80.3})
    chain, info = bc.decide_preprocessing("f.mp4", feats, **kw)
    assert chain is None and info["kept"] is None

    # Probe unavailable → nothing ships.
    monkeypatch.setattr(bc, "_preproc_probe_variants", lambda *_a, **_k: None)
    chain, info = bc.decide_preprocessing("f.mp4", feats, **kw)
    assert chain is None


def test_encode_paths_carry_preproc_vf(monkeypatch, tmp_path):
    """A kept prefilter chain must reach the actual ffmpeg -vf argument."""
    import BitCrusherV9 as bc

    captured = {}

    def fake_run(cmd, **_kw):
        captured["cmd"] = [str(c) for c in cmd]
        out = cmd[-1]
        try:
            with open(out, "wb") as f:
                f.write(b"x")
        except Exception:
            pass

        class _R:
            returncode = 0
            stderr = ""
            stdout = ""
        return _R()

    monkeypatch.setattr(bc, "_sp_run", fake_run)
    # Pre-seed the encoder cache so no real ffmpeg probe runs.
    bc.compress_with_handbrake._enc_cache = {"libx264"}
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    ok = bc.compress_with_handbrake(
        input_path=str(src), output_path=str(tmp_path / "o.mp4"),
        encoder="x264", bitrate=500_000, width=1280,
        advanced_options={"preproc_vf": "hqdn3d=3.0:2.0:6.0:4.5"})
    cmd = captured.get("cmd") or []
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=1280:-2" in vf and "hqdn3d=3.0:2.0:6.0:4.5" in vf
    # Order: scale first, prefilter after (probe validated it at delivery res).
    assert vf.index("scale") < vf.index("hqdn3d")


def test_trim_range_parsing():
    """Trim ranges accept SS / MM:SS / HH:MM:SS on both ends and reject nonsense."""
    import pytest
    import BitCrusherV9 as bc

    assert bc._parse_trim_range("1:42-2:05") == (102.0, 125.0)
    assert bc._parse_trim_range("12-31") == (12.0, 31.0)
    assert bc._parse_trim_range("0:00:05.5-0:00:09") == (5.5, 9.0)
    for bad in ("", "5", "9-5", "5-5", "a-b", "1:2:3:4-5"):
        with pytest.raises(ValueError):
            bc._parse_trim_range(bad)


def test_make_trim_intermediate_commands(monkeypatch, tmp_path):
    """Copy mode snaps the start to a keyframe and stream-copies; fade mode is a
    frame-exact near-lossless re-encode with av fades at both ends."""
    import BitCrusherV9 as bc

    captured = {}

    def fake_run(cmd, **_kw):
        captured["cmd"] = [str(c) for c in cmd]
        with open(cmd[-1], "wb") as f:
            f.write(b"x")

        class _R:
            returncode = 0
            stderr = ""
        return _R()

    monkeypatch.setattr(bc, "_sp_run", fake_run)
    monkeypatch.setattr(bc, "_prev_keyframe_time", lambda _p, _t: 4.0)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")

    # Copy mode: -ss at the SNAPPED keyframe, -c copy, duration reaches the exact end.
    res = bc.make_trim_intermediate(str(src), 5.0, 9.0, media_type="video")
    assert res is not None
    out, work = res
    c = captured["cmd"]
    assert c[c.index("-ss") + 1] == "4.000"          # keyframe, not the raw start
    assert c[c.index("-t") + 1] == "5.000"           # 4.0 -> 9.0 = exact end
    assert "copy" in c and "-c" in c
    assert os.path.basename(out) == "in_clip.mp4"
    bc._rmtree_quiet(work)

    # Fade mode: frame-exact -ss, fades in the filter chain, re-encode not copy.
    res = bc.make_trim_intermediate(str(src), 5.0, 9.0, fade=True, media_type="video")
    out, work = res
    c = captured["cmd"]
    assert c[c.index("-ss") + 1] == "5.000"
    assert c[c.index("-t") + 1] == "4.000"
    vf = c[c.index("-vf") + 1]
    assert "fade=t=in" in vf and "fade=t=out" in vf
    af = c[c.index("-af") + 1]
    assert "afade=t=in" in af and "afade=t=out" in af
    assert "libx264" in c and "copy" not in c
    bc._rmtree_quiet(work)


def test_auto_compress_swaps_in_trim_intermediate(monkeypatch, tmp_path):
    """A trim_range must route the pipeline at the intermediate, clean up its temp
    dir afterwards, and fall back to the full file when trimming fails."""
    import BitCrusherV9 as bc

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    fake_clip = tmp_path / "clip_clip.mp4"
    fake_clip.write_bytes(b"y")
    work = tmp_path / "trimwork"
    work.mkdir()

    seen = {}
    monkeypatch.setattr(bc, "make_trim_intermediate",
                        lambda *_a, **_k: (str(fake_clip), str(work)))
    monkeypatch.setattr(bc, "compress_video",
                        lambda inp, *a, **k: seen.setdefault("input", inp) or {"ok": 1})

    st = bc.auto_compress(str(src), str(tmp_path), lambda m, level="INFO": None,
                          5 * 1024 * 1024, "", {"trim_range": "1-3", "_target_is_bytes": True},
                          lambda: False)
    assert seen["input"] == str(fake_clip)       # pipeline consumed the trimmed clip
    assert not work.exists()                     # temp dir cleaned up

    # Invalid range -> warns and compresses the full file.
    seen.clear()
    msgs = []
    bc.auto_compress(str(src), str(tmp_path), lambda m, level="INFO": msgs.append(str(m)),
                     5 * 1024 * 1024, "", {"trim_range": "9-5", "_target_is_bytes": True},
                     lambda: False)
    assert seen["input"] == str(src)
    assert any("Invalid trim range" in m for m in msgs)


def test_rank_energy_windows_finds_peaks_and_weights_mic():
    """Suggestion ranking: finds the loud burst with lead-in, weights track 2
    (mic) above track 1 at equal z-score, and refuses to invent highlights in
    uniform audio."""
    import BitCrusherV9 as bc

    # 60s at 0.5s windows = 120 samples; burst on track 1 at 30-33s (idx 60-66).
    quiet = [10.0] * 120
    burst = list(quiet)
    for i in range(60, 66):
        burst[i] = 200.0
    cands = bc._rank_energy_windows([burst], 0.5, 12.0, top_n=3, total_s=60.0)
    assert len(cands) == 1
    assert cands[0]["start"] <= 30.0 <= cands[0]["end"]     # burst is inside
    assert abs((cands[0]["end"] - cands[0]["start"]) - 12.0) < 0.2   # requested clip length

    # Mic spike (track 2) must outrank an equal game spike (track 1).
    game = list(quiet); game[20] = 120.0
    mic = list(quiet); mic[100] = 120.0
    cands = bc._rank_energy_windows([game, mic], 0.5, 8.0, top_n=2, total_s=60.0)
    assert cands and cands[0]["track"] == 1                 # mic wins the top slot

    # Uniform audio -> no suggestions (never guess).
    assert bc._rank_energy_windows([quiet], 0.5, 12.0, total_s=60.0) == []


def test_spotlight_zone_params():
    """Spotlight zones: frame math from seconds*fps, end clamped to duration,
    base params preserved when empty, pre-existing zones stripped (x264 zones
    must be disjoint), x265 keyed separately."""
    import BitCrusherV9 as bc

    k, v = bc._spotlight_zone_params("", 5.0, 10.0, 30.0, 20.0, "x264")
    assert k == "x264_params" and v.endswith("zones=150,300,b=1.5")
    assert "aq-mode=3" in v                                  # defaults kept

    k, v = bc._spotlight_zone_params("rc-lookahead=40:zones=0,99,b=2", 5.0, 10.0, 30.0, 20.0, "x265")
    assert k == "x265_params"
    assert v == "rc-lookahead=40:zones=150,300,b=1.5"        # old zone stripped

    _, v = bc._spotlight_zone_params("", 15.0, 99.0, 30.0, 20.0, "x264")
    assert v.endswith("zones=450,600,b=1.5")                 # end clamped to 20s


def test_trim_wins_over_spotlight(monkeypatch, tmp_path):
    """When both trim and spotlight are set, trim wins and spotlight is dropped
    with a warning (composing them would double-interpret the timeline)."""
    import BitCrusherV9 as bc

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    fake_clip = tmp_path / "clip_clip.mp4"
    fake_clip.write_bytes(b"y")
    work = tmp_path / "w"; work.mkdir()
    monkeypatch.setattr(bc, "make_trim_intermediate", lambda *_a, **_k: (str(fake_clip), str(work)))
    seen_adv = {}
    monkeypatch.setattr(bc, "compress_video",
                        lambda inp, sp, cb, t, wh, adv, cc: seen_adv.update(adv) or {"ok": 1})
    msgs = []
    adv = {"trim_range": "1-3", "spotlight_range": "1-2", "_target_is_bytes": True}
    bc.auto_compress(str(src), str(tmp_path), lambda m, level="INFO": msgs.append(str(m)),
                     5 * 1024 * 1024, "", adv, lambda: False)
    assert "spotlight_range" not in seen_adv
    assert any("trim wins" in m for m in msgs)


def test_themelab_derive_palette_contrast_safe():
    """The Theme Lab generator must return a complete valid palette whose text
    colours actually clear WCAG AA against their real backgrounds, for both a
    dark and a light base."""
    import ui_aesthetics as ua

    for accent, bg in (("#7C5CFF", "#14161A"),      # dark base
                       ("#4C5BD4", "#F4F6F9"),      # light base
                       ("#00D4AA", "#000000")):     # OLED black
        pal = ua.derive_palette(accent, bg)
        assert set(ua._THEMELAB_KEYS) <= set(pal.keys())
        for k in ua._THEMELAB_KEYS:
            v = pal[k]
            assert isinstance(v, str) and v.startswith("#") and len(v) == 7
            int(v[1:], 16)                          # valid hex
        assert ua._wcag_ratio(pal["FG"], pal["APP_BG"]) >= 7.0
        assert ua._wcag_ratio(pal["FG_SUB"], pal["CARD_BG"]) >= 4.5
        assert ua._wcag_ratio(pal["TITLE"], pal["APP_BG"]) >= 4.5
        assert pal["CARD_BG"].lower() != pal["APP_BG"].lower()


def test_themelab_helpers():
    import ui_aesthetics as ua

    assert ua._hex_norm("abc") == "#aabbcc"
    assert ua._hex_norm("#AABBCC") == "#AABBCC"[:7]
    assert ua._ratio_badge(8.0) == "AAA"
    assert ua._ratio_badge(5.0) == "AA"
    assert ua._ratio_badge(3.2) == "A"
    assert ua._ratio_badge(1.5) == "LOW"
    # White on black is the maximum-contrast pair.
    assert ua._wcag_ratio("#ffffff", "#000000") > 20.0
    assert ua._is_light_hex("#ffffff") and not ua._is_light_hex("#000000")


def test_themelab_wheel_builds_fast():
    """The colour wheel must be vectorised — the old pixel-by-pixel build froze
    the UI for seconds on first open."""
    import time
    import ui_aesthetics as ua

    t0 = time.time()
    img = ua._build_wheel_image(220)
    took = time.time() - t0
    assert img.size == (220, 220)
    assert took < 1.0, f"wheel build too slow: {took:.2f}s"


def _mk_ledger_rec(dev, entropy, encoder="x264", vmaf_model="vmaf_v1.0.16_3d0h",
                   pred=None, n=0):
    """Synthetic ledger record whose first attempt lands at `dev` x the naive
    size model (720p30 @ 1 Mbps video + 128k audio for 10s)."""
    v_bps, a_bps, dur = 1_000_000, 128_000, 10.0
    got = int((v_bps + a_bps) * dur / 8.0 * dev)
    return {
        "schema": 1, "vmaf_model": vmaf_model, "input": "x.mp4",
        "features": {"entropy_p95": entropy, "spatial_complexity": 5.0,
                     "graininess": 0.1, "text_edge_density": 0.1,
                     "blockiness": 1.0, "edge_p95": 100.0},
        "src": {"dur": dur},
        "op": {"encoder_eff": encoder, "width": 1280, "height": 720, "fps": 30.0,
               "v_bps": v_bps, "audio_bps": a_bps, "dur": dur},
        "attempts": [[v_bps, got]],
        "outcome": {"size": got},
        "shadow": ({"dev_pred": pred, "n": n} if pred is not None else None),
    }


def test_ledger_deviation_and_roundtrip(tmp_path):
    import outcome_ledger as ol

    # deviation math: exact overshoot ratio of the naive model
    assert abs(ol.attempt_deviation(1_000_000, 1_410_000, 10.0, 128_000) - 1.0) < 0.01
    assert ol.attempt_deviation(0, 100, 10.0, 0) is None

    # append/load roundtrip with schema + encoder-family filtering
    d = str(tmp_path)
    assert ol.ledger_append(d, _mk_ledger_rec(1.1, 7.0, encoder="x264"))
    assert ol.ledger_append(d, _mk_ledger_rec(0.9, 4.0, encoder="libx265"))
    assert ol.ledger_append(d, {"schema": 99, "junk": True})   # wrong schema -> ignored
    assert len(ol.ledger_load(d)) == 2
    assert len(ol.ledger_load(d, encoder_fam="x265")) == 1
    assert ol.encoder_family("hevc_nvenc") == "x265"
    assert ol.encoder_family("libsvtav1") == "av1"


def test_ledger_shadow_predictor(tmp_path):
    """k-NN deviation prediction: similar content pulls the prediction toward
    its measured deviation; no data stays at the neutral 1.0; shrinkage keeps a
    single sample from dominating."""
    import outcome_ledger as ol

    d = str(tmp_path)
    feats_hi = {"entropy_p95": 7.3, "spatial_complexity": 5.0, "graininess": 0.1,
                "text_edge_density": 0.1, "blockiness": 1.0, "edge_p95": 100.0}

    dev, n = ol.predict_deviation(d, feats_hi, "x264", 1280, 720, 30.0, 1_000_000)
    assert (dev, n) == (1.0, 0)                       # empty ledger -> neutral

    for _ in range(6):                                # high-entropy content overshoots 1.3x
        ol.ledger_append(d, _mk_ledger_rec(1.3, 7.3))
    for _ in range(6):                                # low-entropy content undershoots 0.8x
        ol.ledger_append(d, _mk_ledger_rec(0.8, 2.0))

    dev_hi, n_hi = ol.predict_deviation(d, feats_hi, "x264", 1280, 720, 30.0, 1_000_000)
    assert n_hi > 0 and dev_hi > 1.1                  # pulled toward the 1.3 cluster
    feats_lo = dict(feats_hi, entropy_p95=2.0)
    dev_lo, _ = ol.predict_deviation(d, feats_lo, "x264", 1280, 720, 30.0, 1_000_000)
    assert dev_lo < 0.95                              # pulled toward the 0.8 cluster

    # single sample: shrinkage keeps it near neutral
    d2 = str(tmp_path / "one")
    ol.ledger_append(d2, _mk_ledger_rec(2.0, 7.3))
    dev1, _ = ol.predict_deviation(d2, feats_hi, "x264", 1280, 720, 30.0, 1_000_000)
    assert dev1 < 1.35                                # not dragged to 2.0 by one record


def test_ledger_seed_adjust_guardrails():
    """Stage 2a live seeding: acts only with enough neighbors, clamps the
    correction, respects the feasibility cap, and skips sub-1% nudges."""
    import outcome_ledger as ol

    # Too few neighbors -> no action.
    assert ol.seed_adjust(1_000_000, 1.15, 2) == (1_000_000, False)
    # Normal overshoot correction: 1.15 dev -> ~870k seed.
    bps, acted = ol.seed_adjust(1_000_000, 1.15, 5)
    assert acted and abs(bps - 869_565) < 2_000
    # Wild prediction is clamped to the band (max 1.25).
    bps, _ = ol.seed_adjust(1_000_000, 3.0, 5)
    assert bps >= int(1_000_000 / 1.25) - 1
    # Upward correction (undershoot content) respects the feasibility cap.
    bps, acted = ol.seed_adjust(1_000_000, 0.85, 5, cap_bps=1_050_000)
    assert acted and bps == 1_050_000
    # Negligible correction -> no action (avoid log noise).
    assert ol.seed_adjust(1_000_000, 1.005, 5) == (1_000_000, False)
    # Garbage prediction -> no action.
    assert ol.seed_adjust(1_000_000, 0.0, 5) == (1_000_000, False)


def test_ledger_shadow_report(tmp_path):
    import outcome_ledger as ol

    d = str(tmp_path)
    assert ol.shadow_report(d) == {"n": 0}
    # predictions close to actual -> predictor beats the assume-1.0 baseline
    for _ in range(5):
        ol.ledger_append(d, _mk_ledger_rec(1.30, 7.0, pred=1.28, n=5))
    rep = ol.shadow_report(d)
    assert rep["n"] == 5
    assert rep["pred_mean_abs_err"] < rep["baseline_mean_abs_err"]
    assert rep["verdict"] == "predictor beats baseline"


def test_resolve_vmaf_objective(monkeypatch):
    import BitCrusherV9 as bc

    monkeypatch.delenv("BC_VMAF_OBJECTIVE", raising=False)
    bc.set_vmaf_objective_pref(None)
    assert bc.resolve_vmaf_objective({}) == "window"                       # default
    assert bc.resolve_vmaf_objective({"vmaf_objective": "p5"}) == "p5"     # job opt wins
    assert bc.resolve_vmaf_objective({"vmaf_objective": "bogus"}) == "window"
    monkeypatch.setenv("BC_VMAF_OBJECTIVE", "harmonic")
    assert bc.resolve_vmaf_objective({}) == "harmonic"                     # env
    monkeypatch.delenv("BC_VMAF_OBJECTIVE", raising=False)
