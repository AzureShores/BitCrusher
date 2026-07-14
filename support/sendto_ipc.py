from __future__ import annotations

import os
import subprocess
import sys

from encode.ffmpeg_exec import si, NO_WIN, _sp_run

# =====================================================================
# Single-instance IPC + Explorer "Send to BitCrusher" integration
# =====================================================================

_BC_IPC_HOST = "127.0.0.1"
_BC_IPC_PORT = 49222          # dedicated GUI hand-off port (agent HTTP uses 8765)
_BC_STARTUP_FILES: list[str] = []   # set by --enqueue when no instance was running


def _bc_ipc_send(paths, timeout: float = 1.0) -> bool:
    """
    Hand a list of file paths to an already-running BitCrusher GUI over the local
    loopback IPC port. Returns True if a running instance accepted them, False if
    no instance is listening (caller should then launch the GUI itself).
    """
    import socket
    try:
        real = [os.path.abspath(p) for p in (paths or [])
                if isinstance(p, str) and p and os.path.exists(p)]
    except Exception:
        real = []
    if not real:
        return False
    try:
        with socket.create_connection((_BC_IPC_HOST, _BC_IPC_PORT), timeout=timeout) as sk:
            body = ("BCENQUEUE\n" + "\n".join(real) + "\n").encode("utf-8")
            sk.sendall(body)
            return True
    except Exception:
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
    workdir = os.path.dirname(os.path.abspath(__file__))
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
