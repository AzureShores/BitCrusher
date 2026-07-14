from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Callable
import os
import time


def collect_existing_files(raw_files: Iterable[Any], normalize_path: Callable[[str], str]) -> list[str]:
    out: list[str] = []
    for item in raw_files:
        if not isinstance(item, str):
            continue
        normalized = normalize_path(item)
        if os.path.isfile(normalized):
            out.append(normalized)
    return out


def make_job_log_path(user_settings_dir: str, source_path: str, now_s: float | None = None) -> str:
    logs_dir = Path(user_settings_dir) / "logs" / "jobs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = int(float(now_s if now_s is not None else time.time()))
    return str(logs_dir / f"{ts}_{Path(source_path).name}.log")


def merge_job_options(
    base_options: Mapping[str, Any] | None,
    per_file_options: Mapping[str, Any] | None,
    source_path: str,
    *,
    job_log: str = "",
) -> dict[str, Any]:
    out = dict(base_options or {})
    per_file = (per_file_options or {}).get(source_path, {})
    if isinstance(per_file, Mapping):
        out.update(dict(per_file))
    if job_log:
        out["job_log"] = str(job_log)
    return out
