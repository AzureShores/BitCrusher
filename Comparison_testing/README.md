# Video Quality Comparison Testing

This tool compares multiple videos against an original reference and ranks which candidate appears to have the highest objective quality.

It is designed to reduce bias by:
- using measured metrics (not visual opinion),
- comparing each candidate to the same original,
- showing raw metric values and weighted scores.

## What it measures
- Full-reference metrics vs original: PSNR, SSIM, frame distance
- Resolution-related quality pressure: bits-per-pixel-per-frame
- Bitrate indicators: video bitrate
- Per-candidate visual traits: sharpness, blockiness, noise, clipping, contrast

## Requirements
- Python 3.9+
- `opencv-python`
- `numpy`
- `ffprobe` available on PATH (from FFmpeg)

## Usage
Compare 2 candidates against original:
```bash
python Comparison_testing/video_quality_compare.py "original.mp4" "v1.mp4" "v2.mp4"
```

Compare 3 candidates against original:
```bash
python Comparison_testing/video_quality_compare.py "original.mp4" "v1.mp4" "v2.mp4" "v3.mp4"
```

Optional flags:
```bash
python Comparison_testing/video_quality_compare.py "original.mp4" "v1.mp4" "v2.mp4" "v3.mp4" ^
  --sample-frames 120 ^
  --report-json "Comparison_testing/last_report.json"
```

## Batch Scoreboard Script
Build a BitCrusher vs ShutterEncoder scoreboard from `TESTING_DATA`:

```bash
python Comparison_testing/build_codec_scoreboard.py ^
  --root "TESTING_DATA" ^
  --comparator "Comparison_testing/video_quality_compare.py" ^
  --sample-frames 120 ^
  --min-samples 3 ^
  --out-csv "TESTING_DATA/scoreboard.csv" ^
  --out-json "TESTING_DATA/scoreboard.json"
```

Preferred multi-sample layout per category:
- `TESTING_DATA/<codec>/<category>/<sample_case>/ORIGINAL.*`
- `TESTING_DATA/<codec>/<category>/<sample_case>/BitCrusher/*.mp4`
- `TESTING_DATA/<codec>/<category>/<sample_case>/ShutterEncoder/*.mp4`

Example:
- `TESTING_DATA/hevc_qsv/Gaming_Footage/sample_01/ORIGINAL.mp4`
- `TESTING_DATA/hevc_qsv/Gaming_Footage/sample_01/BitCrusher/output.mp4`
- `TESTING_DATA/hevc_qsv/Gaming_Footage/sample_01/ShutterEncoder/output.mp4`

This avoids duplicate `ORIGINAL` filenames while supporting 3+ samples per category.
