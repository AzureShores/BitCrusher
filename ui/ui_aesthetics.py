

from __future__ import annotations
import tkinter as tk
from tkinter import ttk, Toplevel
from tkinter import filedialog as fd
from tkinter import colorchooser as tkcolor
from tkinter import simpledialog as tksimple
import tkinter.font as tkfont
import json, colorsys
from typing import Callable
from importlib import import_module


def _noop(*_a, **_k): pass

def _get(host, name, default):
    return getattr(host, name, default)


def _set_font_family(root: tk.Tk, family_order: tuple[str, ...] = ("Segoe UI Variable","Inter","Segoe UI","Arial"),
                     force_family: str | None = None, force_size: int | None = None):
    available = set(tkfont.families(root))
    pick = (force_family if force_family in available else None) or next((f for f in family_order if f in available), "Segoe UI")
    for fam in ("TkDefaultFont","TkTextFont","TkHeadingFont","TkMenuFont","TkTooltipFont","TkFixedFont"):
        try:
            f = tkfont.nametofont(fam)
            sz = f.cget("size")
            if fam == "TkDefaultFont" and (force_size or 0) > 0:
                f.configure(family=pick, size=int(force_size))
            elif fam == "TkDefaultFont" and sz < 10:
                f.configure(family=pick, size=10)
            else:
                f.configure(family=pick)
        except Exception:
            pass

def animated_retheme(host, theme_name: str, fade_ms: int = 0, dim_to: float | None = None):
    
    root: tk.Tk = host.root
    style: ttk.Style = host.style

    try:
        mod = import_module(host.__module__)
    except Exception:
        mod = None

    def _resolve(name, default=_noop):
        if hasattr(host, name):
            return getattr(host, name)
        if mod and hasattr(mod, name):
            return getattr(mod, name)
        return default

    apply_theme_fn     = _resolve("apply_theme")
    retheme_runtime_fn = _resolve("retheme_runtime")
    fade_window_fn     = _resolve("fade_window")

    def _maybe_dim(start_alpha, end_alpha):
        try:
            if dim_to is not None:

                fade_window_fn(root, start=start_alpha, end=dim_to, dur_ms=max(80, int(fade_ms*0.5)))
        except Exception:
            pass

    def _maybe_undim(start_alpha, end_alpha):
        try:
            if dim_to is not None:
                fade_window_fn(root, start=dim_to, end=1.0, dur_ms=max(120, int(fade_ms*0.7)))
        except Exception:
            pass

    try:
        cur_alpha = 1.0
        try:
            cur_alpha = root.attributes("-alpha")
        except Exception:
            pass
        _maybe_dim(cur_alpha, dim_to)
    except Exception:
        pass

    try:
        if retheme_runtime_fn is not _noop:
            retheme_runtime_fn(host, style, theme_name)
        else:
            apply_theme_fn(style, theme_name)
    except Exception:
        pass

    _maybe_undim(dim_to, 1.0)


def make_card(parent: tk.Misc, padding: int = 12) -> ttk.Frame:
    

    shadow = ttk.Frame(parent, style="TFrame")
    shadow.place_configure()  # noop if used with .pack/.grid

    card = ttk.Frame(shadow, style="Card.TFrame")

    try:

        s = ttk.Style()
        cbg = s.lookup("Card.TFrame", "background") or "#1C1F24"

        try:
            from importlib import import_module as _imp
            _mod = _imp(parent.winfo_toplevel().__class__.__module__)
            _hsl = getattr(_mod, "_hsl_shift", lambda c, **_: c)
        except Exception:
            _hsl = lambda c, **_: c
        backplate = tk.Frame(shadow, bg=_hsl(cbg, l_mul=0.80))

        backplate.place(relx=0, rely=0, x=2, y=3, relwidth=1, relheight=1)
        card.place(in_=shadow, relx=0, rely=0, x=0, y=0, relwidth=1, relheight=1)
    except Exception:
        pass

    shadow.card = card  # type: ignore

    for side in ("top", "bottom", "left", "right"):
        tk.Frame(card, height=padding if side in ("top", "bottom") else 0,
                 width=padding if side in ("left", "right") else 0).pack(
            side=side, fill="x" if side in ("top", "bottom") else "y")
    return card

def wrap_as_card(widget: tk.Widget) -> ttk.Frame:
    
    parent = widget.master
    card = make_card(parent)
    widget.pack_forget()
    widget.master = card  # type: ignore
    widget.pack(fill="both", expand=True)
    return card


def install_button_hover_fx(root: tk.Misc):
    """Give every button a pointer cursor (the old tk 'scale' call was a no-op on ttk widgets)."""
    def _bind_recursive(w: tk.Widget):
        if isinstance(w, (ttk.Button, tk.Button)):
            try:
                w.configure(cursor="hand2")
            except Exception:
                pass
        for child in w.winfo_children():
            _bind_recursive(child)
    _bind_recursive(root)

