from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import hashlib
import os
import platform
import shutil
import stat
import tarfile
import zipfile

import requests


class ToolInstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    url: str
    exe: str            # basename to look for inside the archive / final name in tools/
    archive: str = "zip"  # "zip" or "tarxz"
    sha256: str | None = None
    aliases: tuple[str, ...] = ()


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _is_mac() -> bool:
    return platform.system() == "Darwin"


def _is_arm() -> bool:
    return platform.machine().lower() in ("arm64", "aarch64")


def _exe_name(base: str) -> str:
    """Append .exe on Windows, bare name elsewhere."""
    return base + ".exe" if _is_windows() else base


# Static ffmpeg builds (BtbN on Win/Linux, evermeet.cx on macOS): SVT-AV1,
# libvmaf, full filter set. Self-contained, dropped into tools/. No sudo.
def _build_specs() -> dict[str, ToolSpec]:
    specs: dict[str, ToolSpec] = {}

    if _is_windows():
        ff_url = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
                  "latest/ffmpeg-master-latest-win64-gpl.zip")
        ff_arch = "zip"
        # ffmpeg + ffprobe ship in the same BtbN archive.
        specs["ffmpeg"] = ToolSpec("ffmpeg", ff_url, _exe_name("ffmpeg"), ff_arch,
                                   aliases=("ffmpeg.exe", "ffmpeg"))
        specs["ffprobe"] = ToolSpec("ffprobe", ff_url, _exe_name("ffprobe"), ff_arch,
                                    aliases=("ffprobe.exe", "ffprobe"))
        specs["HandBrakeCLI"] = ToolSpec(
            "HandBrakeCLI",
            "https://github.com/HandBrake/HandBrake/releases/download/"
            "1.7.3/HandBrakeCLI-1.7.3-win-x86_64.zip",
            _exe_name("HandBrakeCLI"), "zip",
            aliases=("HandBrakeCLI.exe", "HandBrakeCLI"),
        )
        return specs

    if _is_mac():
        # evermeet.cx: one static binary per zip (Intel; runs on ASi via Rosetta).
        specs["ffmpeg"] = ToolSpec(
            "ffmpeg", "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip",
            "ffmpeg", "zip", aliases=("ffmpeg",))
        specs["ffprobe"] = ToolSpec(
            "ffprobe", "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip",
            "ffprobe", "zip", aliases=("ffprobe",))
        # HandBrakeCLI: brew, not a download. See _install_handbrake_native.
        return specs

    # Linux/POSIX: BtbN static tarball, both bins in one archive.
    linux_slug = "linuxarm64" if _is_arm() else "linux64"
    ff_url = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
              f"latest/ffmpeg-master-latest-{linux_slug}-gpl.tar.xz")
    specs["ffmpeg"] = ToolSpec("ffmpeg", ff_url, "ffmpeg", "tarxz",
                               aliases=("ffmpeg",))
    specs["ffprobe"] = ToolSpec("ffprobe", ff_url, "ffprobe", "tarxz",
                                aliases=("ffprobe",))
    # HandBrakeCLI: package manager, not a download. See _install_handbrake_native.
    return specs


TOOL_SPECS: dict[str, ToolSpec] = _build_specs()


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


def _safe_target(dest_dir: Path, member: str) -> Path:
    """Resolve an archive member to a path under dest_dir, blocking traversal."""
    member_path = Path(member)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ToolInstallError(f"Unsafe archive member path: {member}")
    target = (dest_dir / member_path).resolve()
    base = dest_dir.resolve()
    if os.path.commonpath([str(base), str(target)]) != str(base):
        raise ToolInstallError(f"Blocked archive traversal for member: {member}")
    return target


def _extract_binary_from_zip(zip_path: Path, exe: str, dest_dir: Path) -> Path:
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        members = [m for m in zip_ref.namelist()
                   if Path(m).name.lower() == exe.lower()]
        if not members:
            raise ToolInstallError(f"{exe} not found inside archive.")
        members.sort(key=lambda m: len(Path(m).parts))
        member = members[0]
        target = _safe_target(dest_dir, member)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zip_ref.open(member, "r") as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return target


def _extract_binary_from_tarxz(tar_path: Path, exe: str, dest_dir: Path) -> Path:
    with tarfile.open(tar_path, "r:xz") as tar_ref:
        members = [m for m in tar_ref.getmembers()
                   if m.isfile() and Path(m.name).name.lower() == exe.lower()]
        if not members:
            raise ToolInstallError(f"{exe} not found inside archive.")
        members.sort(key=lambda m: len(Path(m.name).parts))
        member = members[0]
        target = _safe_target(dest_dir, member.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        extracted = tar_ref.extractfile(member)
        if extracted is None:
            raise ToolInstallError(f"Could not read {exe} from archive.")
        with extracted as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return target


def _download_archive(url: str, dst: Path, retries: int, timeout: tuple[float, float]) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, int(max(1, retries)) + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout) as response:
                response.raise_for_status()
                with open(dst, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            if dst.exists() and dst.stat().st_size > 0:
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


def _make_executable(path: Path) -> None:
    """chmod +x on POSIX so an extracted binary is runnable."""
    if _is_windows():
        return
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def _run_pkg_cmd(cmd: list[str], timeout_s: float = 600.0) -> tuple[int, str]:
    """Run a pkg-manager command (no shell). Returns (rc, output), never raises."""
    import subprocess
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout_s)
        return proc.returncode, (proc.stdout or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


_FLATPAK_APP = "fr.handbrake.ghb"


def _write_flatpak_wrapper(tools_dir: Path) -> Path:
    """Shim tools/HandBrakeCLI -> `flatpak run`. The flatpak app isn't on PATH,
    so this keeps every call site invoking a bare HandBrakeCLI token."""
    wrapper = tools_dir / "HandBrakeCLI"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'exec flatpak run --command=HandBrakeCLI {_FLATPAK_APP} "$@"\n'
    )
    _make_executable(wrapper)
    return wrapper


