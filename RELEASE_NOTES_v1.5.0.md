# BitCrusher v1.5.0

Workflow release. The queue gets a per-file size target you can type straight
into it and reorder while a batch is running; finished jobs can be re-queued
with one knob changed; and three new read-only reports tell you what an encode
actually did - whether Discord will preview it, which scenes ate the bit
budget, and which two seconds ruined the quality.

Nothing here changes how bits are allocated. The size ceiling, the codec race
and the shadow-mode learning rules are untouched.

## Added

- **Per-file size target in the queue.** A `Target` column you can double-click
  and type into (`10`, `8.5`, `700kb`), or set from the right-click menu for a
  whole selection at once. Blank means "use the batch target". An explicit
  per-file target outranks a folder-watcher rule.

- **Reorder the queue while it is running.** Dragging a row now actually moves
  it up the line: dispatch picks each next job from the live queue instead of
  the snapshot it took when you pressed Start. Files already encoding finish
  where they are. Under `-j > 1`, jobs are handed to workers as slots free up
  rather than all being submitted at once.

- **"Encode Again With..."** from the batch summary and the queue's right-click
  menu. Pre-fills what the last run actually used - including the encoder that
  *won the codec race*, not the one you requested - and lets you change the
  target, the encoder, or Discord-compatible mode. A chosen encoder is pinned,
  so "encode again with x265" really produces x265.

- **Discord playback verdict.** A live chip beside the Discord size presets
  says what your current settings will do: "H.264 + AAC (MP4) - will
  inline-play", "AV1 ... - may not preview", "MKV has no inline player". Click
  it for the full reason list. The CLI summary and the batch summary show the
  *measured* verdict for each delivered file, and the batch-summary export
  gains a `discord` column.

- **Scene report card.** For a finished file, where the bits actually went:
  per-scene bitrate measured from the delivered file's packets, ranked by share
  of the budget, with thumbnails of the top spenders. Reached from the batch
  summary ("Scene Report") or the queue's right-click menu. Reports runtime
  share alongside bit share, so "3 scenes used 60% of the bits" can be checked
  against how much of the video they are.

- **`--suggest-trim` now uses measured quality, not just audio.** When the
  ledger has a past encode of the same file, the worst-scoring 2s window is
  offered as a trim candidate - as ranges that *avoid* it, since a trim says
  what to keep. Says plainly when only part of a long source was measured.
  Audio-energy peaks still follow.

- **Cross-session duplicate detection.** "Scan Queue for Duplicates" now also
  asks the ledger whether a queued file's content was already delivered in an
  earlier session at the same settings, and offers to reuse that output.
  Deliberately strict: identical size ceiling, matching encoder when one is
  pinned, never for trimmed or spotlit jobs, and the file has to still exist.
  Re-verified at encode time, so a source edited between the scan and Start
  falls back to a normal encode.

## Changed

- The ledger records two new fields per encode: a path-independent content
  signature and the delivered output path. Both are additive; older records
  are read exactly as before, and simply do not match cross-session lookups.

- Cancelling a concurrent batch now leaves not-yet-started files pending and
  resumable, matching what single-worker mode already did, instead of badging
  them cancelled.

## Internal

- `ui/charts.py` - framework-free chart geometry (bars, sparkline, quality
  strip) plus one thin canvas renderer, following `learning/dashboard.py`'s
  pure view-model pattern. No Tkinter anywhere in the geometry.
- `encode/scene_bits.py` - packet-level bit attribution: ffprobe packet sizes
  bucketed into scene ranges, with pure bucketing and ranking.
- `encode/discord_compat.py` - the inline-playback verdict table.
