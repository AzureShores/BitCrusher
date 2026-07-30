"""Remote upload listener - a phone on the LAN hands a file to this instance.

TRUST MODEL (read this before changing anything here)
-----------------------------------------------------
Everything arriving over the socket is attacker-controlled: the request bytes,
the multipart filename, the Content-Type header, the password attempt and the
session cookie. Nothing from the wire is ever joined to a filesystem path.
The client's filename is kept ONLY as a display label; the file on disk is a
server-minted UUID plus an extension derived from sniffed magic bytes. That is
the path-traversal defence and it is not optional.

Magic-byte sniffing is a SANITY FILTER, NOT A SECURITY BOUNDARY. It rejects an
obvious .exe, .zip or HTML polyglot. It does NOT protect ffmpeg: ffmpeg parses
by content, and a file with a valid ftyp box and a hostile atom structure
passes every header check here and reaches the decoder intact. Do not describe
it as protection it does not provide. The mitigations that do apply are the
size cap, the UUID name, and the quarantine directory living outside anything
BitCrusher reads for configuration.

"LAN" is not a trust boundary - a dorm, office or hotel network is hostile.
So the default bind is 127.0.0.1, a non-loopback bind requires TLS, and a
non-loopback bind without a password is refused rather than degraded.

Sessions deliberately do NOT survive a restart. Persisting them would buy a
small convenience and cost a durable credential on disk; re-pairing takes
seconds. Do not "fix" this.

The server does not know what a queue is. It takes a sink (accept/status), the
same way support.sendto_ipc.IpcServer takes on_paths - which is what makes the
GUI host and the headless CLI host the same code.

Peripheral and opt-in: this never touches the encode or planning path. It hands
files to the same queue the folder watcher uses and gets out of the way.
"""

# NOTE: no `from __future__ import annotations` here, unlike the rest of
# support/. FastAPI resolves handler annotations at decoration time against the
# module globals, and `Request` is imported inside build_app (deliberately, so a
# base install never imports fastapi). Stringised annotations would therefore
# fail to resolve and every handler's `request` would be read as a query
# parameter -> 422 on every POST. Python 3.10+ is already required, so the
# `X | None` syntax below works without the future import.

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

# ---------------------------------------------------------------------------
# Optional dependencies
#
# The server stack lives in requirements-remote.txt, NOT requirements.txt, so a
# normal install never pays for it. Everything above the RemoteServer class is
# import-free on purpose: config validation, password storage and the settings
# panel's "not installed" message all work without the extras present.
# ---------------------------------------------------------------------------

# python-multipart renamed its import from `multipart` to `python_multipart`;
# accept either so the dep works on old and new releases.
REMOTE_REQUIREMENTS = ("fastapi", "uvicorn", "python_multipart|multipart")
# Not required to start: each of these degrades to a working fallback.
REMOTE_OPTIONAL_REQUIREMENTS = ("argon2", "qrcode", "cryptography")
REMOTE_INSTALL_HINT = "pip install -r requirements-remote.txt"

# Import name -> distribution name, for the message the user actually needs.
_DIST_NAMES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn[standard]",
    "python_multipart|multipart": "python-multipart",
    "argon2": "argon2-cffi",
    "qrcode": "qrcode[pil]",
    "cryptography": "cryptography",
}

# What each package is actually for, so the install prompt can say it.
_DEP_PURPOSE = {
    "fastapi": "HTTP layer",
    "uvicorn[standard]": "web server",
    "python-multipart": "streamed upload parsing (size cap during the read)",
    "argon2-cffi": "password hashing (falls back to hashlib.scrypt)",
    "qrcode[pil]": "pairing QR code (falls back to a plain link)",
    "cryptography": "self-signed TLS certificate, needed for a LAN bind",
}


def _importable(name: str) -> bool:
    import importlib.util
    for candidate in str(name).split("|"):
        try:
            if importlib.util.find_spec(candidate) is not None:
                return True
        except Exception:
            continue
    return False


def remote_deps_missing() -> list[str]:
    """Distribution names of the missing required server dependencies."""
    return [_DIST_NAMES.get(m, m) for m in REMOTE_REQUIREMENTS if not _importable(m)]


def _multipart_module():
    """python-multipart's parser under whichever name this version exposes."""
    try:
        from python_multipart.multipart import MultipartParser, parse_options_header
    except Exception:
        from multipart.multipart import MultipartParser, parse_options_header
    return MultipartParser, parse_options_header


def remote_deps_message() -> str:
    """Plain-ASCII status line naming what to install, or '' when all present."""
    missing = remote_deps_missing()
    if not missing:
        return ""
    return ("[Remote] Server dependencies not installed ("
            + ", ".join(missing) + "). Install with: " + REMOTE_INSTALL_HINT)


def remote_deps_report() -> list[dict]:
    """Every remote extra: {dist, purpose, required, installed}.

    The GUI's install prompt is built from this - the user gets told what is
    about to be downloaded and why, before anything touches the network.
    """
    out = []
    for group, required in ((REMOTE_REQUIREMENTS, True),
                            (REMOTE_OPTIONAL_REQUIREMENTS, False)):
        for mod in group:
            dist = _DIST_NAMES.get(mod, mod)
            out.append({"dist": dist,
                        "purpose": _DEP_PURPOSE.get(dist, ""),
                        "required": required,
                        "installed": _importable(mod)})
    return out


def remote_deps_pending() -> list[dict]:
    """The entries of remote_deps_report() that are not installed yet."""
    return [d for d in remote_deps_report() if not d["installed"]]


def requirements_remote_path() -> str:
    """Path to requirements-remote.txt, or '' when it is not next to the app.

    Preferred over a bare package list because it carries the version floors.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(root, "requirements-remote.txt")
    return p if os.path.isfile(p) else ""


def remote_install_command() -> list[str]:
    """The exact pip argv that install_remote_deps() would run."""
    args = [sys.executable, "-m", "pip", "install"]
    req = requirements_remote_path()
    if req:
        return args + ["-r", req]
    pending = [d["dist"] for d in remote_deps_pending()]
    return args + (pending or [d["dist"] for d in remote_deps_report()])


def remote_install_blocker() -> str:
    """Why a pip install cannot run here, or '' when it can.

    A frozen bundle has no pip and no site-packages to write to; saying so beats
    a confusing pip traceback.
    """
    if getattr(sys, "frozen", False):
        return ("This is a packaged build - it has no pip. Install the extras "
                "into a normal Python install, or run BitCrusher from source.")
    import importlib.util
    try:
        if importlib.util.find_spec("pip") is None:
            return "pip is not available in this Python installation."
    except Exception:
        return "pip is not available in this Python installation."
    return ""


def install_remote_deps(log_cb=None, timeout: float = 900.0) -> tuple[bool, str]:
    """Run pip to install the remote extras. NETWORK - user-triggered only.

    This is a peripheral, opt-in touchpoint of the same class as
    tool_installer: nothing in the encode or planning path calls it. Returns
    (ok, output) and never raises for an ordinary pip failure.
    """
    blocker = remote_install_blocker()
    if blocker:
        return False, blocker

    def emit(line):
        if log_cb:
            try:
                log_cb(line)
            except Exception:
                pass

    cmd = remote_install_command()
    emit("[Remote] Running: " + " ".join(cmd[1:]))
    lines: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace",
            creationflags=(subprocess.CREATE_NO_WINDOW
                           if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                           else 0))
    except Exception as e:
        return False, f"could not start pip: {e}"

    def _drain():
        for raw in proc.stdout:                      # type: ignore[union-attr]
            line = raw.rstrip()
            if not line:
                continue
            lines.append(line)
            emit(line)

    reader = threading.Thread(target=_drain, name="bc_pip_reader", daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return False, "pip timed out.\n" + "\n".join(lines[-20:])
    reader.join(timeout=10)

    output = "\n".join(lines)
    if proc.returncode != 0:
        return False, f"pip exited with code {proc.returncode}.\n" + "\n".join(lines[-20:])
    # pip installed into THIS interpreter, but modules already looked up stay
    # cached as missing - drop the finder caches so remote_deps_missing() sees
    # the new packages without a restart.
    try:
        import importlib
        importlib.invalidate_caches()
    except Exception:
        pass
    return True, output


class RemoteError(Exception):
    """Base for every refuse-to-start condition."""


class RemoteConfigError(RemoteError):
    """Configuration that cannot be honoured safely (see validate_remote_config)."""


class RemoteDependencyError(RemoteError):
    """Server extras are not installed."""


class RemoteStartError(RemoteError):
    """The listener could not be bound (port taken, cert unreadable, ...)."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REMOTE_DEFAULTS = {
    "enabled": False,
    "bind": "127.0.0.1",        # LAN needs an explicit change + TLS
    "port": 8420,
    "require_password": True,   # False is refused unless bind is loopback
    "tls_cert": "",             # required when bind is not loopback
    "tls_key": "",
    "max_upload_mb": 2048,
    "max_concurrent_uploads": 2,
    "session_idle_hours": 12,
    "session_max_days": 7,
    "quarantine_dir": "",       # default: <settings_dir>/remote_inbox
    "auto_pipeline": False,     # reuse pipeline_mode once queued
    "auto_start": False,        # start the server when BitCrusher launches
}

