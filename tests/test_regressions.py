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


def test_next_lower_std_width():
    """The ceiling downscale-retry ladder steps to the next standard width,
    then falls back to 80%, then bottoms out at 0 (stop downscaling)."""
    import BitCrusherV9 as bc

    assert bc.next_lower_std_width(1920) == 1600
    assert bc.next_lower_std_width(1280) == 1024
    assert bc.next_lower_std_width(1921) == 1920      # not equal-or-above
    assert bc.next_lower_std_width(426) == 340        # below ladder -> 80% even
    assert bc.next_lower_std_width(300) == 0          # too small -> stop
    assert bc.next_lower_std_width(0) == 0
    # Every step strictly decreases (loop-termination guarantee).
    w, seen = 3840, []
    while w:
        nxt = bc.next_lower_std_width(w)
        assert nxt < w
        seen.append(nxt)
        w = nxt
    assert seen[-1] == 0


def test_size_controller_never_accepts_overshoot():
    """The size target is a hard ceiling. Neither the default-constructed
    controller nor the explicit legacy policy may report an overshoot as
    'close enough' to stop retrying."""
    # Default policy (now no_overshoot_near_max): an over-target result must retry.
    c = SizeController(target_bytes=1_000_000, duration_s=10.0, audio_bps=96_000)
    c.set_initial(seed_v_bps=1_200_000, seed_bytes=1_050_000)  # 5% over the ceiling
    assert c.should_retry(1_050_000)
    # Even a whisker over (1 byte) is not accepted.
    assert c.should_retry(1_000_001)
    # An under-ceiling result inside the window stops the loop.
    c2 = SizeController(target_bytes=1_000_000, duration_s=10.0, audio_bps=96_000)
    c2.set_initial(seed_v_bps=980_000, seed_bytes=999_000)
    assert not c2.should_retry(999_000)
    # Legacy policy is likewise clamped strictly under the ceiling.
    lg = SizeController(target_bytes=1_000_000, duration_s=10.0, audio_bps=96_000,
                        target_policy="legacy")
    lg.set_initial(seed_v_bps=1_200_000, seed_bytes=1_050_000)
    assert lg.should_retry(1_050_000)


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
                   pred=None, n=0, film_grain=None, preproc=None, spotlight=None,
                   worst=None):
    """Synthetic ledger record whose first attempt lands at `dev` x the naive
    size model (720p30 @ 1 Mbps video + 128k audio for 10s)."""
    v_bps, a_bps, dur = 1_000_000, 128_000, 10.0
    got = int((v_bps + a_bps) * dur / 8.0 * dev)
    outcome = {"size": got}
    if worst is not None:
        outcome["min_window"] = worst
    return {
        "schema": 1, "vmaf_model": vmaf_model, "input": "x.mp4",
        "features": {"entropy_p95": entropy, "spatial_complexity": 5.0,
                     "graininess": 0.1, "text_edge_density": 0.1,
                     "blockiness": 1.0, "edge_p95": 100.0},
        "src": {"dur": dur},
        "op": {"encoder_eff": encoder, "width": 1280, "height": 720, "fps": 30.0,
               "v_bps": v_bps, "audio_bps": a_bps, "dur": dur, "target_bytes": got,
               "film_grain": film_grain, "preproc": preproc, "spotlight": spotlight},
        "attempts": [[v_bps, got]],
        "outcome": outcome,
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


def test_ledger_records_effective_encoder():
    """The poisoned-cache scar: when the codec race switches the encoder, the
    outcome must be attributed to the encoder that ACTUALLY ran (encoder_eff),
    never to the request (encoder_req)."""
    import outcome_ledger as ol

    # Requested x264, race switched to AV1 -> the effective op must say av1.
    op = ol.build_op(target_bytes=5_000_000, encoder_req="x264", encoder_eff="av1",
                     width=1920, height=1080, fps=30.0, v_bps=1_000_000,
                     audio_bps=128_000, audio_copy=False, preset="slow",
                     quality_mode="max", preproc=None, film_grain=None,
                     film_grain_ratio=None, spotlight=None, dur=60.0)
    assert op["encoder_req"] == "x264"
    assert op["encoder_eff"] == "av1"
    assert op["encoder_eff"] != op["encoder_req"]
    assert ol.encoder_family(op["encoder_eff"]) == "av1"

    # A record built from this op is filtered/attributed by the effective family.
    rec = ol.build_record(input_path="x.mp4", features={}, src={}, op=op,
                          attempts=[[1_000_000, 4_900_000]], race=None,
                          outcome={"size": 4_900_000}, shadow=None,
                          vmaf_model="vmaf_v1")
    assert ol.encoder_family(rec["op"]["encoder_eff"]) == "av1"


def test_ledger_build_record_attempts_with_reject_reason():
    """build_record's attempts field must accept the richer
    [v_bps, actual_bytes, accepted, reason] shape (per-attempt rejection
    tracking) while staying backward compatible with plain [v_bps, bytes]
    pairs, and record_deviation (which only ever reads index 0/1) must keep
    working unchanged against the richer shape."""
    import outcome_ledger as ol

    op = ol.build_op(target_bytes=5_000_000, encoder_req="x264", encoder_eff="x264",
                     width=1920, height=1080, fps=30.0, v_bps=1_000_000,
                     audio_bps=128_000, audio_copy=False, preset="slow",
                     quality_mode="max", preproc=None, film_grain=None,
                     film_grain_ratio=None, spotlight=None, dur=60.0)
    rec = ol.build_record(
        input_path="x.mp4", features={}, src={}, op=op,
        attempts=[(1_000_000, 6_000_000, True, "primary"),
                 (700_000, 4_500_000, False, "retry_worse_than_best"),
                 (0, 0, False, "encode_failed"),          # zero-byte: must be dropped
                 (500_000, 4_200_000)],                   # legacy 2-tuple still accepted
        race=None, outcome={"size": 4_200_000}, shadow=None, vmaf_model="vmaf_v1")

    assert rec["attempts"][0] == [1_000_000, 6_000_000, True, "primary"]
    assert rec["attempts"][1] == [700_000, 4_500_000, False, "retry_worse_than_best"]
    assert rec["attempts"][2] == [500_000, 4_200_000]      # legacy pair, no crash
    assert len(rec["attempts"]) == 3                       # the (0, 0) pair was dropped

    dev = ol.record_deviation(rec)
    assert dev is not None
    assert dev == ol.attempt_deviation(1_000_000, 6_000_000, 60.0, 128_000)


def test_ledger_build_op_manual_override_fields():
    """build_op's manual-override fields default safely and round-trip the
    user-override delta (requested vs advised vs whether it was applied)."""
    import outcome_ledger as ol

    default_op = ol.build_op(target_bytes=1, encoder_req="x264", encoder_eff="x264",
                             width=1, height=1, fps=30.0, v_bps=1, audio_bps=1,
                             audio_copy=False, preset=None, quality_mode=None,
                             preproc=None, film_grain=None, film_grain_ratio=None,
                             spotlight=None, dur=1.0)
    assert default_op["manual_bitrate_requested"] is None
    assert default_op["advised_v_bps"] is None
    assert default_op["override_applied"] is False

    op = ol.build_op(target_bytes=1, encoder_req="x264", encoder_eff="x264",
                     width=1, height=1, fps=30.0, v_bps=300_000, audio_bps=1,
                     audio_copy=False, preset=None, quality_mode=None,
                     preproc=None, film_grain=None, film_grain_ratio=None,
                     spotlight=None, dur=1.0,
                     manual_bitrate_requested=300_000, advised_v_bps=1_800_000,
                     override_applied=True)
    assert op["manual_bitrate_requested"] == 300_000
    assert op["advised_v_bps"] == 1_800_000
    assert op["override_applied"] is True


def test_video_ledger_records_failure_on_infeasible_budget(monkeypatch, tmp_path):
    """The ledger must see FAILED encodes, not just successes -- previously
    every RuntimeError abort (undecodable source, infeasible budget, exhausted
    fallback chain) skipped the ledger-write block entirely, so nothing ever
    recorded what fails or why."""
    import pytest
    import BitCrusherV9 as bc

    settings_dir = tmp_path / "user_settings"
    monkeypatch.setattr(bc, "USER_SETTINGS_DIR", str(settings_dir))

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 5_000_000)
    _stub_video_backend(bc, monkeypatch, duration=600.0)  # 10 min @ 1MB target -> infeasible

    def _must_not_run(*_a, **_k):
        raise AssertionError("encoder must not be invoked for an infeasible target")
    monkeypatch.setattr(bc, "compress_with_handbrake", _must_not_run)
    monkeypatch.setattr(bc, "_remux_smart", _must_not_run)

    with pytest.raises(RuntimeError, match=r"\[Budget\] Target .* is infeasible"):
        bc.compress_video(
            str(src), str(tmp_path), lambda *_a, **_k: None,
            target_size_mb=1, webhook_url=None,
            advanced_options={"scene_zones": False, "measure_quality": False,
                               "quality_mode": "max", "auto_codec": False},
            cancel_cb=lambda: False,
        )

    ledger_file = settings_dir / "stats" / "ledger.jsonl"
    assert ledger_file.exists()
    recs = [json.loads(l) for l in ledger_file.read_text().splitlines() if l.strip()]
    assert len(recs) == 1
    assert recs[0]["outcome"]["success"] is False
    assert recs[0]["outcome"]["error_stage"] == "budget"
    assert recs[0]["outcome"]["error_code"] == "budget_infeasible"


