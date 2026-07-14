from __future__ import annotations

import logging
import os
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


_orig_run = subprocess.run


def _run_logged(cmd, *args, **kwargs):
    t0 = time.time()
    text_mode = kwargs.get("text", False)
    capture = (kwargs.get("stdout") is subprocess.PIPE
               or kwargs.get("stderr") is subprocess.PIPE
               or kwargs.get("capture_output") is True
               or text_mode)

    try:

        _parts = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
        _lp = [str(x).lower() for x in _parts]

        _is_ffmpeg = any(("ffmpeg" in p) for p in _lp)
        _asks_for_frame = any((flag in _lp) for flag in ("-frames:v", "-vframes"))
        _has_vf = "-vf" in _lp
        # -------- scene-zones injection (x264) ----------
        try:
            _xparams = os.environ.get("BC_X264_PARAMS", "")
            _qpfile  = os.environ.get("BC_QPFILE", "")

            # HandBrakeCLI path (x264)
            if any(("handbrakecli" in p) for p in _lp) and any(("x264" in p) for p in _lp):
                if _xparams and "-x264-params" not in _lp and "--encoder-options" not in _lp:
                    _parts.extend(["-x264-params", _xparams])
                if _qpfile and os.path.exists(_qpfile) and "--qpfile" not in _lp:
                    _parts.extend(["--qpfile", _qpfile])
                cmd = _parts

            # ffmpeg path (libx264)
            elif _is_ffmpeg and ("libx264" in _lp or ("-c:v" in _lp and "libx264" in _lp)):
                if _xparams and "-x264-params" not in _lp:
                    _parts.extend(["-x264-params", _xparams])
                if _qpfile and os.path.exists(_qpfile) and "-qpfile" not in _lp:
                    _parts.extend(["-qpfile", _qpfile])
                cmd = _parts
        except Exception:
            pass
        # -----------------------------------------------
        _in_idx = next((i for i, v in enumerate(_lp) if v == "-i"), -1)
        _in_path = str(_parts[_in_idx + 1]) if (_in_idx != -1 and _in_idx + 1 < len(_parts)) else ""

        _audio_exts = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".opus", ".ogg"}
        _is_audio_only = os.path.splitext(_in_path)[1].lower() in _audio_exts

        if _is_ffmpeg and (_asks_for_frame or _has_vf) and _is_audio_only:

            LOG.info("Skipping video-frame extraction on audio-only input: %s", _render_cmd(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
    except Exception:
        pass

    try:
        res = _orig_run(cmd, *args, **kwargs)

    except Exception as e:
        LOG.error("subprocess.run raised exception for: %s\n%s", _render_cmd(cmd), repr(e))
        raise
    dt = time.time() - t0
    try:
        LOG.debug("CMD: %s", _render_cmd(cmd))
        LOG.debug("RET: %s in %.2fs", res.returncode, dt)
        if capture:
            if hasattr(res, "stdout") and res.stdout:
                LOG.debug("STDOUT (tail):\n%s", _tail(res.stdout if isinstance(res.stdout, str)
                                                     else res.stdout.decode("utf-8", "ignore"), 50))
            if hasattr(res, "stderr") and res.stderr:
                LOG.debug("STDERR (tail):\n%s", _tail(res.stderr if isinstance(res.stderr, str)
                                                     else res.stderr.decode("utf-8", "ignore"), 120))
        if res.returncode != 0:
            try:
                _parts = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
                _lp = [str(x).lower() for x in _parts]
                if any("ffmpeg" in p for p in _lp) and any(flag in _lp for flag in ("-frames:v", "-vframes")):
                    LOG.warning("Command nonzero (rc=%s): %s", res.returncode, _render_cmd(cmd))
                else:
                    LOG.error("Command failed (rc=%s): %s", res.returncode, _render_cmd(cmd))
            except Exception:
                LOG.error("Command failed (rc=%s): %s", res.returncode, _render_cmd(cmd))
    except Exception:
        pass
    return res


def _sp_run(cmd, *args, **kwargs):
    return _run_logged(cmd, *args, **kwargs)


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


FFPROBE = None


def set_ffprobe_path(path: str | None) -> None:
    """Sync the ffprobe binary path resolved by BitCrusherV9.py's load_paths(),
    same rationale as set_ffmpeg_path."""
    global FFPROBE
    FFPROBE = path


def probe_video_stream_dims(path: str) -> dict:
    """Minimal standalone ffprobe of a file's first video stream (width, height,
    avg_frame_rate) for quality_metrics.py's reference-dimension needs.

    Deliberately does NOT share BitCrusherV9.py's _probe_media_cached LRU cache:
    that cache is monkeypatched by name (tests bind fakes to BitCrusherV9's own
    module namespace), and _probe_video_stream calling it internally from a
    different module would silently resolve the real function instead of a
    test's fake, defeating that isolation invisibly. This runs once per VMAF/
    XPSNR measurement (not hot-path), so a redundant ffprobe call is cheap.
    """
    import json
    try:
        out = _sp_check_output(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,avg_frame_rate",
             "-of", "json", path],
            text=True, startupinfo=si, creationflags=NO_WIN)
        data = json.loads(out or "{}") or {}
        streams = data.get("streams") or []
        return streams[0] if streams else {}
    except Exception:
        return {}
