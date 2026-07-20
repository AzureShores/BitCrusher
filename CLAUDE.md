# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working style

Before making changes:

1. Understand the execution path before editing.
2. Identify the smallest set of affected modules.
3. Check callers and downstream dependencies before changing interfaces.
4. Prefer minimal targeted fixes over broad refactors.
5. Preserve existing architecture patterns unless there is a clear reason not to.

For unfamiliar areas:
- Map the relevant files and data flow first.
- Trace the path from user input → processing → output.
- Identify tests covering the behavior.
- Explain the planned change before large edits.

When modifying code:
- Keep GUI and CLI behavior consistent.
- Preserve offline guarantees.
- Do not bypass existing abstractions.
- Avoid introducing dependencies without approval.
- Match existing naming, logging, and error-handling conventions.

Before finishing:
- Review your own diff.
- Look for regressions and edge cases.
- Run the relevant tests.
- Summarize:
  - files changed
  - why they changed
  - tests run
  - remaining risks

## What this is

BitCrusher compresses video, audio, and images to a target size (a **ceiling**, never
overshot — built for upload limits like Discord). It ships as a single Tkinter GUI app
that doubles as a CLI, backed by a learning system that tunes rate control from measured
VMAF/XPSNR outcomes.

## Commands

```bash
pip install -r requirements.txt        # deps; ffmpeg/ffprobe must also be on PATH
python BitCrusherV9.py                  # launch GUI (NO arguments)
python BitCrusherV9.py clip.mp4 -t 8    # CLI mode (ANY argument switches to CLI)
pytest                                  # full test suite (testpaths=tests)
pytest tests/test_regressions.py::test_name   # single test
```

- **GUI vs CLI dispatch is argument-count based**: `len(sys.argv) > 1` runs `cli_main()`,
  otherwise `CompressorGUI` launches. This lives at the bottom of `BitCrusherV9.py`.
- Bundled binaries live in `tools/` (`ffmpeg.exe`, `ffprobe.exe`, `HandBrakeCLI.exe`,
  `vmaf_models/`). Binary discovery honors env vars `BC_FFMPEG`/`FFMPEG` and
  `BC_FFPROBE`/`FFPROBE` before falling back to PATH.
- `pytest.ini` excludes `backups/`. `tests/test_regressions.py` is the large behavioral
  net — run it after any change to the encode/planning path.
- Full CLI flag reference is in `CLI_COMMANDS.md`. PowerShell drops empty-string args, so
  use bare `--suffix` / `--prefix` (not `--suffix ""`) to mean "empty".

## Debugging protocol

For bugs:

Do not immediately patch.

First determine:

1. Where the bad behavior begins.
2. Which module owns that behavior.
3. Whether the problem is:
   - input validation
   - planning
   - encoding
   - size control
   - learning state
   - GUI/CLI dispatch

Then:
- reproduce if possible
- inspect existing tests
- make the smallest fix
- add regression coverage if the bug was not already covered

## Architecture

`BitCrusherV9.py` (~17k lines) is the monolith: it holds the Tkinter `CompressorGUI`, the
`cli_main()` entry, and the core encode orchestrators `compress_video()` /
`compress_audio()` / `compress_image()`. **GUI and CLI both call the same core functions** —
there is no separate business-logic layer inside the monolith.

The heavy logic is extracted into flat, root-level, GUI-free modules that the monolith
imports. These are the units that carry tests and where most real work happens. Import
sites tolerate both a packaged (`from bitcrusher.overhead import ...`) and a flat
(`from overhead import ...`) layout via try/except fallback — keep that pattern when adding
cross-module imports.

Encode pipeline modules:
- `ml_heuristics.py` — content feature extraction (ffprobe + PIL/numpy), scene-cut analysis
  and per-scene difficulty "zones". Caches analysis on disk under
  `%LOCALAPPDATA%/BitCrusher/cache` (outside the repo).
