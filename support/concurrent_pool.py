"""Process-based concurrent encode helper (see docs/CONCURRENCY_REWORK.md).

The old in-process ThreadPoolExecutor ran the ACTUAL encode in-thread, and the
encode/planning path communicates through process-global BC_* env vars, so
parallel threads corrupted each other's state (wrong encode params + poisoned
ledger attribution). The fix keeps the ThreadPoolExecutor (it already gives
bounded concurrency, ordering, cancellation semantics the callers rely on) but
makes each thread's actual encode step run in its OWN OS process instead of
in-process: `run_worker_subprocess` spawns `BitCrusherV9.py --bc-worker <spec>
<result>` with an isolated BC_USER_SETTINGS_DIR, so N threads' child processes
can never share mutable env or interleave ledger writes - there is nothing
shared between them to corrupt.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time


def _kill_tree(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import signal
            os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def merge_worker_ledger(worker_settings_dir: str, real_settings_dir: str, stats_lock=None) -> int:
    """Append a worker's isolated ledger.jsonl onto the real one. Single-writer
    (the calling thread, optionally under a shared lock) - workers never touch
    the shared ledger file, so there is no cross-process interleaving to guard
    against. Returns the number of lines merged."""
    src = os.path.join(worker_settings_dir, "stats", "ledger.jsonl")
    if not os.path.isfile(src):
        return 0
    try:
        with open(src, "r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except Exception:
        return 0
    if not lines:
        return 0

    dst_dir = os.path.join(real_settings_dir, "stats")
    dst = os.path.join(dst_dir, "ledger.jsonl")

    def _write():
        os.makedirs(dst_dir, exist_ok=True)
        with open(dst, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")

    if stats_lock is not None:
        with stats_lock:
            _write()
    else:
        _write()
    return len(lines)


def run_worker_subprocess(spec: dict, *, script_path: str, real_settings_dir: str,
                          python_exe: str | None = None, thread_budget: int | None = None,
                          stats_lock=None, cancel_event=None,
                          poll_interval: float = 0.25) -> dict:
    """Run ONE job as an isolated worker subprocess; block the calling thread
    until it finishes (or cancel_event fires, in which case the process tree is
    killed). Returns the worker's result dict (same shape _run_one/_one produce:
    ok/in_bytes/out_bytes/vmaf/encoder/output_path/secs/error).

    Intended to be called from inside a ThreadPoolExecutor worker (the executor
    already provides bounded concurrency/ordering) - this function's only job is
    making that thread's actual encode step process-isolated instead of
    in-process.
    """
    python_exe = python_exe or sys.executable
    work_dir = tempfile.mkdtemp(prefix="bc_job_")
    spec_path = os.path.join(work_dir, "spec.json")
    result_path = os.path.join(work_dir, "result.json")
    worker_settings_dir = os.path.join(work_dir, "user_settings")

    try:
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(spec, f)

        env = dict(os.environ)
        env["BC_USER_SETTINGS_DIR"] = worker_settings_dir
        if thread_budget:
            env["BC_THREAD_BUDGET"] = str(int(thread_budget))

        argv = [python_exe, script_path, "--bc-worker", spec_path, result_path]
        proc = subprocess.Popen(
            argv, env=env, cwd=os.path.dirname(os.path.abspath(script_path)) or ".",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        killed = False
        while True:
            try:
                proc.wait(timeout=poll_interval)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    _kill_tree(proc.pid)
                    killed = True
                    break

        if killed:
            return {"ok": False, "in_bytes": 0, "out_bytes": 0, "vmaf": None,
                    "encoder": None, "secs": 0.0, "error": "cancelled"}

        result = {"ok": False, "error": "worker produced no result"}
        try:
            if os.path.isfile(result_path):
                with open(result_path, "r", encoding="utf-8") as f:
                    result = json.load(f)
        except Exception as e:
            result = {"ok": False, "error": f"result read failed: {e}"}

        try:
            merge_worker_ledger(worker_settings_dir, real_settings_dir, stats_lock=stats_lock)
        except Exception:
            pass

        return result
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
