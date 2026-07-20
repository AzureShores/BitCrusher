from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


@dataclass
class VideoMetrics:
    path: str
    duration_s: float
    fps: float
    width: int
    height: int
    codec: str
    video_bitrate_kbps: float
    audio_bitrate_kbps: float
    bits_per_pixel_frame: float
    sharpness: float
    blockiness: float
    noise: float
    clipping_ratio: float
    contrast_std: float


def run_ffprobe_json(video_path: str) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        video_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for '{video_path}': {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_video_stream(streams: list[dict[str, Any]]) -> dict[str, Any]:
    for stream in streams:
        if stream.get("codec_type") == "video":
            return stream
    raise RuntimeError("No video stream found")


def get_audio_stream(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    for stream in streams:
        if stream.get("codec_type") == "audio":
            return stream
    return None


def parse_video_fps(stream: dict[str, Any]) -> float:
    frame_rate_raw = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    if isinstance(frame_rate_raw, str) and "/" in frame_rate_raw:
        num, den = frame_rate_raw.split("/", 1)
        n = safe_float(num, 0.0)
        d = safe_float(den, 1.0)
        if d != 0:
            return n / d
    return safe_float(frame_rate_raw, 0.0)


def frame_indexes(frame_count: int, sample_frames: int) -> list[int]:
    if frame_count <= 0:
        return []
    n = max(1, min(sample_frames, frame_count))
    if n == 1:
        return [frame_count // 2]
    positions = np.linspace(0, frame_count - 1, n)
    return [int(round(x)) for x in positions]


def variance_of_laplacian(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def estimate_noise(gray: np.ndarray) -> float:
    denoised = cv2.GaussianBlur(gray, (3, 3), 0)
    residual = gray.astype(np.float32) - denoised.astype(np.float32)
    return float(np.std(residual))


def estimate_blockiness(gray: np.ndarray, block_size: int = 8) -> float:
    h, w = gray.shape
    if h < block_size * 2 or w < block_size * 2:
        return 0.0

    diff_h = np.abs(np.diff(gray.astype(np.float32), axis=1))
    diff_v = np.abs(np.diff(gray.astype(np.float32), axis=0))

    bcols = [c for c in range(block_size - 1, w - 1, block_size)]
    brows = [r for r in range(block_size - 1, h - 1, block_size)]

    if not bcols or not brows:
        return 0.0

    edge_h = float(np.mean(diff_h[:, bcols]))
    edge_v = float(np.mean(diff_v[brows, :]))

    non_bcols = [c for c in range(w - 1) if (c + 1) % block_size != 0]
    non_brows = [r for r in range(h - 1) if (r + 1) % block_size != 0]

    base_h = float(np.mean(diff_h[:, non_bcols])) if non_bcols else edge_h
    base_v = float(np.mean(diff_v[non_brows, :])) if non_brows else edge_v

    return max(0.0, ((edge_h - base_h) + (edge_v - base_v)) / 2.0)


def clipping_ratio(gray: np.ndarray) -> float:
    low = np.count_nonzero(gray <= 5)
    high = np.count_nonzero(gray >= 250)
    total = gray.size
    if total == 0:
        return 0.0
    return float((low + high) / total)


def extract_visual_metrics(video_path: str, sample_frames: int) -> dict[str, float]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video '{video_path}'")

    frame_count = safe_int(cap.get(cv2.CAP_PROP_FRAME_COUNT), 0)
    picks = frame_indexes(frame_count, sample_frames)
    if not picks:
        picks = list(range(min(sample_frames, 120)))

    sharpness_values: list[float] = []
    blockiness_values: list[float] = []
    noise_values: list[float] = []
    clipping_values: list[float] = []
    contrast_values: list[float] = []

    for idx in picks:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharpness_values.append(variance_of_laplacian(gray))
        blockiness_values.append(estimate_blockiness(gray))
        noise_values.append(estimate_noise(gray))
        clipping_values.append(clipping_ratio(gray))
        contrast_values.append(float(np.std(gray.astype(np.float32))))

    cap.release()

    if not sharpness_values:
        raise RuntimeError(f"Could not sample frames from '{video_path}'")

    return {
        "sharpness": float(statistics.fmean(sharpness_values)),
        "blockiness": float(statistics.fmean(blockiness_values)),
        "noise": float(statistics.fmean(noise_values)),
        "clipping_ratio": float(statistics.fmean(clipping_values)),
        "contrast_std": float(statistics.fmean(contrast_values)),
    }


def extract_metrics(video_path: str, sample_frames: int) -> VideoMetrics:
    probe = run_ffprobe_json(video_path)
    streams = probe.get("streams", [])
    fmt = probe.get("format", {})

    vstream = get_video_stream(streams)
    astream = get_audio_stream(streams)

    width = safe_int(vstream.get("width"), 0)
    height = safe_int(vstream.get("height"), 0)
    fps = parse_video_fps(vstream)
    duration_s = safe_float(vstream.get("duration"), safe_float(fmt.get("duration"), 0.0))

    v_bitrate = safe_float(vstream.get("bit_rate"), 0.0)
    if v_bitrate <= 0.0:
        total_bitrate = safe_float(fmt.get("bit_rate"), 0.0)
        v_bitrate = max(0.0, total_bitrate - safe_float(astream.get("bit_rate"), 0.0) if astream else total_bitrate)

    a_bitrate = safe_float(astream.get("bit_rate"), 0.0) if astream else 0.0

    denominator = fps * max(width * height, 1)
    bppf = (v_bitrate / denominator) if denominator > 0 else 0.0

    visual = extract_visual_metrics(video_path, sample_frames)

    return VideoMetrics(
        path=video_path,
        duration_s=duration_s,
        fps=fps,
        width=width,
        height=height,
        codec=str(vstream.get("codec_name", "unknown")),
        video_bitrate_kbps=v_bitrate / 1000.0,
        audio_bitrate_kbps=a_bitrate / 1000.0,
        bits_per_pixel_frame=bppf,
        sharpness=visual["sharpness"],
        blockiness=visual["blockiness"],
        noise=visual["noise"],
        clipping_ratio=visual["clipping_ratio"],
        contrast_std=visual["contrast_std"],
    )


def psnr_from_gray(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))
    if mse <= 1e-12:
        return 100.0
    return 20.0 * math.log10(255.0 / math.sqrt(mse))


def ssim_from_gray(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    mu_a, mu_b = float(a.mean()), float(b.mean())
    sigma_a, sigma_b = float(a.var()), float(b.var())
    sigma_ab = float(((a - mu_a) * (b - mu_b)).mean())
    l = 255.0
    c1 = (0.01 * l) ** 2
    c2 = (0.03 * l) ** 2
    den = (mu_a**2 + mu_b**2 + c1) * (sigma_a + sigma_b + c2)
    if den == 0:
        return 1.0
    ssim = ((2 * mu_a * mu_b + c1) * (2 * sigma_ab + c2)) / den
    return float(max(0.0, min(1.0, ssim)))


def compare_to_original(original_path: str, candidate_path: str, sample_frames: int) -> dict[str, float]:
    cap_o = cv2.VideoCapture(original_path)
    cap_c = cv2.VideoCapture(candidate_path)
    if not cap_o.isOpened() or not cap_c.isOpened():
        return {"psnr_mean": 0.0, "ssim_mean": 0.0, "content_distance": 999.0}

    count_o = safe_int(cap_o.get(cv2.CAP_PROP_FRAME_COUNT), 0)
    count_c = safe_int(cap_c.get(cv2.CAP_PROP_FRAME_COUNT), 0)
    n = min(count_o, count_c)
    picks = frame_indexes(n, max(12, sample_frames)) if n > 0 else []
    if not picks:
        picks = list(range(12))

    psnr_vals: list[float] = []
    ssim_vals: list[float] = []
    dist_vals: list[float] = []

    for idx in picks:
        cap_o.set(cv2.CAP_PROP_POS_FRAMES, idx)
        cap_c.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok_o, fo = cap_o.read()
        ok_c, fc = cap_c.read()
        if not ok_o or not ok_c or fo is None or fc is None:
            continue

        base_h, base_w = fo.shape[0], fo.shape[1]
        if fc.shape[0] != base_h or fc.shape[1] != base_w:
            fc = cv2.resize(fc, (base_w, base_h), interpolation=cv2.INTER_AREA)

        go = cv2.cvtColor(fo, cv2.COLOR_BGR2GRAY)
        gc = cv2.cvtColor(fc, cv2.COLOR_BGR2GRAY)

        psnr_vals.append(psnr_from_gray(go, gc))
        ssim_vals.append(ssim_from_gray(go, gc))

        go_s = cv2.resize(go, (160, 90), interpolation=cv2.INTER_AREA)
        gc_s = cv2.resize(gc, (160, 90), interpolation=cv2.INTER_AREA)
        dist_vals.append(float(np.mean(np.abs(go_s.astype(np.float32) - gc_s.astype(np.float32)))))

    cap_o.release()
    cap_c.release()

    if not psnr_vals:
        return {"psnr_mean": 0.0, "ssim_mean": 0.0, "content_distance": 999.0}

    return {
        "psnr_mean": float(statistics.fmean(psnr_vals)),
        "ssim_mean": float(statistics.fmean(ssim_vals)),
        "content_distance": float(statistics.fmean(dist_vals)) if dist_vals else 999.0,
    }


def confidence_from_distance(distance: float) -> str:
    if distance < 12:
        return "high"
    if distance < 24:
        return "medium"
    return "low"


def normalize(values: dict[str, float], higher_is_better: bool) -> dict[str, float]:
    out: dict[str, float] = {}
    nums = list(values.values())
    lo = min(nums)
    hi = max(nums)
    if hi - lo < 1e-12:
        for k in values:
            out[k] = 50.0
        return out
    for key, val in values.items():
        raw = (val - lo) / (hi - lo)
        score = raw if higher_is_better else (1.0 - raw)
        out[key] = float(100.0 * score)
    return out


def as_dict(m: VideoMetrics) -> dict[str, Any]:
    return {
        "path": m.path,
        "duration_s": m.duration_s,
        "fps": m.fps,
        "width": m.width,
        "height": m.height,
        "codec": m.codec,
        "video_bitrate_kbps": m.video_bitrate_kbps,
        "audio_bitrate_kbps": m.audio_bitrate_kbps,
        "bits_per_pixel_frame": m.bits_per_pixel_frame,
        "sharpness": m.sharpness,
        "blockiness": m.blockiness,
        "noise": m.noise,
        "clipping_ratio": m.clipping_ratio,
        "contrast_std": m.contrast_std,
    }


def rank_candidates(original: VideoMetrics, candidates: list[VideoMetrics], refs: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    values_by_metric: dict[str, dict[str, float]] = {
        "psnr_mean": {},
        "ssim_mean": {},
        "content_distance": {},
        "sharpness": {},
        "blockiness": {},
        "noise": {},
        "clipping_ratio": {},
        "bits_per_pixel_frame": {},
        "video_bitrate_kbps": {},
    }

    for c in candidates:
        key = c.path
        values_by_metric["psnr_mean"][key] = refs[key]["psnr_mean"]
        values_by_metric["ssim_mean"][key] = refs[key]["ssim_mean"]
        values_by_metric["content_distance"][key] = refs[key]["content_distance"]
        values_by_metric["sharpness"][key] = c.sharpness
        values_by_metric["blockiness"][key] = c.blockiness
        values_by_metric["noise"][key] = c.noise
        values_by_metric["clipping_ratio"][key] = c.clipping_ratio
        values_by_metric["bits_per_pixel_frame"][key] = c.bits_per_pixel_frame
        values_by_metric["video_bitrate_kbps"][key] = c.video_bitrate_kbps

    scores_by_metric = {
        "psnr_mean": normalize(values_by_metric["psnr_mean"], higher_is_better=True),
        "ssim_mean": normalize(values_by_metric["ssim_mean"], higher_is_better=True),
        "content_distance": normalize(values_by_metric["content_distance"], higher_is_better=False),
        "sharpness": normalize(values_by_metric["sharpness"], higher_is_better=True),
        "blockiness": normalize(values_by_metric["blockiness"], higher_is_better=False),
        "noise": normalize(values_by_metric["noise"], higher_is_better=False),
        "clipping_ratio": normalize(values_by_metric["clipping_ratio"], higher_is_better=False),
        "bits_per_pixel_frame": normalize(values_by_metric["bits_per_pixel_frame"], higher_is_better=True),
        "video_bitrate_kbps": normalize(values_by_metric["video_bitrate_kbps"], higher_is_better=True),
    }

    weights = {
        "psnr_mean": 0.28,
        "ssim_mean": 0.26,
        "content_distance": 0.08,
        "sharpness": 0.10,
        "blockiness": 0.10,
        "noise": 0.08,
        "clipping_ratio": 0.05,
        "bits_per_pixel_frame": 0.03,
        "video_bitrate_kbps": 0.02,
    }

    ranking: list[dict[str, Any]] = []
    for c in candidates:
        key = c.path
        total = 0.0
        metric_scores: dict[str, float] = {}
        for metric, w in weights.items():
            s = scores_by_metric[metric][key]
            metric_scores[metric] = s
            total += s * w

        ranking.append(
            {
                "path": c.path,
                "total_score": total,
                "confidence": confidence_from_distance(refs[key]["content_distance"]),
                "ref_metrics": refs[key],
                "raw_metrics": as_dict(c),
                "normalized_scores": metric_scores,
            }
        )

    ranking.sort(key=lambda x: x["total_score"], reverse=True)
    return ranking


def print_summary(original: VideoMetrics, ranking: list[dict[str, Any]]) -> None:
    print("=== Multi-Video Quality Comparison (Original-Referenced) ===")
    print(f"Original: {original.path}")
    print()
    print("Ranking (best to worst):")

    for idx, item in enumerate(ranking, start=1):
        p = item["path"]
        ref = item["ref_metrics"]
        print(
            f"{idx}. {Path(p).name} | score={item['total_score']:.2f} | "
            f"PSNR={ref['psnr_mean']:.2f} | SSIM={ref['ssim_mean']:.4f} | "
            f"distance={ref['content_distance']:.2f} | confidence={item['confidence']}"
        )

    print()
    print("Notes:")
    print("- This is objective scoring based on measurable metrics.")
    print("- Best reliability is when all compared files are the same source content as the original.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare multiple videos against an original and rank quality objectively."
    )
    parser.add_argument("original", type=str, help="Path to original/reference video")
    parser.add_argument(
        "videos",
        nargs="+",
        type=str,
        help="One or more videos to compare against the original",
    )
    parser.add_argument(
        "--sample-frames",
        type=int,
        default=90,
        help="Number of sampled frames per video for analysis (default: 90)",
    )
    parser.add_argument(
        "--report-json",
        type=str,
        default="",
        help="Optional output path for full JSON report",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    original_path = str(Path(args.original).expanduser().resolve())
    candidate_paths = [str(Path(v).expanduser().resolve()) for v in args.videos]

    all_paths = [original_path] + candidate_paths
    missing = [p for p in all_paths if not Path(p).exists()]
    if missing:
        print("Error: the following input files do not exist:", file=sys.stderr)
        for m in missing:
            print(f"- {m}", file=sys.stderr)
        return 2

    try:
        original_metrics = extract_metrics(original_path, args.sample_frames)
        candidate_metrics = [extract_metrics(p, args.sample_frames) for p in candidate_paths]
    except FileNotFoundError:
        print("Error: ffprobe was not found on PATH. Install FFmpeg and try again.", file=sys.stderr)
        return 3
    except ImportError:
        print("Error: missing dependencies. Install with: pip install -r Comparison_testing/requirements.txt", file=sys.stderr)
        return 4
    except Exception as exc:
        print(f"Error while extracting metrics: {exc}", file=sys.stderr)
        return 5

    refs: dict[str, dict[str, float]] = {}
    for c in candidate_metrics:
        refs[c.path] = compare_to_original(original_path, c.path, args.sample_frames)

    ranking = rank_candidates(original_metrics, candidate_metrics, refs)
    print_summary(original_metrics, ranking)

    if args.report_json:
        report = {
            "original": as_dict(original_metrics),
            "candidates": [as_dict(c) for c in candidate_metrics],
            "ranking": ranking,
        }
        out_path = Path(args.report_json).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nSaved JSON report: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
