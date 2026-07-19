from __future__ import annotations
import json, os, subprocess, tempfile, shutil, hashlib, time
from typing import Dict, Any, Tuple, List
from pathlib import Path
from PIL import Image
import numpy as np

FFPROBE = os.environ.get("FFPROBE", "ffprobe")
FFMPEG  = os.environ.get("FFMPEG", "ffmpeg")

# -----------------------
# On-disk analysis cache
# -----------------------
def _bc_default_cache_dir() -> Path:
    # Cross-platform default cache dir
    try:
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
            return Path(base) / "BitCrusher" / "cache"
        return Path.home() / ".cache" / "bitcrusher"
    except Exception:
        return Path(".bitcrusher_cache")

def _bc_cache_dir() -> Path:
    p = os.environ.get("BC_CACHE_DIR")
    d = Path(p) if p else _bc_default_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d

def _bc_cache_disabled() -> bool:
    return str(os.environ.get("BC_CACHE_DISABLE", "")).strip() in ("1","true","yes","on","TRUE","YES","ON")

def _bc_file_sig(path: str) -> str:
    # Fast signature: size + mtime + first 2MB hash (enough to avoid collisions without hashing whole file)
    p = Path(path)
    try:
        st = p.stat()
        size = int(st.st_size)
        mtime = int(st.st_mtime)
    except Exception:
        size = 0
        mtime = 0
    h = hashlib.sha1()
    h.update(str(p.resolve()).encode("utf-8", errors="ignore"))
    h.update(f"|{size}|{mtime}|".encode("utf-8"))
    try:
        with p.open("rb") as f:
            h.update(f.read(2 * 1024 * 1024))
    except Exception:
        pass
    return h.hexdigest()

def _bc_content_hash(path: str) -> str | None:
    # Path/mtime-independent whole-file hash for cross-file batch dedup (unlike
    # _bc_file_sig, which is path+mtime+first-2MB). None on read failure so
    # callers can exclude the file without aborting the batch.
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None

def build_batch_dedup_index(file_paths: list) -> list:
    # O(n) exact-match grouping by content hash -- no pairwise/frame comparison.
    # A path listed twice (file queued twice) is deduped first so it can't group
    # as "a duplicate of itself." Files whose hash can't be computed are
    # silently excluded from the index (not from the batch itself); only
    # groups of 2+ distinct paths are duplicate candidates.
    file_paths = list(dict.fromkeys(file_paths))
    groups: dict = {}
    for p in file_paths:
        h = _bc_content_hash(p)
        if h is None:
            continue
        groups.setdefault(h, []).append(p)
    return [paths for paths in groups.values() if len(paths) >= 2]

def _bc_cache_path(kind: str, sig: str) -> Path:
    return _bc_cache_dir() / f"{kind}_{sig}.json"

def _bc_cache_load_json(p: Path) -> dict | None:
    try:
        if p.exists() and p.stat().st_size > 2:
            return json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}")
    except Exception:
        return None
    return None

def _bc_cache_save_json(p: Path, data: dict) -> None:
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def _run_json(cmd: list[str]) -> dict:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return json.loads(p.stdout)
    except Exception:
        pass
    return {}

def _safe_float(v, d=0.0):
    try: return float(v)
    except Exception: return float(d)

def _safe_int(v, d=0):
    try: return int(v)
    except Exception: return int(d)

def probe_media(path: str) -> dict:
    if not _bc_cache_disabled():
        sig = _bc_file_sig(path)
        cp = _bc_cache_path('ffprobe', sig)
        cached = _bc_cache_load_json(cp)
        if isinstance(cached, dict) and cached.get('streams') is not None:
            return cached
    data = _run_json([
        FFPROBE, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path
    ])
    if not _bc_cache_disabled():
        try:
            _bc_cache_save_json(cp, data if isinstance(data, dict) else {})
        except Exception:
            pass
    return data

