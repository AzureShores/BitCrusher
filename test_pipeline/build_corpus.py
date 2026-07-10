"""
build_corpus.py — assemble a diverse, legal media test corpus for BitCrusher.

Populates test_pipeline/corpus/<category>/ from three sources:
  1. Downloaded free/CC/public-domain clips (Blender open movies, Internet
     Archive PD cartoons + old live-action, Wikimedia) and the Kodak still set.
  2. Synthetic ffmpeg "torture" clips (banding gradients, high-motion fractal,
     sharp-text/pattern) — 100% reproducible, no network.
  3. A stratified sample of the user's own local media (C:\\Users\\damon\\Videos
     for real-world video, D:\\MUSIC for audio — READ ONLY, never modified).

Big downloads (Tears of Steel = 372 MB) are never fetched whole: ffmpeg pulls a
short representative segment straight over HTTP via range requests (stream-copy,
no re-encode, no quality loss).

Writes test_pipeline/corpus/manifest.json describing every item + its category.
Idempotent: existing/materialized items are skipped, so it's safe to re-run.
"""
import json, os, subprocess, sys, time, random, urllib.request, socket

ROOT = os.path.dirname(os.path.abspath(__file__))
# Corpus lives on D: by default (C: is nearly full); BC_CORPUS_DIR overrides.
CORPUS = (os.environ.get("BC_CORPUS_DIR")
          or (r"D:\BitCrusherTraining\corpus" if os.path.isdir(r"D:/")
              else os.path.join(ROOT, "corpus")))
UA = "Mozilla/5.0"
socket.setdefaulttimeout(20)

FFMPEG = os.path.join(os.path.dirname(ROOT), "tools", "ffmpeg.exe")
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"

VIDEOS_DIR = r"C:\Users\damon\Videos"
MUSIC_DIR = r"D:\MUSIC"

VID_EXT = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")
AUD_EXT = (".flac", ".mp3", ".m4a", ".opus", ".wav", ".ogg", ".aac")


def log(msg):
    print(f"[corpus] {msg}", flush=True)


def _download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 1024:
        return True
    tmp = dest + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, dest)
        return True
    except Exception as e:
        log(f"download FAILED {os.path.basename(dest)}: {e}")
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False


def _decodable(path):
    """True only if ffmpeg can decode every frame (container-valid isn't enough:
    a mid-GOP stream-copy passes ffprobe but has no leading keyframe to decode)."""
    r = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0 and not r.stderr.strip()


def _segment(url, dest, ss, dur):
    """Extract a short, GUARANTEED-decodable segment from an HTTP source.

    Re-encodes (accurate seek, clean keyframes, drops stray data/timecode
    streams) rather than stream-copying — a `-ss ... -c copy` cut starts mid-GOP
    and yields an undecodable file that no encoder can open. Cost is small
    (veryfast, ~seconds) and the result is a clean, fair test source.
    """
    if os.path.exists(dest) and os.path.getsize(dest) > 1024 and _decodable(dest):
        return True
    tmp = dest + ".part.mp4"
    cmd = [FFMPEG, "-y", "-user_agent", UA, "-ss", str(ss), "-i", url, "-t", str(dur),
           "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-crf", "16",
           "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
           "-map_metadata", "-1", tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=360)
        if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1024 and _decodable(tmp):
            os.replace(tmp, dest)
            return True
        log(f"segment FAILED {os.path.basename(dest)}: {(r.stderr or '')[-200:]}")
    except Exception as e:
        log(f"segment ERROR {os.path.basename(dest)}: {e}")
    try:
        os.remove(tmp)
    except Exception:
        pass
    return False


def _synth(dest, lavfi, dur, extra=None):
    if os.path.exists(dest) and os.path.getsize(dest) > 1024:
        return True
    tmp = dest + ".part.mp4"
    cmd = [FFMPEG, "-y", "-f", "lavfi", "-i", lavfi, "-t", str(dur)]
    cmd += (extra or ["-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-pix_fmt", "yuv420p"])
    cmd += [tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, dest)
            return True
        log(f"synth FAILED {os.path.basename(dest)}: {(r.stderr or '')[-200:]}")
    except Exception as e:
        log(f"synth ERROR {os.path.basename(dest)}: {e}")
    return False


