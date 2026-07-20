"""
summarize.py — turn test_pipeline/results.json into the numbers for a report.

Prints: overall totals, per-category quality/size, per-encoder codec-race stats,
target-hit rate (how often output landed under the size cap — the hard ceiling),
and any overshoots or failures worth eyeballing.
"""
import json, os
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(ROOT, "results.json"), encoding="utf-8"))
ok = [r for r in rows if r.get("ok")]


def mb(b):
    return (b or 0) / 1024 / 1024


def avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else 0


print(f"=== OVERALL ===  {len(ok)}/{len(rows)} ok")
tin = sum(r.get("in_bytes", 0) for r in ok)
tout = sum(r.get("out_bytes", 0) for r in ok)
print(f"in={mb(tin):.1f}MB  out={mb(tout):.1f}MB  saved={mb(tin-tout):.1f}MB  "
      f"avg out%={avg([r.get('ratio_pct') for r in ok]):.1f}")
under = [r for r in ok if r.get("under_target")]
print(f"under target: {len(under)}/{len(ok)}  ({len(under)/max(1,len(ok))*100:.0f}%)")

print("\n=== BY CATEGORY ===")
cat = defaultdict(list)
for r in ok:
    cat[r["category"]].append(r)
for c, rs in sorted(cat.items()):
    vm = [r.get("vmaf") for r in rs if isinstance(r.get("vmaf"), (int, float))]
    print(f"{c:16} n={len(rs):2}  avg out%={avg([r.get('ratio_pct') for r in rs]):5.1f}"
          f"  avgVMAF={avg(vm):5.1f}" + (f" (n={len(vm)})" if vm else "  (no vmaf)"))

print("\n=== CODEC RACE (video, by chosen encoder) ===")
enc = defaultdict(list)
for r in ok:
    if r["type"] == "video" and r.get("encoder"):
        enc[r["encoder"]].append(r)
for e, rs in sorted(enc.items(), key=lambda kv: -avg([x.get("vmaf") for x in kv[1]])):
    vm = [r.get("vmaf") for r in rs if isinstance(r.get("vmaf"), (int, float))]
    print(f"{e:10} n={len(rs):2}  avgVMAF={avg(vm):5.1f}  avg out%={avg([r.get('ratio_pct') for r in rs]):5.1f}")

print("\n=== AUDIO (by output ratio) ===")
aud = [r for r in ok if r["type"] == "audio"]
if aud:
    print(f"n={len(aud)}  in={mb(sum(r['in_bytes'] for r in aud)):.1f}MB  "
          f"out={mb(sum(r['out_bytes'] for r in aud)):.1f}MB  "
          f"avg out%={avg([r.get('ratio_pct') for r in aud]):.1f}")

overs = [r for r in ok if not r.get("under_target")]
if overs:
    print("\n=== OVER TARGET (ceiling breaches — should be zero) ===")
    for r in overs:
        print(f"  {r['id']:20} {r['target_mb']}MB  out={mb(r['out_bytes']):.2f}MB  "
              f"over by {r.get('over_by_bytes',0)/1024:.0f}KB")

fails = [r for r in rows if not r.get("ok")]
if fails:
    print(f"\n=== FAILURES ({len(fails)}) ===")
    for r in fails:
        print(f"  {r['id']:20} {r.get('target_mb')}MB  {r.get('error')}")