def _frm_rate_to_float(fr: str | None) -> float:
    if not fr: return 0.0
    try:
        if "/" in fr:
            a,b = fr.split("/",1)
            return float(a) / float(b) if float(b) != 0 else float(a)
        return float(fr)
    except Exception:
        return 0.0

def _sample_frames(path: str, num: int = 16) -> Tuple[List[Image.Image], float]:
    """
    Uniform whole-video frame sampler.

    The old "fps=6, first 16 frames" sampler only ever saw the FIRST ~2.7
    seconds of the video — intros/title cards made hard content look easy, so
    the advisor cut the bit budget on exactly the clips that needed more. Now
    frames are pulled at evenly spaced timestamps across the full duration.
    """
    t0 = time.time()
    dur = _safe_float(probe_media(path).get("format", {}).get("duration"), 0.0)
    tmpdir = tempfile.mkdtemp(prefix="bc_frames_")
    try:
        frames: list[Image.Image] = []
        if dur > 8.0:
            # Seek-based sampling: cheap (input seeking) and exactly uniform.
            for i in range(num):
                t = dur * (i + 0.5) / float(num)
                fp = os.path.join(tmpdir, f"f_{i:03d}.jpg")
                cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", path,
                       "-frames:v", "1", "-vf", "scale=192:-2", fp]
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if os.path.exists(fp):
                    try:
                        frames.append(Image.open(fp).convert("L"))
                    except Exception:
                        pass
        else:
            # Short clip: grab a burst (covers most of it anyway).
            pattern = os.path.join(tmpdir, "f_%03d.jpg")
            rate = (num / dur) if dur > 0.5 else 6.0
            cmd = [FFMPEG, "-y", "-i", path, "-vf", f"fps={rate:.4f},scale=192:-2",
                   "-frames:v", str(num), pattern]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for i in range(1, num + 1):
                fp = os.path.join(tmpdir, f"f_{i:03d}.jpg")
                if os.path.exists(fp):
                    try:
                        frames.append(Image.open(fp).convert("L"))
                    except Exception:
                        pass
        return frames, time.time() - t0
    finally:
        try: shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception: pass


# ---------- Scene analysis + zones (new) ----------
def _detect_scene_boundaries(path: str, thresh: float = 0.40) -> list[float]:
    """
    Return an increasing list of boundary timestamps (seconds), including 0.0 and final duration.
    """
    boundaries: list[float] = [0.0]
    try:
        cmd = [FFMPEG, "-hide_banner", "-i", path,
               "-vf", f"select='gt(scene,{thresh})',showinfo", "-an", "-f", "null", "-"]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stderr = p.stderr or ""
        for ln in stderr.splitlines():
            if "pts_time:" in ln:
                try:
                    t = float(ln.split("pts_time:")[1].split(" ")[0])
                    if boundaries and t - boundaries[-1] < 0.25:
                        continue  # ignore micro-cuts
                    boundaries.append(max(0.0, t))
                except Exception:
                    pass
        dur = _safe_float(probe_media(path).get("format", {}).get("duration"), 0.0)
        if dur > 0 and (not boundaries or boundaries[-1] < dur):
            boundaries.append(dur)
    except Exception:
        pass
    if len(boundaries) < 2:
        # fallback: one scene whole file
        dur = _safe_float(probe_media(path).get("format", {}).get("duration"), 0.0) or 60.0
        boundaries = [0.0, dur]
    return boundaries

def _scene_features_from_frames(frames: list[Image.Image]) -> dict:
    ent = np.array([_entropy(f) for f in frames], dtype=float) if frames else np.array([0.0])
    edg = np.array([_edge_strength(f) for f in frames], dtype=float) if frames else np.array([0.0])
    txt = np.array([_text_edge_density(f) for f in frames], dtype=float) if frames else np.array([0.0])
    blk = np.array([_blockiness_score(f) for f in frames], dtype=float) if frames else np.array([0.0])
    grn = np.array([_graininess_score(f) for f in frames], dtype=float) if frames else np.array([0.0])
    return {
        "entropy_p95": float(np.percentile(ent, 95)) if ent.size>1 else float(ent[0]),
        "edge_p95":    float(np.percentile(edg, 95)) if edg.size>1 else float(edg[0]),
        "text_edge_density": float(np.mean(txt)),
        "blockiness": float(np.mean(blk)),
        "graininess": float(np.mean(grn)),
    }

