from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
import glob
import os


_AUDIO_EXTS = {
    ".flac", ".wav", ".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wma", ".alac", ".aiff", ".aif"
}


def merge_per_file_options(
    adv_options: Mapping[str, Any] | None,
    per_file_opts: Mapping[str, Any] | None,
    filepath: str,
) -> dict[str, Any]:
    out = dict(adv_options or {})
    per_file = (per_file_opts or {}).get(filepath, {})
    if isinstance(per_file, Mapping):
        out.update(dict(per_file))
    return out


def resolve_target_bytes(
    target_size: Any,
    fallback_target_bytes: int,
    adv_options: dict[str, Any] | None,
) -> int:
    try:
        target_bytes = int(target_size)
        if target_bytes <= 0:
            raise ValueError
    except (TypeError, ValueError):
        target_bytes = int(max(1, int(fallback_target_bytes)))

    t_mb = int(max(0, target_bytes // (1024 * 1024)))
    if isinstance(adv_options, dict) and t_mb > 0:
        adv_options["two_pass"] = True
        try:
            overshoot = float(adv_options.get("overshoot_ratio", 1.0))
        except (TypeError, ValueError):
            overshoot = 1.0
        adv_options["overshoot_ratio"] = max(0.90, min(1.15, overshoot))

    return int(max(1, target_bytes))


def media_ratio_info(filepath: str, target_bytes: int) -> tuple[int, float, bool]:
    src_bytes = int(os.path.getsize(filepath))
    target_bytes = int(max(1, int(target_bytes)))
    ratio = (target_bytes / float(src_bytes)) if src_bytes else 1.0
    ext = Path(filepath).suffix.lower()
    is_audio = ext in _AUDIO_EXTS
    return src_bytes, ratio, is_audio


def apply_hwaccel_encoder_defaults(
    adv_options: Mapping[str, Any] | None,
    hwaccel_value: str | None,
) -> dict[str, Any]:
    out = dict(adv_options or {})
    out["hwaccel"] = str(hwaccel_value or out.get("hwaccel", "CPU"))
    out["encoder"] = str(out.get("encoder") or "x264")
    return out


def latest_matching_output(
    filepath: str,
    output_folder: str,
    save_path_getter: Callable[[], str] | None,
) -> str | None:
    stem = Path(filepath).stem
    save_dir_guess = output_folder if os.path.isdir(output_folder) else (save_path_getter() if save_path_getter else ".")
    candidates = sorted(
        glob.glob(os.path.join(save_dir_guess, f"*{stem}*")),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    return candidates[0] if candidates else None