def install_queue_drop_highlight(host):
    
    install_drop_highlight = _get(host, "install_drop_highlight", _noop)
    if hasattr(host, "queue_container"):
        try:
            install_drop_highlight(host.queue_container)  # type: ignore
        except Exception:
            pass

def install_snackbar_hooks(host):
    
    snackbar = _get(host, "snackbar", _noop)
    def info(msg: str): snackbar(host.root, msg, millis=1800, kind="info")
    def warn(msg: str): snackbar(host.root, msg, millis=2200, kind="warn")
    def err (msg: str): snackbar(host.root, msg, millis=2600, kind="error")
    host.ui_info = info
    host.ui_warn = warn
    host.ui_error = err


def _hex_norm(x: str) -> str:
    try:
        x = (x or "").strip()
        if not x: return "#000000"
        if x[0] != "#": x = "#" + x
        if len(x) == 4:  # #abc → #aabbcc
            x = "#" + "".join(ch*2 for ch in x[1:])
        return x[:7]
    except Exception:
        return "#000000"

def _wcag_ratio(c1: str, c2: str) -> float:
    def _lumin(hex6: str) -> float:
        hex6 = hex6.lstrip("#")
        r = int(hex6[0:2],16)/255.0; g=int(hex6[2:4],16)/255.0; b=int(hex6[4:6],16)/255.0
        def _lin(u): return u/12.92 if u<=0.03928 else ((u+0.055)/1.055)**2.4
        R,G,B = _lin(r),_lin(g),_lin(b)
        return 0.2126*R + 0.7152*G + 0.0722*B
    L1, L2 = sorted([_lumin(_hex_norm(c1)), _lumin(_hex_norm(c2))], reverse=True)
    return (L1 + 0.05) / (L2 + 0.05)

def _ratio_badge(r: float) -> str:
    return "AAA" if r >= 7 else ("AA" if r >= 4.5 else ("A" if r >= 3 else "LOW"))


# Theme Lab: edits happen on a non-destructive draft (nothing touches
# theme_var/settings/disk until Apply/Save As); the full-app retheme is
# debounced to ~6/s to avoid the old per-slider-tick choppiness; the preview
# uses a temporary pseudo-theme removed on close.

_THEMELAB_KEYS = ("APP_BG", "CARD_BG", "FG", "FG_SUB",
                  "ACCENT", "ACCENT_2", "ERROR", "WARN", "TITLE")

# Which background each colour is judged against for WCAG contrast.
_CONTRAST_REF = {"FG": "APP_BG", "FG_SUB": "CARD_BG", "TITLE": "APP_BG",
                 "ACCENT": "APP_BG", "ACCENT_2": "APP_BG",
                 "ERROR": "CARD_BG", "WARN": "CARD_BG"}

_KEY_LABELS = {"APP_BG": "Background", "CARD_BG": "Cards", "FG": "Text",
               "FG_SUB": "Muted text", "ACCENT": "Accent", "ACCENT_2": "Accent 2",
               "ERROR": "Error", "WARN": "Warning", "TITLE": "Title"}


def _lumin(hex6: str) -> float:
    hex6 = _hex_norm(hex6).lstrip("#")
    r = int(hex6[0:2], 16) / 255.0
    g = int(hex6[2:4], 16) / 255.0
    b = int(hex6[4:6], 16) / 255.0
    def _lin(u): return u / 12.92 if u <= 0.03928 else ((u + 0.055) / 1.055) ** 2.4
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _is_light_hex(hex6: str) -> bool:
    return _lumin(hex6) > 0.45


def _hsl(hex_color: str, h_delta=0.0, s_mul=1.0, l_mul=1.0, l_set=None) -> str:
    hex_color = _hex_norm(hex_color).lstrip("#")
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + h_delta) % 1.0
    s = max(0.0, min(1.0, s * s_mul))
    l = max(0.0, min(1.0, (l_set if l_set is not None else l * l_mul)))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"