def test_video_ledger_attempts_carry_accept_reject_reason(monkeypatch, tmp_path):
    """Every attempt across the retry/downscale pipeline must land in the
    ledger's attempts field tagged accepted/rejected with a reason -- not just
    an opaque (v_bps, bytes) pair -- and the accepted (primary) attempt must
    be present."""
    import BitCrusherV9 as bc

    settings_dir = tmp_path / "user_settings"
    monkeypatch.setattr(bc, "USER_SETTINGS_DIR", str(settings_dir))

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 5_000_000)  # skip passthrough
    _stub_video_backend(bc, monkeypatch, duration=5.0, width=640, height=360)

    target_mb = 2
    oversized_bytes = int(target_mb * 1024 * 1024 * 1.5)  # always over ceiling

    def fake_handbrake(*, output_path, **_kw):
        with open(output_path, "wb") as f:
            f.write(b"z" * oversized_bytes)
        return True

    monkeypatch.setattr(bc, "compress_with_handbrake", fake_handbrake)
    monkeypatch.setattr(bc, "_remux_smart", lambda *_a, **_k: False)

    bc.compress_video(
        str(src), str(tmp_path), lambda *_a, **_k: None,
        target_size_mb=target_mb, webhook_url=None,
        advanced_options={"scene_zones": False, "measure_quality": False,
                           "quality_mode": "max", "auto_codec": False},
        cancel_cb=lambda: False,
    )

    ledger_file = settings_dir / "stats" / "ledger.jsonl"
    recs = [json.loads(l) for l in ledger_file.read_text().splitlines() if l.strip()]
    assert len(recs) == 1
    attempts = recs[0]["attempts"]
    assert len(attempts) >= 2                       # primary + at least one retry/downscale
    assert all(len(a) == 4 for a in attempts)        # [v_bps, bytes, accepted, reason]
    assert attempts[0][2] is True and attempts[0][3] == "primary"
    reasons = {a[3] for a in attempts}
    assert reasons - {"primary"}                     # at least one non-primary reason present
    assert all(a[2] is False for a in attempts[1:])  # every retry/downscale here was rejected


def test_smart_rate_learn_from_result_confidence_grows_with_observations(tmp_path):
    """learn_from_result must pass a REAL per-bucket confidence into
    update_overshoot instead of the old hardcoded 0.5 -- a thin bucket (first
    observation) should move its overshoot factor much less than a
    well-observed bucket (many prior observations) reacting to the same
    fresh ratio."""
    import pytest
    import smart_rate as sr

    d = str(tmp_path)

    # First observation ever for this bucket: confidence must be ~0 (n=0
    # before this update) and the factor should barely move off 1.00.
    sr.learn_from_result(d, encoder="x264", container="mp4",
                         target_bytes=1_000_000, actual_bytes=1_100_000,
                         width_hint=1920, fps_hint=30.0)
    s = sr.load_stats(d)
    factor_after_1 = sr.get_dynamic_overshoot(s, "x264", "mp4", width=1920, fps=30.0)
    assert sr.get_overshoot_confidence(s, "x264", "mp4", width=1920, fps=30.0) == pytest.approx(0.1)

    # Feed the SAME bucket 9 more observations so confidence saturates near 1.0.
    for _ in range(9):
        sr.learn_from_result(d, encoder="x264", container="mp4",
                             target_bytes=1_000_000, actual_bytes=1_100_000,
                             width_hint=1920, fps_hint=30.0)
    s = sr.load_stats(d)
    assert sr.get_overshoot_confidence(s, "x264", "mp4", width=1920, fps=30.0) == pytest.approx(1.0)

    # A fresh, DIFFERENT bucket (different resolution) must start cold again
    # (n=0 -> confidence 0), independent of the well-observed 1920-wide bucket.
    assert sr.get_overshoot_confidence(s, "x264", "mp4", width=640, fps=30.0) == 0.0


