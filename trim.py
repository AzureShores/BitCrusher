from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import numpy as np

import ffmpeg_exec
from ffmpeg_exec import si, NO_WIN, _sp_run, _sp_check_output
from media_math import get_media_type
from media_probe import get_video_metadata
from audio_encode import _probe_audio_meta
from feature_helpers import _count_audio_streams

# ---- Trim-aware compression core -------------------------------------------
# Cutting duration is a 2-10x lever (half the clip = double the bitrate under
# the same cap). A trim range produces a stream-copied intermediate (fast, zero
# quality loss) that the whole pipeline then consumes unchanged - features,
# codec race, preprocessing, VMAF and packing all operate on the trimmed
# content automatically. The source file is never modified.


def _rmtree_quiet(path: str | None) -> None:
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _parse_timespec(s: str) -> float:
    """'SS(.d)', 'MM:SS(.d)' or 'HH:MM:SS(.d)' -> seconds. Raises ValueError."""
    t = str(s or "").strip()
    if not t:
        raise ValueError("empty time")
    parts = t.split(":")
    if len(parts) > 3 or any(p.strip() == "" for p in parts):
        raise ValueError(f"bad time '{s}'")
    try:
        nums = [float(p) for p in parts]
    except Exception:
        raise ValueError(f"bad time '{s}'")
    secs = 0.0
    for n in nums:
        secs = secs * 60.0 + n
    if secs < 0:
        raise ValueError(f"negative time '{s}'")
    return secs


def _parse_trim_range(s: str) -> tuple[float, float]:
    """'START-END' (each SS / MM:SS / HH:MM:SS) -> (start_s, end_s)."""
    t = str(s or "").strip()
    if "-" not in t:
        raise ValueError("expected START-END (e.g. 1:42-2:05)")
    a_raw, b_raw = t.split("-", 1)
    a, b = _parse_timespec(a_raw), _parse_timespec(b_raw)
    if b <= a:
        raise ValueError(f"end ({b_raw.strip()}) must be after start ({a_raw.strip()})")
    return a, b


def _prev_keyframe_time(input_path: str, t: float) -> float:
    """
    The last video keyframe at or before t (seconds). Stream-copy trims can only
    start on a keyframe; snapping backward gives a short lead-in instead of a
    broken first GOP. Reads only a few seconds of the file (read_intervals).
    Falls back to a 2.5s-early guess when probing fails.
    """
    if t <= 0.1:
        return 0.0
    try:
        lo = max(0.0, t - 8.0)
        out = _sp_check_output(
            [ffmpeg_exec.FFPROBE, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
             "-show_entries", "frame=pts_time", "-of", "csv=p=0",
             "-read_intervals", f"{lo:.3f}%{t + 0.5:.3f}", input_path],
            text=True, startupinfo=si, creationflags=NO_WIN)
        kfs = []
        for ln in (out or "").splitlines():
            ln = ln.strip().rstrip(",")
            if ln:
                try:
                    kfs.append(float(ln))
                except Exception:
                    pass
        prior = [k for k in kfs if k <= t + 0.001]
        if prior:
            return max(0.0, max(prior))
    except Exception:
        pass
    return max(0.0, t - 2.5)


