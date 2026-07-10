"""
shutter_method.py — a faithful replica of Shutter Encoder's DOCUMENTED
"maximum file size" compression method, for a fair max-vs-max benchmark.

Shutter Encoder (ffmpeg-based) hits a target size by computing a single average
bitrate from the target and duration, then running a 2-pass encode:

    total_kbps = target_MB * 8192 / duration_s        # 8192 = 8 * 1024 (MiB->kbit)
    video_kbps = round(total_kbps) - audio_kbps       # reserve audio
    -> 2-pass libx264 (their default) / libx265 (their high-efficiency option)

Notes / assumptions (Shutter's docs don't publish the literal formula, so these
are the standard, well-known values and are stated openly for honesty):
  * No container-overhead reservation — this is exactly why Shutter's own docs
    warn it "cannot guarantee a perfect size" (it can slightly overshoot).
  * Two-pass is Shutter's documented default at these bitrates (<20 000 kbps).
  * Default video audio = AAC 192 kbps, subtracted from the total.
  * Audio-only files: bitrate = round(target_MB * 8192 / duration), single pass.
This is the NAIVE baseline on purpose: no codec race, no VMAF measurement, no
passthrough, no smart-source guard — the things BitCrusher adds.
"""
import os, subprocess, json, time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(BASE, "tools", "ffmpeg.exe")
FFPROBE = os.path.join(BASE, "tools", "ffprobe.exe")
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"
if not os.path.exists(FFPROBE):
    FFPROBE = "ffprobe"

SHUTTER_AUDIO_KBPS = 192  # default AAC reserve for video


def _duration(path):
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def shutter_encode_video(input_path, output_path, target_mb, codec="libx264"):
    """Replicate Shutter's file-size method: compute bitrate, 2-pass encode."""
    dur = _duration(input_path)
    if dur <= 0:
        return {"ok": False, "error": "no duration"}
    total_kbps = target_mb * 8192.0 / dur
    video_kbps = int(round(total_kbps)) - SHUTTER_AUDIO_KBPS
    video_kbps = max(video_kbps, 100)
    passlog = output_path + ".shutterpass"
    t0 = time.time()
    common = [FFMPEG, "-y", "-i", input_path, "-c:v", codec, "-b:v", f"{video_kbps}k",
              "-pix_fmt", "yuv420p", "-passlogfile", passlog]
    try:
        p1 = subprocess.run(common + ["-pass", "1", "-an", "-f", "mp4", os.devnull],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        p2 = subprocess.run(common + ["-pass", "2", "-c:a", "aac", "-b:a", f"{SHUTTER_AUDIO_KBPS}k",
                                      output_path],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    finally:
        for ext in ("", "-0.log", "-0.log.mbtree", ".log", ".log.mbtree"):
            try:
                os.remove(passlog + ext)
            except Exception:
                pass
    if p2.returncode != 0 or not os.path.exists(output_path):
        return {"ok": False, "error": (p2.stderr or "")[-160:], "video_kbps": video_kbps}
    return {"ok": True, "out_bytes": os.path.getsize(output_path),
            "video_kbps": video_kbps, "secs": round(time.time() - t0, 1)}


def shutter_encode_audio(input_path, output_path, target_mb, codec="aac"):
    """Shutter audio-to-size: bitrate = target*8192/duration, single-pass encode."""
    dur = _duration(input_path)
    if dur <= 0:
        return {"ok": False, "error": "no duration"}
    kbps = max(32, int(round(target_mb * 8192.0 / dur)))
    t0 = time.time()
    try:
        p = subprocess.run([FFMPEG, "-y", "-i", input_path, "-vn", "-c:a", codec,
                            "-b:a", f"{kbps}k", output_path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    if p.returncode != 0 or not os.path.exists(output_path):
        return {"ok": False, "error": (p.stderr or "")[-160:], "kbps": kbps}
    return {"ok": True, "out_bytes": os.path.getsize(output_path),
            "kbps": kbps, "secs": round(time.time() - t0, 1)}


def measure_vmaf(reference, distorted):
    try:
        r = subprocess.run([FFMPEG, "-i", distorted, "-i", reference,
                            "-lavfi", "[0:v][1:v]libvmaf", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)
        import re
        m = re.search(r"VMAF score:\s*([0-9.]+)", r.stderr)
        return float(m.group(1)) if m else None
    except Exception:
        return None
