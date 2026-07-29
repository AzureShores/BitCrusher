"""Queue persistence (user_settings/queue.json), schema v2.

v1 stored only file paths + per-file overrides, so a crash mid-batch
restored every file as pending and already-done files re-encoded on the
next Start. v2 persists per-job status so a restarted session can skip
completed work.

Pure functions - no Tk, no globals. The GUI's _save_queue/_load_queue
are thin adapters keeping the tmp-file + os.replace atomic-write pattern.

Schema v2:
    {"version": 2,
     "jobs": [{"path": str,
               "status": "pending" | "done" | "failed" | "cancelled",
               "output": str | None,       # final file for done jobs
               "finished_at": float | None,
               "opts": dict}]}             # per-file overrides
"""
from __future__ import annotations

import os

_VALID_STATUSES = ("pending", "done", "failed", "cancelled")


def make_job(path: str, *, status: str = "pending", output=None,
             finished_at=None, opts=None) -> dict:
    return {
        "path": str(path),
        "status": status if status in _VALID_STATUSES else "pending",
        "output": output,
        "finished_at": finished_at,
        "opts": dict(opts or {}),
    }


def dump_queue(file_list, per_file_opts=None, job_states=None) -> dict:
    """Build the v2 payload.

    job_states: optional dict path -> {"status", "output", "finished_at"}
    (paths missing from it are pending).
    """
    per_file_opts = per_file_opts or {}
    job_states = job_states or {}
    jobs = []
    for p in file_list or []:
        if not isinstance(p, str) or not p:
            continue
        st = job_states.get(p) or {}
        jobs.append(make_job(
            p,
            status=str(st.get("status", "pending")),
            output=st.get("output"),
            finished_at=st.get("finished_at"),
            opts=per_file_opts.get(p),
        ))
    return {"version": 2, "jobs": jobs}


def load_queue(data) -> list[dict]:
    """Parse a queue payload (v1 or v2) into a list of job dicts.

    Tolerant of corrupt/partial input: bad entries are dropped, never
    raised. Files missing on disk are dropped (nothing to encode). Done
    jobs whose recorded output no longer exists are demoted to pending.
    """
    if not isinstance(data, dict):
        return []
    version = data.get("version")

    jobs: list[dict] = []
    if version == 2 and isinstance(data.get("jobs"), list):
        for row in data["jobs"]:
            if not isinstance(row, dict):
                continue
            p = row.get("path")
            if not isinstance(p, str) or not p:
                continue
            jobs.append(make_job(
                p,
                status=str(row.get("status", "pending")),
                output=row.get("output"),
                finished_at=row.get("finished_at"),
                opts=row.get("opts") if isinstance(row.get("opts"), dict) else None,
            ))
    else:
        # v1 (or unversioned): files + per_file_opts, everything pending.
        files = [x for x in (data.get("files") or []) if isinstance(x, str) and x]
        opts = data.get("per_file_opts") if isinstance(data.get("per_file_opts"), dict) else {}
        for p in files:
            o = opts.get(p)
            jobs.append(make_job(p, opts=o if isinstance(o, dict) else None))

    out = []
    for job in jobs:
        if not os.path.isfile(job["path"]):
            continue
        if job["status"] == "done":
            outp = job.get("output")
            if not (isinstance(outp, str) and os.path.isfile(outp)):
                job["status"] = "pending"
                job["output"] = None
                job["finished_at"] = None
        out.append(job)
    return out


def pending_jobs(jobs) -> list[dict]:
    """Jobs Start should actually encode (everything not done)."""
    return [j for j in jobs or [] if j.get("status") != "done"]


def next_pending_path(order, batch, done, running, *, deferred=None):
    """The next file a running batch should start, honouring the queue's
    CURRENT order rather than the order it had when Start was pressed.

    `order` is the live queue (self.file_list, which drag-to-reorder rewrites).
    `batch` is what this run is encoding, in its original order - anything the
    user has since removed from the queue is still encoded, in its original
    position, because removing a row is not the same gesture as cancelling a
    job and must not quietly become one.

    `deferred` maps a file to a prerequisite that should finish first (a dedup
    duplicate to its canonical). It is a preference, not a rule: if every
    remaining candidate is waiting on something, the first is started anyway -
    the reuse path already falls back to a normal encode when its canonical
    is not ready, so a stall here would cost more than the missed saving.

    Returns a path, or None when nothing is startable.
    """
    batch_set = set(batch or [])
    done_set = set(done or [])
    running_set = set(running or [])
    seen = set()
    candidates = []
    for p in list(order or []):
        if p in batch_set and p not in seen:
            seen.add(p)
            candidates.append(p)
    # Batch entries the live order no longer mentions keep their original slot.
    for i, p in enumerate(list(batch or [])):
        if p not in seen:
            seen.add(p)
            candidates.insert(min(i, len(candidates)), p)
    candidates = [p for p in candidates
                  if p not in done_set and p not in running_set]
    if not candidates:
        return None
    dep = deferred or {}
    for p in candidates:
        need = dep.get(p)
        if not need or need in done_set:
            return p
    return candidates[0]


# Sentinel for "the caller did not touch this knob", so that None can mean
# "clear it" without the two cases collapsing into each other.
KEEP = object()


def reencode_overrides(prev_opts=None, *, target_mb=KEEP, encoder=KEEP,
                       discord_compat=KEEP):
    """Per-file overrides for re-running a finished job with one knob changed.

    Starts from the overrides the job already had and applies only what the
    caller passed; a value of None clears that override and falls back to the
    batch setting. Returns (opts, changed) where `changed` is a list of plain
    labels for the status line.

    An explicitly chosen encoder is pinned - "encode again with x265" has to
    actually produce x265, so the codec race is disabled for that run. Clearing
    the encoder hands the choice back to the race.
    """
    opts = dict(prev_opts or {})
    changed: list[str] = []

    if target_mb is not KEEP:
        old = opts.get("target_mb")
        if target_mb is None or str(target_mb).strip() == "":
            if opts.pop("target_mb", None) is not None:
                changed.append("target: batch default")
        else:
            opts["target_mb"] = str(target_mb).strip()
            if opts["target_mb"] != old:
                changed.append(f"target: {opts['target_mb']} MB")

    if encoder is not KEEP:
        old = opts.get("encoder")
        name = "" if encoder is None else str(encoder).strip()
        if not name:
            for k in ("encoder", "codec_pinned", "auto_codec"):
                opts.pop(k, None)
            if old:
                changed.append("encoder: automatic")
        else:
            opts["encoder"] = name
            opts["codec_pinned"] = True
            opts["auto_codec"] = False
            if name != old:
                changed.append(f"encoder: {name} (pinned)")

    if discord_compat is not KEEP:
        old = bool(opts.get("discord_compat"))
        new = bool(discord_compat)
        opts["discord_compat"] = new
        if new != old:
            changed.append("Discord-compatible: " + ("on" if new else "off"))

    return opts, changed