# category -> list of downloadable/segmentable/synthetic sources
DOWNLOADS = [
    # (id, category, type, kind, spec)
    ("sintel_trailer", "animation", "video", "download",
     "https://download.blender.org/durian/trailer/sintel_trailer-1080p.mp4"),
    ("popeye_patriotic", "animation", "video", "download",
     "https://archive.org/download/popeye_patriotic_popeye/popeye_patriotic_popeye_512kb.mp4"),
    ("betty_boop_1936", "old_film", "video", "download",
     "https://archive.org/download/BettyBoopCartoons/Betty_Boop_A_Song_a_Day_1936_512kb.mp4"),
    ("three_stooges", "old_film", "video", "segment",
     ("https://archive.org/download/3stooges/3stooges_NewApants2.mp4", 120, 25)),
    ("popeye_sinbad", "old_film", "video", "download",
     "https://archive.org/download/PopeyeTheSailorMeetsSindbadTheSailor/Popeye_meetsSinbadtheSailor_512kb.mp4"),
    ("tears_of_steel", "live_action", "video", "segment",
     ("https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov", 90, 22)),
    ("bbb_wikimedia_webm", "live_action", "video", "segment",
     ("https://upload.wikimedia.org/wikipedia/commons/transcoded/c/c0/Big_Buck_Bunny_4K.webm/Big_Buck_Bunny_4K.webm.360p.webm", 200, 20)),
    # --- round 2 additions (all free/CC/public domain; failures are skipped) ---
    ("sintel_full_action", "animation", "video", "segment",
     ("https://download.blender.org/durian/movies/Sintel.2010.720p.mkv", 600, 24)),
    ("bbb_720_action", "animation", "video", "segment",
     ("https://download.blender.org/peach/bigbuckbunny_movies/big_buck_bunny_720p_h264.mov", 300, 24)),
    ("elephants_dream", "animation", "video", "segment",
     ("https://archive.org/download/ElephantsDream/ed_1024.avi", 180, 24)),
    ("superman_1941", "old_film", "video", "download",
     "https://archive.org/download/superman_1941/superman_1941_512kb.mp4"),
    ("night_living_dead", "old_film", "video", "segment",
     ("https://archive.org/download/night_of_the_living_dead/night_of_the_living_dead_512kb.mp4", 1500, 25)),
    ("duck_and_cover", "old_film", "video", "download",
     "https://archive.org/download/DuckandC1951/DuckandC1951_512kb.mp4"),
]

SYNTH = [
    ("synth_banding", "screen_synth", "video",
     "gradients=size=1920x1080:rate=30:speed=0.02", 12, None),
    ("synth_fractal_motion", "screen_synth", "video",
     "mandelbrot=size=1280x720:rate=30", 15, None),
    ("synth_text_pattern", "screen_synth", "video",
     "testsrc2=size=1920x1080:rate=30", 12, None),
    # --- round 2: torture cases aimed at specific pipeline paths ---
    ("synth_scroll", "screen_synth", "video",
     "testsrc2=size=1920x1080:rate=30,scroll=vertical=0.002", 12, None),
    ("synth_grain", "screen_synth", "video",
     "testsrc2=size=1280x720:rate=30,noise=alls=10:allf=t", 12, None),
    ("synth_dark_band", "screen_synth", "video",
     "gradients=size=1280x720:rate=24:c0=0x050510:c1=0x101830:speed=0.01", 12, None),
    ("synth_60fps", "screen_synth", "video",
     "testsrc2=size=1280x720:rate=60", 10, None),
]

KODAK = ["kodim05", "kodim13", "kodim19", "kodim23", "kodim01"]  # varied detail/edges


def build_downloads(manifest):
    for cid, cat, typ, kind, spec in DOWNLOADS:
        d = os.path.join(CORPUS, cat)
        os.makedirs(d, exist_ok=True)
        dest = os.path.join(d, f"{cid}.mp4" if kind == "segment" else f"{cid}{os.path.splitext(str(spec))[1] or '.mp4'}")
        if kind == "download":
            ok = _download(spec, dest)
        else:
            url, ss, dur = spec
            ok = _segment(url, dest, ss, dur)
        if ok:
            log(f"ok  {cat:12} {cid}  ({os.path.getsize(dest)/1024/1024:.1f} MB)")
            manifest.append({"id": cid, "category": cat, "type": typ,
                             "source": kind, "path": dest})
        time.sleep(0.2)