def test_video_warm_start_narrows_k_bounds_from_ledger_prior(monkeypatch, tmp_path):
    """When the ledger has >=3 similar prior encodes, the SizeController must
    be constructed with a narrower bytes-per-bit search window than the cold
    [0.55, 1.25] default -- the warm-start lever that lets the retry loop
    bracket a good bitrate faster instead of starting from scratch every time."""
    import outcome_ledger as ol
    import BitCrusherV9 as bc

    settings_dir = tmp_path / "user_settings"
    monkeypatch.setattr(bc, "USER_SETTINGS_DIR", str(settings_dir))
    stats_dir = str(settings_dir / "stats")

    # Seed 3 prior x264 records whose first-attempt deviation consistently
    # overshoots (dev ~1.8x the naive size estimate) for a plain feature set,
    # so predict_deviation returns a confident, non-neutral prediction.
    for i in range(3):
        rec = ol.build_record(
            input_path=f"prior_{i}.mp4",
            features={"entropy_p95": 5.0, "spatial_complexity": 5.0},
            src={"w": 640, "h": 360, "fps": 30.0, "dur": 5.0},
            op=ol.build_op(target_bytes=2_000_000, encoder_req="x264", encoder_eff="x264",
                           width=640, height=360, fps=30.0, v_bps=1_000_000,
                           audio_bps=128_000, audio_copy=False, preset="medium",
                           quality_mode="max", preproc=None, film_grain=None,
                           film_grain_ratio=None, spotlight=None, dur=5.0),
            attempts=[[1_000_000, int(1_000_000 * 1.8)]],
            race=None, outcome={"size": int(1_000_000 * 1.8)}, shadow=None,
            vmaf_model="vmaf_v0.6.1")
        ol.ledger_append(stats_dir, rec)

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 5_000_000)
    _stub_video_backend(bc, monkeypatch, duration=5.0, width=640, height=360)

    captured = {}
    _RealController = bc.SizeController

    class _CapturingController(_RealController):
        def __init__(self, *a, **kw):
            captured["_k_low"] = kw.get("_k_low")
            captured["_k_high"] = kw.get("_k_high")
            super().__init__(*a, **kw)

    monkeypatch.setattr(bc, "SizeController", _CapturingController)

    def fake_handbrake(*, output_path, **_kw):
        with open(output_path, "wb") as f:
            f.write(b"z" * 1_800_000)
        return True

    monkeypatch.setattr(bc, "compress_with_handbrake", fake_handbrake)
    monkeypatch.setattr(bc, "_remux_smart", lambda *_a, **_k: False)

    bc.compress_video(
        str(src), str(tmp_path), lambda *_a, **_k: None,
        target_size_mb=2, webhook_url=None,
        advanced_options={"scene_zones": False, "measure_quality": False,
                           "quality_mode": "max", "auto_codec": False},
        cancel_cb=lambda: False,
    )

    assert captured["_k_low"] is not None and captured["_k_high"] is not None
    assert (captured["_k_low"], captured["_k_high"]) != (0.55, 1.25)
    assert 0.35 <= captured["_k_low"] < captured["_k_high"] <= 1.80


def test_smart_rate_klass_bucket_falls_back_when_thin(tmp_path):
    """The graduated per-content-class trust ladder: get_dynamic_overshoot
    must use the coarse (no-klass) bucket while the content-class-specific
    bucket is thin (< _KLASS_MIN_N observations), then switch to the
    class-specific factor once it earns enough data -- exactly the same
    n-gated trust pattern outcome_ledger's codec-prior already uses,
    generalized to smart_rate's overshoot buckets."""
    import pytest
    import smart_rate as sr

    d = str(tmp_path)

    # Coarse bucket learns a mild overshoot correction from "general" content.
    for _ in range(5):
        sr.learn_from_result(d, encoder="x264", container="mp4",
                             target_bytes=1_000_000, actual_bytes=1_050_000,
                             width_hint=1920, fps_hint=30.0, klass_hint=None)
    # A "screen_ui" class bucket with only 1 observation, pulling HARD the
    # other direction (heavy undershoot) -- must NOT be trusted yet. This
    # update ALSO dual-writes the coarse bucket (never orphaned), so compare
    # against the coarse factor as it stands right after this same call, not
    # a stale pre-observation snapshot.
    sr.learn_from_result(d, encoder="x264", container="mp4",
                         target_bytes=1_000_000, actual_bytes=920_000,
                         width_hint=1920, fps_hint=30.0, klass_hint="screen_ui")
    s = sr.load_stats(d)
    coarse_factor = sr.get_dynamic_overshoot(s, "x264", "mp4", width=1920, fps=30.0)
    thin_klass_read = sr.get_dynamic_overshoot(s, "x264", "mp4", width=1920, fps=30.0, klass="screen_ui")
    assert thin_klass_read == pytest.approx(coarse_factor, abs=1e-6)  # fell back to coarse

    # Feed the class bucket up to the trust threshold with a CONSISTENT,
    # strongly different ratio -- now it must win over the coarse bucket.
    for _ in range(sr._KLASS_MIN_N + 2):
        sr.learn_from_result(d, encoder="x264", container="mp4",
                             target_bytes=1_000_000, actual_bytes=920_000,
                             width_hint=1920, fps_hint=30.0, klass_hint="screen_ui")
    s = sr.load_stats(d)
    trusted_klass_read = sr.get_dynamic_overshoot(s, "x264", "mp4", width=1920, fps=30.0, klass="screen_ui")
    coarse_factor_now = sr.get_dynamic_overshoot(s, "x264", "mp4", width=1920, fps=30.0)
    # The class bucket's own EMA history (fewer, undershoot-only updates)
    # necessarily diverged from the coarse bucket's (more updates, mixed
    # ratios, dual-written on every call) -- and once trusted, the read
    # returns the class-specific value, not the coarse one.
    assert trusted_klass_read != pytest.approx(coarse_factor_now, abs=1e-6)
    assert 0.90 <= trusted_klass_read <= 1.12

    # A DIFFERENT class (unrelated, no observations) must still fall back cleanly.
    fresh_klass_read = sr.get_dynamic_overshoot(s, "x264", "mp4", width=1920, fps=30.0, klass="film_grain")
    assert fresh_klass_read == pytest.approx(coarse_factor_now, abs=1e-6)


