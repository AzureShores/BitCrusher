BitCrusher User Guide

For terminal/scripting use instead of the GUI, see CLI_COMMANDS.md.

QUICK START

1. Add files - drag and drop, or Add Files...
2. Set a target size (MB) or pick a preset.
3. Click Start Compression. Progress and logs show in the window.
4. Click Open Save Folder to grab the results.

That covers most use. Everything below is for extra control.

QUALITY MODE

In Advanced Options:

fast - one-shot encode, no packing/retargeting. Quickest, least precise.
balanced - accurate targeting without overshoot, single refinement pass.
max (default) - also packs the size cap and raises encoder effort. Slower, best quality per byte.

Encode time is treated as free, so max mode can take a while on long or high-res sources - it's doing real work with that time. Drop to fast if you're iterating quickly.

ADVANCED OPTIONS

Encoder - x264, x265, AV1 (SVT or aom), VP9, VVC, or a hardware encoder if your GPU has one. Left on auto, BitCrusher measures your codec against AV1 by VMAF and keeps whichever wins.
Manual CRF / bitrate - override automatic rate control.
Two-pass - more accurate targeting, roughly double the encode time.
Scene zones - allocates bitrate per-scene so hard scenes get more bits than easy ones.
HW decode - GPU decoding for reading the source; falls back to software automatically if unsupported.
Audio format - AAC, Opus, MP3, etc. Opus generally beats AAC at the same bitrate where the container supports it (MKV/WebM).
Audio track mode - for multi-track sources, keep the first track or mix all down to one.
Image format - JPEG or PNG, with optional Guetzli/pngopt for extra slow squeeze.
Discord-compat - forces H.264 + AAC in MP4 for guaranteed inline Discord playback.
Embed lyrics - folds a sibling .lrc file into the output's tags automatically.
Copy to clipboard - copies each finished file to the clipboard when done (Ctrl+V into Discord).
Output prefix / suffix - customize the output filename pattern.

TRIM

Set a trim range per file to cut before compressing - the cut is lossless (stream-copy) on a throwaway intermediate; your source is never touched. Suggest Trim analyzes audio energy and proposes candidate ranges with a reason for each.

FOLDER WATCHER & SEND-TO

Folder Watcher (Settings) auto-queues new files dropped into a folder you point it at, with optional per-file rules for target/encoder. Send to > BitCrusher (right-click in Explorer) sends a file to a running window, or launches one.

WEBHOOK

Paste a Discord webhook URL (Settings > Webhook) for a post on start/success/failure.

PROFILES & THEMES

Save Profile / Load Profile snapshots your whole Advanced Options setup under a name. Theme Lab (Settings > Themes) builds/saves a custom color theme.

DASHBOARD

After an encode, shows a VMAF-over-time sparkline (worst moment flagged) and a codec-race scoreboard explaining why the winning codec won.

PDF COMPRESSION

Exists (Ghostscript-based) but is the least developed path - expect rougher edges than video/audio/image. A PDF already mostly text/vector that doesn't need much shrinking is kept as-is.

TROUBLESHOOTING

logs/bitcrusher.log and logs/run_YYYYMMDD.jsonl are the ground truth for what the pipeline decided and why.
"Unrealistic target size" warning means the request is too small to survive at acceptable quality - aim for 10-20% of the original, not 1-2%.
Size cap is a hard ceiling - if nothing fits, BitCrusher says so instead of shipping an oversized file.
Missing ffmpeg/ffprobe/HandBrakeCLI - BitCrusher checks its own tools/ folder first, then PATH. Set BC_FFMPEG/BC_FFPROBE env vars to point at custom locations.

Core encoding, quality measurement, and learning run fully offline. Network touchpoints are opt-in only: webhook, folder watcher, Send to, and pulling a URL via yt-dlp.
