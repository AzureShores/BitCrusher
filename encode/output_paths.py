from __future__ import annotations

import os
from pathlib import Path

from encode.media_math import determine_tune_profile

# --- unified output-naming helpers (prefix/suffix always applied, all media) ---


def _build_vf_chain_for_noise(input_path, w, h, new_w, new_h, advanced_options):
    filters = []
    if new_w and new_h:
        filters.append(f"scale={new_w}:{new_h}:flags=lanczos")

    fn_low = os.path.basename(input_path).lower()
    looks_grainy = (
        "grain" in fn_low or "noise" in fn_low or "iso" in fn_low
        or determine_tune_profile(w, h, input_path) == "grain"
    )
    if advanced_options.get("grain_filter", True) and looks_grainy:
        filters.append("hqdn3d=1.5:1.5:6:6")

    return ",".join(filters) if filters else None


def _build_output_path(kind: str, input_path: str, out_dir: str, adv: dict, default_ext: str) -> str:
    """
    kind: 'video' | 'audio' | 'image'
    Keeps exact prefix/suffix (including empty strings). Never forces defaults.

    Guards against overwriting the source: with an empty prefix AND suffix, an
    output written next to the input at the same extension resolves to the input
    path itself — the pipeline then encodes over the original in place and later
    "measures" VMAF of the file against itself (observed: a 4K source destroyed,
    reported as VMAF 99.98 with original_size == compressed_size). If the target
    collides with the input, a disambiguating suffix is inserted so the original
    is always preserved.
    """
    stem = Path(input_path).stem
    prefix = str(adv.get("output_prefix", "") or "")
    suffix = str(adv.get("output_suffix", "") or "")
    # Two-target export: tag secondary outputs (e.g. "_25MB") so both exports
    # of one source are distinguishable instead of colliding into _dup names.
    size_tag = str(adv.get("size_tag", "") or "")
    if size_tag:
        suffix = f"{suffix}_{size_tag}"
    ext = default_ext.lstrip(".")
    candidate = os.path.join(out_dir, f"{prefix}{stem}{suffix}.{ext}")

    def _same_file(a: str, b: str) -> bool:
        try:
            return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        except Exception:
            return False

    if _same_file(candidate, input_path):
        candidate = os.path.join(out_dir, f"{prefix}{stem}{suffix}_compressed.{ext}")
        n = 1
        while _same_file(candidate, input_path) or os.path.exists(candidate):
            candidate = os.path.join(out_dir, f"{prefix}{stem}{suffix}_compressed_{n}.{ext}")
            n += 1
    return candidate


def _bc_build_output_path(input_path: str, out_dir: str, adv: dict, default_ext: str = "mp4") -> str:
    # Back-compat shim: older code called this helper without the 'kind' parameter.
    return _build_output_path("video", input_path, out_dir, adv or {}, default_ext)


def _dedup_safe_output_path(candidate: str, avoid: str) -> str:
    # _build_output_path only guards against colliding with its own input, not
    # a sibling duplicate's already-written output -- two sources sharing a
    # basename in different folders would collide. Disambiguate the same way:
    # an incrementing suffix.
    def _same(a, b):
        try:
            return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        except Exception:
            return False
    if not _same(candidate, avoid) and not os.path.exists(candidate):
        return candidate
    root, ext = os.path.splitext(candidate)
    n = 1
    cand = f"{root}_dup{n}{ext}"
    while _same(cand, avoid) or os.path.exists(cand):
        n += 1
        cand = f"{root}_dup{n}{ext}"
    return cand