def build_synth(manifest):
    d = os.path.join(CORPUS, "screen_synth")
    os.makedirs(d, exist_ok=True)
    for cid, cat, typ, lavfi, dur, extra in SYNTH:
        dest = os.path.join(d, f"{cid}.mp4")
        if _synth(dest, lavfi, dur, extra):
            log(f"ok  {cat:12} {cid}  ({os.path.getsize(dest)/1024/1024:.1f} MB)")
            manifest.append({"id": cid, "category": cat, "type": typ,
                             "source": "synthetic", "path": dest})


def build_stills(manifest):
    d = os.path.join(CORPUS, "stills")
    os.makedirs(d, exist_ok=True)
    for k in KODAK:
        dest = os.path.join(d, f"{k}.png")
        if _download(f"http://r0k.us/graphics/kodak/kodak/{k}.png", dest):
            log(f"ok  {'stills':12} {k}")
            manifest.append({"id": k, "category": "stills", "type": "image",
                             "source": "download", "path": dest})
    # synthetic high-detail still (sharp text + patterns)
    dest = os.path.join(d, "synth_pattern.png")
    if not os.path.exists(dest):
        subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080",
                        "-frames:v", "1", dest], capture_output=True)
    if os.path.exists(dest):
        manifest.append({"id": "synth_pattern", "category": "stills", "type": "image",
                         "source": "synthetic", "path": dest})


def sample_local_video(manifest, n=12):
    if not os.path.isdir(VIDEOS_DIR):
        log("local video dir not found, skipping")
        return
    files = []
    for r, _, fs in os.walk(VIDEOS_DIR):
        for f in fs:
            if f.lower().endswith(VID_EXT):
                p = os.path.join(r, f)
                try:
                    files.append((p, os.path.getsize(p)))
                except Exception:
                    pass
    if not files:
        return
    # stratify by size into n buckets, pick one per bucket for diversity
    files.sort(key=lambda t: t[1])
    picks = []
    if len(files) <= n:
        picks = [p for p, _ in files]
    else:
        step = len(files) / n
        for i in range(n):
            picks.append(files[int(i * step)][0])
    for i, p in enumerate(picks):
        manifest.append({"id": f"local_vid_{i:02d}", "category": "local_realworld",
                         "type": "video", "source": "local", "path": p})
    log(f"sampled {len(picks)} local videos")


def sample_local_audio(manifest, n=10):
    if not os.path.isdir(MUSIC_DIR):
        log("music dir not found, skipping")
        return
    files = []
    for r, _, fs in os.walk(MUSIC_DIR):
        for f in fs:
            if f.lower().endswith(AUD_EXT):
                files.append(os.path.join(r, f))
    if not files:
        return
    random.seed(42)
    picks = random.sample(files, min(n, len(files)))
    for i, p in enumerate(picks):
        ext = os.path.splitext(p)[1].lstrip(".").lower()
        manifest.append({"id": f"local_aud_{i:02d}", "category": "local_audio",
                         "type": "audio", "source": "local", "path": p,
                         "src_format": ext})
    log(f"sampled {len(picks)} local audio tracks (D:\\MUSIC, read-only)")


def main():
    os.makedirs(CORPUS, exist_ok=True)
    manifest = []
    log("=== downloading reference clips ===")
    build_downloads(manifest)
    log("=== synthesizing torture clips ===")
    build_synth(manifest)
    log("=== fetching still-image set ===")
    build_stills(manifest)
    log("=== sampling local media ===")
    sample_local_video(manifest)
    sample_local_audio(manifest)

    mpath = os.path.join(CORPUS, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    from collections import Counter
    by_cat = Counter(m["category"] for m in manifest)
    by_type = Counter(m["type"] for m in manifest)
    log(f"=== DONE: {len(manifest)} items ===")
    log(f"by category: {dict(by_cat)}")
    log(f"by type:     {dict(by_type)}")
    log(f"manifest -> {mpath}")


if __name__ == "__main__":
    main()
