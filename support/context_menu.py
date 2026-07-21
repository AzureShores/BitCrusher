"""Explorer right-click presets ("BitCrusher: 10MB" etc.) via HKCU.

Registers per-extension shell verbs under
HKCU\\Software\\Classes\\SystemFileAssociations\\<ext>\\shell\\ - current
user only, no admin, and scoped to video extensions instead of the
global *\\shell (which would pollute every file type). Each entry
invokes the existing --enqueue hand-off with an --enqueue-target cap,
so clicks land in the running window (IPC TARGET line) or launch one.

unregister deletes exactly the keys register created - fixed name list,
no wildcard deletion. Key-path/command builders are pure and tested;
winreg writes are Windows-only and manual-verified.
"""
from __future__ import annotations

import os
import sys

from support.sendto_ipc import _sendto_launch_target

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm")

# (key_suffix, menu_label, target_mb). Key names are stable identifiers -
# unregister enumerates exactly these.
PRESETS = (
    ("10mb", "BitCrusher: 10 MB (Discord)", 10),
    ("25mb", "BitCrusher: 25 MB", 25),
    ("50mb", "BitCrusher: 50 MB", 50),
)

_ROOT_FMT = r"Software\Classes\SystemFileAssociations\{ext}\shell\BitCrusher.{suffix}"


def preset_key_paths() -> list[str]:
    """Every HKCU-relative key this module ever creates (exact list)."""
    return [_ROOT_FMT.format(ext=ext, suffix=suffix)
            for ext in VIDEO_EXTS
            for suffix, _label, _mb in PRESETS]


def build_command(target_mb: int) -> str:
    """Shell command string for one preset entry ("%1" = clicked file)."""
    exe, args = _sendto_launch_target()
    return f'"{exe}" {args} --enqueue-target {int(target_mb)} "%1"'


def register_presets() -> tuple[bool, str]:
    """Create the right-click entries for the current user. (ok, msg)."""
    if os.name != "nt":
        return False, "Context-menu presets are Windows-only."
    try:
        import winreg
        made = 0
        for ext in VIDEO_EXTS:
            for suffix, label, mb in PRESETS:
                key_path = _ROOT_FMT.format(ext=ext, suffix=suffix)
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                    winreg.SetValueEx(k, None, 0, winreg.REG_SZ, label)
                    winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ,
                                      sys.executable)
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                      key_path + r"\command") as k:
                    winreg.SetValueEx(k, None, 0, winreg.REG_SZ,
                                      build_command(mb))
                made += 1
        return True, (f"Registered {made} right-click entries "
                      f"({len(PRESETS)} presets x {len(VIDEO_EXTS)} video types).")
    except Exception as e:
        return False, f"Context-menu registration error: {e}"


def unregister_presets() -> tuple[bool, str]:
    """Delete exactly the keys register_presets creates. (ok, msg)."""
    if os.name != "nt":
        return False, "Context-menu presets are Windows-only."
    try:
        import winreg
        removed = 0
        for key_path in preset_key_paths():
            for sub in (key_path + r"\command", key_path):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
                    removed += 1
                except FileNotFoundError:
                    pass
        if removed:
            return True, "Right-click presets removed."
        return True, "Right-click presets were not installed."
    except Exception as e:
        return False, f"Context-menu removal error: {e}"
