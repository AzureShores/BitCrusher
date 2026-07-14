from __future__ import annotations

import logging
import os
import platform
import subprocess
import tempfile
import threading
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


HANDBRAKE_CLI = None


def set_handbrake_path(path: str | None) -> None:
    """Sync the HandBrakeCLI binary path resolved by BitCrusherV9.py's
    load_paths(), same rationale as set_ffmpeg_path."""
    global HANDBRAKE_CLI
    HANDBRAKE_CLI = path


# Deferred (module-level but attribute-only) imports: media_probe,
# feature_helpers and encoder_caps all import ffmpeg_exec themselves, so a
# `from X import name` here would deadlock the cycle. A bare module import is
# safe because the names below are only dereferenced inside function bodies,
# long after all modules involved have finished initializing.
import media_probe
import feature_helpers
import encoder_caps


def _ffmpeg_emergency_encode(
    input_path: str,
    output_path: str,
    *,
    encoder: str | None,
    bitrate: int | None,
    crf: int | None,
    width: int | None,
    fps: float | None,
    audio_bitrate: int | None,
    audio_copy: bool,
    tune: str | None = None,
) -> bool:
    """
    Last-resort direct ffmpeg path when HandBrake two-pass and CRF fallback both fail.
    Keeps arguments minimal and widely supported.
    """
    try:
        vmap = {
            "x264": "libx264", "h264": "libx264", "libx264": "libx264",
            "x265": "libx265", "hevc": "libx265", "libx265": "libx265",
            "h264_nvenc": "h264_nvenc", "hevc_nvenc": "hevc_nvenc", "av1_nvenc": "av1_nvenc",
            "h264_qsv": "h264_qsv", "hevc_qsv": "hevc_qsv", "av1_qsv": "av1_qsv",
            "av1": "libaom-av1", "svt_av1": "libsvtav1", "libaom-av1": "libaom-av1", "libsvtav1": "libsvtav1",
        }
        vcodec = vmap.get((encoder or "").lower(), "libx264")
        # sanitize tune for libx265 (avoid forcing grain on generic film/animation content)
        if tune:
            _tn = str(tune).lower()
            if vcodec == "libx265" and _tn in ("film", "animation", "stillimage"):
                tune = None
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", input_path]

        if fps:
            cmd += ["-r", f"{fps}"]

        vf = []
        if width:
            try:
                vf.append(f"scale={int(width)}:-2")
            except Exception:
                pass
        if vf:
            cmd += ["-vf", ",".join(vf)]

        cmd += ["-c:v", vcodec]
        if bitrate and int(bitrate) > 0:
            b = int(bitrate)
            cmd += ["-b:v", str(b), "-maxrate", str(int(b * 1.15)), "-bufsize", str(int(b * 2))]
        elif crf is not None:
            cmd += ["-crf", str(int(crf))]
            if vcodec in ("libx264", "libx265"):
                cmd += ["-preset", "medium"]

        if audio_copy:
            cmd += ["-c:a", "copy"]
        else:
            if audio_bitrate and int(audio_bitrate) > 0:
                cmd += ["-c:a", "aac", "-b:a", str(int(audio_bitrate))]
            else:
                cmd += ["-an"]

        cmd += ["-movflags", "+faststart", output_path]

        res = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return int(getattr(res, "returncode", 1) or 0) == 0
    except Exception:
        return False


def _detect_reencoding_risk(source_codec: str, target_encoder: str,
                             source_bps: int, target_bps: int) -> str | None:
    """
    Returns a warning message if the user is re-encoding an already-compressed source
    with the same codec at a significantly lower bitrate (generation loss risk).
    Returns None if no concern.
    """
    _codec_family: dict[str, str] = {
        "h264": "h264", "avc": "h264", "libx264": "h264",
        "hevc": "h265", "h265": "h265", "libx265": "h265",
        "av1": "av1", "libaom-av1": "av1", "libsvtav1": "av1",
        "vp9": "vp9", "libvpx-vp9": "vp9",
    }
    src_fam = _codec_family.get((source_codec or "").lower().replace("-", ""), "")
    tgt_raw = (target_encoder or "").lower()
    tgt_fam = ""
    for k, v in _codec_family.items():
        if k in tgt_raw:
            tgt_fam = v
            break
    if not src_fam or not tgt_fam or src_fam != tgt_fam:
        return None  # different codec families — transcoding is expected, no special warning
    if source_bps <= 0 or target_bps <= 0:
        return None
    if target_bps < source_bps * 0.75:
        drop_pct = int((1.0 - target_bps / source_bps) * 100)
        src_kbps = source_bps // 1000
        return (
            f"Source is already {source_codec.upper()} at {src_kbps} kbps. "
            f"Re-encoding to the same codec at -{drop_pct}% bitrate will cause "
            f"generation loss (compounding block/ring artifacts). "
            f"Consider using a different target codec (e.g. AV1 or x265) for better quality."
        )
    return None


