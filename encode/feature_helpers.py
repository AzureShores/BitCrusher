from __future__ import annotations

import os
import subprocess
import tempfile
import time

import encode.ffmpeg_exec as ffmpeg_exec
from encode.ffmpeg_exec import si, NO_WIN, _sp_run, _sp_check_output

# =====================================================================
# Batch-1 feature helpers: clipboard (CF_HDROP), multi-audio-track, lyrics
# =====================================================================


def set_clipboard_files(paths) -> bool:
    """
    Put one or more real files on the Windows clipboard as CF_HDROP, so a single
    Ctrl+V pastes them into Explorer, Discord, chat apps, etc. Native (ctypes) —
    no third-party dependency and works from a worker thread. No-op returning
    False on non-Windows or on any failure. Offline by design.
    """
    if os.name != "nt":
        return False
    try:
        files = [os.path.abspath(p) for p in (paths or [])
                 if isinstance(p, str) and p and os.path.exists(p)]
    except Exception:
        files = []
    if not files:
        return False
    try:
        import ctypes, struct
        from ctypes import wintypes

        CF_HDROP = 15
        GMEM_MOVEABLE = 0x0002

        # DROPFILES header (20 bytes) + double-null-terminated wide file list.
        # struct DROPFILES { DWORD pFiles; POINT pt; BOOL fNC; BOOL fWide; }
        # pFiles = offset to the file list (== sizeof(DROPFILES) == 20);
        # fWide = 1 marks the list as UTF-16.
        joined = "\0".join(files) + "\0\0"
        file_bytes = joined.encode("utf-16-le")
        header = struct.pack("<Iiiii", 20, 0, 0, 0, 1)
        payload = header + file_bytes

        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32
        # Declare handle/pointer types explicitly — the ctypes default (c_int) is
        # 32-bit and silently truncates 64-bit handles/pointers, which made
        # SetClipboardData receive a bad HGLOBAL and fail on 64-bit Python.
        k32.GlobalAlloc.restype = wintypes.HGLOBAL
        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalLock.restype = wintypes.LPVOID
        k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalFree.restype = wintypes.HGLOBAL
        k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        u32.OpenClipboard.restype = wintypes.BOOL
        u32.OpenClipboard.argtypes = [wintypes.HWND]
        u32.EmptyClipboard.restype = wintypes.BOOL
        u32.CloseClipboard.restype = wintypes.BOOL
        u32.SetClipboardData.restype = wintypes.HANDLE
        u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

        h_global = k32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not h_global:
            return False
        ptr = k32.GlobalLock(h_global)
        if not ptr:
            k32.GlobalFree(h_global)
            return False
        ctypes.memmove(ptr, payload, len(payload))
        k32.GlobalUnlock(h_global)

        if not u32.OpenClipboard(None):
            k32.GlobalFree(h_global)
            return False
        try:
            u32.EmptyClipboard()
            if not u32.SetClipboardData(CF_HDROP, h_global):
                # Ownership only transfers to the clipboard on success; free on failure.
                k32.GlobalFree(h_global)
                return False
        finally:
            u32.CloseClipboard()
        return True
    except Exception:
        return False


def get_clipboard_media_paths(temp_dir=None) -> list:
    """
    Read the clipboard for either a file list (copied in Explorer/Discord) or a
    raw bitmap (e.g. a screenshot) so it can be dropped straight into the queue.
    A raw bitmap is saved as a PNG under temp_dir (or the system temp dir) since
    the encode pipeline needs a real file. Returns [] if the clipboard holds
    neither. Uses PIL (already a dependency); offline, no-op on any failure.
    """
    try:
        from PIL import ImageGrab
    except Exception:
        return []
    try:
        data = ImageGrab.grabclipboard()
    except Exception:
        return []
    if data is None:
        return []
    if isinstance(data, list):
        return [p for p in data if isinstance(p, str) and os.path.isfile(p)]
    try:
        d = temp_dir or tempfile.gettempdir()
        os.makedirs(d, exist_ok=True)
        out_path = os.path.join(d, f"bc_clipboard_{int(time.time() * 1000)}.png")
        data.save(out_path, "PNG")
        return [out_path]
    except Exception:
        return []


