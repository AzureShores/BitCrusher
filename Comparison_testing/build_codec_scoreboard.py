from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}


@dataclass
class CompareResult:
    winner: str
    bc_score: float
    se_score: float
    bc_psnr: float
    se_psnr: float
    bc_ssim: float
    se_ssim: float
    bc_distance: float
    se_distance: float
    bc_confidence: str
    se_confidence: str


def norm_stem(path: Path) -> str:
    s = path.stem.lower().strip()
    for token in (
        "bitcrusher",
        "shutterencoder",
        "_bitcrusher",
        "_shutterencoder",
        "-bitcrusher",
        "-shutterencoder",
    ):
        s = s.replace(token, "")
    return s.replace(" ", "").replace("_", "").replace("-", "")


def video_files(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    return sorted([p for p in path.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS])


def path_eq(a: Path, b: Path) -> bool:
    try:
        return a.resolve().as_posix().lower() == b.resolve().as_posix().lower()
    except Exception:
        return str(a).lower() == str(b).lower()


def pair_outputs(bitcrusher_files: list[Path], shutter_files: list[Path]) -> list[tuple[Path, Path]]:
    if not bitcrusher_files or not shutter_files:
        return []

    pairs: list[tuple[Path, Path]] = []
    se_by_key: dict[str, list[Path]] = {}
    for sf in shutter_files:
        se_by_key.setdefault(norm_stem(sf), []).append(sf)

    used_se: set[Path] = set()

    for bf in bitcrusher_files:
        key = norm_stem(bf)
        candidates = [x for x in se_by_key.get(key, []) if x not in used_se]
        if candidates:
            chosen = candidates[0]
            used_se.add(chosen)
            pairs.append((bf, chosen))

    remaining_b = [b for b in bitcrusher_files if all(b != p[0] for p in pairs)]
    remaining_s = [s for s in shutter_files if s not in used_se]

    if len(remaining_b) == 1 and len(remaining_s) == 1:
        pairs.append((remaining_b[0], remaining_s[0]))
    elif remaining_b and remaining_s and len(remaining_b) == len(remaining_s):
        for bf, sf in zip(sorted(remaining_b), sorted(remaining_s)):
            pairs.append((bf, sf))

    return pairs


def pick_original_file(search_root: Path, bc_file: Path, se_file: Path, original_name: str) -> Path | None:
    roots = [p for p in search_root.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    if not roots:
        return None

    preferred = [p for p in roots if p.stem.lower() == original_name.lower()]
    if preferred:
        return preferred[0]

    # Allow ORIGINAL_caseName naming patterns.
    preferred_prefix = [p for p in roots if p.stem.lower().startswith(original_name.lower() + "_")]
    if len(preferred_prefix) == 1:
        return preferred_prefix[0]

    bc_key = norm_stem(bc_file)
    se_key = norm_stem(se_file)
    for p in roots:
        k = norm_stem(p)
        if k and (k == bc_key or k == se_key):
            return p

    if len(roots) == 1:
        return roots[0]

    return None


def parse_compare_report(report: dict[str, Any], bc_file: Path, se_file: Path) -> CompareResult:
    ranking = report.get("ranking", [])
    if not isinstance(ranking, list) or len(ranking) < 2:
        raise RuntimeError("Invalid ranking payload in comparator report")

    bc_item = None
    se_item = None
    for item in ranking:
        p = Path(str(item.get("path", "")))
        if path_eq(p, bc_file):
            bc_item = item
        elif path_eq(p, se_file):
            se_item = item

    if bc_item is None or se_item is None:
        raise RuntimeError("Could not map comparator ranking rows to BitCrusher/ShutterEncoder files")

    top_path = Path(str(ranking[0].get("path", "")))
    winner = "BitCrusher" if path_eq(top_path, bc_file) else "ShutterEncoder"

    bc_ref = bc_item.get("ref_metrics", {})
    se_ref = se_item.get("ref_metrics", {})

    return CompareResult(
        winner=winner,
        bc_score=float(bc_item.get("total_score", 0.0)),
        se_score=float(se_item.get("total_score", 0.0)),
        bc_psnr=float(bc_ref.get("psnr_mean", 0.0)),
        se_psnr=float(se_ref.get("psnr_mean", 0.0)),
        bc_ssim=float(bc_ref.get("ssim_mean", 0.0)),
        se_ssim=float(se_ref.get("ssim_mean", 0.0)),
        bc_distance=float(bc_ref.get("content_distance", 999.0)),
        se_distance=float(se_ref.get("content_distance", 999.0)),
        bc_confidence=str(bc_item.get("confidence", "unknown")),
        se_confidence=str(se_item.get("confidence", "unknown")),
    )


def run_compare(comparator: Path, original: Path, bc_file: Path, se_file: Path, sample_frames: int) -> CompareResult:
    with tempfile.TemporaryDirectory(prefix="bc_cmp_report_") as td:
        report_path = Path(td) / "report.json"
        cmd = [
            sys.executable,
            str(comparator),
            str(original),
            str(bc_file),
            str(se_file),
            "--sample-frames",
            str(sample_frames),
            "--report-json",
            str(report_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Comparator failed")
        if not report_path.exists():
            raise RuntimeError("Comparator did not produce JSON report")
        data = json.loads(report_path.read_text(encoding="utf-8"))
        return parse_compare_report(data, bc_file, se_file)


def gather_case_dirs(category_dir: Path) -> list[Path]:
    # New preferred layout: <category>/<sample_case>/BitCrusher + ShutterEncoder + ORIGINAL.*
    case_dirs: list[Path] = []
    for d in sorted([p for p in category_dir.iterdir() if p.is_dir()]):
        if d.name in {"BitCrusher", "ShutterEncoder"}:
            continue
        if (d / "BitCrusher").is_dir() and (d / "ShutterEncoder").is_dir():
            case_dirs.append(d)
    return case_dirs


def compare_one_case(
    *,
    codec: str,
    category: str,
    case_name: str,
    case_root: Path,
    comparator: Path,
    sample_frames: int,
    original_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    bc_files = video_files(case_root / "BitCrusher")
    se_files = video_files(case_root / "ShutterEncoder")
    pairs = pair_outputs(bc_files, se_files)

    if not pairs:
        rows.append(
            {
                "codec": codec,
                "category": category,
                "case": case_name,
                "status": "missing_pairs",
                "winner": "",
                "error": "No matchable BitCrusher/ShutterEncoder output pair found",
            }
        )
        return rows

    for bf, sf in pairs:
        original = pick_original_file(case_root, bf, sf, original_name)
        test_name = norm_stem(bf) or bf.stem

        if original is None:
            rows.append(
                {
                    "codec": codec,
                    "category": category,
                    "case": case_name,
                    "test": test_name,
                    "status": "missing_original",
                    "winner": "",
                    "bitcrusher_file": str(bf),
                    "shutterencoder_file": str(sf),
                    "error": "No original video found for this case",
                }
            )
            continue

        try:
            result = run_compare(comparator, original, bf, sf, sample_frames)
            rows.append(
                {
                    "codec": codec,
                    "category": category,
                    "case": case_name,
                    "test": test_name,
                    "status": "ok",
                    "winner": result.winner,
                    "original_file": str(original),
                    "bitcrusher_file": str(bf),
                    "shutterencoder_file": str(sf),
                    "bitcrusher_score": round(result.bc_score, 4),
                    "shutterencoder_score": round(result.se_score, 4),
                    "score_delta_se_minus_bc": round(result.se_score - result.bc_score, 4),
                    "bitcrusher_psnr": round(result.bc_psnr, 4),
                    "shutterencoder_psnr": round(result.se_psnr, 4),
                    "bitcrusher_ssim": round(result.bc_ssim, 6),
                    "shutterencoder_ssim": round(result.se_ssim, 6),
                    "bitcrusher_distance": round(result.bc_distance, 4),
                    "shutterencoder_distance": round(result.se_distance, 4),
                    "bitcrusher_confidence": result.bc_confidence,
                    "shutterencoder_confidence": result.se_confidence,
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "codec": codec,
                    "category": category,
                    "case": case_name,
                    "test": test_name,
                    "status": "compare_error",
                    "winner": "",
                    "original_file": str(original),
                    "bitcrusher_file": str(bf),
                    "shutterencoder_file": str(sf),
                    "error": str(exc),
                }
            )

    return rows


def build_rows(
    root: Path,
    comparator: Path,
    sample_frames: int,
    original_name: str,
    min_samples: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    category_summary: dict[str, dict[str, Any]] = {}

    for codec_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        codec = codec_dir.name
        category_dirs = [
            p for p in codec_dir.iterdir()
            if p.is_dir() and p.name not in {"BitCrusher", "ShutterEncoder"}
        ]

        for category_dir in sorted(category_dirs):
            category = category_dir.name
            key = f"{codec}/{category}"

            case_dirs = gather_case_dirs(category_dir)
            if case_dirs:
                for case_dir in case_dirs:
                    rows.extend(
                        compare_one_case(
                            codec=codec,
                            category=category,
                            case_name=case_dir.name,
                            case_root=case_dir,
                            comparator=comparator,
                            sample_frames=sample_frames,
                            original_name=original_name,
                        )
                    )
            else:
                # Legacy fallback layout: <category>/BitCrusher + <category>/ShutterEncoder + ORIGINAL* in category root
                rows.extend(
                    compare_one_case(
                        codec=codec,
                        category=category,
                        case_name="default",
                        case_root=category_dir,
                        comparator=comparator,
                        sample_frames=sample_frames,
                        original_name=original_name,
                    )
                )

            ok_count = sum(1 for r in rows if r.get("codec") == codec and r.get("category") == category and r.get("status") == "ok")
            category_summary[key] = {
                "codec": codec,
                "category": category,
                "ok_cases": ok_count,
                "min_samples_required": int(min_samples),
                "meets_min_samples": bool(ok_count >= int(min_samples)),
            }

    return rows, category_summary


def write_outputs(
    rows: list[dict[str, Any]],
    category_summary: dict[str, dict[str, Any]],
    csv_path: Path,
    json_path: Path,
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    all_keys: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in all_keys:
                all_keys.append(k)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    summary_by_codec: dict[str, dict[str, int]] = {}
    for r in rows:
        codec = str(r.get("codec", ""))
        if not codec:
            continue
        bucket = summary_by_codec.setdefault(codec, {"BitCrusher": 0, "ShutterEncoder": 0, "errors": 0})
        if r.get("status") != "ok":
            bucket["errors"] += 1
        elif r.get("winner") == "BitCrusher":
            bucket["BitCrusher"] += 1
        elif r.get("winner") == "ShutterEncoder":
            bucket["ShutterEncoder"] += 1

    payload = {
        "summary_by_codec": summary_by_codec,
        "summary_by_codec_category": category_summary,
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build BitCrusher vs ShutterEncoder scoreboard from TESTING_DATA")
    p.add_argument("--root", type=str, default="TESTING_DATA", help="Root testing data folder")
    p.add_argument(
        "--comparator",
        type=str,
        default="Comparison_testing/video_quality_compare.py",
        help="Comparator script path",
    )
    p.add_argument("--sample-frames", type=int, default=120, help="Sample frames for comparator")
    p.add_argument("--original-name", type=str, default="ORIGINAL", help="Preferred original filename stem")
    p.add_argument("--min-samples", type=int, default=3, help="Minimum OK samples per codec/category")
    p.add_argument("--out-csv", type=str, default="TESTING_DATA/scoreboard.csv", help="CSV output path")
    p.add_argument("--out-json", type=str, default="TESTING_DATA/scoreboard.json", help="JSON output path")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    comparator = Path(args.comparator).expanduser().resolve()
    out_csv = Path(args.out_csv).expanduser().resolve()
    out_json = Path(args.out_json).expanduser().resolve()

    if not root.exists() or not root.is_dir():
        print(f"Error: root does not exist or is not a directory: {root}", file=sys.stderr)
        return 2
    if not comparator.exists():
        print(f"Error: comparator script not found: {comparator}", file=sys.stderr)
        return 3

    rows, category_summary = build_rows(
        root=root,
        comparator=comparator,
        sample_frames=args.sample_frames,
        original_name=args.original_name,
        min_samples=max(1, int(args.min_samples)),
    )
    write_outputs(rows, category_summary, out_csv, out_json)

    ok_rows = [r for r in rows if r.get("status") == "ok"]
    err_rows = [r for r in rows if r.get("status") != "ok"]
    bc_wins = sum(1 for r in ok_rows if r.get("winner") == "BitCrusher")
    se_wins = sum(1 for r in ok_rows if r.get("winner") == "ShutterEncoder")
    weak_categories = [v for v in category_summary.values() if not v.get("meets_min_samples")]

    print("Scoreboard build complete")
    print(f"- Root: {root}")
    print(f"- Comparator: {comparator}")
    print(f"- Cases compared: {len(ok_rows)}")
    print(f"- BitCrusher wins: {bc_wins}")
    print(f"- ShutterEncoder wins: {se_wins}")
    print(f"- Non-compared/error rows: {len(err_rows)}")
    print(f"- Categories below min samples ({args.min_samples}): {len(weak_categories)}")
    print(f"- CSV: {out_csv}")
    print(f"- JSON: {out_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
