from __future__ import annotations

# ---- Spotlight mode (keep the full video, boost the moment) ------------------
# For "I can't cut context but THIS range must look good": an x264/x265 rate-
# control zone boosts the marked range; the rest of the video pays for it under
# the same cap. Zones exist only for x264/x265, and x264 zones must be disjoint,
# so a Spotlight file pins its codec and takes over zoning (scene-zones off).

_SPOTLIGHT_BOOST = 1.5
_X264_BASE_PARAMS = ("aq-mode=3:aq-strength=1.00:mbtree=1:deblock=-1,-1:psy-rd=1.10,0.15:"
                     "rc-lookahead=80:qcomp=0.70:ipratio=1.30:pbratio=1.20:trellis=2:bframes=8:ref=5")
_X265_BASE_PARAMS = "rc-lookahead=80:bframes=8:ref=5"


def _spotlight_zone_params(existing: str, start_s: float, end_s: float,
                           fps_eff: float, duration_s: float, encoder: str) -> tuple[str, str]:
    """
    Build the (params_key, params_value) carrying the Spotlight zone for the
    given encoder. Any zones already present in `existing` are stripped (x264
    zones must be disjoint; Spotlight owns zoning for this file); when there are
    no existing params the encoder's standard base params are used so the boost
    doesn't silently discard the tuned defaults.
    """
    is265 = str(encoder or "").lower() in ("x265", "libx265", "hevc")
    key = "x265_params" if is265 else "x264_params"
    fps_eff = max(1.0, float(fps_eff or 30.0))
    dur = float(duration_s or end_s)
    a = max(0.0, min(float(start_s), dur))
    b = max(a + (1.0 / fps_eff), min(float(end_s), dur))
    fs = int(a * fps_eff)
    fe = max(fs + 1, int(b * fps_eff))
    zone = f"zones={fs},{fe},b={_SPOTLIGHT_BOOST:g}"
    base = str(existing or "").strip()
    if not base:
        base = _X265_BASE_PARAMS if is265 else _X264_BASE_PARAMS
    base = ":".join(p for p in base.split(":") if p and not p.startswith("zones="))
    return key, (base + ":" + zone if base else zone)