def _scene_weight(sf: dict) -> float:
    # Difficulty proxy 0.5..2.0
    w = 0.6*max(sf["entropy_p95"]/8.0, sf["edge_p95"]/8.0) \
        + 0.3*min(1.0, sf["text_edge_density"]*3.0) \
        + 0.2*min(1.0, sf["graininess"]*0.8)
    return float(max(0.5, min(2.0, 1.0 + w)))

def _qp_offset_for_scene(sf: dict) -> int:
    # Negative offsets give more bits to hard scenes
    if sf["text_edge_density"] >= 0.12 and sf["edge_p95"] >= 5.5:
        return -2
    if sf["entropy_p95"] >= 7.2 or sf["graininess"] >= 0.35:
        return -1
    if sf["entropy_p95"] <= 4.2 and sf["edge_p95"] <= 3.5:
        return +1
    return 0

def _build_zones_str(scenes: list[dict], fps: float, encoder: str) -> str:
    """
    Build x264/x265 'zones' parameter: zones=start,end,q=offset per *frame counts* (inclusive ranges).
    """
    enc = (encoder or "x264").lower()
    parts = []
    for sc in scenes:
        if not sc.get("qp_offset"):
            continue
        start_f = max(0, int(round(sc["start"] * fps)))
        end_f   = max(start_f, int(round(sc["end"] * fps)) - 1)
        parts.append(f"{start_f},{end_f},q={sc['qp_offset']:+d}")
    if not parts:
        return ""
    # ffmpeg: -x264-params "zones=0,250,q=-2/..."
    join = "/".join(parts)
    if "265" in enc:
        return f"zones={join}"
    return f"zones={join}"

