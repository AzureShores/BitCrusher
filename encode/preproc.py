from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile

import encode.ffmpeg_exec as ffmpeg_exec
from encode.ffmpeg_exec import si, NO_WIN, _ffmpeg_has_filter
from encode.encoder_caps import best_av1_encoder
from encode.codec_race import _probe_ref_clip, _probe_codec_args, _probe_vmaf_fullrate

# Artifact/texture-aware preprocessing: deband/deblock/denoise let a starved
# encoder spend bits on structure instead of noise. Prefilters can also hurt
# (smearing real detail), so every candidate chain is A/B probed against the
# unfiltered encode at the real operating point and kept only if it measurably
# wins full-rate VMAF against the pristine source.


def _rmtree_quiet(path: str | None) -> None:
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# Feature-gate thresholds (measured scales: banding_risk 0..1, graininess 0..~1,
# blockiness 0..~24, spatial_complexity 0..~10; bpp = bits per pixel per frame).
_PREPROC_BAND_THR = 0.35        # banding_risk above this → deband candidate
_PREPROC_BLOCK_THR = 10.0       # blockiness above this → deblock candidate
_PREPROC_GRAIN_THR = 0.25       # graininess above this (when starved) → denoise
_PREPROC_ENTROPY_THR = 7.0      # entropy_p95 (0..8) above this = dense texture
_PREPROC_BPP_STARVED = 0.11     # below this bpp texture starts to crumble
_PREPROC_BPP_CRUSHED = 0.055    # below this even moderate texture is hopeless
_PREPROC_KEEP_MARGIN = 0.4      # VMAF points a chain must win by to be kept

_PREPROC_FILTERS = {
    "deband":        "deband=1thr=0.015:2thr=0.015:3thr=0.015:range=16:blur=1",
    "deblock":       "deblock=filter=strong:block=8",
    "denoise_light": "hqdn3d=1.5:1.2:3.0:2.5",
    "denoise_med":   "hqdn3d=3.0:2.0:6.0:4.5",
}


def _preproc_candidates(feats: dict, *, video_bps: int, width: int, height: int,
                        fps: float, allow_denoise: bool = True) -> list[dict]:
    """
    Decide which prefilters the measured content indicates, strongest first.
    Returns [{"name", "vf", "why", "severity"}]; empty list = nothing indicated.
    Pure decision logic — validation (the probe A/B) happens separately.
    """
    feats = feats or {}
    try:
        band = float(feats.get("banding_risk", 0.0) or 0.0)
        grain = float(feats.get("graininess", 0.0) or 0.0)
        block = float(feats.get("blockiness", 0.0) or 0.0)
        cx = float(feats.get("spatial_complexity", 0.0) or 0.0)
        entropy = float(feats.get("entropy_p95", 0.0) or 0.0)
    except Exception:
        return []
    try:
        bpp = float(video_bps) / max(1.0, float(width) * float(height) * max(1.0, float(fps)))
    except Exception:
        bpp = 1.0

    out: list[dict] = []
    if block >= _PREPROC_BLOCK_THR:
        out.append({"name": "deblock", "vf": _PREPROC_FILTERS["deblock"],
                    "why": f"blockiness {block:.1f}", "severity": block / _PREPROC_BLOCK_THR})
    if allow_denoise:
        starved = bpp < _PREPROC_BPP_STARVED
        crushed = bpp < _PREPROC_BPP_CRUSHED
        # Dense-texture starvation is signalled by ENTROPY, not the grain score:
        # structured texture (game surfaces, foliage, concrete) reads as near-max
        # entropy while graininess stays low — measured on the corpus, the
        # pathological game capture sat at entropy 7.24/8 with grain 0.04.
        texture_hard = (entropy >= _PREPROC_ENTROPY_THR) or (cx >= 6.0)
        if (grain >= _PREPROC_GRAIN_THR and starved) or (crushed and texture_hard):
            key = "denoise_med" if crushed else "denoise_light"
            out.append({"name": key, "vf": _PREPROC_FILTERS[key],
                        "why": f"grain {grain:.2f} / entropy {entropy:.1f} / cx {cx:.1f} at {bpp:.3f} bpp",
                        "severity": (_PREPROC_BPP_STARVED / max(1e-6, bpp))
                                    * max(grain, entropy / 8.0, cx / 10.0)})
    if band >= _PREPROC_BAND_THR:
        out.append({"name": "deband", "vf": _PREPROC_FILTERS["deband"],
                    "why": f"banding risk {band:.2f}", "severity": band / _PREPROC_BAND_THR})

    # Drop filters this ffmpeg build doesn't ship, order strongest-first, and fix
    # the chain order for combination: deblock (source artifact) -> denoise
    # (noise) -> deband (banding shows once noise is gone).
    out = [c for c in out if _ffmpeg_has_filter(c["vf"].split("=", 1)[0])]
    out.sort(key=lambda c: -float(c.get("severity") or 0.0))
    return out