SETTINGS_KEY = "remote_upload"
AUTH_FILE = "remote_auth.json"
QUARANTINE_DIRNAME = "remote_inbox"

# An upload that nothing referenced for this long is abandoned. Deliberately
# longer than a restart cycle: the queue persists across restarts, so a shorter
# sweep would delete a file that is still sitting in queue.json.
QUARANTINE_TTL_S = 48 * 3600

PAIRING_TTL_S = 60.0
AUTH_MAX_ATTEMPTS = 5
AUTH_WINDOW_S = 300.0

# A slow client must not hold a worker: an upload gets this long overall, and
# this long between chunks.
UPLOAD_TOTAL_TIMEOUT_S = 3600.0
UPLOAD_IDLE_TIMEOUT_S = 60.0

_LOOPBACK_PREFIXES = ("127.", "::1", "localhost")


def is_loopback_bind(addr: str) -> bool:
    """True when this bind address cannot be reached from another machine."""
    a = str(addr or "").strip().lower().strip("[]")
    if not a:
        return False
    if a in ("::1", "localhost", "localhost.localdomain"):
        return True
    return a.startswith("127.")


def load_remote_config(settings: dict | None) -> dict:
    """Merge the saved remote_upload block over the defaults, coercing types.

    Unknown keys are dropped and junk values fall back to the default rather
    than propagating into a bind address or a size cap.
    """
    cfg = dict(REMOTE_DEFAULTS)
    raw = {}
    try:
        raw = dict((settings or {}).get(SETTINGS_KEY) or {})
    except Exception:
        raw = {}
    for key, default in REMOTE_DEFAULTS.items():
        if key not in raw:
            continue
        val = raw[key]
        try:
            if isinstance(default, bool):
                cfg[key] = bool(val)
            elif isinstance(default, int):
                cfg[key] = int(val)
            elif isinstance(default, str):
                cfg[key] = str(val).strip()
        except Exception:
            cfg[key] = default
    return cfg


def validate_remote_config(cfg: dict, settings_dir: str | None = None) -> list[str]:
    """Reasons this configuration must not be served. Empty list = safe to start.

    Fail closed: every one of these is a refusal, never a silent downgrade.
    """
    reasons: list[str] = []
    bind = str(cfg.get("bind") or "").strip()
    if not bind:
        reasons.append("no bind address configured")
    loopback = is_loopback_bind(bind)

    port = int(cfg.get("port") or 0)
    if not (1 <= port <= 65535):
        reasons.append(f"port {port} is out of range")

    cert = str(cfg.get("tls_cert") or "").strip()
    key = str(cfg.get("tls_key") or "").strip()
    if not loopback:
        if not (cert and key):
            reasons.append(
                f"bind {bind} is not loopback and no TLS certificate is set - "
                "refusing to serve plaintext on the network")
        if not cfg.get("require_password", True):
            reasons.append(
                f"bind {bind} is not loopback with require_password off - "
                "that is an open upload endpoint")
    if cert and not os.path.isfile(cert):
        reasons.append(f"TLS certificate not found: {cert}")
    if key and not os.path.isfile(key):
        reasons.append(f"TLS key not found: {key}")
    if bool(cert) != bool(key):
        reasons.append("TLS needs both tls_cert and tls_key")

    if int(cfg.get("max_upload_mb") or 0) <= 0:
        reasons.append("max_upload_mb must be greater than 0")
    if int(cfg.get("max_concurrent_uploads") or 0) <= 0:
        reasons.append("max_concurrent_uploads must be greater than 0")

    if cfg.get("require_password", True) and settings_dir is not None:
        if not has_password(settings_dir):
            reasons.append("no remote password is set (run --serve-set-password "
                           "or set one in Settings)")
    return reasons


def quarantine_dir_for(cfg: dict, settings_dir: str) -> str:
    d = str((cfg or {}).get("quarantine_dir") or "").strip()
    if not d:
        d = os.path.join(settings_dir, QUARANTINE_DIRNAME)
    return os.path.abspath(d)


# ---------------------------------------------------------------------------
# Password storage
#
# argon2 when the extra is installed, hashlib.scrypt otherwise - both are
# memory-hard and both verify in constant time. Never plaintext, never a
# reversible encoding.
# ---------------------------------------------------------------------------

_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 96 * 1024 * 1024


def _auth_path(settings_dir: str) -> str:
    return os.path.join(settings_dir, AUTH_FILE)


def _argon2_hasher():
    try:
        from argon2 import PasswordHasher
        return PasswordHasher()
    except Exception:
        return None


