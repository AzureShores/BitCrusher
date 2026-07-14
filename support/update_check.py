"""
update_check.py — opt-in GitHub Releases check.

Peripheral network touchpoint, same tier as webhook.py and tool_installer.py:
never called from the encode/planning path, only ever from GUI startup (after
explicit user consent) or the CLI --check-updates flag. Every network call is
wrapped so failure (offline, rate-limited, DNS down) is silent and returns
None -- callers must treat that as "no opinion", never as "no update".
"""
from __future__ import annotations

import re

import requests

RELEASES_API_URL = "https://api.github.com/repos/AzureShores/BitCrusher/releases/latest"
_TIMEOUT_S = 5.0


def fetch_latest_release(timeout: float = _TIMEOUT_S) -> dict | None:
    """GET the latest GitHub release. Returns {"tag", "url", "notes"} or None
    on any failure -- network off, rate-limited, malformed response, etc."""
    try:
        resp = requests.get(
            RELEASES_API_URL,
            headers={"Accept": "application/vnd.github+json",
                    "User-Agent": "BitCrusher-update-check"},
            timeout=float(timeout))
        if resp.status_code != 200:
            return None
        data = resp.json()
        tag = str(data.get("tag_name") or "").strip()
        if not tag:
            return None
        return {"tag": tag,
               "url": str(data.get("html_url") or ""),
               "notes": str(data.get("body") or "")}
    except Exception:
        return None


def _version_tuple(v: str) -> tuple[int, ...]:
    v = str(v or "").strip().lstrip("vV")
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts) or (0,)


def is_newer(remote_tag: str, current: str) -> bool:
    """True when remote_tag's dotted version is greater than current's."""
    return _version_tuple(remote_tag) > _version_tuple(current)
