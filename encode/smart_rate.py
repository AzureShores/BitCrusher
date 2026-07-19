from __future__ import annotations

import json, os, re, time, hashlib, subprocess, tempfile, shutil
from pathlib import Path
from typing import Tuple, List, Optional

def _find_bin(env_keys: List[str], default_name: str) -> str:
    for k in env_keys:
        v = os.environ.get(k)
        if v:
            return v
    return shutil.which(default_name) or default_name

FFMPEG  = _find_bin(["BC_FFMPEG", "FFMPEG"], "ffmpeg")
FFPROBE = _find_bin(["BC_FFPROBE", "FFPROBE"], "ffprobe")


DEFAULT_STATS = {
    "overshoot": {},
    "encoder_probe_cache": {},
    "updated_at": 0
}


def _ensure_dir(p: str | os.PathLike) -> Path:
    d = Path(p)
    d.mkdir(parents=True, exist_ok=True)
    return d

def _stats_path(base_dir: str | os.PathLike) -> str:
    return str(_ensure_dir(base_dir) / "encode_stats.json")

def load_stats(base_dir: str | os.PathLike) -> dict:
    p = _stats_path(base_dir)
    if not os.path.exists(p):
        return dict(DEFAULT_STATS)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULT_STATS)
        data.setdefault("overshoot", {})
        data.setdefault("updated_at", 0)
        return data
    except Exception:
        return dict(DEFAULT_STATS)

def save_stats(base_dir: str | os.PathLike, stats: dict) -> None:
    p = _stats_path(base_dir)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except Exception:
        pass

def _res_bucket(width: int | None) -> str:
    try:
        w = int(width or 0)
    except Exception:
        w = 0
    if w < 1: return "unk"
    if w < 640: return "subsd"
    if w < 854: return "sd"
    if w < 1280: return "720p"
    if w < 1920: return "1080p"
    if w < 2560: return "1440p"
    if w < 3840: return "2160p"
    return f"{w}w"

def _fps_bucket(fps: float | None) -> str:
    try:
        f = float(fps or 0.0)
    except Exception:
        f = 0.0
    if f < 1: return "unk"
    if f < 24.5: return "le24"
    if f < 30.5: return "24-30"
    if f < 45.0: return "30-45"
    if f < 60.5: return "45-60"
    return "gt60"

# Minimum observations a content-class-specific bucket needs before it's
# trusted over the coarser encoder/container/resolution/fps bucket -- same
# >=3-neighbor gate outcome_ledger's codec-prior pattern already uses
# (outcome_ledger._MIN_PRIOR_N), so a class bucket earns trust the same way
# the rest of the ledger's live predictions do, not a fresh invented threshold.
_KLASS_MIN_N = 3


def _ov_key(encoder: str, container: str, width: int | None, fps: float | None,
           klass: str | None = None) -> str:
    enc = (encoder or "x264").lower()
    cont = (container or "mp4").lower()
    base = f"{enc}|{cont}|{_res_bucket(width)}|{_fps_bucket(fps)}"
    return f"{base}|{klass}" if klass else base


def get_overshoot_confidence(stats: dict, encoder: str, container: str = "mp4",
                             width: int | None = None, fps: float | None = None,
                             full_trust_n: int = 10, klass: str | None = None) -> float:
    """How many prior observations fed this bucket's overshoot factor --
    scaled to 0..1, saturating at `full_trust_n`. Lets learn_from_result pass
    a REAL confidence into update_overshoot's confidence-weighted learning
    rate instead of the hardcoded 0.5 every caller used before (a thin bucket
    got the same trust as a well-observed one). Pass `klass` to read the
    content-class-specific bucket's own observation count."""
    key = _ov_key(encoder, container, width, fps, klass)
    try:
        n = int(stats.get("overshoot_n", {}).get(key, 0) or 0)
        return max(0.0, min(1.0, n / max(1, int(full_trust_n))))
    except Exception:
        return 0.0