def make_trim_intermediate(input_path: str, start_s: float, end_s: float, *,
                           fade: bool = False, media_type: str = "video",
                           status_cb=None) -> tuple[str, str] | None:
    """
    Cut [start_s, end_s] of the source into a temp intermediate the pipeline
    compresses instead of the full file. Returns (intermediate_path, temp_dir)
    — caller removes temp_dir when done — or None on failure.

    Default: stream copy (fast, zero generation loss). Video starts snap back
    to the previous keyframe (a short lead-in); the end is exact.
    fade=True: frame-exact re-encode at visually-lossless settings with a 0.5s
    audio+video fade at both ends (the polish mode for music/rhythm clips).
    """
    def _say(msg, level="INFO"):
        if callable(status_cb):
            try:
                status_cb(msg, level)
            except Exception:
                pass

    try:
        stem, ext = os.path.splitext(os.path.basename(input_path))
        work = tempfile.mkdtemp(prefix="bc_trim_")
        _FADE = 0.5

        if fade:
            dur = max(0.2, end_s - start_s)
            _af = (f"afade=t=in:st=0:d={_FADE},"
                   f"afade=t=out:st={max(0.0, dur - _FADE):.3f}:d={_FADE}")
            if media_type == "audio":
                out = os.path.join(work, f"{stem}_clip.flac")
                cmd = [ffmpeg_exec.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                       "-ss", f"{start_s:.3f}", "-i", input_path, "-t", f"{dur:.3f}",
                       "-af", _af, "-c:a", "flac", "-map_metadata", "0", out]
            else:
                out = os.path.join(work, f"{stem}_clip.mp4")
                _vf = (f"fade=t=in:st=0:d={_FADE},"
                       f"fade=t=out:st={max(0.0, dur - _FADE):.3f}:d={_FADE}")
                cmd = [ffmpeg_exec.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                       "-ss", f"{start_s:.3f}", "-i", input_path, "-t", f"{dur:.3f}",
                       "-vf", _vf, "-af", _af,
                       "-c:v", "libx264", "-crf", "12", "-preset", "veryfast",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
                       "-movflags", "+faststart", out]
            _say(f"[Trim] Cutting {start_s:.1f}s-{end_s:.1f}s frame-exact with "
                 f"{_FADE:.1f}s fades (near-lossless re-encode).")
        else:
            if media_type == "audio":
                cut_from = start_s          # audio frames cut cleanly, no keyframes
                maps = ["-map", "0"]
                out = os.path.join(work, f"{stem}_clip{ext or '.mka'}")
            else:
                cut_from = _prev_keyframe_time(input_path, start_s)
                maps = ["-map", "0:v:0?", "-map", "0:a?", "-sn", "-dn"]
                out = os.path.join(work, f"{stem}_clip{ext or '.mp4'}")
            dur = max(0.2, end_s - cut_from)
            cmd = ([ffmpeg_exec.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{cut_from:.3f}", "-i", input_path, "-t", f"{dur:.3f}"]
                   + maps + ["-c", "copy", "-ignore_unknown",
                             "-avoid_negative_ts", "make_zero", out])
            lead = start_s - cut_from
            _say(f"[Trim] Cutting {start_s:.1f}s-{end_s:.1f}s via stream copy"
                 + (f" (start snapped to keyframe, {lead:.1f}s lead-in)" if lead > 0.05 else "")
                 + " - zero quality loss.")

        proc = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, startupinfo=si, creationflags=NO_WIN)
        if getattr(proc, "returncode", 1) == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            return out, work
        _rmtree_quiet(work)
        return None
    except Exception:
        return None


# ---- Trim suggestion markers (audio-energy analysis) ------------------------
# Offline heuristic ASSISTANT for picking a trim range: windowed RMS loudness
# per audio track, z-scored per track so a quiet mic and loud game audio are
# comparable. Track 2+ (the OBS/ShadowPlay mic convention) is weighted higher —
# a mic spike over steady game audio is the best available "something just
# happened" signal. These are suggestions, never automatic cuts: semantically
# important-but-silent moments (a great play in a card game) are undetectable
# by any signal analysis, so manual trim stays the primary interface.

def _fmt_ts(seconds: float) -> str:
    s = max(0, int(round(float(seconds))))
    return f"{s // 60}:{s % 60:02d}"