def _scene_core(path: str, fps_hint: float | None = None) -> dict:
    """
    Codec-independent scene analysis: scene-cut boundaries + per-scene frame
    features. This is the expensive part (a full-file scene-cut pass plus 3-frame
    sampling per scene), so it is cached by file signature ALONE — encoding the
    same source with x264 and then x265 reuses one analysis instead of running
    the ffmpeg work twice. analyze_scenes derives the cheap per-codec fields
    (zones/gop/aq) from this. Returns {"scenes":[...], "fps":float}.
    """
    if not _bc_cache_disabled():
        sig = _bc_file_sig(path)
        cp = _bc_cache_path('scene_core', sig)
        cached = _bc_cache_load_json(cp)
        if isinstance(cached, dict) and cached.get('scenes') is not None:
            return cached

    info = probe_media(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    fps = _frm_rate_to_float(v.get("avg_frame_rate") or v.get("r_frame_rate")) or float(fps_hint or 30.0)
    b = _detect_scene_boundaries(path)
    scenes: list[dict] = []
    for i in range(len(b)-1):
        s, e = b[i], b[i+1]
        mid = (s + e) * 0.5
        # sample 3 small frames within the scene
        tmpd = tempfile.mkdtemp(prefix="bc_scnf_")
        try:
            pattern = os.path.join(tmpd, "s_%02d.jpg")
            # pick 3 timestamps: start+0.1s, mid, end-0.1s
            # clamp each timestamp so it never exceeds duration-0.05s (avoid EOF seeks)
            dur_safe = _safe_float(probe_media(path).get("format", {}).get("duration") or 0.0, 0.0)
            if dur_safe <= 0.0:
                # fallback if probing fails: keep within this scene's end
                dur_safe = max(e, s, mid, 0.0)
            safe_max = max(0.0, dur_safe - 0.05)

            raw_times = [s + 0.10, mid, e - 0.10]
            tlist: list[float] = []
            for tt in raw_times:
                clamped = min(safe_max, max(0.0, tt))
                tlist.append(clamped)

            frames: list[Image.Image] = []
            for t in tlist:
                out = os.path.join(
                    tmpd,
                    os.path.basename(pattern).replace("%02d", f"{int(t*1000)%97:02d}")
                )
                cmd = [
                    FFMPEG, "-y", "-v", "error",
                    "-i", path,
                    "-ss", f"{t:.3f}",
                    "-frames:v", "1",
                    "-vf", "scale=256:-2",
                    out
                ]
                p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                # retry once with a slightly earlier timestamp if the first attempt fails
                if (p.returncode != 0) or (not os.path.exists(out)):
                    alt_t = 0.0 if t < 0.35 else (t - 0.333)
                    cmd = [
                        FFMPEG, "-y", "-v", "error",
                        "-i", path,
                        "-ss", f"{alt_t:.3f}",
                        "-frames:v", "1",
                        "-vf", "scale=256:-2",
                        out
                    ]
                    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                if os.path.exists(out):
                    try:
                        frames.append(Image.open(out).convert("L"))
                    except Exception:
                        pass

            sf = _scene_features_from_frames(frames)
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
        w = _scene_weight(sf)
        qp_off = _qp_offset_for_scene(sf)
        scenes.append({"start": s, "end": e, "weight": w, "qp_offset": qp_off, **sf})

    core = {"scenes": scenes, "fps": fps}
    if not _bc_cache_disabled():
        try:
            _bc_cache_save_json(_bc_cache_path('scene_core', _bc_file_sig(path)), core)
        except Exception:
            pass
    return core


def analyze_scenes(path: str, encoder: str = "x264", fps_hint: float | None = None,
                   difficulty: float | None = None) -> dict:
    if not _bc_cache_disabled():
        sig = _bc_file_sig(path)
        cp = _bc_cache_path('scenes', f"{encoder}_{sig}")
        cached = _bc_cache_load_json(cp)
        if isinstance(cached, dict) and cached.get('scenes') is not None:
            # Persist a stable scene-json file path for other modules
            try:
                sj = _bc_cache_dir() / f"scenes_{encoder}_{sig}.json"
                if not sj.exists():
                    _bc_cache_save_json(sj, cached)
                cached['scene_json'] = str(sj)
            except Exception:
                pass
            return cached
    """
    Returns {"zones_str":..., "gop":..., "aq_strength":..., "scenes":[...]}
    """
    # Heavy, codec-independent scene analysis (scene-cut pass + per-scene frame
    # sampling) is computed once per file and shared across encoders; only the
    # cheap per-codec fields below (zones/gop/aq) are re-derived here.
    core = _scene_core(path, fps_hint=fps_hint)
    scenes = list(core.get("scenes") or [])
    fps = float(core.get("fps") or fps_hint or 30.0)

    # Normalize weights and choose GOP/AQ from global tempo
    tot_len = sum(sc["end"]-sc["start"] for sc in scenes) or 1.0
    sc_rate = float(len(scenes)/max(0.1, tot_len))
    if sc_rate > 0.6:    gop = 30
    elif sc_rate > 0.25: gop = 60
    elif sc_rate > 0.10: gop = 120
    else:                gop = 240
    aq_strength = 1.2 if (difficulty or 0.0) >= 0.6 else (1.0 if (difficulty or 0.0) >= 0.35 else 0.9)

    zones = _build_zones_str(scenes, fps=fps, encoder=encoder)

    # Stable numeric fields for planner/controller (no API breaking; additive only)
    try:
        d0 = float(difficulty if difficulty is not None else _estimate_difficulty(scenes))
    except Exception:
        d0 = 0.5
    d0 = max(0.0, min(1.0, d0))

    scene_count = int(len(scenes))
    hard_ratio = 0.0
    try:
        if scene_count > 0:
            hard_ratio = float(sum(1 for s in scenes if float(s.get("difficulty", 0.0)) >= 0.70)) / float(scene_count)
    except Exception:
        hard_ratio = 0.0

    # Deterministic lightweight zone list (start/end seconds with weight)
    zones_list = []
    try:
        for s in scenes:
            st = float(s.get("start", 0.0))
            en = float(s.get("end", 0.0))
            sd = float(s.get("difficulty", 0.5))
            wgt = 1.0 + 0.20 * (max(0.0, min(1.0, sd)) - 0.5)  # +/-10%
            zones_list.append({"start": st, "end": en, "weight": float(max(0.85, min(1.15, wgt)))})
    except Exception:
        zones_list = []

    out = {
        "zones_str": zones,
        "gop": gop,
        "aq_strength": aq_strength,
        "scenes": scenes,
        "fps": fps,
        "difficulty": float(d0),
        "scene_count": int(scene_count),
        "hard_scene_ratio": float(max(0.0, min(1.0, hard_ratio))),
        "zones": zones_list,
    }

    if not _bc_cache_disabled():
        try:
            sig = _bc_file_sig(path)
            cp = _bc_cache_path('scenes', f"{encoder}_{sig}")
            _bc_cache_save_json(cp, out)
            sj = _bc_cache_dir() / f"scenes_{encoder}_{sig}.json"
            _bc_cache_save_json(sj, out)
            out['scene_json'] = str(sj)
        except Exception:
            pass
    return out

def build_scene_params(path: str, encoder: str = "x264", fps_hint: float | None = None) -> Tuple[str, str | None]:
    """
    Build conservative x26x scene params used by the main encode pipeline.
    Returns: (params_str, qpfile_path_or_none)
    """
    try:
        plan = analyze_scenes(path, encoder=encoder, fps_hint=fps_hint)
    except Exception:
        return "", None

    params: list[str] = []
    try:
        gop = int(plan.get("gop") or 0)
        if gop > 0:
            params.append(f"keyint={gop}:min-keyint={max(1, gop // 2)}")
    except Exception:
        pass

    try:
        aq = float(plan.get("aq_strength") or 0.0)
        if aq > 0:
            params.append(f"aq-strength={aq:.2f}")
    except Exception:
        pass

    try:
        zones = str(plan.get("zones_str") or "").strip()
        if zones:
            params.append(zones)
    except Exception:
        pass

    # QP file export is optional and disabled for now to avoid forcing scene-cuts
    # on codecs that do not honor x264/x265 qpfile semantics uniformly.
    qpfile: str | None = None

    joined = ":".join([p for p in params if p])
    return joined, qpfile
# ---------- end new block ----------


def _blockiness_score(img: Image.Image) -> float:
    # Emphasize 8x8 boundary energy; larger => more blocky risk when starved
    a = np.asarray(img, dtype=np.float32)
    h_edges = a[:, 7::8] - a[:, 6::8]
    v_edges = a[7::8, :] - a[6::8, :]
    return float(np.mean(np.abs(h_edges)) + np.mean(np.abs(v_edges)))

def _graininess_score(img: Image.Image) -> float:
    # High-frequency energy via Laplacian variance, normalized to 0-1 range.
    # Calibration constant ~800 maps typical grain variance to ~0.35-0.5 on noisy film.
    _GRAIN_NORM = 800.0
    try:
        from scipy.ndimage import laplace
        lap = laplace(np.asarray(img, dtype=np.float32))
        return float(min(1.0, np.var(lap) / _GRAIN_NORM))
    except Exception:
        arr = np.asarray(img, dtype=np.float32)
        lap = (arr[:-2, :] - 2 * arr[1:-1, :] + arr[2:, :])
        return float(min(1.0, np.var(lap) / _GRAIN_NORM))

def _entropy(img: Image.Image) -> float:
    arr = np.asarray(img, dtype=np.uint8)
    hist, _ = np.histogram(arr, bins=256, range=(0,255))
    p = hist / max(1, hist.sum())
    nz = p[p>0]
    return float(-(nz * np.log2(nz)).sum())

def _sparsity(img: Image.Image) -> float:
    arr = np.asarray(img, dtype=np.uint8)
    return float(np.mean(arr==0) + np.mean(arr==255))

def _edge_strength(img: Image.Image) -> float:
    arr = np.asarray(img, dtype=np.float32)

    kx = np.array([[-1,0,1],[-2,0,2],[-1,0,1]], dtype=np.float32)
    ky = np.array([[ 1,2,1],[ 0,0,0],[-1,-2,-1]], dtype=np.float32)
    from scipy.signal import convolve2d as conv
    try:
        gx = conv(arr, kx, mode="same", boundary="symm")
        gy = conv(arr, ky, mode="same", boundary="symm")
        mag = np.sqrt(gx*gx + gy*gy)
        return float(np.mean(mag))
    except Exception:

        return float(np.mean(np.abs(np.diff(arr, axis=1))))


def _blockiness(img: Image.Image) -> float:
    arr = np.asarray(img, dtype=np.float32)
    # Horizontal/vertical differences (JPEG-block proxy)
    v = np.mean(np.abs(np.diff(arr, axis=0))[:, ::8]) + np.mean(np.abs(np.diff(arr, axis=1))[::8, :])
    return float(v / 255.0)

def _graininess(img: Image.Image) -> float:
    arr = np.asarray(img, dtype=np.float32) / 255.0
    # High-frequency energy via simple Laplacian
    lap = np.abs(arr[:-2,1:-1] - 2*arr[1:-1,1:-1] + arr[2:,1:-1])
    return float(np.mean(lap))

def _text_edge_density(img: Image.Image) -> float:
    # Many thin edges -> high density; screen/UI proxy
    arr = np.asarray(img, dtype=np.uint8)
    try:
        from cv2 import Canny
        edges = Canny(arr, 60, 120)  # type: ignore
        return float(np.mean(edges > 0))
    except Exception:
        # Fallback using simple gradient
        gx = np.abs(np.diff(arr.astype(np.float32), axis=1))
        return float(np.mean(gx > 20.0))

def _temporal_ssim_variance(frames: list[Image.Image]) -> float:
    if len(frames) < 3: return 0.0
    import numpy as _np
    vals = []
    for i in range(1, len(frames)):
        a = _np.asarray(frames[i-1].convert("L"), dtype=_np.float32)
        b = _np.asarray(frames[i].convert("L"), dtype=_np.float32)
        mu_a, mu_b = a.mean(), b.mean()
        sigma_a, sigma_b = a.var(), b.var()
        sigma_ab = ((a - mu_a) * (b - mu_b)).mean()
        L = 255.0; c1 = (0.01*L)**2; c2 = (0.03*L)**2
        den = (mu_a**2 + mu_b**2 + c1) * (sigma_a + sigma_b + c2)
        ssim = 1.0 if den == 0 else float(((2*mu_a*mu_b + c1) * (2*sigma_ab + c2)) / den)
        vals.append(max(0.0, min(1.0, ssim)))
    return float(np.std(vals))

def _motion_mad(frames: list[Image.Image]) -> float:
    if len(frames) < 2: return 0.0
    vals = []
    for i in range(1, len(frames)):
        a = np.asarray(frames[i-1], dtype=np.float32)
        b = np.asarray(frames[i], dtype=np.float32)
        vals.append(np.mean(np.abs(a - b)) / 255.0)
    return float(np.mean(vals))

def _scene_change_rate(path: str) -> float:
    """
    Uses ffmpeg's scene detect to approximate number of hard cuts per second.
    """
    try:
        # Analyze at low scale for speed
        cmd = [FFMPEG, "-v", "error", "-i", path,
               "-filter_complex", "select='gt(scene,0.4)',metadata=print",
               "-f", "null", "-"]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        cuts = sum(1 for line in (p.stderr or "").splitlines() if "scene_score" in line.lower())
        meta = probe_media(path)
        dur = _safe_float(meta.get("format", {}).get("duration"), 0.0)
        if dur <= 0: return 0.0
        return float(cuts / max(1.0, dur))
    except Exception:
        return 0.0

def _banding_risk(frames: list[Image.Image]) -> float:
    if not frames: return 0.0
    risk = 0.0
    for f in frames:
        arr = np.asarray(f, dtype=np.uint8)
        hist, _ = np.histogram(arr, bins=256, range=(0, 255))
        # Large gaps in histogram buckets imply banding risk
        zero_bins = np.mean(hist == 0)
        risk += float(zero_bins)
    risk /= float(len(frames))
    return float(min(1.0, max(0.0, (risk - 0.2) / 0.5)))


def extract_media_features(path: str) -> Dict[str, Any]:
    if not _bc_cache_disabled():
        sig = _bc_file_sig(path)
        # v3: adds banding_risk — old v2 entries lack it and would keep
        # silently feeding 0.0 to every consumer (ai_advisor, encoder_profiles,
        # preproc deband gate, outcome_ledger), so bump to force recompute.
        cp = _bc_cache_path('features_v3', sig)
        cached = _bc_cache_load_json(cp)
        if isinstance(cached, dict) and cached.get('width') is not None:
            return cached
    info = probe_media(path)
    fmt = info.get("format", {})
    streams = info.get("streams", [])
    vstream = next((s for s in streams if s.get("codec_type")=="video"), {})
    astream = next((s for s in streams if s.get("codec_type")=="audio"), {})

    width  = _safe_int(vstream.get("width"), 0)
    height = _safe_int(vstream.get("height"), 0)
    fps    = _frm_rate_to_float(vstream.get("avg_frame_rate") or vstream.get("r_frame_rate"))
    dur    = _safe_float(fmt.get("duration") or vstream.get("duration") or 0.0)
    vbr    = _safe_float(vstream.get("bit_rate") or 0.0)
    abr    = _safe_float(astream.get("bit_rate") or 0.0)
    nb_frames = _safe_int(vstream.get("nb_frames") or 0)
    pix_fmt = (vstream.get("pix_fmt") or "").lower()
    color_range = (vstream.get("color_range") or "").lower()
    color_prim  = (vstream.get("color_primaries") or "").lower()
    profile     = (vstream.get("profile") or "").lower()
    codec_name  = (vstream.get("codec_name") or "").lower()

    frames, decode_s = _sample_frames(path, num=16)
    ent = [_entropy(f) for f in frames] if frames else [0.0]
    edg = [_edge_strength(f) for f in frames] if frames else [0.0]
    spz = [_sparsity(f) for f in frames] if frames else [0.0]
    blk = [_blockiness(f) for f in frames] if frames else [0.0]
    grn = [_graininess(f) for f in frames] if frames else [0.0]
    ted = [_text_edge_density(f) for f in frames] if frames else [0.0]

    # HDR detection: 10/12-bit pixel formats, BT.2020 primaries, or HDR transfer functions.
    _hdr_transfers = {"smpte2084", "arib-std-b67", "smpte428", "bt2020-10", "bt2020-12"}
    _hdr_primaries = {"bt2020", "bt2020nc", "bt2020c"}
    is_hdr = bool(
        any(x in pix_fmt for x in ("10le", "10be", "12le", "12be", "p010", "p016")) or
        color_prim in _hdr_primaries or
        (vstream.get("color_transfer") or "").lower() in _hdr_transfers
    )

    features = {
        "width": width, "height": height, "fps": fps, "duration": dur,
        "input_v_bps": vbr, "input_a_bps": abr, "nb_frames": nb_frames,
        "pix_fmt": pix_fmt, "color_range": color_range, "color_primaries": color_prim,
        "profile": profile, "codec_name": codec_name,
        "is_hdr": bool(is_hdr),
        "entropy_mean": float(np.mean(ent)),
        "entropy_p95": float(np.percentile(ent, 95)) if len(ent)>1 else float(ent[0]),
        "edge_mean": float(np.mean(edg)),
        "edge_p95": float(np.percentile(edg, 95)) if len(edg)>1 else float(edg[0]),
        "sparsity_mean": float(np.mean(spz)),
        # New:
        "blockiness": float(np.mean(blk)),
        "graininess": float(np.mean(grn)),
        "text_edge_density": float(np.mean(ted)),
        "temporal_ssim_std": float(_temporal_ssim_variance(frames)),
        "motion_mad": float(_motion_mad(frames)),
        "scene_rate": float(_scene_change_rate(path)),
        "banding_risk": float(_banding_risk(frames)),
    }

    # Derive a coarse "spatial_complexity" for legacy callers
    features["spatial_complexity"] = float(np.clip(
        0.8 * features["entropy_mean"]/6.5 + 0.2 * features["edge_mean"]/8.0, 0.0, 10.0
    ))

    if not _bc_cache_disabled():
        try:
            _bc_cache_save_json(cp, features)
        except Exception:
            pass

    return features





class ComplexityAdvisor:
    """
    Content-difficulty advisor. The old version looked ONLY at spatial
    complexity (entropy/edges of a handful of frames) — a 4K action clip with
    flat-ish frames but violent motion was rated "easy" and had its bit budget
    CUT. Difficulty now blends spatial detail, temporal change, scene-cut rate
    and grain into one score, with the source's own bits-per-pixel as a sanity
    prior (upstream encoders already "voted" on how hard this content is).
    """

    def __init__(self):
        pass

    def advise(self, feats: Dict[str, Any]) -> Tuple[float, int, int]:
        sc     = float(feats.get("spatial_complexity", 5.5) or 5.5)     # 0..10
        motion = float(feats.get("motion_mad", 0.0) or 0.0)             # spread-sample MAD, ~0..0.35
        cuts   = float(feats.get("scene_rate", 0.0) or 0.0)             # hard cuts / s
        grain  = float(feats.get("graininess", 0.0) or 0.0)
        fps    = float(feats.get("fps", 30.0) or 30.0)
        w      = int(feats.get("width", 1280) or 1280)
        h      = int(feats.get("height", 720) or 720)

        # Source bits-per-pixel-per-frame: how many bits the ORIGINAL spends.
        src_bpp = 0.0
        try:
            v_bps = float(feats.get("input_v_bps", 0.0) or 0.0)
            if v_bps > 0 and w > 0 and h > 0:
                src_bpp = v_bps / (float(w * h) * max(12.0, min(120.0, fps)))
        except Exception:
            src_bpp = 0.0

        difficulty = (
            0.40 * min(1.0, sc / 8.0)
            + 0.30 * min(1.0, motion / 0.18)
            + 0.10 * min(1.0, cuts / 0.5)
            + 0.10 * min(1.0, grain * 8.0)
            + 0.10 * min(1.0, src_bpp / 0.15)
        )
        difficulty = max(0.0, min(1.0, difficulty))

        # Budget multiplier 0.92..1.35 and CRF bias -3..+1 from one signal.
        scale = 0.92 + 0.43 * difficulty
        if difficulty >= 0.75:  crf_bias = -3
        elif difficulty >= 0.55: crf_bias = -2
        elif difficulty >= 0.40: crf_bias = -1
        elif difficulty <= 0.20: crf_bias = +1
        else: crf_bias = 0

        if fps >= 50:  scale *= 1.08
        elif fps <= 24: scale *= 0.97
        if w >= 3840:  scale *= 1.06
        elif w >= 1920: scale *= 1.03

        audio_min = 96 if difficulty >= 0.55 else (80 if difficulty >= 0.35 else 64)
        return float(scale), int(crf_bias), int(audio_min)

def analyze_and_advise(path: str) -> Tuple[Dict[str, Any], float, int, int]:
    feats = extract_media_features(path)
    scale, bias, audio_min = ComplexityAdvisor().advise(feats)
    return feats, scale, bias, audio_min
