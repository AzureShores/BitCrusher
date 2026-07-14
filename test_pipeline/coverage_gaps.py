"""
coverage_gaps.py -- feature-space coverage report for the outcome ledger.

Buckets existing ledger.jsonl records by (content_class, encoder family, HDR)
and reports which buckets are thin, so test_pipeline's corpus can be grown
toward what's actually under-sampled instead of guessing. Read-only: this
never touches the live encode path or writes anything back to the ledger.

Usage:
  python test_pipeline/coverage_gaps.py                 # full report
  python test_pipeline/coverage_gaps.py --min-n 10       # custom "thin" threshold
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import learning.outcome_ledger as ol


def bucket_key(rec: dict) -> tuple:
    outc = rec.get("outcome") or {}
    op = rec.get("op") or {}
    src = rec.get("src") or {}
    klass = outc.get("content_class") or "unknown"
    fam = ol.encoder_family(op.get("encoder_eff"))
    hdr = bool(src.get("is_hdr"))
    return (klass, fam, hdr)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--min-n", type=int, default=5,
                   help="Buckets with fewer than this many records are flagged thin (default 5)")
    p.add_argument("--stats-dir", default=None,
                   help="Override the ledger's stats dir (default: user_settings/stats)")
    args = p.parse_args()

    stats_dir = args.stats_dir or os.path.join(ROOT, "user_settings", "stats")
    recs = ol.ledger_load(stats_dir)
    if not recs:
        print("No ledger records found -- nothing to analyze yet.")
        return 1

    counts: dict[tuple, int] = {}
    for r in recs:
        if not (r.get("outcome") or {}).get("success", True):
            continue  # failure records carry no useful feature-space coverage signal
        key = bucket_key(r)
        counts[key] = counts.get(key, 0) + 1

    print(f"[Coverage] {len(recs)} ledger records, {len(counts)} distinct "
          f"(content_class, encoder, hdr) buckets:\n")
    thin = []
    for key, n in sorted(counts.items(), key=lambda kv: kv[1]):
        klass, fam, hdr = key
        flag = " *** THIN ***" if n < args.min_n else ""
        if flag:
            thin.append(key)
        print(f"  {klass:15} {fam:6} hdr={hdr!s:5}  n={n:4}{flag}")

    unknown_n = sum(n for (klass, _, _), n in counts.items() if klass == "unknown")
    if unknown_n:
        print(f"\n  Note: {unknown_n} record(s) predate content_class tagging "
              f"and show as 'unknown' -- coverage for those will sharpen as new "
              f"encodes accumulate.")

    print(f"\n[Coverage] {len(thin)} bucket(s) below the n={args.min_n} threshold. "
          f"Prioritize test_pipeline corpus additions there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