def get_dynamic_overshoot(stats: dict, encoder: str, container: str = "mp4", default: float = 1.00,
                          width: int | None = None,
                          fps: float | None = None,
                          klass: str | None = None) -> float:
    """Graduated per-content-class trust ladder: when `klass` is given (e.g.
    ai_advisor's DifficultyScore.klass -- screen_ui/film_grain/sports_action/
    flat_camera/general) and that class's own bucket already has >=_KLASS_MIN_N
    observations, prefer its narrower, more specific factor. Otherwise fall
    back to the coarse encoder/container/resolution/fps bucket, same as
    before -- a thin class bucket never gets to act, it just quietly waits
    for enough data while the coarse bucket keeps serving requests."""
    if klass:
        key_c = _ov_key(encoder, container, width, fps, klass)
        n_c = int(stats.get("overshoot_n", {}).get(key_c, 0) or 0)
        if n_c >= _KLASS_MIN_N:
            try:
                val = float(stats.get("overshoot", {}).get(key_c, default))
                if 0.5 < val < 1.5:
                    return val
            except Exception:
                pass
    key = _ov_key(encoder, container, width, fps)
    try:
        val = float(stats.get("overshoot", {}).get(key, default))
        if not (0.5 < val < 1.5):
            return default
        return val
    except Exception:
        return default


def _apply_overshoot_update(stats: dict, key: str, ratio: float, lr: float,
                            confidence: float, cur: float) -> None:
    try:
        c = max(0.0, min(1.0, float(confidence)))
    except Exception:
        c = 0.5
    lr_eff = max(0.02, min(0.45, float(lr) * (0.20 + 0.80 * c)))
    new = max(0.92, min(1.08, (1.0 - lr_eff) * float(cur) + lr_eff * float(ratio)))
    stats.setdefault("overshoot", {})[key] = float(round(new, 4))
    n_map = stats.setdefault("overshoot_n", {})
    n_map[key] = int(n_map.get(key, 0) or 0) + 1


def update_overshoot(stats: dict,
                     encoder: str,
                     container: str,
                     target_bytes: int,
                     actual_bytes: int,
                     lr: float = 0.25,
                     width: int | None = None,
                     fps: float | None = None,
                     confidence: float = 0.5,
                     klass: str | None = None) -> dict:
    """
    Confidence-weighted overshoot learning.
    Stores a multiplicative factor such that planned_bytes * factor ~= actual_bytes.

    When `klass` is given, updates BOTH the coarse bucket (unchanged) and the
    content-class-specific bucket (dual-write) -- the class bucket never
    fragments the coarse one away, it just accumulates its own narrower
    history alongside it until get_dynamic_overshoot's trust gate lets it act.

    Deterministic update; callers may freeze time.time() in tests.
    """
    try:
        ratio = float(actual_bytes) / max(1.0, float(int(target_bytes)))
        # slight directional bias to reduce repeated overshoot/undershoot oscillation
        ratio = ratio * (1.02 if ratio > 1.0 else 0.995)
        ratio = max(0.90, min(1.12, ratio))
    except Exception:
        return stats

    key = _ov_key(encoder, container, width, fps)
    cur = get_dynamic_overshoot(stats, encoder, container, width=width, fps=fps)
    _apply_overshoot_update(stats, key, ratio, lr, confidence, cur)

    if klass:
        key_c = _ov_key(encoder, container, width, fps, klass)
        cur_c = get_dynamic_overshoot(stats, encoder, container, width=width, fps=fps, klass=klass)
        conf_c = get_overshoot_confidence(stats, encoder, container, width=width, fps=fps, klass=klass)
        _apply_overshoot_update(stats, key_c, ratio, lr, conf_c, cur_c)

    stats["updated_at"] = int(time.time())
    return stats


def guardrail_adjust(actual_bytes: int, target_bytes: int, tol: float = 0.02) -> float | None:
    """
    Returns a correction factor when file size misses the target by more than ±2 %.
    """
    if actual_bytes > target_bytes * (1.0 + tol):
        # overshoot → shrink next run
        return target_bytes / actual_bytes
    if actual_bytes < target_bytes * (1.0 - tol):
        # undershoot → expand next run
        return target_bytes / max(1, actual_bytes)
    return None


def pick_audio_bitrate(channels: int, sample_rate: int, audio_fmt: str = "aac") -> int:
    fmt = (audio_fmt or "aac").lower()
    ch = max(1, int(channels or 2))
    sr = int(sample_rate or 48000)
    if fmt == "opus":
        # Opus is highly efficient: 48 kbps stereo = AAC 96 kbps quality.
        base = 48_000 if ch <= 2 else 80_000
        if sr > 48_000: base = int(base * 1.1)
    elif fmt in ("aac", "m4a", "aac_lc", "aac-ld"):
        base = 96_000 if ch <= 2 else 128_000
        if sr > 48_000: base = int(base * 1.1)
    elif fmt == "mp3":
        base = 128_000 if ch <= 2 else 160_000
    else:
        base = 96_000 if ch <= 2 else 128_000
    return int(base)

