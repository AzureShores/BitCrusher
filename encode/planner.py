from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path


from encode.smart_rate import get_dynamic_overshoot
try:
    from bitcrusher.overhead import get_overhead_factor
except Exception:
    # Fallback for non-packaged layouts (encode/overhead.py).
    from encode.overhead import get_overhead_factor


# Error codes taxonomy
E_VAL_PLAN = "E_VAL_PLAN"
E_VAL_SCENES = "E_VAL_SCENES"
E_RUN_PBAE = "E_RUN_PBAE"
E_VAL_ZONES = "E_VAL_ZONES"
E_RUN_ZONES = "E_RUN_ZONES"

def _apply_confidence_margin(vbps: float, confidence: float, bounds: Optional[Dict[str, Any]]) -> float:
    """
    Deterministic overshoot-prevention margin:
    - If explicit upper error bound is present, shrink vbps so vbps*(1+upper_pct) <= vbps_target.
    - Otherwise apply confidence-based margin (lower confidence => larger reduction).
    """
    vbps = float(max(1.0, vbps))
    c = float(max(0.0, min(1.0, confidence)))
    # default margin: up to 12% at c=0, down to 2% at c=1
    margin = 0.02 + (1.0 - c) * 0.10

    if bounds and isinstance(bounds, dict):
        up = bounds.get("upper_pct")
        try:
            upf = float(up)
            if upf > 0.0 and upf < 5.0:
                # Ensure worst-case does not exceed target.
                margin = max(margin, min(0.30, upf))
        except Exception:
            pass

    return float(vbps) * (1.0 - float(margin))


def _smoothstep01(x: float) -> float:
    x = float(max(0.0, min(1.0, x)))
    return x * x * (3.0 - 2.0 * x)


def _clamp_float(v: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, float(v))))


def _zones_strip_key(param_str: str, key: str) -> str:
    """
    Remove key=... from a colon-separated x26x param string deterministically.
    Example: "aq-mode=3:zones=...:psy-rd=2.0" -> "aq-mode=3:psy-rd=2.0"
    """
    s = str(param_str or "").strip()
    if not s:
        return ""
    parts = [p for p in s.split(":") if p.strip()]
    out = []
    k = str(key or "").strip()
    for p in parts:
        if p.strip().startswith(k):
            continue
        out.append(p.strip())
    return ":".join(out)


def _zones_append_key(param_str: str, key: str, value: str) -> str:
    base = _zones_strip_key(param_str, key)
    if base:
        return f"{base}:{key}{value}"
    return f"{key}{value}"


