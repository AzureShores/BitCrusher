from __future__ import annotations

import json
import os


def _ui_json_path():
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = os.getcwd()
    p = os.path.join(base, "user_settings")
    os.makedirs(p, exist_ok=True)
    return os.path.join(p, "ui.json")


def _save_theme_choice(name: str) -> None:

    path = _ui_json_path()
    try:
        data = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        if not isinstance(data, dict):
            data = {}
        data["theme"] = str(name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:

        pass

def _load_theme_choice(default_name: str = "Dark") -> str:

    path = _ui_json_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            v = data.get("theme")
            if isinstance(v, str) and v:
                return v
    except Exception:
        pass
    return str(default_name or "Dark")
