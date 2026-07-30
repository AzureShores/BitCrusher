from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile

import encode.ffmpeg_exec as ffmpeg_exec
from encode.ffmpeg_exec import si, NO_WIN, _ffmpeg_has_filter
from encode.encoder_caps import best_av1_encoder
import encode.media_probe as media_probe
from encode.media_probe import _probe_video_stream
from encode.quality_metrics import _vmaf_model_opt

# Codec-race VMAF probing: short representative-segment shootouts used to
# pick a winning encoder and to validate preprocessing filter chains
# (preproc.py reuses these same probe primitives).


def _rmtree_quiet(path: str | None) -> None:
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# Grain probe: denoise must save at least this fraction of bytes for the source
# to count as "grainy enough" to synthesize. Measured discrimination on probes:
# clean content lands ~0.97, real grain ~0.60-0.80.
_FILM_GRAIN_RATIO_THR = 0.90


def _probe_film_grain(input_path: str, *, scale_width: int, duration_s: float,
                      cancel_cb=None, status_cb=None) -> dict | None:
    """
    Decide whether to synthesize film grain, by MEASUREMENT rather than the
    graininess feature (which doesn't track real grain — the synthetic-grain
    torture clip scored lower than clean footage). Encode short probe segments
    with SVT-AV1 twice at the same CRF — with vs without film-grain-denoise —
    and compare size: grainy content shrinks a lot when the grain is stripped,
    clean content barely moves. Returns {"level": int, "size_ratio": float}
    when grain is worth re-synthesizing (denoise saved >10%), else None. Grain
    is AV1-only (SVT), so this no-ops on other encoders.
    """
    ff = ffmpeg_exec.FFMPEG
    if not ff or best_av1_encoder() != "libsvtav1":
        return None
    dur = float(duration_s or 0.0)
    if dur < 3.0:
        return None
    seg = max(3.0, min(6.0, 0.06 * dur))
    starts = ([max(0.0, dur * 0.35 - seg / 2.0), max(0.0, dur * 0.70 - seg / 2.0)]
              if dur >= 4.0 * seg else [max(0.0, dur * 0.5 - seg / 2.0)])
    _scale = (["-vf", f"scale={int(scale_width)}:-2"] if int(scale_width or 0) > 0 else [])
    work = tempfile.mkdtemp(prefix="bc_grain_")
    tot_plain = tot_den = 0
    try:
        for i, t0 in enumerate(starts):
            if callable(cancel_cb) and cancel_cb():
                return None
            for tag, params in (("p", "tune=0:film-grain=8"),
                                ("d", "tune=0:film-grain=8:film-grain-denoise=1")):
                out = os.path.join(work, f"g{i}{tag}.mp4")
                cmd = ([ff, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{t0:.3f}", "-t", f"{seg:.3f}",
                        "-i", os.path.abspath(input_path), "-an"] + _scale
                       + ["-c:v", "libsvtav1", "-preset", "8", "-crf", "32",
                          "-svtav1-params", params, "-pix_fmt", "yuv420p10le", out])
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   startupinfo=si, creationflags=NO_WIN)
                if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
                    return None
                sz = os.path.getsize(out)
                if tag == "p":
                    tot_plain += sz
                else:
                    tot_den += sz
        if tot_plain <= 0:
            return None
        ratio = tot_den / tot_plain
        if ratio >= _FILM_GRAIN_RATIO_THR:
            return None
        # More savings = more grain = stronger re-synthesis. ratio 0.88 -> ~14,
        # 0.77 -> ~28, <=0.67 -> capped at 40.
        level = int(min(40, max(8, round((1.0 - ratio) * 120))))
        return {"level": level, "size_ratio": round(ratio, 3)}
    except Exception:
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


# Production parameter strings, mirrored into the probe so the race measures
# the encoder each candidate will ACTUALLY be driven with (see _probe_codec_args).
_PROBE_X264_PARAMS = ("aq-mode=3:aq-strength=1.00:mbtree=1:deblock=-1,-1:psy-rd=1.10,0.15"
                      ":rc-lookahead=80:qcomp=0.70:ipratio=1.30:pbratio=1.20:trellis=2:bframes=8:ref=5")
_PROBE_X265_PARAMS = "log-level=none:rc-lookahead=80:bframes=8:ref=5"
# 2-pass-safe SVT params (enable-overlays is rejected in multi-pass mode, and
# the production encode for a size-targeted job is two-pass).
_PROBE_SVT_PARAMS = "tune=0:scd=1"

_NULL_DEV = "NUL" if os.name == "nt" else "/dev/null"


def _needs_two_pass_probe(cargs: list[str]) -> bool:
    """SVT-AV1 is the only probe encoder that can't be held to -b:v in one pass."""
    return "libsvtav1" in cargs


