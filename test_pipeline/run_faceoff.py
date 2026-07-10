"""
run_faceoff.py — BitCrusher MAX vs Shutter Encoder's documented method (MAX).

Same corpus files, same target, same VMAF measurement (ffmpeg libvmaf vs source),
sizes read from disk on both sides. Video: BitCrusher max vs Shutter 2-pass x264
AND x265 (giving Shutter its best codec). Audio: BitCrusher vs Shutter AAC.
"""
import os, sys, json, subprocess, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shutter_method as sm

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "tmp", "faceoff")
manifest = {m["id"]: m for m in json.load(open(os.path.join(ROOT, "corpus", "manifest.json"), encoding="utf-8"))}

VIDEOS = [("sintel_trailer", "animation"), ("betty_boop_1936", "old cartoon"),
          ("synth_fractal_motion", "synthetic"), ("popeye_sinbad", "old grainy"),
          ("local_vid_10", "screen rec")]
AUDIO = [("local_aud_09", "FLAC 68MB"), ("local_aud_04", "FLAC 20MB")]
VID_TARGET = 10
AUD_TARGET = 8


def mb(b):
    return (b or 0) / 1024 / 1024


def run_bitcrusher(src, target, outdir):
    os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        try:
            os.remove(os.path.join(outdir, f))
        except Exception:
            pass
    t0 = time.time()
    subprocess.run([sys.executable, os.path.join(BASE, "BitCrusherV9.py"), src,
                    "-t", str(target), "-o", outdir, "--quality", "max"],
                   cwd=BASE, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2400)
    secs = round(time.time() - t0, 1)
    files = [os.path.join(outdir, f) for f in os.listdir(outdir) if os.path.isfile(os.path.join(outdir, f))]
    if not files:
        return None
    out = max(files, key=os.path.getmtime)
    return {"path": out, "bytes": os.path.getsize(out), "secs": secs}


def main():
    rows = []
    print("=== VIDEO: BitCrusher MAX vs Shutter MAX (target %d MB) ===" % VID_TARGET, flush=True)
    for vid, label in VIDEOS:
        src = manifest[vid]["path"]
        src_mb = mb(os.path.getsize(src))
        d = os.path.join(OUT, vid)
        print(f"\n[{vid}] ({label}, src {src_mb:.1f} MB)", flush=True)
        rec = {"id": vid, "label": label, "type": "video", "src_mb": round(src_mb, 2), "target": VID_TARGET}

        bc = run_bitcrusher(src, VID_TARGET, os.path.join(d, "bc"))
        if bc:
            bc["vmaf"] = sm.measure_vmaf(src, bc["path"])
            rec["bitcrusher"] = {"mb": round(mb(bc["bytes"]), 2), "vmaf": bc["vmaf"], "secs": bc["secs"],
                                 "under": bc["bytes"] <= VID_TARGET * 1024 * 1024}
            print(f"  BitCrusher : {mb(bc['bytes']):6.2f} MB  VMAF {bc['vmaf']}  {bc['secs']}s"
                  f"  {'OK' if rec['bitcrusher']['under'] else 'OVER'}", flush=True)

        for codec, tag in (("libx264", "shutter_x264"), ("libx265", "shutter_x265")):
            os.makedirs(d, exist_ok=True)
            out = os.path.join(d, tag + ".mp4")
            r = sm.shutter_encode_video(src, out, VID_TARGET, codec=codec)
            if r.get("ok"):
                v = sm.measure_vmaf(src, out)
                rec[tag] = {"mb": round(mb(r["out_bytes"]), 2), "vmaf": v, "secs": r["secs"],
                            "kbps": r["video_kbps"], "under": r["out_bytes"] <= VID_TARGET * 1024 * 1024}
                print(f"  {tag:12}: {mb(r['out_bytes']):6.2f} MB  VMAF {v}  {r['secs']}s"
                      f"  {'OK' if rec[tag]['under'] else 'OVER'}  (@{r['video_kbps']}k)", flush=True)
            else:
                rec[tag] = {"error": r.get("error")}
                print(f"  {tag:12}: FAILED {r.get('error')}", flush=True)
        rows.append(rec)

    print("\n=== AUDIO: BitCrusher vs Shutter AAC (target %d MB) ===" % AUD_TARGET, flush=True)
    for aid, label in AUDIO:
        src = manifest[aid]["path"]
        src_mb = mb(os.path.getsize(src))
        d = os.path.join(OUT, aid)
        print(f"\n[{aid}] ({label}, src {src_mb:.1f} MB)", flush=True)
        rec = {"id": aid, "label": label, "type": "audio", "src_mb": round(src_mb, 2), "target": AUD_TARGET}
        bc = run_bitcrusher(src, AUD_TARGET, os.path.join(d, "bc"))
        if bc:
            rec["bitcrusher"] = {"mb": round(mb(bc["bytes"]), 2), "secs": bc["secs"],
                                 "ext": os.path.splitext(bc["path"])[1], "under": bc["bytes"] <= AUD_TARGET * 1024 * 1024}
            print(f"  BitCrusher : {mb(bc['bytes']):6.2f} MB  {rec['bitcrusher']['ext']}  {bc['secs']}s"
                  f"  {'OK' if rec['bitcrusher']['under'] else 'OVER'}", flush=True)
        out = os.path.join(d, "shutter_aac.m4a")
        r = sm.shutter_encode_audio(src, out, AUD_TARGET, codec="aac")
        if r.get("ok"):
            rec["shutter_aac"] = {"mb": round(mb(r["out_bytes"]), 2), "kbps": r["kbps"], "secs": r["secs"],
                                  "under": r["out_bytes"] <= AUD_TARGET * 1024 * 1024}
            print(f"  shutter_aac : {mb(r['out_bytes']):6.2f} MB  @{r['kbps']}k  {r['secs']}s"
                  f"  {'OK' if rec['shutter_aac']['under'] else 'OVER'}", flush=True)
        else:
            rec["shutter_aac"] = {"error": r.get("error")}
        rows.append(rec)

    json.dump(rows, open(os.path.join(ROOT, "faceoff_results.json"), "w", encoding="utf-8"), indent=1)
    print("\n=== done -> faceoff_results.json ===", flush=True)


if __name__ == "__main__":
    main()
