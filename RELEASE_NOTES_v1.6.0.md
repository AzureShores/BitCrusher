# BitCrusher v1.6.0 — Remote upload, an honest codec race, themes that stay readable

Three separate pieces of work landed in this release:

- **Remote upload** — send a file from your phone to the PC that is already
  running BitCrusher, and get it back under your size cap. GUI or headless.
- **The codec race now measures what it ships.** Four compounding defects meant
  the AV1 candidate was probed at settings its real encode would never use, and
  lost races it deserved to win. All four are fixed.
- **Themes and dialogs.** A contrast solver that cannot return invisible text, a
  Theme Lab randomiser, and thirteen dialogs that fit their own content at any
  display scaling.

---

## Remote upload

A browser on the same network signs in, uploads a file, watches the progress bar
and downloads the result. The file rides exactly the path a folder-watcher drop
takes — same rules, same queue, same encoder decisions. The GUI's sink *is* the
watcher's enqueue path, not a second copy of it, and `--serve` reuses the same
per-file entry point `cli_main` uses, so a headless upload takes the identical
encode path a local file does with the flags that invocation carried.

This is the first inbound, network-reachable, file-accepting listener BitCrusher
has ever had, so the whole feature is built to fail closed.

**It is off by default, and turning it on does not put anything on your
network.** The default bind is `127.0.0.1`. Reaching it from another device is a
separate, explicit setting that additionally requires a TLS certificate and a
password.

### Getting started

The server's dependencies are **not** in `requirements.txt` — nobody who leaves
this feature off pays for them:

```
pip install -r requirements-remote.txt
python BitCrusherV9.py --serve-set-password
python BitCrusherV9.py --serve -t 8
```

Or in the GUI: **Settings > Watcher > Remote Upload**. Set a password, tick the
box, and use **Pair a device…** to mint a one-time link (with a QR code) for the
phone.

### New CLI flags

| Flag | What it does |
|---|---|
| `--serve [PORT]` | Run the upload server headless until Ctrl+C. Every upload is compressed with the flags this invocation carried (`-t`, `--encoder`, `--quality`, …); outputs go to `-o`, or the working directory. |
| `--serve-bind ADDR` | Bind address override. Anything other than loopback needs TLS. |
| `--serve-set-password` | Prompt once, store the hash, exit. Required before `--serve` will start. |
| `--serve-make-cert` | Generate this machine's own TLS certificate, wire it into settings, exit. Required before a non-loopback `--serve` will start. |

A headless box that just sits there is arguably the better host for this, which
is why `--serve` shipped with the feature instead of after it.

### What it refuses to do

Every one of these is a hard failure with a reason, not a silent downgrade:

- bind to a network address without a TLS certificate;
- bind to a network address with the password requirement switched off;
- start with no password set at all;
- start when the port is already taken — it will **never** quietly pick a
  different one, because the pairing link would then point at the wrong place.

### Security notes

- Password hashed with argon2 (`hashlib.scrypt` when the extra is absent),
  stored in `user_settings/remote_auth.json`, never in `settings.json`.
- `/auth` is rate limited from the first commit: 5 attempts per 5 minutes per
  source IP, then 429. Pairing redemption has its own separate budget, so a
  stale link cannot lock you out of the password login.
- Pairing tokens are single-use with a 60-second life, burned inside the same
  lock that reads them.
- Sessions are memory-only and die with the process. That is deliberate:
  persisting them would put a durable credential on disk to save a few seconds
  of re-pairing.
- Uploads never touch a path derived from the client. The file on disk is a
  server-minted UUID; the phone's filename is kept only as a display label — the
  queue row shows the label, the file on disk keeps the UUID.
- The size cap is enforced *while* the body is read, not after, and the partial
  file is deleted on every failure path.
- `/status`, `/jobs` and `/download` are scoped to the session that created the
  job, and a job belonging to someone else answers exactly like one that never
  existed.
- Closing the tab or letting the phone background it no longer loses your
  files: the page asks `/jobs` on load and restores the list, so a finished
  encode stays downloadable for as long as the session lives. Downloads carry
  both `filename=` and `filename*=`, because older mobile browsers understand
  only the former and would otherwise save the file under its job UUID.
