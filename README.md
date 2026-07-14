# BitCrusher

Compresses video, audio, images, and PDFs down to a target size — a ceiling
it aims for but never overshoots. Built for upload limits like Discord's
10/25/50MB caps. Runs as a desktop GUI or a CLI, and learns from its own
past encodes to make better first-attempt decisions over time.

> **Requires Python 3.10+ on your PATH.** Get it from
> [python.org/downloads](https://www.python.org/downloads/) — check
> "Add python.exe to PATH" during install. `BitCrusher.bat` will tell you
> if it's missing.

## Features

- Video, audio, image, and PDF compression in one queue, routed automatically.
- Measured codec race: your requested encoder vs AV1, picked by actual VMAF.
- Two-pass rate control, per-scene bitrate zones, artifact-aware prefiltering.
- Trim-aware compression, folder watcher, Explorer "Send to", Discord webhook.
- A learning system that seeds future encodes from measured past outcomes.
- Fully offline core — no data leaves your machine during a compression job.

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
