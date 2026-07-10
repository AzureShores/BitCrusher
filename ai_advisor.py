

from __future__ import annotations
import os, json, time, math, threading, logging
from pathlib import Path
from typing import Dict, Tuple, Optional

import numpy as np

try:

    from sklearn.linear_model import Ridge
    _HAS_SK = True
except Exception:
    _HAS_SK = False

import importlib

from ml_heuristics import extract_media_features  # uses ffprobe/ffmpeg and PIL/numpy under the hood
# ---------- Content scoring & scene planning (new) ----------
from ml_heuristics import analyze_scenes  # scene cuts + per-scene difficulty + zones

class DifficultyScore:
    """
    Aggregate, explainable difficulty score for a title.
    0.0 (trivial) … 1.0 (extremely hard). Also assigns a content class and a quality floor.
    """
    __slots__ = ("score","klass","quality_floor","notes")

    def __init__(self, score: float, klass: str, quality_floor: float, notes: dict[str,float]):
        self.score = float(max(0.0, min(1.0, score)))
        self.klass = str(klass)
        self.quality_floor = float(max(0.90, min(0.985, quality_floor)))
        self.notes = dict(notes)

def _difficulty_from_feats(feats: Dict[str, float]) -> DifficultyScore:
    # Normalized feature taps
    ent95 = float(feats.get("entropy_p95", 6.0))/8.0       # 0..~1
    edge95= float(feats.get("edge_p95", 6.0))/8.0
    tvar  = float(feats.get("temporal_ssim_std", 0.03))*6  # 0..~1
    mad   = float(feats.get("motion_mad", 0.02))*6         # 0..~1
    text  = float(feats.get("text_edge_density", 0.0))*3   # 0..~1
    grain = float(feats.get("graininess", 0.0))*0.8        # 0..~1
    band  = float(feats.get("banding_risk", 0.0))          # 0..1
    blk   = float(feats.get("blockiness", 0.0))/24.0       # scaled

    # Weighted blend (emphasize what humans hate)
    motion = 0.35*max(tvar, mad)
    detail = 0.30*max(ent95, edge95)
    ui_pen = 0.25*text
    artifacts = 0.10*max(grain, blk) + 0.08*band

    raw = motion + detail + ui_pen + artifacts
    score = max(0.0, min(1.0, raw))

    # Classify
    if text > 0.35 and edge95 > 0.7:
        klass = "screen_ui"
        qfloor = 0.966 if band < 0.35 else 0.972
    elif grain > 0.35 and ent95 > 0.65:
        klass = "film_grain"
        qfloor = 0.952 if band < 0.35 else 0.960
    elif motion > 0.55:
        klass = "sports_action"
        qfloor = 0.948
    elif ent95 < 0.45 and edge95 < 0.40:
        klass = "flat_camera"
        qfloor = 0.940
    else:
        klass = "general"
        qfloor = 0.955
    notes = {"motion":motion,"detail":detail,"text":text,"grain":grain,"band":band,"blk":blk}
    return DifficultyScore(score=score, klass=klass, quality_floor=qfloor, notes=notes)

def _export_scene_env(scene_plan: Dict[str, Any]) -> None:
    """
    Export per-scene zones/GOP/AQ as environment variables so the encoder builder can pick them up.
    """
    try:
        zones = scene_plan.get("zones_str","")
        gop   = int(scene_plan.get("gop", 0) or 0)
        aq    = float(scene_plan.get("aq_strength", 1.0))
        os.environ["BC_ZONE_PARAMS"] = zones
        if gop > 0: os.environ["BC_GOP"] = str(gop)
        os.environ["BC_AQ_STRENGTH"] = f"{aq:.2f}"
    except Exception:
        pass
# ---------- end new block ----------




LOG = logging.getLogger("BitCrusher.AIAdvisor")

_APP_DIR = Path(__file__).resolve().parent
_MODEL_DIR = _APP_DIR / "user_settings" / "advisor"
_MODEL_DIR.mkdir(parents=True, exist_ok=True)
_MODEL_PATH = _MODEL_DIR / "quality_model.json"
_DATA_CSV   = _MODEL_DIR / "samples.csv"


