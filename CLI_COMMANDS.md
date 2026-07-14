BitCrusher CLI Reference

python BitCrusherV9.py [inputs...] [options]

Any argument = CLI mode. No arguments = GUI. Compresses video, audio,
images, and PDFs to a target size (a ceiling, never overshot). Output goes
to --output (or next to the source), named <prefix><name><suffix>.<ext>.

Examples:
python BitCrusherV9.py clip.mp4                         (10MB default target)
python BitCrusherV9.py clip.mp4 -t 8
python BitCrusherV9.py "C:\Videos" -t 25 -j 3            (folder, parallel)
python BitCrusherV9.py clip.mp4 --quality fast --encoder x265 --no-measure -o out
python BitCrusherV9.py "renders/*.mov" -t 10 --suffix    (bare flag = empty)

inputs - files, folders, or globs. Folders scanned for media; globs expanded.

Core:
-o, --output DIR          output directory, created if missing
-t, --target-size MB      default 10
-j, --jobs N              encode N files at once
-q, --quiet               warnings/errors only
--version, -h/--help

Quality & codec:
--quality {fast,balanced,max}   default max. fast=one-shot, balanced=accurate no-overshoot, max=packs the cap + raises encoder effort
--encoder NAME             x264, x265, av1, svt-av1, aom-av1, vp9, vvc, or HW: h264_nvenc/hevc_nvenc/av1_nvenc, *_qsv, *_amf
--no-auto-codec            disable the VMAF codec race (your pick vs AV1), use requested/default as-is
--min-vmaf N               spend spare budget until output reaches VMAF N (0=off)
--no-measure               skip final VMAF check
--vmaf-model VALUE         auto|v1|neg|4k|default|raw version=/path= value
--vmaf-objective {window,p5,p1,harmonic,mean}   which VMAF number the min-vmaf floor targets (default window = worst ~2s scene)
--no-preproc               disable deband/deblock/denoise prefilters
--no-learned-seed          disable learned first-attempt bitrate from the ledger
--no-preflight             disable the advisory pre-flight guardrail
--no-ceiling-downscale     disable last-resort downscale-and-retry (may ship oversized instead)
--film-grain {auto,off,force}   AV1 grain synthesis, default auto
--no-scene-zones           disable per-scene bitrate allocation (x264/x265)

Rate control:
--crf N                    manual CRF, overrides prediction
--bitrate BPS               manual average video bitrate, forces ABR
--two-pass                 allow two-pass when it helps
--force-two-pass            force two-pass regardless of heuristics

Hardware:
--hwaccel {CPU,NVENC,QSV,AMF}   default CPU
--no-hw-decode              disable GPU decode of the source (encode unaffected)

Audio & images:
--audio-format {opus,aac,m4a,mp3}   tags/art preserved on every format incl. opus. Source-fits and lossless-FLAC guards run first.
--audio-track-mode {keepfirst,mix}   multi-track sources
--no-lyrics                 don't embed a sibling .lrc into output tags
--image-format {jpg,png,webp}   default jpg

Trim & spotlight:
--trim START-END            compress only this range, e.g. 1:42-2:05 or 12-31 (source never modified)
--trim-fade                 frame-exact trim with 0.5s fades (default is zero-loss stream-copy)
--suggest-trim [SECONDS]    print candidate trim ranges from audio energy, then exit
--spotlight START-END       keep the whole video but boost quality in this range

Output & sharing:
--prefix TEXT               bare flag = empty
--suffix TEXT                default _discord_ready, bare flag = none
--webhook URL                POST result summary
--discord-compat             force H.264+AAC MP4 for guaranteed inline playback
--clipboard                  copy finished file to clipboard (Ctrl+V into Discord)
--enqueue FILE [FILE...]     hand file(s) to a running window, or launch one
--register-send-to / --unregister-send-to   Explorer "Send to" shortcut

Ledger / diagnostics (no input files, print and exit):
--dedup-scan                scan inputs for byte-identical duplicates before encoding
--estimate                  predict size/VMAF/worst-scene/time from the ledger, no encode
--learning-trend             are the ledger's shadow predictors getting more accurate over time
--ledger-audit               anomalous encodes + VMAF-scale population report
--check-updates               check GitHub for a newer release, print result, exit

Notes:
Exit codes: 0 = at least one file compressed, 1 = nothing produced, 2 = argument error.
PowerShell drops empty-string args - use the bare flag (--suffix, --prefix) instead of --suffix "" for empty.
Interactive terminals show a live "stage NN% ETA ~Xs" line per file; -q suppresses it.
