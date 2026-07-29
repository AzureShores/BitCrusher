"""Where an encode actually spent its bits, per scene.

The feature plan assumed this was a charting job on data the pipeline already
had. It was not: nothing in the codebase read packet sizes, so "these 3 scenes
cost 60% of your bits" had no numbers behind it. This module measures them.

Two halves, deliberately split:
  * `read_video_packets` shells out to ffprobe (the only impure part);
  * the bucketing and ranking below are pure and unit-tested.

Scene boundaries come from ml_heuristics.analyze_scenes on the SOURCE, and the
packet timestamps come from the OUTPUT. Those share a timeline only when the
encode was not trimmed - callers must not run this on a trimmed job.
"""
from __future__ import annotations

import bisect
import subprocess

from encode.ffmpeg_exec import _sp_run, si, NO_WIN
from encode import ffmpeg_exec as _fx

# A long source produces a lot of packets; past this many we stop reading
# rather than pull a multi-megabyte table into memory for a report card.
MAX_PACKETS = 400_000


def read_video_packets(path: str, *, ffprobe=None, max_packets: int = MAX_PACKETS):
    """[(pts_seconds, size_bytes), ...] for the video stream, in file order.

    Packets with no usable timestamp are dropped: a packet that cannot be
    placed on the timeline cannot be attributed to a scene, and guessing one
    would quietly move bits into the wrong bucket.
    """
    ffprobe = ffprobe or _fx.FFPROBE or "ffprobe"
    try:
        r = _sp_run([ffprobe, "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "packet=pts_time,size",
                     "-of", "csv=p=0", path],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, startupinfo=si, creationflags=NO_WIN)
    except Exception:
        return []
    if getattr(r, "returncode", 1) != 0:
        return []
    return parse_packet_csv(getattr(r, "stdout", "") or "", max_packets=max_packets)


def parse_packet_csv(text: str, *, max_packets: int = MAX_PACKETS):
    """Parse ffprobe's `pts_time,size` CSV. Tolerant of N/A and blank lines."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip().rstrip(",")
        if not line or "," not in line:
            continue
        t_raw, _, s_raw = line.partition(",")
        try:
            t = float(t_raw)
            s = int(float(s_raw))
        except ValueError:
            continue
        if s < 0:
            continue
        out.append((t, s))
        if len(out) >= max_packets:
            break
    return out


def bucket_by_scenes(packets, scenes):
    """Sum packet bytes into scene ranges.

    Returns one row per scene: index, start, end, seconds, bytes, bits_per_s
    and `share` (fraction of all attributed bytes). Packets outside every
    scene are dropped from the shares rather than smeared into the nearest
    one - a report card that invents attribution is worse than one that
    admits a gap.
    """
    rows = []
    for i, sc in enumerate(scenes or []):
        try:
            a = float((sc or {}).get("start"))
            b = float((sc or {}).get("end"))
        except (TypeError, ValueError, AttributeError):
            continue
        if b <= a:
            continue
        rows.append({"index": i, "start": a, "end": b, "seconds": b - a,
                     "bytes": 0})
    if not rows:
        return []
    rows.sort(key=lambda r: r["start"])
    edges = [r["start"] for r in rows]
    for t, size in (packets or []):
        j = bisect.bisect_right(edges, t) - 1
        if j < 0 or t >= rows[j]["end"]:
            continue
        rows[j]["bytes"] += size
    total = sum(r["bytes"] for r in rows)
    for r in rows:
        r["share"] = (r["bytes"] / total) if total else 0.0
        r["bits_per_s"] = (r["bytes"] * 8.0 / r["seconds"]) if r["seconds"] else 0.0
    return rows


def rank_scenes(rows, *, top_n: int = 3):
    """The `top_n` scenes by share of the bit budget, plus what they cost.

    Returns {"top": [...], "top_share", "top_seconds_share", "total_bytes",
    "n_scenes"}. `top_seconds_share` is the honest counterweight to the
    headline: three scenes eating 60% of the bits is only interesting if they
    are not also 60% of the runtime.
    """
    usable = [r for r in (rows or []) if r.get("seconds")]
    if not usable:
        return {"top": [], "top_share": 0.0, "top_seconds_share": 0.0,
                "total_bytes": 0, "n_scenes": 0}
    ordered = sorted(usable, key=lambda r: r.get("bytes", 0), reverse=True)
    top = ordered[:max(1, int(top_n))]
    total_secs = sum(r["seconds"] for r in usable) or 1.0
    return {
        "top": top,
        "top_share": sum(r.get("share", 0.0) for r in top),
        "top_seconds_share": sum(r["seconds"] for r in top) / total_secs,
        "total_bytes": sum(r.get("bytes", 0) for r in usable),
        "n_scenes": len(usable),
    }


def report_headline(ranked) -> str:
    """One plain-ASCII sentence for the report card, or '' when there is
    nothing worth claiming."""
    if not isinstance(ranked, dict) or not ranked.get("top"):
        return ""
    n = len(ranked["top"])
    share = ranked.get("top_share") or 0.0
    secs_share = ranked.get("top_seconds_share") or 0.0
    if share <= 0:
        return ""
    txt = (f"{n} scene{'s' if n != 1 else ''} used {share * 100:.0f}% of the "
           f"bits in {secs_share * 100:.0f}% of the runtime")
    if share <= secs_share * 1.15:
        txt += " - bits are spread fairly evenly here"
    return txt