def _rank_energy_windows(tracks: list, win_s: float, clip_seconds: float,
                         top_n: int = 3, mic_weight: float = 1.5,
                         total_s: float | None = None) -> list[dict]:
    """
    Rank candidate clip windows from per-track RMS series (one value per win_s).
    Pure logic (testable): z-score each track, weight non-primary tracks (mic)
    higher, pick the strongest well-separated peaks, and cut a clip_seconds
    window around each (35% lead-in / 65% follow-through).
    Returns [{"start","end","score","track"}] sorted by score, strongest first.
    """
    tracks = [list(t) for t in (tracks or []) if t]
    if not tracks:
        return []
    n = min(len(t) for t in tracks)
    if n < 3:
        return []

    zs = []
    for t in tracks:
        vals = t[:n]
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        std = var ** 0.5
        zs.append([((v - mean) / std) if std > 1e-9 else 0.0 for v in vals])

    combined = []
    track_of = []
    for i in range(n):
        best, bt = None, 0
        for ti, z in enumerate(zs):
            w = mic_weight if ti >= 1 else 1.0
            v = w * z[i]
            if best is None or v > best:
                best, bt = v, ti
        combined.append(best)
        track_of.append(bt)
    # Light smoothing so a single hot window doesn't outrank a sustained burst.
    sm = [(combined[max(0, i - 1)] + combined[i] + combined[min(n - 1, i + 1)]) / 3.0
          for i in range(n)]

    total = float(total_s if total_s is not None else n * win_s)
    min_sep = max(1, int(round((clip_seconds * 0.8) / win_s)))
    order = sorted(range(n), key=lambda i: -sm[i])
    picked: list[int] = []
    for i in order:
        if sm[i] < 0.75:      # below a meaningful spike; don't invent highlights
            break
        if all(abs(i - p) >= min_sep for p in picked):
            picked.append(i)
        if len(picked) >= top_n:
            break

    out = []
    for i in picked:
        t = i * win_s
        start = max(0.0, t - 0.35 * clip_seconds)
        end = min(total, start + clip_seconds)
        start = max(0.0, end - clip_seconds)
        # Attribute the track at the RAW peak near the picked (smoothed) index —
        # smoothing can tie-shift the pick one window off the spike, where the
        # baseline noise of another track would mislabel the source.
        j = max(range(max(0, i - 1), min(n, i + 2)), key=lambda x: combined[x])
        out.append({"start": round(start, 1), "end": round(end, 1),
                    "score": round(sm[i], 2), "track": track_of[j]})
    return out


def _audio_energy_tracks(input_path: str, n_tracks: int, win_s: float = 0.5) -> list:
    """Windowed RMS per audio track via a mono 8 kHz PCM decode (tiny + fast)."""
    out = []
    sr = 8000
    for ti in range(max(1, int(n_tracks))):
        try:
            cmd = [ffmpeg_exec.FFMPEG, "-v", "error", "-i", input_path, "-map", f"0:a:{ti}",
                   "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
            r = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        startupinfo=si, creationflags=NO_WIN)
            raw = getattr(r, "stdout", b"") or b""
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            w = int(sr * win_s)
            k = len(data) // w
            if k <= 0:
                out.append([])
                continue
            rms = np.sqrt(np.mean(np.square(data[:k * w].reshape(k, w)), axis=1))
            out.append([float(v) for v in rms])
        except Exception:
            out.append([])
    return [t for t in out if t] or []


def suggest_trim_ranges(input_path: str, *, clip_seconds: float = 20.0,
                        top_n: int = 3, status_cb=None) -> list[dict]:
    """
    Suggest up to top_n candidate trim ranges from audio energy. Adds a human
    "why" to each. Returns [] when there is nothing meaningful to point at
    (silent/uniform audio) — the caller should say so, never guess.
    """
    try:
        mt = get_media_type(input_path)
        if mt == "video":
            dur = float(get_video_metadata(input_path)[0] or 0.0)
        elif mt == "audio":
            dur = float((_probe_audio_meta(input_path) or {}).get("duration") or 0.0)
        else:
            return []
    except Exception:
        dur = 0.0
    if dur <= clip_seconds * 1.2:
        return []       # nothing to cut away
    n_a = _count_audio_streams(input_path)
    if n_a <= 0:
        if callable(status_cb):
            status_cb("[Suggest] No audio track to analyze - set the trim manually.", "INFO")
        return []
    win_s = 0.5
    tracks = _audio_energy_tracks(input_path, min(n_a, 3), win_s=win_s)
    cands = _rank_energy_windows(tracks, win_s, float(clip_seconds),
                                 top_n=top_n, total_s=dur)
    for c in cands:
        c["why"] = ("mic/track-2 spike" if c.get("track", 0) >= 1 else "audio energy peak")
        c["range"] = f"{_fmt_ts(c['start'])}-{_fmt_ts(c['end'])}"
    if callable(status_cb):
        if cands:
            status_cb("[Suggest] Candidate moments: "
                      + "; ".join(f"{c['range']} ({c['why']}, score {c['score']})"
                                  for c in cands), "INFO")
        else:
            status_cb("[Suggest] No clear audio peaks found - set the trim manually.", "INFO")
    return cands