def derive_palette(accent: str, app_bg: str) -> dict:
    """
    Generate a complete, contrast-safe 9-colour theme from just an accent and a
    background. Pure and deterministic (testable): the card sits a step off the
    background, text colours are pushed until they clear WCAG AA against their
    reference, the second accent is the hue-rotated complement, and error/warn
    stay in their conventional hue families adjusted for the mode.
    """
    accent = _hex_norm(accent)
    app_bg = _hex_norm(app_bg)
    light = _is_light_hex(app_bg)

    card = _hsl(app_bg, l_mul=(0.94 if light else 1.35))
    if card.lower() == app_bg.lower():
        card = _hsl(app_bg, l_set=(0.90 if light else 0.14))

    def _readable(base: str, against: str, target: float, prefer_dark: bool) -> str:
        c = base
        for _ in range(24):
            if _wcag_ratio(c, against) >= target:
                return c
            c = _hsl(c, l_mul=(0.92 if prefer_dark else 1.10))
            if c in ("#000000", "#ffffff"):
                break
        return c

    fg = _readable(_hsl(accent, s_mul=0.10, l_set=(0.12 if light else 0.90)),
                   app_bg, 7.0, prefer_dark=light)
    fg_sub = _readable(_hsl(fg, l_mul=(1.45 if light else 0.72), s_mul=0.6),
                       card, 4.5, prefer_dark=light)
    accent2 = _hsl(accent, h_delta=0.42, s_mul=0.95)
    title = _readable(_hsl(accent, l_mul=(0.75 if light else 1.35), s_mul=0.8),
                      app_bg, 4.5, prefer_dark=light)
    error = _readable("#c62828" if light else "#ff6b6b", card, 3.0, prefer_dark=light)
    warn = _readable("#b46913" if light else "#ffb020", card, 3.0, prefer_dark=light)

    return {"APP_BG": app_bg, "CARD_BG": card, "FG": fg, "FG_SUB": fg_sub,
            "ACCENT": accent, "ACCENT_2": accent2, "ERROR": error,
            "WARN": warn, "TITLE": title}


def _build_wheel_image(d: int = 220):
    """HSV colour wheel as a PIL image, vectorised with numpy (the old lab
    built it pixel-by-pixel in Python and froze the UI for seconds)."""
    import numpy as np
    from PIL import Image
    yy, xx = np.mgrid[0:d, 0:d].astype(np.float32)
    c = (d - 1) / 2.0
    dx, dy = xx - c, yy - c
    r = np.sqrt(dx * dx + dy * dy)
    R = d / 2.0 - 2.0
    h = (np.degrees(np.arctan2(dy, dx)) % 360.0) / 360.0
    s = np.clip(r / R, 0.0, 1.0)
    v = np.ones_like(s)
    i = (np.floor(h * 6.0).astype(np.int32)) % 6
    f = h * 6.0 - np.floor(h * 6.0)
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    rc = np.choose(i, [v, q, p, p, t, v])
    gc = np.choose(i, [t, v, v, q, p, p])
    bc = np.choose(i, [p, p, t, v, v, q])
    rgb = (np.stack([rc, gc, bc], axis=-1) * 255).astype(np.uint8)
    rgb[r > R] = (40, 42, 48)
    return Image.fromarray(rgb, "RGB")


