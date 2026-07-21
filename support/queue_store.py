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