def test_dashboard_build_trend_model():
    """build_trend_model aggregates predicted-vs-actual error across MULTIPLE
    ledger records (not one, like build_dashboard_model) into a per-predictor
    first-half-vs-second-half trend, so 'is this predictor getting better'
    is answerable from the ledger."""
    import pytest
    import dashboard as dash

    records = []
    # Ledger-dev predictor: error shrinks from 0.5 to 0.1 across 4 records.
    for i, dev_pred in enumerate([1.5, 1.4, 1.05, 1.0]):
        records.append({
            "attempts": [[1_000_000, 1_000_000]],   # dev == 1.0 (actual == naive expectation)
            "op": {"dur": 8.0, "audio_bps": 0},
            "src": {"dur": 8.0},
            "shadow": {"dev_pred": dev_pred},
            "outcome": {"retries_per_encode": i},
        })

    model = dash.build_trend_model(records)
    ld = model["ledger_dev"]
    assert ld["n"] == 4
    assert ld["series"] == [pytest.approx(0.5), pytest.approx(0.4),
                            pytest.approx(0.05), pytest.approx(0.0)]
    assert ld["improving"] is True
    assert ld["first_half_mean"] > ld["second_half_mean"]

    assert model["probe"]["n"] == 0          # no probe_dev_pred/actual in the fixture
    assert model["advisor"]["n"] == 0        # no advisor_q_pred/vmaf in the fixture
    assert model["retries_per_encode"]["series"] == [0.0, 1.0, 2.0, 3.0]


def test_ledger_lookup_by_signature_exact_and_near_match(tmp_path):
    """lookup_by_signature (the content-fingerprint recall cache) must find a
    prior encode of the SAME input signature within tolerance_pct of the
    requested target, prefer the most recent match, and return None for a
    signature/target/encoder that was never seen."""
    import outcome_ledger as ol

    d = str(tmp_path)
    sig = "abc123-same-file"

    def _mk(sig_, target, ts, v_bps, encoder="x264"):
        op = ol.build_op(target_bytes=target, encoder_req=encoder, encoder_eff=encoder,
                         width=1280, height=720, fps=30.0, v_bps=v_bps,
                         audio_bps=128_000, audio_copy=False, preset=None,
                         quality_mode=None, preproc=None, film_grain=None,
                         film_grain_ratio=None, spotlight=None, dur=10.0)
        rec = ol.build_record(input_path="whatever.mp4", features={}, src={"input_sig": sig_},
                              op=op, attempts=[[v_bps, target]], race=None,
                              outcome={"size": target}, shadow=None, vmaf_model="vmaf_v0.6.1")
        rec["ts"] = ts  # build_record stamps "now"; override for deterministic ordering
        ol.ledger_append(d, rec)

    _mk(sig, 2_000_000, "2026-01-01T00:00:00", 1_500_000)
    _mk(sig, 2_010_000, "2026-01-02T00:00:00", 1_550_000)   # newer, still within tolerance
    _mk(sig, 9_000_000, "2026-01-03T00:00:00", 6_000_000)   # way off target, must not match
    _mk("different-file-sig", 2_000_000, "2026-01-04T00:00:00", 1_600_000)  # different input

    hit = ol.lookup_by_signature(d, sig, 2_000_000, encoder="x264", tolerance_pct=3.0)
    assert hit is not None
    assert hit["ts"] == "2026-01-02T00:00:00"  # most recent within-tolerance match wins
    assert hit["v_bps"] == 1_550_000

    assert ol.lookup_by_signature(d, "never-seen-sig", 2_000_000) is None
    assert ol.lookup_by_signature(d, sig, 2_000_000, encoder="av1") is None  # wrong family
    assert ol.lookup_by_signature(d, "", 2_000_000) is None


def test_ledger_detect_anomalies_requires_all_predictors_to_miss(tmp_path):
    """detect_anomalies must flag a record only when EVERY predictor that had
    an opinion missed badly -- a record where even one predictor was close
    is not an anomaly, just normal predictor noise."""
    import outcome_ledger as ol

    d = str(tmp_path)

    def _rec(dev_pred, probe_pred, probe_actual, advisor_pred, advisor_actual, ts):
        op = ol.build_op(target_bytes=2_000_000, encoder_req="x264", encoder_eff="x264",
                         width=1280, height=720, fps=30.0, v_bps=1_000_000,
                         audio_bps=128_000, audio_copy=False, preset=None,
                         quality_mode=None, preproc=None, film_grain=None,
                         film_grain_ratio=None, spotlight=None, dur=10.0)
        rec = ol.build_record(
            input_path="x.mp4", features={}, src={}, op=op,
            attempts=[[1_000_000, 1_500_000]],  # dev == 1.5 (actual/expected-ish)
            race=None, outcome={"size": 1_500_000, "vmaf": advisor_actual}, shadow={
                "dev_pred": dev_pred, "probe_dev_pred": probe_pred,
                "probe_dev_actual": probe_actual, "advisor_q_pred": advisor_pred,
            }, vmaf_model="vmaf_v0.6.1")
        rec["ts"] = ts
        ol.ledger_append(d, rec)

    # Ledger-dev predictor was close (1.45 vs actual ~1.42..1.5); NOT anomalous
    # even though probe/advisor are wildly off.
    _rec(dev_pred=1.45, probe_pred=3_000_000, probe_actual=1_000_000,
        advisor_pred=20.0, advisor_actual=90.0, ts="2026-01-01T00:00:00")
    # Every present predictor missed badly here -> anomalous.
    _rec(dev_pred=5.0, probe_pred=3_000_000, probe_actual=1_000_000,
        advisor_pred=20.0, advisor_actual=90.0, ts="2026-01-02T00:00:00")

    anomalies = ol.detect_anomalies(d)
    assert len(anomalies) == 1
    assert anomalies[0]["ts"] == "2026-01-02T00:00:00"
    assert set(anomalies[0]["missed_by"]) == {"ledger_dev", "probe", "advisor"}


