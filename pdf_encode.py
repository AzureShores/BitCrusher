from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime

from PIL import Image

from ffmpeg_exec import si, NO_WIN, _sp_run
from media_math import apply_target_size_margin
from text_utils import format_bytes
from webhook import _post_webhook_hardened


def _jsonl_log(event: str, data: dict | None = None):
    try:
        os.makedirs("logs", exist_ok=True)
        path = os.path.join("logs", f"run_{datetime.now().strftime('%Y%m%d')}.jsonl")
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event}
        if data:
            rec.update(data)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None

def compress_pdf(input_path: str, save_path: str, status_callback,
                 target_size_mb: int, webhook_url: str,
                 advanced_options: dict, cancel_callback) -> dict:


    adv = dict(advanced_options or {})
    status_callback(f"Compressing PDF: {input_path}")

    gs = _which("gswin64c", "gswin32c", "gs")
    if not gs:
        status_callback("Ghostscript not found. Install Ghostscript or add it to PATH.", level="ERROR")
        return {}

    try:
        os.makedirs(save_path, exist_ok=True)
    except Exception:
        pass

    src_size = os.path.getsize(input_path)
    target_bytes = max(1, int(target_size_mb * 1024 * 1024))
    target_bytes = apply_target_size_margin(target_bytes)
    tol = float(adv.get("pdf_tolerance", 0.10))            # ±10%
    min_dpi = int(adv.get("pdf_min_dpi", 90))
    max_dpi = int(adv.get("pdf_max_dpi", 300))
    linearize_if_close = bool(adv.get("pdf_linearize_if_close", True))

    filename = os.path.basename(input_path)
    name, _ = os.path.splitext(filename)
    out_prefix = adv.get("output_prefix", "")
    out_suffix = adv.get("output_suffix", "")
    output_file = os.path.join(save_path, f"{out_prefix}{name}{out_suffix}.pdf")

    if src_size <= int(target_bytes * (1.0 + tol)) and linearize_if_close:
        try:
            if os.path.abspath(input_path) != os.path.abspath(output_file):
                shutil.copy2(input_path, output_file)
        except Exception:
            output_file = input_path
        stats = {
            "filename": os.path.basename(output_file),
            "original_size": src_size,
            "compressed_size": os.path.getsize(output_file),
            "ratio": os.path.getsize(output_file) / max(1, src_size),
            "time_taken": 0.0,
            "output_path": output_file,
            "note": "Kept original (already near target).",
        }
        _jsonl_log("encode_end", {"type": "pdf", **stats})
        if webhook_url:
            _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
        status_callback(f"PDF already near target - kept original ({format_bytes(stats['compressed_size'])}).")
        return stats

    def _pdfset_for_dpi(dpi_val: int) -> str:
        if dpi_val <= 120:
            return "/screen"
        if dpi_val <= 180:
            return "/ebook"
        return "/printer"

    def _gs_trial(out_path: str, dpi_val: int) -> tuple[bool, int]:
        pdfset = _pdfset_for_dpi(dpi_val)
        cmd = [
            gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6",
            "-dDetectDuplicateImages=true",
            "-dDownsampleColorImages=true", "-dColorImageDownsampleType=/Bicubic", f"-dColorImageResolution={dpi_val}",
            "-dDownsampleGrayImages=true",  "-dGrayImageDownsampleType=/Bicubic",  f"-dGrayImageResolution={dpi_val}",
            "-dDownsampleMonoImages=true",  "-dMonoImageDownsampleType=/Subsample", "-dMonoImageResolution=300",
            "-dAutoRotatePages=/None", "-dOptimize=true",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-dPDFSETTINGS={pdfset}",
            f"-sOutputFile={out_path}", input_path
        ]
        p = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=si, creationflags=NO_WIN)
        if p.returncode != 0 or (not os.path.exists(out_path)):

            job_log = adv.get("job_log")
            if job_log:
                try:
                    with open(job_log, "a", encoding="utf-8") as lf:
                        lf.write(" ".join(cmd) + "\n")
                        if p.stdout: lf.write(p.stdout + "\n")
                        if p.stderr: lf.write(p.stderr + "\n")
                except Exception:
                    pass
            return False, 0
        return True, os.path.getsize(out_path)

    try:
        with open(input_path, "rb") as f:
            head = f.read(2_000_000)
        looks_text_vector = (b"/Subtype /Image" not in head) and (b"/XObject" not in head)
    except Exception:
        looks_text_vector = False

    if looks_text_vector:
        want_raster_flag = adv.get("pdf_force_rasterize")
        want_raster = True if (want_raster_flag is None) else (str(want_raster_flag).lower() in {"1","true","yes"})
        need_shrink = target_bytes < int(src_size * 0.9)
        if want_raster and need_shrink:
            ok = _rasterize_pdf_to_target(
                input_path=input_path,
                output_file=output_file,
                target_bytes=target_bytes,
                gs=gs,
                status_callback=status_callback,
                adv=adv,
                cancel_callback=cancel_callback,
            )
            if ok:
                took = max(0.0, time.time() - os.path.getmtime(output_file))
                stats = {
                    "filename": os.path.basename(output_file),
                    "original_size": src_size,
                    "compressed_size": os.path.getsize(output_file),
                    "ratio": os.path.getsize(output_file) / max(1, src_size),
                    "time_taken": took,
                    "output_path": output_file,
                    "note": "Rasterized pages to hit target.",
                }
                _jsonl_log("encode_end", {"type": "pdf", **stats})
                if webhook_url:
                    _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
                status_callback(f"PDF rasterized to {format_bytes(stats['compressed_size'])} (target {format_bytes(target_bytes)})")
                return stats

        try:
            if os.path.abspath(input_path) != os.path.abspath(output_file):
                shutil.copy2(input_path, output_file)
        except Exception:
            output_file = input_path
        stats = {
            "filename": os.path.basename(output_file),
            "original_size": src_size,
            "compressed_size": os.path.getsize(output_file),
            "ratio": os.path.getsize(output_file) / max(1, src_size),
            "time_taken": 0.0,
            "output_path": output_file,
            "note": "Kept original: vector/text-only; rasterization disabled/unavailable.",
        }
        _jsonl_log("encode_end", {"type": "pdf", **stats})
        if webhook_url:
            _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
        status_callback("PDF appears text/vector-only - kept original to avoid size increase.")
        return stats

    probe_tmp = output_file + ".probe.pdf"
    trial_tmp = output_file + ".trial.pdf"
    for pth in (probe_tmp, trial_tmp):
        if os.path.exists(pth):
            try: os.remove(pth)
            except Exception: pass

    status_callback("Probing PDF @ 300 DPI (/printer)")
    ok_probe, probe_size = _gs_trial(probe_tmp, 300)
    if not ok_probe:
        status_callback("PDF probe failed; aborting PDF compression.", level="ERROR")
        return {}

    best_path = None
    best_size = None
    if probe_size < src_size and probe_size <= target_bytes:
        best_path = probe_tmp
        best_size = probe_size

    low, high = min_dpi, max_dpi
    TRIALS_MAX = 8

    for _ in range(TRIALS_MAX):
        if cancel_callback():
            status_callback("PDF compression cancelled.", level="WARNING")
            for pth in (probe_tmp, trial_tmp):
                try:
                    if os.path.exists(pth): os.remove(pth)
                except Exception: pass
            return {}

        mid = (low + high) // 2

        if 280 <= mid <= 320:
            ok, size = True, probe_size
            cur_path = probe_tmp
        else:
            if os.path.exists(trial_tmp):
                try: os.remove(trial_tmp)
                except Exception: pass
            status_callback(f"PDF trial @ {mid} DPI ({_pdfset_for_dpi(mid)})")
            ok, size = _gs_trial(trial_tmp, mid)
            cur_path = trial_tmp
            if not ok:
                high = max(min_dpi, mid - 10)
                continue

        if size >= src_size:
            high = max(min_dpi, mid - 10)
            continue

        if size <= target_bytes and (best_size is None or size < best_size):
            best_path, best_size = cur_path, size

        if target_bytes * (1.0 - tol) <= size <= target_bytes:
            best_path, best_size = cur_path, size
            break

        if size > target_bytes:
            high = max(min_dpi, mid - 10)
        else:
            low = min(max_dpi, mid + 10)

    if best_path is None:

        if probe_size < src_size:
            best_path, best_size = probe_tmp, probe_size
        else:
            try:
                if os.path.abspath(input_path) != os.path.abspath(output_file):
                    shutil.copy2(input_path, output_file)
            except Exception:
                output_file = input_path
            stats = {
                "filename": os.path.basename(output_file),
                "original_size": src_size,
                "compressed_size": os.path.getsize(output_file),
                "ratio": os.path.getsize(output_file) / max(1, src_size),
                "time_taken": 0.0,
                "output_path": output_file,
                "note": "Kept original: no beneficial recompress result.",
            }
            _jsonl_log("encode_end", {"type": "pdf", **stats})
            if webhook_url:
                _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
            status_callback("No beneficial PDF compression found - kept original.")
            for pth in (probe_tmp, trial_tmp):
                try:
                    if os.path.exists(pth): os.remove(pth)
                except Exception: pass
            return stats

    try:
        if os.path.exists(output_file):
            os.remove(output_file)
    except Exception:
        pass
    os.replace(best_path, output_file)

    for pth in (probe_tmp, trial_tmp):
        if pth != output_file:
            try:
                if os.path.exists(pth): os.remove(pth)
            except Exception:
                pass

    took = max(0.0, time.time() - os.path.getmtime(output_file))
    stats = {
        "filename": os.path.basename(output_file),
        "original_size": src_size,
        "compressed_size": os.path.getsize(output_file),
        "ratio": os.path.getsize(output_file) / max(1, src_size),
        "time_taken": took,
        "output_path": output_file,
    }
    _jsonl_log("encode_end", {"type": "pdf", **stats})
    if webhook_url:
        _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
    status_callback(f"PDF compressed to {format_bytes(stats['compressed_size'])} (target {format_bytes(target_bytes)})")
    return stats



