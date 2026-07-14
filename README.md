# BitCrusher

Compresses video, audio, images, and PDFs down to a target size — a ceiling
it aims for but never overshoots. Built for upload limits like Discord's
10/25/50MB caps. Runs as a desktop GUI or a CLI, and learns from its own
past encodes to make better first-attempt decisions over time.

> **Requires Python 3.10+ on your PATH.** Get it from
> [python.org/downloads](https://www.python.org/downloads/) — check
> "Add python.exe to PATH" during install. `BitCrusher.bat` will tell you
> if it's missing.

![BitCrusher GUI](assets/screenshot.png)

## Features

- Video, audio, image, and PDF compression in one queue, routed automatically.
- Measured codec race: your requested encoder vs AV1, picked by actual VMAF.
- Two-pass rate control, per-scene bitrate zones, artifact-aware prefiltering.
- Trim-aware compression, folder watcher, Explorer "Send to", Discord webhook.
- A learning system that seeds future encodes from measured past outcomes.
- Fully offline core — no data leaves your machine during a compression job.
- Opt-in update check against GitHub Releases (asked once on first launch,
  toggle any time in Settings > Check for Updates).
- Advanced Options > Export Sanitized Logs makes a redacted copy of the
  learning ledger, recent job logs, and settings — safe to paste into a bug
  report (strips your home path and any saved webhook URL).

## Benchmark

One real CLI run, not cherry-picked — reproduce with:
`python BitCrusherV9.py <file> -t 10 --quality max`

| | Source | Output |
|---|---|---|
| Resolution | 3840x2160 (4K) | 3840x2160 (4K) |
| Codec | H.264 | AV1 |
| Duration | 14.01s | 14.01s |
| Size | 39.40 MB | 9.85 MB (25.0% of source) |
| Bitrate | 23.6 Mbps | ~5.9 Mbps |

- **Content**: high-motion, low-complexity 4K stock clip, 29.97fps.
- **Target**: 10 MB (Discord's free-tier cap) — landed 9.85 MB, under the ceiling.
- **Codec race** (quality mode: Max, CPU-only): measured av1=87.1 vs
  x265=82.2 vs x264=71.3 (VMAF-per-bit on probe segments), picked AV1
  automatically over the requested x264.
- **Quality**: VMAF (v0.6.1 model) 86.9 mean, 85.5 worst-scene
  (2-second rolling window, the floor the pipeline actually optimizes for)
  at 0:04. XPSNR 42.3 dB as a perceptual cross-check.
- **Encode time**: 357s (~6 min), two-pass, CPU-only, no hardware acceleration.

## Requirements

- Windows, [Python 3.10+](https://www.python.org/downloads/)
- ffmpeg, ffprobe, and HandBrakeCLI — either on `PATH`, or dropped into a
  `tools/` folder next to `BitCrusherV9.py`. Configure custom paths in the
  app (Settings > Configure Paths) or with the `BC_FFMPEG`/`BC_FFPROBE`
  env vars.

## Install

```
pip install -r requirements.txt
```

## Run

Double-click `BitCrusher.bat` to launch the GUI, or use it from a terminal for CLI mode:

```
BitCrusher.bat                          # GUI
BitCrusher.bat clip.mp4 -t 8            # CLI — any argument switches to CLI mode
```

Or run the script directly:

```
python BitCrusherV9.py                  # GUI
python BitCrusherV9.py clip.mp4 -t 8    # CLI — any argument switches to CLI mode
```

## Docs

- [User Guide](docs/USER_GUIDE.md) — GUI walkthrough.
- [CLI_COMMANDS.md](CLI_COMMANDS.md) — full flag reference.

## License

GNU General Public License v3.0 or later. See [LICENSE.md](LICENSE.md).

Copyright (C) 2026 AzureShores

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.