def _count_audio_streams(input_path: str) -> int:
    """Number of audio streams in a media file (ffprobe). 0 on failure."""
    try:
        out = _sp_check_output(
            [ffmpeg_exec.FFPROBE, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", input_path],
            text=True, startupinfo=si, creationflags=NO_WIN)
        return len([ln for ln in (out or "").splitlines() if ln.strip()])
    except Exception:
        return 0


def _audio_track_plan(input_path: str, advanced_options: dict,
                      default_mode: str = "keepfirst") -> dict:
    """
    Decide how to map audio for a multi-track source. Returns a dict:
      {"n": <stream count>, "mode": "keepfirst"|"mix", "multi": bool, "notice": str|None}
    Only sources with >1 audio track get a notice / non-default mapping; single-track
    sources are unaffected (mode "keepfirst", multi False, notice None).

    default_mode mirrors BitCrusherV9.ADVANCED_DEFAULTS["audio_track_mode"] — kept
    as a literal default here rather than importing the monolith's settings dict,
    so this module stays free of a back-import onto BitCrusherV9.
    """
    mode = str((advanced_options or {}).get("audio_track_mode") or default_mode).strip().lower()
    if mode not in ("keepfirst", "mix"):
        mode = "keepfirst"
    n = _count_audio_streams(input_path)
    multi = n > 1
    notice = None
    if multi:
        if mode == "mix":
            notice = (f"[Audio] {n} audio tracks found - mixing them into one "
                      f"(amix) so nothing is dropped (e.g. game + mic).")
        else:
            notice = (f"[Audio] {n} audio tracks found - keeping track 1 only. "
                      f"Switch multi-track mode to 'mix' to merge them.")
    return {"n": n, "mode": mode, "multi": multi, "notice": notice}


def _audio_map_ffmpeg_args(plan: dict, audio_copy_ref: dict | None = None) -> list[str]:
    """
    Build the ffmpeg stream-map/filter args for a multi-track plan, to be spliced
    into a video encode command right after the input. Single-track / keepfirst
    with a single track returns an explicit-but-harmless map. For "mix" it returns
    an amix filter_complex; the caller must re-encode audio (amix can't -c:a copy),
    so audio_copy_ref["audio_copy"] is forced False when provided.
    """
    plan = plan or {}
    mode = plan.get("mode", "keepfirst")
    n = int(plan.get("n") or 0)
    if mode == "mix" and n > 1:
        if isinstance(audio_copy_ref, dict):
            audio_copy_ref["audio_copy"] = False
        return ["-filter_complex",
                f"[0:a]amix=inputs={n}:normalize=0[bcaout]",
                "-map", "0:v:0?", "-map", "[bcaout]"]
    # keepfirst (default): map video + the first audio track explicitly so
    # ffmpeg's "pick the stream with the most channels" default can't grab the
    # wrong track and silently drop track 1.
    return ["-map", "0:v:0?", "-map", "0:a:0?"]


def _read_sibling_lrc(input_path: str) -> str | None:
    """
    Return the text of a .lrc lyric file sitting next to input_path (same stem),
    or None. Offline, tolerant of the common encodings .lrc ships in.
    """
    try:
        base, _ = os.path.splitext(input_path)
    except Exception:
        return None
    for cand in (base + ".lrc", base + ".LRC"):
        try:
            if os.path.isfile(cand):
                for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
                    try:
                        with open(cand, "r", encoding=enc) as f:
                            txt = f.read().strip()
                        if txt:
                            return txt
                    except Exception:
                        continue
        except Exception:
            continue
    return None


def _embed_lyrics_into(output_path: str, lyrics: str, status_cb=None) -> bool:
    """
    Add a `lyrics` metadata tag to an existing audio file in place (stream copy +
    -metadata), remuxing through a temp file. ffmpeg maps the generic `lyrics`
    key to the container-appropriate frame (USLT / ©lyr / vorbis comment). No-op
    returning False if the container can't be rewritten. Offline.
    """
    if not lyrics or not output_path or not os.path.isfile(output_path):
        return False
    try:
        root, ext = os.path.splitext(output_path)
        tmp = root + "._bc_lrc_" + ext
        cmd = [ffmpeg_exec.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-i", output_path, "-map", "0", "-c", "copy",
               "-metadata", f"lyrics={lyrics}", tmp]
        proc = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, startupinfo=si, creationflags=NO_WIN)
        if proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, output_path)
            if callable(status_cb):
                try:
                    status_cb("[Lyrics] Embedded lyrics from sibling .lrc into the output tags.",
                              level="INFO")
                except Exception:
                    pass
            return True
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    except Exception:
        pass
    return False