def hash_password(password: str) -> dict:
    """Hash a password into a JSON-serialisable record (no plaintext anywhere)."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    ph = _argon2_hasher()
    if ph is not None:
        return {"algo": "argon2", "hash": ph.hash(password),
                "created": int(time.time())}
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N,
                        r=_SCRYPT_R, p=_SCRYPT_P, dklen=32,
                        maxmem=_SCRYPT_MAXMEM)
    return {"algo": "scrypt", "salt": salt.hex(), "hash": dk.hex(),
            "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P,
            "created": int(time.time())}


def verify_password_hash(record: dict, password: str) -> bool:
    """Constant-time verify of a password against a stored record."""
    if not isinstance(record, dict) or not isinstance(password, str):
        return False
    algo = str(record.get("algo") or "")
    try:
        if algo == "argon2":
            from argon2 import PasswordHasher
            from argon2.exceptions import VerificationError, VerifyMismatchError
            try:
                PasswordHasher().verify(str(record.get("hash") or ""), password)
                return True
            except (VerificationError, VerifyMismatchError):
                return False
        if algo == "scrypt":
            salt = bytes.fromhex(str(record.get("salt") or ""))
            expected = bytes.fromhex(str(record.get("hash") or ""))
            dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                                n=int(record.get("n") or _SCRYPT_N),
                                r=int(record.get("r") or _SCRYPT_R),
                                p=int(record.get("p") or _SCRYPT_P),
                                dklen=len(expected) or 32,
                                maxmem=_SCRYPT_MAXMEM)
            return hmac.compare_digest(dk, expected)
    except Exception:
        return False
    return False


def _chmod_600(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass    # best effort; Windows ACLs do not map onto the POSIX bits


def set_password(settings_dir: str, password: str) -> None:
    """Write the password hash to <settings_dir>/remote_auth.json, mode 0600."""
    rec = hash_password(password)
    os.makedirs(settings_dir, exist_ok=True)
    path = _auth_path(settings_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    _chmod_600(tmp)
    os.replace(tmp, path)
    _chmod_600(path)


def read_password_record(settings_dir: str) -> dict | None:
    try:
        with open(_auth_path(settings_dir), "r", encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) and rec.get("hash") else None
    except Exception:
        return None


def has_password(settings_dir: str) -> bool:
    return read_password_record(settings_dir) is not None


def verify_password(settings_dir: str, password: str) -> bool:
    return verify_password_hash(read_password_record(settings_dir) or {}, password)


def clear_password(settings_dir: str) -> None:
    try:
        os.remove(_auth_path(settings_dir))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rate limiting
#
# Ships WITH auth, not as later hardening: it is the only thing standing between
# the password and an offline-speed guessing loop over the LAN.
# ---------------------------------------------------------------------------

class RateLimiter:
    """Fixed window per source IP: N failures per window, then locked out."""

    def __init__(self, max_attempts: int = AUTH_MAX_ATTEMPTS,
                 window_s: float = AUTH_WINDOW_S):
        self.max_attempts = int(max_attempts)
        self.window_s = float(window_s)
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, ip: str, now: float) -> list[float]:
        hits = [t for t in self._hits.get(ip, []) if now - t < self.window_s]
        if hits:
            self._hits[ip] = hits
        else:
            self._hits.pop(ip, None)
        return hits

    def check(self, ip: str, now: float | None = None) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). retry_after is 0 when allowed."""
        now = time.time() if now is None else now
        with self._lock:
            hits = self._prune(ip, now)
            if len(hits) < self.max_attempts:
                return True, 0
            oldest = min(hits)
            return False, max(1, int(self.window_s - (now - oldest)) + 1)

    def record_failure(self, ip: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._prune(ip, now)
            self._hits.setdefault(ip, []).append(now)

    def reset(self, ip: str) -> None:
        with self._lock:
            self._hits.pop(ip, None)


# ---------------------------------------------------------------------------
# Filenames and content sniffing
# ---------------------------------------------------------------------------

_UNSAFE_LABEL = re.compile(r"[\x00-\x1f\x7f<>:\"/\\|?*]")
# Bidi overrides render "cod.exe" as "cod.mp4" in a label. Strip them.
_BIDI = re.compile(r"[‪-‮⁦-⁩‎‏]")


def safe_display_name(raw, max_len: int = 120) -> str:
    """Sanitise a client-supplied filename into a DISPLAY LABEL.

    This value is never joined to a path - see quarantine_filename for the
    name that actually reaches the filesystem. It is sanitised anyway because
    it is echoed back into logs, the queue and the web page.
    """
    try:
        s = str(raw or "")
    except Exception:
        s = ""
    s = _BIDI.sub("", s)
    s = s.replace("\\", "/").split("/")[-1]     # never a directory component
    s = _UNSAFE_LABEL.sub("_", s).strip().strip(".")
    while ".." in s:
        s = s.replace("..", ".")
    if len(s) > max_len:
        root, ext = os.path.splitext(s)
        s = root[: max(1, max_len - len(ext))] + ext
    return s or "upload"


def quarantine_filename(ext: str) -> str:
    """Server-side name: a UUID plus a sniffed extension. Nothing else."""
    e = re.sub(r"[^a-z0-9]", "", str(ext or "").lower())[:8] or "bin"
    return f"{uuid.uuid4().hex}.{e}"


# Enough leading bytes to reach the second MPEG-TS sync byte at offset 188.
_HEAD_BYTES = 256

# Whitelist. Anything that does not match a known media/document signature is
# rejected - see the module docstring for what this does and does not buy.
_FTYP_BRANDS = {
    b"isom": "mp4", b"iso2": "mp4", b"mp41": "mp4", b"mp42": "mp4",
    b"avc1": "mp4", b"dash": "mp4", b"M4V ": "mp4", b"mmp4": "mp4",
    b"qt  ": "mov", b"M4A ": "m4a", b"M4B ": "m4a",
    b"3gp4": "3gp", b"3gp5": "3gp", b"3g2a": "3gp",
    b"avif": "avif", b"avis": "avif",
    b"heic": "heic", b"heix": "heic", b"hevc": "heic", b"mif1": "heic",
}


def sniff_extension(head: bytes) -> str | None:
    """Extension implied by the leading bytes, or None when unrecognised.

    Sanity filter only. A hostile file with a valid header passes.
    """
    if not head or len(head) < 12:
        return None
    b = bytes(head)

    if b[4:8] == b"ftyp":
        brand = b[8:12]
        if brand in _FTYP_BRANDS:
            return _FTYP_BRANDS[brand]
        return "mp4"                       # unknown brand, still an ISO-BMFF box
    if b[:4] == b"\x1a\x45\xdf\xa3":
        return "webm" if b"webm" in b[:64] else "mkv"
    if b[:4] == b"RIFF":
        tag = b[8:12]
        if tag == b"AVI ":
            return "avi"
        if tag == b"WAVE":
            return "wav"
        if tag == b"WEBP":
            return "webp"
        return None
    if b[:4] == b"OggS":
        return "ogg"
    if b[:4] == b"fLaC":
        return "flac"
    if b[:4] == b"FORM" and b[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if b[:3] == b"ID3" or (b[0] == 0xFF and (b[1] & 0xE0) == 0xE0):
        return "mp3"
    if b[:4] == b"FLV\x01":
        return "flv"
    if b[:4] == b"\x30\x26\xb2\x75":
        return "wmv"
    if b[:4] == b"\x00\x00\x01\xba" or b[:4] == b"\x00\x00\x01\xb3":
        return "mpg"
    # MPEG-TS: one 0x47 is just the letter G, so require the next packet's sync
    # byte too before believing it.
    if b[0] == 0x47 and len(b) > 188 and b[188] == 0x47:
        return "ts"
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if b[:2] == b"BM":
        return "bmp"
    if b[:4] in (b"II*\x00", b"MM\x00*"):
        return "tif"
    if b[:5] == b"%PDF-":
        return "pdf"
    return None


def is_probably_media(head: bytes) -> bool:
    return sniff_extension(head) is not None


# ---------------------------------------------------------------------------
# Streaming upload writer
# ---------------------------------------------------------------------------

class UploadTooLarge(RemoteError):
    """The body exceeded the cap while being read (never after)."""


class UploadTimeout(RemoteError):
    """The client stalled or took too long."""


class QuarantineWriter:
    """Writes an upload to a temp file, enforcing the cap DURING the stream.

    The partial file is deleted on every failure path, so an aborted or hostile
    upload cannot leave bytes behind or fill the disk past the cap.
    """

    def __init__(self, quarantine_dir: str, max_bytes: int,
                 total_timeout_s: float = UPLOAD_TOTAL_TIMEOUT_S,
                 idle_timeout_s: float = UPLOAD_IDLE_TIMEOUT_S):
        self.dir = quarantine_dir
        self.max_bytes = int(max_bytes)
        self.total_timeout_s = float(total_timeout_s)
        self.idle_timeout_s = float(idle_timeout_s)
        self.bytes_written = 0
        self.head = b""
        self.path: str | None = None
        self._fh = None
        self._started = time.monotonic()
        self._last = self._started

    def open(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, quarantine_filename("part"))
        self._fh = open(self.path, "wb")

    def write(self, chunk: bytes) -> None:
        if self._fh is None:
            self.open()
        now = time.monotonic()
        if now - self._started > self.total_timeout_s:
            raise UploadTimeout("upload exceeded the total time budget")
        if now - self._last > self.idle_timeout_s:
            raise UploadTimeout("client stalled mid-upload")
        self._last = now
        if self.bytes_written + len(chunk) > self.max_bytes:
            raise UploadTooLarge(
                f"upload exceeds the {self.max_bytes} byte cap")
        self._fh.write(chunk)
        self.bytes_written += len(chunk)
        if len(self.head) < _HEAD_BYTES:
            self.head = (self.head + chunk)[:_HEAD_BYTES]

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

    def discard(self) -> None:
        """Close and delete the partial file. Safe to call more than once."""
        self.close()
        if self.path:
            try:
                os.remove(self.path)
            except Exception:
                pass
            self.path = None

    def finalize(self, ext: str) -> str:
        """Rename the .part file to its final UUID + sniffed extension."""
        self.close()
        if not self.path:
            raise RemoteError("nothing was written")
        final = os.path.join(self.dir, quarantine_filename(ext))
        os.replace(self.path, final)
        self.path = final
        return final


# ---------------------------------------------------------------------------
# Session / pairing / job stores
# ---------------------------------------------------------------------------

def _digest(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


class SessionStore:
    """In-memory sessions, pairings and job ownership behind one lock.

    Records are keyed by SHA-256 of the token so the dict key is not itself the
    secret, and the token comparison is hmac.compare_digest. Nothing here is
    written to disk - a restart invalidates every session, on purpose.
    """

    def __init__(self, idle_hours: float = 12.0, max_days: float = 7.0):
        self.idle_s = float(idle_hours) * 3600.0
        self.max_s = float(max_days) * 86400.0
        self._sessions: dict[str, dict] = {}
        self._pairings: dict[str, dict] = {}
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    # -- sessions ---------------------------------------------------------
    def new_session(self, device_label: str = "", now: float | None = None) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[_digest(token)] = {
                "token": token,
                "created": now,
                "last_seen": now,
                "expires": now + self.max_s,
                "device_label": str(device_label or "")[:64],
                "job_ids": [],
            }
        return token

    def validate(self, token: str, now: float | None = None) -> dict | None:
        """Return the session record and touch last_seen, or None."""
        if not token:
            return None
        now = time.time() if now is None else now
        with self._lock:
            rec = self._sessions.get(_digest(token))
            if rec is None or not hmac.compare_digest(rec["token"], str(token)):
                return None
            if now > rec["expires"] or (now - rec["last_seen"]) > self.idle_s:
                self._sessions.pop(_digest(token), None)
                return None
            rec["last_seen"] = now
            return dict(rec)

    def drop(self, token: str) -> bool:
        with self._lock:
            return self._sessions.pop(_digest(token or ""), None) is not None

    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    # -- pairing ----------------------------------------------------------
    def mint_pairing(self, issued_by: str = "", now: float | None = None) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._pairings[_digest(token)] = {
                "token": token,
                "expires": now + PAIRING_TTL_S,
                "used": False,
                "issued_by": _digest(issued_by) if issued_by else "",
            }
        return token

    def redeem_pairing(self, token: str, device_label: str = "",
                       now: float | None = None) -> str | None:
        """Single-use redemption. The token is invalidated inside the same lock
        that reads it, so two simultaneous redemptions cannot both win."""
        if not token:
            return None
        now = time.time() if now is None else now
        with self._lock:
            key = _digest(token)
            rec = self._pairings.get(key)
            if rec is None or not hmac.compare_digest(rec["token"], str(token)):
                return None
            if rec["used"] or now > rec["expires"]:
                self._pairings.pop(key, None)
                return None
            self._pairings.pop(key, None)   # single-use, burned here
            session_token = secrets.token_urlsafe(32)
            self._sessions[_digest(session_token)] = {
                "token": session_token,
                "created": now,
                "last_seen": now,
                "expires": now + self.max_s,
                "device_label": str(device_label or "")[:64],
                "job_ids": [],
            }
        return session_token

    # -- jobs -------------------------------------------------------------
    def register_job(self, session_token: str, job_id: str, meta: dict,
                     now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            sess = self._sessions.get(_digest(session_token))
            if sess is not None:
                sess["job_ids"].append(job_id)
            self._jobs[job_id] = {"session": _digest(session_token),
                                  "created": now, "meta": dict(meta or {})}

    def job_owned_by(self, session_token: str, job_id: str) -> dict | None:
        """Job record, but only if THIS session created it.

        Session scoping is what stops device A enumerating device B's jobs.
        """
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            if not hmac.compare_digest(rec["session"], _digest(session_token)):
                return None
            return dict(rec)

    def jobs_for(self, session_token: str) -> list[str]:
        d = _digest(session_token)
        with self._lock:
            return [j for j, r in self._jobs.items() if r["session"] == d]

    # -- housekeeping -----------------------------------------------------
    def sweep(self, now: float | None = None) -> dict:
        """Drop expired sessions, pairings and orphaned jobs."""
        now = time.time() if now is None else now
        dropped = {"sessions": 0, "pairings": 0, "jobs": 0}
        with self._lock:
            for key, rec in list(self._sessions.items()):
                if now > rec["expires"] or (now - rec["last_seen"]) > self.idle_s:
                    self._sessions.pop(key, None)
                    dropped["sessions"] += 1
            for key, rec in list(self._pairings.items()):
                if rec["used"] or now > rec["expires"]:
                    self._pairings.pop(key, None)
                    dropped["pairings"] += 1
            live = set(self._sessions.keys())
            for job_id, rec in list(self._jobs.items()):
                if rec["session"] not in live:
                    self._jobs.pop(job_id, None)
                    dropped["jobs"] += 1
        return dropped


def sweep_quarantine(quarantine_dir: str, keep_paths=(),
                     ttl_s: float = QUARANTINE_TTL_S,
                     now: float | None = None) -> int:
    """Delete abandoned quarantine files. Returns how many went.

    keep_paths protects anything still referenced by a live job; the TTL is
    longer than a restart cycle because the queue itself survives restarts.
    """
    now = time.time() if now is None else now
    keep = {os.path.abspath(p) for p in (keep_paths or []) if p}
    removed = 0
    try:
        entries = os.listdir(quarantine_dir)
    except Exception:
        return 0
    for name in entries:
        path = os.path.abspath(os.path.join(quarantine_dir, name))
        if path in keep:
            continue
        try:
            if not os.path.isfile(path):
                continue
            if now - os.path.getmtime(path) < ttl_s:
                continue
            os.remove(path)
            removed += 1
        except Exception:
            continue
    return removed


def free_disk_bytes(path: str) -> int:
    try:
        return int(shutil.disk_usage(path).free)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Sink protocol
# ---------------------------------------------------------------------------

class UploadSink:
    """What the server needs from its host. The server knows nothing else.

    accept(path, meta) -> job_id   hand the quarantined file to the host
    status(job_id) -> {"state", "pct", "out_path", "error"}
    """

    def accept(self, path: str, meta: dict) -> str:      # pragma: no cover - interface
        raise NotImplementedError

    def status(self, job_id: str) -> dict:               # pragma: no cover - interface
        raise NotImplementedError


def blank_status(state: str = "queued") -> dict:
    return {"state": state, "pct": 0, "out_path": "", "error": ""}


# ---------------------------------------------------------------------------
# LAN address helper (for the QR payload and the printed URL)
# ---------------------------------------------------------------------------

def primary_lan_ip() -> str:
    """Best-guess LAN address of this host. No traffic is actually sent."""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))     # TEST-NET-1: never routed
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        try:
            if s:
                s.close()
        except Exception:
            pass


def server_url(cfg: dict) -> str:
    bind = str(cfg.get("bind") or "127.0.0.1")
    port = int(cfg.get("port") or REMOTE_DEFAULTS["port"])
    scheme = "https" if (cfg.get("tls_cert") and cfg.get("tls_key")) else "http"
    host = bind
    if bind in ("0.0.0.0", "::", ""):
        host = primary_lan_ip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


CERT_NAME = "remote_cert.pem"
KEY_NAME = "remote_key.pem"
CERT_DAYS = 825


def cert_paths(settings_dir: str) -> tuple:
    return (os.path.join(settings_dir, CERT_NAME),
            os.path.join(settings_dir, KEY_NAME))


def cert_hosts(extra=()) -> list:
    """Names/addresses a generated certificate should be valid for.

    The LAN address is the one that matters, but loopback and the hostname are
    included so the same certificate keeps working when the user reaches the
    server a different way.
    """
    hosts = ["127.0.0.1", "::1", "localhost"]
    try:
        ip = primary_lan_ip()
        if ip and ip not in hosts:
            hosts.append(ip)
    except Exception:
        pass
    try:
        name = socket.gethostname()
        if name and name not in hosts:
            hosts.append(name)
    except Exception:
        pass
    for h in extra or ():
        if h and h not in hosts:
            hosts.append(str(h))
    return hosts


def generate_self_signed_cert(settings_dir: str, hosts=None,
                              overwrite: bool = True) -> tuple:
    """Write a self-signed cert + key for this machine. Returns (cert, key).

    This buys ENCRYPTION WITHOUT AUTHENTICATION: it stops passive sniffing on
    the LAN, it does not stop an active attacker, and every browser will show an
    interstitial. That trade is the documented v1 choice - see
    docs/dev/REMOTE_UPLOAD.md section 3.2.

    The key never leaves this machine. It is generated per-install precisely
    because a shipped key would be shared by every user, which is strictly worse
    than no TLS at all.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except Exception as e:
        raise RemoteDependencyError(
            "certificate generation needs the 'cryptography' package "
            f"({e}). Install with: {REMOTE_INSTALL_HINT}") from e

    import datetime
    import ipaddress

    hosts = list(hosts or cert_hosts())
    cert_path, key_path = cert_paths(settings_dir)
    if not overwrite and os.path.isfile(cert_path) and os.path.isfile(key_path):
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    primary = next((h for h in hosts if h not in ("127.0.0.1", "::1", "localhost")),
                   "127.0.0.1")
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, primary),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "BitCrusher remote upload"),
    ])
    alt = []
    for h in hosts:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            alt.append(x509.DNSName(h))
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))   # clock skew
        .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
        # Browsers ignore CN and read subjectAltName only; without this the
        # certificate is rejected outright rather than merely warned about.
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                       critical=True)
        .sign(key, hashes.SHA256())
    )

    os.makedirs(settings_dir, exist_ok=True)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))
    _chmod_600(key_path)
    return cert_path, key_path


def cert_covers_host(cert_path: str, host: str) -> bool:
    """True when this certificate's subjectAltName includes `host`.

    Browsers match on SAN alone. If the PC's DHCP lease moves it to a new
    address, the old certificate stops matching and the phone's warning changes
    from "unknown issuer" (accept once) to a name mismatch, which reads as an
    attack and which some browsers refuse to let you click through.
    """
    try:
        from cryptography import x509
    except Exception:
        return True         # cannot check without the extra; do not cry wolf
    try:
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        names = {str(v) for v in san.get_values_for_type(x509.IPAddress)}
        names |= set(san.get_values_for_type(x509.DNSName))
        return str(host) in names
    except Exception:
        return False


def cert_stale_reason(settings_dir: str, cfg: dict) -> str:
    """Why the configured certificate no longer fits, or '' when it is fine.

    Only meaningful for a non-loopback bind - on loopback the address never
    moves.
    """
    if is_loopback_bind(str((cfg or {}).get("bind") or "")):
        return ""
    cert = str((cfg or {}).get("tls_cert") or "")
    if not cert or not os.path.isfile(cert):
        return ""                       # a missing cert is a different problem
    ip = primary_lan_ip()
    if ip and ip != "127.0.0.1" and not cert_covers_host(cert, ip):
        return (f"this PC's address is now {ip}, which the certificate does not "
                f"cover - phones will report a name mismatch instead of a "
                f"one-time warning")
    try:
        from cryptography import x509
        import datetime
        with open(cert, "rb") as f:
            parsed = x509.load_pem_x509_certificate(f.read())
        try:
            expires = parsed.not_valid_after_utc
            now = datetime.datetime.now(datetime.timezone.utc)
        except AttributeError:
            expires = parsed.not_valid_after
            now = datetime.datetime.utcnow()
        if expires < now:
            return "the certificate has expired"
        if (expires - now).days < 30:
            return f"the certificate expires in {(expires - now).days} day(s)"
    except Exception:
        pass
    return ""


def describe_cert(cert_path: str) -> str:
    """One plain-ASCII line about an existing certificate, or '' if unreadable."""
    try:
        from cryptography import x509
    except Exception:
        return ""
    try:
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        names = []
        try:
            san = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName).value
            names = [str(v) for v in san.get_values_for_type(x509.IPAddress)]
            names += san.get_values_for_type(x509.DNSName)
        except Exception:
            pass
        try:
            expires = cert.not_valid_after_utc
        except AttributeError:
            expires = cert.not_valid_after
        return (f"valid for {', '.join(names) or '?'} "
                f"until {expires.strftime('%Y-%m-%d')}")
    except Exception:
        return ""


def qr_ascii(payload: str) -> str:
    """QR as ASCII art, or '' when the optional qrcode extra is not installed.

    Never raises: a missing extra degrades to the printed URL, it does not
    break the pairing flow.
    """
    try:
        import qrcode
    except Exception:
        return ""
    try:
        import io
        qr = qrcode.QRCode(border=1)
        qr.add_data(payload)
        qr.make(fit=True)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        return buf.getvalue()
    except Exception:
        return ""


def qr_svg_data_uri(payload: str) -> str:
    """QR as an inline data: URI, or '' without the qrcode extra.

    Inline on purpose - the served page must not fetch anything (offline rule).
    """
    try:
        import qrcode
        import qrcode.image.svg as qrsvg
    except Exception:
        return ""
    try:
        import base64
        import io
        img = qrcode.make(payload, image_factory=qrsvg.SvgPathImage, border=2)
        buf = io.BytesIO()
        img.save(buf)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return "data:image/svg+xml;base64," + b64
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# HTTP layer
#
# Everything above this line imports nothing optional. Everything below needs
# the requirements-remote.txt extras and is imported lazily so a base install
# can still validate config, set a password and render the settings panel.
# ---------------------------------------------------------------------------

COOKIE_NAME = "bc_remote"
_CHUNK = 64 * 1024

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # A pairing token rides in the /pair/{token} URL; no-referrer keeps it out
    # of any outbound Referer.
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
}


def _content_disposition(name: str) -> str:
    """attachment header carrying BOTH the ASCII and the RFC 5987 filename.

    Modern browsers prefer filename*=; older mobile ones only understand
    filename=, and given neither they save the URL's last segment - which here
    is a job UUID, not anything the user recognises.
    """
    from urllib.parse import quote
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "")
    ascii_name = ascii_name.replace("?", "_") or "download"
    return (f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(name, safe='')}")


def _client_ip(request) -> str:
    """Source IP. Deliberately NOT X-Forwarded-For: there is no trusted proxy
    in front of this, so honouring that header would let a client forge the
    identity the rate limiter keys on."""
    try:
        return str(request.client.host or "unknown")
    except Exception:
        return "unknown"


def _same_origin(request) -> bool:
    """Reject a cross-origin state change. SameSite=Strict already covers the
    cookie; this is the belt to that braces."""
    origin = request.headers.get("origin")
    if not origin:
        return True                     # non-browser client (curl) has none
    try:
        from urllib.parse import urlparse
        host = (request.headers.get("host") or "").lower()
        return urlparse(origin).netloc.lower() == host
    except Exception:
        return False


async def _stream_multipart_file(request, writer: QuarantineWriter,
                                 field: str = "file",
                                 body_slack: int = 1024 * 1024) -> str:
    """Stream one multipart file part into `writer`, enforcing the cap as we go.

    Returns the client-supplied filename (a display label only). Raises
    UploadTooLarge / UploadTimeout / RemoteError; the caller discards the
    partial file. Nothing is buffered in memory beyond one chunk.
    """
    MultipartParser, parse_options_header = _multipart_module()

    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    if ctype != b"multipart/form-data":
        raise RemoteError("expected multipart/form-data")
    boundary = params.get(b"boundary")
    if not boundary:
        raise RemoteError("multipart boundary missing")

    state = {"name": b"", "filename": "", "in_file": False,
             "hdr_field": b"", "hdr_value": b"", "seen": False}

    def on_header_field(data, start, end):
        state["hdr_field"] += data[start:end]

    def on_header_value(data, start, end):
        state["hdr_value"] += data[start:end]

    def on_header_end():
        if state["hdr_field"].lower() == b"content-disposition":
            _, opts = parse_options_header(state["hdr_value"])
            state["name"] = opts.get(b"name", b"")
            fn = opts.get(b"filename")
            if fn is not None:
                state["filename"] = fn.decode("utf-8", "replace")
        state["hdr_field"] = b""
        state["hdr_value"] = b""

    def on_headers_finished():
        state["in_file"] = (state["name"].decode("latin-1", "replace") == field)
        if state["in_file"]:
            state["seen"] = True

    def on_part_data(data, start, end):
        if state["in_file"]:
            writer.write(data[start:end])

    def on_part_end():
        state["in_file"] = False
        state["name"] = b""

    parser = MultipartParser(boundary, {
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
    })

    # The file cap is enforced by the writer; this second budget bounds the
    # whole body so a hostile client cannot stream unbounded non-file parts.
    body_budget = writer.max_bytes + body_slack
    read = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        read += len(chunk)
        if read > body_budget:
            raise UploadTooLarge("request body exceeds the upload cap")
        parser.write(chunk)
    parser.finalize()
    if not state["seen"]:
        raise RemoteError(f"no '{field}' part in the request")
    return state["filename"]


def build_app(sink, settings_dir: str, cfg: dict, sessions: SessionStore,
              limiter: RateLimiter, status_cb=None, tls: bool = False,
              palette=None):
    """Build the ASGI app. Imported lazily - needs the remote extras.

    `palette` is a desktop THEMES entry, or a callable returning one, so the
    served page follows the theme the user is looking at. A callable is read per
    request (and the result cached by colour), which is what makes a theme
    change on the desktop show up on the phone without a restart.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                                   RedirectResponse)

    def log(msg, level="INFO"):
        # Plain ASCII, [Remote] prefix, and never the password, the session
        # token or the pairing token - not even truncated. Source IPs are fine.
        if status_cb:
            try:
                status_cb(f"[Remote] {msg}", level)
            except Exception:
                pass

    quarantine = quarantine_dir_for(cfg, settings_dir)
    max_bytes = int(cfg.get("max_upload_mb", 2048)) * 1024 * 1024
    upload_slots = threading.BoundedSemaphore(
        max(1, int(cfg.get("max_concurrent_uploads", 2))))
    require_password = bool(cfg.get("require_password", True))
    # Pairing redemption gets its OWN budget. Sharing the /auth limiter meant a
    # phone that reloaded a stale pairing link burned the password-login budget
    # and locked itself out of both.
    pair_limiter = RateLimiter(max_attempts=limiter.max_attempts,
                               window_s=limiter.window_s)
    # Passwordless mode only survives validation on a loopback bind, i.e. when
    # nothing off this machine can reach the socket. One shared session then
    # stands in for the login, so job scoping still has something to key on.
    _open_session = (None if require_password
                     else sessions.new_session("loopback"))

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        for k, v in _SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    def _json(payload, status=200, **kw):
        return JSONResponse(payload, status_code=status, **kw)

    def _set_session_cookie(resp, token: str):
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict",
                        secure=bool(tls), path="/")

    def _session(request):
        """(token, record) for a valid session, or (None, None)."""
        nonlocal _open_session
        if not require_password:
            rec = sessions.validate(_open_session) if _open_session else None
            if rec is None:                     # idle-expired; mint a new one
                _open_session = sessions.new_session("loopback")
                rec = sessions.validate(_open_session)
            return _open_session, rec
        token = request.cookies.get(COOKIE_NAME) or ""
        return (token, sessions.validate(token)) if token else (None, None)

    # -- pages ------------------------------------------------------------
    _page_cache = {"sig": None, "html": None}

    def _page() -> str:
        pal = palette
        if callable(pal):
            try:
                pal = pal()
            except Exception:
                pal = None
        sig = palette_signature(pal)
        if _page_cache["sig"] != sig:
            _page_cache["sig"] = sig
            _page_cache["html"] = render_page(pal)
        return _page_cache["html"]

    @app.get("/", response_class=HTMLResponse)
    async def index():
        # Identical response whether or not a session exists: the page itself
        # discloses nothing, the JS probes an authenticated endpoint.
        return HTMLResponse(_page())

    # -- auth -------------------------------------------------------------
    @app.post("/auth")
    async def auth(request: Request):
        ip = _client_ip(request)
        if not _same_origin(request):
            return _json({"ok": False, "error": "cross-origin request refused"}, 403)
        allowed, retry_after = limiter.check(ip)
        if not allowed:
            log(f"auth lockout for {ip} - {retry_after}s remaining", "WARNING")
            return _json({"ok": False, "error": "too many attempts"}, 429,
                         headers={"Retry-After": str(retry_after)})
        try:
            body = await request.json()
            password = str((body or {}).get("password") or "")
        except Exception:
            password = ""
        # The KDF always runs (start-up refuses a passwordless network bind),
        # so wrong-password and unknown-state cost the same time and return the
        # same body.
        if password and verify_password(settings_dir, password):
            limiter.reset(ip)
            token = sessions.new_session(device_label=ip)
            resp = _json({"ok": True})
            _set_session_cookie(resp, token)
            log(f"session opened for {ip}")
            return resp
        limiter.record_failure(ip)
        log(f"auth failed for {ip}", "WARNING")
        return _json({"ok": False, "error": "invalid credentials"}, 401)

    @app.post("/logout")
    async def logout(request: Request):
        token = request.cookies.get(COOKIE_NAME) or ""
        if token:
            sessions.drop(token)        # server-side, not just the cookie
        resp = _json({"ok": True})
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp

    # -- pairing ----------------------------------------------------------
    @app.post("/pair/new")
    async def pair_new(request: Request):
        token, rec = _session(request)
        if not rec:
            return _json({"ok": False, "error": "not authenticated"}, 401)
        if not _same_origin(request):
            return _json({"ok": False, "error": "cross-origin request refused"}, 403)
        pairing = sessions.mint_pairing(issued_by=token)
        url = f"{server_url(cfg)}/pair/{pairing}"
        log(f"pairing token issued to {_client_ip(request)}")
        return _json({"ok": True, "url": url, "expires_in": int(PAIRING_TTL_S),
                      "qr": qr_svg_data_uri(url)})

    @app.get("/pair/{token}")
    async def pair_redeem(token: str, request: Request):
        ip = _client_ip(request)
        allowed, retry_after = pair_limiter.check(ip)
        if not allowed:
            return _json({"ok": False, "error": "too many attempts"}, 429,
                         headers={"Retry-After": str(retry_after)})
        session_token = sessions.redeem_pairing(token, device_label=ip)
        if not session_token:
            pair_limiter.record_failure(ip)
            log(f"pairing redemption refused for {ip} (expired, used or unknown)",
                "WARNING")
            return _json({"ok": False, "error": "pairing link is not valid"}, 403)
        # 303 straight back to / so the one-time token does not linger in the
        # address bar or the history entry that gets shared.
        resp = RedirectResponse("/", status_code=303)
        _set_session_cookie(resp, session_token)
        log(f"device paired from {ip}")
        return resp

    # -- upload -----------------------------------------------------------
    @app.post("/upload")
    async def upload(request: Request):
        token, rec = _session(request)
        if not rec:
            return _json({"ok": False, "error": "not authenticated"}, 401)
        if not _same_origin(request):
            return _json({"ok": False, "error": "cross-origin request refused"}, 403)
        ip = _client_ip(request)

        if not upload_slots.acquire(blocking=False):
            log(f"upload refused for {ip} - concurrent upload cap reached", "WARNING")
            return _json({"ok": False, "error": "server busy, try again"}, 429,
                         headers={"Retry-After": "10"})

        writer = QuarantineWriter(quarantine, max_bytes)
        try:
            try:
                declared = int(request.headers.get("content-length") or 0)
            except Exception:
                declared = 0
            if declared and declared > max_bytes + 1024 * 1024:
                log(f"upload refused for {ip} - declared size over the cap", "WARNING")
                return _json({"ok": False, "error": "file exceeds the size cap"}, 413)

            try:
                client_name = await _stream_multipart_file(request, writer)
            except UploadTooLarge:
                writer.discard()
                log(f"upload aborted for {ip} - exceeded the size cap", "WARNING")
                return _json({"ok": False, "error": "file exceeds the size cap"}, 413)
            except UploadTimeout as e:
                writer.discard()
                log(f"upload aborted for {ip} - {e}", "WARNING")
                return _json({"ok": False, "error": "upload timed out"}, 408)
            except OSError as e:
                writer.discard()
                if getattr(e, "errno", None) == 28:      # ENOSPC
                    log(f"upload aborted for {ip} - disk full", "ERROR")
                    return _json({"ok": False, "error": "server is out of disk space"}, 507)
                log(f"upload aborted for {ip} - write error: {e}", "ERROR")
                return _json({"ok": False, "error": "could not store the upload"}, 500)
            except Exception as e:
                writer.discard()
                log(f"upload rejected for {ip} - malformed request ({e})", "WARNING")
                return _json({"ok": False, "error": "malformed upload"}, 400)

            if writer.bytes_written <= 0:
                writer.discard()
                return _json({"ok": False, "error": "empty upload"}, 400)

            ext = sniff_extension(writer.head)
            if ext is None:
                writer.discard()
                log(f"upload rejected for {ip} - unrecognised file type", "WARNING")
                return _json({"ok": False,
                              "error": "that does not look like a media file"}, 415)

            label = safe_display_name(client_name)
            path = writer.finalize(ext)
            meta = {"display_name": label, "bytes": writer.bytes_written,
                    "source_ip": ip, "received": time.time()}
            try:
                job_id = str(sink.accept(path, meta) or uuid.uuid4().hex)
            except Exception as e:
                writer.discard()        # path is the finalized name by now
                log(f"queueing failed for {ip}: {e}", "ERROR")
                return _json({"ok": False, "error": "could not queue the file"}, 500)
            sessions.register_job(token, job_id, dict(meta, path=path))
            log(f"accepted {label} ({writer.bytes_written} bytes) from {ip}")
            return _json({"ok": True, "job_id": job_id, "name": label})
        finally:
            upload_slots.release()

    # -- status / download ------------------------------------------------
    @app.get("/status/{job_id}")
    async def status(job_id: str, request: Request):
        token, rec = _session(request)
        if not rec:
            return _json({"ok": False, "error": "not authenticated"}, 401)
        owned = sessions.job_owned_by(token, job_id)
        if owned is None:
            # Same answer for "not yours" and "does not exist" - no enumeration.
            return _json({"ok": False, "error": "unknown job"}, 404)
        try:
            st = dict(blank_status(), **(sink.status(job_id) or {}))
        except Exception as e:
            st = dict(blank_status("failed"), error=str(e))
        st["name"] = (owned.get("meta") or {}).get("display_name", "")
        st["ok"] = True
        st["can_download"] = bool(st.get("out_path")) and st.get("state") == "done"
        st.pop("out_path", None)        # server path is not the client's business
        return _json(st)

    @app.get("/jobs")
    async def jobs(request: Request):
        """This session's jobs, so a page reload does not lose them.

        The browser's job list is in-memory JavaScript; without this, closing
        the tab or letting the phone background it stranded finished files with
        no way to reach them again.
        """
        token, rec = _session(request)
        if not rec:
            return _json({"ok": False, "error": "not authenticated"}, 401)
        out = []
        for job_id in sessions.jobs_for(token):
            owned = sessions.job_owned_by(token, job_id) or {}
            try:
                st = dict(blank_status(), **(sink.status(job_id) or {}))
            except Exception as e:
                st = dict(blank_status("failed"), error=str(e))
            out.append({
                "job_id": job_id,
                "name": (owned.get("meta") or {}).get("display_name", ""),
                "state": st.get("state"), "pct": st.get("pct"),
                "error": st.get("error"),
                "can_download": bool(st.get("out_path")) and st.get("state") == "done",
                "created": owned.get("created", 0),
            })
        out.sort(key=lambda j: j.get("created") or 0)
        return _json({"ok": True, "jobs": out})

    @app.get("/download/{job_id}")
    async def download(job_id: str, request: Request):
        token, rec = _session(request)
        if not rec:
            return _json({"ok": False, "error": "not authenticated"}, 401)
        owned = sessions.job_owned_by(token, job_id)
        if owned is None:
            return _json({"ok": False, "error": "unknown job"}, 404)
        try:
            st = sink.status(job_id) or {}
        except Exception:
            st = {}
        out = str(st.get("out_path") or "")
        if not out or not os.path.isfile(out):
            return _json({"ok": False, "error": "not ready"}, 409)
        label = (owned.get("meta") or {}).get("display_name") or os.path.basename(out)
        name = safe_display_name(os.path.splitext(label)[0] +
                                 os.path.splitext(out)[1])
        # Both header forms on purpose. Starlette emits only filename*= once the
        # name has a space or any non-token character, and older mobile browsers
        # ignore filename*= entirely - they would save the job UUID instead.
        return FileResponse(out, media_type="application/octet-stream",
                            headers={"Content-Disposition":
                                     _content_disposition(name)})

    return app


