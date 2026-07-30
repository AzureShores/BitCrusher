from __future__ import annotations

from typing import Dict, Any

E_VAL_PROFILE = "E_VAL_PROFILE"

_SUPPORTED = ("libx264", "x264", "h264", "libx265", "x265", "hevc",
              "libsvtav1", "svt-av1", "libaom-av1", "aom-av1", "av1")


def _clamp(x: float, lo: float, hi: float) -> float:
    return float(lo if x < lo else hi if x > hi else x)


# SVT-AV1 preset window per quality mode (lower = slower/better). The upper
# bound is the historical effective preset (6), so no content ever encodes
# FASTER than before; only hard content is allowed to go slower.
#
# Max mode's window is deliberately narrow. Measured against a hand-tuned
# SVT-AV1 preset-3 encode at matched size (3 clips, bit-normalized VMAF), the
# preset is monotone: preset 4 loses by 0.28, preset 3 draws, preset 2 WINS on
# every clip by 0.23 mean. There is no content for which a faster preset scores
# better - only cheaper - so adaptivity inside max mode would buy nothing but
# time, which is the one thing max mode has already opted out of.
# _svt_preset_for_duration still keeps preset 2 off anything but short clips.
_SVT_PRESET_RANGE = {
    "max": (2.0, 3.0),
    "balanced": (4.0, 6.0),
    "fast": (6.0, 10.0),
}
# Reachable span of the SVT preset formula below (diff/tight/bias all in 0..1).
_SVT_BASE_LO = 9.0 - 2.5   # hardest content, loosest budget, least conservative
_SVT_BASE_HI = 9.0 + 2.0 + 1.5  # easiest content, tightest budget, most conservative


