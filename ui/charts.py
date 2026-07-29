"""Framework-free chart geometry, plus one thin canvas renderer.

Same split learning/dashboard.py already proves: the functions that decide
WHERE things go import nothing and return plain coordinates, so they are
unit-testable and can be rendered by Tk today or by PIL later. `draw_on_canvas`
is the only part that touches a widget, and it does so through the canvas
object it is handed - this module imports no tkinter at all.

A spec is:
    {"kind": str, "width": float, "height": float, "shapes": [shape, ...]}
and a shape is:
    {"type": "rect"|"line"|"text", "coords": [...], "role": str, ...}

`role` names what a shape MEANS ("bar", "label", "good"), never what colour it
is - the caller maps roles to theme colours so charts retheme for free.
"""
from __future__ import annotations

# Bands for the quality strip, matching learning/dashboard.py's thresholds so
# the two never disagree about what counts as a good scene.
STRIP_GOOD = 95.0
STRIP_OK = 85.0


def _f(x, default=0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if v == v else default  # NaN -> default


def _clean(values) -> list[float]:
    return [_f(v) for v in (values or [])]


def bars(values, width: float, height: float, *, labels=None,
         horizontal: bool = False, gap: float = 2.0, max_value=None,
         min_extent: float = 1.0) -> dict:
    """Bar geometry for `values` inside a width x height box.

    Bars are scaled against `max_value` (default: the largest value), so
    several charts can share a scale by passing the same number. Negative
    values are clamped to zero - these charts show magnitudes, and a bar
    growing the wrong way is worse than no bar.

    `min_extent` keeps a tiny-but-nonzero value visible as a sliver rather
    than rounding it away to nothing.
    """
    vals = _clean(values)
    w, h = max(1.0, _f(width)), max(1.0, _f(height))
    if not vals:
        return {"kind": "bars", "width": w, "height": h, "shapes": []}
    vals = [v if v > 0 else 0.0 for v in vals]
    top = _f(max_value, 0.0) or max(vals)
    if top <= 0:
        top = 1.0
    n = len(vals)
    g = max(0.0, _f(gap))
    span = (h if horizontal else w)
    slot = max(1.0, (span - g * (n - 1)) / n)
    depth = (w if horizontal else h)

    shapes = []
    for i, v in enumerate(vals):
        frac = min(1.0, v / top)
        extent = max(min_extent if v > 0 else 0.0, frac * depth)
        off = i * (slot + g)
        if horizontal:
            coords = [0.0, off, extent, off + slot]
        else:
            coords = [off, h - extent, off + slot, h]
        shape = {"type": "rect", "coords": [round(c, 2) for c in coords],
                 "role": "bar", "value": v, "frac": round(frac, 4), "index": i}
        if labels is not None and i < len(labels):
            shape["label"] = str(labels[i])
        shapes.append(shape)
    return {"kind": "bars", "width": w, "height": h, "shapes": shapes}


def sparkline(series, width: float, height: float, *, y_min=None,
              y_max=None) -> dict:
    """A single polyline through `series`, as one 'line' shape per segment.

    A flat series is drawn along the middle rather than collapsed onto an
    edge, so "constant" reads differently from "bottomed out".
    """
    vals = _clean(series)
    w, h = max(1.0, _f(width)), max(1.0, _f(height))
    if len(vals) < 2:
        return {"kind": "sparkline", "width": w, "height": h, "shapes": []}
    lo = _f(y_min, min(vals)) if y_min is not None else min(vals)
    hi = _f(y_max, max(vals)) if y_max is not None else max(vals)
    if hi <= lo:
        pts = [(i * w / (len(vals) - 1), h / 2.0) for i in range(len(vals))]
    else:
        pts = [(i * w / (len(vals) - 1), h - (v - lo) / (hi - lo) * h)
               for i, v in enumerate(vals)]
    shapes = [{"type": "line",
               "coords": [round(pts[i][0], 2), round(pts[i][1], 2),
                          round(pts[i + 1][0], 2), round(pts[i + 1][1], 2)],
               "role": "line"}
              for i in range(len(pts) - 1)]
    return {"kind": "sparkline", "width": w, "height": h, "shapes": shapes}


def strip(values, width: float, height: float, *, weights=None) -> dict:
    """A left-to-right quality strip: one band per value, coloured by role
    ('good' / 'ok' / 'poor'). `weights` gives bands proportional widths (scene
    durations, say) instead of equal ones."""
    vals = _clean(values)
    w, h = max(1.0, _f(width)), max(1.0, _f(height))
    if not vals:
        return {"kind": "strip", "width": w, "height": h, "shapes": []}
    ws = _clean(weights) if weights is not None else []
    if len(ws) != len(vals) or sum(x for x in ws if x > 0) <= 0:
        ws = [1.0] * len(vals)
    ws = [x if x > 0 else 0.0 for x in ws]
    total = sum(ws) or 1.0
    shapes = []
    x = 0.0
    for i, (v, wt) in enumerate(zip(vals, ws)):
        span = wt / total * w
        role = "good" if v >= STRIP_GOOD else ("ok" if v >= STRIP_OK else "poor")
        shapes.append({"type": "rect",
                       "coords": [round(x, 2), 0.0, round(min(w, x + span), 2), h],
                       "role": role, "value": v, "index": i})
        x += span
    if shapes:  # absorb rounding so the strip always fills its box
        shapes[-1]["coords"][2] = w
    return {"kind": "strip", "width": w, "height": h, "shapes": shapes}


def draw_on_canvas(canvas, spec, colors=None, *, default: str = "#888888",
                   text_fill: str | None = None, font=None) -> int:
    """Render a spec onto a Tk canvas. Returns the number of items drawn.

    The only widget-aware function here, and it still imports nothing: the
    canvas arrives from the caller. `colors` maps role -> colour; unmapped
    roles fall back to `default`, so a new role degrades to a visible shape
    rather than an exception.
    """
    if canvas is None or not isinstance(spec, dict):
        return 0
    cmap = dict(colors or {})
    drawn = 0
    for sh in (spec.get("shapes") or []):
        role = str(sh.get("role") or "")
        col = cmap.get(role, default)
        c = sh.get("coords") or []
        try:
            if sh.get("type") == "rect" and len(c) >= 4:
                canvas.create_rectangle(c[0], c[1], c[2], c[3], fill=col, outline="")
            elif sh.get("type") == "line" and len(c) >= 4:
                canvas.create_line(c[0], c[1], c[2], c[3], fill=col,
                                   width=sh.get("width", 1))
            elif sh.get("type") == "text" and len(c) >= 2:
                kw = {"text": str(sh.get("text") or ""),
                      "fill": (text_fill or col),
                      "anchor": sh.get("anchor", "nw")}
                if font is not None:
                    kw["font"] = font
                canvas.create_text(c[0], c[1], **kw)
            else:
                continue
        except Exception:
            continue
        drawn += 1
    return drawn
