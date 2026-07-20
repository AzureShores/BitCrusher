from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time

from encode.ffmpeg_exec import si, NO_WIN, _sp_run

# =====================================================================
# Single-instance IPC + Explorer "Send to BitCrusher" integration
#
# Protocol v2 (BCENQUEUE2): the running GUI binds the first free port in
# _BC_IPC_PORTS and writes user_settings/ipc_endpoint.json with the port
# and a per-session random token. A client reads that file, connects,
# sends the token + paths, and REQUIRES an "OK" ack - so a foreign app
# that happens to own the port can never swallow a hand-off silently.
# =====================================================================

_BC_IPC_HOST = "127.0.0.1"
_BC_IPC_PORT = 49222                       # first-choice port (legacy constant)
_BC_IPC_PORTS = tuple(range(49222, 49233))  # bind ladder: 49222..49232
_BC_STARTUP_FILES: list[str] = []   # set by --enqueue when no instance was running

_ENDPOINT_NAME = "ipc_endpoint.json"


def _endpoint_path(settings_dir: str) -> str:
    return os.path.join(settings_dir, _ENDPOINT_NAME)


def read_endpoint(settings_dir: str) -> dict | None:
    """Read the running instance's endpoint file; None if absent/corrupt."""
    try:
        with open(_endpoint_path(settings_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and int(data.get("port", 0)) > 0:
            return data
    except Exception:
        pass
    return None


class IpcServer:
    """Loopback hand-off listener owned by the running GUI instance.

    Framework-free: `on_paths(paths, target_mb)` is called from the accept
    thread - the GUI shim marshals to the Tk main loop itself.
    """

    def __init__(self, on_paths, settings_dir: str,
                 host: str = _BC_IPC_HOST, ports=None):
        self.on_paths = on_paths
        self.settings_dir = settings_dir
        self.host = host
        self.ports = list(ports if ports is not None else _BC_IPC_PORTS)
        self.port: int | None = None
        self.token: str | None = None
        self._srv: socket.socket | None = None
        self._stop = False

    def start(self) -> bool:
        """Bind the first free port and start serving. False if all busy."""
        for port in self.ports:
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # Deliberately NOT SO_REUSEADDR: on Windows that would let a
                # second instance also bind, defeating single-ownership.
                srv.bind((self.host, port))
                srv.listen(8)
            except OSError:
                continue
            self._srv = srv
            self.port = port
            self.token = secrets.token_hex(16)
            self._write_endpoint()
            threading.Thread(target=self._serve, name="bc_ipc", daemon=True).start()
            return True
        return False

    def _write_endpoint(self):
        try:
            os.makedirs(self.settings_dir, exist_ok=True)
            path = _endpoint_path(self.settings_dir)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"port": self.port, "token": self.token,
                           "pid": os.getpid()}, f)
            os.replace(tmp, path)
        except Exception:
            pass

    def stop(self):
        self._stop = True
        try:
            if self._srv:
                self._srv.close()
        except Exception:
            pass
        # Only remove the endpoint file if it is still ours (a newer
        # instance may have overwritten it).
        try:
            ep = read_endpoint(self.settings_dir)
            if ep and int(ep.get("pid", -1)) == os.getpid():
                os.remove(_endpoint_path(self.settings_dir))
        except Exception:
            pass

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except Exception:
                break
            try:
                conn.settimeout(3.0)
                data = self._read_request(conn)
                ok = self._handle(data)
                if ok:
                    try:
                        conn.sendall(b"OK\n")
                    except Exception:
                        pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    @staticmethod
    def _read_request(conn) -> str:
        """Read until the blank-line terminator (or EOF/timeout)."""
        chunks = []
        try:
            while True:
                b = conn.recv(4096)
                if not b:
                    break
                chunks.append(b)
                if b"\n\n" in b"".join(chunks[-2:]):
                    break
        except Exception:
            pass
        return b"".join(chunks).decode("utf-8", "replace")

    def _handle(self, data: str) -> bool:
        lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
        if len(lines) < 2 or lines[0] != "BCENQUEUE2":
            return False
        if lines[1] != self.token:
            return False  # bad token: no ack, client falls back safely
        target_mb = None
        rest = lines[2:]
        if rest and rest[0].startswith("TARGET "):
            try:
                target_mb = float(rest[0].split(None, 1)[1])
            except Exception:
                target_mb = None
            rest = rest[1:]
        paths = [p for p in rest if p]
        if not paths:
            return False
        try:
            self.on_paths(paths, target_mb)
        except Exception:
            return False
        return True


