

from __future__ import annotations
import os, subprocess, tempfile, math, shutil
from PIL import Image, ImageTk
import numpy as np

FFMPEG  = os.environ.get("FFMPEG", "ffmpeg")

def _grab_frame(path: str, t: float, scale_w: int = 640) -> Image.Image | None:
    tmpdir = tempfile.mkdtemp(prefix="bc_cmp_")
    try:
        out = os.path.join(tmpdir, "f.jpg")

        cmd = [FFMPEG, "-y", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={scale_w}:-2", out]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(out):
            return Image.open(out).convert("RGB")
        return None
    finally:
        try: shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception: pass

def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2)
    if mse <= 1e-9: return 100.0
    return 20.0 * math.log10(255.0 / math.sqrt(mse))

def _ssim(a: np.ndarray, b: np.ndarray) -> float:

    a = a.astype(np.float32); b = b.astype(np.float32)
    mu_a, mu_b = a.mean(), b.mean()
    sigma_a, sigma_b = a.var(), b.var()
    sigma_ab = ((a - mu_a) * (b - mu_b)).mean()
    L = 255.0; c1 = (0.01*L)**2; c2 = (0.03*L)**2
    num = (2*mu_a*mu_b + c1) * (2*sigma_ab + c2)
    den = (mu_a**2 + mu_b**2 + c1) * (sigma_a + sigma_b + c2)
    if den == 0: return 1.0
    return float(max(0.0, min(1.0, num/den)))

def open_compare_viewer(root, original_path: str, compressed_path: str, duration_hint: float | None = None) -> None:
    import tkinter as tk
    from tkinter import ttk

    win = tk.Toplevel(root); win.title("Compare: Original vs. Output")
    win.geometry("1320x520"); win.transient(root)
    main = ttk.Frame(win, padding=10); main.pack(fill="both", expand=True)

    left = tk.Label(main); left.grid(row=0, column=0, padx=(0,6))
    right = tk.Label(main); right.grid(row=0, column=1, padx=(6,0))

    ctl = ttk.Frame(main); ctl.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8,0))
    tvar = tk.DoubleVar(value=0.0)
    info = ttk.Label(ctl, text="Ready"); info.pack(side="left")
    sld  = ttk.Scale(ctl, from_=0.0, to=max(0.1, (duration_hint or 60.0)), variable=tvar, orient="horizontal", length=900)
    sld.pack(side="right", fill="x", expand=True)

    def _refresh(*_):
        t = float(tvar.get())
        o = _grab_frame(original_path, t)
        c = _grab_frame(compressed_path, t)
        if o is None or c is None:
            info.config(text="No frame @ {:.2f}s".format(t)); return

        og = np.asarray(o.convert("L")); cg = np.asarray(c.convert("L"))
        ps = _psnr(og, cg); ss = _ssim(og, cg)
        info.config(text=f"t={t:.2f}s | PSNR={ps:.2f} dB | SSIM={ss:.4f}")

        left.img  = ImageTk.PhotoImage(o)
        right.img = ImageTk.PhotoImage(c)
        left.config(image=left.img)
        right.config(image=right.img)

    sld.bind("<ButtonRelease-1>", _refresh)
    sld.bind("<B1-Motion>", _refresh)
    win.after(200, _refresh)
    try: win.attributes("-topmost", True); win.after(300, lambda: win.attributes("-topmost", False))
    except Exception: pass
