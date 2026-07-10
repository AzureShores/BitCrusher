from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

# Error codes taxonomy
E_IO_OVERHEAD = "E_IO_OVERHEAD"
E_VAL_OVERHEAD = "E_VAL_OVERHEAD"

_DEFAULT = {
    "containers": {
        "mp4": 1.020,
        "mkv": 1.008,
        "webm": 1.010,
    }
}


def _settings_path(settings_dir: Optional[str]) -> Optional[Path]:
    if not settings_dir:
        return None
    try:
        p = Path(settings_dir) / "overhead.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return None


def load_overhead(settings_dir: Optional[str]) -> Dict[str, Any]:
    p = _settings_path(settings_dir)
    if not p or not p.exists():
        return dict(_DEFAULT)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        return dict(_DEFAULT)
    return dict(_DEFAULT)


def save_overhead(settings_dir: Optional[str], data: Dict[str, Any]) -> None:
    p = _settings_path(settings_dir)
    if not p:
        return
    try:
        p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        return


def _key(container: str) -> str:
    return (container or "mp4").lower().strip(".")


def get_overhead_factor(settings_dir: Optional[str], container: str, width: int = 0, height: int = 0, fps: float = 0.0) -> float:
    # width/height/fps included for future compatibility; currently unused but deterministic
    data = load_overhead(settings_dir)
    c = _key(container)
    try:
        v = float(((data.get("containers") or {}).get(c)) or ((data.get("containers") or {}).get("mp4")) or 1.02)
    except Exception:
        v = 1.02
    if not (1.0 <= v <= 1.10):
        v = 1.02
    return float(v)


def update_overhead(settings_dir: Optional[str],
                    container: str,
                    predicted_core_bytes: int,
                    actual_bytes: int,
                    confidence: float = 0.5) -> None:
    """
    confidence-weighted EWMA update for container overhead factor.
    predicted_core_bytes: bytes excluding overhead (video+audio payload).
    """
    if predicted_core_bytes <= 0 or actual_bytes <= 0:
        return
    c = _key(container)
    ratio = float(actual_bytes) / float(max(1, int(predicted_core_bytes)))
    ratio = float(max(1.0, min(1.10, ratio)))

    try:
        conf = float(confidence)
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    w = float(max(0.05, min(0.35, 0.10 + 0.25 * conf)))

    data = load_overhead(settings_dir)
    containers = data.get("containers")
    if not isinstance(containers, dict):
        containers = {}
        data["containers"] = containers

    prev = float(containers.get(c) or containers.get("mp4") or 1.02)
    prev = float(max(1.0, min(1.10, prev)))
    new = prev * (1.0 - w) + ratio * w
    containers[c] = float(max(1.0, min(1.10, new)))

    save_overhead(settings_dir, data)
