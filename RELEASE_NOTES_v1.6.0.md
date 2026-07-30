# BitCrusher v1.6.0 — Remote upload

Send a file from your phone to the PC that is already running BitCrusher, and
get it back under your size cap. Works from the GUI or headless from the CLI.

This is the first inbound, network-reachable, file-accepting listener BitCrusher
has ever had, so the whole feature is built to fail closed.

---

## Remote upload

A browser on the same network signs in, uploads a file, watches the progress bar
and downloads the result. The file rides exactly the path a folder-watcher drop
takes — same rules, same queue, same encoder decisions.

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
  server-minted UUID; the phone's filename is kept only as a display label.
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

## Also in this release

- `build_bundle.py --with-remote` bundles the server extras into the portable
  Windows build. Off by default — a normal build should not carry fastapi and
  uvicorn for everyone who never turns the feature on.
- `CLAUDE.md`'s network-touchpoint invariant now lists this listener explicitly,
  so the next person reading it is not surprised by an inbound socket.

## Upgrading

Nothing to do. The feature is off, and the base install is unchanged — if you
never run `pip install -r requirements-remote.txt`, nothing new is imported and
nothing new listens.
