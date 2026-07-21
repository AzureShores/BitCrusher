#!/usr/bin/env python3
"""Build self-contained per-OS release bundles for BitCrusher.

A GitHub source zip ships without the tools/ binaries (they are gitignored),
so a fresh extract has no ffmpeg/ffprobe and every encode fails. This script
assembles a bundle per OS: a clean `git archive` of HEAD (the full source,
including tools/vmaf_models) plus the correct static ffmpeg/ffprobe (and
HandBrakeCLI on Windows), zipped as dist/BitCrusher-<ver>-<os>.zip.

Binary sources mirror support/tool_installer.py:
  - Windows : BtbN win64 gpl zip     (+ HandBrake 1.7.3 win-x86_64 zip)
  - Linux   : BtbN linux64 gpl tar.xz
  - macOS   : evermeet.cx ffmpeg/ffprobe zips (Intel; runs on ASi via Rosetta)

Windows binaries default to the already-vetted copies in ./tools; pass
--fresh-win to download them instead. Linux/macOS binaries are always fetched.

Usage:
  python build_bundle.py --os all          # win64 + linux64 + macos
  python build_bundle.py --os win          # just the local machine's target
  python build_bundle.py --version 1.2.0 --os linux
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

FF_WIN = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
          "latest/ffmpeg-master-latest-win64-gpl.zip")
HB_WIN = ("https://github.com/HandBrake/HandBrake/releases/download/"
          "1.7.3/HandBrakeCLI-1.7.3-win-x86_64.zip")
FF_LINUX = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
            "latest/ffmpeg-master-latest-linux64-gpl.tar.xz")
FF_MAC = "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
FP_MAC = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"

OS_SLUG = {"win": "win64", "linux": "linux64", "mac": "macos"}


def log(msg: str) -> None:
    print(f"[bundle] {msg}", flush=True)


def read_version() -> str:
    txt = (ROOT / "BitCrusherV9.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', txt)
    if not m:
        raise SystemExit("[bundle] could not read APP_VERSION from BitCrusherV9.py")
    return m.group(1)


def stage_source(stage: Path) -> None:
    """Export a clean tree of HEAD (tracked files only) into stage/."""
    stage.mkdir(parents=True, exist_ok=True)
    tar_path = stage.parent / "_src.tar"
    log("git archive HEAD -> clean source tree")
    with open(tar_path, "wb") as fh:
        subprocess.run(["git", "archive", "--format=tar", "HEAD"],
                       cwd=str(ROOT), stdout=fh, check=True)
    with tarfile.open(tar_path, "r:") as tar:
        try:
            tar.extractall(stage, filter="data")  # Py>=3.12: safe extraction
        except TypeError:
            tar.extractall(stage)
    tar_path.unlink(missing_ok=True)
    (stage / "tools").mkdir(exist_ok=True)


def download(url: str, dst: Path) -> None:
    log(f"download {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "BitCrusher-bundle"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dst, "wb") as out:
        shutil.copyfileobj(resp, out)
    if dst.stat().st_size == 0:
        raise SystemExit(f"[bundle] empty download: {url}")


def _shallowest(names, exe: str):
    hits = [n for n in names if Path(n).name.lower() == exe.lower()]
    if not hits:
        return None
    hits.sort(key=lambda n: len(Path(n).parts))
    return hits[0]


def extract_from_zip(zip_path: Path, exe: str, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as z:
        member = _shallowest(z.namelist(), exe)
        if not member:
            raise SystemExit(f"[bundle] {exe} not in {zip_path.name}")
        with z.open(member) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)


def extract_from_tarxz(tar_path: Path, exe: str, dest: Path) -> None:
    with tarfile.open(tar_path, "r:xz") as t:
        member = _shallowest([m.name for m in t.getmembers() if m.isfile()], exe)
        if not member:
            raise SystemExit(f"[bundle] {exe} not in {tar_path.name}")
        src = t.extractfile(member)
        with src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)


def place_win(tools: Path, fresh: bool, tmp: Path) -> None:
    local = ROOT / "tools"
    have_local = all((local / f"{n}.exe").is_file() for n in ("ffmpeg", "ffprobe"))
    if have_local and not fresh:
        for n in ("ffmpeg", "ffprobe", "HandBrakeCLI"):
            src = local / f"{n}.exe"
            if src.is_file():
                log(f"copy local tools/{n}.exe")
                shutil.copy2(src, tools / f"{n}.exe")
        return
    zf = tmp / "ff_win.zip"
    download(FF_WIN, zf)
    extract_from_zip(zf, "ffmpeg.exe", tools / "ffmpeg.exe")
    extract_from_zip(zf, "ffprobe.exe", tools / "ffprobe.exe")
    hb = tmp / "hb_win.zip"
    download(HB_WIN, hb)
    extract_from_zip(hb, "HandBrakeCLI.exe", tools / "HandBrakeCLI.exe")


def place_linux(tools: Path, tmp: Path) -> None:
    tx = tmp / "ff_linux.tar.xz"
    download(FF_LINUX, tx)
    for n in ("ffmpeg", "ffprobe"):
        out = tools / n
        extract_from_tarxz(tx, n, out)
        os.chmod(out, 0o755)


def place_mac(tools: Path, tmp: Path) -> None:
    for n, url in (("ffmpeg", FF_MAC), ("ffprobe", FP_MAC)):
        zf = tmp / f"{n}_mac.zip"
        download(url, zf)
        out = tools / n
        extract_from_zip(zf, n, out)
        os.chmod(out, 0o755)


def zip_bundle(stage: Path, out_zip: Path) -> None:
    log(f"zip -> {out_zip.name}")
    top = stage.name
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, f"{top}/{p.relative_to(stage)}")


def build_one(target: str, version: str, fresh_win: bool) -> Path:
    slug = OS_SLUG[target]
    log(f"=== building {slug} ===")
    workroot = DIST / f"stage-{slug}"
    if workroot.exists():
        shutil.rmtree(workroot)
    stage = workroot / f"BitCrusher-{version}"
    stage_source(stage)
    tools = stage / "tools"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if target == "win":
            place_win(tools, fresh_win, tmp)
        elif target == "linux":
            place_linux(tools, tmp)
        else:
            place_mac(tools, tmp)
    out_zip = DIST / f"BitCrusher-{version}-{slug}.zip"
    out_zip.unlink(missing_ok=True)
    zip_bundle(stage, out_zip)
    shutil.rmtree(workroot, ignore_errors=True)
    mb = out_zip.stat().st_size / (1024 * 1024)
    log(f"done {out_zip.name} ({mb:.0f} MB)")
    return out_zip


def main() -> int:
    ap = argparse.ArgumentParser(description="Build per-OS BitCrusher bundles.")
    ap.add_argument("--os", choices=["win", "linux", "mac", "all"], default="all")
    ap.add_argument("--version", default=None, help="override APP_VERSION")
    ap.add_argument("--fresh-win", action="store_true",
                    help="download Windows ffmpeg instead of copying ./tools")
    args = ap.parse_args()

    version = args.version or read_version()
    targets = ["win", "linux", "mac"] if args.os == "all" else [args.os]
    DIST.mkdir(exist_ok=True)

    built = []
    for t in targets:
        try:
            built.append(build_one(t, version, args.fresh_win))
        except Exception as exc:  # keep going; report at the end
            log(f"FAILED {t}: {exc}")
    log("built: " + ", ".join(p.name for p in built) if built else "nothing built")
    return 0 if len(built) == len(targets) else 1


if __name__ == "__main__":
    sys.exit(main())