- `planner.py` — turns content + budget into an encode plan (`PlanInputs`, `plan()`): CRF /
  bitrate math, scene-zone bitrate allocation, error-code taxonomy (`E_VAL_*`).
- `probe_predictor.py` + `codec_probe.py` — measured rate→quality probe fit used to pick
  operating points.
- `encoder_profiles.py` — deterministic content-aware encoder parameter selection.
- `smart_rate.py` — dynamic overshoot learning, encoder-probe cache, `guardrail_adjust`,
  `learn_from_result`.
- `ai_advisor.py` — Ridge-regression bitrate advisor (`choose_bitrates_advised`), runs in
  shadow alongside the deterministic path.
- `size_controller.py` — retry-loop controller that converges the output just under the
  size cap.
- `overhead.py` — per-container muxing overhead factors.

Learning, reporting, and support:
- `outcome_ledger.py` — **Stage 1 of the learning system**. Appends one rich record per
  completed encode to `user_settings/stats/ledger.jsonl`; shadow predictors read it.
- `dashboard.py` / `visual_compare.py` — pure, framework-agnostic view-models (VMAF
  sparkline + codec-race scoreboard; side-by-side frame compare). No Tkinter, unit-testable.
- `tool_installer.py` — fetches/verifies ffmpeg (one of the few network touchpoints).

## Dependency investigation rules

When changing a module:

1. Find all importers and callers first.
2. Check tests before changing behavior.
3. Check whether the module participates in the encode pipeline.

Important dependency chains:

Input:
GUI / CLI arguments
    ↓
compress_video()
    ↓
planner.py
    ↓
encoder_profiles.py
    ↓
size_controller.py
    ↓
encoder execution
    ↓
outcome_ledger.py

Learning:
encode result
    ↓
outcome_ledger.py
    ↓
shadow predictors
    ↓
future tuning

Do not modify one stage without considering the stages before and after it.

## Invariants — do not break these

These encode hard-won lessons; violating them silently corrupts quality or the learning data.

- **Core is fully offline.** No network in the encode/planning path. The only network
  touchpoints are opt-in and peripheral: `tool_installer.py`, the `--webhook` result POST,
  `yt-dlp` URL input, and the folder watcher.
- **The learning system is trust-nothing / shadow-mode.** Predictors log their predictions
  but must not steer real encodes until logged accuracy beats the shipping heuristics.
- **Never mix VMAF model scales.** v0.6.1 and v1 numbers are on different scales; the ledger
  tags the model in use, and v0.6.1 numbers must never train v1-scale predictions.
- **Record only EFFECTIVE settings.** When the codec race picks a different encoder than
  requested, attribute the outcome to the encoder that actually ran (the old poisoned-cache
  bug came from attributing it to the requested one).
- **Size target is a ceiling.** Aim just under, never over.
- **Codec race**: by default the requested/default codec is measured against AV1 by VMAF and
  the winner is kept; `--no-auto-codec` disables it.

## Avoid

Do not:

- rewrite BitCrusherV9.py into multiple files without explicit approval.
- replace deterministic logic with ML guesses.
- modify the learning ledger format casually.
- change encode decisions without measuring impact.
- remove old fallback paths without checking compatibility.
- add abstractions only for style reasons.

## State locations

- `user_settings/` — `settings.json`, `queue.json`, themes, i18n, and `stats/ledger.jsonl`
  (the learning data — treat as precious; there are timestamped `.bak` copies here).
- `test_pipeline/` — diverse-corpus harness: `build_corpus.py`, `run_corpus.py`,
  `summarize.py`, `train_ledger.py`, `run_faceoff.py`. Use this to validate changes against a
  real corpus rather than trusting unit tests alone.

## Conventions

- **Logs and status messages are plain text with `[Tag]` prefixes — no emoji.** The codebase
  has a history of emoji-mojibake corruption; keep new log/status strings ASCII.