def test_ledger_audit_vmaf_scale_counts(tmp_path):
    """audit_vmaf_scale must report a population count per VMAF scale tag and
    always include the standing safety note (predict_deviation has no scale
    gate, but is safe only because it never touches VMAF values)."""
    import outcome_ledger as ol

    d = str(tmp_path)
    for i, model in enumerate(["vmaf_v0.6.1", "vmaf_v0.6.1", "vmaf_v1"]):
        op = ol.build_op(target_bytes=1, encoder_req="x264", encoder_eff="x264",
                         width=1, height=1, fps=30.0, v_bps=1, audio_bps=1,
                         audio_copy=False, preset=None, quality_mode=None,
                         preproc=None, film_grain=None, film_grain_ratio=None,
                         spotlight=None, dur=1.0)
        rec = ol.build_record(input_path=f"x{i}.mp4", features={}, src={}, op=op,
                              attempts=[], race=None, outcome={}, shadow=None,
                              vmaf_model=model)
        ol.ledger_append(d, rec)

    result = ol.audit_vmaf_scale(d)
    assert result["counts"].get("v0.6.1") == 2
    assert result["counts"].get("v1") == 1
    assert "predict_deviation" in result["note"]


def test_ledger_build_op_provenance_and_overhead_fields():
    """build_op's two_pass/encoder_version/hwaccel/overhead fields round-trip
    and default to None/False safely when omitted."""
    import outcome_ledger as ol

    default_op = ol.build_op(target_bytes=1, encoder_req="x264", encoder_eff="x264",
                             width=1, height=1, fps=30.0, v_bps=1, audio_bps=1,
                             audio_copy=False, preset=None, quality_mode=None,
                             preproc=None, film_grain=None, film_grain_ratio=None,
                             spotlight=None, dur=1.0)
    assert default_op["two_pass"] is None
    assert default_op["encoder_version"] is None
    assert default_op["hwaccel"] is None
    assert default_op["overhead_predicted"] is None
    assert default_op["overhead_measured"] is None

    op = ol.build_op(target_bytes=1, encoder_req="x264", encoder_eff="x264",
                     width=1, height=1, fps=30.0, v_bps=1, audio_bps=1,
                     audio_copy=False, preset=None, quality_mode=None,
                     preproc=None, film_grain=None, film_grain_ratio=None,
                     spotlight=None, dur=1.0,
                     two_pass=True, encoder_version="ffmpeg 7.1", hwaccel="NVENC",
                     overhead_predicted=1.02, overhead_measured=1.031)
    assert op["two_pass"] is True
    assert op["encoder_version"] == "ffmpeg 7.1"
    assert op["hwaccel"] == "NVENC"
    assert op["overhead_predicted"] == 1.02
    assert op["overhead_measured"] == 1.031


def test_ledger_neighbor_op_flags(tmp_path):
    """Operating-point flags steer neighbour matching: two clusters with
    identical content but opposite film-grain settings and opposite size
    deviations must each pull the same-flag query toward its own cluster,
    instead of blending (the grain-synth vs plain cross-contamination bug)."""
    import outcome_ledger as ol

    d = str(tmp_path)
    feats = {"entropy_p95": 6.0, "spatial_complexity": 5.0, "graininess": 0.1,
             "text_edge_density": 0.1, "blockiness": 1.0, "edge_p95": 100.0}
    for _ in range(6):                                # grain-on encodes overshoot
        ol.ledger_append(d, _mk_ledger_rec(1.4, 6.0, film_grain=14))
    for _ in range(6):                                # grain-off encodes undershoot
        ol.ledger_append(d, _mk_ledger_rec(0.9, 6.0, film_grain=None))

    dev_on, n_on = ol.predict_deviation(d, feats, "x264", 1280, 720, 30.0,
                                        1_000_000, op_flags={"film_grain": 14})
    dev_off, n_off = ol.predict_deviation(d, feats, "x264", 1280, 720, 30.0,
                                          1_000_000, op_flags={"film_grain": None})
    assert n_on > 0 and n_off > 0
    assert dev_on > dev_off                           # flag separates the clusters
    assert dev_on > 1.15                              # pulled toward the 1.4 cluster
    assert dev_off < 1.05                             # pulled toward the 0.9 cluster


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


# Pictographic-emoji ranges. Logs/status messages must stay plain ASCII with
# [Tag] prefixes (the codebase has an emoji-mojibake corruption history); this
# guard makes that invariant self-enforcing instead of discipline-only. Arrows
# are intentionally NOT included: the i18n table maps a literal arrow key.
# Emoji/dingbat/symbol blocks. Excludes arrows (U+2190-21FF), math, punctuation
# and box-drawing/geometric shapes (U+2500-25FF) which are legit UI glyphs.
_EMOJI_RE = __import__("re").compile("[℀-⅏⌀-⏿☀-➿⬀-⯿\U0001F000-\U0001FAFF]")
# C1 control chars are the reliable fingerprint of UTF-8 bytes misdecoded as
# cp1252/latin-1 (the mojibake that once corrupted 52 strings). They are never
# legitimate in source, so a whole-file scan is the strongest guard.
_C1_RE = __import__("re").compile("[" + chr(0x80) + "-" + chr(0x9f) + "]")


def test_logs_are_ascii_only():
    """No string literal in the root modules may contain pictographic emoji.
    ast is used so comments are ignored; only real string constants are scanned.
    (i18n translation strings are non-ASCII but contain no pictographic emoji.)"""
    import ast

    offenders = []
    for py in sorted(ROOT.glob("*.py")):
        src = py.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                m = _EMOJI_RE.search(node.value)
                if m:
                    offenders.append(f"{py.name}:{node.lineno}: {m.group()!r} in {node.value[:50]!r}")
    assert not offenders, "emoji found in string literals:\n" + "\n".join(offenders)


def _unmojibake(s):
    """Reverse cp1252 mojibake: rebuild the original bytes (ascii/latin-1/raw-C1
    pass through, cp1252-remapped printables encode back) and re-decode UTF-8.
    Legit Latin-1/CJK text does NOT round-trip to a *different* valid string, so
    a change here is the reliable fingerprint of a double-encoded UTF-8 string --
    catching the emoji mojibake that has NO C1 chars (e.g. cp1252 0x9F->Y-diaeresis)
    which the C1 scan alone missed."""
    out = bytearray()
    for ch in s:
        o = ord(ch)
        if o < 0x100:
            out.append(o)
        else:
            try:
                out.extend(ch.encode("cp1252"))
            except Exception:
                out.extend(ch.encode("utf-8"))
    try:
        return out.decode("utf-8")
    except Exception:
        return s


def test_no_mojibake():
    """No root module may contain double-encoded (mojibake) text. Covers both C1
    control chars and the C1-free emoji mojibake that slipped past a naive scan."""
    offenders = []
    for py in sorted(ROOT.glob("*.py")):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if _C1_RE.search(line):
                offenders.append(f"{py.name}:{i} (C1 control)")
            elif any(ord(c) > 0x7f for c in line) and _unmojibake(line) != line:
                offenders.append(f"{py.name}:{i} (double-encoded)")
    assert not offenders, "mojibake found at:\n" + "\n".join(offenders)


