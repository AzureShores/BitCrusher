from __future__ import annotations

import os
import shutil
import subprocess

import ffmpeg_exec
from ffmpeg_exec import _sp_run


def _privacy_args(preset: str | None):
    p = str(preset or "default").lower()
    if p == "strict":
        return [
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-write_tmcd", "0",
            "-fflags", "+bitexact",
            "-flags:v", "+bitexact",
            "-flags:a", "+bitexact",
        ]
    elif p == "keep":
        return []
    else:  # default
        return ["-map_metadata", "-1"]


def _remux_smart(src: str, dst: str, privacy_args: list[str] | None = None) -> bool:

    FFMPEG = ffmpeg_exec.FFMPEG

    # Window-suppression for the ffmpeg child (Windows). These were previously
    # only defined in an unreachable block after `return False`, which made
    # `subprocess`/`si`/`NO_WIN` unbound function-locals — so `_run_and_check`
    # raised NameError on EVERY call and the whole passthrough remux silently
    # failed (callers caught it and fell through to a full re-encode).
    si = None
    NO_WIN = 0
    try:
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            NO_WIN = 0x08000000
    except Exception:
        si, NO_WIN = None, 0

    def _mk_base_maps():
        base = [
            "-ignore_unknown",
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-map", "0:v:0?",
            "-map", "0:a:0?",
            "-sn", "-dn",
            "-map", "-0:tmcd",
            "-map", "-0:d",
            "-movflags", "+faststart",
        ]
        if privacy_args:
            for a in privacy_args:
                if a not in base:
                    base.append(a)
        return base

    real_dst = dst
    need_rename = False
    if str(dst).lower().endswith(".partial") and str(dst).lower().endswith(".mp4.partial"):
        real_dst = dst[:-len(".partial")]  # strip only the ".partial"
        need_rename = True

    def _run_and_check(cmd):
        p = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, startupinfo=si, creationflags=NO_WIN)
        ok = (p.returncode == 0) and os.path.exists(cmd[-1]) and os.path.getsize(cmd[-1]) > 0
        return ok

    base_maps = _mk_base_maps()

    cmd1 = [FFMPEG, "-y", "-i", src, "-c", "copy", *base_maps, "-f", "mp4", real_dst]
    if _run_and_check(cmd1):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    cmd2 = [FFMPEG, "-y", "-i", src, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", *base_maps, "-f", "mp4", real_dst]
    if _run_and_check(cmd2):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    cmd3 = [
        FFMPEG, "-y", "-i", src,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-vf", "format=yuv420p", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        *base_maps, "-f", "mp4", real_dst
    ]
    if _run_and_check(cmd3):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    base_relaxed = ["-ignore_unknown", "-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart"]

    cmd4a = [FFMPEG, "-y", "-i", src, "-c", "copy", *base_relaxed, "-f", "mp4", real_dst]
    if _run_and_check(cmd4a):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    cmd4b = [FFMPEG, "-y", "-i", src, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", *base_relaxed, "-f", "mp4", real_dst]
    if _run_and_check(cmd4b):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    cmd4c = [
        FFMPEG, "-y", "-i", src,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-vf", "format=yuv420p", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        *base_relaxed, "-f", "mp4", real_dst
    ]
    if _run_and_check(cmd4c):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    return False