def _bc_ipc_send(paths, settings_dir: str | None = None,
                 target_mb: float | None = None,
                 attempts: int = 3, timeout: float = 1.5) -> bool:
    """
    Hand file paths to an already-running BitCrusher GUI. True only when the
    instance ACKed the hand-off; False means the caller should launch the GUI.
    """
    try:
        real = [os.path.abspath(p) for p in (paths or [])
                if isinstance(p, str) and p and os.path.exists(p)]
    except Exception:
        real = []
    if not real:
        return False

    ep = read_endpoint(settings_dir) if settings_dir else None
    port = int(ep["port"]) if ep else _BC_IPC_PORT
    token = str(ep.get("token", "")) if ep else ""

    body = "BCENQUEUE2\n" + token + "\n"
    if target_mb:
        body += f"TARGET {target_mb}\n"
    body += "\n".join(real) + "\n\n"
    payload = body.encode("utf-8")

    for attempt in range(max(1, attempts)):
        try:
            with socket.create_connection((_BC_IPC_HOST, port), timeout=timeout) as sk:
                sk.sendall(payload)
                sk.settimeout(timeout)
                resp = sk.recv(16)
                # No/garbage ack = foreign app or token mismatch; retrying
                # cannot help, so fall back to launching our own GUI.
                return resp.strip() == b"OK"
        except ConnectionRefusedError:
            return False  # nothing listening (stale endpoint file)
        except Exception:
            time.sleep(0.2)
    return False


def _sendto_shortcut_path() -> str:
    """Path to the per-user SendTo shortcut (...\\SendTo\\BitCrusher.lnk)."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "Microsoft", "Windows", "SendTo", "BitCrusher.lnk")


def _sendto_launch_target() -> tuple[str, str]:
    """
    (target_exe, arguments) that a SendTo shortcut should invoke. Explorer appends
    the selected file path(s) after `arguments`, giving `<exe> <args> "file"`.
    Prefers pythonw.exe (no console flash) when running from source; uses the
    frozen exe directly when packaged.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, "--enqueue"
    exe = sys.executable or "python.exe"
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(pyw):
        exe = pyw
    # The launch target must be the entry script actually run (BitCrusherV9.py),
    # not this module — resolve via __main__ rather than this file's own path.
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    script = os.path.abspath(main_file or __file__)
    return exe, f'"{script}" --enqueue'


def register_send_to() -> tuple[bool, str]:
    """
    Create the 'Send to > BitCrusher' shortcut for the current user (no admin
    needed — it just drops a .lnk in the user's SendTo folder). Returns (ok, msg).
    """
    if os.name != "nt":
        return False, "Send To integration is Windows-only."
    lnk = _sendto_shortcut_path()
    target, arguments = _sendto_launch_target()
    workdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    icon = os.path.join(workdir, "icon.png")
    try:
        os.makedirs(os.path.dirname(lnk), exist_ok=True)
        # Build the shortcut via WScript.Shell (ships with Windows — no pywin32).
        ps = (
            "$w = New-Object -ComObject WScript.Shell; "
            f"$s = $w.CreateShortcut('{lnk}'); "
            f"$s.TargetPath = '{target}'; "
            f"$s.Arguments = '{arguments}'; "
            f"$s.WorkingDirectory = '{workdir}'; "
            f"$s.Description = 'Send file(s) to BitCrusher'; "
            f"$s.Save()"
        )
        proc = _sp_run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       startupinfo=si, creationflags=NO_WIN)
        if proc.returncode == 0 and os.path.isfile(lnk):
            return True, f"'Send to > BitCrusher' installed.\n{lnk}"
        return False, f"Shortcut creation failed: {(proc.stderr or '').strip()[:200]}"
    except Exception as e:
        return False, f"Send To registration error: {e}"


def unregister_send_to() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Send To integration is Windows-only."
    lnk = _sendto_shortcut_path()
    try:
        if os.path.isfile(lnk):
            os.remove(lnk)
            return True, "'Send to > BitCrusher' removed."
        return True, "'Send to > BitCrusher' was not installed."
    except Exception as e:
        return False, f"Send To removal error: {e}"