def _probe_codec_args(codec_tag: str, av1_enc: str | None, video_bps: int, *,
                      preset: str | None = None, quality_mode: str = "",
                      duration_s: float = 0.0):
    """
    Probe-encode args (codec, ffmpeg_args, suffix) for codec ranking.

    Fairness matters more than speed here, in two directions:

    Bit fairness — x264/x265 single-pass ABR on a short clip can blow 30-40%
    past the budget (measured: x264 used 38% more bits than SVT-AV1 at the same
    -b:v and "won" the race with them), so the x26x probes get a maxrate/bufsize
    cap.

    Settings fairness — the probe must run each candidate at the settings the
    real encode will use. It used to pit SVT-AV1 at a hardcoded preset 6 with
    bare `tune=0` and 8-bit against x264 `medium`, while production ran SVT at
    preset <=4 with scd/film-grain in 10-bit: the probe measured an encoder that
    never ships, and x264 won races it would have lost on the full encode.
    Presets, params and bit depth now come from the same sources the production
    command builder uses.

    AV1 probes go into .mp4 — the .mkv container produced non-monotonic
    timestamps that broke VMAF frame alignment.
    """
    t = (codec_tag or "").lower()
    bps = int(video_bps)
    cap = ["-maxrate", str(int(bps * 1.10)), "-bufsize", str(int(bps * 2))]
    _preset_name = str(preset or "medium").strip().lower()
    if t in ("av1", "svt-av1", "aom-av1") and av1_enc:
        if av1_enc == "libsvtav1":
            _p = media_probe._svt_preset_num(_preset_name)
            if str(quality_mode or "").strip().lower() == "max":
                _p = str(min(int(_p), 4))
            _p = str(media_probe._svt_preset_for_duration(_p, duration_s))
            # Film-grain synthesis is intentionally absent — see
            # choose_best_codec_by_vmaf's docstring.
            return av1_enc, ["-c:v", "libsvtav1", "-preset", _p,
                             "-svtav1-params",
                             media_probe.svt_params_with_quality(_PROBE_SVT_PARAMS),
                             "-pix_fmt", "yuv420p10le"], ".mp4"
        if av1_enc == "libaom-av1":
            _cpu = media_probe._AOM_CPU_USED_MAP.get(_preset_name, "6")
            # Iterative size-targeting re-encodes the whole file many times, so
            # production floors libaom at cpu-used 6 in bitrate mode; match it.
            _cpu = str(max(int(_cpu), 6))
            return av1_enc, ["-c:v", "libaom-av1", "-cpu-used", _cpu,
                             "-row-mt", "1", "-tile-columns", "1"], ".mp4"
        return av1_enc, ["-c:v", av1_enc, "-preset", "p5"], ".mp4"
    # x26x reject SVT's numeric presets; fall back if an AV1 preset leaked in.
    if _preset_name.isdigit():
        _preset_name = "medium"
    if t in ("x265", "hevc", "libx265"):
        return "libx265", ["-c:v", "libx265", "-preset", _preset_name,
                           "-x265-params", _PROBE_X265_PARAMS] + cap, ".mp4"
    return "libx264", ["-c:v", "libx264", "-preset", _preset_name,
                       "-x264-params", _PROBE_X264_PARAMS] + cap, ".mp4"


def _probe_vmaf_fullrate(ff: str, ref_path: str, dist_path: str) -> float | None:
    """
    Full-rate VMAF for short probe clips. The sampled/decimated path in
    compute_vmaf mis-aligned frames on AV1 probe clips (SVT-AV1 measured 33
    decimated vs 70.8 full-rate on the same file), which made the codec race
    systematically pick the worse codec. Probe segments are only a few seconds
    long, so scoring every frame is affordable and exact.
    """
    try:
        _vst = _probe_video_stream(ref_path)
        rw, rh = int(_vst.get("width") or 0), int(_vst.get("height") or 0)
    except Exception:
        return None
    if rw <= 0 or rh <= 0:
        return None
    n_threads = max(1, min(16, (os.cpu_count() or 4)))
    work_dir = tempfile.mkdtemp(prefix="bc_vmafp_")
    log_name = "vmaf.json"
    lavfi = (
        f"[0:v]scale={rw}:{rh}:flags=bicubic,setpts=PTS-STARTPTS,format=yuv420p[dist];"
        f"[1:v]setpts=PTS-STARTPTS,format=yuv420p[ref];"
        f"[dist][ref]libvmaf={_vmaf_model_opt(ff, work_dir)}n_threads={n_threads}:log_fmt=json:log_path={log_name}"
    )
    _null_dev = "NUL" if os.name == "nt" else "/dev/null"
    cmd = [ff, "-hide_banner", "-loglevel", "error",
           "-i", os.path.abspath(dist_path), "-i", os.path.abspath(ref_path),
           "-lavfi", lavfi, "-an", "-sn", "-f", "null", _null_dev]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             cwd=work_dir, startupinfo=si, creationflags=NO_WIN)
        if getattr(res, "returncode", 1) != 0:
            return None
        with open(os.path.join(work_dir, log_name), "r", encoding="utf-8") as f:
            data = json.load(f)
        pooled = (data.get("pooled_metrics") or {}).get("vmaf") or {}
        if "mean" in pooled:
            return float(pooled["mean"])
        frames = data.get("frames") or []
        vals = [float((fr.get("metrics") or {}).get("vmaf")) for fr in frames
                if (fr.get("metrics") or {}).get("vmaf") is not None]
        return (sum(vals) / len(vals)) if vals else None
    except Exception:
        return None
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