def select_profile(content: dict, encoder: str, width: int, fps: float, budget_kbps: float,
                   conservative_bias: float, quality_mode: str = "balanced") -> Dict[str, Any]:
    """
    Deterministic content-aware encoder parameters.

    Returns a dict intended to be consumed by existing command-builder patterns.
    Keys are stable and values remain within conservative/safe bounds.
    """
    if width <= 0 or fps <= 0.0 or budget_kbps < 0.0:
        raise ValueError(f"{E_VAL_PROFILE}: invalid numeric inputs")

    enc = (encoder or "").strip().lower()
    if enc not in [e.lower() for e in _SUPPORTED] and not any(
            k in enc for k in ("x264", "264", "avc", "x265", "svtav1", "svt-av1", "aom", "av1", "hevc")):
        # Unknown encoder (vp9/vvc/hardware): return conservative common params
        # instead of raising — this used to ValueError on every x264 job and
        # silently disable the whole profile model.
        return {"keyint": int(_clamp(2.0 * fps, 48.0, 240.0))}

    diff = 0.0
    try:
        diff = float(content.get("difficulty", 0.0) or 0.0)
    except Exception:
        diff = 0.0
    diff = _clamp(diff, 0.0, 1.0)

    banding_risk = 0.0
    try:
        banding_risk = float(content.get("banding_risk", 0.0) or 0.0)
    except Exception:
        banding_risk = 0.0
    banding_risk = _clamp(banding_risk, 0.0, 1.0)

    grain_sensitive = bool(content.get("grain_sensitive", False))
    bias = _clamp(float(conservative_bias or 0.5), 0.0, 1.0)

    # Tight budget signal: lower kbps per pixel => more constrained.
    kbps = float(budget_kbps)
    px = float(max(1, int(width))) * float(max(1.0, fps))
    tight = _clamp((2500.0 / max(1.0, kbps)) * (px / (1920.0 * 30.0)), 0.0, 2.0)
    tight = _clamp(tight / 2.0, 0.0, 1.0)

    params: Dict[str, Any] = {}

    # Common: prefer longer GOP on higher fps but cap for seeking/robustness.
    keyint = int(_clamp(2.0 * fps, 48.0, 240.0))
    params["keyint"] = keyint

    if ("264" in enc or "avc" in enc) and "265" not in enc:
        # x264: falls apart at low bits/pixel sooner than x265/AV1, so lean on
        # slower presets and adaptive quantization when the budget is tight.
        preset = "medium"
        if tight > 0.60:
            preset = "slow"
        if tight > 0.85 and bias < 0.7:
            preset = "slower"

        aq_strength = _clamp(0.8 + 0.4 * diff, 0.8, 1.2)
        psy_rd = _clamp(1.0 + 0.2 * diff, 1.0, 1.2)
        if banding_risk > 0.5:
            psy_rd = _clamp(psy_rd * 0.9, 0.8, 1.2)
            aq_strength = _clamp(aq_strength * 1.05, 0.8, 1.3)

        params["preset"] = preset
        params["x264-params"] = (
            f"aq-mode=2:aq-strength={aq_strength:.2f}:psy-rd={psy_rd:.2f},0.00"
        )
        return params

    if "265" in enc or "x265" in enc or "hevc" in enc or "libx265" in enc:
        # Conservative x265 defaults.
        preset = "medium"
        if tight > 0.75:
            preset = "slow"
        if tight > 0.90 and bias < 0.7:
            preset = "slower"

        # Only use grain tune for grain-sensitive content; no tune is better than ssim for general content.
        tune = "grain" if grain_sensitive else None

        # Safe AQ/psy bounds (avoid artifacty extremes).
        aq_mode = 2
        aq_strength = _clamp(0.8 + 0.6 * diff, 0.8, 1.4)
        psy_rd = _clamp(1.2 + 0.8 * diff, 1.2, 2.0)
        psy_rdoq = _clamp(0.8 + 0.6 * diff, 0.8, 1.6)
        rdoq = 1

        # Reduce risky sharpening/over-texture at very low budgets.
        if tight > 0.85:
            psy_rd = _clamp(psy_rd, 1.2, 1.6)
            psy_rdoq = _clamp(psy_rdoq, 0.8, 1.2)
            aq_strength = _clamp(aq_strength, 0.8, 1.1)

        # High banding risk: soften psy sharpening and use stronger deblock to smooth gradients.
        deblock = "-1,-1"
        if banding_risk > 0.5:
            psy_rd = _clamp(psy_rd * 0.92, 1.2, 2.0)
            deblock = "-2,-2"

        params["preset"] = preset
        if tune is not None:
            params["tune"] = tune
        params["x265-params"] = (
            f"aq-mode={aq_mode}:aq-strength={aq_strength:.2f}:psy-rd={psy_rd:.2f}"
            f":psy-rdoq={psy_rdoq:.2f}:rdoq-level={rdoq}:deblock={deblock}"
        )
        return params

    # Hardware AV1 (nvenc/qsv/amf/vaapi) uses its own preset scale — neither the
    # SVT numbers nor libaom's cpu-used apply, so emit only the common params.
    if any(k in enc for k in ("nvenc", "qsv", "amf", "vaapi")):
        return params

    # The codec race and the CLI both hand this a bare "av1" tag; SVT-AV1 is the
    # AV1 encoder BitCrusher actually drives (libaom is a slow fallback, and is
    # matched explicitly below). Requiring "svt" in the name sent every bare-av1
    # job into the libaom branch, which returns no preset at all.
    if "av1" in enc and "aom" not in enc:
        # SVT-AV1: lower preset = better quality/slower; higher preset = faster/lower quality.
        # Tight budget → use faster preset (higher number) to keep encode tractable.
        # High difficulty → use slower preset (lower number) for better compression efficiency.
        base = 9.0 - 2.5 * diff + 2.0 * tight + 1.5 * bias
        _lo, _hi = _SVT_PRESET_RANGE.get(str(quality_mode or "").strip().lower(),
                                         _SVT_PRESET_RANGE["balanced"])
        if _hi >= 10.0:
            # Fast mode keeps the original speed-first mapping unchanged.
            preset_i = int(_clamp(base, _lo, _hi))
        else:
            # `base` only ever spans _SVT_BASE_LO.._SVT_BASE_HI, which sits
            # entirely above the quality windows — clamping it there pinned every
            # job to the fastest allowed preset and made difficulty irrelevant.
            # Normalize instead, so content difficulty picks a point INSIDE the
            # window. Truncating (not rounding) biases toward the slower preset.
            frac = _clamp((base - _SVT_BASE_LO) / (_SVT_BASE_HI - _SVT_BASE_LO), 0.0, 1.0)
            preset_i = int(_lo + frac * (_hi - _lo))
        params["preset"] = str(preset_i)
        # Keep film-grain tools conservative; don't enable heavy synthesis.
        params["svtav1-params"] = "tune=0"
        return params

    # AOM-AV1 fallback (libaom-av1)
    # cpu-used higher is faster/lower quality; keep moderate.
    cpu_used = int(_clamp(6.0 + 2.0 * bias + 1.5 * tight - 2.5 * diff, 4.0, 8.0))
    tile_cols = 1
    tile_rows = 0
    params["cpu-used"] = str(cpu_used)
    params["row-mt"] = "1"
    params["tile-columns"] = str(tile_cols)
    params["tile-rows"] = str(tile_rows)
    return params