class ThemeLab(Toplevel):
    PREVIEW_KEY = "__themelab_preview__"

    def __init__(self, host):
        super().__init__(host.root)
        self.host = host
        self.style: ttk.Style = host.style
        self.title("Theme Lab")
        self.resizable(True, True)
        self.transient(host.root)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # The lab paints its own swatches/preview; the app-wide retheme walk
        # must not descend into this window and stomp them (swatches went
        # black on every debounced preview push before this flag existed).
        self._bc_no_retint = True

        try:
            self._mod = import_module(host.__module__)
        except Exception:
            self._mod = None
        self._themes = getattr(host, "THEMES", getattr(self._mod, "THEMES", {}))

        # --- entry snapshot: everything Close-without-Apply must restore ----
        self._entry_theme = self._current_theme_name()
        self._entry_font = self._font_snapshot()
        self._applied = False
        self._syncing = False          # guards var traces during bulk refresh
        self._preview_after = None
        self._font_after = None

        base = dict(self._themes.get(self._entry_theme)
                    or next(iter(self._themes.values()), {}))
        self.draft = {k: _hex_norm(base.get(k, "#000000")) for k in _THEMELAB_KEYS}
        self.draft["_PADDING_SCALE"] = float(base.get("_PADDING_SCALE", 1.00))
        self.draft["_BORDER_WIDTH"] = int(base.get("_BORDER_WIDTH", 1))
        self._adjust_base = {k: self.draft[k] for k in _THEMELAB_KEYS}

        # --- layout: left notebook / right always-visible preview ------------
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        nb = ttk.Notebook(body)
        nb.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        tab_colors = ttk.Frame(nb); nb.add(tab_colors, text="  Colors  ")
        tab_adjust = ttk.Frame(nb); nb.add(tab_adjust, text="  Adjust  ")
        tab_more = ttk.Frame(nb); nb.add(tab_more, text="  Font & Layout  ")

        self._build_colors_tab(tab_colors)
        self._build_adjust_tab(tab_adjust)
        self._build_more_tab(tab_more)

        prev_wrap = ttk.LabelFrame(body, text="Live preview", style="Card.TLabelframe")
        prev_wrap.grid(row=0, column=1, sticky="nsew")
        self._build_preview_panel(prev_wrap)

        self._contrast_lbl = ttk.Label(self, text="", style="Sub.TLabel", anchor="w")
        self._contrast_lbl.pack(fill="x", padx=12, pady=(6, 0))

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=10)
        ttk.Label(bar, text="Start from:").pack(side="left")
        self._start_var = tk.StringVar(value=self._entry_theme)
        names = [n for n in self._themes.keys() if not n.startswith("__")]
        cb = ttk.Combobox(bar, values=names, textvariable=self._start_var,
                          state="readonly", width=14)
        cb.pack(side="left", padx=(4, 8))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._start_from(self._start_var.get()))
        ttk.Button(bar, text="Generate from Accent+BG", style="Ghost.TButton",
                   command=self._generate).pack(side="left", padx=4)
        ttk.Button(bar, text="Import...", style="Ghost.TButton",
                   command=self._import_json).pack(side="left", padx=4)
        ttk.Button(bar, text="Export...", style="Ghost.TButton",
                   command=self._export_json).pack(side="left", padx=4)

        ttk.Button(bar, text="Close", command=self._on_close).pack(side="right")
        ttk.Button(bar, text="Apply", command=self._apply).pack(side="right", padx=6)
        ttk.Button(bar, text="Save As...", style="Ghost.TButton",
                   command=self._save_named).pack(side="right")
        ttk.Button(bar, text="Revert", style="Ghost.TButton",
                   command=self._revert).pack(side="right", padx=6)

        try:
            self.configure(bg=self.style.lookup(".", "background") or "#14161A")
        except Exception:
            pass
        self._paint_preview()
        self._update_contrast_summary()
        self.geometry("880x560")

    # ------------------------------------------------------------- helpers --
    def _current_theme_name(self) -> str:
        try:
            return self.host.theme_var.get()
        except Exception:
            return "Dark"

    def _font_snapshot(self):
        try:
            f = tkfont.nametofont("TkDefaultFont")
            return (f.cget("family"), int(f.cget("size")))
        except Exception:
            return ("Segoe UI", 10)

    def _settings_dir(self) -> str:
        import os
        d = getattr(self._mod, "USER_SETTINGS_DIR", None)
        if not d:
            d = os.path.join(os.getcwd(), "user_settings")
        return d

    # ------------------------------------------------- draft change plumbing --
    def _draft_changed(self, from_adjust: bool = False):
        """Instant local preview; debounced full-app preview. Direct edits also
        rebase the Adjust sliders so H/S/L always work relative to what you see."""
        if not from_adjust:
            self._adjust_base = {k: self.draft[k] for k in _THEMELAB_KEYS}
            self._reset_adjust_sliders(silent=True)
        self._paint_preview()
        self._update_contrast_summary()
        self._schedule_app_preview()

    def _schedule_app_preview(self, delay_ms: int = 160):
        if self._preview_after is not None:
            try:
                self.after_cancel(self._preview_after)
            except Exception:
                pass
        self._preview_after = self.after(delay_ms, self._push_app_preview)

    def _push_app_preview(self):
        self._preview_after = None
        try:
            self._themes[self.PREVIEW_KEY] = dict(self.draft)
            animated_retheme(self.host, self.PREVIEW_KEY)
        except Exception:
            pass

    def _refresh_vars_from_draft(self):
        self._syncing = True
        try:
            for k in _THEMELAB_KEYS:
                self._vars[k].set(self.draft[k])
        finally:
            self._syncing = False

    # ------------------------------------------------------------ Colors tab --
    def _build_colors_tab(self, parent):
        grid = ttk.Frame(parent)
        grid.pack(fill="both", expand=True, padx=12, pady=12)
        self._vars = {}
        self._swatches = {}
        self._badges = {}
        for r, key in enumerate(_THEMELAB_KEYS):
            self._vars[key] = tk.StringVar(value=self.draft[key])
            ttk.Label(grid, text=_KEY_LABELS.get(key, key), width=11).grid(
                row=r, column=0, sticky="w", padx=(0, 6), pady=3)
            sw = tk.Label(grid, width=4, relief="flat", bd=0,
                          bg=self.draft[key], cursor="hand2")
            sw.grid(row=r, column=1, padx=3, pady=3)
            sw.bind("<Button-1>", lambda _e, k=key: self._pick_color(k))
            self._swatches[key] = sw
            ent = ttk.Entry(grid, textvariable=self._vars[key], width=10,
                            style="Dark.TEntry")
            ent.grid(row=r, column=2, padx=3, pady=3)
            ttk.Button(grid, text="Pick", style="Ghost.TButton", width=5,
                       command=lambda k=key: self._pick_color(k)).grid(
                row=r, column=3, padx=3, pady=3)
            ttk.Button(grid, text="Wheel", style="Ghost.TButton", width=6,
                       command=lambda k=key: self._wheel_pick(k)).grid(
                row=r, column=4, padx=3, pady=3)
            badge = ttk.Label(grid, text="", width=9, style="Sub.TLabel")
            badge.grid(row=r, column=5, padx=(6, 0), pady=3, sticky="w")
            self._badges[key] = badge
            self._vars[key].trace_add(
                "write", lambda *_a, k=key: self._on_color_var(k))
        ttk.Label(parent, style="Sub.TLabel", justify="left",
                  text="Contrast badges are WCAG ratios against each colour's real "
                       "background (AA needs 4.5 for text, 3.0 for UI accents).").pack(
            anchor="w", padx=14, pady=(0, 10))

    def _on_color_var(self, key: str):
        if self._syncing:
            return
        raw = (self._vars[key].get() or "").strip()
        # Accept only complete colours; don't repaint the app on half-typed hex.
        if len(raw.lstrip("#")) not in (3, 6):
            return
        try:
            int(raw.lstrip("#"), 16)
        except Exception:
            return
        self.draft[key] = _hex_norm(raw)
        try:
            self._swatches[key].configure(bg=self.draft[key])
        except Exception:
            pass
        self._draft_changed()

    def _pick_color(self, key: str):
        c0 = self.draft.get(key, "#000000")
        _rgb, hx = tkcolor.askcolor(color=c0, parent=self)
        if hx:
            self._syncing = True
            try:
                self._vars[key].set(_hex_norm(hx))
            finally:
                self._syncing = False
            self.draft[key] = _hex_norm(hx)
            try:
                self._swatches[key].configure(bg=self.draft[key])
            except Exception:
                pass
            self._draft_changed()

    def _wheel_pick(self, key: str):
        try:
            from PIL import ImageTk
        except Exception:
            return self._pick_color(key)
        d = 220
        if getattr(ThemeLab, "_wheel_img", None) is None:
            try:
                ThemeLab._wheel_img = _build_wheel_image(d)
            except Exception:
                return self._pick_color(key)
        top = tk.Toplevel(self)
        top.title(f"Wheel - {_KEY_LABELS.get(key, key)}")
        top.resizable(False, False)
        top.transient(self)
        # PhotoImage must be created per-Toplevel display and kept referenced.
        photo = ImageTk.PhotoImage(ThemeLab._wheel_img)
        top._photo_ref = photo
        c = tk.Canvas(top, width=d, height=d, bd=0, highlightthickness=0)
        c.pack(padx=8, pady=(8, 4))
        c.create_image(0, 0, anchor="nw", image=photo)
        bright = tk.DoubleVar(value=1.0)
        row = ttk.Frame(top); row.pack(fill="x", padx=8)
        ttk.Label(row, text="Darker").pack(side="left")
        ttk.Scale(row, from_=0.25, to=1.0, variable=bright).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Label(row, text="Brighter").pack(side="left")
        cur = tk.Label(top, text=self.draft.get(key, ""), width=20,
                       bg=self.draft.get(key, "#000000"))
        cur.pack(fill="x", padx=8, pady=6)
        picked = {"hex": self.draft.get(key, "#000000")}

        def _from_xy(event):
            x, y = int(event.x), int(event.y)
            if 0 <= x < d and 0 <= y < d:
                try:
                    r, g, b = ThemeLab._wheel_img.getpixel((x, y))
                except Exception:
                    return
                v = float(bright.get())
                hx = f"#{int(r*v):02x}{int(g*v):02x}{int(b*v):02x}"
                picked["hex"] = hx
                try:
                    cur.configure(bg=hx, text=hx,
                                  fg="#000000" if _is_light_hex(hx) else "#ffffff")
                except Exception:
                    pass
        c.bind("<Button-1>", _from_xy)
        c.bind("<B1-Motion>", _from_xy)

        def _ok():
            self._syncing = True
            try:
                self._vars[key].set(picked["hex"])
            finally:
                self._syncing = False
            self.draft[key] = picked["hex"]
            try:
                self._swatches[key].configure(bg=picked["hex"])
            except Exception:
                pass
            self._draft_changed()
            top.destroy()
        btns = ttk.Frame(top); btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Use colour", command=_ok).pack(side="right")
        ttk.Button(btns, text="Cancel", style="Ghost.TButton",
                   command=top.destroy).pack(side="right", padx=6)

    # ------------------------------------------------------------ Adjust tab --
    def _build_adjust_tab(self, parent):
        self.h = tk.DoubleVar(value=0.0)
        self.s = tk.DoubleVar(value=1.0)
        self.l = tk.DoubleVar(value=1.0)
        frm = ttk.Frame(parent)
        frm.pack(fill="x", padx=14, pady=14)
        for label, var, mn, mx in (("Hue shift", self.h, -0.5, 0.5),
                                   ("Saturation x", self.s, 0.4, 1.6),
                                   ("Lightness x", self.l, 0.6, 1.4)):
            row = ttk.Frame(frm); row.pack(fill="x", pady=8)
            ttk.Label(row, text=label, width=12).pack(side="left")
            sc = ttk.Scale(row, from_=mn, to=mx, variable=var,
                           command=lambda _v=None: self._on_adjust())
            sc.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(frm, text="Reset sliders", style="Ghost.TButton",
                   command=self._reset_adjust_sliders).pack(anchor="w", pady=(10, 0))
        ttk.Label(parent, style="Sub.TLabel", justify="left",
                  text="Sliders re-shade every colour of the current draft together.\n"
                       "Editing a colour directly re-bases the sliders on the result.").pack(
            anchor="w", padx=16, pady=(0, 10))

    def _on_adjust(self):
        h, s, l = float(self.h.get()), float(self.s.get()), float(self.l.get())
        for k in _THEMELAB_KEYS:
            self.draft[k] = _hsl(self._adjust_base[k], h_delta=h, s_mul=s, l_mul=l)
        self._refresh_vars_from_draft()
        for k in _THEMELAB_KEYS:
            try:
                self._swatches[k].configure(bg=self.draft[k])
            except Exception:
                pass
        self._draft_changed(from_adjust=True)

    def _reset_adjust_sliders(self, silent: bool = False):
        try:
            self.h.set(0.0); self.s.set(1.0); self.l.set(1.0)
        except Exception:
            pass
        if not silent:
            self._on_adjust()

    # ----------------------------------------------------- Font & Layout tab --
    def _build_more_tab(self, parent):
        f = ttk.LabelFrame(parent, text="Typography", style="Card.TLabelframe")
        f.pack(fill="x", padx=12, pady=(12, 6))
        row = ttk.Frame(f); row.pack(fill="x", padx=10, pady=10)
        ttk.Label(row, text="Font family:").pack(side="left")
        fams = sorted(set(tkfont.families(self)))
        self._fam = tk.StringVar(value=self._entry_font[0])
        cbf = ttk.Combobox(row, values=fams, textvariable=self._fam, width=24,
                           state="readonly", style="Dark.TCombobox")
        cbf.pack(side="left", padx=6)
        cbf.bind("<<ComboboxSelected>>", lambda _e: self._schedule_font_apply())
        ttk.Label(row, text="Size:").pack(side="left", padx=(14, 0))
        self._fsize = tk.IntVar(value=self._entry_font[1])
        sp = ttk.Spinbox(row, from_=8, to=18, textvariable=self._fsize, width=5,
                         command=self._schedule_font_apply)
        sp.pack(side="left", padx=6)
        sp.bind("<Return>", lambda _e: self._schedule_font_apply())
        sp.bind("<FocusOut>", lambda _e: self._schedule_font_apply())

        g = ttk.LabelFrame(parent, text="Layout", style="Card.TLabelframe")
        g.pack(fill="x", padx=12, pady=6)
        row2 = ttk.Frame(g); row2.pack(fill="x", padx=10, pady=10)
        ttk.Label(row2, text="Padding scale:").pack(side="left")
        self._pad = tk.DoubleVar(value=float(self.draft.get("_PADDING_SCALE", 1.0)))
        ttk.Scale(row2, from_=0.8, to=1.4, variable=self._pad,
                  command=lambda _v=None: self._on_layout()).pack(
            side="left", fill="x", expand=True, padx=8)
        ttk.Label(row2, text="Border width:").pack(side="left", padx=(14, 0))
        self._bord = tk.IntVar(value=int(self.draft.get("_BORDER_WIDTH", 1)))
        spb = ttk.Spinbox(row2, from_=0, to=4, textvariable=self._bord, width=4,
                          command=self._on_layout)
        spb.pack(side="left", padx=6)
        ttk.Label(parent, style="Sub.TLabel", justify="left",
                  text="Font changes preview live and are only kept on Apply.\n"
                       "(The old 'corner radius' control did nothing - tk widgets "
                       "cannot round corners - so it is gone.)").pack(
            anchor="w", padx=16, pady=(4, 10))

    def _schedule_font_apply(self, delay_ms: int = 250):
        if self._font_after is not None:
            try:
                self.after_cancel(self._font_after)
            except Exception:
                pass
        self._font_after = self.after(delay_ms, self._apply_font_live)

    def _apply_font_live(self):
        self._font_after = None
        try:
            _set_font_family(self.host.root, force_family=self._fam.get().strip(),
                             force_size=int(self._fsize.get()))
        except Exception:
            pass

    def _on_layout(self):
        try:
            self.draft["_PADDING_SCALE"] = round(float(self._pad.get()), 2)
            self.draft["_BORDER_WIDTH"] = int(self._bord.get())
        except Exception:
            return
        self._schedule_app_preview()

    # ---------------------------------------------------------- preview panel --
    def _build_preview_panel(self, parent):
        self._pv = {}
        outer = tk.Frame(parent, bd=0, highlightthickness=0)
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        self._pv["app"] = outer
        card = tk.Frame(outer, bd=0, highlightthickness=0)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        self._pv["card"] = card
        self._pv["title"] = tk.Label(card, text="BitCrusher", anchor="w",
                                     font=("Segoe UI Semibold", 14))
        self._pv["title"].pack(fill="x", padx=12, pady=(10, 0))
        self._pv["body"] = tk.Label(card, anchor="w", justify="left",
                                    text="Compressing clip.mp4 to 10 MB...")
        self._pv["body"].pack(fill="x", padx=12)
        self._pv["sub"] = tk.Label(card, anchor="w",
                                   text="[Quality] VMAF 94.2 (excellent)")
        self._pv["sub"].pack(fill="x", padx=12)
        self._pv["bar"] = tk.Canvas(card, height=8, bd=0, highlightthickness=0)
        self._pv["bar"].pack(fill="x", padx=12, pady=8)
        row = tk.Frame(card, bd=0)
        self._pv["btnrow"] = row
        row.pack(fill="x", padx=12, pady=(0, 6))
        self._pv["btn"] = tk.Label(row, text="  Start  ", relief="flat")
        self._pv["btn"].pack(side="left")
        self._pv["ghost"] = tk.Label(row, text="  Cancel  ", relief="flat")
        self._pv["ghost"].pack(side="left", padx=6)
        row2 = tk.Frame(card, bd=0)
        self._pv["chiprow"] = row2
        row2.pack(fill="x", padx=12, pady=(0, 12))
        self._pv["err"] = tk.Label(row2, text=" encode failed ")
        self._pv["err"].pack(side="left")
        self._pv["warn"] = tk.Label(row2, text=" near size cap ")
        self._pv["warn"].pack(side="left", padx=6)

    def _paint_preview(self):
        d = self.draft
        try:
            btn_fg = "#101215" if _is_light_hex(d["ACCENT"]) else "#ffffff"
            self._pv["app"].configure(bg=d["APP_BG"])
            for k in ("card", "btnrow", "chiprow"):
                self._pv[k].configure(bg=d["CARD_BG"])
            self._pv["title"].configure(bg=d["CARD_BG"], fg=d["TITLE"])
            self._pv["body"].configure(bg=d["CARD_BG"], fg=d["FG"])
            self._pv["sub"].configure(bg=d["CARD_BG"], fg=d["FG_SUB"])
            bar = self._pv["bar"]
            bar.configure(bg=d["CARD_BG"])
            bar.delete("all")
            bar.create_rectangle(0, 0, 4000, 20, fill=_hsl(d["CARD_BG"], l_mul=0.85),
                                 outline="")
            bar.create_rectangle(0, 0, 260, 20, fill=d["ACCENT"], outline="")
            self._pv["btn"].configure(bg=d["ACCENT"], fg=btn_fg)
            self._pv["ghost"].configure(bg=_hsl(d["CARD_BG"], l_mul=1.12), fg=d["FG"])
            self._pv["err"].configure(bg=d["CARD_BG"], fg=d["ERROR"])
            self._pv["warn"].configure(bg=d["CARD_BG"], fg=d["WARN"])
        except Exception:
            pass

    def _update_contrast_summary(self):
        try:
            parts = []
            worst = 99.0
            for key, ref in (("FG", "APP_BG"), ("FG_SUB", "CARD_BG"),
                             ("TITLE", "APP_BG")):
                r = _wcag_ratio(self.draft[key], self.draft[ref])
                worst = min(worst, r)
                parts.append(f"{_KEY_LABELS[key]} {r:.1f} {_ratio_badge(r)}")
            self._contrast_lbl.configure(
                text="Contrast: " + "   |   ".join(parts)
                     + ("   -  low-contrast text will be hard to read" if worst < 4.5 else ""))
            for key in _THEMELAB_KEYS:
                ref = _CONTRAST_REF.get(key)
                if not ref:
                    self._badges[key].configure(text="")
                    continue
                r = _wcag_ratio(self.draft[key], self.draft[ref])
                self._badges[key].configure(text=f"{r:.1f} {_ratio_badge(r)}")
        except Exception:
            pass

    # ------------------------------------------------------- palette actions --
    def _start_from(self, name: str):
        src = self._themes.get(name)
        if not isinstance(src, dict):
            return
        for k in _THEMELAB_KEYS:
            self.draft[k] = _hex_norm(src.get(k, self.draft[k]))
        self.draft["_PADDING_SCALE"] = float(src.get("_PADDING_SCALE", 1.0))
        self.draft["_BORDER_WIDTH"] = int(src.get("_BORDER_WIDTH", 1))
        try:
            self._pad.set(self.draft["_PADDING_SCALE"])
            self._bord.set(self.draft["_BORDER_WIDTH"])
        except Exception:
            pass
        self._refresh_vars_from_draft()
        for k in _THEMELAB_KEYS:
            try:
                self._swatches[k].configure(bg=self.draft[k])
            except Exception:
                pass
        self._draft_changed()

    def _generate(self):
        pal = derive_palette(self.draft["ACCENT"], self.draft["APP_BG"])
        self.draft.update(pal)
        self._refresh_vars_from_draft()
        for k in _THEMELAB_KEYS:
            try:
                self._swatches[k].configure(bg=self.draft[k])
            except Exception:
                pass
        self._draft_changed()

    def _revert(self):
        self._start_from(self._entry_theme)
        try:
            self._start_var.set(self._entry_theme)
        except Exception:
            pass
        try:
            self._fam.set(self._entry_font[0]); self._fsize.set(self._entry_font[1])
        except Exception:
            pass
        self._apply_font_live()

    # ------------------------------------------------------------ persistence --
    def _theme_file(self, name: str) -> str:
        import os, re
        safe = re.sub(r"[^\w \-]+", "", str(name)).strip() or "Custom"
        d = os.path.join(self._settings_dir(), "themes")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{safe}.json")

    def _commit(self, name: str):
        """Make the draft a real named theme, switch the app to it, persist."""
        import os
        data = dict(self.draft)
        self._themes[name] = data
        try:
            with open(self._theme_file(name), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
        try:
            self.host.settings = getattr(self.host, "settings", {}) or {}
            self.host.settings["ui_font_family"] = self._fam.get().strip()
            self.host.settings["ui_font_size"] = int(self._fsize.get())
        except Exception:
            pass
        try:
            if hasattr(self.host, "_on_theme_select"):
                self.host._on_theme_select(name)   # var + retheme + settings + save
            else:
                self.host.theme_var.set(name)
                animated_retheme(self.host, name)
        except Exception:
            pass
        try:
            if hasattr(self.host, "rebuild_themes_menu"):
                self.host.rebuild_themes_menu()
        except Exception:
            pass
        self._applied = True
        self._entry_theme = name
        self._entry_font = (self._fam.get().strip(), int(self._fsize.get()))
        snack = _get(self.host, "snackbar", _noop)
        try:
            snack(self.host.root, f"Theme '{name}' applied", 1400, "info")
        except Exception:
            pass

    def _apply(self):
        self._commit("Custom")

    def _save_named(self):
        name = tksimple.askstring("Save theme", "Theme name:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name.startswith("__"):
            name = name.lstrip("_") or "Custom"
        self._commit(name)

    def _export_json(self):
        p = fd.asksaveasfilename(defaultextension=".json",
                                 filetypes=[("JSON", "*.json")],
                                 initialfile="MyTheme.json", parent=self)
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(dict(self.draft), f, indent=2)
        except Exception:
            pass

    def _import_json(self):
        p = fd.askopenfilename(filetypes=[("JSON", "*.json")], parent=self)
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not a theme dict")
            for k in _THEMELAB_KEYS:
                if k in data:
                    self.draft[k] = _hex_norm(str(data[k]))
            for k in ("_PADDING_SCALE", "_BORDER_WIDTH"):
                if k in data:
                    self.draft[k] = data[k]
            self._refresh_vars_from_draft()
            for k in _THEMELAB_KEYS:
                try:
                    self._swatches[k].configure(bg=self.draft[k])
                except Exception:
                    pass
            self._draft_changed()
        except Exception:
            snack = _get(self.host, "snackbar", _noop)
            try:
                snack(self.host.root, "Invalid theme file", 1800, "error")
            except Exception:
                pass

    # ----------------------------------------------------------------- close --
    def _on_close(self):
        try:
            if self._preview_after is not None:
                self.after_cancel(self._preview_after)
        except Exception:
            pass
        try:
            self._themes.pop(self.PREVIEW_KEY, None)
        except Exception:
            pass
        if not self._applied:
            # Restore exactly what the user walked in with (theme + fonts).
            try:
                _set_font_family(self.host.root, force_family=self._entry_font[0],
                                 force_size=self._entry_font[1])
            except Exception:
                pass
            try:
                animated_retheme(self.host, self._entry_theme)
            except Exception:
                pass
        else:
            try:
                animated_retheme(self.host, self._entry_theme)
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass


def init_aesthetics(host):

    st = getattr(host, "settings", {}) or {}
    _set_font_family(host.root, force_family=st.get("ui_font_family"), force_size=st.get("ui_font_size"))
    install_button_hover_fx(host.root)
    install_snackbar_hooks(host)
    for attr in ("queue_container", "log_container", "preview_container"):
        if hasattr(host, attr):
            try:
                wrap_as_card(getattr(host, attr))
            except Exception:
                pass


def open_theme_lab(host):
    ThemeLab(host)
