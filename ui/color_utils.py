from __future__ import annotations

import colorsys


def _hsl_shift(hex_color: str, h_delta=0.0, s_mul=1.0, l_mul=1.0) -> str:
    # Defensive: a missing/None colour (e.g. an unset palette global on a
    # first-run race) must not crash the whole GUI. Fall back to neutral grey.
    if not isinstance(hex_color, str) or not hex_color.strip():
        hex_color = "#808080"
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    h,l,s = colorsys.rgb_to_hls(r,g,b)
    h = (h + h_delta) % 1.0
    s = max(0.0, min(1.0, s * s_mul))
    l = max(0.0, min(1.0, l * l_mul))
    r,g,b = colorsys.hls_to_rgb(h,l,s)
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"


def _is_light_color(hex_color: str) -> bool:
    """True if the colour is perceptually light (so dark text should sit on it)."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 150
    except Exception:
        return False


def _contrast_fg(bg: str) -> str:
    """Pick black or white text for maximum contrast against the given background."""
    return "#101215" if _is_light_color(bg) else "#FFFFFF"