def _scene_based_probe_starts(scene_json: str, dur: float, seconds: int) -> Optional[List[float]]:
    try:
        if not scene_json or not os.path.exists(scene_json):
            return None
        with open(scene_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        scenes = data.get("scenes") if isinstance(data, dict) else None
        if not isinstance(scenes, list) or not scenes:
            return None

        scored = []
        for s in scenes:
            if not isinstance(s, dict):
                continue
            t0 = float(s.get("t0", s.get("start", s.get("s", 0.0))) or 0.0)
            t1 = float(s.get("t1", s.get("end", s.get("e", 0.0))) or 0.0)
            if t1 <= t0:
                continue
            if (t1 - t0) < seconds * 1.10:
                continue

            score = s.get("difficulty", s.get("score", s.get("complexity", s.get("motion", s.get("weight", 0.0)))))
            try:
                score = float(score)
            except Exception:
                score = 0.0

            scored.append((score, t0, t1))

        if not scored:
            return None

        scored.sort(key=lambda x: x[0], reverse=True)
        picks: List[float] = []
        for _, t0, t1 in scored:
            scene_len = t1 - t0
            start = t0 + max(0.0, (scene_len - seconds) * 0.35)
            start = max(0.0, min(dur - max(0.05, float(seconds)), float(start)))
            if all(abs(start - p) > seconds * 1.50 for p in picks):
                picks.append(start)
            if len(picks) >= 3:
                break

        return picks if picks else None
    except Exception:
        return None


def _extract_probe_segments(src: str, out_dir: str, seconds: int = 5) -> list[str]:
    """
    Build a representative probe clip set from diverse parts.
    If BC_SCENE_JSON is set and parseable, prefer the hardest scenes.
    """
    os.makedirs(out_dir, exist_ok=True)
    dur = float(_safe_duration(src))
    if dur <= 0.0:
        return []

    scene_json = os.environ.get("BC_SCENE_JSON", "")
    starts = _scene_based_probe_starts(scene_json, dur, seconds) or [dur * 0.10, dur * 0.50, dur * 0.90]
    starts = [max(0.0, min(dur - max(0.05, float(seconds)), float(t))) for t in starts][:3]

    outs: List[str] = []
    for i, t in enumerate(starts, 1):
        dst = os.path.join(out_dir, f"probe_{i}.mp4")
        cmd = [
            FFMPEG, "-y", "-v", "error",
            "-ss", f"{t:.3f}", "-t", str(seconds),
            "-i", src,
            "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-vf", "format=yuv420p",
            "-movflags", "+faststart",
            dst,
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(dst):
            outs.append(dst)
    return outs


def _probe_encode_ssim(clips: list[str], encoder: str, crf: int, tmpdir: str | None = None) -> tuple[float, float]:
    ffmpeg = os.environ.get("FFMPEG", "ffmpeg")
    ffprobe = os.environ.get("FFPROBE", "ffprobe")

    tmp = tmpdir or tempfile.mkdtemp(prefix="bc_probe_")
    try:
        mosaic = os.path.join(tmp, "mosaic.mp4")
        out = os.path.join(tmp, "enc.mkv")

        # Build a simple concat/mosaic for stability across clips
        # (keep your existing logic if you already create `mosaic`)
        if len(clips) == 1 and os.path.isfile(clips[0]):
            mosaic = clips[0]
        else:
            # fallback: concat list -> mosaic.mp4
            lst = os.path.join(tmp, "list.txt")
            with open(lst, "w", encoding="utf-8") as f:
                for c in clips:
                    f.write("file '{}'\n".format(os.path.abspath(c).replace("\\", "/").replace("'", "\\'")))
            subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", mosaic],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        enc = (encoder or "").lower()
        if "vvenc" in enc or "vvc" in enc or "266" in enc:
            vcodec = "libvvenc"
        elif "svt" in enc and "av1" in enc:
            vcodec = "libsvtav1"
        elif "av1" in enc:
            vcodec = "libaom-av1"
        elif "vp9" in enc:
            vcodec = "libvpx-vp9"
        elif "265" in enc or "x265" in enc or "hevc" in enc:
            vcodec = "libx265"
            out = os.path.join(tmp, "enc.mp4")
        else:
            vcodec = "libx264"
            out = os.path.join(tmp, "enc.mp4")

        # very-fast probe settings (probe must be stable, not perfect)
        speed: list[str] = []
        if vcodec in ("libx264", "libx265"):
            speed = ["-preset", "veryfast"]
        elif vcodec == "libsvtav1":
            speed = ["-preset", "10"]
        elif vcodec == "libaom-av1":
            speed = ["-cpu-used", "6", "-row-mt", "1", "-tile-columns", "1", "-tile-rows", "0"]
        elif vcodec == "libvpx-vp9":
            speed = ["-deadline", "good", "-cpu-used", "5", "-row-mt", "1", "-tile-columns", "1", "-tile-rows", "0", "-lag-in-frames", "25"]
        elif vcodec == "libvvenc":
            speed = ["-preset", "fast"]

        pix: list[str] = ["-pix_fmt", "yuv420p10le"] if vcodec == "libvvenc" else []

        # rate control
        if vcodec == "libvvenc":
            rc = ["-qp", str(int(crf))]
        elif vcodec in ("libaom-av1", "libsvtav1", "libvpx-vp9"):
            rc = ["-b:v", "0", "-crf", str(int(crf))]
        else:
            rc = ["-crf", str(int(crf))]

        # encode
        cmd = [ffmpeg, "-y", "-v", "error", "-i", mosaic, "-c:v", vcodec] + speed + pix + rc + ["-an"]
        if out.lower().endswith(".mp4"):
            cmd += ["-movflags", "+faststart"]
        cmd += [out]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # compute SSIM + bitrate
        ssim = 0.0
        bitrate_kbps = 0.0

        # bitrate
        try:
            p = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=bit_rate",
                 "-of", "default=nw=1:nk=1", out],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            br = float((p.stdout or "").strip() or 0.0)
            bitrate_kbps = br / 1000.0
        except Exception:
            bitrate_kbps = 0.0

        # SSIM
        try:
            p = subprocess.run(
                [ffmpeg, "-v", "error", "-i", mosaic, "-i", out,
                 "-lavfi", "ssim=stats_file=-", "-f", "null", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            txt = (p.stderr or "") + "\n" + (p.stdout or "")
            # parse "All:" value
            m = re.search(r"All:\s*([0-9.]+)", txt)
            if m:
                ssim = float(m.group(1))
        except Exception:
            ssim = 0.0

        return float(ssim), float(bitrate_kbps)
    finally:
        if tmpdir is None:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass


def _fit_quality_rate(crf_vals: list[int], kbps_vals: list[float], ssim_vals: list[float]):
    """
    Fit a simple monotone mapping from CRF to (kbps, ssim) via local regression.
    Returns a function f(desired_ssim)->crf.
    """
    if len(crf_vals) < 2:
        return lambda s: crf_vals[0] if crf_vals else 26
    import numpy as _np
    X = _np.array(crf_vals, dtype=float)
    Y = _np.array(ssim_vals, dtype=float)
    # Linear fit in CRF domain for SSIM for small range
    a, b = _np.polyfit(X, Y, 1)
    def _solve(desired):
        if abs(a) < 1e-6: return float(X.mean())
        crf = (desired - b) / a
        return float(max(min(crf, float(max(crf_vals))), float(min(crf_vals))))
    return _solve

def _safe_duration(path: str) -> float:
    try:
        out = subprocess.check_output([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                       "-of", "default=noprint_wrappers=1:nokey=1", path], text=True)
        return float(out.strip() or "0")
    except Exception:
        return 0.0

# ---------- Byte-accurate mux overhead model (new) ----------
def estimate_mux_overhead(duration_s: float, fps: float, keyint: int, tracks: int = 2,
                          container: str = "mp4") -> int:
    """
    Very close estimate of container bytes for MP4/MKV given duration/fps/keyint/track count.
    The model is deterministic and errs conservatively high by design.
    """
    cont = (container or "mp4").lower()
    seconds = max(0.0, float(duration_s))
    frames  = seconds * max(1.0, float(fps))
    i_freq  = max(1, int(keyint or 120))
    keyframes = max(1, int(frames / i_freq))
    base = 18_000 if cont in ("mp4","mov","m4v") else 14_000   # ftyp + moov or segment index
    per_frame = 9.0 if cont in ("mp4","mov","m4v") else 6.0    # stts/ctts/mdat indexing
    per_key   = 120.0 if cont in ("mp4","mov","m4v") else 60.0
    per_track = 6_000 if cont in ("mp4","mov","m4v") else 4_000
    est = base + int(per_frame * frames) + int(per_key * keyframes) + int(per_track * max(1, tracks))
    return int(est)
# ---------- end new helper ----------

def _is_auto_encoder(encoder: str) -> bool:
    e = (encoder or "").strip().lower().replace("_", "-")
    return e in ("auto", "auto-probe", "best", "probe", "smart")


def _probe_candidates_for(container: str) -> List[str]:
    return ["svt-av1", "aom-av1", "x265", "x264", "vp9", "vvc"]


def _probe_grid_for(encoder: str, target_kbps: float, width_hint: int = 0, fps_hint: float = 0.0) -> List[int]:
    w = int(width_hint or 1280)
    h = max(16, int(w * 9 / 16))
    fps = float(fps_hint or 30.0)
    pix = float(w * h) * max(1.0, fps)
    bpp = (float(target_kbps) * 1000.0) / max(1.0, pix)

    if bpp < 0.060:
        base = 34
    elif bpp < 0.090:
        base = 30
    elif bpp < 0.130:
        base = 26
    else:
        base = 22

    v = _ffmpeg_vcodec_for_encoder(encoder)
    if v == "libx264":
        base -= 2
    if v in ("libaom-av1", "libsvtav1", "libvpx-vp9"):
        base += 4
    if v == "libvvenc":
        base += 2

    base = int(max(12, min(50, base)))
    return sorted({base - 4, base, base + 4})


def _interp_ssim_at_kbps(points: List[Tuple[float, float]], target_kbps: float) -> float:
    pts = sorted([(float(k), float(s)) for k, s in points if k > 0.0], key=lambda x: x[0])
    if not pts:
        return 0.0
    if len(pts) == 1:
        return pts[0][1]

    tk = float(target_kbps)
    if tk <= pts[0][0]:
        return pts[0][1]
    if tk >= pts[-1][0]:
        return pts[-1][1]

    for (k0, s0), (k1, s1) in zip(pts, pts[1:]):
        if k0 <= tk <= k1:
            if k1 == k0:
                return s1
            t = (tk - k0) / (k1 - k0)
            return s0 + t * (s1 - s0)

    return pts[-1][1]

def _probe_encode_metrics(clips: list[str], encoder: str, crf: int) -> tuple[float, float, float]:
    """
    Deterministic probe metrics helper for auto-pick.
    Returns (kbps, ssim_all, seconds). Seconds is a deterministic placeholder (no wall-clock).
    """
    kbps, ssim_all = _probe_encode_ssim(clips, encoder, crf)
    # Planning must be deterministic; do not use wall-clock speed. Return a stable sentinel.
    return float(kbps), float(ssim_all), 1.0


def _auto_pick_encoder_by_probe(
    src: str,
    target_kbps: float,
    width_hint: int = 0,
    fps_hint: float = 0.0,
    container: str = "mp4",
    candidates: Optional[List[str]] = None,
) -> Optional[str]:
    if not src or not os.path.exists(src):
        return None

    cand = candidates or _probe_candidates_for(container)
    tmpdir = tempfile.mkdtemp(prefix="bc_autoprobe_")
    try:
        clips = _extract_probe_segments(src, tmpdir, seconds=3)
        if len(clips) < 2:
            return None

        stage = []
        for enc in cand:
            grid = _probe_grid_for(enc, target_kbps, width_hint, fps_hint)
            q_mid = grid[1] if len(grid) > 1 else grid[0]
            q_hi = grid[-1]

            pts: List[Tuple[float, float]] = []
            # Determinism: do not use wall-clock timing for planning decisions.

            for q in [q_mid, q_hi]:
                kbps, ssim_all, sec = _probe_encode_metrics(clips, enc, int(q))
                if kbps <= 0.0 or ssim_all <= 0.0:
                    pts = []
                    break
                pts.append((kbps, ssim_all))


            if not pts:
                continue

            ssim_at = _interp_ssim_at_kbps(pts, target_kbps)
            # Determinism: do not compute speed from wall-clock time; use stable tie-breakers only.
            stage.append((enc, float(ssim_at), pts, grid))

        if not stage:
            return None

        # Deterministic ordering: primary SSIM, secondary stable encoder preference.
        pref_order = {e: i for i, e in enumerate(cand)}
        stage.sort(key=lambda x: (-float(x[1]), int(pref_order.get(x[0], 10_000))))
        
        # Keep up to top-2 for refinement; stable selection.
        top = stage[:2]


        refined: List[Tuple[str, float]] = []
        for enc, _, pts, grid in top:
            q_lo = grid[0]
            kbps, ssim_all, sec = _probe_encode_metrics(clips, enc, int(q_lo))
            pts2 = pts + [(kbps, ssim_all)] if kbps > 0.0 and ssim_all > 0.0 else pts
            ssim_at = _interp_ssim_at_kbps(pts2, target_kbps)
            refined.append((enc, float(ssim_at)))

        for enc, ssim_at, _pts, _grid in stage[2:]:
            refined.append((enc, float(ssim_at)))

        refined.sort(key=lambda x: (-float(x[1]), int(pref_order.get(x[0], 10_000))))
        return refined[0][0] if refined else None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def choose_bitrates(duration_s: float,
                    target_bytes: int,
                    encoder: str = "x264",
                    container: str = "mp4",
                    channels: int = 2,
                    sample_rate: int = 48000,
                    audio_fmt: str = "aac",
                    stats_dir: str = ".smart",
                    width_hint: int | None = None,
                    fps_hint: float | None = None,
                    audio_copy_bps: int | None = None,
                    input_path: str | None = None,
                    skip_probe: bool = False) -> Tuple[int, int, float]:
    """
    SmartRate:
      • dynamic overshoot (per encoder/res/fps bucket)
      • micro-probe (3 points) -> local R–Q solve
      • byte-accurate mux overhead reserve
    input_path: explicit source path (preferred over the BC_CURRENT_INPUT env
    fallback, which is process-global and unsafe under concurrent jobs).
    skip_probe: bypass the micro-probe (used when a cached result seeds the job).
    """
    stats = load_stats(stats_dir)
    enc_in = (encoder or "x264").strip().lower().replace("_", "-")
    if _is_auto_encoder(enc_in):
        os.environ.setdefault("BC_PICKED_ENCODER", "x265")
        encoder_for_ov = os.environ.get("BC_PICKED_ENCODER") or "x265"
    else:
        encoder_for_ov = encoder

    ov_raw = get_dynamic_overshoot(stats, encoder_for_ov, container, default=1.02, width=width_hint, fps=fps_hint)
    ov = max(1.00, min(1.20, float(ov_raw)))


    # Audio plan
    a_bps = pick_audio_bitrate(channels, sample_rate, audio_fmt) if not audio_copy_bps else int(audio_copy_bps)

    # Reserve container bytes up front
    fps = float(fps_hint or 30.0)
    keyint = 60 if fps >= 60 else 120
    mux_bytes = estimate_mux_overhead(duration_s=float(duration_s), fps=fps, keyint=keyint,
                                      tracks=2 if a_bps>0 else 1, container=container)
    usable = max(1, int(target_bytes - mux_bytes))

    # First pass split
    a_bytes = int(max(0, duration_s) * a_bps / 8)
    v_bytes = max(1, usable - a_bytes)
    v_bps = max(80_000, int((v_bytes * 8) / max(1.0, duration_s) / ov))

    # Micro-probe on representative mosaic (adaptive grid; optional auto-encoder shootout)
    try:
        in_path = (input_path or "").strip() or os.environ.get("BC_CURRENT_INPUT", "")
        long_enough = float(duration_s) >= 12.0
        tight_budget = v_bps / (max(1.0, float(width_hint or 1280)) * max(1.0, fps)) < 0.08

        if (not skip_probe) and in_path and os.path.exists(in_path) and (long_enough or tight_budget):

            if _is_auto_encoder(encoder):
                target_kbps = max(150.0, float(v_bps) / 1000.0)
                picked = _auto_pick_encoder_by_probe(
                    in_path,
                    target_kbps=target_kbps,
                    width_hint=int(width_hint or 0),
                    fps_hint=float(fps_hint or 0.0),
                    container=container,
                )
                if picked:
                    os.environ["BC_PICKED_ENCODER"] = picked
                    encoder = picked

            tmp = tempfile.mkdtemp(prefix="bc_probeX_")
            try:
                clips = _extract_probe_segments(in_path, tmp, seconds=3)
                grid = _probe_grid_for(
                    encoder,
                    max(150.0, float(v_bps) / 1000.0),
                    int(width_hint or 0),
                    float(fps_hint or 0.0),
                )

                qs: List[int] = []
                kbps_list: List[float] = []
                ssim_list: List[float] = []

                for q in grid:
                    kbps, ssim = _probe_encode_ssim(clips, encoder, int(q))
                    if kbps > 0.0 and ssim > 0.0:
                        qs.append(int(q))
                        kbps_list.append(float(kbps))
                        ssim_list.append(float(ssim))

                if len(qs) >= 2:
                    solve_q = _fit_quality_rate(qs, kbps_list, ssim_list)
                    desired_ssim = 0.965 if (width_hint and int(width_hint) >= 1920) else 0.955
                    pick_q = float(solve_q(desired_ssim))

                    import numpy as _np
                    kbps_est = float(_np.interp(pick_q, _np.array(qs, dtype=float), _np.array(kbps_list, dtype=float)))
                    v_bps = int(kbps_est * 1000.0)
                    v_bytes = int(v_bps * max(1.0, float(duration_s)) / 8.0)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    except Exception:
        pass

    # codec/res clamps to avoid starving high-motion and tiny-bit budget traps
    enc = (encoder or "x264").lower()
    if "hevc" in enc or "x265" in enc or "265" in enc:
        v_bps = int(min(max(v_bps, 180_000), 12_000_000))
    elif "av1" in enc:
        v_bps = int(min(max(v_bps, 140_000), 10_000_000))
    elif "vvc" in enc or "vvenc" in enc or "266" in enc:
        v_bps = int(min(max(v_bps, 120_000), 10_000_000))
    else:  # x264 and everything else
        v_bps = int(min(max(v_bps, 220_000), 12_000_000))
    return int(v_bps), int(a_bps), float(ov)



def cache_path(base_dir: str) -> str:
    return str(_ensure_dir(base_dir) / "abr_cache.jsonl")

def _cache_key(input_path: str, target_mb: int, encoder: str) -> str:
    # Keying on filename alone let two different files that happen to share a
    # name (common across folders/re-exports) collide and silently reuse a
    # stale v_bps/width/fps record for unrelated content. Fold in the same
    # cheap content signature ml_heuristics already computes (path + size +
    # mtime + first 2MB) so distinct files never collide.
    try:
        from encode.ml_heuristics import _bc_file_sig
        _sig = _bc_file_sig(input_path)
    except Exception:
        _sig = Path(input_path).name
    h = hashlib.sha256()
    h.update(f"{_sig}|{int(target_mb)}|{(encoder or 'x264').lower()}".encode("utf-8"))
    return h.hexdigest()

def cache_store(base_dir: str, input_path: str, target_mb: int, encoder: str, v_bps: int,
                width: int, fps: float, final_size: int) -> None:
    rec = {
        "ts": int(time.time()),
        "key": _cache_key(input_path, target_mb, encoder),
        "input": Path(input_path).name,
        "target_mb": int(target_mb),
        "encoder": (encoder or "x264").lower(),
        "v_bps": int(v_bps),
        "width": int(width),
        "fps": float(fps),
        "final_size": int(final_size),
    }
    try:
        with open(cache_path(base_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        pass

def cache_lookup(base_dir: str, input_path: str, target_mb: int, encoder: str) -> dict | None:
    """
    Return the newest cached encode record for (file name, target, encoder),
    or None. Counterpart of cache_store — records were written for years but
    never read back; this closes that loop so repeat jobs skip probing.
    """
    key = _cache_key(input_path, target_mb, encoder)
    try:
        with open(cache_path(base_dir), "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("key") == key:
            return rec
    return None


def learn_from_result(stats_dir: str,
                      encoder: str,
                      container: str,
                      target_bytes: int,
                      actual_bytes: int,
                      width_hint: int | None = None,
                      fps_hint: float | None = None,
                      klass_hint: str | None = None) -> None:
    s = load_stats(stats_dir)
    # Real per-bucket confidence (how many prior observations this exact
    # encoder/container/resolution/fps bucket already has) instead of the
    # fixed 0.5 every caller previously got by omission -- a thin bucket now
    # updates slowly, a well-observed one updates near full rate.
    conf = get_overshoot_confidence(s, encoder, container, width=width_hint, fps=fps_hint)
    s = update_overshoot(s, encoder, container, target_bytes, actual_bytes,
                         lr=0.25, width=width_hint, fps=fps_hint, confidence=conf,
                         klass=klass_hint)
    save_stats(stats_dir, s)