def _ffmpeg_run_with_progress(cmd: list, duration_s: float, pass_label: str, cwd: str | None = None,
                              progress_cb=None, job_id: str | None = None,
                              stage: str | None = None) -> int:
    """
    Run an ffmpeg command with real-time progress reporting via '-progress pipe:1'.
    Emits '[Pass N] XX% | NNN fps | ETA ~Xs' to the BitCrusher logger every ~1 second,
    and forwards {stage, pct, fps, eta_s} to progress_cb(job_id, event) when given.
    Returns the process returncode.
    """
    _log = logging.getLogger("BitCrusher")
    prog_cmd = list(cmd)
    # Inject -progress pipe:1 just before the -i flag so ffmpeg streams progress to stdout.
    try:
        i_idx = prog_cmd.index("-i")
        prog_cmd = prog_cmd[:i_idx] + ["-progress", "pipe:1", "-stats_period", "1"] + prog_cmd[i_idx:]
    except ValueError:
        pass  # no -i found; run as-is

    _log.info("FFmpeg two-pass %s: %s", pass_label, " ".join(prog_cmd))
    try:
        proc = subprocess.Popen(
            prog_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            text=True,
            bufsize=1,
        )
        # IMPORTANT: x265 (and other encoders) write progress/info straight to
        # stderr even at -loglevel error. If we don't drain stderr it fills the
        # OS pipe buffer (~64 KB) and the encoder blocks mid-pass, silently
        # failing the encode. Drain it on a background thread, keeping the tail
        # for error diagnosis.
        from collections import deque
        _err_tail: deque = deque(maxlen=40)
        def _drain_stderr(pipe):
            try:
                for ln in iter(pipe.readline, ""):
                    if ln:
                        _err_tail.append(ln.rstrip("\n"))
            except Exception:
                pass
        _err_thread = threading.Thread(target=_drain_stderr, args=(proc.stderr,), daemon=True)
        _err_thread.start()

        kv: dict[str, str] = {}
        start_wall = time.time()
        last_emit = 0.0
        for line in (proc.stdout or []):
            line = line.strip()
            if "=" in line:
                k, _, v = line.partition("=")
                kv[k.strip()] = v.strip()
            if line.startswith("progress="):
                now = time.monotonic()
                if now - last_emit >= 1.0:
                    last_emit = now
                    try:
                        out_ms = int(kv.get("out_time_ms", 0) or 0)
                        fps_val = float(kv.get("fps", "0") or 0)
                        if duration_s > 0 and out_ms > 0:
                            pct = min(100.0, out_ms / (duration_s * 1_000_000) * 100.0)
                            elapsed = max(0.001, time.time() - start_wall)
                            done_s = out_ms / 1_000_000
                            speed = done_s / elapsed  # video-seconds per wall-second
                            remaining = max(0.0, duration_s - done_s)
                            eta_s = int(remaining / speed) if speed > 0 else 0
                            fps_str = f" | {fps_val:.0f} fps" if fps_val > 0 else ""
                            eta_str = f" | ETA ~{eta_s}s" if eta_s > 0 else ""
                            _log.info("[%s] %.0f%%%s%s", pass_label, pct, fps_str, eta_str)
                            if callable(progress_cb):
                                try:
                                    progress_cb(job_id, {
                                        "stage": (stage or "encoding"),
                                        "pct": float(pct),
                                        "fps": float(fps_val),
                                        "eta_s": int(eta_s),
                                        "detail": pass_label,
                                    })
                                except Exception:
                                    pass
                    except Exception:
                        pass
        proc.wait()
        try:
            _err_thread.join(timeout=2.0)
        except Exception:
            pass
        if proc.returncode != 0 and _err_tail:
            _log.error("FFmpeg %s failed (rc=%s): %s", pass_label, proc.returncode,
                       " | ".join(list(_err_tail)[-6:]))
        return proc.returncode
    except Exception as exc:
        _log.debug("Progress-aware run failed (%r); falling back", exc)
        # Fallback: plain run (stderr to DEVNULL so it can't deadlock).
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=cwd)
        return r.returncode