def _preproc_chain(cands: list[dict]) -> str:
    """Combine candidates into one -vf fragment in the artifact-correct order."""
    order = {"deblock": 0, "denoise_light": 1, "denoise_med": 1, "deband": 2}
    return ",".join(c["vf"] for c in sorted(cands, key=lambda c: order.get(c["name"], 9)))


def _preproc_probe_variants(input_path: str, variants: dict, *, encoder: str,
                            video_bps: int, scale_width: int, duration_s: float,
                            status_cb=None, cancel_cb=None,
                            ref_cache: dict | None = None,
                            ref_cache_dir: str | None = None) -> dict | None:
    """
    A/B the prefilter variants against the unfiltered baseline at the job's real
    operating point. Mirrors the codec race: 1-2 representative segments, a
    lossless reference at delivery resolution (from the UNFILTERED source — the
    honest yardstick), each variant encoded at the same -b:v with the job's
    encoder family, scored with full-rate VMAF and bit-normalized (a variant
    that made the encoder undershoot gets credit, overshoot gets debited).

    Returns {"baseline": score, "<variant>": score, "_sizes": {...}} or None
    when probing isn't possible.
    """
    ff = ffmpeg_exec.FFMPEG
    if not ff or not _ffmpeg_has_filter("libvmaf"):
        return None
    dur = float(duration_s or 0.0)
    if dur < 3.0 or int(video_bps) <= 0 or not variants:
        return None

    e = (encoder or "x264").lower()
    tag = ("av1" if "av1" in e else "x265" if ("265" in e or "hevc" in e) else "x264")
    av1_enc = best_av1_encoder() if tag == "av1" else None
    if tag == "av1" and not av1_enc:
        tag = "x264"

    seg = max(3.0, min(6.0, 0.06 * dur))
    if dur >= 4.0 * seg:
        starts = [max(0.0, dur * 0.30 - seg / 2.0), max(0.0, dur * 0.65 - seg / 2.0)]
    else:
        starts = [max(0.0, dur * 0.5 - seg / 2.0)]

    _scale = (f"scale={int(scale_width)}:-2," if int(scale_width or 0) > 0 else "")
    names = ["baseline"] + list(variants.keys())
    scores: dict[str, list[float]] = {}
    sizes: dict[str, int] = {}

    work = tempfile.mkdtemp(prefix="bc_preproc_")
    try:
        for si_n, t0 in enumerate(starts):
            if callable(cancel_cb) and cancel_cb():
                return None
            ref_clip = _probe_ref_clip(ff, input_path, t0=t0, seg=seg,
                                       scale_width=int(scale_width or 0), own_dir=work,
                                       ref_cache=ref_cache, ref_cache_dir=ref_cache_dir)
            if not ref_clip:
                continue
            for name in names:
                if callable(cancel_cb) and cancel_cb():
                    return None
                vf_pre = variants.get(name, "")
                vf_full = (_scale + vf_pre).rstrip(",") if (name != "baseline") else _scale.rstrip(",")
                enc_name, cargs, suffix = _probe_codec_args(tag, av1_enc, int(video_bps))
                clip = os.path.join(work, f"v_{si_n}_{name}{suffix}")
                cmd = ([ff, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{t0:.3f}", "-t", f"{seg:.3f}",
                        "-i", os.path.abspath(input_path), "-an"]
                       + (["-vf", vf_full] if vf_full else [])
                       + ["-pix_fmt", "yuv420p"] + cargs
                       + ["-b:v", str(int(video_bps)), clip])
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   startupinfo=si, creationflags=NO_WIN)
                if r.returncode != 0 or not os.path.exists(clip) or os.path.getsize(clip) == 0:
                    continue
                vm = _probe_vmaf_fullrate(ff, ref_clip, clip)
                if vm is not None:
                    scores.setdefault(name, []).append(float(vm))
                    sizes[name] = sizes.get(name, 0) + os.path.getsize(clip)

        if "baseline" not in scores or not scores["baseline"]:
            return None
        _VMAF_PER_DOUBLING = 4.0
        out: dict = {"_sizes": dict(sizes)}
        for name, v in scores.items():
            raw_v = sum(v) / len(v)
            tgt_bytes = max(1.0, float(video_bps) * float(seg) * float(len(v)) / 8.0)
            act_bytes = max(1.0, float(sizes.get(name, 0)))
            adj = _VMAF_PER_DOUBLING * math.log2(tgt_bytes / act_bytes)
            out[name] = raw_v + max(-8.0, min(8.0, adj))
        return out
    except Exception:
        return None
    finally:
        _rmtree_quiet(work)


