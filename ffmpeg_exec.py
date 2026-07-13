from __future__ import annotations

import logging
import platform
import subprocess
import time

LOG = logging.getLogger("BitCrusher")

if platform.system() == "Windows":

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    NO_WIN = subprocess.CREATE_NO_WINDOW
else:
    si = None
    NO_WIN = 0


def _render_cmd(cmd):
    try:
        return " ".join(str(c) for c in cmd)
    except Exception:
        return repr(cmd)


def _tail(txt, n=80):
    try:
        lines = (txt or "").splitlines()
        if len(lines) <= n: return txt or ""
        return "\n".join(lines[-n:])
    except Exception:
        return txt or ""


_orig_check_output = subprocess.check_output


def _check_output_logged(cmd, *args, **kwargs):
    t0 = time.time()
    try:
        out = _orig_check_output(cmd, *args, **kwargs)
        LOG.debug("CHECK_OUTPUT OK (%.2fs): %s", time.time() - t0, _render_cmd(cmd))
        return out
    except subprocess.CalledProcessError as e:
        so = getattr(e, 'stdout', b'')
        se = getattr(e, 'stderr', b'')
        try:
            so = so if isinstance(so, str) else so.decode('utf-8', 'ignore')
        except Exception:
            so = ""
        try:
            se = se if isinstance(se, str) else se.decode('utf-8', 'ignore')
        except Exception:
            se = ""
        LOG.error("CHECK_OUTPUT FAILED (rc=%s) for: %s\nSTDOUT tail:\n%s\nSTDERR tail:\n%s",
                  e.returncode, _render_cmd(cmd), _tail(so, 60), _tail(se, 120))
        raise
    except Exception as e:
        LOG.error("CHECK_OUTPUT EXCEPTION for: %s\n%s", _render_cmd(cmd), repr(e))
        raise


def _sp_check_output(cmd, *args, **kwargs):
    return _check_output_logged(cmd, *args, **kwargs)


FFMPEG = None


def set_ffmpeg_path(path: str | None) -> None:
    """Sync the ffmpeg binary path resolved by BitCrusherV9.py's load_paths()
    so _ffmpeg_has_filter (called with the original single-arg signature
    tests and callers already depend on) sees the real, live path."""
    global FFMPEG
    FFMPEG = path


def _ffmpeg_has_filter(name: str) -> bool:
    """Cached check for whether the local ffmpeg build exposes a given filter."""
    cache = getattr(_ffmpeg_has_filter, "_cache", None)
    if cache is None:
        cache = {}
        try:
            out = _sp_check_output([FFMPEG, "-hide_banner", "-filters"],
                                   text=True, startupinfo=si, creationflags=NO_WIN)
            for line in (out or "").splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    cache[parts[1]] = True
        except Exception:
            pass
        setattr(_ffmpeg_has_filter, "_cache", cache)
    return bool(cache.get(name))
