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


# --- WCAG contrast ---------------------------------------------------------
# _is_light_color above is a cheap linear-luminance test, fine for "should the
# text on this button be black or white". Anything that has to MEET a ratio
# needs the gamma-corrected version, so the two live side by side on purpose.

def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        return (0, 0, 0)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def relative_luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance, 0.0 (black) to 1.0 (white)."""
    def _lin(v: int) -> float:
        u = v / 255.0
        return u / 12.92 if u <= 0.03928 else ((u + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(hex_color)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours, 1.0 (identical) to 21.0."""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def best_possible_contrast(bg: str) -> float:
    """The highest ratio ANY colour can reach against this background.

    Mid-luminance backgrounds are a trap: against #777777 nothing beats about
    4.5:1, so a 7:1 target there is unreachable by definition rather than by a
    bad search. Callers that must guarantee readability check this first.
    """
    return max(contrast_ratio("#000000", bg), contrast_ratio("#ffffff", bg))


def ensure_contrast(fg: str, bg: str, target: float = 4.5,
                    keep_hue: bool = True) -> str:
    """Return `fg` adjusted until it clears `target` against `bg`.

    Walks lightness in fixed additive steps - both directions, nearest result
    wins - because the old multiplicative approach could not move a colour
    whose lightness was already 0: l * 1.1 stays 0, so a black accent stayed
    black and the "readable" colour it produced was invisible. Hue and
    saturation are preserved where possible.

    Always returns something at least as readable as the input, and falls back
    to black or white when the hue simply cannot reach the target. If even
    that cannot (a mid-grey background), the best available is returned - see
    best_possible_contrast() to detect that case before it happens.
    """
    import colorsys

    fg = fg if isinstance(fg, str) and fg.strip() else "#808080"
    if contrast_ratio(fg, bg) >= target:
        return _norm(fg)

    r, g, b = (v / 255.0 for v in _rgb(fg))
    h, l0, s = colorsys.rgb_to_hls(r, g, b)
    if not keep_hue:
        s = 0.0

    hits = []
    for direction in (1, -1):
        l = l0
        for _ in range(50):
            l += direction * 0.02
            if not (0.0 <= l <= 1.0):
                break
            cand = _from_hls(h, l, s)
            if contrast_ratio(cand, bg) >= target:
                # First hit in a direction is that direction's nearest match.
                hits.append((abs(l - l0), cand))
                break

    if hits:
        return min(hits)[1]          # the smaller lightness move of the two

    # The hue cannot get there. Black or white always maximises the ratio.
    for pole in ("#000000", "#ffffff"):
        if contrast_ratio(pole, bg) >= target:
            return pole
    poles = ("#000000", "#ffffff")
    return max(poles, key=lambda c: contrast_ratio(c, bg))


def _norm(hex_color: str) -> str:
    r, g, b = _rgb(hex_color)
    return f"#{r:02x}{g:02x}{b:02x}"


def _from_hls(h: float, l: float, s: float) -> str:
    import colorsys
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), s)
    return f"#{int(round(r*255)):02x}{int(round(g*255)):02x}{int(round(b*255)):02x}"
