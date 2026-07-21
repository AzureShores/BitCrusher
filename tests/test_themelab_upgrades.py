"""Theme Lab C5 upgrade tests: contrast autofix, light/dark pairing,
share-code round-trip (pure helpers in ui/ui_aesthetics.py)."""
from ui.ui_aesthetics import (_wcag_ratio, autofix_contrast, derive_palette,
                              pair_light_dark, theme_from_share_string,
                              theme_to_share_string)

_BAD = {
    "APP_BG": "#101418", "CARD_BG": "#1a1f26",
    "FG": "#20242a",        # nearly invisible on dark bg
    "FG_SUB": "#242a31", "TITLE": "#1e2228",
    "ACCENT": "#2b3038", "ACCENT_2": "#2b3038",
    "ERROR": "#221a1a", "WARN": "#22200f",
}


def test_autofix_reaches_aa():
    fixed = autofix_contrast(_BAD)
    assert _wcag_ratio(fixed["FG"], fixed["APP_BG"]) >= 7.0
    assert _wcag_ratio(fixed["FG_SUB"], fixed["CARD_BG"]) >= 4.5
    assert _wcag_ratio(fixed["TITLE"], fixed["APP_BG"]) >= 4.5


def test_autofix_preserves_hue_roughly():
    import colorsys
    def hue(hx):
        hx = hx.lstrip("#")
        r, g, b = (int(hx[i:i+2], 16) / 255 for i in (0, 2, 4))
        return colorsys.rgb_to_hls(r, g, b)[0]
    fixed = autofix_contrast(_BAD)
    # Hue drift stays small (lightness-only nudge). Grey inputs are exempt.
    assert abs(hue(fixed["WARN"]) - hue(_BAD["WARN"])) < 0.1


def test_autofix_idempotent_on_good_palette():
    good = derive_palette("#4caf7d", "#14161a")
    once = autofix_contrast(good)
    twice = autofix_contrast(once)
    assert once == twice


def test_pair_flips_mode():
    dark = derive_palette("#4caf7d", "#14161a")
    light = pair_light_dark(dark)
    # Dark bg is darker than its light counterpart.
    def lum(hx):
        hx = hx.lstrip("#")
        return sum(int(hx[i:i+2], 16) for i in (0, 2, 4))
    assert lum(light["APP_BG"]) > lum(dark["APP_BG"])
    # Accent carried across.
    assert light["ACCENT"].lower() == "#4caf7d"


def test_pair_carries_layout_knobs():
    src = dict(derive_palette("#4caf7d", "#14161a"))
    src["_PADDING_SCALE"] = 1.3
    src["_BORDER_WIDTH"] = 2
    out = pair_light_dark(src)
    assert out["_PADDING_SCALE"] == 1.3 and out["_BORDER_WIDTH"] == 2


def test_share_round_trip():
    pal = derive_palette("#c62828", "#f4f5f7")
    pal["_PADDING_SCALE"] = 1.1
    code = theme_to_share_string(pal)
    assert code.startswith("BCTHEME1:")
    back = theme_from_share_string(code)
    for k in ("APP_BG", "CARD_BG", "FG", "ACCENT", "TITLE"):
        assert back[k] == pal[k]
    assert back["_PADDING_SCALE"] == 1.1


def test_share_rejects_junk():
    assert theme_from_share_string("") is None
    assert theme_from_share_string("hello") is None
    assert theme_from_share_string("BCTHEME1:not-base64!!!") is None
    assert theme_from_share_string(None) is None