- Never logged: the password, the session token, the pairing token. Source IPs
  are logged. The uvicorn access log is off permanently, because a pairing token
  rides in a URL path.

**Honest limitation, stated the same way in the app, the settings panel and the
docstring:** the magic-byte check on uploads is a *sanity filter, not a security
boundary*. It rejects an obvious `.exe`, `.zip` or HTML polyglot. It does not
protect ffmpeg — ffmpeg parses by content, and a file with a valid header and a
hostile structure reaches the decoder intact. What does apply: the size cap, the
UUID filename, and a quarantine directory outside anything BitCrusher reads for
configuration.

### The certificate is yours, not mine

`--serve-make-cert`, or **Create certificate…** in the settings panel, generates
a self-signed certificate and key into `user_settings/` and points the config at
them. Ticking Enable with a LAN bind and no certificate offers to make one on
the spot, so the feature is reachable without anyone learning what `openssl` is.

**No certificate or key ships in a release, and none ever will.** `user_settings/`
is untracked; a bundled key would be identical on every install, which means any
user could decrypt or impersonate any other user's server — strictly worse than
no TLS. Each machine generates its own, and the private key never leaves it.

The generated certificate carries `subjectAltName` for loopback, this machine's
LAN address and its hostname. Browsers read SAN and ignore CN entirely; a
certificate without it is rejected outright rather than merely warned about.

If your PC's LAN address later moves (a new DHCP lease), BitCrusher notices and
offers to regenerate. That check earns its keep: a certificate that no longer
covers the address downgrades the phone's one-time "unknown issuer" prompt into
a *name mismatch*, which reads as an attack and which some browsers will not let
you click through at all. Giving the PC a static lease on your router avoids it
entirely.

A self-signed certificate buys encryption without authentication: it stops
passive sniffing on the LAN, it does not stop an active attacker, and every
mobile browser will show an interstitial once. If that trade is not good enough
for your network, bind loopback and use a tunnel.

---

## The codec race now measures the settings it ships

By default BitCrusher races your codec against AV1, measures both by VMAF and
keeps the winner. That race was deciding between configurations that **never
shipped**. Four defects compounded, all of them pushing the same direction:

**1. The profile model's AV1 preset had never once reached the encoder.**
`select_profile()` returns SVT-AV1 presets as numbers, but both command builders
resolved them through a table keyed by x264 preset *names*. Every numeric preset
missed and fell back to 6. Preset resolution now accepts either form.

**2. The AV1 arm of the profile model was unreachable.** It required `svt` in the
encoder name, but the codec race and the CLI both hand it a bare `av1` tag — so
AV1 fell through to the libaom branch and got no preset at all. Hardware AV1
(nvenc/qsv/amf/vaapi) now correctly returns only the common parameters, since
neither the SVT numbers nor libaom's `cpu-used` apply to it.

**3. The preset formula could not reach its own clamp.** `base` spanned 6.5 to
12.5 against a clamp of 6 to 10, so all content saturated at the top and content
difficulty never moved the preset at all. The window is now per quality mode and
`base` is normalized *into* that window. The upper bound of every non-fast window
is 6 — the old effective preset — so **no content encodes faster than before**;
only hard content is allowed to go slower.

**4. The AV1 probe was single-pass while production runs two-pass.** SVT-AV1
single-pass VBR is wildly loose: it emitted 4.07 MB against a 2.03 MB probe
target, and the bit-normalizer then docked AV1 roughly 4 VMAF for an overshoot
that *only exists in the probe*. On a test-pattern clip that cost AV1 the race by
0.2 despite scoring 98.6 raw against x264's 94.8 — and the x264 output that won
instead lost to a hand-tuned AV1 encode by 2.06 VMAF. SVT probes now run
two-pass. x264/x265 stay single-pass: their maxrate cap already holds the target,
and SVT rejects maxrate outside CRF mode.

Alongside those: presets, parameters and bit depth in the probe now come from the
same sources the production command builder uses. SVT-AV1 was being probed at a
hardcoded preset 6 with a bare `tune=0` in 8-bit while production ran preset 4 or
lower with `scd=1` in 10-bit, and x264 was probed *without* the `-x264-params`
production always passes, which flattered it. On a synthetic mixed-noise clip the
old race picked x265 with AV1 1.31 behind; the new one picks AV1 by 4.17.