class RemoteServer:
    """The listener. Refuses to start rather than degrading.

    Every failure here is loud: a taken port, a LAN bind without a certificate
    and a passwordless network bind all raise instead of silently choosing
    something else. Never fall back to a different port - the QR and the
    printed URL would point at the wrong place.
    """

    def __init__(self, sink, settings_dir: str, config: dict | None = None,
                 status_cb=None, palette=None):
        self.sink = sink
        self.settings_dir = settings_dir
        self.config = dict(REMOTE_DEFAULTS, **(config or {}))
        self.status_cb = status_cb
        # Theme for the served page: a palette dict, or a callable returning one
        # (the GUI passes a callable so a theme switch reaches the phone).
        self.palette = palette
        self.sessions = SessionStore(
            idle_hours=float(self.config.get("session_idle_hours", 12)),
            max_days=float(self.config.get("session_max_days", 7)))
        self.limiter = RateLimiter()
        self.quarantine_dir = quarantine_dir_for(self.config, settings_dir)
        self._sock: socket.socket | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._janitor: threading.Thread | None = None
        self._stop = threading.Event()
        self.running = False

    # -- helpers ----------------------------------------------------------
    def _log(self, msg, level="INFO"):
        if self.status_cb:
            try:
                self.status_cb(f"[Remote] {msg}", level)
            except Exception:
                pass

    @property
    def tls_enabled(self) -> bool:
        return bool(self.config.get("tls_cert") and self.config.get("tls_key"))

    @property
    def url(self) -> str:
        return server_url(self.config)

    def _bind(self) -> socket.socket:
        host = str(self.config.get("bind") or "127.0.0.1")
        port = int(self.config.get("port"))
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        # Deliberately NOT SO_REUSEADDR: squatting on a port another process
        # already owns is exactly the confusion this must not create.
        try:
            sock.bind((host, port))
            sock.listen(16)
        except OSError as e:
            try:
                sock.close()
            except Exception:
                pass
            raise RemoteStartError(
                f"cannot bind {host}:{port} ({e.strerror or e}). The port is in "
                f"use or the address is not available - refusing to start on a "
                f"different port.") from e
        return sock

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        missing = remote_deps_missing()
        if missing:
            raise RemoteDependencyError(
                "server dependencies not installed (" + ", ".join(missing) +
                "). Install with: " + REMOTE_INSTALL_HINT)
        reasons = validate_remote_config(self.config, self.settings_dir)
        if reasons:
            raise RemoteConfigError("; ".join(reasons))

        os.makedirs(self.quarantine_dir, exist_ok=True)
        swept = sweep_quarantine(self.quarantine_dir)
        if swept:
            self._log(f"swept {swept} abandoned upload(s) from quarantine")

        import uvicorn
        app = build_app(self.sink, self.settings_dir, self.config, self.sessions,
                        self.limiter, status_cb=self.status_cb,
                        tls=self.tls_enabled, palette=self.palette)
        self._sock = self._bind()
        kwargs = {
            "app": app,
            "log_level": "warning",
            # A pairing token rides in the /pair/{token} path, and uvicorn's
            # access log would write it to disk. Off, permanently.
            "access_log": False,
            "timeout_keep_alive": 15,
        }
        if self.tls_enabled:
            kwargs["ssl_certfile"] = str(self.config["tls_cert"])
            kwargs["ssl_keyfile"] = str(self.config["tls_key"])
        try:
            config = uvicorn.Config(**kwargs)
            self._server = uvicorn.Server(config)
        except Exception as e:
            self._close_socket()
            raise RemoteStartError(f"could not configure the server: {e}") from e

        sock = self._sock
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(sock,), name="bc_remote", daemon=True)
        self._thread.start()
        self._janitor = threading.Thread(
            target=self._sweep_loop, name="bc_remote_janitor", daemon=True)
        self._janitor.start()
        self.running = True

        scope = ("loopback only" if is_loopback_bind(str(self.config.get("bind")))
                 else "LAN")
        self._log(f"listening on {self.url} ({scope}, "
                  f"{'TLS' if self.tls_enabled else 'plaintext'})")

    def _run(self, sock):
        try:
            self._server.run(sockets=[sock])
        except Exception as e:
            self._log(f"server stopped unexpectedly: {e}", "ERROR")
        finally:
            self.running = False

    def _sweep_loop(self):
        while not self._stop.wait(300.0):
            try:
                dropped = self.sessions.sweep()
                if dropped["sessions"] or dropped["pairings"]:
                    self._log(f"expired {dropped['sessions']} session(s), "
                              f"{dropped['pairings']} pairing(s)")
                live = set()
                for job_id in list(self.sessions._jobs.keys()):
                    rec = self.sessions._jobs.get(job_id) or {}
                    p = (rec.get("meta") or {}).get("path")
                    if p:
                        live.add(p)
                sweep_quarantine(self.quarantine_dir, keep_paths=live)
            except Exception:
                pass

    def _close_socket(self):
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.should_exit = True
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=10.0)
        self._close_socket()
        self._server = None
        self._thread = None
        self.running = False
        self._log("stopped")