def _feat_vec(feats: Dict[str, float], v_bps: int, a_bps: int) -> np.ndarray:
    """
    Feature vector for quality prediction and bitrate advice.
    Includes spatial + temporal + content flags.
    """
    w   = float(feats.get("width", 0) or 0.0)
    h   = float(feats.get("height", 0) or 0.0)
    fps = float(feats.get("fps", 0) or 0.0)
    sc  = float(feats.get("spatial_complexity", 5.5))
    ent = float(feats.get("entropy_p95", 6.0))
    edg = float(feats.get("edge_p95", 6.0))
    spz = float(feats.get("sparsity_mean", 0.10))
    # New temporal/content features (added)
    tssim_std = float(feats.get("temporal_ssim_std", 0.03))
    motion_mad = float(feats.get("motion_mad", 0.02))
    scene_rate = float(feats.get("scene_rate", 0.0))
    banding_risk = float(feats.get("banding_risk", 0.0))  # 0..1
    text_edge_density = float(feats.get("text_edge_density", 0.0))
    graininess = float(feats.get("graininess", 0.0))
    blockiness = float(feats.get("blockiness", 0.0))
    bit_depth10 = int(str(feats.get("pix_fmt","")).endswith("10le"))
    codec_id = (feats.get("codec_name") or "").lower()
    codec_x264 = int("264" in codec_id)
    codec_x265 = int("265" in codec_id or "hevc" in codec_id)
    codec_av1  = int("av1" in codec_id)

    area = max(1.0, w * h)
    fps  = max(1.0, fps)
    vbppf = float(v_bps) / (area * fps)        # video bits per pixel per frame
    abppf = float(a_bps) / max(1.0, fps)       # cheap audio density proxy

    return np.array([
        1.0,
        w, h, fps,
        math.sqrt(area), area,
        sc, ent, edg, spz,
        tssim_std, motion_mad, scene_rate,
        banding_risk, text_edge_density, graininess, blockiness,
        vbppf, math.log1p(vbppf*1e6),
        abppf, math.log1p(abppf),
        int(w >= 1920), int(fps >= 60), int(spz >= 0.2), int(sc >= 7.0),
        bit_depth10, codec_x264, codec_x265, codec_av1
    ], dtype=float)



