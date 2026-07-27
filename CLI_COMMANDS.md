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
--also-target MB          also export each item at this extra cap; repeatable,
                          e.g. -t 8 --also-target 25 --also-target 50
                          Extra targets reuse the rate/size behaviour measured
                          by the earlier ones, so they usually converge in fewer
                          encodes. Each cap is still enforced independently.
-j, --jobs N              encode N files at once
-q, --quiet               warnings/errors only
--json                    machine-readable: suppresses all human status/progress
                          text and prints ONE JSON array of per-file result
                          objects to stdout at the end. Diagnostics go to stderr,
                          so `... --json 2>/dev/null` is safe to pipe.
--version, -h/--help      --version prints app version, Python, OS, ffmpeg build
                          and the resolved ffmpeg/ffprobe paths

Quality & codec:
--quality {fast,balanced,max}   default max. fast=one-shot, balanced=accurate no-overshoot, max=packs the cap + raises encoder effort
--encoder NAME             x264, x265, av1, svt-av1, aom-av1, vp9, vvc, or HW: h264_nvenc/hevc_nvenc/av1_nvenc, *_qsv, *_amf
--no-auto-codec            disable the VMAF codec race (your pick vs AV1), use requested/default as-is
--min-vmaf N               spend spare budget until output reaches VMAF N (0=off)
--min-vmaf-relax-max MB    if the --min-vmaf floor is unreachable within the target,
                           allow the cap to grow in 1.25x steps up to this size
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
--name-template TEXT        full filename control; overrides --prefix/--suffix.
                            Tokens: {name} {ext} {codec} {size} {bytes} {vmaf}
                            {target} {width} {height} {res} {fps} {date} {time}
                            e.g. --name-template "{name}_{codec}_{size}"
                            Tokens with no value drop out and their separators
                            collapse, so a missing {vmaf} gives clip_x264, not
                            clip_x264_. {codec}/{size}/{vmaf} are only known
                            after encoding, so the file is renamed at the end.
                            Path separators and characters Windows forbids are
                            stripped from prefix/suffix/template.
--webhook URL                POST result summary
--discord-compat             force H.264+AAC MP4 for guaranteed inline playback
--clipboard                  copy finished file to clipboard (Ctrl+V into Discord)
--enqueue FILE [FILE...]     hand file(s) to a running window, or launch one
--enqueue-target MB          with --enqueue: per-file target override carried into
                             the running instance (context-menu presets use this)
--register-send-to / --unregister-send-to   Explorer "Send to" shortcut
--export-format {gif,webp}   export video sources as animated GIF/WebP instead of
                             an encoded video (size-targeted quality ladder)
--contact-sheet              also save a 4x4 frame-grid JPEG (<output>_sheet.jpg)
                             beside each video output

Ledger / diagnostics (no input files, print and exit):
--dedup-scan                scan inputs for byte-identical duplicates before encoding
--estimate                  what the ledger knows about this file, no encode.
                            Lists every past encode of the exact same file
                            (measured size/VMAF/encoder/time per target), then
                            per codec the predicted size and encode time plus
                            the VMAF range OBSERVED on comparable encodes with
                            the record count. A range, not a point estimate:
                            for content the ledger has not seen, a predicted
                            VMAF is no more accurate than the overall average.
--learning-trend             are the ledger's shadow predictors getting more accurate over time
--ledger-audit               anomalous encodes + VMAF-scale population report
--check-updates               check GitHub for a newer release, print result, exit

Notes:
Exit codes: 0 = at least one file compressed, 1 = nothing produced, 2 = argument error.
PowerShell drops empty-string args - use the bare flag (--suffix, --prefix) instead of --suffix "" for empty.
Interactive terminals show a live "stage NN% ETA ~Xs" line per file; -q suppresses it.