# ---------------------------------------------------------------------------
# Frontend - one server-rendered page, inline CSS/JS, no CDN and no build step.
# The offline rule applies to the page too: a <script src="https://..."> would
# break on an air-gapped machine and leak a request on a normal one.
#
# The palette is the app's own theme, injected as CSS variables at request time
# so the phone looks like the desktop window that answers it. Every value is
# validated as a hex colour before it reaches the stylesheet - a theme file is
# user-editable JSON, and unvalidated text substituted into CSS is an injection
# point.
# ---------------------------------------------------------------------------

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Same keys as the desktop THEMES entries, so a palette can be passed straight
# through. Fallback is the previous hardcoded dark page.
_PALETTE_FALLBACK = {
    "APP_BG": "#14161a", "CARD_BG": "#1d2026",
    "FG": "#e8eaee", "FG_SUB": "#9aa3b0",
    "ACCENT": "#59a5f5", "ACCENT_2": "#4ec98a",
    "ERROR": "#f0736a", "WARN": "#e9b949", "TITLE": "#e8eaee",
}


def _hex_rgb(value: str) -> tuple:
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _mix(a: str, b: str, t: float) -> str:
    """`t` of the way from colour a to colour b."""
    ra, rb = _hex_rgb(a), _hex_rgb(b)
    return _rgb_hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


