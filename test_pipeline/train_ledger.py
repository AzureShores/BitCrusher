"""
train_ledger.py — cold-start trainer for the outcome ledger.

Runs the legal corpus (D:\\BitCrusherTraining\\corpus) through the REAL
BitCrusher pipeline sequentially — one encode at a time, never parallel — so
the outcome ledger accumulates diverse (content x encoder x bitrate) records
without waiting months of organic use. Outputs land on D: and are deleted
after each run; only the ledger knowledge is kept. Fully resumable: combos
already present in the ledger are skipped.

Usage:
  python test_pipeline/train_ledger.py                # all combos, sequential
  python test_pipeline/train_ledger.py --limit 6      # first N combos only
  python test_pipeline/train_ledger.py --dry-run      # list the plan, run nothing
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

CORPUS = (os.environ.get("BC_CORPUS_DIR") or r"D:\BitCrusherTraining\corpus")
OUT_DIR = (os.environ.get("BC_TRAIN_OUT") or r"D:\BitCrusherTraining\out")
VID_EXT = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")
ENCODERS = ("x264", "x265", "av1")   # alternated; av1 feeds the codec-winner prior
TARGET_FRACTIONS = (0.35, 0.25, 0.15, 0.10)  # four operating points per file
MAX_SRC_SECONDS = 480                # longer sources are TRIMMED, not skipped
TRIM_SECONDS = 90                    # middle window taken from long sources
TARGET_CLAMP = (3, 25)               # MB


def log(msg):
    print(f"[Train] {msg}", flush=True)


def _src_duration(path):
    import subprocess
    ffprobe = os.path.join(ROOT, "tools", "ffprobe.exe")
    if not os.path.exists(ffprobe):
        ffprobe = "ffprobe"
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                              "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30).stdout
        return float(out.strip())
    except Exception:
        return 0.0


def plan_combos():
    files = []
    for base, _dirs, names in os.walk(CORPUS):
        for n in sorted(names):
            if n.lower().endswith(VID_EXT):
                p = os.path.join(base, n)
                files.append((p, _src_duration(p)))
    combos = []
    for i, (f, d) in enumerate(sorted(files)):
        try:
            src_mb = os.path.getsize(f) / 1048576.0
        except Exception:
            continue
        # Very long sources train on a trimmed middle window (uses the trim
        # feature); targets are computed from the TRIMMED size so they still
        # force real compression instead of tripping the passthrough.
        trim = None
        eff_mb = src_mb
        if d > MAX_SRC_SECONDS and d > 0:
            a = max(0.0, d / 2.0 - TRIM_SECONDS / 2.0)
            trim = f"{int(a)}-{int(a + TRIM_SECONDS)}"
            eff_mb = src_mb * (TRIM_SECONDS / d)
        for j, frac in enumerate(TARGET_FRACTIONS):
            tmb = int(max(TARGET_CLAMP[0], min(TARGET_CLAMP[1], round(eff_mb * frac))))
            if tmb >= eff_mb:          # target must actually force compression
                tmb = max(TARGET_CLAMP[0], int(eff_mb * 0.5)) or TARGET_CLAMP[0]
                if tmb >= eff_mb:
                    continue
            enc = ENCODERS[(i + j) % len(ENCODERS)]
            combos.append((f, tmb, enc, trim))
    return combos


def already_done(ledger_recs, path, tmb, enc):
    import outcome_ledger as ol
    base = os.path.basename(path).lower()
    fam = ol.encoder_family(enc)
    tb = tmb * 1024 * 1024
    for r in ledger_recs:
        try:
            if (os.path.basename(r.get("input", "")).lower() == base
                    and ol.encoder_family((r.get("op") or {}).get("encoder_eff")) == fam
                    and abs(int((r.get("op") or {}).get("target_bytes") or 0) - tb) < tb * 0.06):
                return True
        except Exception:
            continue
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="run at most N combos")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(CORPUS):
        log(f"corpus not found: {CORPUS} - run build_corpus.py first")
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)

    import BitCrusherV9 as bc
    import outcome_ledger as ol
    stats_dir = os.path.join(bc.USER_SETTINGS_DIR, "stats")
    recs = ol.ledger_load(stats_dir)

    combos = plan_combos()
    todo = [(f, t, e, tr) for f, t, e, tr in combos
            if not already_done(recs, f, t, e)]
    log(f"{len(combos)} combos planned, {len(combos) - len(todo)} already in ledger, "
        f"{len(todo)} to run")
    if args.limit > 0:
        todo = todo[:args.limit]
    if args.dry_run:
        for f, t, e, tr in todo:
            log(f"  would run: {os.path.basename(f)} @ {t}MB [{e}]"
                + (f" trim {tr}" if tr else ""))
        return 0

    ok = fail = 0
    for i, (f, tmb, enc, trim) in enumerate(todo, 1):
        name = os.path.basename(f)
        log(f"({i}/{len(todo)}) {name} @ {tmb}MB [{enc}]" + (f" trim {trim}" if trim else ""))
        adv = dict(bc.ADVANCED_DEFAULTS)
        adv.update({"_target_is_bytes": True, "quality_mode": "balanced",
                    "encoder": enc, "auto_codec": False, "codec_pinned": True,
                    "measure_quality": True, "smart_preproc": True})
        if trim:
            adv["trim_range"] = trim
        t0 = time.time()
        try:
            st = bc.auto_compress(f, OUT_DIR, lambda m, level="INFO": None,
                                  tmb * 1024 * 1024, "", adv, lambda: False) or {}
            outp = st.get("output_path")
            got = int(st.get("compressed_size") or 0)
            if got > 0:
                ok += 1
                log(f"    done: {got/1048576:.2f}MB, vmaf {st.get('vmaf')}, "
                    f"worst {st.get('vmaf_min_window')}, {time.time()-t0:.0f}s")
            else:
                fail += 1
                log("    FAILED: no output")
            if outp and os.path.isfile(outp):
                try:
                    os.remove(outp)      # keep the knowledge, not the bytes
                except Exception:
                    pass
        except KeyboardInterrupt:
            log("interrupted - ledger keeps everything finished so far")
            break
        except Exception as e:
            fail += 1
            log(f"    ERROR: {type(e).__name__}: {e}")

    log(f"batch complete: {ok} ok, {fail} failed")
    rep = ol.shadow_report(stats_dir)
    log(f"shadow report: {rep}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
