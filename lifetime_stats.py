from __future__ import annotations

import glob as _glob
import json
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_LOG_DIR = os.path.join(_SCRIPT_DIR, "logs")


def aggregate_lifetime_stats(log_dir: str | None = None) -> dict:
    """
    Read every logs/run_*.jsonl and roll up the 'encode_end' history into
    lifetime totals for the in-app Stats tab. Pure/offline — just parses the
    JSONL the pipeline already writes. Returns a dict with:
      count, total_original, total_compressed, bytes_saved, overall_ratio,
      total_time, by_type{video/audio/image:{count,original,compressed}},
      vmaf{count,avg,buckets{...}}, encoders{name:count}, files_first/last ts.
    """
    base = log_dir or _DEFAULT_LOG_DIR
    agg = {
        "count": 0, "total_original": 0, "total_compressed": 0,
        "bytes_saved": 0, "overall_ratio": 0.0, "total_time": 0.0,
        "by_type": {}, "vmaf": {"count": 0, "avg": 0.0, "buckets": {}},
        "encoders": {}, "first_ts": None, "last_ts": None,
    }
    # VMAF buckets from worst to best (label -> [lo, hi)).
    _vbuckets = [("<80", -1e9, 80.0), ("80–90", 80.0, 90.0), ("90–95", 90.0, 95.0),
                 ("95–98", 95.0, 98.0), ("98+", 98.0, 1e9)]
    for _lbl, _, _ in _vbuckets:
        agg["vmaf"]["buckets"][_lbl] = 0
    _vmaf_sum = 0.0
    try:
        files = sorted(_glob.glob(os.path.join(base, "run_*.jsonl")))
    except Exception:
        files = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        d = json.loads(ln)
                    except Exception:
                        continue
                    if d.get("event") != "encode_end":
                        continue
                    try:
                        o = int(d.get("original_size") or 0)
                        c = int(d.get("compressed_size") or 0)
                    except Exception:
                        continue
                    if o <= 0 or c <= 0:
                        continue
                    agg["count"] += 1
                    agg["total_original"] += o
                    agg["total_compressed"] += c
                    try:
                        agg["total_time"] += float(d.get("time_taken") or 0.0)
                    except Exception:
                        pass
                    ts = d.get("ts")
                    if ts:
                        if agg["first_ts"] is None or ts < agg["first_ts"]:
                            agg["first_ts"] = ts
                        if agg["last_ts"] is None or ts > agg["last_ts"]:
                            agg["last_ts"] = ts
                    t = str(d.get("type") or "other").lower()
                    bt = agg["by_type"].setdefault(t, {"count": 0, "original": 0, "compressed": 0})
                    bt["count"] += 1; bt["original"] += o; bt["compressed"] += c
                    enc = str(d.get("encoder") or "").strip().lower()
                    if enc:
                        agg["encoders"][enc] = agg["encoders"].get(enc, 0) + 1
                    v = d.get("vmaf")
                    if isinstance(v, (int, float)) and v > 0:
                        agg["vmaf"]["count"] += 1
                        _vmaf_sum += float(v)
                        for lbl, lo, hi in _vbuckets:
                            if lo <= float(v) < hi:
                                agg["vmaf"]["buckets"][lbl] += 1
                                break
        except Exception:
            continue
    agg["bytes_saved"] = agg["total_original"] - agg["total_compressed"]
    if agg["total_original"] > 0:
        agg["overall_ratio"] = agg["total_compressed"] / agg["total_original"]
    if agg["vmaf"]["count"] > 0:
        agg["vmaf"]["avg"] = _vmaf_sum / agg["vmaf"]["count"]
    return agg