def _ffmpeg_two_pass_encode(
    input_path: str,
    output_path: str,
    *,
    encoder: str | None,
    bitrate: int,
    width: int | None,
    fps: float | None,
    tune: str | None,
    audio_bitrate: int | None,
    audio_copy: bool,
    preset: str | None = "medium",
    turbo: bool = False,
    duration_s: float = 0.0,
    progress_cb=None,
    job_id: str | None = None,
    advanced_options: dict | None = None,
) -> bool:
    """
    Robust native two-pass using ffmpeg for libx264/libx265/libvpx-vp9.
    Pass 1 to null, pass 2 to file with real-time progress display.
    turbo=True speeds up pass 1 only where it's stats-compatible (VP9 cpu-used);
    x264/x265 keep a consistent preset across passes (a preset mismatch breaks
    their two-pass stats).
    """
    # Both passes run with cwd set to a temp dir (so the relative pass-log file
    # lands there). Any relative input/output path would break from that cwd, so
    # resolve them to absolute up front.
    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)
    vmap = {
        "x264": "libx264", "h264": "libx264", "libx264": "libx264",
        "x265": "libx265", "hevc": "libx265", "libx265": "libx265",
        "vp9": "libvpx-vp9", "libvpx-vp9": "libvpx-vp9",
        "svt-av1": "libsvtav1", "svtav1": "libsvtav1", "libsvtav1": "libsvtav1",
        # generic 'av1' only reaches here when _supports_true_two_pass verified
        # the concrete AV1 encoder is SVT-AV1 (libaom 2-pass is far too slow).
        "av1": "libsvtav1",
    }
    vcodec = vmap.get((encoder or "").lower(), "libx264")
    # sanitize tune per codec (avoid x265 invalid 'film')
    safe_tune = None
    if tune:
        _tn = str(tune).lower()
        if vcodec == "libx265":
            # x265 does not support x264-style tune semantics.
            if _tn in ("film", "animation", "stillimage"):
                _tn = ""
            if _tn in ("grain","psnr","ssim","fastdecode"):
                safe_tune = _tn
        else:
            safe_tune = _tn
    # Detect HDR source and select pixel format accordingly (cached probe —
    # this used to spawn a fresh ffprobe on EVERY retry pass).
    try:
        _vst = media_probe._probe_video_stream(input_path)
    except Exception:
        _vst = {}
    source_is_hdr = media_probe._is_hdr_source(_vst)
    hdr_pf = media_probe._hdr_pixel_fmt(vcodec) if source_is_hdr else None
    if source_is_hdr and hdr_pf:
        logging.getLogger("BitCrusher").info(
            "[HDR] HDR source detected — encoding to 10-bit (%s) to preserve quality", hdr_pf)
    elif source_is_hdr:
        logging.getLogger("BitCrusher").warning(
            "[HDR] HDR source detected but %s only supports 8-bit — color precision will be reduced", vcodec)

    # temp dir + pass-log path are chosen further below, after the encode
    # signature (codec/res/preset/tune/filters/params) is known, so an optional
    # shared pass-log can key on it and reuse pass 1 across bitrate retries.

    # Video rc params (shared)
    rc = ["-b:v", str(int(bitrate)), "-maxrate", str(int(bitrate * 1.10)), "-bufsize", str(int(bitrate * 2))]
    # Build the filter chain: HDR->SDR tone-map (when target is 8-bit) then scale.
    _vf_chain: list[str] = []
    if source_is_hdr and not hdr_pf:
        _tm = media_probe._hdr_tonemap_vf(vcodec)
        if _tm:
            _vf_chain.append(_tm)
            logging.getLogger("BitCrusher").info(
                "[HDR] HDR source + 8-bit %s -> tone-mapping BT.2020->BT.709 (two-pass).", vcodec)
    if width:
        _vf_chain.append(f"scale={int(width)}:-2")
    # Validated artifact-aware prefilters (deband/deblock/denoise) — applied at
    # delivery resolution, identical in both passes (pass-log stats must match).
    _pre_vf = str((advanced_options or {}).get("preproc_vf") or "").strip()
    if _pre_vf:
        _vf_chain.append(_pre_vf)
    vscale = (["-vf", ",".join(_vf_chain)] if _vf_chain else [])
    vfps   = (["-r", str(int(round(float(fps))))] if fps else [])
    # Pixel format: prefer 10-bit for HDR sources when codec supports it; else standard 8-bit.
    vpix   = (["-pix_fmt", hdr_pf] if hdr_pf else ["-pix_fmt", "yuv420p"])

    # Multi-audio-track mapping (keep-first / mix). amix can't stream-copy, so a
    # "mix" plan forces an audio re-encode. Pass 1 stays -an; only pass 2 carries
    # the audio map/filter.
    _atplan = (advanced_options or {}).get("_audio_track_plan") or {}
    _acopy_ref = {"audio_copy": bool(audio_copy)}
    _amap = feature_helpers._audio_map_ffmpeg_args(_atplan, _acopy_ref) if _atplan.get("multi") else []
    _audio_copy_eff = _acopy_ref["audio_copy"]

    # Audio settings for the 2nd pass
    if _audio_copy_eff:
        a2 = ["-c:a", "copy"]
    elif audio_bitrate and int(audio_bitrate) > 0:
        a2 = ["-c:a", "aac", "-b:a", str(int(audio_bitrate))]
    elif _atplan.get("multi") and _atplan.get("mode") == "mix":
        # mix forced re-encode but no explicit bitrate was passed — use a safe default.
        a2 = ["-c:a", "aac", "-b:a", "192k"]
    else:
        a2 = ["-an"]

    preset = str(preset or "medium")
    # Turbo used to run pass 1 at a faster PRESET, but x264/x265 two-pass stats
    # are preset-specific — a fast→medium mismatch makes pass 2 fail to open the
    # encoder ("Invalid argument"). x264 already runs a fast first pass
    # internally (unless --slow-firstpass), so both passes must share a preset.
    # VP9's per-pass -cpu-used speedup (below) IS pass-safe and still applies.
    preset_p1 = preset
    _adv = advanced_options or {}

    # Base (common) flags (optional GPU decode of the source; encode unchanged)
    _hw = encoder_caps._hw_decode_args(_adv)
    base = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"] + _hw + ["-i", input_path,
            "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn"]

    # === Pass-1 stats reuse (shared pass-log) ==============================
    # Pass 1 (whole-file complexity analysis) is identical for every encode of
    # this source that shares codec + resolution + fps + preset + tune + filter
    # chain + codec params; only the target bitrate differs, and pass-1 stats are
    # bitrate-independent. When the caller supplies a shared pass-log dir (the
    # size-convergence loop does), key the log on those invariants and reuse the
    # stats across calls so only pass 2 re-runs — roughly halving the loop's
    # encode time. A resolution/filter change yields a new key = a fresh pass 1.
    import hashlib as _hashlib
    _sig_src = "|".join([
        vcodec, str(int(width or 0)), str(int(round(float(fps or 0.0)))),
        str(preset), str(safe_tune or ""), (vpix[-1] if vpix else ""),
        ",".join(_vf_chain),
        str(_adv.get("x265_params") or ""), str(_adv.get("x264_params") or ""),
        str(os.environ.get("BC_X265_PARAMS", "")), str(os.environ.get("BC_X264_PARAMS", "")),
    ])
    _sig8 = _hashlib.sha1(_sig_src.encode("utf-8", "ignore")).hexdigest()[:12]
    null_dev = "NUL" if os.name == "nt" else "/dev/null"
    _pl_dir = str((_adv.get("_twopass_passlog_dir") or "")).strip()
    if _pl_dir and os.path.isdir(_pl_dir):
        temp_dir = _pl_dir
        _owns_temp = False
        passlog = os.path.join(temp_dir, f"bc2p_{_sig8}")
        passlog_name = os.path.basename(passlog)
    else:
        temp_dir = tempfile.mkdtemp(prefix="bc_ff2p_")
        _owns_temp = True
        passlog = os.path.join(tempfile.gettempdir(), f"bc2p_{next(tempfile._get_candidate_names())}")
        passlog_name = "passlog"
    _p1_sentinel = passlog + ".p1done"
    # x265 writes the stats file to its exact `stats=` name; the generic
    # -pass/-passlogfile encoders (x264/vp9/svt-av1) write "<passlogfile>-0.log".
    _stats_probe = passlog if vcodec == "libx265" else (passlog + "-0.log")
    _reuse_p1 = bool(
        (not _owns_temp) and _adv.get("_twopass_reuse_stats")
        and os.path.exists(_p1_sentinel)
        and os.path.exists(_stats_probe) and os.path.getsize(_stats_probe) > 0)

    # Codec-specific two-pass
    try:
        if vcodec == "libsvtav1":
            # SVT-AV1 1-pass VBR is wildly loose on hard-cut content: on
            # asdfmovie (a cut every ~2s) -b:v 605k produced 2.0x the target,
            # and dropping the rate barely shrank the file — 1-pass hits a floor
            # it can't plan past. 2-pass VBR measures the whole-file complexity
            # in pass 1 and actually holds the budget in pass 2. SVT writes its
            # stats to <passlogfile>-0.log via the generic ffmpeg -pass flags.
            _svt_p = media_probe._SVT_PRESET_MAP.get(str(preset).lower(), "6")
            _svt_max = str((_adv or {}).get("quality_mode") or "").lower() == "max"
            if _svt_max:
                _svt_p = str(min(int(_svt_p), 4))
            # Long clips must not run a glacial preset carried over from x265's
            # "slow"/"slower" (SVT preset 3 = ~9fps on a 33-min clip = hours).
            _svt_p = str(media_probe._svt_preset_for_duration(_svt_p, duration_s))
            # NOTE: enable-overlays is REJECTED by SVT in multi-pass mode
            # ("overlay frames feature is currently not supported with
            # multi-pass encoding"), so the single-pass params can't be reused
            # verbatim here. scd + film-grain ARE 2-pass-safe (verified).
            _svt_params = "tune=0:scd=1"
            # Film-grain synthesis (denoise + re-synthesize) from the measured
            # grain probe — see advanced_options["_film_grain"]; 2-pass-safe.
            _fg_lvl2 = int(((_adv or {}).get("_film_grain") or {}).get("level") or 0)
            if _fg_lvl2 > 0:
                _svt_params += f":film-grain={_fg_lvl2}:film-grain-denoise=1"
            _svt_v = ["-c:v", "libsvtav1", "-preset", _svt_p, "-g", "300",
                      "-svtav1-params", _svt_params, "-pix_fmt", "yuv420p10le"]
            # SVT rejects -maxrate/-bufsize with -b:v (VBR); target bitrate only.
            rc_svt = ["-b:v", str(int(bitrate))]
            cmd1 = base + _svt_v + vscale + vfps + rc_svt \
                + ["-pass", "1", "-passlogfile", passlog, "-an", "-f", "null", null_dev]
            cmd2 = base + _svt_v + vscale + vfps + rc_svt \
                + ["-pass", "2", "-passlogfile", passlog] + _amap + a2 + ["-movflags", "+faststart", output_path]
        elif vcodec == "libx265":
            # libx265 uses x265-params pass=N:stats=...
            # Measured (10MB head-to-head): x265 defaults beat psy-rd/aq overrides
            # by ~2 VMAF. Keep only neutral analysis-depth bumps; let the encoder
            # use its (already VMAF-optimal) defaults for psy-rd/aq/deblock.
            _x265_base = (str(_adv.get("x265_params") or "").strip()
                          or os.environ.get("BC_X265_PARAMS", "").strip()
                          or "rc-lookahead=80:bframes=8:ref=5")
            x265p1 = f"pass=1:stats={passlog_name}:{_x265_base}"
            x265p2 = f"pass=2:stats={passlog_name}:{_x265_base}"

            cmd1 = base + ["-c:v", vcodec, "-preset", preset_p1] + (["-tune", safe_tune] if safe_tune else []) \
                  + vscale + vfps + vpix + rc + ["-x265-params", x265p1, "-an", "-f", "null", null_dev]
            cmd2 = base + ["-c:v", vcodec, "-preset", preset] + (["-tune", safe_tune] if safe_tune else []) \
                  + vscale + vfps + vpix + rc + ["-x265-params", x265p2] + _amap + a2 + ["-movflags", "+faststart", output_path]
        elif vcodec == "libvpx-vp9":
            # VP9 does not accept x264/x265-style tune/preset knobs.
            # VP9 with 10-bit requires profile:v 2.
            vp9_prof = (["-profile:v", "2"] if hdr_pf else [])
            vp9_common = ["-c:v", vcodec, "-deadline", "good", "-cpu-used", ("1" if preset in ("slow", "veryslow") else "2"),
                          "-row-mt", "1", "-tile-columns", "1", "-frame-parallel", "0",
                          "-lag-in-frames", "25", "-auto-alt-ref", "1"] + vp9_prof
            vp9_p1 = (["-cpu-used", "4"] if turbo else [])  # last flag wins in ffmpeg
            cmd1 = base + vp9_common + vp9_p1 + vscale + vfps + vpix + rc + ["-pass", "1", "-passlogfile", passlog, "-an", "-f", "null", null_dev]
            cmd2 = base + vp9_common + vscale + vfps + vpix + rc + ["-pass", "2", "-passlogfile", passlog] + _amap + a2 + ["-movflags", "+faststart", output_path]
        else:
            # libx264 honors global -pass / -passlogfile. x264 is 8-bit only; vpix falls back to yuv420p.
            _x264_params = (str(_adv.get("x264_params") or "").strip()
                            or str(os.environ.get("BC_X264_PARAMS", "")).strip())
            if not _x264_params:
                _x264_params = "aq-mode=3:aq-strength=1.00:mbtree=1:deblock=-1,-1:psy-rd=1.10,0.15:rc-lookahead=80:qcomp=0.70:ipratio=1.30:pbratio=1.20:trellis=2:bframes=8:ref=5"
            cmd1 = base + ["-c:v", vcodec, "-preset", preset_p1] + (["-tune", safe_tune] if safe_tune else []) \
                  + vscale + vfps + vpix + rc + (["-x264-params", _x264_params] if _x264_params else []) + ["-pass", "1", "-passlogfile", passlog, "-an", "-f", "null", null_dev]
            cmd2 = base + ["-c:v", vcodec, "-preset", preset] + (["-tune", safe_tune] if safe_tune else []) \
                  + vscale + vfps + vpix + rc + (["-x264-params", _x264_params] if _x264_params else []) + ["-pass", "2", "-passlogfile", passlog] + _amap + a2 + ["-movflags", "+faststart", output_path]

        # Map per-pass 0-100% onto overall 0-50 / 50-100 for the job progress row.
        def _cb_pass(lo: float, span: float, stage: str):
            if not callable(progress_cb):
                return None
            def _cb(jid, ev):
                try:
                    ev = dict(ev)
                    ev["pct"] = lo + float(ev.get("pct", 0.0)) * span
                    ev["stage"] = stage
                    progress_cb(jid, ev)
                except Exception:
                    pass
            return _cb

        # Fast path: a prior identical pass 1 already produced reusable stats —
        # run pass 2 only (mapped across the whole progress bar). If it fails
        # (stale/incompatible stats), fall through and regenerate pass 1.
        _did_reuse = False
        if _reuse_p1:
            logging.getLogger("BitCrusher").info(
                "[TwoPass] Reusing pass-1 stats (%s); running pass 2 only.",
                os.path.basename(passlog))
            rc2 = _ffmpeg_run_with_progress(cmd2, duration_s or 0.0, "Pass 2/2", cwd=temp_dir,
                                            progress_cb=_cb_pass(0.0, 1.0, "pass2"),
                                            job_id=job_id, stage="pass2")
            if rc2 == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                _did_reuse = True
            else:
                logging.getLogger("BitCrusher").warning(
                    "[TwoPass] Pass 2 with reused stats failed (rc=%s); regenerating pass 1.", rc2)

        if not _did_reuse:
            # Pass 1 — stats gathering (null output); show progress if duration known.
            rc1 = _ffmpeg_run_with_progress(cmd1, duration_s or 0.0, "Pass 1/2", cwd=temp_dir,
                                            progress_cb=_cb_pass(0.0, 0.5, "pass1"), job_id=job_id, stage="pass1")
            if rc1 != 0 and _hw:
                # GPU decode can fail on exotic sources/drivers — fall back to
                # software decode for this and all subsequent jobs.
                encoder_caps._mark_hw_decode_broken()
                logging.getLogger("BitCrusher").warning(
                    "HW decode failed (rc=%s); retrying with software decode.", rc1)
                cmd1, cmd2 = encoder_caps._strip_hw_args(cmd1), encoder_caps._strip_hw_args(cmd2)
                rc1 = _ffmpeg_run_with_progress(cmd1, duration_s or 0.0, "Pass 1/2", cwd=temp_dir,
                                                progress_cb=_cb_pass(0.0, 0.5, "pass1"), job_id=job_id, stage="pass1")
            if rc1 != 0:
                return False
            # Pass 1 succeeded — mark the stats reusable for later bitrate retries
            # of this same job (shared pass-log mode only; the caller owns cleanup).
            if not _owns_temp:
                try:
                    with open(_p1_sentinel, "w") as _sf:
                        _sf.write("1")
                except Exception:
                    pass
            # Pass 2 — actual encode with real-time progress display.
            rc2 = _ffmpeg_run_with_progress(cmd2, duration_s or 0.0, "Pass 2/2", cwd=temp_dir,
                                            progress_cb=_cb_pass(50.0, 0.5, "pass2"), job_id=job_id, stage="pass2")
        return rc2 == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    finally:
        # Only remove the temp dir we created. A shared pass-log dir is owned by
        # the caller (the size loop) so its stats survive for the next retry.
        if _owns_temp:
            import shutil
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