def _install_handbrake_native(
    tools_dir: Path,
    status_cb: Callable[[str, str], None] | None = None,
) -> Path:
    """HandBrakeCLI on non-Windows via package manager (no static build exists):
    brew (macOS) or flatpak --user (Linux), no sudo. Raises with the manual
    command if no no-sudo route. Optional fallback tool -- callers don't block."""
    existing = shutil.which("HandBrakeCLI")
    if existing:
        return Path(existing)

    if _is_mac():
        if shutil.which("brew"):
            _emit_status(status_cb, "Installing HandBrakeCLI via Homebrew...")
            rc, _ = _run_pkg_cmd(["brew", "install", "handbrake"])
            found = shutil.which("HandBrakeCLI")
            if found:
                return Path(found)
            raise ToolInstallError(
                "brew ran but HandBrakeCLI is not on PATH. Install manually: "
                "`brew install handbrake` (or the CLI cask).")
        raise ToolInstallError(
            "Homebrew not found. Install HandBrakeCLI with: `brew install handbrake`.")

    # Linux: flatpak --user (no sudo). Never auto-run `sudo apt` -- would hang
    # a GUI launch on the password prompt.
    if shutil.which("flatpak"):
        _emit_status(status_cb, "Installing HandBrake via flatpak (--user)...")
        _run_pkg_cmd(["flatpak", "remote-add", "--if-not-exists", "--user",
                      "flathub", "https://flathub.org/repo/flathub.flatpakrepo"])
        rc, out = _run_pkg_cmd(["flatpak", "install", "--user", "-y",
                                "flathub", _FLATPAK_APP])
        info_rc, _ = _run_pkg_cmd(["flatpak", "info", _FLATPAK_APP], timeout_s=30.0)
        if info_rc == 0:
            return _write_flatpak_wrapper(Path(tools_dir))
        raise ToolInstallError(
            "flatpak install of HandBrake failed. Install manually: "
            "`flatpak install flathub fr.handbrake.ghb`, or "
            "`sudo apt install handbrake-cli` / `sudo dnf install HandBrake`.")

    raise ToolInstallError(
        "No package manager found for HandBrakeCLI. Install it with your "
        "distro's package manager, e.g. `sudo apt install handbrake-cli` "
        "or `sudo dnf install HandBrake`.")


def install_tool(
    *,
    name: str,
    tools_dir: Path,
    status_cb: Callable[[str, str], None] | None = None,
    retries: int = 3,
    connect_timeout_s: float = 10.0,
    read_timeout_s: float = 90.0,
) -> Path:
    # HandBrakeCLI: package-manager path off Windows (no static build).
    if str(name) == "HandBrakeCLI" and not _is_windows():
        return _install_handbrake_native(Path(tools_dir), status_cb)

    spec = TOOL_SPECS.get(str(name))
    if spec is None:
        raise ToolInstallError(
            f"No self-contained download for {name} on this platform.")

    tools_dir = Path(tools_dir)
    tools_dir.mkdir(parents=True, exist_ok=True)
    exe_path = tools_dir / spec.exe
    if exe_path.is_file():
        _emit_status(status_cb, f"{spec.name} already installed at: {exe_path}")
        return exe_path

    archive_path = tools_dir / f"{spec.name}.{spec.archive}.download"
    _emit_status(status_cb, f"Downloading {spec.name}...")
    _download_archive(
        spec.url,
        archive_path,
        retries=int(max(1, retries)),
        timeout=(float(connect_timeout_s), float(read_timeout_s)),
    )

    try:
        _verify_sha256(archive_path, spec.sha256)
        _emit_status(status_cb, f"Extracting {spec.name}...")
        if spec.archive == "tarxz":
            if not tarfile.is_tarfile(archive_path):
                raise ToolInstallError(f"Corrupted or invalid archive for {spec.name}.")
            extracted = _extract_binary_from_tarxz(archive_path, spec.exe, tools_dir)
        else:
            if not zipfile.is_zipfile(archive_path):
                raise ToolInstallError(f"Corrupted or invalid ZIP archive for {spec.name}.")
            extracted = _extract_binary_from_zip(archive_path, spec.exe, tools_dir)

        if extracted.resolve() != exe_path.resolve():
            exe_path.parent.mkdir(parents=True, exist_ok=True)
            if exe_path.exists():
                exe_path.unlink()
            extracted.replace(exe_path)
        _make_executable(exe_path)
        return exe_path
    finally:
        try:
            if archive_path.exists():
                archive_path.unlink()
        except OSError:
            pass