# --- Video smart-source guard (compress_video passthrough + shrink race) ---
# compress_video() never had a dedicated test before (other tests only mock it
# as a dependency); it always calls real ffprobe/ffmpeg via a handful of choke
# points, so these stub those points instead of extracting a "testable" helper.

def _stub_video_backend(bc, monkeypatch, *, duration=5.0, width=640, height=360):
    """Fake every subprocess/learning boundary compress_video touches so a
    passthrough/shrink-race test never shells out or writes real user stats."""
    probe = {
        "format": {"duration": str(duration)},
        "streams": [
            {"codec_type": "video", "width": width, "height": height,
             "avg_frame_rate": "30/1", "bit_rate": "1000000"},
            {"codec_type": "audio", "channels": 2, "sample_rate": "48000"},
        ],
    }
    monkeypatch.setattr(bc, "_probe_media_cached", lambda _p: probe)
    monkeypatch.setattr(bc, "_jsonl_log", lambda *_a, **_k: None)
    monkeypatch.setattr(bc, "learn_from_result", lambda *_a, **_k: None)
    monkeypatch.setattr(bc, "guardrail_adjust", lambda *_a, **_k: None)


def test_video_passthrough_never_inflates(monkeypatch, tmp_path):
    """Source already under target: remux passthrough must ship a file no
    bigger than the source, not fall through to a full re-encode."""
    import BitCrusherV9 as bc

    src = tmp_path / "in.mp4"
    src_bytes = b"x" * 500_000
    src.write_bytes(src_bytes)

    def fake_remux(_src, dst, _privacy=None):
        with open(dst, "wb") as f:
            f.write(src_bytes)
        return True

    _stub_video_backend(bc, monkeypatch)
    monkeypatch.setattr(bc, "_remux_smart", fake_remux)

    result = bc.compress_video(
        str(src), str(tmp_path), lambda *_a, **_k: None,
        target_size_mb=2, webhook_url=None,
        advanced_options={"scene_zones": False, "measure_quality": False,
                           "quality_mode": "max"},
        cancel_cb=lambda: False,
    )

    assert result["passthrough"] is True
    assert result["compressed_size"] <= result["original_size"]
    assert os.path.getsize(result["output_path"]) == len(src_bytes)


def test_video_passthrough_remux_fail_falls_back_to_crf18(monkeypatch, tmp_path):
    """Remux failure must fall back to a bounded near-lossless CRF-18 encode,
    not silently proceed into the full bitrate-targeting pipeline."""
    import BitCrusherV9 as bc

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 500_000)

    _stub_video_backend(bc, monkeypatch)
    monkeypatch.setattr(bc, "_remux_smart", lambda *_a, **_k: False)

    captured = {}

    def fake_handbrake(*, input_path, output_path, encoder, bitrate, crf, **_kw):
        captured["encoder"] = encoder
        captured["crf"] = crf
        captured["bitrate"] = bitrate
        with open(output_path, "wb") as f:
            f.write(b"y" * 400_000)
        return True

    monkeypatch.setattr(bc, "compress_with_handbrake", fake_handbrake)

    result = bc.compress_video(
        str(src), str(tmp_path), lambda *_a, **_k: None,
        target_size_mb=2, webhook_url=None,
        advanced_options={"scene_zones": False, "measure_quality": False,
                           "quality_mode": "max"},
        cancel_cb=lambda: False,
    )

    assert result["ok"] is True
    assert captured["crf"] == 18
    assert captured["bitrate"] is None
    assert os.path.getsize(result["output"]) == 400_000


def test_video_shrink_race_adopts_only_a_clear_win(monkeypatch, tmp_path):
    """Source-as-candidate race: adopt the re-encode only when it beats the
    original by >10% and clears the VMAF floor; otherwise keep the original."""
    import BitCrusherV9 as bc

    src = tmp_path / "in.mp4"
    src_bytes = b"x" * 3_000_000  # >= the race's 2MB/25%-of-target trigger floor
    src.write_bytes(src_bytes)

    def fake_remux(_src, dst, _privacy=None):
        with open(dst, "wb") as f:
            f.write(src_bytes)
        return True

    adv = {"scene_zones": False, "measure_quality": True, "quality_mode": "max"}

    # --- reject: shrink candidate isn't a clear win (only 5% smaller) ---
    _stub_video_backend(bc, monkeypatch)
    monkeypatch.setattr(bc, "_remux_smart", fake_remux)
    monkeypatch.setattr(bc, "extract_video_duration", lambda _p: 5.0)

    def fake_shrink_reject(*, input_path, output_path, **_kw):
        with open(output_path, "wb") as f:
            f.write(b"y" * int(len(src_bytes) * 0.95))
        return True

    monkeypatch.setattr(bc, "compress_with_handbrake", fake_shrink_reject)
    monkeypatch.setattr(bc, "compute_vmaf", lambda *_a, **_k:
                         (_ for _ in ()).throw(AssertionError("vmaf must not run when not a clear size win")))

    result = bc.compress_video(str(src), str(tmp_path), lambda *_a, **_k: None,
                                5, None, adv, lambda: False)
    assert result["passthrough"] is True
    assert result["compressed_size"] == len(src_bytes)

    # --- adopt: shrink candidate is a clear, transparent win ---
    def fake_shrink_adopt(*, input_path, output_path, **_kw):
        with open(output_path, "wb") as f:
            f.write(b"y" * int(len(src_bytes) * 0.5))
        return True

    monkeypatch.setattr(bc, "compress_with_handbrake", fake_shrink_adopt)
    monkeypatch.setattr(bc, "compute_vmaf", lambda *_a, **_k: {"vmaf": 99.0})

    src2 = tmp_path / "in2.mp4"
    src2.write_bytes(src_bytes)
    monkeypatch.setattr(bc, "_remux_smart", fake_remux)  # unchanged; ignores _src, writes src_bytes

    result2 = bc.compress_video(str(src2), str(tmp_path), lambda *_a, **_k: None,
                                 5, None, adv, lambda: False)
    assert result2["passthrough"] is False
    assert result2["compressed_size"] == int(len(src_bytes) * 0.5)


