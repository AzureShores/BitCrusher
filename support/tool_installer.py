from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
import hashlib
import os
import shutil
import zipfile

import requests


class ToolInstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    url: str
    exe: str
    sha256: str | None = None
    aliases: tuple[str, ...] = ()


TOOL_SPECS: dict[str, ToolSpec] = {
    "HandBrakeCLI": ToolSpec(
        name="HandBrakeCLI",
        url="https://github.com/HandBrake/HandBrake/releases/download/1.7.3/HandBrakeCLI-1.7.3-win-x86_64.zip",
        exe="HandBrakeCLI.exe",
        aliases=("HandBrakeCLI.exe", "HandBrakeCLI"),
    ),
    # Full-featured build (BtbN GPL): includes SVT-AV1 (fast AV1 encoding),
    # libvmaf, and the complete filter set — enables BitCrusher's AV1 auto-pick
    # on long content. ~160 MB download vs ~90 MB for gyan "essentials".
    # (gyan.dev's own "full" build ships only as .7z, which we can't extract.)
    "ffmpeg": ToolSpec(
        name="ffmpeg",
        url="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        exe="ffmpeg.exe",
        aliases=("ffmpeg.exe", "ffmpeg"),
    ),
    "ffprobe": ToolSpec(
        name="ffprobe",
        url="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        exe="ffprobe.exe",
        aliases=("ffprobe.exe", "ffprobe"),
    ),
}


def _emit_status(status_cb: Callable[[str, str], None] | None, msg: str, level: str = "INFO") -> None:
    if status_cb is None:
        return
    status_cb(str(msg), str(level).upper())


def _is_executable_available(token: str) -> bool:
    val = str(token or "").strip()
    if not val:
        return False
    if os.path.isabs(val) or any(sep in val for sep in ("\\", "/")):
        return Path(val).is_file()
    return shutil.which(val) is not None


def resolve_tool_path(primary: str, aliases: Iterable[str]) -> str:
    val = str(primary or "").strip()
    if _is_executable_available(val):
        if os.path.isabs(val) or any(sep in val for sep in ("\\", "/")):
            return str(Path(val))
        found = shutil.which(val)
        if found:
            return found
        return val
    for alias in aliases:
        found = shutil.which(str(alias))
        if found:
            return found
    return val


def find_missing_tools(tool_map: dict[str, str]) -> list[str]:
    missing: list[str] = []
    for name, token in (tool_map or {}).items():
        if not _is_executable_available(str(token or "")):
            missing.append(str(name))
    return missing


def _safe_extract_member(zip_ref: zipfile.ZipFile, member: str, dest_dir: Path) -> Path:
    member_path = Path(member)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ToolInstallError(f"Unsafe archive member path: {member}")

    target = (dest_dir / member_path).resolve()
    base = dest_dir.resolve()
    if os.path.commonpath([str(base), str(target)]) != str(base):
        raise ToolInstallError(f"Blocked archive traversal for member: {member}")

    target.parent.mkdir(parents=True, exist_ok=True)
    with zip_ref.open(member, "r") as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return target


def _download_zip(url: str, dst_zip: Path, retries: int, timeout: tuple[float, float]) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, int(max(1, retries)) + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout) as response:
                response.raise_for_status()
                with open(dst_zip, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            if dst_zip.exists() and dst_zip.stat().st_size > 0:
                return
            raise ToolInstallError("Downloaded archive is empty.")
        except (requests.RequestException, OSError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
    if last_exc is None:
        raise ToolInstallError("Download failed for unknown reasons.")
    raise ToolInstallError(f"Failed to download archive: {last_exc}") from last_exc


def _verify_sha256(path: Path, expected: str | None) -> None:
    if not expected:
        return
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    actual = h.hexdigest().lower()
    if actual != str(expected).strip().lower():
        raise ToolInstallError(f"SHA256 mismatch for {path.name}.")


def install_tool(
    *,
    name: str,
    tools_dir: Path,
    status_cb: Callable[[str, str], None] | None = None,
    retries: int = 3,
    connect_timeout_s: float = 10.0,
    read_timeout_s: float = 90.0,
) -> Path:
    spec = TOOL_SPECS.get(str(name))
    if spec is None:
        raise ToolInstallError(f"Unknown tool: {name}")

    tools_dir = Path(tools_dir)
    tools_dir.mkdir(parents=True, exist_ok=True)
    exe_path = tools_dir / spec.exe
    if exe_path.is_file():
        _emit_status(status_cb, f"{spec.name} already installed at: {exe_path}")
        return exe_path

    zip_path = tools_dir / f"{spec.name}.zip.download"
    _emit_status(status_cb, f"Downloading {spec.name}...")
    _download_zip(
        spec.url,
        zip_path,
        retries=int(max(1, retries)),
        timeout=(float(connect_timeout_s), float(read_timeout_s)),
    )

    try:
        if not zipfile.is_zipfile(zip_path):
            raise ToolInstallError(f"Corrupted or invalid ZIP archive for {spec.name}.")
        _verify_sha256(zip_path, spec.sha256)
        _emit_status(status_cb, f"Extracting {spec.name}...")
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            members = [
                m
                for m in zip_ref.namelist()
                if Path(m).name.lower() == spec.exe.lower()
            ]
            if not members:
                raise ToolInstallError(f"{spec.exe} not found inside archive.")
            members.sort(key=lambda m: len(Path(m).parts))
            extracted = _safe_extract_member(zip_ref, members[0], tools_dir)

        if extracted.resolve() != exe_path.resolve():
            exe_path.parent.mkdir(parents=True, exist_ok=True)
            if exe_path.exists():
                exe_path.unlink()
            extracted.replace(exe_path)
        return exe_path
    finally:
        try:
            if zip_path.exists():
                zip_path.unlink()
        except OSError:
            pass
