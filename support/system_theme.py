"""Detect the OS light/dark mode preference, stdlib-only.

Windows: HKCU\\...\\Personalize\\AppsUseLightTheme (winreg, stdlib). Other
platforms: no reliable cross-desktop-environment signal without a new
dependency, so this returns None there and the "Follow system theme" toggle
is simply a no-op off Windows - consistent with the rest of the codebase's
cross-platform approach (features degrade gracefully rather than requiring
extra installs per OS).
"""
from __future__ import annotations

import os


def detect_system_theme() -> str | None:
    """Returns "Light", "Dark", or None if it can't be determined."""
    if os.name != "nt":
        return None
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "Light" if int(value) != 0 else "Dark"
        finally:
            winreg.CloseKey(key)
    except Exception:
        return None