def _probe_ref_clip(ff, input_path, *, t0, seg, scale_width, own_dir,
                    ref_cache=None, ref_cache_dir=None):
    """
    Lossless reference clip for a probe segment at delivery resolution. The codec
    race and the preproc A/B both need the SAME reference — it depends only on
    source + segment + scale (codec-independent) — so a shared (ref_cache,
    ref_cache_dir) lets the two stages extract each segment once per job instead
    of twice. Returns a clip path, or None on failure.
    """
    key = (os.path.abspath(input_path), int(scale_width or 0),
           round(float(t0), 3), round(float(seg), 3))
    if isinstance(ref_cache, dict):
        hit = ref_cache.get(key)
        if hit and os.path.exists(hit) and os.path.getsize(hit) > 0:
            return hit
    dest_dir = ref_cache_dir if (ref_cache_dir and os.path.isdir(ref_cache_dir)) else own_dir
    name = (f"ref_{int(scale_width or 0)}_{int(round(float(t0) * 1000))}"
            f"_{int(round(float(seg) * 1000))}.mp4")
    ref_clip = os.path.join(dest_dir, name)
    if not (os.path.exists(ref_clip) and os.path.getsize(ref_clip) > 0):
        _scale_vf = (["-vf", f"scale={int(scale_width)}:-2"] if int(scale_width or 0) > 0 else [])
        rc = subprocess.run(
            [ff, "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{float(t0):.3f}", "-t", f"{float(seg):.3f}",
             "-i", os.path.abspath(input_path), "-an"] + _scale_vf +
            ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", ref_clip],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=si, creationflags=NO_WIN)
        if rc.returncode != 0 or not os.path.exists(ref_clip):
            return None
    if isinstance(ref_cache, dict):
        ref_cache[key] = ref_clip
    return ref_clip


def choose_best_codec_by_vmaf(input_path: str, *, duration_s: float, video_bps: int,
                              candidates: list[str], scale_width: int = 0,
                              incumbent: str | None = None, switch_margin: float = 0.75,
                              status_cb=None, result_sink: dict | None = None,
                              ref_cache: dict | None = None,
                              ref_cache_dir: str | None = None,
                              preset: str | None = None,
                              quality_mode: str = "") -> str | None:
    """
    Probe short representative clips with each candidate codec at the SAME video
    bitrate and return the codec tag with the highest VMAF (best quality for the
    bit budget). Returns None if probing isn't possible (caller keeps its choice).

    The race is run at the DELIVERY resolution (scale_width), not the source's:
    a 4K source that will ship at 2560w must be judged at 2560w, otherwise the
    probe measures a completely different operating point. Two segments are
    scored (early + late) so a single unlucky scene can't decide the winner, and
    an incumbent is only dethroned when the challenger wins by switch_margin —
    re-running the same job should not flip-flop between codecs on noise.

    preset/quality_mode are the settings the real encode will run with; they are
    threaded into _probe_codec_args so the race compares shipping configurations
    rather than an arbitrary probe-only one. Film-grain synthesis is excluded on
    purpose — it measures LOWER on VMAF than the grain it replaces, so mirroring
    it here would cost AV1 races it deserves to win.
    """
    ff = ffmpeg_exec.FFMPEG
    if not ff or not _ffmpeg_has_filter("libvmaf"):
        return None
    dur = float(duration_s or 0.0)
    if dur <= 1.0 or int(video_bps) <= 0:
        return None

    av1_enc = best_av1_encoder()
    cand = [c for c in candidates if not (("av1" in c.lower()) and not av1_enc)]
    cand = list(dict.fromkeys(cand))  # de-dup, preserve order
    if len(cand) < 2:
        return None

    seg = max(3.0, min(6.0, 0.06 * dur))
    if dur >= 4.0 * seg:
        starts = [max(0.0, dur * 0.30 - seg / 2.0), max(0.0, dur * 0.65 - seg / 2.0)]
    else:
        starts = [max(0.0, dur * 0.5 - seg / 2.0)]

    _scale_vf = []
    if int(scale_width or 0) > 0:
        _scale_vf = ["-vf", f"scale={int(scale_width)}:-2"]

    work = tempfile.mkdtemp(prefix="bc_codecpick_")
    try:
        scores: dict[str, list[float]] = {}
        sizes: dict[str, int] = {}
        for si_n, t0 in enumerate(starts):
            # Lossless reference at the delivery resolution for this segment
            # (shared with the preproc A/B when the operating point matches).
            ref_clip = _probe_ref_clip(ff, input_path, t0=t0, seg=seg,
                                       scale_width=int(scale_width or 0), own_dir=work,
                                       ref_cache=ref_cache, ref_cache_dir=ref_cache_dir)
            if not ref_clip:
                continue
            for tag in cand:
                enc_name, cargs, suffix = _probe_codec_args(
                    tag, av1_enc, int(video_bps), preset=preset,
                    quality_mode=quality_mode, duration_s=dur)
                clip = os.path.join(work, f"cand_{tag}_{si_n}{suffix}")
                head = [ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.3f}", "-t", f"{seg:.3f}",
                        "-i", os.path.abspath(input_path), "-an"] + _scale_vf + ["-pix_fmt", "yuv420p"] + \
                       cargs + ["-b:v", str(int(video_bps))]
                # SVT-AV1 single-pass VBR is wildly loose (measured 2x target on
                # probe segments), and the bit-normalizer below then docked AV1
                # ~4 VMAF for an overshoot that only exists in the probe —
                # production runs it two-pass and holds the budget. x26x hit the
                # target on one pass thanks to their maxrate cap, which SVT
                # rejects outside CRF mode, so only SVT needs the second pass.
                if _needs_two_pass_probe(cargs):
                    plog = os.path.join(work, f"p2p_{tag}_{si_n}")
                    r1 = subprocess.run(head + ["-pass", "1", "-passlogfile", plog,
                                                "-f", "null", _NULL_DEV],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        startupinfo=si, creationflags=NO_WIN)
                    if r1.returncode != 0:
                        continue
                    cmd = head + ["-pass", "2", "-passlogfile", plog, clip]
                else:
                    cmd = head + [clip]
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   startupinfo=si, creationflags=NO_WIN)
                if r.returncode != 0 or not os.path.exists(clip) or os.path.getsize(clip) == 0:
                    continue
                vm = _probe_vmaf_fullrate(ff, ref_clip, clip)
                if vm is not None:
                    scores.setdefault(tag, []).append(float(vm))
                    sizes[tag] = sizes.get(tag, 0) + os.path.getsize(clip)

        # Bit-normalized scoring: encoders don't hit -b:v exactly on short probe
        # segments (SVT-AV1 runs hot), so raw VMAF rewarded whoever overshot
        # hardest. Credit/penalise relative to target bytes instead.
        _VMAF_PER_DOUBLING = 4.0  # ~VMAF points gained per 2x bitrate in this range
        raw: dict[str, float] = {}
        results: dict[str, float] = {}
        for k, v in scores.items():
            if not v:
                continue
            raw_v = sum(v) / len(v)
            tgt_bytes = max(1.0, float(video_bps) * float(seg) * float(len(v)) / 8.0)
            act_bytes = max(1.0, float(sizes.get(k, 0)))
            adj = _VMAF_PER_DOUBLING * math.log2(tgt_bytes / act_bytes)
            adj = max(-8.0, min(8.0, adj))  # cap so one wild clip can't dominate
            raw[k] = raw_v
            results[k] = raw_v + adj
        if not results:
            return None
        winner = max(results.items(), key=lambda kv: kv[1])[0]
        # Every race is a labeled experiment; hand the full scoreboard to the
        # caller so the outcome ledger can learn from it (it used to be thrown
        # away after the pick).
        if isinstance(result_sink, dict):
            result_sink["scores"] = {k: round(v, 2) for k, v in results.items()}
            result_sink["raw"] = {k: round(v, 2) for k, v in raw.items()}
        inc = (incumbent or "").lower()
        if inc and inc in results and winner != inc:
            if results[winner] < results[inc] + float(switch_margin):
                winner = inc  # challenger didn't clear the margin; stay put
        if callable(status_cb):
            tbl = ", ".join(
                f"{k}={results[k]:.1f} (raw {raw[k]:.1f}, {sizes.get(k, 0) / 1e6:.2f}MB)"
                for k in sorted(results, key=lambda kk: -results[kk]))
            status_cb(f"[Codec] VMAF probe @ {int(video_bps)//1000}kbps, "
                      f"{int(scale_width) if scale_width else 'src'}w, {len(starts)} segment(s), "
                      f"preset {preset or 'medium'}"
                      + (f"/{quality_mode}" if quality_mode else "")
                      + f", bit-normalized -> {tbl}. Picking {winner}.", "INFO")
        return winner
    except Exception:
        return None
    finally:
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass
