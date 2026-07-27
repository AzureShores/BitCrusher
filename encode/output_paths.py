from __future__ import annotations

import os
import re
from pathlib import Path

from encode.media_math import determine_tune_profile

# --- unified output-naming helpers (prefix/suffix always applied, all media) ---

# Characters that must never reach a filename. Separators are the important
# ones: a prefix/suffix containing "/" or ".." redirected the output out of the
# chosen folder entirely, and slipped past the overwrite guard below (which only
# compares against the input path). The rest are illegal on Windows and produced
# an opaque encoder failure. Applied on every platform so names stay portable.
_AFFIX_BAD = set('<>:"/\\|?*') | {chr(c) for c in range(32)}


def _sanitize_affix(value) -> str:
    """Make a user-supplied prefix/suffix safe to splice into a filename."""
    s = "".join(ch for ch in str(value or "") if ch not in _AFFIX_BAD)
    # Windows silently drops trailing dots/spaces, so the name on disk would not
    # match the name we think we wrote.
    return s.strip().strip(".").strip()


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


SUPPORTED_TOKENS = {
    "name", "ext", "codec", "encoder", "size", "bytes", "vmaf", "target",
    "width", "height", "res", "fps", "date", "time", "index",
}

# Tokens only knowable once the encode has finished. A template using any of
# them gets its final name after the fact; everything else resolves up front.
POST_ENCODE_TOKENS = {"codec", "encoder", "size", "bytes", "vmaf"}

_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")


def template_tokens(template) -> set:
    """Tokens referenced by a template, whether or not they are supported."""
    return set(_TOKEN_RE.findall(str(template or "")))


def render_output_template(template, ctx: dict) -> str:
    """Render a filename stem from a template, dropping unknown tokens.

    A token with no value becomes empty and the separators left stranded around
    it are collapsed, so a missing {vmaf} yields "clip_x264" not "clip_x264_".
    Unknown token names are left literal so a typo is visible rather than
    silently eating part of the name.
    """
    def sub(m):
        key = m.group(1)
        if key not in SUPPORTED_TOKENS:
            return m.group(0)
        val = ctx.get(key)
        return "" if val is None else str(val)

    out = _TOKEN_RE.sub(sub, str(template or ""))
    out = re.sub(r"[_\-.]{2,}", lambda m: m.group(0)[0], out)
    return _sanitize_affix(out.strip("_-. "))


def template_context(input_path: str, adv: dict, ext: str, stats: dict | None = None) -> dict:
    """Token values for a template. Post-encode tokens stay None until `stats`."""
    from datetime import datetime

    adv = adv or {}
    st = stats or {}
    now = datetime.now()
    w = st.get("width") or adv.get("_out_width")
    h = st.get("height") or adv.get("_out_height")
    size_b = st.get("compressed_size")
    vmaf = st.get("vmaf")
    tgt = adv.get("_target_bytes")
    ctx = {
        "name": Path(input_path).stem,
        "ext": str(ext or "").lstrip("."),
        "date": now.strftime("%Y%m%d"),
        "time": now.strftime("%H%M%S"),
        "index": adv.get("_batch_index"),
        "width": int(w) if w else None,
        "height": int(h) if h else None,
        "res": (f"{int(w)}x{int(h)}" if w and h else None),
        "fps": (f"{float(st.get('frame_rate') or adv.get('_out_fps') or 0):g}"
                if (st.get("frame_rate") or adv.get("_out_fps")) else None),
        "target": (_human_mb(tgt) if tgt else None),
        "codec": (str(st.get("encoder")) or None) if st.get("encoder") else None,
        "size": (_human_mb(size_b) if size_b else None),
        "bytes": (int(size_b) if size_b else None),
        "vmaf": (f"{float(vmaf):.1f}" if isinstance(vmaf, (int, float)) else None),
    }
    ctx["encoder"] = ctx["codec"]
    return ctx


def _human_mb(n) -> str:
    try:
        mb = float(n) / (1024 * 1024)
    except Exception:
        return ""
    # Keep a decimal unless it is exact, so an 11.83MB output does not render
    # as "12MB" and read like the target it was aiming at.
    if mb == int(mb) or mb >= 100:
        return f"{mb:.0f}MB"
    return f"{mb:.1f}MB"


def _avoid_source(candidate: str, input_path: str, out_dir: str, stem: str, ext: str) -> str:
    """Never hand back a path equal to the source file."""
    def same(a, b):
        try:
            return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        except Exception:
            return False

    n = 0
    while same(candidate, input_path):
        n += 1
        tail = "_compressed" if n == 1 else f"_compressed_{n - 1}"
        candidate = os.path.join(out_dir, f"{stem}{tail}.{ext}")
    return candidate


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
    prefix = _sanitize_affix(adv.get("output_prefix", ""))
    suffix = _sanitize_affix(adv.get("output_suffix", ""))
    # Two-target export: tag secondary outputs (e.g. "_25MB") so both exports
    # of one source are distinguishable instead of colliding into _dup names.
    size_tag = _sanitize_affix(adv.get("size_tag", ""))
    if size_tag:
        suffix = f"{suffix}_{size_tag}"
    ext = default_ext.lstrip(".")

    # A template replaces prefix/suffix entirely. Post-encode tokens are unknown
    # here and drop out; compress_video renames once it knows them.
    tpl = str(adv.get("output_template", "") or "")
    if tpl:
        stem_t = render_output_template(tpl, template_context(input_path, adv, ext))
        if stem_t:
            candidate = os.path.join(out_dir, f"{stem_t}.{ext}")
            return _avoid_source(candidate, input_path, out_dir, stem_t, ext)

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


def finalize_templated_name(out_file: str, input_path: str, adv: dict,
                            stats: dict) -> str:
    """Rename a finished output once post-encode tokens are known.

    No-op unless a template is set and actually uses one of those tokens.
    Returns the path the file now lives at - the original on any failure, so a
    rename problem can never lose a good encode.
    """
    tpl = str((adv or {}).get("output_template", "") or "")
    if not tpl or not (template_tokens(tpl) & POST_ENCODE_TOKENS):
        return out_file
    try:
        if not os.path.isfile(out_file):
            return out_file
        out_dir = os.path.dirname(os.path.abspath(out_file))
        ext = os.path.splitext(out_file)[1].lstrip(".")
        stem = render_output_template(tpl, template_context(input_path, adv, ext, stats))
        if not stem:
            return out_file
        target = _avoid_source(os.path.join(out_dir, f"{stem}.{ext}"),
                               input_path, out_dir, stem, ext)
        if os.path.normcase(target) == os.path.normcase(os.path.abspath(out_file)):
            return out_file
        target = _dedup_safe_output_path(target, out_file)
        os.replace(out_file, target)
        return target
    except Exception:
        return out_file


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