Film-grain synthesis is deliberately **not** mirrored into the probe: it measures
*lower* on VMAF than the grain it replaces, so racing with it would cost AV1
races it deserves to win.

When the race switches encoders, the profile is now re-run for the winner. Preset
scales are not shared across encoders — an x264 `slow` carried into SVT-AV1
silently means preset 4. Only a preset that came from the profile model is
replaced; an explicit preset you asked for still wins.

### Other encode fixes

- **Two-pass AV1 keyframe distance** was hardcoded to `-g 300`, which is 12.5
  seconds at 24fps and 5 seconds at 60fps — the same flag doing two different
  things depending on the source. It is now `clamp(fps*10, 48, 300)`, matching
  the single-pass builder.
- **`enable-variance-boost=1`** is added to the SVT parameters. It was the only
  parameter that survived a per-parameter ablation (3 clips, two-pass, matched
  bitrate, bit-normalized VMAF): +1.01 mean, positive on every clip, and it pulls
  two-pass rate control on noisy content from 1.43x its bitrate budget down to
  1.04x — retries the size controller no longer has to spend. Measured and
  **rejected**: `lookahead=120` (+0.06) and `aq-mode=2` (+0.01) were inside the
  noise, and `enable-qm` helped at presets 3 and 4 but at preset 2 blew a noisy
  clip to 1.36x its budget for -1.03, so it is not a safe unconditional default.
- That parameter is capability-gated by a cached one-frame probe that greps
  **stderr**, not the exit code: ffmpeg logs `Error parsing option KEY` for an
  unknown `-svtav1-params` key and then exits 0, so an older SVT build would have
  silently ignored it rather than failing.

### `--quality max` can now reach preset 2

Max mode's preset window is 2 to 3 and deliberately narrow. Measured against a
hand-tuned SVT-AV1 preset-3 encode at matched size (bit-normalized VMAF, 3
clips): preset 4 loses by 0.28, preset 3 draws, preset 2 wins on every clip by
0.23 mean. Preset is monotone in quality, so there is no content for which a
faster preset scores *better* — only cheaper — and a mode that has already opted
out of caring about time gains nothing from adapting.

**It costs. One preset-2 encode is roughly 4x a preset-4 one, and size targeting
runs several per job**, so preset 2 is kept to clips of 90 seconds or less, which
is the case it was measured on. A new 90s-to-4min tier floors at preset 3.

### What the whole series adds up to

Across a four-clip matched-size face-off against a hand-tuned SVT-AV1 preset-3
encode, the codec-race series moves `--quality max` from a mean VMAF deficit of
0.81 to a mean win of 0.13 — still a win after charging it for the 0.4 to 1.1
percent extra bytes it carried — and ahead on all four clips. Two of those
margins are thin enough to read as draws; the pattern and 60fps-motion wins are
clear. One clip's encode time also fell from 420s to 142s, because the
rate-control fix removes size-controller retries (though preset 2 spends that back
and more on the clips it applies to).

**Read that as a direction, not a benchmark.** Four clips, two of them synthetic,
from one real source — enough to steer the code, not enough to be a general
quality claim. It is deliberately not stated in the README for that reason, and a
`test_pipeline` corpus run is what would earn it.

---

## Themes and dialogs

**A contrast solver that cannot return invisible text.** The old lightness search
was multiplicative, and multiplying a lightness of 0 by 1.1 leaves it at 0 — so a
black input stayed black no matter how many steps it took, and it returned that
silently when it ran out. `ensure_contrast()` now steps additively, tries both
directions and takes the nearer result, preserves hue and saturation, and falls
back to black or white when the hue cannot reach the target. Zero genuine
failures across 15,000 randomised foreground/background/target checks; the only
misses left are backgrounds where no colour of any hue can pass, which is now
*reported* rather than hidden. Gamma-correct WCAG primitives (relative luminance,
contrast ratio) come with it.

**The palette generator missed its own targets on 53 percent of checks** over 400
randomised accent/background pairs — because of that multiplicative search, and
because it judged each colour against one surface and stopped. It now re-checks
every surface a colour can land on after each move, and drops to a neutral that
satisfies all of them when the hue cannot.

