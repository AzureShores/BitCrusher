"""Size a Toplevel to the content it actually laid out.

Hardcoded pixel geometry is why dialogs open cropped: Tk scales fonts and
widget padding with the system DPI, so a 450x300 dialog that fit at 100%
display scaling loses its bottom row at 125% and its Save button at 150%.
The floor sizes passed in here are the old hardcoded numbers, so a window
never opens smaller than it used to - it only grows when its content needs
the room, and it is always clamped to the screen and left resizable.
"""

from __future__ import annotations

import tkinter as tk


def fit_window(win, min_w: int = 0, min_h: int = 0,
               screen_margin: tuple[int, int] = (60, 100),
               resizable: bool = True) -> tuple[int, int]:
    """Grow `win` to fit its laid-out content. Returns the applied (w, h).

    `min_w`/`min_h` are a floor, not a target - pass the size the dialog used
    to hardcode. `screen_margin` is (width, height) kept free so a grown
    window does not slide under the taskbar. Never raises: a dialog that
    fails to measure keeps whatever size it already had.
    """
    try:
        win.update_idletasks()
    except tk.TclError:
        return (0, 0)

    try:
        req_w = int(win.winfo_reqwidth())
        req_h = int(win.winfo_reqheight())
    except (tk.TclError, ValueError):
        req_w = req_h = 0

    w = max(int(min_w or 0), req_w)
    h = max(int(min_h or 0), req_h)
    if w <= 1 or h <= 1:
        return (0, 0)

    try:
        max_w = max(320, int(win.winfo_screenwidth()) - screen_margin[0])
        max_h = max(240, int(win.winfo_screenheight()) - screen_margin[1])
    except (tk.TclError, ValueError):
        max_w, max_h = w, h

    w = min(w, max_w)
    h = min(h, max_h)

    try:
        win.geometry(f"{w}x{h}")
        # minsize floors at the content's own requirement where that fits on
        # screen, so dragging the edge in cannot re-create the cropping.
        win.minsize(min(w, max(1, min(req_w or w, max_w))),
                    min(h, max(1, min(req_h or h, max_h))))
        if resizable:
            win.resizable(True, True)
    except tk.TclError:
        pass

    return (w, h)


def fit_window_later(win, min_w: int = 0, min_h: int = 0, **kw) -> None:
    """Queue fit_window() for after the caller finishes building the dialog.

    Call sites set their geometry before packing anything, so measuring at
    that point would report an empty window. Deferring to the idle queue lets
    the whole builder run first. Purely additive: the dialog still opens at
    whatever geometry the call site asked for, and only grows on the machines
    where that size was actually too small.
    """
    try:
        win.after_idle(lambda: fit_window(win, min_w, min_h, **kw))
    except tk.TclError:
        pass
