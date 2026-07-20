"""
run_corpus.py — run BitCrusher over the built corpus and collect real results.

For each manifest item it invokes the BitCrusher CLI at one or more target sizes,
then correlates the resulting `encode_end` event from the day's run log (the
ground-truth record of what the pipeline actually decided — encoder, VMAF, sizes)
back to that item. Outputs go to test_pipeline/out/ only; sources are never
touched (D:\\MUSIC stays read-only).

Resumable + incremental: results are keyed by (id, target_mb) and flushed after
every encode, so it's safe to Ctrl-C and re-run — finished encodes are skipped.

Usage:
  python test_pipeline/run_corpus.py                 # full run, default targets
  python test_pipeline/run_corpus.py --only video    # just one media type
  python test_pipeline/run_corpus.py --quality balanced
"""
import argparse, glob, json, os, shutil, subprocess, sys, time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(ROOT)
CORPUS = (os.environ.get("BC_CORPUS_DIR")
          or (r"D:\BitCrusherTraining\corpus" if os.path.isdir(r"D:/")
              else os.path.join(ROOT, "corpus")))
OUT = os.path.join(ROOT, "out")
RESULTS = os.path.join(ROOT, "results.json")
LOGDIRS = [d for d in (os.path.join(BASE, "logs"),
                       os.path.join(BASE, "user_settings", "logs")) if os.path.isdir(d)]

TARGETS = {"video": [10, 25], "image": [1.5], "audio": [8]}
MIN_VIDEO_BYTES = 100 * 1024  # skip degenerate empty/near-empty local clips


def log(m):
    print(f"[run] {m}", flush=True)


def load_results():
    if os.path.exists(RESULTS):
        try:
            return json.load(open(RESULTS, encoding="utf-8"))
        except Exception:
            return []
    return []


def save_results(rows):
    tmp = RESULTS + ".tmp"
    json.dump(rows, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, RESULTS)


def scan_encode_end(since_iso, want):
    """Find the newest encode_end event at/after since_iso whose output basename
    is exactly `want` (used only for vmaf/encoder metadata; sizes come from disk)."""
    best = None
    logfiles = [f for d in LOGDIRS for f in glob.glob(os.path.join(d, "run_*.jsonl"))]
    for lf in logfiles:
        try:
            if os.path.getmtime(lf) < time.time() - 86400:
                continue
        except Exception:
            pass
        try:
            for line in open(lf, encoding="utf-8"):
                line = line.strip()
                if not line or '"encode_end"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("event") != "encode_end":
                    continue
                ts = d.get("ts", "")
                if ts < since_iso:
                    continue
                op = (d.get("output_path") or d.get("filename") or "").replace("\\", "/")
                if os.path.basename(op).lower() == want.lower():   # exact, not substring
                    if best is None or ts >= best.get("ts", ""):
                        best = d
        except Exception:
            continue
    return best


def run_item(item, target_mb, quality):
    src = item["path"]
    # Each (item, target) gets its OWN output dir so the produced file is
    # unambiguous — sizes are read from disk (ground truth), never fuzzy-matched
    # from logs (which mis-correlated on shared dirs / substring stems).
    outdir = os.path.join(OUT, item["category"], f"{item['id']}_t{target_mb}")
    if os.path.isdir(outdir):
        shutil.rmtree(outdir, ignore_errors=True)
    os.makedirs(outdir, exist_ok=True)
    since = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    t0 = time.time()
    cmd = [sys.executable, os.path.join(BASE, "BitCrusherV9.py"), src,
           "-t", str(target_mb), "-o", outdir, "--quality", quality]
    try:
        subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "secs": round(time.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120], "secs": round(time.time() - t0, 1)}

    secs = round(time.time() - t0, 1)
    produced = [os.path.join(outdir, f) for f in os.listdir(outdir)
                if os.path.isfile(os.path.join(outdir, f))]
    if not produced:
        return {"ok": False, "error": "no output file produced", "secs": secs}
    out_path = max(produced, key=os.path.getmtime)          # the delivered file
    cb = os.path.getsize(out_path)                           # size from disk
    ob = os.path.getsize(src)                                # source size from disk
    ev = scan_encode_end(since, os.path.basename(out_path)) or {}  # vmaf/encoder only
    tgt = int(target_mb * 1024 * 1024)
    return {
        "ok": True, "secs": secs,
        "in_bytes": ob, "out_bytes": cb,
        "ratio_pct": round(cb / ob * 100, 1) if ob else None,
        "under_target": cb <= tgt,
        "over_by_bytes": max(0, cb - tgt),
        "vmaf": ev.get("vmaf"),
        "encoder": ev.get("encoder"),
        "width": ev.get("width"), "height": ev.get("height"),
        "encode_seconds": ev.get("encode_seconds"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["video", "image", "audio"], default=None)
    ap.add_argument("--quality", default="max")
    args = ap.parse_args()

    manifest = json.load(open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8"))
    rows = load_results()
    # keep only successful results; failed ones should retry on resume
    rows = [r for r in rows if r.get("ok")]
    done = {(r["id"], r["target_mb"]) for r in rows}

    jobs = []
    for item in manifest:
        typ = item["type"]
        if args.only and typ != args.only:
            continue
        if typ == "video" and item.get("source") == "local":
            try:
                if os.path.getsize(item["path"]) < MIN_VIDEO_BYTES:
                    continue
            except Exception:
                continue
        for tmb in TARGETS.get(typ, [10]):
            if (item["id"], tmb) in done:
                continue
            jobs.append((item, tmb))

    log(f"{len(jobs)} encodes to run ({len(done)} already done), quality={args.quality}")
    for i, (item, tmb) in enumerate(jobs, 1):
        log(f"[{i}/{len(jobs)}] {item['category']:16} {item['id']:20} -> {tmb} MB ...")
        res = run_item(item, tmb, args.quality)
        rec = {"id": item["id"], "category": item["category"], "type": item["type"],
               "source": item["source"], "target_mb": tmb, **res}
        rows.append(rec)
        save_results(rows)
        if res.get("ok"):
            vm = f"VMAF {res['vmaf']}" if res.get("vmaf") is not None else "no-vmaf"
            enc = res.get("encoder") or "-"
            flag = "" if res.get("under_target") else "  !! OVER TARGET"
            log(f"      {res['in_bytes']/1024/1024:7.1f}MB -> {res['out_bytes']/1024/1024:6.2f}MB"
                f"  {res.get('ratio_pct')}%  {vm}  {enc}  {res['secs']}s{flag}")
        else:
            log(f"      FAILED: {res.get('error')} ({res.get('secs')}s)")

    ok = [r for r in rows if r.get("ok")]
    log(f"=== complete: {len(ok)}/{len(rows)} ok ===")


if __name__ == "__main__":
    main()
