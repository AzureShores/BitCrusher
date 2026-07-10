# BitCrusher — CLI Command Reference

Run BitCrusher from the terminal instead of the GUI. **Passing any argument
starts CLI mode; running with no arguments launches the GUI.**

```
python BitCrusherV9.py [inputs...] [options]
```

Compresses video, audio, and images to a target size. Outputs land in the
`--output` directory (or next to the source if not set), named
`<prefix><name><suffix>.<ext>`.

---

## Quick examples

```bash
# Shrink one video to fit under 10 MB (default target)
python BitCrusherV9.py clip.mp4

# Target 8 MB, let it pick the best codec by measured quality
python BitCrusherV9.py clip.mp4 -t 8

# A whole folder, 25 MB each, 3 files at once
python BitCrusherV9.py "C:\Videos" -t 25 -j 3

# Fast one-shot, force x265, no VMAF measurement, custom output dir
python BitCrusherV9.py clip.mp4 --quality fast --encoder x265 --no-measure -o out

# Glob + no filename suffix (bare flag = empty on Windows/PowerShell)
python BitCrusherV9.py "renders/*.mov" -t 10 --suffix
```

---

## Positional

| Argument | Description |
|---|---|
| `inputs` | One or more files, folders, or globs to compress. Folders are scanned for media; globs like `*.mp4` are expanded. If omitted, the GUI launches. |

## Core options

| Flag | Default | Description |
|---|---|---|
| `-o`, `--output DIR` | next to first input | Output directory. Created if missing. |
| `-t`, `--target-size MB` | `10` | Target size in **MB**, applied to each item. |
| `-j`, `--jobs N` | `1` | Encode N files at once (parallel batch). |
| `-q`, `--quiet` | off | Only print warnings/errors (suppresses per-step chatter). |
| `--version` | — | Print version info and exit. |
| `-h`, `--help` | — | Show the built-in help and exit. |

## Quality & codec

| Flag | Default | Description |
|---|---|---|
| `--quality {fast,balanced,max}` | `max` | `fast` = quick single shot; `balanced` = accurate no-overshoot targeting; `max` = also packs the size cap and raises AV1 effort (slower, best quality per byte). |
| `--encoder NAME` | auto | Force a video encoder. Choices: `x264`, `x265`, `av1`, `svt-av1`, `aom-av1`, `vp9`, `vvc`, and HW variants `h264_nvenc`/`hevc_nvenc`/`av1_nvenc`, `*_qsv`, `*_amf`. |
| `--no-auto-codec` | off | Disable the VMAF-measured codec race (your codec vs AV1); use the requested/default codec as-is. |
| `--min-vmaf N` | `0` (off) | Keep spending spare size budget until the output reaches VMAF N (implies quality measurement). |
| `--no-measure` | off | Skip the VMAF quality check of the final file (faster; no VMAF in summary). |
| `--no-scene-zones` | off | Disable per-scene bitrate allocation for x264/x265. |

## Rate control (manual overrides)

| Flag | Default | Description |
|---|---|---|
| `--crf N` | auto | Force a constant-quality CRF instead of the predicted value. |
| `--bitrate BPS` | auto | Force an average video bitrate in **bits/sec** (ABR; may trigger two-pass). |
| `--two-pass` | off | Allow two-pass encoding when it helps. |
| `--force-two-pass` | off | Force two-pass regardless of heuristics. |

## Hardware

| Flag | Default | Description |
|---|---|---|
| `--hwaccel {CPU,NVENC,QSV,AMF}` | `CPU` | Hardware-acceleration hint for GPU encode pipelines. |
| `--no-hw-decode` | off | Disable GPU-accelerated **decoding** of the source (encode side is unaffected). |

## Audio & images

| Flag | Default | Description |
|---|---|---|
| `--audio-format {opus,aac,m4a,mp3}` | auto | Preferred audio codec. Opus is smallest/best; AAC/M4A most compatible. **Tags and album art are preserved on every format** — including Opus (embedded as a metadata-block picture). Two quality guards run first: if the source already fits under the target it's kept as-is (no re-compression), and if a lossless source can be re-compressed to FLAC under the target, you get lossless FLAC instead of a lossy squeeze. `--audio-force-reencode` skips both guards. |
| `--image-format {jpg,png,webp}` | `jpg` | Output format for image inputs. |

## Output naming & notifications

| Flag | Default | Description |
|---|---|---|
| `--prefix TEXT` | *(none)* | Prefix added to each output filename. Bare `--prefix` = empty. |
| `--suffix TEXT` | `_discord_ready` | Suffix added before the extension. Bare `--suffix` = none. |
| `--webhook URL` | — | POST a result summary to a Discord/webhook URL. |

---

## Notes

- **GUI vs CLI:** any argument = CLI mode; no arguments = GUI.
- **Exit codes:** `0` = at least one file compressed; `1` = nothing produced / no matching inputs; `2` = argument error.
- **PowerShell tip:** PowerShell drops empty-string arguments, so use the bare
  flag form (`--suffix`, `--prefix`) instead of `--suffix ""` to mean "empty".
- **Progress:** in an interactive terminal you get a live `stage NN% ETA ~Xs`
  line per file; with `-q` only warnings/errors print.
- **Size targets** are treated as a ceiling — the encoder aims to land just
  under the target, never over (great for upload limits like Discord).
