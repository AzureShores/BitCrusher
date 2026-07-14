# BitCrusher

Compresses video, audio, images, and PDFs down to a target size — a ceiling
it aims for but never overshoots. Built for upload limits like Discord's
10/25/50MB caps. Runs as a desktop GUI or a CLI, and learns from its own
past encodes to make better first-attempt decisions over time.

## Features

- Video, audio, image, and PDF compression in one queue, routed automatically.
- Measured codec race: your requested encoder vs AV1, picked by actual VMAF.
- Two-pass rate control, per-scene bitrate zones, artifact-aware prefiltering.
- Trim-aware compression, folder watcher, Explorer "Send to", Discord webhook.
- A learning system that seeds future encodes from measured past outcomes.
- Fully offline core — no data leaves your machine during a compression job.

## Requirements

- Windows, Python 3.10+
- ffmpeg, ffprobe, and HandBrakeCLI — either on `PATH`, or dropped into a
  `tools/` folder next to `BitCrusherV9.py`. Configure custom paths in the
  app (Settings > Configure Paths) or with the `BC_FFMPEG`/`BC_FFPROBE`
  env vars.

## Install

```
pip install -r requirements.txt
```

## Run

```
python BitCrusherV9.py                  # GUI
python BitCrusherV9.py clip.mp4 -t 8    # CLI — any argument switches to CLI mode
```

## Docs

- [User Guide](docs/USER_GUIDE.md) — GUI walkthrough.
- [CLI_COMMANDS.md](CLI_COMMANDS.md) — full flag reference.

## License

TBD.