def _handbrake_encode(
    input_path: str,
    output_path: str,
    *,
    encoder: str | None,
    bitrate: int | None,
    crf: int | None,
    width: int | None,
    fps: float | None,
    audio_bitrate: int | None,
    audio_copy: bool,
    two_pass: bool,
    turbo: bool,
) -> bool:
    """
    Minimal HandBrakeCLI encode. Uses x264/x265 only, mapped from requested encoder.
    """
    import shutil, subprocess, os
    hb = HANDBRAKE_CLI if os.path.isfile(HANDBRAKE_CLI) else shutil.which("HandBrakeCLI") or shutil.which("HandBrakeCLI.exe")
    if not hb:
        return False

    e = (encoder or "").lower()
    hb_encoder = "x265" if ("265" in e or "hevc" in e) else "x264"

    cmd = [hb, "-i", input_path, "-o", output_path, "-e", hb_encoder, "--optimize"]
    if width:
        cmd += ["-w", str(int(width))]
    if fps:
        cmd += ["-r", str(int(round(float(fps))))]

    if two_pass and bitrate:
        cmd += ["-b", str(int(bitrate)//1000), "-2"]
        if turbo:
            cmd += ["--turbo"]
    elif crf is not None:
        cmd += ["-q", str(int(crf))]
    elif bitrate:
        cmd += ["-b", str(int(bitrate)//1000)]

    if audio_copy:
        cmd += ["-E", "copy"]
    elif audio_bitrate and int(audio_bitrate) > 0:
        cmd += ["-E", "av_aac", "-B", str(int(audio_bitrate)//1000)]
    else:
        cmd += ["-a", "none"]

    r = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ok = (r.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0)

    # If passthrough isn't supported for the input, retry with AVC AAC encode
    if (not ok) and audio_copy:
        try:
            # swap audio to aac quickly
            cmd_fallback = [c for c in cmd if c != "copy"]
            # ensure encoder & bitrate exist
            if "-E" in cmd_fallback:
                i = cmd_fallback.index("-E")
                cmd_fallback[i+1] = "av_aac"
            else:
                cmd_fallback += ["-E", "av_aac"]
            if audio_bitrate and int(audio_bitrate) > 0:
                if "-B" in cmd_fallback:
                    j = cmd_fallback.index("-B")
                    cmd_fallback[j+1] = str(int(audio_bitrate)//1000)
                else:
                    cmd_fallback += ["-B", str(int(audio_bitrate)//1000)]
            r2 = _sp_run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ok = (r2.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0)
        except Exception:
            ok = False

    return ok