def _luminance(value: str) -> float:
    r, g, b = (c / 255.0 for c in _hex_rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _readable_on(value: str) -> str:
    """Black or white text, whichever survives on this background."""
    return "#0b0d10" if _luminance(value) > 0.55 else "#ffffff"


def normalize_palette(palette) -> dict:
    """A full, hex-validated palette. Anything unusable falls back per key."""
    out = dict(_PALETTE_FALLBACK)
    if isinstance(palette, dict):
        for key in _PALETTE_FALLBACK:
            val = str(palette.get(key) or "").strip()
            if _HEX_RE.match(val):
                out[key] = val.lower()
    return out


def _css_variables(palette) -> str:
    p = normalize_palette(palette)
    bg, card, fg = p["APP_BG"], p["CARD_BG"], p["FG"]
    light = _luminance(bg) > 0.5
    return ";".join([
        "color-scheme:" + ("light" if light else "dark"),
        "--bg:" + bg,
        "--card:" + card,
        # Hairlines and field fills are derived, not themed: a theme only
        # defines surfaces and text, and hardcoded neutrals broke light themes
        # on the desktop side for exactly this reason.
        "--line:" + _mix(card, fg, 0.16),
        "--field:" + _mix(card, bg, 0.55),
        "--fg:" + fg,
        "--dim:" + p["FG_SUB"],
        "--acc:" + p["ACCENT"],
        "--onacc:" + _readable_on(p["ACCENT"]),
        "--ok:" + p["ACCENT_2"],
        "--bad:" + p["ERROR"],
        "--title:" + p["TITLE"],
    ])


_PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="__THEMECOLOR__">
<title>BitCrusher</title>
<style>
:root{__VARS__}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:480px;margin:0 auto;padding:40px 20px 72px}
h1{font-size:15px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;
color:var(--title);margin:0 0 28px}
.sec{margin-bottom:22px}
label{display:block;font-size:12px;color:var(--dim);margin-bottom:8px;letter-spacing:.04em}
input[type=password]{width:100%;padding:13px 14px;border-radius:10px;
border:1px solid var(--line);background:var(--field);color:var(--fg);font-size:16px}
input[type=password]:focus{outline:0;border-color:var(--acc)}
button{width:100%;padding:13px;border:0;border-radius:10px;background:var(--acc);
color:var(--onacc);font-size:15px;font-weight:600;cursor:pointer}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--dim);
font-weight:500;font-size:14px;padding:11px}
button.ghost:hover{color:var(--fg);border-color:var(--acc)}
button:disabled{opacity:.5;cursor:default}
.msg{font-size:13px;margin-top:10px;min-height:18px;color:var(--dim)}
.msg.bad{color:var(--bad)} .msg.ok{color:var(--ok)}
.drop{border:1px dashed var(--line);border-radius:14px;padding:44px 16px;text-align:center;
color:var(--dim);font-size:14px;cursor:pointer;background:var(--card);transition:border-color .15s}
.drop:hover{border-color:var(--acc)}
.drop.over{border-color:var(--acc);border-style:solid;color:var(--fg)}
.job{border-top:1px solid var(--line);padding:14px 0;font-size:14px}
.job:first-child{border-top:0;padding-top:0}
.job .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{height:3px;background:var(--line);border-radius:2px;overflow:hidden;margin:8px 0 6px}
.bar i{display:block;height:100%;background:var(--acc);width:0%;transition:width .3s}
.st{font-size:12px;color:var(--dim)}
.st.done{color:var(--ok)} .st.failed{color:var(--bad)}
a.dl{color:var(--acc);font-size:12px;text-decoration:none;margin-left:8px}
.row{display:flex;gap:10px}
.qr{background:#ffffff;border-radius:10px;padding:12px;margin:14px 0;text-align:center}
.qr img{width:100%;max-width:220px;display:block;margin:0 auto}
code{background:var(--field);padding:3px 7px;border-radius:5px;font-size:12px;
word-break:break-all;color:var(--dim)}
.hide{display:none}
.note{font-size:12px;color:var(--dim);margin-top:10px}
</style></head><body><div class="wrap">
<h1>BitCrusher</h1>

<div id="login" class="sec hide">
  <label for="pw">Password</label>
  <input id="pw" type="password" autocomplete="current-password" enterkeyhint="go">
  <div style="height:12px"></div>
  <button id="go">Unlock</button>
  <div id="lmsg" class="msg"></div>
</div>

<div id="main" class="hide">
  <div class="sec">
    <div id="drop" class="drop">Tap to choose a file, or drop one here</div>
    <div id="umsg" class="msg"></div>
    <input id="file" type="file" class="hide">
  </div>
  <div id="jobcard" class="sec hide"><div id="jobs"></div></div>
  <div class="sec">
    <div class="row">
      <button id="pair" class="ghost">Pair a device</button>
      <button id="logout" class="ghost">Sign out</button>
    </div>
    <div id="pairbox" class="hide">
      <div id="qrbox" class="qr hide"><img id="qrimg" alt="Pairing QR code"></div>
      <code id="pairurl"></code>
      <div class="note">Single use, expires in 60 seconds.</div>
    </div>
  </div>
</div>
</div><script>
(function(){
var $=function(id){return document.getElementById(id)};
var jobs={},timer=null;
function show(el,on){el.classList[on?'remove':'add']('hide')}
function esc(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML}

function probe(){
  // Doubles as the auth check and as job-list recovery: the list lives in this
  // page's memory, so a reload (or the phone backgrounding the tab) would
  // otherwise strand finished files with no way to reach them again.
  fetch('/jobs').then(function(r){
    if(r.status===401){show($('login'),true);show($('main'),false);$('pw').focus();return null}
    show($('login'),false);show($('main'),true);return r.json()
  }).then(function(j){
    if(!j||!j.ok)return;
    j.jobs.forEach(function(o){jobs[o.job_id]=o});
    render();poll()
  }).catch(function(){show($('login'),true)});
}
function unlock(){
  var b=$('go');b.disabled=true;$('lmsg').className='msg';$('lmsg').textContent='';
  fetch('/auth',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({password:$('pw').value})})
  .then(function(r){return r.json().then(function(j){return{s:r.status,j:j}})})
  .then(function(o){
    b.disabled=false;
    if(o.s===200){$('pw').value='';show($('login'),false);show($('main'),true);probe();return}
    $('lmsg').className='msg bad';
    $('lmsg').textContent=o.s===429?'Too many attempts. Wait a few minutes.':'Wrong password.';
  }).catch(function(){b.disabled=false;$('lmsg').className='msg bad';$('lmsg').textContent='Connection failed.'});
}
function upload(f){
  if(!f)return;
  var fd=new FormData();fd.append('file',f,f.name);
  $('umsg').className='msg';$('umsg').textContent='Uploading '+f.name+'...';
  var x=new XMLHttpRequest();x.open('POST','/upload');
  x.upload.onprogress=function(e){if(e.lengthComputable)
    $('umsg').textContent='Uploading '+f.name+' - '+Math.round(e.loaded/e.total*100)+'%'};
  x.onload=function(){
    var j={};try{j=JSON.parse(x.responseText)}catch(e){}
    if(x.status===200&&j.job_id){
      $('umsg').className='msg ok';$('umsg').textContent='Queued: '+j.name;
      jobs[j.job_id]={name:j.name};render();poll()
    }else{
      $('umsg').className='msg bad';
      $('umsg').textContent=(j.error||'Upload failed')+(x.status===401?' - sign in again':'');
      if(x.status===401)probe()
    }
  };
  x.onerror=function(){$('umsg').className='msg bad';$('umsg').textContent='Upload failed.'};
  x.send(fd)
}
function render(){
  var ids=Object.keys(jobs);show($('jobcard'),ids.length>0);
  $('jobs').innerHTML=ids.map(function(id){
    var j=jobs[id],st=j.state||'queued',pct=j.pct||0;
    var dl=(j.can_download?'<a class="dl" href="/download/'+encodeURIComponent(id)+'">Download</a>':'');
    return '<div class="job"><div class="n">'+esc(j.name)+'</div>'+
      '<div class="bar"><i style="width:'+pct+'%"></i></div>'+
      '<div class="st '+esc(st)+'">'+esc(j.error||st)+' '+dl+'</div></div>'
  }).join('')
}
function poll(){
  if(timer)return;
  timer=setInterval(function(){
    var ids=Object.keys(jobs).filter(function(i){
      return jobs[i].state!=='done'&&jobs[i].state!=='failed'});
    if(!ids.length){clearInterval(timer);timer=null;return}
    ids.forEach(function(id){
      fetch('/status/'+encodeURIComponent(id)).then(function(r){return r.json()})
      .then(function(j){if(j&&j.ok){jobs[id]=Object.assign(jobs[id],j);render()}})
      .catch(function(){})
    })
  },2000)
}
$('go').onclick=unlock;
$('pw').addEventListener('keydown',function(e){if(e.key==='Enter')unlock()});
// Coming back to a backgrounded tab re-syncs rather than showing stale state.
document.addEventListener('visibilitychange',function(){
  if(!document.hidden)probe()});
$('drop').onclick=function(){$('file').click()};
$('file').onchange=function(){upload(this.files[0]);this.value=''};
['dragenter','dragover'].forEach(function(t){$('drop').addEventListener(t,function(e){
  e.preventDefault();$('drop').classList.add('over')})});
['dragleave','drop'].forEach(function(t){$('drop').addEventListener(t,function(e){
  e.preventDefault();$('drop').classList.remove('over')})});
$('drop').addEventListener('drop',function(e){
  if(e.dataTransfer&&e.dataTransfer.files.length)upload(e.dataTransfer.files[0])});
$('pair').onclick=function(){
  fetch('/pair/new',{method:'POST'}).then(function(r){return r.json()}).then(function(j){
    if(!j.ok)return;
    show($('pairbox'),true);$('pairurl').textContent=j.url;
    if(j.qr){$('qrimg').src=j.qr;show($('qrbox'),true)}
  }).catch(function(){})
};
$('logout').onclick=function(){
  fetch('/logout',{method:'POST'}).then(function(){jobs={};render();probe()})
};
probe();
})();
</script></body></html>
"""


def render_page(palette=None) -> str:
    """The upload page painted in `palette` (a desktop THEMES entry or None)."""
    p = normalize_palette(palette)
    return (_PAGE_TEMPLATE
            .replace("__VARS__", _css_variables(p))
            .replace("__THEMECOLOR__", p["APP_BG"]))


def palette_signature(palette) -> str:
    """Cheap cache key - two palettes with the same colours render the same."""
    p = normalize_palette(palette)
    return "|".join(p[k] for k in sorted(p))


# Kept as a module constant: the default-themed page, and what the offline test
# scans for external references.
PAGE_HTML = render_page()