class _QualityModel:
    """
    Hybrid linear + tiny-MLP predictor.
    If MLP weights are unavailable, falls back to strong analytical baseline.
    """
    def __init__(self):
        self.coef = None         # np.ndarray (linear)
        self.bias = 0.0
        # Optional MLP (one hidden layer, tanh)
        self.mlp = {"W1": None, "b1": None, "W2": None, "b2": None}
        self.lock = threading.Lock()
        self._load()

    def _load(self):
        try:
            if _MODEL_PATH.exists():
                data = json.loads(_MODEL_PATH.read_text(encoding="utf-8"))
                self.bias = float(data.get("bias", 0.0))
                self.coef = np.asarray(data.get("coef", None), dtype=float) if data.get("coef") is not None else None
                mlp = data.get("mlp", {})
                for k in ("W1","b1","W2","b2"):
                    v = mlp.get(k)
                    self.mlp[k] = None if v is None else np.asarray(v, dtype=float)
        except Exception:
            self.coef = None
            self.bias = 0.0
            self.mlp = {"W1": None, "b1": None, "W2": None, "b2": None}

    def _save(self):
        try:
            payload = {
                "bias": float(self.bias),
                "coef": (self.coef.tolist() if self.coef is not None else None),
                "mlp": {k:(None if self.mlp[k] is None else self.mlp[k].tolist()) for k in self.mlp}
            }
            _MODEL_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def purge_incompatible(self, feat_dim: int) -> None:
        """
        If persisted weights don't match the current feature vector length,
        drop them to avoid shape errors on next runs.
        """
        try:
            bad_lin = self.coef is not None and getattr(self.coef, "shape", (0,))[0] != int(feat_dim)
            bad_mlp = self.mlp["W1"] is not None and getattr(self.mlp["W1"], "shape", (0, 0))[1] != int(feat_dim)
            if bad_lin or bad_mlp:
                self.coef = None
                self.mlp = {"W1": None, "b1": None, "W2": None, "b2": None}
                self.bias = 0.0
                # best-effort overwrite on disk so future runs are clean
                self._save()
        except Exception:
            pass


    def _mlp_forward(self, x: np.ndarray) -> float | None:
        W1, b1, W2, b2 = (self.mlp["W1"], self.mlp["b1"], self.mlp["W2"], self.mlp["b2"])
        # Guard against stale/dimension-mismatched weights saved from older versions
        if any(v is None for v in (W1, b1, W2, b2)):
            return None
        try:
            if W1.ndim != 2 or W2.ndim != 2 or b1.ndim != 1 or b2.ndim != 1:
                return None
            if W1.shape[1] != x.shape[0] or W2.shape[1] != W1.shape[0]:
                return None
            h = np.tanh(W1 @ x + b1.reshape(-1))
            y = float((W2 @ h).reshape(-1)[0] + float(b2.reshape(-1)[0]))
            return y
        except Exception:
            return None


    def predict(self, x: np.ndarray) -> float:
        with self.lock:
            # Analytical fallback on vbppf & complexity — always safe
            vbppf = float(x[17]) if len(x) > 17 else 0.05
            sc = float(x[6]) if len(x) > 6 else 5.5
            base = 100.0 - 120.0 * math.exp(-4000.0 * max(1e-8, vbppf))
            if sc >= 7.0:
                base -= max(0.0, 12.0 * (0.10 - vbppf) * 12.0)
            base = max(40.0, min(99.5, base))

            # Ignore incompatible/stale linear weights (older feature dimension)
            coef = self.coef
            if coef is not None:
                try:
                    if coef.ndim != 1 or coef.shape[0] != x.shape[0]:
                        coef = None
                except Exception:
                    coef = None

            lin = (self.bias + float(np.dot(coef, x))) if coef is not None else None
            mlp = self._mlp_forward(x)

            # Blend available heads with confidence weighting
            vals = [v for v in (base, lin, mlp) if v is not None]
            if not vals:
                return float(base)
            if (lin is not None) and (mlp is not None):
                return float(0.2 * base + 0.4 * lin + 0.4 * mlp)
            if lin is not None:
                return float(0.35 * base + 0.65 * lin)
            if mlp is not None:
                return float(0.35 * base + 0.65 * mlp)
            return float(base)


    def fit_incremental(self, X: np.ndarray, y: np.ndarray, alpha: float = 1.25):
        with self.lock:
            try:
                if _HAS_SK:
                    mdl = Ridge(alpha=alpha, fit_intercept=True, random_state=42)
                    mdl.fit(X, y)
                    self.coef = np.asarray(mdl.coef_, dtype=float)
                    self.bias = float(mdl.intercept_)
                else:
                    XtX = X.T @ X
                    I = np.eye(X.shape[1])
                    w = np.linalg.solve(XtX + alpha * I, X.T @ y)
                    self.bias = float(w[0])
                    self.coef = w
                # Opportunistic tiny-MLP init if dimension known
                d = X.shape[1]
                if self.mlp["W1"] is None:
                    h = min(16, max(8, d//2))
                    rng = np.random.default_rng(42)
                    self.mlp["W1"] = (rng.standard_normal((h, d)) * 0.05).astype(float)
                    self.mlp["b1"] = np.zeros((h,), dtype=float)
                    self.mlp["W2"] = (rng.standard_normal((1, h)) * 0.05).astype(float)
                    self.mlp["b2"] = np.zeros((1,), dtype=float)
                self._save()
            except Exception as e:
                LOG.debug("Incremental fit skipped: %r", e)



_MODEL = _QualityModel()

def _get_smart_rate():
    return importlib.import_module("smart_rate")


def choose_bitrates_advised(duration_s: float,
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
                            **_kw) -> Tuple[int, int, float]:
    """
    Advises (video_bps, audio_bps, overshoot) and emits scene plan via ENV:
    - Byte-accurate mux overhead model
    - Difficulty scoring -> quality floor
    - Three-point micro-probe for R-Q solving (smart_rate)
    - Scene-wise bit quotas with x26x 'zones' string (ENV: BC_ZONE_PARAMS)
    """
    from smart_rate import choose_bitrates as _sr_choose, estimate_mux_overhead

    input_path = _kw.get("input_path")
    skip_probe = bool(_kw.get("skip_probe"))

    # Base pick from SmartRate (already includes micro-probe)
    try:
        v_bps, a_bps, ov = _sr_choose(duration_s, target_bytes, encoder, container,
                                      channels, sample_rate, audio_fmt, stats_dir,
                                      width_hint, fps_hint, audio_copy_bps,
                                      input_path=input_path, skip_probe=skip_probe)
    except TypeError:
        # Monkeypatched/legacy choose_bitrates without the new kwargs.
        v_bps, a_bps, ov = _sr_choose(duration_s, target_bytes, encoder, container,
                                      channels, sample_rate, audio_fmt, stats_dir,
                                      width_hint, fps_hint, audio_copy_bps)

    in_path = (str(input_path or "").strip()
               or os.environ.get("BC_CURRENT_INPUT", "").strip())
    if not in_path or not os.path.exists(in_path):
        return int(v_bps), int(a_bps), float(ov)

    # Media features & scoring
    feats = extract_media_features(in_path)
    x = _feat_vec(feats, v_bps, a_bps)
    try: _MODEL.purge_incompatible(len(x))
    except Exception: pass

    q_pred = float(_MODEL.predict(x))
    diff = _difficulty_from_feats(feats)

    try:
        os.environ["BC_CONTENT_CLASS"] = str(getattr(diff, "klass", "") or "")
        # Extended, backward-compatible difficulty signals for codec-aware planning/profiles.
        try:
            os.environ["BC_CONTENT_DIFFICULTY"] = str(float(getattr(diff, "score", 0.0) or 0.0))
        except Exception:
            os.environ["BC_CONTENT_DIFFICULTY"] = "0.0"
        # Grain sensitivity proxy: higher when film/grain-like or noisy sources are detected.
        try:
            g = float(feats.get("graininess", 0.0) or 0.0)
            n = float(feats.get("blockiness", 0.0) or 0.0)
            os.environ["BC_GRAIN_SENSITIVE"] = "1" if (g + n) >= 0.8 else "0"
        except Exception:
            os.environ["BC_GRAIN_SENSITIVE"] = "0"
        # Quality floor hint (0..1): conservative minimum visual quality at tight budgets.
        try:
            d = float(getattr(diff, "score", 0.0) or 0.0)
            os.environ["BC_QUALITY_FLOOR"] = str(max(0.0, min(1.0, 0.35 + 0.35 * d)))
        except Exception:
            os.environ["BC_QUALITY_FLOOR"] = "0.35"

        os.environ["BC_BANDING_RISK"] = str(float(feats.get("banding_risk", 0.0) or 0.0))
        os.environ["BC_TEXT_DENSITY"] = str(float(feats.get("text_edge_density", 0.0) or 0.0))
        os.environ["BC_GRAININESS"] = str(float(feats.get("graininess", 0.0) or 0.0))
        os.environ["BC_BLOCKINESS"] = str(float(feats.get("blockiness", 0.0) or 0.0))
    except Exception:
        pass


    # Quality floor: if predicted < floor, push video bps up by 4–10%
    floor = float(diff.quality_floor)
    # High banding risk needs more bits to preserve smooth gradients — raise the floor slightly.
    banding_risk_val = float(feats.get("banding_risk", 0.0) or 0.0)
    if banding_risk_val > 0.45:
        floor = min(0.985, floor + 0.008)
    if q_pred/100.0 < floor:
        bump = min(0.10, max(0.04, (floor - q_pred/100.0) * 0.6))
        v_bps = int(v_bps * (1.0 + bump))

    # Scene analysis → zones + suggested GOP/AQ (exported via ENV)
    try:
        plan = analyze_scenes(in_path, encoder=encoder, fps_hint=fps_hint, difficulty=diff.score)
        _export_scene_env(plan)
    except Exception:
        pass

    # Byte-accurate mux overhead reserve (final correction)
    try:
        fps = float(feats.get("fps") or (fps_hint or 30.0))
        keyint = int(os.environ.get("BC_GOP", "0") or 0) or (60 if fps >= 60 else 120)
        tracks = 2 if a_bps > 0 else 1
        mux_bytes = estimate_mux_overhead(duration_s=duration_s, fps=fps, keyint=keyint,
                                          tracks=tracks, container=container)
        usable = max(1, int(target_bytes - mux_bytes))
        # Re-solve the simple split with updated headroom
        a_bytes = int(duration_s * a_bps / 8)
        if a_bytes >= usable:
            # Audio dominates budget — skip mux correction to avoid setting v_bps negative
            LOG.debug("Mux correction skipped: audio bytes (%d) >= usable budget (%d)", a_bytes, usable)
        else:
            v_bps = int(max(80_000, (usable - a_bytes) * 8 / max(1.0, duration_s) / ov))
    except Exception:
        pass

    return int(v_bps), int(a_bps), float(ov)


def _set_current_input(input_path: str | None) -> None:
    """
    Remember current input for advisor/SmartRate feature extraction.
    """
    try:
        if input_path and os.path.exists(input_path):
            os.environ["BC_CURRENT_INPUT"] = os.path.abspath(input_path)
        else:
            os.environ.pop("BC_CURRENT_INPUT", None)
    except Exception:
        pass

def advisor_preview_for_gui(path: str, encoder: str, target_bytes: int,
                            duration_s: float, channels: int = 2, sample_rate: int = 48000,
                            audio_fmt: str = "aac", container: str = "mp4") -> Dict[str, float]:
    """
    Lightweight preview for the GUI panel.
    """
    _set_current_input(path)
    v_bps, a_bps, ov = choose_bitrates_advised(
        duration_s, target_bytes, encoder, container, channels, sample_rate, audio_fmt
    )
    try:
        feats = extract_media_features(path)
        x = _feat_vec(feats, v_bps, a_bps)
        q = float(_MODEL.predict(x))
    except Exception:
        q = 88.0
    return {"v_bps": float(v_bps), "a_bps": float(a_bps), "overshoot": float(ov), "pred_quality": float(q)}


def post_encode_learn(input_path: str,
                      output_path: str,
                      encoder: str,
                      target_bytes: int,
                      actual_bytes: int,
                      a_bps_used: int,
                      v_bps_used: int) -> None:
    
    if not input_path or not os.path.exists(input_path):
        return
    try:
        feats = extract_media_features(input_path)
        w = float(feats.get("width", 0.0))
        h = float(feats.get("height", 0.0))
        fps = float(feats.get("fps", 0.0))
        area = max(1.0, w * h)
        fps  = max(1.0, fps)
        vbppf = float(v_bps_used) / (area * fps)
        sc = float(feats.get("spatial_complexity", 5.5))

        q = 100.0 - 120.0 * math.exp(-4000.0 * max(1e-8, vbppf))
        if sc >= 7.0:
            q -= max(0.0, 12.0 * (0.10 - vbppf) * 12.0)
        q = float(max(40.0, min(99.8, q)))

        _CSV_HEADER = (
            "ts,encoder,width,height,fps,spatial_complexity,entropy_p95,edge_p95,sparsity_mean,"
            "temporal_ssim_std,motion_mad,scene_rate,banding_risk,text_edge_density,graininess,"
            "blockiness,pix_fmt,codec_name,v_bps,a_bps,quality\n"
        )
        with _DATA_CSV.open("a", encoding="utf-8") as f:
            if _DATA_CSV.stat().st_size == 0:
                f.write(_CSV_HEADER)
            f.write("{ts},{enc},{w},{h},{fps},{sc},{ent},{edg},{spz},{tssim},{mmad},{sr},"
                    "{band},{ted},{grn},{blk},{pf},{cn},{vb},{ab},{q}\n".format(
                ts=int(time.time()), enc=(encoder or "").lower(),
                w=int(w), h=int(h), fps=float(fps), sc=float(sc),
                ent=float(feats.get("entropy_p95", 6.0)),
                edg=float(feats.get("edge_p95", 6.0)),
                spz=float(feats.get("sparsity_mean", 0.10)),
                tssim=float(feats.get("temporal_ssim_std", 0.03)),
                mmad=float(feats.get("motion_mad", 0.02)),
                sr=float(feats.get("scene_rate", 0.0)),
                band=float(feats.get("banding_risk", 0.0)),
                ted=float(feats.get("text_edge_density", 0.0)),
                grn=float(feats.get("graininess", 0.0)),
                blk=float(feats.get("blockiness", 0.0)),
                pf=(feats.get("pix_fmt") or "").replace(",", ""),
                cn=(feats.get("codec_name") or "").replace(",", ""),
                vb=int(v_bps_used), ab=int(a_bps_used), q=q
            ))

        X, y = [], []
        with _DATA_CSV.open("r", encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        for ln in lines[1:][-800:]:  # last 800 samples
            parts = ln.split(",")
            try:
                # Support both old (12-col) and new (21-col) CSV formats
                w = float(parts[2]); h = float(parts[3]); fps = float(parts[4]); sc = float(parts[5])
                ent = float(parts[6]); edg = float(parts[7]); spz = float(parts[8])
                if len(parts) >= 21:
                    tssim = float(parts[9]); mmad = float(parts[10]); srate = float(parts[11])
                    band = float(parts[12]); ted = float(parts[13]); grn = float(parts[14])
                    blk = float(parts[15]); pf = parts[16]; cn = parts[17]
                    vb = float(parts[18]); ab = float(parts[19]); qv = float(parts[20])
                else:
                    tssim = 0.03; mmad = 0.02; srate = 0.0
                    band = 0.0; ted = 0.0; grn = 0.0; blk = 0.0; pf = ""; cn = ""
                    vb = float(parts[9]); ab = float(parts[10]); qv = float(parts[11])
            except Exception:
                continue
            feats2 = {
                "width": w, "height": h, "fps": fps,
                "spatial_complexity": sc, "entropy_p95": ent, "edge_p95": edg, "sparsity_mean": spz,
                "temporal_ssim_std": tssim, "motion_mad": mmad, "scene_rate": srate,
                "banding_risk": band, "text_edge_density": ted, "graininess": grn,
                "blockiness": blk, "pix_fmt": pf, "codec_name": cn,
            }
            X.append(_feat_vec(feats2, vb, ab))
            y.append(qv)
        if X and y:
            X = np.vstack(X); y = np.asarray(y, dtype=float)
            _MODEL.fit_incremental(X, y, alpha=1.4)
    except Exception as e:
        try: LOG.debug("post_encode_learn skipped: %r", e)
        except Exception: pass

def cache_store_advised(base_dir: str, input_path: str, target_mb: int, encoder: str, v_bps: int,
                        width: int, fps: float, final_size: int) -> None:
    try:
        sr = _get_smart_rate()
        sr.cache_store(base_dir, input_path, target_mb, encoder, v_bps, width, fps, final_size)
    finally:
        try:
            post_encode_learn(
                input_path=input_path,
                output_path="",  # optional
                encoder=encoder,
                target_bytes=int(max(1, target_mb) * 1_000_000),
                actual_bytes=int(max(1, final_size)),
                a_bps_used=int(os.environ.get("BC_LAST_A_BPS", "0") or 0),
                v_bps_used=int(v_bps)
            )
        except Exception:
            pass


def cache_lookup_advised(base_dir: str, input_path: str, target_mb: int, encoder: str):
    """Forwarder for smart_rate.cache_lookup (newest prior result or None)."""
    try:
        sr = _get_smart_rate()
        return sr.cache_lookup(base_dir, input_path, target_mb, encoder)
    except Exception:
        return None