def test_video_shrink_race_attributes_actual_encoder(monkeypatch, tmp_path):
    """When the source-as-candidate shrink race wins, the ledger/rate-model
    learning call must attribute the outcome to the encoder that ACTUALLY ran
    (source_candidate_encoder, default x265), never the originally requested
    encoder -- the same poisoned-cache bug class the main path already fixed,
    regressed in this parallel passthrough code path."""
    import BitCrusherV9 as bc

    src = tmp_path / "in.mp4"
    src_bytes = b"x" * 3_000_000
    src.write_bytes(src_bytes)

    def fake_remux(_src, dst, _privacy=None):
        with open(dst, "wb") as f:
            f.write(src_bytes)
        return True

    def fake_shrink_adopt(*, input_path, output_path, **_kw):
        with open(output_path, "wb") as f:
            f.write(b"y" * int(len(src_bytes) * 0.5))
        return True

    _stub_video_backend(bc, monkeypatch)
    monkeypatch.setattr(bc, "_remux_smart", fake_remux)
    monkeypatch.setattr(bc, "extract_video_duration", lambda _p: 5.0)
    monkeypatch.setattr(bc, "compress_with_handbrake", fake_shrink_adopt)
    monkeypatch.setattr(bc, "compute_vmaf", lambda *_a, **_k: {"vmaf": 99.0})

    captured = {}
    monkeypatch.setattr(bc, "learn_from_result",
                         lambda _dir, encoder, *_a, **_k: captured.setdefault("encoder", encoder))

    adv = {"scene_zones": False, "measure_quality": True, "quality_mode": "max",
           "encoder": "x264"}  # requested x264; shrink race defaults to x265
    result = bc.compress_video(str(src), str(tmp_path), lambda *_a, **_k: None,
                                5, None, adv, lambda: False)

    assert result["passthrough"] is False  # shrink race won
    assert captured["encoder"] == "x265"   # attributed to the encoder that ran, not "x264"


def test_video_tiny_target_infeasible_fails_fast(monkeypatch, tmp_path):
    """A target far below what the structural bitrate floor can reach for the
    given duration must raise before any encode attempt, not silently proceed
    to ship an oversized file (or worse, grind through the full retry/fallback
    chain for 30 minutes before giving up)."""
    import pytest
    import BitCrusherV9 as bc

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 5_000_000)  # large enough to skip the passthrough branch

    _stub_video_backend(bc, monkeypatch, duration=600.0)  # 10 min @ 1MB target

    def _must_not_run(*_a, **_k):
        raise AssertionError("encoder must not be invoked for an infeasible target")

    monkeypatch.setattr(bc, "compress_with_handbrake", _must_not_run)
    monkeypatch.setattr(bc, "_remux_smart", _must_not_run)

    with pytest.raises(RuntimeError, match=r"\[Budget\] Target .* is infeasible"):
        bc.compress_video(
            str(src), str(tmp_path), lambda *_a, **_k: None,
            target_size_mb=1, webhook_url=None,
            advanced_options={"scene_zones": False, "measure_quality": False,
                               "quality_mode": "max", "auto_codec": False},
            cancel_cb=lambda: False,
        )


def test_video_ceiling_exceeded_flag_set_after_retry_exhaustion(monkeypatch, tmp_path):
    """When a target is feasible in principle but every encode attempt
    (retries AND downscale steps) still lands over the ceiling, compress_video
    must surface stats["ceiling_exceeded"]=True instead of only a WARNING log
    that callers never check."""
    import BitCrusherV9 as bc

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 5_000_000)  # skip passthrough

    _stub_video_backend(bc, monkeypatch, duration=5.0, width=640, height=360)

    target_mb = 2
    oversized_bytes = int(target_mb * 1024 * 1024 * 1.5)  # always over ceiling

    def fake_handbrake(*, output_path, **_kw):
        with open(output_path, "wb") as f:
            f.write(b"z" * oversized_bytes)
        return True

    monkeypatch.setattr(bc, "compress_with_handbrake", fake_handbrake)
    monkeypatch.setattr(bc, "_remux_smart", lambda *_a, **_k: False)

    result = bc.compress_video(
        str(src), str(tmp_path), lambda *_a, **_k: None,
        target_size_mb=target_mb, webhook_url=None,
        advanced_options={"scene_zones": False, "measure_quality": False,
                           "quality_mode": "max", "auto_codec": False},
        cancel_cb=lambda: False,
    )

    assert result["ceiling_exceeded"] is True
    assert result["compressed_size"] > target_mb * 1024 * 1024


def test_video_manual_bitrate_override_wins(monkeypatch, tmp_path):
    """advanced_options['manual_bitrate'] (GUI manual-bitrate field / CLI
    --bitrate) must actually reach the encoder call, overriding the
    microprobe/planner/ledger-seed heuristic picks -- it was previously
    computed and then silently discarded, so --bitrate had zero effect."""
    import BitCrusherV9 as bc

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 5_000_000)  # large enough to skip passthrough

    _stub_video_backend(bc, monkeypatch, duration=5.0, width=640, height=360)

    captured = {}
    target_bytes = 2 * 1024 * 1024  # smaller than source: skips the passthrough branch

    def fake_handbrake(*, output_path, bitrate=None, **_kw):
        if bitrate is not None:
            captured.setdefault("bitrate", bitrate)
        with open(output_path, "wb") as f:
            f.write(b"z" * int(target_bytes * 0.9))
        return True

    monkeypatch.setattr(bc, "compress_with_handbrake", fake_handbrake)
    monkeypatch.setattr(bc, "_remux_smart", lambda *_a, **_k: False)

    manual_bps = 300_000  # deliberately far below what heuristics would pick
    bc.compress_video(
        str(src), str(tmp_path), lambda *_a, **_k: None,
        target_size_mb=2, webhook_url=None,
        advanced_options={"scene_zones": False, "measure_quality": False,
                           "quality_mode": "max", "auto_codec": False,
                           "manual_bitrate": str(manual_bps)},
        cancel_cb=lambda: False,
    )

    assert captured["bitrate"] == manual_bps


def test_video_undecodable_source_fails_fast(monkeypatch, tmp_path):
    """An unreadable/undecodable source must raise immediately with a specific
    [Probe] error, never reach the primary + 5-fallback encode chain."""
    import pytest
    import BitCrusherV9 as bc

    src = tmp_path / "corrupt.mp4"
    src.write_bytes(b"not a real container")

    monkeypatch.setattr(bc, "_probe_media_cached", lambda _p: {})  # simulates ffprobe failure
    monkeypatch.setattr(bc, "_jsonl_log", lambda *_a, **_k: None)

    def _must_not_run(*_a, **_k):
        raise AssertionError("encoder must not be invoked for an undecodable source")

    monkeypatch.setattr(bc, "_remux_smart", _must_not_run)
    monkeypatch.setattr(bc, "compress_with_handbrake", _must_not_run)

    with pytest.raises(RuntimeError, match=r"\[Probe\] Source undecodable or unreadable"):
        bc.compress_video(str(src), str(tmp_path), lambda *_a, **_k: None,
                           2, None, {"scene_zones": False}, lambda: False)


