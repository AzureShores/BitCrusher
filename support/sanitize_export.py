"""
sanitize_export.py — export-only redaction for diagnostics a user shares.

BitCrusher's real logs (ledger.jsonl, per-job .log files, settings.json) keep
full absolute paths and, in settings.json, a plaintext Discord webhook URL --
useful for the user's own troubleshooting, but not safe to paste into a bug
report as-is. Everything here reads the originals and writes NEW files; it
never mutates user_settings/ in place, so local debugging power is untouched.

Redaction rules (deliberately minimal, matching the release-screenshot
precedent of blanking just the username token, not the whole path):
  - Only the home-directory segment of a path is replaced (<home>); the rest
    of the path structure is kept, since drive/subfolder context is often
    what's actually needed to diagnose a bug.
  - Filenames are left as-is -- BitCrusher already surfaces them elsewhere
    (Activity log, webhook summaries), so they're not treated as secret.
  - The webhook URL is not redacted, it is REMOVED entirely -- it is a live
    credential, not just PII, so there is no partial-redaction option for it.
"""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

_HOME = os.path.expanduser("~")


def redact_home(path: str) -> str:
    """Replace the user-profile prefix of `path` with a placeholder, keeping
    the rest of the path (drive, subfolders, filename) intact."""
    p = str(path or "")
    if not p:
        return p
    try:
        if _HOME and (p == _HOME or p.startswith(_HOME + os.sep) or p.startswith(_HOME + "/")):
            return "<home>" + p[len(_HOME):]
    except Exception:
        pass
    return p


def sanitize_ledger(stats_dir: str, out_path: str) -> int:
    """Copy ledger.jsonl with each record's `input` path redacted. Returns the
    number of records written; 0 if the ledger doesn't exist or is empty."""
    src = os.path.join(stats_dir, "ledger.jsonl")
    n = 0
    try:
        with open(src, "r", encoding="utf-8") as fin, \
             open(out_path, "w", encoding="utf-8") as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict) and "input" in rec:
                    rec["input"] = redact_home(str(rec.get("input") or ""))
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    except OSError:
        return 0
    return n


def sanitize_job_log(path: str, out_path: str) -> bool:
    """Copy a single job log with the home-directory prefix redacted from
    every line (job logs embed full ffmpeg command lines with absolute
    paths). Returns False if the source can't be read."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False
    text = text.replace(_HOME, "<home>") if _HOME else text
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        return False
    return True


def sanitize_settings(settings: dict) -> dict:
    """Strip the webhook URL entirely and redact home-directory paths from a
    settings dict. Does not touch the caller's dict or disk."""
    out = dict(settings or {})
    out.pop("webhook_url", None)
    out["use_webhook"] = 0
    for k in ("output_dir", "watch_folder"):
        if k in out:
            out[k] = redact_home(str(out.get(k) or ""))
    return out


def export_sanitized_bundle(user_settings_dir: str, out_zip_path: str, *,
                             recent_logs: int = 5) -> str:
    """Build a zip at out_zip_path containing a sanitized ledger.jsonl, the
    `recent_logs` most-recently-modified job logs, and a sanitized
    settings.json. Reads only -- user_settings_dir is never modified.
    Returns out_zip_path."""
    stats_dir = os.path.join(user_settings_dir, "stats")
    jobs_dir = os.path.join(user_settings_dir, "logs", "jobs")
    settings_path = os.path.join(user_settings_dir, "settings.json")

    tmp_dir = Path(out_zip_path).with_suffix("")
    with zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # ledger.jsonl
        ledger_tmp = str(tmp_dir) + "_ledger.jsonl"
        try:
            if sanitize_ledger(stats_dir, ledger_tmp) > 0:
                zf.write(ledger_tmp, arcname="ledger.jsonl")
        finally:
            try:
                os.remove(ledger_tmp)
            except OSError:
                pass

        # most recent job logs
        try:
            logs = sorted(Path(jobs_dir).glob("*.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            logs = []
        for i, log_path in enumerate(logs[:max(0, int(recent_logs))]):
            log_tmp = f"{tmp_dir}_log{i}.log"
            if sanitize_job_log(str(log_path), log_tmp):
                try:
                    zf.write(log_tmp, arcname=f"logs/{log_path.name}")
                finally:
                    try:
                        os.remove(log_tmp)
                    except OSError:
                        pass

        # settings.json
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f) or {}
            clean = sanitize_settings(settings)
            zf.writestr("settings.json", json.dumps(clean, indent=2, ensure_ascii=False))
        except (OSError, json.JSONDecodeError):
            pass

    return out_zip_path