def _pbae_export_zones(
    *,
    encoder: str,
    fps_out: float,
    pbae: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Deterministic zone exporter (Option 2):
      - x264: zones=start,end,b=mult
      - x265: zones=start,end,bitrate-factor=mult

    Returns:
      {
        "enabled": bool,
        "format": "x264-zones"|"x265-zones"|"none",
        "args": [ ... ],             # e.g. ["-x265-params", "zones=..."]
        "zones_str": "0,10,.../..",  # inner zones string (no leading 'zones=')
        "zones": [ {f0,f1,mult,start_s,end_s}, ... ],
        "zones_count": int,
        "mult_min": float,
        "mult_max": float,
        "min_zone_s": float,
        "merge_applied": bool,
        "reason": str,
      }
    """
    enc = (encoder or "").lower()
    try:
        fps = float(fps_out)
    except Exception:
        fps = 0.0

    out: Dict[str, Any] = {
        "enabled": False,
        "format": "none",
        "args": [],
        "zones_str": "",
        "zones": [],
        "zones_count": 0,
        "mult_min": 1.0,
        "mult_max": 1.0,
        "min_zone_s": 2.0,
        "merge_applied": False,
        "reason": "",
    }

    if fps <= 0.0:
        out["reason"] = "invalid_fps"
        return out

    fmt = "none"
    if enc in ("x264", "libx264"):
        fmt = "x264-zones"
        m_lo, m_hi, max_z = 0.85, 1.20, 80
    elif enc in ("x265", "libx265"):
        fmt = "x265-zones"
        m_lo, m_hi, max_z = 0.86, 1.18, 60
    else:
        out["reason"] = "unsupported_encoder"
        return out

    rows = []
    try:
        rows = list((pbae or {}).get("scene_alloc") or [])
    except Exception:
        rows = []

    if len(rows) < 2:
        out["reason"] = "insufficient_scenes"
        return out

    # Compute duration-weighted mean weight
    total_dur = 0.0
    w_sum = 0.0
    for r in rows:
        try:
            dur = float(r.get("dur_s") or (float(r.get("end_s")) - float(r.get("start_s"))))
        except Exception:
            dur = 0.0
        dur = float(max(0.0, dur))
        total_dur += dur
        try:
            w = float(r.get("weight") or 1.0)
        except Exception:
            w = 1.0
        w_sum += dur * w

    if total_dur <= 0.01:
        out["reason"] = "invalid_duration"
        return out

    w_mean = float(max(0.01, w_sum / total_dur))

    # Build candidates (min duration + non-trivial multipliers)
    MIN_ZONE_S = float(out["min_zone_s"])
    SKIP_EPS = 0.03

    candidates = []
    for r in rows:
        try:
            s0 = float(r.get("start_s") or 0.0)
            s1 = float(r.get("end_s") or 0.0)
        except Exception:
            continue
        dur = float(max(0.0, s1 - s0))
        if dur <= 0.0:
            continue

        try:
            w = float(r.get("weight") or 1.0)
        except Exception:
            w = 1.0
        w_norm = float(w / w_mean)

        m = _clamp_float(w_norm, m_lo, m_hi)

        try:
            imp = float(r.get("importance") or 0.0)
        except Exception:
            imp = 0.0
        soft = _smoothstep01(imp)
        m = 1.0 + (m - 1.0) * soft
        m = _clamp_float(m, m_lo, m_hi)

        candidates.append(
            {
                "start_s": float(s0),
                "end_s": float(s1),
                "dur_s": float(dur),
                "mult": float(m),
                "importance": float(max(0.0, min(1.0, imp))),
            }
        )

    # Deterministic merge of short scenes into neighbors, then drop tiny deltas
    merge_applied = False

    # First, stable sort by start time
    candidates.sort(key=lambda z: (float(z["start_s"]), float(z["end_s"])))

    # Merge short zones (dur < MIN_ZONE_S) into a neighbor.
    # Deterministic rule: prefer previous; if both exist choose neighbor with closer mult; tie -> previous.
    i = 0
    while i < len(candidates):
        z = candidates[i]
        if float(z["dur_s"]) >= MIN_ZONE_S:
            i += 1
            continue
        if len(candidates) <= 1:
            break

        prev_i = i - 1 if i - 1 >= 0 else None
        next_i = i + 1 if i + 1 < len(candidates) else None

        if prev_i is None and next_i is None:
            break
        if prev_i is None:
            pick = next_i
        elif next_i is None:
            pick = prev_i
        else:
            dp = abs(math.log(max(0.01, float(candidates[prev_i]["mult"]))) - math.log(max(0.01, float(z["mult"]))))
            dn = abs(math.log(max(0.01, float(candidates[next_i]["mult"]))) - math.log(max(0.01, float(z["mult"]))))
            if dn < dp:
                pick = next_i
            else:
                pick = prev_i

        # Merge z into candidates[pick] using log-space duration-weighted average
        a = candidates[pick]
        dur_a = float(max(0.0, a["dur_s"]))
        dur_b = float(max(0.0, z["dur_s"]))
        m_a = float(max(0.01, a["mult"]))
        m_b = float(max(0.01, z["mult"]))
        dur_t = dur_a + dur_b
        if dur_t <= 0.0:
            # just drop z
            candidates.pop(i)
            merge_applied = True
            continue
        logm = (dur_a * math.log(m_a) + dur_b * math.log(m_b)) / dur_t
        m_new = float(math.exp(logm))
        m_new = _clamp_float(m_new, m_lo, m_hi)

        # Expand time span deterministically
        a["start_s"] = float(min(float(a["start_s"]), float(z["start_s"])))
        a["end_s"] = float(max(float(a["end_s"]), float(z["end_s"])))
        a["dur_s"] = float(max(0.0, float(a["end_s"]) - float(a["start_s"])))
        a["mult"] = float(m_new)
        a["importance"] = float(max(float(a["importance"]), float(z["importance"])))

        candidates.pop(i)
        merge_applied = True
        # restart from previous index to allow cascading merges
        i = max(0, (pick or 0) - 1)

    # Drop negligible multipliers and enforce min duration
    filtered = []
    for z in candidates:
        if float(z["dur_s"]) < MIN_ZONE_S:
            continue
        if abs(float(z["mult"]) - 1.0) < SKIP_EPS:
            continue
        filtered.append(z)

    # If too many zones, merge adjacent smallest deltas (log-space) deterministically.
    def _merge_pair(idx: int) -> None:
        nonlocal merge_applied, filtered
        a = filtered[idx]
        b = filtered[idx + 1]
        dur_a = float(max(0.0, a["dur_s"]))
        dur_b = float(max(0.0, b["dur_s"]))
        dur_t = dur_a + dur_b
        if dur_t <= 0.0:
            filtered.pop(idx + 1)
            merge_applied = True
            return
        m_a = float(max(0.01, a["mult"]))
        m_b = float(max(0.01, b["mult"]))
        logm = (dur_a * math.log(m_a) + dur_b * math.log(m_b)) / dur_t
        m_new = float(_clamp_float(math.exp(logm), m_lo, m_hi))
        a["start_s"] = float(min(float(a["start_s"]), float(b["start_s"])))
        a["end_s"] = float(max(float(a["end_s"]), float(b["end_s"])))
        a["dur_s"] = float(max(0.0, float(a["end_s"]) - float(a["start_s"])))
        a["mult"] = float(m_new)
        a["importance"] = float(max(float(a["importance"]), float(b["importance"])))
        filtered.pop(idx + 1)
        merge_applied = True

    while len(filtered) > int(max_z):
        # find smallest adjacent delta
        best_i = 0
        best_d = float("inf")
        for j in range(0, len(filtered) - 1):
            d = abs(math.log(max(0.01, float(filtered[j]["mult"]))) - math.log(max(0.01, float(filtered[j + 1]["mult"]))))
            if d < best_d:
                best_d = d
                best_i = j
        _merge_pair(best_i)

    if not filtered:
        out["reason"] = "no_effective_zones"
        out["format"] = fmt
        out["merge_applied"] = bool(merge_applied)
        return out

    # Convert to frame ranges and build zones string.
    zones = []
    for z in filtered:
        f0 = int(math.floor(float(z["start_s"]) * fps))
        f1 = int(math.floor(float(z["end_s"]) * fps)) - 1
        f0 = int(max(0, f0))
        f1 = int(max(f0, f1))
        zones.append(
            {
                "start_s": float(z["start_s"]),
                "end_s": float(z["end_s"]),
                "f0": int(f0),
                "f1": int(f1),
                "mult": float(z["mult"]),
            }
        )

    mults = [float(z["mult"]) for z in zones]
    mult_min = float(min(mults)) if mults else 1.0
    mult_max = float(max(mults)) if mults else 1.0

    # Sanity: avoid extreme pumping
    if mult_min > 0.0 and (mult_max / mult_min) > 1.35:
        out["format"] = fmt
        out["merge_applied"] = bool(merge_applied)
        out["reason"] = "mult_range_too_wide"
        return out

    parts = []
    for z in zones:
        if fmt == "x264-zones":
            parts.append(f"{int(z['f0'])},{int(z['f1'])},b={float(z['mult']):.3f}")
        else:
            parts.append(f"{int(z['f0'])},{int(z['f1'])},bitrate-factor={float(z['mult']):.3f}")

    zones_str = "/".join(parts)

    out.update(
        {
            "enabled": True,
            "format": fmt,
            "zones_str": str(zones_str),
            "zones": zones,
            "zones_count": int(len(zones)),
            "mult_min": float(mult_min),
            "mult_max": float(mult_max),
            "merge_applied": bool(merge_applied),
            "reason": "",
        }
    )

    # Export args are append-only; caller may merge into existing -x26x-params.
    if fmt == "x264-zones":
        out["args"] = ["-x264-params", f"zones={zones_str}"]
    elif fmt == "x265-zones":
        out["args"] = ["-x265-params", f"zones={zones_str}"]

    return out


@dataclass(frozen=True)
class PlanInputs:
    target_bytes: int
    duration_s: float
    encoder: str
    container: str
    width: int
    height: int
    fps: float
    audio_bps_hint: int
    probe: Optional[Dict[str, Any]] = None  # from probe_predictor
    scene: Optional[Dict[str, Any]] = None  # from analyze_scenes/analyze_and_advise
    stats: Optional[Dict[str, Any]] = None  # smart_rate stats dict (loaded)
    settings_dir: Optional[str] = None      # for overhead persistence


@dataclass(frozen=True)
class PlanOutputs:
    video_bps: int
    audio_bps: int
    encoder_params: Dict[str, Any]
    confidence: float
    predicted_bytes: int
    overhead_factor: float
    overshoot_factor: float
    zone_plan: Optional[Dict[str, Any]] = None
    # True when scene-based zones should be applied by the encoder
    zones_enabled: bool = False


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, int(v))))


def _audio_plan(target_bytes: int, duration_s: float, hint: int, aggressive: bool) -> int:
    base = _clamp_int(int(hint or 128_000), 48_000, 384_000)
    if duration_s <= 0:
        return base
    total_bits = int(target_bytes) * 8
    share = 0.06 if not aggressive else 0.035
    cap = int(max(48_000, min(384_000, (total_bits * share) / max(1.0, float(duration_s)))))
    return _clamp_int(min(base, cap) if aggressive else base, 48_000, 384_000)

def _utc_now_iso() -> str:
    # Time is allowed for logging; tests can freeze by monkeypatching this function.
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _log_event(
    *,
    severity: str,
    component: str,
    operation: str,
    error_code: str,
    message: str,
    context: Dict[str, Any],
    settings_dir: Optional[str] = None,
) -> None:
    """
    Structured logging contract:
    timestamp(ISO8601 UTC), severity, component, operation, error_code, message, context(object)
    Destinations:
      - <settings_dir>/logs/errors.jsonl (structured)
      - <settings_dir>/logs/bitcrusher.log (human-ish single-line)
    Falls back to a CWD-relative "logs" dir only when no settings_dir is given
    (e.g. direct unit-test calls) — GUI (fixed install dir) and CLI (arbitrary
    shell CWD) must not end up writing to two different places for the same
    logical log.
    """
    rec = {
        "timestamp": _utc_now_iso(),
        "severity": str(severity).upper(),
        "component": str(component),
        "operation": str(operation),
        "error_code": str(error_code),
        "message": str(message),
        "context": dict(context or {}),
    }
    try:
        log_dir = (Path(settings_dir) / "logs") if settings_dir else Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        # JSONL
        with (log_dir / "errors.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        # Plain log
        with (log_dir / "bitcrusher.log").open("a", encoding="utf-8") as f:
            f.write(f"{rec['timestamp']} {rec['severity']} {rec['component']}:{rec['operation']} {rec['error_code']} {rec['message']} {json.dumps(rec['context'], ensure_ascii=False, sort_keys=True)}\n")
    except Exception:
        # Logging must never crash planning.
        return


def _clamp01(x: float) -> float:
    try:
        xf = float(x)
    except Exception:
        xf = 0.0
    return float(max(0.0, min(1.0, xf)))


def _round6(x: float) -> float:
    # Determinism: quantize at stable precision
    return float(round(float(x), 6))


def _smoothstep01(x: float) -> float:
    # Deterministic polynomial smoothstep
    x = _clamp01(x)
    return float(x * x * (3.0 - 2.0 * x))


def _pbae_margin(enc: str, conf_probe: float, duration_s: float, w: int, fps: float) -> float:
    """
    Deterministic overshoot-prevention margin (fraction).
    Higher margin when confidence is low / content is short / high res-fps pressure.
    """
    e = (enc or "").lower()
    is_av1 = "av1" in e
    is_x265 = ("x265" in e) or ("265" in e) or ("hevc" in e)
    base = 0.06 if (is_av1 or is_x265) else 0.04

    # Short clips: probing fits are less stable → more margin
    short_bump = 0.02 if float(duration_s) < 45.0 else 0.0

    # Confidence: low conf → add up to +4%
    conf = _clamp01(conf_probe)
    conf_bump = (1.0 - conf) * 0.04

    # Resolution/fps pressure bucket: mild +0..2%
    px = max(1, int(w)) * max(1, int(1 if fps <= 0 else int(round(fps))))
    # 1080p60-ish ~ 1920*60=115k; scale loosely
    pressure = min(1.0, max(0.0, (px - 80_000) / 160_000))
    pressure_bump = pressure * 0.02

    m = base + short_bump + conf_bump + pressure_bump
    return float(max(0.02, min(0.14, _round6(m))))


def _pbae_allocate(
    *,
    scenes: list,
    budget_video_bps: float,
    duration_s: float,
    width: int,
    height: int,
    fps: float,
    content: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Planner-only PBAE (Option 1):
      - computes deterministic scene importance + weights
      - allocates per-scene target_bps conserving total budget
      - does NOT emit encoder zones (stored for later Option 2)
    Expects each scene dict to have start/end in seconds using keys:
      - start or start_s
      - end or end_s
    Optional per-scene:
      - difficulty (0..1)
      - edge_p95 / text_edge_density / graininess (from ml_heuristics)
      - weight (existing heuristic weight; used only as a weak signal)
    """
    if not isinstance(scenes, list) or len(scenes) < 2:
        raise ValueError(f"{E_VAL_SCENES}: need >=2 scenes")

    # Global signals
    try:
        diff_g = _clamp01(float(content.get("difficulty", 0.5)))
    except Exception:
        diff_g = 0.5
    grain_sensitive = bool(content.get("grain_sensitive", False))
    grain_g = 1.0 if grain_sensitive else 0.0

    # Tightness: bits-per-pixel-frame proxy (only for deterministic moderation; v1 keeps it mild)
    try:
        bppf = float(budget_video_bps) / max(1.0, float(max(1, width) * max(1, height) * max(1.0, fps)))
    except Exception:
        bppf = 0.0
    # Reference bppf: lower means tighter; clamp into 0..1 tightness measure
    bppf_ref = 0.075
    tight = _clamp01(bppf_ref / max(1e-9, float(bppf)))
    # Map tightness so very-tight→1, loose→0
    tight = _clamp01(min(1.0, tight / 2.0))

    rows: list[Dict[str, Any]] = []
    total_dur = 0.0

    for idx, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            continue
        try:
            s = float(sc.get("start_s", sc.get("start", 0.0)) or 0.0)
            e = float(sc.get("end_s", sc.get("end", 0.0)) or 0.0)
        except Exception:
            continue
        dur = max(0.01, e - s)
        total_dur += dur

        # scene difficulty
        try:
            diff_i = _clamp01(float(sc.get("difficulty", diff_g)))
        except Exception:
            diff_i = diff_g

        # edge/text proxies (deterministic fallbacks)
        # ml_heuristics provides edge_p95 ~[0..8+] and text_edge_density ~[0..0.2]
        try:
            edge_p95 = float(sc.get("edge_p95")) if sc.get("edge_p95") is not None else None
        except Exception:
            edge_p95 = None
        if edge_p95 is None:
            edges_i = 0.5
        else:
            edges_i = _clamp01(edge_p95 / 8.0)

        try:
            txt = float(sc.get("text_edge_density")) if sc.get("text_edge_density") is not None else 0.0
        except Exception:
            txt = 0.0
        text_i = _clamp01(txt * 3.0)

        # v1: no face detector; placeholder is deterministic 0.0
        face_i = 0.0

        # importance (quantized)
        mid_sensitive = 1.0 - abs(diff_i - 0.5) * 2.0
        mid_sensitive = _clamp01(mid_sensitive)
        importance_raw = 0.45 * face_i + 0.30 * text_i + 0.15 * edges_i + 0.10 * mid_sensitive
        importance = _clamp01(importance_raw + 0.08 * grain_g + 0.10 * (1.0 - tight) * face_i)
        importance = _round6(importance)

        # weights (bounded, quantized)
        curve = _smoothstep01(importance)
        weight = 1.0 + 0.55 * curve - 0.35 * (1.0 - curve)
        weight *= (1.0 + 0.12 * diff_i * importance)
        weight = float(max(0.75, min(1.40, weight)))
        weight = _round6(weight)

        rows.append(
            {
                "scene_id": int(idx),
                "start_s": float(s),
                "end_s": float(e),
                "dur_s": float(dur),
                "importance": float(importance),
                "weight": float(weight),
            }
        )

    if not rows or total_dur <= 0.0:
        raise ValueError(f"{E_VAL_SCENES}: empty/invalid scenes")

    # Normalize weights with duration to conserve budget
    S = sum(float(r["dur_s"]) * float(r["weight"]) for r in rows) or 1.0
    # average duration-weight multiplier
    norm = S / max(0.01, float(total_dur))

    # initial integer bps allocation
    for r in rows:
        bps = float(budget_video_bps) * (float(r["weight"]) / max(1e-9, float(norm)))
        r["target_bps"] = int(max(1, int(round(bps))))

    # deterministic correction to match total bits as closely as possible
    # desired total bits is budget_video_bps * total_dur
    desired_bits = float(budget_video_bps) * float(total_dur)
    have_bits = sum(float(r["target_bps"]) * float(r["dur_s"]) for r in rows)
    err = desired_bits - have_bits

    # distribute +/-1 bps steps deterministically
    # priority: higher importance, longer duration, stable scene_id
    rows_sorted = sorted(rows, key=lambda rr: (-float(rr["importance"]), -float(rr["dur_s"]), int(rr["scene_id"])))
    # compute max steps to avoid long loops on huge durations; err in bits, step changes bits by dur_s
    max_iters = 20000
    it = 0
    while abs(err) >= 0.5 and it < max_iters:
        it += 1
        progressed = False
        for r in rows_sorted:
            d = float(r["dur_s"])
            if d <= 0:
                continue
            # one bps step changes bits by d
            step_bits = d
            if err > 0.0:
                r["target_bps"] = int(r["target_bps"]) + 1
                err -= step_bits
                progressed = True
            elif err < 0.0 and int(r["target_bps"]) > 1:
                r["target_bps"] = int(r["target_bps"]) - 1
                err += step_bits
                progressed = True
            if abs(err) < 0.5:
                break
        if not progressed:
            break

    # summary stats
    wmin = min(float(r["weight"]) for r in rows)
    wmax = max(float(r["weight"]) for r in rows)
    prot = sum(float(r["dur_s"]) for r in rows if float(r["importance"]) >= 0.65) / max(0.01, total_dur)
    sac  = sum(float(r["dur_s"]) for r in rows if float(r["importance"]) <= 0.30) / max(0.01, total_dur)

    # re-check conservation (post-adjust)
    have_bits2 = sum(float(r["target_bps"]) * float(r["dur_s"]) for r in rows)
    budget_out = have_bits2 / max(0.01, float(total_dur))

    return {
        "scene_alloc": rows,
        "summary": {
            "budget_in_bps": int(round(float(budget_video_bps))),
            "budget_out_bps": int(round(float(budget_out))),
            "weight_min": float(_round6(wmin)),
            "weight_max": float(_round6(wmax)),
            "protected_fraction": float(_round6(prot)),
            "sacrificed_fraction": float(_round6(sac)),
            "scenes": int(len(rows)),
        },
    }


def _scene_complexity(scene: Optional[Dict[str, Any]]) -> float:
    if not isinstance(scene, dict):
        return 0.5
    try:
        d = float(scene.get("difficulty"))
    except Exception:
        d = 0.5
    return float(max(0.0, min(1.0, d)))


def plan(inputs: PlanInputs) -> PlanOutputs:
    if int(inputs.target_bytes) <= 0 or float(inputs.duration_s) <= 0:
        raise ValueError(f"{E_VAL_PLAN}: invalid target/duration")

    enc = (inputs.encoder or "x264").lower()
    cont = (inputs.container or "mp4").lower()
    w = int(inputs.width or 0)
    h = int(inputs.height or 0)
    fps = float(inputs.fps or 0.0)

    overhead = get_overhead_factor(inputs.settings_dir, cont, w, h, fps)
    stats = inputs.stats or {}
    scene = inputs.scene
    # Graduated per-content-class trust: prefer the narrower content-class
    # bucket (screen_ui/film_grain/sports_action/flat_camera/general) once it
    # has enough observations, else get_dynamic_overshoot falls back to the
    # coarse encoder/container/resolution/fps bucket unchanged. BC_CONTENT_CLASS
    # is set by ai_advisor.choose_bitrates_advised earlier in the same encode.
    _klass = os.environ.get("BC_CONTENT_CLASS") or None
    overshoot = float(get_dynamic_overshoot(stats, enc, cont, default=1.00, width=w, fps=fps, klass=_klass))
    overshoot = float(max(0.90, min(1.12, overshoot)))

    aggressive = int(inputs.target_bytes) < 25_000_000
    audio_bps = _audio_plan(int(inputs.target_bytes), float(inputs.duration_s), int(inputs.audio_bps_hint), aggressive)

    audio_bytes = (audio_bps * float(inputs.duration_s)) / 8.0
    core_budget = (float(inputs.target_bytes) / max(1.0, float(overhead))) - audio_bytes
    core_budget = max(1.0, core_budget)
    vid_budget_bps = (core_budget / float(inputs.duration_s)) * 8.0

    conf_probe = 0.0
    vbps0 = float(vid_budget_bps)
    crf0: Optional[float] = None
    if isinstance(inputs.probe, dict):
        try:
            vbps0 = float(inputs.probe.get("video_bps") or vbps0)
        except Exception:
            vbps0 = float(vbps0)
        try:
            conf_probe = float(inputs.probe.get("confidence") or 0.0)
        except Exception:
            conf_probe = 0.0
        try:
            crf0 = float(inputs.probe.get("crf")) if inputs.probe.get("crf") is not None else None
        except Exception:
            crf0 = None

    diff = _scene_complexity(inputs.scene)
    scene_bump = 1.0 + 0.06 * (diff - 0.5)  # +/-3%
    vbps = vbps0 * scene_bump

    # learned overshoot correction
    vbps = vbps / max(0.90, min(1.12, overshoot))

    vbps = max(140_000.0, float(vbps))
    # ------------------------------------------------------------
    # Option 1: Planner-only Perceptual Budget Allocation Engine
    # (Scene-split path is canonical; no encoder zones emitted yet)
    # ------------------------------------------------------------
    margin = _pbae_margin(enc, conf_probe, float(inputs.duration_s), w, fps)
    vbps_safe = float(vbps) * (1.0 - float(margin))
    vbps_safe = max(120_000.0, float(vbps_safe))

    pbae = None
    try:
        if isinstance(inputs.scene, dict) and isinstance(inputs.scene.get("scenes"), list) and len(inputs.scene.get("scenes") or []) >= 2:
            # content hints (pure; deterministic fallbacks)
            content = {
                "difficulty": _scene_complexity(inputs.scene),
                "grain_sensitive": bool((inputs.scene.get("grain_sensitive") or False)),
            }
            pbae = _pbae_allocate(
                scenes=list(inputs.scene.get("scenes") or []),
                budget_video_bps=float(vbps_safe),
                duration_s=float(inputs.duration_s),
                width=int(w),
                height=int(h),
                fps=float(fps if fps > 0 else 30.0),
                content=content,
            )
            _log_event(
                severity="INFO",
                component="planner",
                operation="pbae_allocate",
                error_code="OK",
                message="PBAE scene allocation computed (planner-only)",
                context={
                    "encoder": enc,
                    "container": cont,
                    "vbps_base": int(round(float(vbps))),
                    "vbps_safe": int(round(float(vbps_safe))),
                    "margin": float(margin),
                    "scenes": int(pbae.get("summary", {}).get("scenes") or 0),
                    "protected_fraction": float(pbae.get("summary", {}).get("protected_fraction") or 0.0),
                    "weight_min": float(pbae.get("summary", {}).get("weight_min") or 0.0),
                    "weight_max": float(pbae.get("summary", {}).get("weight_max") or 0.0),
                },
                settings_dir=inputs.settings_dir,
            )
    except Exception as _pbae_e:
        _log_event(
            severity="WARNING",
            component="planner",
            operation="pbae_allocate",
            error_code=E_RUN_PBAE,
            message="PBAE failed; continuing without scene allocation",
            context={"encoder": enc, "container": cont, "err": f"{type(_pbae_e).__name__}: {_pbae_e}"},
            settings_dir=inputs.settings_dir,
        )
        pbae = None

    # Use the safe vbps seed for downstream size targeting (never exceed invariant remains external)
    vbps = float(vbps_safe)


    params: Dict[str, Any] = {"encoder": enc}
    if enc in ("x264", "libx264"):
        params.update({"preset": "slow" if not aggressive else "medium", "tune": "film"})
        if crf0 is not None:
            params["crf_hint"] = float(crf0)
    elif enc in ("x265", "libx265"):
        params.update({"preset": "slow" if not aggressive else "medium"})
        if crf0 is not None:
            params["crf_hint"] = float(crf0)
    elif "av1" in enc:
        params.update({"preset": 6 if not aggressive else 8})
        if crf0 is not None:
            params["cq_hint"] = float(crf0)
    else:
        params.update({"preset": "medium"})

    zone_plan = None
    zones_enabled = False
    if scene and isinstance(scene, dict):
        try:
            if int(os.environ.get("BC_SCENE_SPLIT", "0")) == 1:
                if scene.get("scenes") and len(scene.get("scenes")) >= 2:
                    zones_enabled = True
        except Exception:
            zones_enabled = False
    if isinstance(inputs.scene, dict) and inputs.scene.get("zones") is not None:
        zone_plan = {"zones": inputs.scene.get("zones"), "zones_str": inputs.scene.get("zones_str")}

    # Backward-compatible enrichment: include PBAE output and (Option 2) emit codec-native zones for x264/x265.
    if pbae is not None:
        if zone_plan is None:
            zone_plan = {}
        zone_plan["pbae"] = pbae

        # Zone exporter: deterministic, conservative; AV1 and unknown encoders => disabled.
        try:
            zexp = _pbae_export_zones(
                encoder=str(enc),
                fps_out=float(fps if fps > 0 else 0.0),
                pbae=pbae,
            )
        except Exception as _ze:
            zexp = {
                "enabled": False,
                "format": "none",
                "args": [],
                "zones_str": "",
                "zones": [],
                "zones_count": 0,
                "mult_min": 1.0,
                "mult_max": 1.0,
                "min_zone_s": 2.0,
                "merge_applied": False,
                "reason": f"exception:{type(_ze).__name__}",
            }
            _log_event(
                severity="WARNING",
                component="planner",
                operation="zone_export",
                error_code=E_RUN_ZONES,
                message="Zone export failed; continuing without zones",
                context={"encoder": enc, "container": cont, "err": f"{type(_ze).__name__}: {_ze}"},
                settings_dir=inputs.settings_dir,
            )

        if zone_plan is None:
            zone_plan = {}
        zone_plan["export"] = zexp

        # If enabled, override legacy zones_str/zones with exporter output (deterministically).
        if bool(zexp.get("enabled")):
            zone_plan["zones_str"] = str(zexp.get("zones_str") or "")
            zone_plan["zones"] = list(zexp.get("zones") or [])

            _log_event(
                severity="INFO",
                component="planner",
                operation="zone_export",
                error_code="OK",
                message="Zones exported for x26x",
                context={
                    "encoder": enc,
                    "container": cont,
                    "format": str(zexp.get("format") or ""),
                    "zones_count": int(zexp.get("zones_count") or 0),
                    "mult_min": float(zexp.get("mult_min") or 1.0),
                    "mult_max": float(zexp.get("mult_max") or 1.0),
                    "merge_applied": bool(zexp.get("merge_applied") or False),
                },
                settings_dir=inputs.settings_dir,
            )
        else:
            # Disabled is not an error; log at DEBUG-ish level (INFO with reason for now).
            _log_event(
                severity="INFO",
                component="planner",
                operation="zone_export",
                error_code=E_VAL_ZONES,
                message="Zones not enabled for this encode",
                context={
                    "encoder": enc,
                    "container": cont,
                    "format": str(zexp.get("format") or "none"),
                    "reason": str(zexp.get("reason") or ""),
                },
                settings_dir=inputs.settings_dir,
            )

    # If zones are enabled, expose a param-string override for downstream ffmpeg two-pass builders.
    # This is append-only and safe: caller may merge into existing -x26x-params strings.
    try:
        if isinstance(zone_plan, dict) and isinstance(zone_plan.get("export"), dict) and zone_plan["export"].get("enabled"):
            zfmt = str(zone_plan["export"].get("format") or "")
            zstr = str(zone_plan["export"].get("zones_str") or "")
            if zstr:
                if zfmt == "x264-zones":
                    params["x264_params"] = _zones_append_key(str(params.get("x264_params", "")), "zones=", str(zstr))
                    _log_event(
                        severity="INFO",
                        component="planner",
                        operation="zone_inject",
                        error_code="OK",
                        message="Injected x264 zones into encoder params",
                        context={
                            "encoder": enc,
                            "zones_str": str(zstr),
                            "zones_count": int(zone_plan.get("export", {}).get("zones_count", 0)),
                            "mult_min": float(zone_plan.get("export", {}).get("mult_min", 1.0)),
                            "mult_max": float(zone_plan.get("export", {}).get("mult_max", 1.0)),
                        },
                        settings_dir=inputs.settings_dir,
                    )

                elif zfmt == "x265-zones":
                    params["x265_params"] = _zones_append_key(str(params.get("x265_params", "")), "zones=", str(zstr))
                _log_event(
                    severity="INFO",
                    component="planner",
                    operation="zone_inject",
                    error_code="OK",
                    message="Injected x265 zones into encoder params",
                    context={
                        "encoder": enc,
                        "zones_str": str(zstr),
                        "zones_count": int(zone_plan.get("export", {}).get("zones_count", 0)),
                        "mult_min": float(zone_plan.get("export", {}).get("mult_min", 1.0)),
                        "mult_max": float(zone_plan.get("export", {}).get("mult_max", 1.0)),
                    },
                    settings_dir=inputs.settings_dir,
                )

    except Exception:
        pass

    predicted = int(((vbps * float(inputs.duration_s)) / 8.0 + audio_bytes) * max(1.0, float(overhead)))

    conf_scene = 0.55 + 0.45 * (1.0 - abs(diff - 0.5) * 2.0)
    conf = float(max(0.0, min(1.0, 0.65 * conf_probe + 0.35 * conf_scene)))

    # Conservative, deterministic overshoot-prevention based on probe confidence/bounds.
    # Do not increase vbps here; only reduce to honor strict size targeting.
    try:
        vbps = _apply_confidence_margin(vbps, conf, getattr(inputs, "probe", None) and inputs.probe.get("error_bounds"))
    except Exception:
        pass
    try:
        if isinstance(zone_plan, dict) and isinstance(zone_plan.get("export"), dict):
            _log_event(
                severity="INFO",
                component="planner",
                operation="zone_summary",
                error_code="OK",
                message="Zone summary",
                context={
                    "enabled": bool(zone_plan["export"].get("enabled")),
                    "encoder": enc,
                    "zones_count": int(zone_plan["export"].get("zones_count", 0)),
                    "mult_min": float(zone_plan["export"].get("mult_min", 1.0)),
                    "mult_max": float(zone_plan["export"].get("mult_max", 1.0)),
                    "reason": str(zone_plan["export"].get("reason", "")),
                },
                settings_dir=inputs.settings_dir,
            )
    except Exception:
        pass

    return PlanOutputs(
        video_bps=int(vbps),
        audio_bps=int(audio_bps),
        encoder_params=dict(params or {}),
        confidence=float(conf),
        predicted_bytes=int(predicted),
        overhead_factor=float(overhead),
        overshoot_factor=float(overshoot),
        zone_plan=zone_plan,
        zones_enabled=bool(zones_enabled),
    )