**Accents are now judged against the card as well as the window background, at
4.5 rather than 3.0**, because they are not decoration: they tint queue rows and
log lines. Judging them against the window background only is how a theme could
pass its own contrast check while rendering queue text at **1.53:1**. The summary
line now covers all seven judged pairs instead of three and names the failures —
it immediately flags the shipped Dark theme's own accent at 3.80.

**New Randomize button** in Theme Lab, with an any/dark/light selector. It
generates palettes that are readable by construction rather than by luck: it
keeps the background's lightness out of the mid-luminance band where nothing can
reach AA, then runs the same contrast pass a hand-built theme gets. 3,000
randomised palettes, zero with a failing pair. Undoable like every other palette
action, and it leaves the padding and border knobs alone.

**Status colours come from the palette now.** The queue's status rows were tinted
with three hexes chosen against the dark theme; on a light custom theme, "done"
measured 1.53:1 against the row background. They now go through the contrast
solver and are re-applied on every theme switch — a tag's foreground outranks the
widget style, so restyling the tree does not touch tagged rows. The log's
WARNING/ERROR tags and the feed's quality-score tag get the same treatment; the
score tag was set once at build time and went stale after any theme change.

**Thirteen dialogs now fit their own content.** Dialogs opened cropped — rows off
the bottom, Browse buttons off the right — because every window hardcoded a pixel
geometry that only held at 100 percent display scaling. Tk scales fonts and
padding with the system DPI, so at 125 or 150 percent the content outgrows the
frame while the frame stays put; several were also non-resizable, leaving no way
to reach what was hidden. Windows are now measured after they are built and grown
to fit, clamped to the screen, with a minimum size that cannot re-create the
crop. **The floor at every call site is that dialog's old hardcoded size, so this
can only grow a window, never shrink one.** Two more specific fixes: the main
window set its geometry twice and the second call stomped the intended 1400x820
back to 1200x800 (clipping the notebook tabs and the right panel — it is now the
intended size, clamped so a 1366x768 laptop still fits), and the Settings dialog
pinned its scroll container to the canvas width, so any row wider than 600px was
cut off with no horizontal scrollbar to reach it.

---

## Documentation

- **The README's feature list now matches what ships.** Backfilled from 1.3.x
  through 1.6.0 and never previously listed: multi-target exports, `-j` parallel
  encoding, the live-editable queue (per-file Target column, reorder while
  running, Encode Again With…), the post-encode reports (scene report card,
  Discord playback verdict, `--estimate`), `--suggest-trim`, cross-session
  duplicate reuse, `--name-template`, remote upload, and the workflow niceties
  (clipboard paste, keyboard shortcuts, Retry Failed, size chip, completion
  toast, auto-open output folder, startup self-check).
- **The lead screenshot is replaced.** The old one predated the sidebar and showed
  a three-pane layout the current build cannot produce, so it advertised a UI that
  no longer exists. The new one is a real run of generated demo files — no
  filename or path in the frame belongs to anybody.
- **Eight theme tiles** are eight consecutive clicks of the Theme Lab randomiser
  — no hand-picking, no retries, nothing discarded — and the worst contrast pair
  across all eight measures 4.58:1 against a 4.5:1 minimum.
- The remote-upload section states the two things a user can get wrong, in the
  same wording as the settings panel and the module docstring: enabling the
  feature does not by itself put anything on the network, and the magic-byte
  check is not protection against a malicious media file.

---

## Also in this release

- `build_bundle.py --with-remote` bundles the server extras into the portable
  Windows build. Off by default — a normal build should not carry fastapi and
  uvicorn for everyone who never turns the feature on.

## Upgrading

Nothing to do, and nothing to reconfigure. Remote upload is off, and the base
install is unchanged — if you never run `pip install -r requirements-remote.txt`,
nothing new is imported and nothing new listens.

The encode changes apply automatically. The visible one is time: `--quality max`
on clips of 90 seconds or less can now pick SVT-AV1 preset 2, which is
substantially slower than what 1.5.0 would have run. Every other quality mode is
capped at the preset 1.5.0 effectively always used, so nothing else got slower.