def _rasterize_pdf_to_target(input_path: str, output_file: str, target_bytes: int,
                             gs: str, status_callback, adv: dict, cancel_callback) -> bool:

    import tempfile, glob
    tmpdir = tempfile.mkdtemp(prefix="bc_pdf_rast_")
    try:
        dpi_min = int(adv.get("pdf_raster_min_dpi", 110))
        dpi_max = int(adv.get("pdf_raster_max_dpi", 220))
        q_min   = int(adv.get("pdf_raster_min_q", 60))
        q_max   = int(adv.get("pdf_raster_max_q", 90))
        max_iters = int(adv.get("pdf_raster_max_iters", 7))

        best_pdf = None
        best_size = None

        for _ in range(max_iters):
            if cancel_callback():
                status_callback("PDF rasterization cancelled.", level="WARNING")
                return False

            dpi = (dpi_min + dpi_max) // 2
            q   = (q_min + q_max) // 2

            img_pattern = os.path.join(tmpdir, "page-%06d.jpg")
            for f in glob.glob(os.path.join(tmpdir, "page-*.jpg")):
                try: os.remove(f)
                except Exception: pass
            status_callback(f"Raster trial DPI={dpi}, Q={q}")
            cmd = [gs, "-dNOPAUSE", "-dQUIET", "-dBATCH", "-sDEVICE=jpeg", f"-r{dpi}", f"-dJPEGQ={q}", "-o", img_pattern, input_path]
            p = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=si, creationflags=NO_WIN)
            if p.returncode != 0:
                status_callback("Ghostscript rasterization failed.", level="ERROR")
                return False

            jpgs = sorted(glob.glob(os.path.join(tmpdir, "page-*.jpg")))
            if not jpgs:
                status_callback("No pages rendered during rasterization.", level="ERROR")
                return False

            images = []
            for j in jpgs:
                im = Image.open(j)
                if im.mode != "RGB":
                    im = im.convert("RGB")
                images.append(im.copy())
                im.close()

            tmp_pdf = output_file + ".tmp.pdf"
            try:
                if os.path.exists(tmp_pdf):
                    os.remove(tmp_pdf)
            except Exception:
                pass
            if len(images) == 1:
                images[0].save(tmp_pdf, "PDF", resolution=72.0, save_all=False)
            else:
                images[0].save(tmp_pdf, "PDF", resolution=72.0, save_all=True, append_images=images[1:])
            for im in images:
                try: im.close()
                except Exception: pass

            size = os.path.getsize(tmp_pdf)
            status_callback(f"Raster result -> {format_bytes(size)} (target {format_bytes(target_bytes)})")

            if size <= target_bytes and (best_size is None or size < best_size):
                best_pdf, best_size = tmp_pdf, size
                dpi_min = min(dpi_max, dpi + 10)
                q_min   = min(q_max, q + 5)
            else:
                dpi_max = max(dpi_min, dpi - 10)
                q_max   = max(q_min, q - 5)

        if best_pdf and os.path.exists(best_pdf):
            try:
                if os.path.exists(output_file):
                    os.remove(output_file)
            except Exception:
                pass
            os.replace(best_pdf, output_file)
            return True
        return False
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