def test_video_audio_only_with_video_extension_fails_fast(monkeypatch, tmp_path):
    """An audio file saved with a video extension (e.g. a voice memo exported
    as .mp4) has streams (audio), so the undecodable-source guard alone would
    pass it through -- but with no video stream, width/height never get
    repaired downstream and the encode would be attempted against degenerate
    0x0 geometry. Must fail fast here instead, before the encoder chain."""
    import pytest
    import BitCrusherV9 as bc

    src = tmp_path / "voice_memo.mp4"
    src.write_bytes(b"not a real container")

    monkeypatch.setattr(bc, "_probe_media_cached", lambda _p: {
        "streams": [{"codec_type": "audio", "codec_name": "aac"}]
    })
    monkeypatch.setattr(bc, "_jsonl_log", lambda *_a, **_k: None)

    def _must_not_run(*_a, **_k):
        raise AssertionError("encoder must not be invoked for a video-less source")

    monkeypatch.setattr(bc, "_remux_smart", _must_not_run)
    monkeypatch.setattr(bc, "compress_with_handbrake", _must_not_run)

    with pytest.raises(RuntimeError, match=r"\[Probe\] Source has no video stream"):
        bc.compress_video(str(src), str(tmp_path), lambda *_a, **_k: None,
                           2, None, {"scene_zones": False}, lambda: False)


def test_ai_advisor_quality_floor_bump_is_shadow_only(monkeypatch):
    """_MODEL is an unvalidated learner (post_encode_learn has no accuracy
    gate) -- its quality-floor prediction must be logged, never applied to
    the real encode bitrate."""
    work = ROOT / "tmp" / "test_regressions"
    work.mkdir(parents=True, exist_ok=True)
    src = work / "in_advisor_bump.mp4"
    src.write_bytes(b"x")
    os.environ["BC_CURRENT_INPUT"] = str(src)

    import smart_rate

    monkeypatch.setattr(smart_rate, "choose_bitrates", lambda *a, **k: (600_000, 128_000, 1.02))
    monkeypatch.setattr(smart_rate, "estimate_mux_overhead", lambda **k: 0)
    monkeypatch.setattr(adv, "extract_media_features", lambda _p: {
        "width": 1920, "height": 1080, "fps": 30.0,
        "spatial_complexity": 7.8, "entropy_p95": 7.6, "edge_p95": 6.2,
        "sparsity_mean": 0.08, "temporal_ssim_std": 0.05, "motion_mad": 0.04,
        "scene_rate": 0.15, "banding_risk": 0.20, "text_edge_density": 0.12,
        "graininess": 0.25, "blockiness": 2.0,
    })
    # Force a predicted quality far below any difficulty-derived floor
    # (quality_floor is clamped to >= 0.90, so 10.0/100 is below it regardless
    # of content features).
    monkeypatch.setattr(adv._MODEL, "predict", lambda _x: 10.0)
    monkeypatch.setattr(adv, "analyze_scenes", lambda *_a, **_k: {"zones_str": "", "gop": 60, "aq_strength": 1.0})

    # Small target so audio dominates the budget and the mux-overhead
    # correction (which independently overwrites v_bps) is skipped -- isolates
    # whether the quality-floor bump itself still mutates v_bps.
    v_bps, a_bps, ov = adv.choose_bitrates_advised(
        duration_s=60.0, target_bytes=500_000, encoder="x264", container="mp4",
    )
    assert v_bps == 600_000  # shadow-only: predicted bump must NOT steer real bitrate


def test_post_encode_learn_requires_measured_quality(monkeypatch, tmp_path):
    """Without a real measured quality score, post_encode_learn must skip
    training rather than fabricating a label from the same closed-form
    formula predict() uses as its analytical fallback (training a model to
    reproduce its own baseline is not learning)."""
    src = tmp_path / "learn_src.mp4"
    src.write_bytes(b"x")

    monkeypatch.setattr(adv, "_DATA_CSV", tmp_path / "samples.csv")

    def _must_not_fit(*_a, **_k):
        raise AssertionError("fit_incremental must not run without a real measured_quality")

    monkeypatch.setattr(adv._MODEL, "fit_incremental", _must_not_fit)

    adv.post_encode_learn(
        input_path=str(src), output_path=str(tmp_path / "out.mp4"),
        encoder="x264", target_bytes=1_000_000, actual_bytes=950_000,
        a_bps_used=128_000, v_bps_used=800_000,
    )
    assert not (tmp_path / "samples.csv").exists()


def test_plan_zone_export_survives_legacy_zones_field():
    """The PBAE zone exporter's output (zone_plan['export']) must survive even
    when inputs.scene also carries a legacy 'zones' key -- a prior bug
    reassigned zone_plan wholesale after the export was built, silently
    discarding the injected x264-params zones string despite the planner
    logging a success message."""
    import planner as pl

    scenes = [
        {"start": 0.0, "end": 3.0, "difficulty": 0.8},
        {"start": 3.0, "end": 6.0, "difficulty": 0.2},
    ]
    inputs = pl.PlanInputs(
        target_bytes=5_000_000,
        duration_s=6.0,
        encoder="x264",
        container="mp4",
        width=1280,
        height=720,
        fps=30.0,
        audio_bps_hint=128_000,
        scene={"scenes": scenes, "zones": [], "zones_str": ""},
    )
    out = pl.plan(inputs)
    assert out.zone_plan is not None
    assert "export" in out.zone_plan
    assert "pbae" in out.zone_plan


def test_smart_rate_cache_key_distinguishes_same_named_files(tmp_path):
    """Two different files that happen to share a filename (common across
    folders/re-exports) must not collide in the ABR cache and silently reuse
    a stale v_bps/width/fps record for unrelated content."""
    import smart_rate as sr

    d1 = tmp_path / "a"; d1.mkdir()
    d2 = tmp_path / "b"; d2.mkdir()
    f1 = d1 / "clip.mp4"; f1.write_bytes(b"aaaaaaaaaa")
    f2 = d2 / "clip.mp4"; f2.write_bytes(b"bbbbbbbbbbbbbbbbbbbb")

    k1 = sr._cache_key(str(f1), 10, "x264")
    k2 = sr._cache_key(str(f2), 10, "x264")
    assert k1 != k2
