# BitCrusher — User Guide

BitCrusher shrinks video, audio, images, and PDFs down to a **target size** —
a ceiling it aims for but never goes over. Built for upload limits (Discord's
10/25/50MB caps, email attachments, etc.), it runs the file's own history
back into future decisions: every completed encode teaches it a little more
about what settings actually hit the target at good quality.

Everything below describes the desktop app. Scripting or batch jobs from a
terminal instead? See **[CLI_COMMANDS.md](../CLI_COMMANDS.md)** — same
engine, same results, just flags instead of clicks.

---

## Quick start

1. **Add files.** Drag and drop onto the window, or click **Add Files...**.
   Mix video, audio, image, and PDF files in one queue — each is routed to
   the right compressor automatically.
2. **Set a target size** (MB) or pick a **preset** (e.g. "Discord — 10MB").
3. Click **Start Compression**. Watch progress and log output in the window;
   finished files land in your chosen save folder (or next to the source).
4. Click **Open Save Folder** to grab the results.

That's the whole workflow for 90% of use. Everything else below is for when
you want more control.

---

## Choosing quality vs. speed

The **Quality** setting (in Advanced Options) controls how hard BitCrusher
works to hit the target:

| Mode | What it does |
|---|---|
| `fast` | One-shot encode, no packing/retargeting. Quickest, least precise. |
| `balanced` | Accurate targeting without overshoot, single pass of refinement. |
| `max` (default) | Also **packs** the size cap (spends every spare byte it can) and raises encoder effort (e.g. AV1 search depth). Slower, best quality-per-byte. |

Per this project's own philosophy: **encode time is treated as free** —
`max` mode can take a while on long/high-res sources, but it's doing real
work with that time, not spinning. If you're iterating quickly or just need
a rough cut, drop to `fast`.

This (and every other Advanced Options toggle) is now correctly **saved and
restored** across restarts — pick it once and it sticks.

---

## Advanced Options

Open the **Advanced Options** panel to tune:

- **Encoder** — x264, x265, AV1 (SVT or aom), VP9, VVC, or a hardware encoder
  (NVENC/QSV/AMF) if your GPU has one. Left on auto, BitCrusher measures
  your requested codec against AV1 by VMAF and keeps whichever wins
  ("codec race") — turn this off in the CLI with `--no-auto-codec` if you
  always want exactly what you picked.
- **Manual CRF / bitrate** — override the automatic rate control if you know
  what you want.
- **Two-pass** — more accurate targeting, roughly double the encode time.
- **Scene zones** — allocates bitrate per-scene instead of flat across the
  whole file, so hard scenes (motion, grain) get more bits than easy ones.
- **HW decode** — use GPU decoding for the source read (encode stays on the
  encoder you picked above). Falls back to software automatically if a
  source/driver combo doesn't support it.
- **Audio format** — AAC, Opus, MP3, etc. for the audio track (or the whole
  file, for audio-only sources). Opus generally beats AAC at the same
  bitrate where the container supports it (MKV/WebM).
- **Audio track mode** — for multi-track sources, keep just the first track
  or mix all tracks down to one.
- **Image format** — JPEG or PNG for image compression, with optional
  Guetzli/pngopt for extra (slow) squeeze.
- **Discord-compat** — force H.264 + AAC in an MP4 container, trading some
  size efficiency for guaranteed inline playback in Discord.
- **Embed lyrics** — if a sibling `.lrc` file sits next to an audio source,
  fold it into the output's tags automatically.
- **Copy to clipboard** — copy each finished file to the Windows clipboard
  (`Ctrl+V` straight into Discord) when compression finishes.
- **Output prefix / suffix** — customize the output filename pattern.

Hover any control in the app for a tooltip with more detail than fits here.

---

## Trim

Only need part of a file? Set a **trim range** (per-file, in the queue) to
cut before compressing — the cut happens losslessly (stream-copy) on a
throwaway intermediate; your source file is never touched, and the whole
pipeline (quality measurement, codec choice, everything) runs on the trimmed
content. Don't know where to cut? **Suggest Trim** analyzes audio energy and
proposes a few candidate ranges with a reason for each ("audio energy peak",
etc.).

---

## Folder Watcher & Send-To

- **Folder Watcher** (Settings) auto-queues any new file dropped into a
  folder you point it at — handy for a phone-sync or screen-recording
  output folder. You can set per-file rules (different target/encoder for
  specific patterns) instead of one global setting.
- **Send to > BitCrusher** — right-click any file in Explorer and send it
  straight to a running BitCrusher window (or launch one if none is open).
  Install/remove the shortcut from the Settings menu.

## Webhook

Paste a Discord webhook URL (Settings → Webhook) to get a post on
start/success/failure for every job — useful if you queue something and
walk away.

## Profiles & Themes

**Save Profile** / **Load Profile** snapshot your whole Advanced Options
setup (encoder, target, toggles, everything) under a name you pick, so you
can swap between "quick Discord clip" and "archival max-quality" setups
instantly. **Theme Lab** (Settings → Themes) lets you build/save a custom
color theme, or pick from the built-ins.

## Dashboard

After an encode, the **Dashboard** shows a VMAF-over-time sparkline (with
the worst-scoring moment flagged) and a codec-race scoreboard explaining
*why* the winning codec won — not just that it did.

---

## PDF compression

PDF support exists (Ghostscript-based: downsample images, or rasterize
text/vector pages that need more shrinking than recompression alone can
give) but it's the least developed path in the app — expect rougher edges
here than on video/audio/image. If a PDF is already mostly text/vector and
doesn't need much shrinking, BitCrusher just keeps it as-is rather than
risk making it worse.

---

## Troubleshooting

- **Log files** — `logs/bitcrusher.log` (human-readable, running log) and
  `logs/run_YYYYMMDD.jsonl` (one structured record per completed job) are
  the ground truth for what the pipeline actually decided and why.
- **"Unrealistic target size" warning** — if the requested size is too
  small for the content to survive at acceptable quality, BitCrusher tells
  you *why* (bitrate/audio/motion) instead of silently producing garbage.
  Aim for 10-20% of the original size as a rule of thumb, not 1-2%.
- **Size cap** — is a hard ceiling. If BitCrusher can't find a setting that
  fits, it says so rather than shipping an oversized file.
- **ffmpeg/ffprobe/HandBrakeCLI not found** — BitCrusher looks in its own
  `tools/` folder first, then your system `PATH`. If you moved/renamed
  binaries, set `BC_FFMPEG`/`BC_FFPROBE` (or `FFMPEG`/`FFPROBE`) env vars to
  point at them directly.

---

## Everything is local

BitCrusher's core (encoding, quality measurement, learning) runs fully
offline — no data leaves your machine during a compression job. The only
network touchpoints are opt-in: the webhook post, the folder watcher, `Send
to`, and pulling a URL via `yt-dlp` if you paste one in as a source.