def decide_preprocessing(input_path: str, feats: dict, *, encoder: str,
                         video_bps: int, scale_width: int, height: int, width: int,
                         fps: float, duration_s: float, advanced_options: dict | None = None,
                         status_cb=None, cancel_cb=None,
                         ref_cache: dict | None = None,
                         ref_cache_dir: str | None = None,
                         default_grain_filter: bool = True) -> tuple[str | None, dict]:
    """
    Full artifact-aware preprocessing decision:
      1. gate candidate filters on measured content features + bit starvation;
      2. probe-validate the combined chain against the unfiltered baseline;
      3. if the combo fails, try the single strongest candidate;
      4. ship a chain only when it beats baseline by _PREPROC_KEEP_MARGIN.
    Returns (vf_chain_or_None, decision_info_for_logging).

    default_grain_filter mirrors BitCrusherV9.ADVANCED_DEFAULTS["grain_filter"] —
    kept as a literal-default parameter rather than importing the monolith's
    settings dict, so this module stays free of a back-import onto BitCrusherV9.
    """
    adv = advanced_options or {}
    info: dict = {"candidates": [], "kept": None, "scores": None}
    allow_denoise = bool(adv.get("grain_filter", default_grain_filter))
    cands = _preproc_candidates(feats, video_bps=int(video_bps), width=int(width or 0),
                                height=int(height or 0), fps=float(fps or 30.0),
                                allow_denoise=allow_denoise)
    info["candidates"] = [{k: c[k] for k in ("name", "why")} for c in cands]
    if not cands:
        return None, info

    if callable(status_cb):
        status_cb("[Preproc] Indicated: "
                  + "; ".join(f"{c['name']} ({c['why']})" for c in cands)
                  + " — validating on probe segments...", "INFO")

    # Variant set: the combined chain, the single strongest candidate, and — when
    # a denoise is in play — the other denoise strength too (structured texture
    # is sensitive to strength; let the probe pick instead of guessing).
    variants: dict[str, str] = {}
    if len(cands) > 1:
        variants["+".join(c["name"] for c in cands)] = _preproc_chain(cands)
    variants[cands[0]["name"]] = cands[0]["vf"]
    for c in cands:
        if c["name"].startswith("denoise"):
            alt = "denoise_light" if c["name"] == "denoise_med" else "denoise_med"
            variants.setdefault(alt, _PREPROC_FILTERS[alt])
            break

    scores = _preproc_probe_variants(
        input_path, variants, encoder=encoder, video_bps=int(video_bps),
        scale_width=int(scale_width or 0), duration_s=float(duration_s or 0.0),
        status_cb=status_cb, cancel_cb=cancel_cb,
        ref_cache=ref_cache, ref_cache_dir=ref_cache_dir)
    info["scores"] = scores
    if not scores:
        if callable(status_cb):
            status_cb("[Preproc] Probe validation unavailable; encoding source as-is.", "INFO")
        return None, info

    base = float(scores.get("baseline") or 0.0)
    best_name, best_score = None, None
    for name in variants:
        v = scores.get(name)
        if v is not None and (best_score is None or float(v) > best_score):
            best_name, best_score = name, float(v)

    if best_name is not None and best_score >= base + _PREPROC_KEEP_MARGIN:
        info["kept"] = best_name
        if callable(status_cb):
            status_cb(f"[Preproc] {best_name} kept: probe VMAF {base:.1f} -> "
                      f"{best_score:.1f} (+{best_score - base:.1f}) at the same bitrate.", "INFO")
        return variants[best_name], info

    _gain = ((best_score - base) if best_score is not None else 0.0)
    if callable(status_cb):
        status_cb(f"[Preproc] No measurable gain (best {_gain:+.1f} VMAF vs baseline); "
                  f"encoding source as-is.", "INFO")
    return None, info
