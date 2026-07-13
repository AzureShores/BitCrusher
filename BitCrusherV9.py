
import io
import sys
import json
import logging
import math
import os
import platform
import re
import shutil
import subprocess
import tempfile
import glob
from probe_predictor import predict_crf_and_bitrate
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from fractions import Fraction
from pathlib import Path
from tkinterdnd2 import TkinterDnD, DND_FILES
from ui_aesthetics import init_aesthetics, animated_retheme, open_theme_lab
import yt_dlp
import queue
import webbrowser

import numpy as np
import psutil
import requests
from PIL import Image, ImageTk
from plyer import notification
from win10toast import ToastNotifier
from datetime import datetime
import argparse

try:
    os.makedirs("logs", exist_ok=True)
except Exception:
    pass
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(os.path.join("logs", "bitcrusher.log"),
                            encoding="utf-8",
                            mode="a")
    ]
)

# Many status messages contain emoji; cp1252 consoles would otherwise crash
# with UnicodeEncodeError on the first print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Serializes the probe/plan stage across concurrent jobs: the planning helpers
# still communicate through process-global BC_* env vars (BC_CURRENT_INPUT,
# BC_PICKED_ENCODER, BC_SCENE_SPLIT, ...), so only one job may plan at a time.
# Encodes (the long part) run outside this lock.
_PLANNING_LOCK = threading.Lock()

# Serializes learning/cache appends (jsonl files interleave on Windows).
_STATS_LOCK = threading.Lock()


import sys, os, traceback

def resource_path(rel_path: str) -> str:
    
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, rel_path)


_BOOT_PHASE = True  # only show popup during true startup failures

def _install_crash_handler():
    import sys, threading, tkinter, warnings, faulthandler

    warnings.simplefilter("default")
    logging.captureWarnings(True)

    def _excepthook(exc_type, exc, tb):
        LOG.critical("Uncaught exception", exc_info=(exc_type, exc, tb))
    sys.excepthook = _excepthook

    def _thread_excepthook(args):
        LOG.critical("Uncaught thread exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = _thread_excepthook

    def _tk_cb_exc(_root, exc, val, tb):
        LOG.error("Tkinter callback exception", exc_info=(exc, val, tb))
    tkinter.Tk.report_callback_exception = _tk_cb_exc  # type: ignore

    def _unraisable_hook(unraisable):
        try:
            import traceback
            msg = getattr(unraisable, "err_msg", "") or "Unraisable exception"
            LOG.error(f"{msg}: {unraisable.exc_value}", exc_info=(type(unraisable.exc_value), unraisable.exc_value, unraisable.exc_traceback))
        except Exception:
            pass
    sys.unraisablehook = _unraisable_hook

    try:
        fh_path = os.path.join(_LOG_DIR, "faulthandler.log")
        fh_file = open(fh_path, "a", encoding="utf-8")
        faulthandler.enable(file=fh_file, all_threads=True)
    except Exception:
        pass





def _ui_json_path():
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = os.getcwd()
    p = os.path.join(base, "user_settings")
    os.makedirs(p, exist_ok=True)
    return os.path.join(p, "ui.json")

import os, sys, json, subprocess

def _i18n_dir():
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = os.getcwd()
    d = os.path.join(base, "user_settings", "i18n")
    os.makedirs(d, exist_ok=True)
    return d

def _open_folder(path: str):
    try:
        if os.name == "nt":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass

LANG_BUILTIN = {
    "en": {
        "menu_language": "Language",
        "menu.file":"File","menu.settings":"Settings","menu.themes":"Themes","menu.guide":"Guide","menu.view":"View",
        "menu.exit":"Exit","menu.configure_paths":"Configure Paths","menu.save_profile":"Save Profile","menu.load_profile":"Load Profile",
        "menu.clear_queue":"Clear Queue","menu.dashboard":"Dashboard","menu.user_guide":"User Guide",
        "menu.theme_lab":"Theme Lab...","menu.save_theme":"Save Current Theme...","menu.load_theme":"Load Theme JSON...",
        "menu.open_i18n_folder":"Open i18n Folder...","menu.export_lang_templates":"Export Language Templates...","menu.language_manager":"Language Manager...",

        "lbl.preset":"Preset:","lbl.target_size":"Target Size (MB):","lbl.queue":"Queue","lbl.preview":"Preview",
        "lbl.save_to":"Save to:",
        "panel.webhook":"Webhook","lbl.webhook_url":"Discord/Webhook URL",
        "panel.watcher":"Folder Watcher","lbl.enable_watcher":"Enable watcher","panel.profiles":"Profiles",
        "panel.advanced":"Advanced Options",
        "lbl.encoder":"Encoder:","lbl.manual_crf":"Manual CRF:","lbl.prefix":"Prefix:","lbl.audio":"Audio:",

        "btn.add_files":"Add Files...","btn.remove_selected":"Remove Selected","btn.remove":"Remove Selected","btn.clear":"Clear",
        "btn.move_up":"Move Up","btn.move_down":"Move Down",
        "btn.start":"Start Compression","btn.stop":"Stop","btn.open_save":"Open Save Folder",
        "btn.user_guide":"User Guide","btn.browse":"Browse...","btn.save":"Save","btn.load":"Load",

        "title.open_save_folder":"Open Save Folder","title.cancel":"Cancel",

        "unreal.title": "Unrealistic target size",
        "unreal.header": "Requested size is too small for acceptable quality.",
        "unreal.original": "Original","unreal.target": "Target",
        "unreal.why": "Why this breaks quality:",
        "unreal.why.v": "- Video bitrate plummets -> blocky macro-artifacts & smearing",
        "unreal.why.a": "- Audio starved of bits -> metallic/warbly sound",
        "unreal.why.m": "- High motion or film grain needs more bits than static scenes",
        "unreal.better": "Better options:",
        "unreal.opt.aim": "- Aim for 10-20% of the original size (not 1-2%)",
        "unreal.opt.scale": "- Downscale resolution and/or reduce frame rate",
        "unreal.opt.codec": "- Use HEVC (x265) or AV1 with a sensible CRF",
    }
}

LANG_BUILTIN.update({
    "es": { "menu_language":"Idioma","menu.file":"Archivo","menu.settings":"Ajustes","menu.themes":"Temas","menu.guide":"Guía","menu.view":"Vista",
            "menu.exit":"Salir","menu.configure_paths":"Configurar rutas","menu.save_profile":"Guardar perfil","menu.load_profile":"Cargar perfil",
            "menu.clear_queue":"Vaciar cola","menu.dashboard":"Panel","menu.user_guide":"Guía del usuario",
            "menu.theme_lab":"Laboratorio de temas…","menu.save_theme":"Guardar tema actual…","menu.load_theme":"Cargar tema JSON…",
            "menu.open_i18n_folder":"Abrir carpeta i18n…","menu.export_lang_templates":"Exportar plantillas de idioma…",
            "lbl.preset":"Preajuste:","lbl.target_size":"Tamaño objetivo (MB):","lbl.queue":"Cola","lbl.preview":"Vista previa","lbl.save_to":"Guardar en:",
            "panel.webhook":"Webhook","lbl.webhook_url":"URL de Discord/Webhook","panel.watcher":"Monitor de carpeta","lbl.enable_watcher":"Activar monitor",
            "panel.profiles":"Perfiles","panel.advanced":"Opciones avanzadas","lbl.encoder":"Codificador:","lbl.manual_crf":"CRF manual:",
            "lbl.prefix":"Prefijo:","lbl.audio":"Audio:","btn.add_files":"Añadir archivos…","btn.remove_selected":"Eliminar seleccionados",
            "btn.clear":"Limpiar","btn.move_up":"Mover ↑","btn.move_down":"Mover ↓","btn.start":"Comenzar compresión","btn.stop":"Detener",
            "btn.open_save":"Abrir carpeta de salida","btn.user_guide":"Guía de usuario","btn.browse":"Examinar…","btn.save":"Guardar","btn.load":"Cargar",
            "title.open_save_folder":"Abrir carpeta de salida","title.cancel":"Cancelar",
            "unreal.title":"Tamaño objetivo irreal","unreal.header":"El tamaño solicitado es demasiado pequeño para una calidad aceptable.",
            "unreal.original":"Original","unreal.target":"Objetivo","unreal.why":"Por qué arruina la calidad:",
            "unreal.why.v": "- Video bitrate plummets -> blocky macro-artifacts & smearing","unreal.why.a": "- Audio starved of bits -> metallic/warbly sound",
            "unreal.why.m": "- High motion or film grain needs more bits than static scenes","unreal.better":"Opciones mejores:",
            "unreal.opt.aim": "- Aim for 10-20% of the original size (not 1-2%)","unreal.opt.scale": "- Downscale resolution and/or reduce frame rate",
            "unreal.opt.codec": "- Use HEVC (x265) or AV1 with a sensible CRF"},
    "fr": { "menu_language":"Langue","menu.file":"Fichier","menu.settings":"Paramètres","menu.themes":"Thèmes","menu.guide":"Guide","menu.view":"Affichage" },
    "de": { "menu_language":"Sprache","menu.file":"Datei","menu.settings":"Einstellungen","menu.themes":"Themen","menu.guide":"Anleitung","menu.view":"Ansicht" },
    "pt": { "menu_language":"Idioma","menu.file":"Arquivo","menu.settings":"Ajustes","menu.themes":"Temas","menu.guide":"Guia","menu.view":"Exibir" },
    "it": { "menu_language":"Lingua","menu.file":"File","menu.settings":"Impostazioni","menu.themes":"Temi","menu.guide":"Guida","menu.view":"Vista" },
    "nl": { "menu_language":"Taal","menu.file":"Bestand","menu.settings":"Instellingen","menu.themes":"Thema's","menu.guide":"Handleiding","menu.view":"Beeld" },
    "pl": { "menu_language":"Język","menu.file":"Plik","menu.settings":"Ustawienia","menu.themes":"Motywy","menu.guide":"Przewodnik","menu.view":"Widok" },
    "tr": { "menu_language":"Dil","menu.file":"Dosya","menu.settings":"Ayarlar","menu.themes":"Temalar","menu.guide":"Kılavuz","menu.view":"Görünüm" },
    "ru": { "menu_language":"Язык","menu.file":"Файл","menu.settings":"Настройки","menu.themes":"Темы","menu.guide":"Справка","menu.view":"Вид" },
    "uk": { "menu_language":"Мова","menu.file":"Файл","menu.settings":"Налаштування","menu.themes":"Теми","menu.guide":"Довідник","menu.view":"Вигляд" },
    "ar": { "menu_language":"اللغة","menu.file":"ملف","menu.settings":"الإعدادات","menu.themes":"الثيمات","menu.guide":"الدليل","menu.view":"عرض" },
    "he": { "menu_language":"שפה","menu.file":"קובץ","menu.settings":"הגדרות","menu.themes":"ערכות נושא","menu.guide":"מדריך","menu.view":"תצוגה" },
    "fa": { "menu_language":"زبان","menu.file":"فایل","menu.settings":"تنظیمات","menu.themes":"تم‌ها","menu.guide":"راهنما","menu.view":"نمایش" },
    "hi": { "menu_language":"भाषा","menu.file":"फ़ाइल","menu.settings":"सेटिंग्स","menu.themes":"थीम्स","menu.guide":"मार्गदर्शिका","menu.view":"दृश्य" },
    "bn": { "menu_language":"ভাষা","menu.file":"ফাইল","menu.settings":"সেটিংস","menu.themes":"থিম","menu.guide":"গাইড","menu.view":"ভিউ" },
    "id": { "menu_language":"Bahasa","menu.file":"Berkas","menu.settings":"Pengaturan","menu.themes":"Tema","menu.guide":"Panduan","menu.view":"Tampilan" },
    "ms": { "menu_language":"Bahasa","menu.file":"Fail","menu.settings":"Tetapan","menu.themes":"Tema","menu.guide":"Panduan","menu.view":"Paparan" },
    "vi": { "menu_language":"Ngôn ngữ","menu.file":"Tệp","menu.settings":"Cài đặt","menu.themes":"Chủ đề","menu.guide":"Hướng dẫn","menu.view":"Xem" },
    "th": { "menu_language":"ภาษา","menu.file":"ไฟล์","menu.settings":"การตั้งค่า","menu.themes":"ธีม","menu.guide":"คู่มือ","menu.view":"มุมมอง" },
    "ja": { "menu_language":"言語","menu.file":"ファイル","menu.settings":"設定","menu.themes":"テーマ","menu.guide":"ガイド","menu.view":"表示" },
    "ko": { "menu_language":"언어","menu.file":"파일","menu.settings":"설정","menu.themes":"테마","menu.guide":"가이드","menu.view":"보기" },
    "zh": { "menu_language":"语言","menu.file":"文件","menu.settings":"设置","menu.themes":"主题","menu.guide":"指南","menu.view":"视图" },
    "zh_TW": { "menu_language":"語言","menu.file":"檔案","menu.settings":"設定","menu.themes":"主題","menu.guide":"指南","menu.view":"檢視" },
})

LANG_CODES = [
    ("en","English"),("es","Spanish"),("fr","French"),("de","German"),("pt","Portuguese"),
    ("it","Italian"),("nl","Dutch"),("pl","Polish"),("tr","Turkish"),("ru","Russian"),
    ("uk","Ukrainian"),("ar","Arabic"),("he","Hebrew"),("fa","Persian"),("hi","Hindi"),
    ("bn","Bengali"),("id","Indonesian"),("ms","Malay"),("vi","Vietnamese"),("th","Thai"),
    ("ja","Japanese"),("ko","Korean"),("zh","Chinese (Simplified)"),("zh_TW","Chinese (Traditional)")
]

LANG = {}
LANG_CODE_NAME = {code: name for code, name in LANG_CODES}
LANG_COVERAGE = {}
LANG_SOURCE = {}
LANG_DISPLAY = {}

def _language_codes_ordered():
    ordered = [c for c, _ in LANG_CODES]
    for c in sorted(LANG.keys()):
        if c not in ordered:
            ordered.append(c)
    return ordered

def _language_menu_label(code: str) -> str:
    base_name = LANG_DISPLAY.get(code) or LANG_CODE_NAME.get(code, code)
    safe_name = _normalize_text(base_name)
    if _mojibake_score(safe_name) > 0:
        safe_name = LANG_CODE_NAME.get(code, code)
    cov = int(LANG_COVERAGE.get(code, 0))
    if code == "en":
        return f"{safe_name} ({code})"
    return f"{safe_name} ({code}) [{cov}%]"
def _load_language_choice(default_code: str = "en") -> str:
    path = _ui_json_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            v = data.get("language")
            if isinstance(v, str) and v:
                return v
    except Exception:
        pass
    return str(default_code or "en")

def _save_language_choice(code: str) -> None:
    path = _ui_json_path()
    try:
        data = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        if not isinstance(data, dict):
            data = {}
        data["language"] = str(code or "en")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass

def _export_lang_templates(codes=None):

    if not codes:
        codes = [c for c,_ in LANG_CODES if c != "en"]
    base = LANG_BUILTIN["en"]
    outdir = _i18n_dir()
    for code in codes:
        try:
            path = os.path.join(outdir, f"{code}.json")
            existing = {}
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f) or {}
            if not isinstance(existing, dict):
                existing = {}

            templ = {
                "__code__": code,
                "__name__": str(existing.get("__name__", LANG_CODE_NAME.get(code, code))),
                "__note__": "Translate values, keep keys unchanged.",
            }
            for k, v in base.items():
                cur = existing.get(k, v)
                templ[k] = _normalize_text(cur) if isinstance(cur, str) else v

            with open(path, "w", encoding="utf-8") as f:
                json.dump(templ, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

def _load_lang_packs():
    def _sanitize_pack(pack: dict, fallback: dict | None = None) -> dict:
        out = {}
        base = fallback or {}
        for k, v in (pack or {}).items():
            if not isinstance(k, str):
                continue
            if isinstance(v, str):
                fixed = _normalize_text(v)
                # If text still looks broken, keep a clean fallback value.
                if _mojibake_score(fixed) > 0:
                    if isinstance(base.get(k), str):
                        fixed = _normalize_text(base.get(k))
                    elif isinstance(LANG_BUILTIN.get("en", {}).get(k), str):
                        fixed = _normalize_text(LANG_BUILTIN["en"][k])
                out[k] = fixed
            else:
                out[k] = v
        return out

    # Use English built-in as canonical base.
    base_en = _sanitize_pack(dict(LANG_BUILTIN.get("en", {})), LANG_BUILTIN.get("en", {}))

    file_packs = {}
    file_meta = {}
    discovered_codes = []
    d = _i18n_dir()
    try:
        for fn in os.listdir(d):
            if not fn.lower().endswith(".json"):
                continue
            code = os.path.splitext(fn)[0]
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            if not isinstance(raw, dict):
                continue

            file_meta[code] = {
                "name": raw.get("__name__", ""),
            }
            pack = {k: v for k, v in raw.items() if not str(k).startswith("__")}
            file_packs[code] = pack
            if code not in discovered_codes:
                discovered_codes.append(code)
    except Exception:
        pass

    all_codes = [c for c, _ in LANG_CODES]
    for code in discovered_codes:
        if code not in all_codes:
            all_codes.append(code)
    if "en" not in all_codes:
        all_codes.insert(0, "en")

    LANG.clear()
    LANG_COVERAGE.clear()
    LANG_SOURCE.clear()
    LANG_DISPLAY.clear()

    for code in all_codes:
        LANG[code] = dict(base_en)
        LANG_SOURCE[code] = "fallback"
        LANG_DISPLAY[code] = LANG_CODE_NAME.get(code, code)

    LANG["en"] = dict(base_en)
    LANG_SOURCE["en"] = "builtin"
    LANG_DISPLAY["en"] = LANG_CODE_NAME.get("en", "English")

    # Merge external language files on top of English base.
    for code, pack in file_packs.items():
        merged = dict(LANG.get(code, base_en))
        merged.update(_sanitize_pack(pack, merged))
        LANG[code] = merged
        LANG_SOURCE[code] = "file"

    # Display names and coverage stats.
    total_keys = max(1, len(base_en))
    for code in all_codes:
        meta_name = file_meta.get(code, {}).get("name", "")
        fallback_name = LANG_CODE_NAME.get(code, code)
        shown = _normalize_text(meta_name) if isinstance(meta_name, str) else ""
        if (not shown.strip()) or _mojibake_score(shown) > 0:
            shown = fallback_name
        LANG_DISPLAY[code] = shown

        if code == "en":
            LANG_COVERAGE[code] = 100
            continue
        pack = LANG.get(code, {})
        translated = 0
        for k, en_v in base_en.items():
            v = _normalize_text(pack.get(k, en_v))
            if v != _normalize_text(en_v):
                translated += 1
        LANG_COVERAGE[code] = int(round((translated * 100.0) / total_keys))

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

import os, sys, time, platform, traceback, logging, threading, subprocess
from logging.handlers import RotatingFileHandler
from smart_rate import learn_from_result, guardrail_adjust, load_stats, save_stats, update_overshoot
from ai_advisor import (choose_bitrates_advised as choose_bitrates,
                        cache_store_advised as cache_store,
                        cache_lookup_advised as cache_lookup,
                        advisor_preview_for_gui)
from ml_heuristics import analyze_and_advise, extract_media_features
from size_controller import SizeController
from encoder_profiles import select_profile
from planner import PlanInputs, plan as plan_encode
from text_utils import _EMOJI_RE, _mojibake_score, _normalize_text, format_bytes
from webhook import DiscordWebhookClient, _format_webhook_summary, _post_webhook_hardened
from ffmpeg_exec import (si, NO_WIN, _render_cmd, _tail, _orig_check_output,
                         _check_output_logged, _sp_check_output, _ffmpeg_has_filter,
                         set_ffmpeg_path as _set_ffmpeg_exec_path)

def _ensure_dir(p):
    try: os.makedirs(p, exist_ok=True)
    except Exception: pass
    return p

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = _ensure_dir(os.path.join(_SCRIPT_DIR, "logs"))

class _MojibakeFilter(logging.Filter):
    def filter(self, record):
        try:
            fixed = _normalize_text(record.getMessage())
            if fixed != record.getMessage():
                record.msg = fixed
                record.args = ()
        except Exception:
            pass
        return True

def _mk_logger():
    level_name = os.environ.get("BITCRUSHER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger = logging.getLogger("BitCrusher")
    logger.setLevel(level)
    logger.propagate = False
    logger.addFilter(_MojibakeFilter())

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    fh = logging.handlers.RotatingFileHandler(os.path.join("logs", "bitcrusher.log"),
                                              maxBytes=5*1024*1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(level)
    logger.addHandler(fh)

    ERR_LOG_PATH = os.path.join(_LOG_DIR, "errors.log")
    err_fh = logging.handlers.RotatingFileHandler(
        ERR_LOG_PATH, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    err_fh.setLevel(logging.ERROR)
    err_fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(threadName)s | %(funcName)s:%(lineno)d | %(message)s"))
    logger.addHandler(err_fh)

    class _JSONErrorHandler(logging.Handler):
        def __init__(self, path):
            super().__init__(level=logging.ERROR)
            self._path = path
        def emit(self, record):
            try:
                import json, traceback, time
                rec = {
                    "ts": time.time(),
                    "level": record.levelname,
                    "name": record.name,
                    "process": record.process,
                    "thread": record.threadName,
                    "func": record.funcName,
                    "line": record.lineno,
                    "msg": record.getMessage(),
                }
                if record.exc_info:
                    rec["exc"] = "".join(traceback.format_exception(*record.exc_info))
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass

    ERR_JSONL_PATH = os.path.join(_LOG_DIR, "errors.jsonl")
    logger.addHandler(_JSONErrorHandler(ERR_JSONL_PATH))


    if os.environ.get("BITCRUSHER_LOG_CONSOLE", "0") == "1":
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        ch.setLevel(level)
        logger.addHandler(ch)

    return logger


LOG = _mk_logger()

class _WPARAMFilter(logging.Filter):
    def filter(self, record):
        msg = str(record.getMessage())
        return False if "WPARAM is simple" in msg else True

try:
    LOG.addFilter(_WPARAMFilter())
except Exception:
    pass


def _log_env_banner():
    try:
        LOG.info("======== BitCrusher start ========")
        LOG.info("Python: %s", sys.version.replace("\n", " "))
        LOG.info("Platform: %s %s (%s)", platform.system(), platform.release(), platform.machine())
        LOG.info("Executable: %s", sys.executable)
        LOG.info("CWD: %s", os.getcwd())
        def _first_line(cmd):
            try:
                p = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, timeout=4)
                out = (p.stdout or "").splitlines()
                return out[0] if out else ""
            except Exception:
                return ""
        for tool in ("HandBrakeCLI", "ffmpeg", "ffprobe"):
            v = _first_line([tool, "-h"]) or _first_line([tool, "-version"])
            if v:
                LOG.info("%s: %s", tool, v)
    except Exception:
        LOG.exception("Failed to log environment banner")

_log_env_banner()

def _format_exc(exc_type, exc, tb):
    return "".join(traceback.format_exception(exc_type, exc, tb))

def _excepthook(exc_type, exc, tb):
    if exc_type is TypeError and "WPARAM is simple" in str(exc):

        LOG.debug("Suppressed benign WPARAM TypeError")
        return
    LOG.critical("UNHANDLED EXCEPTION\n%s", _format_exc(exc_type, exc, tb))
    try:
        sys.__excepthook__(exc_type, exc, tb)
    except Exception:
        pass

sys.excepthook = _excepthook


def _thread_excepthook(args):
    try:
        name = getattr(args.thread, "name", "") or ""
        msg  = str(getattr(args, "exc_value", ""))

        if ("_show_toast" in name) or ("win10toast" in (getattr(args.thread, "__module__", "") or "")) \
           or ("Shell_NotifyIcon" in msg) or ("DestroyWindow" in msg) \
           or ("WPARAM is simple" in msg):
            LOG.debug("Suppressed toast/thread noise: %s | %s", name, msg)
            return

        LOG.critical(
            "UNHANDLED THREAD EXCEPTION (name=%s)\n%s",
            name,
            _format_exc(args.exc_type, args.exc_value, args.exc_traceback),
        )
    except Exception:
        pass


def _patch_tk_report_callback_exception():
    try:
        import tkinter as _tk
        _orig = _tk.Tk.report_callback_exception
        def _report(self, exc, val, tb):
            try:
                if exc is TypeError and "WPARAM is simple" in str(val):
                    LOG.debug("Suppressed benign WPARAM TypeError in Tk callback")
                    return
                LOG.error("Tkinter callback exception\n%s", _format_exc(exc, val, tb))
            except Exception:
                pass
            try:
                return _orig(self, exc, val, tb)
            except Exception:
                pass
            except Exception:
                pass
        _tk.Tk.report_callback_exception = _report
    except Exception:
        LOG.debug("Tkinter not available or already patched", exc_info=True)

_patch_tk_report_callback_exception()

_orig_run = subprocess.run

def _run_logged(cmd, *args, **kwargs):
    t0 = time.time()
    text_mode = kwargs.get("text", False)
    capture = (kwargs.get("stdout") is subprocess.PIPE
               or kwargs.get("stderr") is subprocess.PIPE
               or kwargs.get("capture_output") is True
               or text_mode)

    try:

        _parts = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
        _lp = [str(x).lower() for x in _parts]

        _is_ffmpeg = any(("ffmpeg" in p) for p in _lp)
        _asks_for_frame = any((flag in _lp) for flag in ("-frames:v", "-vframes"))
        _has_vf = "-vf" in _lp
        # -------- scene-zones injection (x264) ----------
        try:
            _xparams = os.environ.get("BC_X264_PARAMS", "")
            _qpfile  = os.environ.get("BC_QPFILE", "")

            # HandBrakeCLI path (x264)
            if any(("handbrakecli" in p) for p in _lp) and any(("x264" in p) for p in _lp):
                if _xparams and "-x264-params" not in _lp and "--encoder-options" not in _lp:
                    _parts.extend(["-x264-params", _xparams])
                if _qpfile and os.path.exists(_qpfile) and "--qpfile" not in _lp:
                    _parts.extend(["--qpfile", _qpfile])
                cmd = _parts

            # ffmpeg path (libx264)
            elif _is_ffmpeg and ("libx264" in _lp or ("-c:v" in _lp and "libx264" in _lp)):
                if _xparams and "-x264-params" not in _lp:
                    _parts.extend(["-x264-params", _xparams])
                if _qpfile and os.path.exists(_qpfile) and "-qpfile" not in _lp:
                    _parts.extend(["-qpfile", _qpfile])
                cmd = _parts
        except Exception:
            pass
        # -----------------------------------------------
        _in_idx = next((i for i, v in enumerate(_lp) if v == "-i"), -1)
        _in_path = str(_parts[_in_idx + 1]) if (_in_idx != -1 and _in_idx + 1 < len(_parts)) else ""

        _audio_exts = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".opus", ".ogg"}
        _is_audio_only = os.path.splitext(_in_path)[1].lower() in _audio_exts

        if _is_ffmpeg and (_asks_for_frame or _has_vf) and _is_audio_only:

            LOG.info("Skipping video-frame extraction on audio-only input: %s", _render_cmd(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
    except Exception:
        pass

    try:
        res = _orig_run(cmd, *args, **kwargs)

    except Exception as e:
        LOG.error("subprocess.run raised exception for: %s\n%s", _render_cmd(cmd), repr(e))
        raise
    dt = time.time() - t0
    try:
        LOG.debug("CMD: %s", _render_cmd(cmd))
        LOG.debug("RET: %s in %.2fs", res.returncode, dt)
        if capture:
            if hasattr(res, "stdout") and res.stdout:
                LOG.debug("STDOUT (tail):\n%s", _tail(res.stdout if isinstance(res.stdout, str)
                                                     else res.stdout.decode("utf-8", "ignore"), 50))
            if hasattr(res, "stderr") and res.stderr:
                LOG.debug("STDERR (tail):\n%s", _tail(res.stderr if isinstance(res.stderr, str)
                                                     else res.stderr.decode("utf-8", "ignore"), 120))
        if res.returncode != 0:
            try:
                _parts = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
                _lp = [str(x).lower() for x in _parts]
                if any("ffmpeg" in p for p in _lp) and any(flag in _lp for flag in ("-frames:v", "-vframes")):
                    LOG.warning("Command nonzero (rc=%s): %s", res.returncode, _render_cmd(cmd))
                else:
                    try:
                        _parts = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
                        _lp = [str(x).lower() for x in _parts]
                        if any("ffmpeg" in p for p in _lp) and any(flag in _lp for flag in ("-frames:v", "-vframes")):
                            LOG.warning("Command nonzero (rc=%s): %s", res.returncode, _render_cmd(cmd))
                        else:
                            LOG.error("Command failed (rc=%s): %s", res.returncode, _render_cmd(cmd))
                    except Exception:
                        LOG.error("Command failed (rc=%s): %s", res.returncode, _render_cmd(cmd))
            except Exception:
                            try:
                                _parts = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
                                _lp = [str(x).lower() for x in _parts]
                                if any("ffmpeg" in p for p in _lp) and any(flag in _lp for flag in ("-frames:v", "-vframes")):
                                    LOG.warning("Command nonzero (rc=%s): %s", res.returncode, _render_cmd(cmd))
                                else:
                                    LOG.error("Command failed (rc=%s): %s", res.returncode, _render_cmd(cmd))
                            except Exception:
                                LOG.error("Command failed (rc=%s): %s", res.returncode, _render_cmd(cmd))
    except Exception:
        pass
    return res

def _ffmpeg_emergency_encode(
    input_path: str,
    output_path: str,
    *,
    encoder: str | None,
    bitrate: int | None,
    crf: int | None,
    width: int | None,
    fps: float | None,
    audio_bitrate: int | None,
    audio_copy: bool,
    tune: str | None = None,
) -> bool:
    """
    Last-resort direct ffmpeg path when HandBrake two-pass and CRF fallback both fail.
    Keeps arguments minimal and widely supported.
    """
    try:
        vmap = {
            "x264": "libx264", "h264": "libx264", "libx264": "libx264",
            "x265": "libx265", "hevc": "libx265", "libx265": "libx265",
            "h264_nvenc": "h264_nvenc", "hevc_nvenc": "hevc_nvenc", "av1_nvenc": "av1_nvenc",
            "h264_qsv": "h264_qsv", "hevc_qsv": "hevc_qsv", "av1_qsv": "av1_qsv",
            "av1": "libaom-av1", "svt_av1": "libsvtav1", "libaom-av1": "libaom-av1", "libsvtav1": "libsvtav1",
        }
        vcodec = vmap.get((encoder or "").lower(), "libx264")
        # sanitize tune for libx265 (avoid forcing grain on generic film/animation content)
        if tune:
            _tn = str(tune).lower()
            if vcodec == "libx265" and _tn in ("film", "animation", "stillimage"):
                tune = None
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", input_path]

        if fps:
            cmd += ["-r", f"{fps}"]

        vf = []
        if width:
            try:
                vf.append(f"scale={int(width)}:-2")
            except Exception:
                pass
        if vf:
            cmd += ["-vf", ",".join(vf)]

        cmd += ["-c:v", vcodec]
        if bitrate and int(bitrate) > 0:
            b = int(bitrate)
            cmd += ["-b:v", str(b), "-maxrate", str(int(b * 1.15)), "-bufsize", str(int(b * 2))]
        elif crf is not None:
            cmd += ["-crf", str(int(crf))]
            if vcodec in ("libx264", "libx265"):
                cmd += ["-preset", "medium"]

        if audio_copy:
            cmd += ["-c:a", "copy"]
        else:
            if audio_bitrate and int(audio_bitrate) > 0:
                cmd += ["-c:a", "aac", "-b:a", str(int(audio_bitrate))]
            else:
                cmd += ["-an"]

        cmd += ["-movflags", "+faststart", output_path]

        res = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return int(getattr(res, "returncode", 1) or 0) == 0
    except Exception:
        return False

def _detect_reencoding_risk(source_codec: str, target_encoder: str,
                             source_bps: int, target_bps: int) -> str | None:
    """
    Returns a warning message if the user is re-encoding an already-compressed source
    with the same codec at a significantly lower bitrate (generation loss risk).
    Returns None if no concern.
    """
    _codec_family: dict[str, str] = {
        "h264": "h264", "avc": "h264", "libx264": "h264",
        "hevc": "h265", "h265": "h265", "libx265": "h265",
        "av1": "av1", "libaom-av1": "av1", "libsvtav1": "av1",
        "vp9": "vp9", "libvpx-vp9": "vp9",
    }
    src_fam = _codec_family.get((source_codec or "").lower().replace("-", ""), "")
    tgt_raw = (target_encoder or "").lower()
    tgt_fam = ""
    for k, v in _codec_family.items():
        if k in tgt_raw:
            tgt_fam = v
            break
    if not src_fam or not tgt_fam or src_fam != tgt_fam:
        return None  # different codec families — transcoding is expected, no special warning
    if source_bps <= 0 or target_bps <= 0:
        return None
    if target_bps < source_bps * 0.75:
        drop_pct = int((1.0 - target_bps / source_bps) * 100)
        src_kbps = source_bps // 1000
        return (
            f"Source is already {source_codec.upper()} at {src_kbps} kbps. "
            f"Re-encoding to the same codec at -{drop_pct}% bitrate will cause "
            f"generation loss (compounding block/ring artifacts). "
            f"Consider using a different target codec (e.g. AV1 or x265) for better quality."
        )
    return None


def _is_hdr_source(vstream: dict) -> bool:
    """
    Returns True if the video stream carries HDR or 10-bit metadata that should be preserved.
    Checks pixel format depth, BT.2020 color primaries, and HDR transfer functions (PQ/HLG).
    """
    pf = (vstream.get("pix_fmt") or "").lower()
    cp = (vstream.get("color_primaries") or "").lower()
    ct = (vstream.get("color_transfer") or "").lower()
    return bool(
        any(x in pf for x in ("10le", "10be", "12le", "12be", "p010", "p016")) or
        cp in ("bt2020", "bt2020nc", "bt2020c") or
        ct in ("smpte2084", "arib-std-b67", "smpte428", "bt2020-10", "bt2020-12")
    )


def _hdr_pixel_fmt(vcodec: str) -> str | None:
    """
    Return the appropriate 10-bit pixel format for encoders that support it,
    or None if the codec is 8-bit only (e.g. libx264).
    """
    _ten_bit_encoders = {"libx265", "libsvtav1", "libaom-av1", "libvpx-vp9"}
    return "yuv420p10le" if vcodec in _ten_bit_encoders else None


_MEDIA_PROBE_CACHE: dict = {}
_MEDIA_PROBE_LOCK = threading.Lock()


def _probe_media_cached(path: str) -> dict:
    """
    One full ffprobe (-show_streams -show_format) per (path, mtime, size),
    cached. Every metadata question in a job (dimensions, duration, HDR flags,
    codec name, audio meta) reads from this instead of re-running ffprobe —
    previously each retry re-probed the same file several times.
    """
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), int(st.st_mtime), int(st.st_size))
    except Exception:
        key = (str(path), 0, 0)
    with _MEDIA_PROBE_LOCK:
        hit = _MEDIA_PROBE_CACHE.get(key)
        if hit is not None:
            return hit
    data: dict = {}
    try:
        out = _sp_check_output(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
            text=True, startupinfo=si, creationflags=NO_WIN)
        data = json.loads(out or "{}") or {}
    except Exception:
        data = {}
    with _MEDIA_PROBE_LOCK:
        if len(_MEDIA_PROBE_CACHE) > 64:  # small LRU-ish cap
            _MEDIA_PROBE_CACHE.clear()
        _MEDIA_PROBE_CACHE[key] = data
    return data


def _probe_video_stream(path: str) -> dict:
    """First video stream dict from the cached probe (or {})."""
    for s in (_probe_media_cached(path).get("streams") or []):
        if (s or {}).get("codec_type") == "video":
            return s
    return {}


def _probe_is_hdr_path(input_path: str) -> bool:
    """Quick HDR/BT.2020 check for a file path (wraps _is_hdr_source)."""
    try:
        return _is_hdr_source(_probe_video_stream(input_path))
    except Exception:
        return False


# BT.2020 PQ/HLG -> BT.709 SDR tone-map. Needed when the target codec is 8-bit
# (e.g. x264): without it HDR sources come out washed-out/grey.
_HDR_TONEMAP_VF = (
    "zscale=transfer=linear:npl=100,format=gbrpf32le,"
    "tonemap=hable:desat=0,"
    "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv,format=yuv420p"
)


def _hdr_tonemap_vf(vcodec: str) -> str | None:
    """
    Return a tone-map filter string when downconverting HDR to an 8-bit codec,
    or None if the codec keeps 10-bit (HEVC/AV1/VP9 preserve HDR natively) or the
    zscale/tonemap filters aren't available in this ffmpeg build.
    """
    if _hdr_pixel_fmt(vcodec):  # codec can carry 10-bit -> preserve, don't tonemap
        return None
    try:
        if not (_ffmpeg_has_filter("zscale") and _ffmpeg_has_filter("tonemap")):
            return None
    except Exception:
        return None
    return _HDR_TONEMAP_VF


# Map x264-style preset names onto SVT-AV1's numeric scale (0=slowest/best .. 13=fastest).
_SVT_PRESET_MAP = {
    "placebo": "2", "veryslow": "2", "slower": "3", "slow": "4",
    "medium": "6", "fast": "8", "faster": "9", "veryfast": "10",
    "superfast": "11", "ultrafast": "12",
}


def _svt_preset_for_duration(preset_num, duration_s: float) -> int:
    """
    Clamp an SVT-AV1 preset (higher number = faster) to a speed FLOOR based on
    clip length. SVT preset semantics don't match x264/x265: a codec race that
    switches x265→AV1 carries x265's "slower" over, which maps to SVT preset 3 —
    ~9 fps on a 33-min 1440p clip, i.e. HOURS per pass, and the wall-clock budget
    can't interrupt a single in-progress pass. Long clips get a faster floor so
    one job stays tractable; short clips may still use the slow presets.
    """
    try:
        p = int(preset_num)
    except Exception:
        p = 6
    d = float(duration_s or 0.0)
    if d <= 0:
        return p
    if d > 1200:   floor = 7   # >20 min
    elif d > 600:  floor = 6   # 10-20 min
    elif d > 240:  floor = 5   # 4-10 min
    else:          floor = 2   # short clips: any preset is fine
    return max(p, floor)

# Map x264-style preset names onto libaom's cpu-used scale (0=slowest/best .. 8=fastest).
_AOM_CPU_USED_MAP = {
    "placebo": "2", "veryslow": "2", "slower": "3", "slow": "3",
    "medium": "4", "fast": "5", "faster": "6", "veryfast": "6",
    "superfast": "8", "ultrafast": "8",
}

_X265_VALID_TUNES = {"grain", "psnr", "ssim", "fastdecode", "zerolatency", "animation"}


def _codec_video_args(
    vcodec: str,
    *,
    preset: str,
    tune: str | None,
    crf: int | None,
    v_bitrate: int | None,
    fps: float | None = None,
    advanced_options: dict | None = None,
) -> tuple[list[str], list[str]]:
    """
    Build (video_args, ratecontrol_args) tuned per encoder so every codec is
    driven with its proper knobs:

      - software encoders get psy/AQ tuning in BOTH bitrate and CRF modes
      - libvpx-vp9 / libaom-av1 CRF mode gets the mandatory `-b:v 0` for true
        constant-quality (otherwise libvpx silently caps at its default bitrate)
      - libsvtav1 gets a numeric preset (string presets are rejected by SVT)
      - NVENC uses -cq, QSV uses -global_quality (ICQ), AMF uses CQP — none of
        them understand -crf
      - 10-bit encoding is enabled by default for HEVC/AV1 (better compression
        and less banding even for 8-bit sources); opt out via
        advanced_options["force_8bit"]
    """
    opts = advanced_options or {}
    use_bitrate = v_bitrate is not None and int(v_bitrate) > 0
    crf_i = int(crf if crf is not None else 22)
    g_frames = max(48, min(300, int(round(float(fps or 30.0) * 10))))

    def _abr(mult_max: float = 1.10, mult_buf: float = 2.0) -> list[str]:
        b = int(v_bitrate)
        return ["-b:v", str(b), "-maxrate", str(int(b * mult_max)), "-bufsize", str(int(b * mult_buf))]

    ten_bit = not bool(opts.get("force_8bit"))
    pix10 = ["-pix_fmt", "yuv420p10le"]

    if vcodec == "libx264":
        v = ["-preset", preset]
        if tune:
            v += ["-tune", str(tune)]
        params = str(opts.get("x264_params") or os.environ.get("BC_X264_PARAMS", "")).strip()
        if not params:
            params = ("aq-mode=3:aq-strength=1.00:mbtree=1:deblock=-1,-1:psy-rd=1.10,0.15"
                      ":rc-lookahead=60:qcomp=0.70:bframes=8:ref=5:trellis=2")
        v += ["-x264-params", params]
        return v, (_abr() if use_bitrate else ["-crf", str(crf_i)])

    if vcodec == "libx265":
        v = ["-preset", preset]
        _tn = str(tune or "").lower()
        if _tn in _X265_VALID_TUNES:
            v += ["-tune", _tn]
        params = (str(opts.get("x265_params") or "").strip()
                  or os.environ.get("BC_X265_PARAMS", "").strip())
        if not params:
            # Measured: x265's own defaults already maximise VMAF — psy-rd/aq
            # overrides (the old default here) traded ~2 VMAF for subjective
            # sharpness and LOST a head-to-head at equal size. Keep only the
            # neutral analysis-depth bumps that don't touch the psy/aq model.
            params = "rc-lookahead=60:bframes=8:ref=5"
        v += ["-x265-params", params]
        if ten_bit:
            v += pix10
        return v, (_abr() if use_bitrate else ["-crf", str(crf_i)])

    _max_mode = str(opts.get("quality_mode") or "").lower() == "max"

    if vcodec == "libsvtav1":
        _svt_p = _SVT_PRESET_MAP.get(str(preset).lower(), "6")
        if _max_mode:
            _svt_p = str(min(int(_svt_p), 4))
        # Never let a long clip run a glacial preset (see _svt_preset_for_duration).
        _svt_p = str(_svt_preset_for_duration(_svt_p, opts.get("duration_s")))
        _svt_params = "tune=0:enable-overlays=1:scd=1"
        # Film-grain synthesis: strip the grain before encoding (film-grain-denoise)
        # so bits go to the real signal, then re-synthesize grain from a model on
        # playback. Enabled by the MEASURED grain probe (_probe_film_grain) via
        # advanced_options["_film_grain"], not the unreliable graininess feature.
        _fg_lvl = int((opts.get("_film_grain") or {}).get("level") or 0)
        if _fg_lvl > 0:
            _svt_params += f":film-grain={_fg_lvl}:film-grain-denoise=1"
        v = ["-preset", _svt_p, "-g", str(g_frames), "-svtav1-params", _svt_params]
        if ten_bit:
            v += pix10
        if use_bitrate:
            # SVT-AV1 rejects -maxrate outside CRF mode ("Max Bitrate only
            # supported with CRF mode") — plain ABR only; retries handle size.
            return v, ["-b:v", str(int(v_bitrate))]
        return v, ["-crf", str(min(63, crf_i))]

    if vcodec == "libaom-av1":
        _cpu = _AOM_CPU_USED_MAP.get(str(preset).lower(), "4")
        if use_bitrate:
            # Size-targeting re-encodes the whole file repeatedly (seed, refine,
            # retries, packing). libaom below cpu-used 6 runs at ~1 fps for
            # 1080p, which turns one job into HOURS — keep the iterative path
            # fast; Max-mode's extra effort applies to single-shot CRF only.
            _cpu = str(max(int(_cpu), 6))
        elif _max_mode:
            _cpu = str(min(int(_cpu), 4))
        v = ["-cpu-used", _cpu,
             "-row-mt", "1", "-tile-columns", "1", "-lag-in-frames", "35",
             "-aq-mode", "1", "-g", str(g_frames)]
        # libaom denoises AND writes a grain table when -denoise-noise-level is
        # set (its own grain-synthesis path). Driven by the measured grain probe.
        _fg_aom = int((opts.get("_film_grain") or {}).get("level") or 0)
        if _fg_aom > 0:
            v += ["-denoise-noise-level", str(int(min(50, max(10, _fg_aom))))]
        if ten_bit:
            v += pix10
        if use_bitrate:
            return v, _abr()
        return v, ["-crf", str(min(63, crf_i)), "-b:v", "0"]

    if vcodec == "libvpx-vp9":
        # frame-parallel hurts quality; lag-in-frames + auto-alt-ref are big efficiency wins.
        v = ["-deadline", "good",
             "-cpu-used", ("1" if str(preset).lower() in ("slow", "slower", "veryslow", "placebo") else "2"),
             "-row-mt", "1", "-tile-columns", "1", "-frame-parallel", "0",
             "-lag-in-frames", "25", "-auto-alt-ref", "1", "-aq-mode", "0", "-g", str(g_frames)]
        if use_bitrate:
            return v, _abr()
        return v, ["-crf", str(min(63, crf_i)), "-b:v", "0"]

    if vcodec.endswith("_nvenc"):
        v = ["-preset", "p7", "-tune", "hq", "-multipass", "fullres", "-rc-lookahead", "32"]
        if vcodec in ("h264_nvenc", "hevc_nvenc"):
            v += ["-spatial-aq", "1", "-temporal-aq", "1", "-aq-strength", "8", "-bf", "4"]
        if vcodec == "hevc_nvenc" and ten_bit:
            v += ["-pix_fmt", "p010le"]
        if use_bitrate:
            return v, ["-rc", "vbr"] + _abr(1.15, 2.0)
        return v, ["-rc", "vbr", "-cq", str(min(51, crf_i)), "-b:v", "0"]

    if vcodec.endswith("_qsv"):
        v = ["-preset", "veryslow"]
        if vcodec in ("h264_qsv", "hevc_qsv"):
            v += ["-extbrc", "1"]
        if use_bitrate:
            return v, _abr(1.15, 2.0)
        # No bitrate => ICQ (intelligent constant quality) via -global_quality.
        return v, ["-global_quality", str(min(51, crf_i))]

    if vcodec.endswith("_amf"):
        # AMF has no -preset; it uses -quality plus pre-analysis/VBAQ for efficiency.
        v = ["-quality", ("high_quality" if vcodec == "av1_amf" else "quality")]
        if vcodec in ("h264_amf", "hevc_amf"):
            v += ["-preanalysis", "1", "-vbaq", "1"]
        if use_bitrate:
            return v, ["-rc", "vbr_peak"] + _abr(1.15, 2.0)
        return v, ["-rc", "cqp", "-qp_i", str(min(51, crf_i)), "-qp_p", str(min(51, crf_i))]

    if vcodec == "libvvenc":
        v = ["-preset", preset, "-pix_fmt", "yuv420p10le"]
        return v, (_abr() if use_bitrate else ["-qp", str(int(crf if crf is not None else 28))])

    # Unknown/VAAPI: keep it minimal and broadly compatible.
    v = []
    if use_bitrate:
        return v, _abr()
    return v, ["-qp", str(crf_i)]


def _strip_runtime_keys(adv: dict | None) -> dict:
    """
    Return a copy of advanced_options safe for JSON serialization / persistence:
    runtime-only keys (callbacks, job ids) are removed.
    """
    d = dict(adv or {})
    for k in ("progress_cb", "job_id", "status_cb", "cancel_cb"):
        d.pop(k, None)
    return d


def _ffmpeg_run_with_progress(cmd: list, duration_s: float, pass_label: str, cwd: str | None = None,
                              progress_cb=None, job_id: str | None = None,
                              stage: str | None = None) -> int:
    """
    Run an ffmpeg command with real-time progress reporting via '-progress pipe:1'.
    Emits '[Pass N] XX% | NNN fps | ETA ~Xs' to the BitCrusher logger every ~1 second,
    and forwards {stage, pct, fps, eta_s} to progress_cb(job_id, event) when given.
    Returns the process returncode.
    """
    _log = logging.getLogger("BitCrusher")
    prog_cmd = list(cmd)
    # Inject -progress pipe:1 just before the -i flag so ffmpeg streams progress to stdout.
    try:
        i_idx = prog_cmd.index("-i")
        prog_cmd = prog_cmd[:i_idx] + ["-progress", "pipe:1", "-stats_period", "1"] + prog_cmd[i_idx:]
    except ValueError:
        pass  # no -i found; run as-is

    _log.info("FFmpeg two-pass %s: %s", pass_label, " ".join(prog_cmd))
    try:
        proc = subprocess.Popen(
            prog_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            text=True,
            bufsize=1,
        )
        # IMPORTANT: x265 (and other encoders) write progress/info straight to
        # stderr even at -loglevel error. If we don't drain stderr it fills the
        # OS pipe buffer (~64 KB) and the encoder blocks mid-pass, silently
        # failing the encode. Drain it on a background thread, keeping the tail
        # for error diagnosis.
        from collections import deque
        _err_tail: deque = deque(maxlen=40)
        def _drain_stderr(pipe):
            try:
                for ln in iter(pipe.readline, ""):
                    if ln:
                        _err_tail.append(ln.rstrip("\n"))
            except Exception:
                pass
        _err_thread = threading.Thread(target=_drain_stderr, args=(proc.stderr,), daemon=True)
        _err_thread.start()

        kv: dict[str, str] = {}
        start_wall = time.time()
        last_emit = 0.0
        for line in (proc.stdout or []):
            line = line.strip()
            if "=" in line:
                k, _, v = line.partition("=")
                kv[k.strip()] = v.strip()
            if line.startswith("progress="):
                now = time.monotonic()
                if now - last_emit >= 1.0:
                    last_emit = now
                    try:
                        out_ms = int(kv.get("out_time_ms", 0) or 0)
                        fps_val = float(kv.get("fps", "0") or 0)
                        if duration_s > 0 and out_ms > 0:
                            pct = min(100.0, out_ms / (duration_s * 1_000_000) * 100.0)
                            elapsed = max(0.001, time.time() - start_wall)
                            done_s = out_ms / 1_000_000
                            speed = done_s / elapsed  # video-seconds per wall-second
                            remaining = max(0.0, duration_s - done_s)
                            eta_s = int(remaining / speed) if speed > 0 else 0
                            fps_str = f" | {fps_val:.0f} fps" if fps_val > 0 else ""
                            eta_str = f" | ETA ~{eta_s}s" if eta_s > 0 else ""
                            _log.info("[%s] %.0f%%%s%s", pass_label, pct, fps_str, eta_str)
                            if callable(progress_cb):
                                try:
                                    progress_cb(job_id, {
                                        "stage": (stage or "encoding"),
                                        "pct": float(pct),
                                        "fps": float(fps_val),
                                        "eta_s": int(eta_s),
                                        "detail": pass_label,
                                    })
                                except Exception:
                                    pass
                    except Exception:
                        pass
        proc.wait()
        try:
            _err_thread.join(timeout=2.0)
        except Exception:
            pass
        if proc.returncode != 0 and _err_tail:
            _log.error("FFmpeg %s failed (rc=%s): %s", pass_label, proc.returncode,
                       " | ".join(list(_err_tail)[-6:]))
        return proc.returncode
    except Exception as exc:
        _log.debug("Progress-aware run failed (%r); falling back", exc)
        # Fallback: plain run (stderr to DEVNULL so it can't deadlock).
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=cwd)
        return r.returncode


def _ffmpeg_two_pass_encode(
    input_path: str,
    output_path: str,
    *,
    encoder: str | None,
    bitrate: int,
    width: int | None,
    fps: float | None,
    tune: str | None,
    audio_bitrate: int | None,
    audio_copy: bool,
    preset: str | None = "medium",
    turbo: bool = False,
    duration_s: float = 0.0,
    progress_cb=None,
    job_id: str | None = None,
    advanced_options: dict | None = None,
) -> bool:
    """
    Robust native two-pass using ffmpeg for libx264/libx265/libvpx-vp9.
    Pass 1 to null, pass 2 to file with real-time progress display.
    turbo=True speeds up pass 1 only where it's stats-compatible (VP9 cpu-used);
    x264/x265 keep a consistent preset across passes (a preset mismatch breaks
    their two-pass stats).
    """
    # Both passes run with cwd set to a temp dir (so the relative pass-log file
    # lands there). Any relative input/output path would break from that cwd, so
    # resolve them to absolute up front.
    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)
    vmap = {
        "x264": "libx264", "h264": "libx264", "libx264": "libx264",
        "x265": "libx265", "hevc": "libx265", "libx265": "libx265",
        "vp9": "libvpx-vp9", "libvpx-vp9": "libvpx-vp9",
        "svt-av1": "libsvtav1", "svtav1": "libsvtav1", "libsvtav1": "libsvtav1",
        # generic 'av1' only reaches here when _supports_true_two_pass verified
        # the concrete AV1 encoder is SVT-AV1 (libaom 2-pass is far too slow).
        "av1": "libsvtav1",
    }
    vcodec = vmap.get((encoder or "").lower(), "libx264")
    # sanitize tune per codec (avoid x265 invalid 'film')
    safe_tune = None
    if tune:
        _tn = str(tune).lower()
        if vcodec == "libx265":
            # x265 does not support x264-style film/animation tune semantics.
            if _tn in ("film", "animation", "stillimage"):
                _tn = ""
            if _tn in ("grain","psnr","ssim","fastdecode"):
                safe_tune = _tn
        else:
            safe_tune = _tn
    # Detect HDR source and select pixel format accordingly (cached probe —
    # this used to spawn a fresh ffprobe on EVERY retry pass).
    try:
        _vst = _probe_video_stream(input_path)
    except Exception:
        _vst = {}
    source_is_hdr = _is_hdr_source(_vst)
    hdr_pf = _hdr_pixel_fmt(vcodec) if source_is_hdr else None
    if source_is_hdr and hdr_pf:
        logging.getLogger("BitCrusher").info(
            "[HDR] HDR source detected — encoding to 10-bit (%s) to preserve quality", hdr_pf)
    elif source_is_hdr:
        logging.getLogger("BitCrusher").warning(
            "[HDR] HDR source detected but %s only supports 8-bit — color precision will be reduced", vcodec)

    # temp dir + pass-log path are chosen further below, after the encode
    # signature (codec/res/preset/tune/filters/params) is known, so an optional
    # shared pass-log can key on it and reuse pass 1 across bitrate retries.

    # Video rc params (shared)
    rc = ["-b:v", str(int(bitrate)), "-maxrate", str(int(bitrate * 1.10)), "-bufsize", str(int(bitrate * 2))]
    # Build the filter chain: HDR->SDR tone-map (when target is 8-bit) then scale.
    _vf_chain: list[str] = []
    if source_is_hdr and not hdr_pf:
        _tm = _hdr_tonemap_vf(vcodec)
        if _tm:
            _vf_chain.append(_tm)
            logging.getLogger("BitCrusher").info(
                "[HDR] HDR source + 8-bit %s -> tone-mapping BT.2020->BT.709 (two-pass).", vcodec)
    if width:
        _vf_chain.append(f"scale={int(width)}:-2")
    # Validated artifact-aware prefilters (deband/deblock/denoise) — applied at
    # delivery resolution, identical in both passes (pass-log stats must match).
    _pre_vf = str((advanced_options or {}).get("preproc_vf") or "").strip()
    if _pre_vf:
        _vf_chain.append(_pre_vf)
    vscale = (["-vf", ",".join(_vf_chain)] if _vf_chain else [])
    vfps   = (["-r", str(int(round(float(fps))))] if fps else [])
    # Pixel format: prefer 10-bit for HDR sources when codec supports it; else standard 8-bit.
    vpix   = (["-pix_fmt", hdr_pf] if hdr_pf else ["-pix_fmt", "yuv420p"])

    # Multi-audio-track mapping (keep-first / mix). amix can't stream-copy, so a
    # "mix" plan forces an audio re-encode. Pass 1 stays -an; only pass 2 carries
    # the audio map/filter.
    _atplan = (advanced_options or {}).get("_audio_track_plan") or {}
    _acopy_ref = {"audio_copy": bool(audio_copy)}
    _amap = _audio_map_ffmpeg_args(_atplan, _acopy_ref) if _atplan.get("multi") else []
    _audio_copy_eff = _acopy_ref["audio_copy"]

    # Audio settings for the 2nd pass
    if _audio_copy_eff:
        a2 = ["-c:a", "copy"]
    elif audio_bitrate and int(audio_bitrate) > 0:
        a2 = ["-c:a", "aac", "-b:a", str(int(audio_bitrate))]
    elif _atplan.get("multi") and _atplan.get("mode") == "mix":
        # mix forced re-encode but no explicit bitrate was passed — use a safe default.
        a2 = ["-c:a", "aac", "-b:a", "192k"]
    else:
        a2 = ["-an"]

    preset = str(preset or "medium")
    # Turbo used to run pass 1 at a faster PRESET, but x264/x265 two-pass stats
    # are preset-specific — a fast→medium mismatch makes pass 2 fail to open the
    # encoder ("Invalid argument"). x264 already runs a fast first pass
    # internally (unless --slow-firstpass), so both passes must share a preset.
    # VP9's per-pass -cpu-used speedup (below) IS pass-safe and still applies.
    preset_p1 = preset
    _adv = advanced_options or {}

    # Base (common) flags (optional GPU decode of the source; encode unchanged)
    _hw = _hw_decode_args(_adv)
    base = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"] + _hw + ["-i", input_path,
            "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn"]

    # === Pass-1 stats reuse (shared pass-log) ==============================
    # Pass 1 (whole-file complexity analysis) is identical for every encode of
    # this source that shares codec + resolution + fps + preset + tune + filter
    # chain + codec params; only the target bitrate differs, and pass-1 stats are
    # bitrate-independent. When the caller supplies a shared pass-log dir (the
    # size-convergence loop does), key the log on those invariants and reuse the
    # stats across calls so only pass 2 re-runs — roughly halving the loop's
    # encode time. A resolution/filter change yields a new key = a fresh pass 1.
    import hashlib as _hashlib
    _sig_src = "|".join([
        vcodec, str(int(width or 0)), str(int(round(float(fps or 0.0)))),
        str(preset), str(safe_tune or ""), (vpix[-1] if vpix else ""),
        ",".join(_vf_chain),
        str(_adv.get("x265_params") or ""), str(_adv.get("x264_params") or ""),
        str(os.environ.get("BC_X265_PARAMS", "")), str(os.environ.get("BC_X264_PARAMS", "")),
    ])
    _sig8 = _hashlib.sha1(_sig_src.encode("utf-8", "ignore")).hexdigest()[:12]
    null_dev = "NUL" if os.name == "nt" else "/dev/null"
    _pl_dir = str((_adv.get("_twopass_passlog_dir") or "")).strip()
    if _pl_dir and os.path.isdir(_pl_dir):
        temp_dir = _pl_dir
        _owns_temp = False
        passlog = os.path.join(temp_dir, f"bc2p_{_sig8}")
        passlog_name = os.path.basename(passlog)
    else:
        temp_dir = tempfile.mkdtemp(prefix="bc_ff2p_")
        _owns_temp = True
        passlog = os.path.join(tempfile.gettempdir(), f"bc2p_{next(tempfile._get_candidate_names())}")
        passlog_name = "passlog"
    _p1_sentinel = passlog + ".p1done"
    # x265 writes the stats file to its exact `stats=` name; the generic
    # -pass/-passlogfile encoders (x264/vp9/svt-av1) write "<passlogfile>-0.log".
    _stats_probe = passlog if vcodec == "libx265" else (passlog + "-0.log")
    _reuse_p1 = bool(
        (not _owns_temp) and _adv.get("_twopass_reuse_stats")
        and os.path.exists(_p1_sentinel)
        and os.path.exists(_stats_probe) and os.path.getsize(_stats_probe) > 0)

    # Codec-specific two-pass
    try:
        if vcodec == "libsvtav1":
            # SVT-AV1 1-pass VBR is wildly loose on hard-cut content: on
            # asdfmovie (a cut every ~2s) -b:v 605k produced 2.0x the target,
            # and dropping the rate barely shrank the file — 1-pass hits a floor
            # it can't plan past. 2-pass VBR measures the whole-file complexity
            # in pass 1 and actually holds the budget in pass 2. SVT writes its
            # stats to <passlogfile>-0.log via the generic ffmpeg -pass flags.
            _svt_p = _SVT_PRESET_MAP.get(str(preset).lower(), "6")
            _svt_max = str((_adv or {}).get("quality_mode") or "").lower() == "max"
            if _svt_max:
                _svt_p = str(min(int(_svt_p), 4))
            # Long clips must not run a glacial preset carried over from x265's
            # "slow"/"slower" (SVT preset 3 = ~9fps on a 33-min clip = hours).
            _svt_p = str(_svt_preset_for_duration(_svt_p, duration_s))
            # NOTE: enable-overlays is REJECTED by SVT in multi-pass mode
            # ("overlay frames feature is currently not supported with
            # multi-pass encoding"), so the single-pass params can't be reused
            # verbatim here. scd + film-grain ARE 2-pass-safe (verified).
            _svt_params = "tune=0:scd=1"
            # Film-grain synthesis (denoise + re-synthesize) from the measured
            # grain probe — see advanced_options["_film_grain"]; 2-pass-safe.
            _fg_lvl2 = int(((_adv or {}).get("_film_grain") or {}).get("level") or 0)
            if _fg_lvl2 > 0:
                _svt_params += f":film-grain={_fg_lvl2}:film-grain-denoise=1"
            _svt_v = ["-c:v", "libsvtav1", "-preset", _svt_p, "-g", "300",
                      "-svtav1-params", _svt_params, "-pix_fmt", "yuv420p10le"]
            # SVT rejects -maxrate/-bufsize with -b:v (VBR); target bitrate only.
            rc_svt = ["-b:v", str(int(bitrate))]
            cmd1 = base + _svt_v + vscale + vfps + rc_svt \
                + ["-pass", "1", "-passlogfile", passlog, "-an", "-f", "null", null_dev]
            cmd2 = base + _svt_v + vscale + vfps + rc_svt \
                + ["-pass", "2", "-passlogfile", passlog] + _amap + a2 + ["-movflags", "+faststart", output_path]
        elif vcodec == "libx265":
            # libx265 uses x265-params pass=N:stats=...
            # Measured (10MB head-to-head): x265 defaults beat psy-rd/aq overrides
            # by ~2 VMAF. Keep only neutral analysis-depth bumps; let the encoder
            # use its (already VMAF-optimal) defaults for psy-rd/aq/deblock.
            _x265_base = (str(_adv.get("x265_params") or "").strip()
                          or os.environ.get("BC_X265_PARAMS", "").strip()
                          or "rc-lookahead=80:bframes=8:ref=5")
            x265p1 = f"pass=1:stats={passlog_name}:{_x265_base}"
            x265p2 = f"pass=2:stats={passlog_name}:{_x265_base}"

            cmd1 = base + ["-c:v", vcodec, "-preset", preset_p1] + (["-tune", safe_tune] if safe_tune else []) \
                  + vscale + vfps + vpix + rc + ["-x265-params", x265p1, "-an", "-f", "null", null_dev]
            cmd2 = base + ["-c:v", vcodec, "-preset", preset] + (["-tune", safe_tune] if safe_tune else []) \
                  + vscale + vfps + vpix + rc + ["-x265-params", x265p2] + _amap + a2 + ["-movflags", "+faststart", output_path]
        elif vcodec == "libvpx-vp9":
            # VP9 does not accept x264/x265-style tune/preset knobs.
            # VP9 with 10-bit requires profile:v 2.
            vp9_prof = (["-profile:v", "2"] if hdr_pf else [])
            vp9_common = ["-c:v", vcodec, "-deadline", "good", "-cpu-used", ("1" if preset in ("slow", "veryslow") else "2"),
                          "-row-mt", "1", "-tile-columns", "1", "-frame-parallel", "0",
                          "-lag-in-frames", "25", "-auto-alt-ref", "1"] + vp9_prof
            vp9_p1 = (["-cpu-used", "4"] if turbo else [])  # last flag wins in ffmpeg
            cmd1 = base + vp9_common + vp9_p1 + vscale + vfps + vpix + rc + ["-pass", "1", "-passlogfile", passlog, "-an", "-f", "null", null_dev]
            cmd2 = base + vp9_common + vscale + vfps + vpix + rc + ["-pass", "2", "-passlogfile", passlog] + _amap + a2 + ["-movflags", "+faststart", output_path]
        else:
            # libx264 honors global -pass / -passlogfile. x264 is 8-bit only; vpix falls back to yuv420p.
            _x264_params = (str(_adv.get("x264_params") or "").strip()
                            or str(os.environ.get("BC_X264_PARAMS", "")).strip())
            if not _x264_params:
                _x264_params = "aq-mode=3:aq-strength=1.00:mbtree=1:deblock=-1,-1:psy-rd=1.10,0.15:rc-lookahead=80:qcomp=0.70:ipratio=1.30:pbratio=1.20:trellis=2:bframes=8:ref=5"
            cmd1 = base + ["-c:v", vcodec, "-preset", preset_p1] + (["-tune", safe_tune] if safe_tune else []) \
                  + vscale + vfps + vpix + rc + (["-x264-params", _x264_params] if _x264_params else []) + ["-pass", "1", "-passlogfile", passlog, "-an", "-f", "null", null_dev]
            cmd2 = base + ["-c:v", vcodec, "-preset", preset] + (["-tune", safe_tune] if safe_tune else []) \
                  + vscale + vfps + vpix + rc + (["-x264-params", _x264_params] if _x264_params else []) + ["-pass", "2", "-passlogfile", passlog] + _amap + a2 + ["-movflags", "+faststart", output_path]

        # Map per-pass 0-100% onto overall 0-50 / 50-100 for the job progress row.
        def _cb_pass(lo: float, span: float, stage: str):
            if not callable(progress_cb):
                return None
            def _cb(jid, ev):
                try:
                    ev = dict(ev)
                    ev["pct"] = lo + float(ev.get("pct", 0.0)) * span
                    ev["stage"] = stage
                    progress_cb(jid, ev)
                except Exception:
                    pass
            return _cb

        # Fast path: a prior identical pass 1 already produced reusable stats —
        # run pass 2 only (mapped across the whole progress bar). If it fails
        # (stale/incompatible stats), fall through and regenerate pass 1.
        _did_reuse = False
        if _reuse_p1:
            logging.getLogger("BitCrusher").info(
                "[TwoPass] Reusing pass-1 stats (%s); running pass 2 only.",
                os.path.basename(passlog))
            rc2 = _ffmpeg_run_with_progress(cmd2, duration_s or 0.0, "Pass 2/2", cwd=temp_dir,
                                            progress_cb=_cb_pass(0.0, 1.0, "pass2"),
                                            job_id=job_id, stage="pass2")
            if rc2 == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                _did_reuse = True
            else:
                logging.getLogger("BitCrusher").warning(
                    "[TwoPass] Pass 2 with reused stats failed (rc=%s); regenerating pass 1.", rc2)

        if not _did_reuse:
            # Pass 1 — stats gathering (null output); show progress if duration known.
            rc1 = _ffmpeg_run_with_progress(cmd1, duration_s or 0.0, "Pass 1/2", cwd=temp_dir,
                                            progress_cb=_cb_pass(0.0, 0.5, "pass1"), job_id=job_id, stage="pass1")
            if rc1 != 0 and _hw:
                # GPU decode can fail on exotic sources/drivers — fall back to
                # software decode for this and all subsequent jobs.
                _mark_hw_decode_broken()
                logging.getLogger("BitCrusher").warning(
                    "HW decode failed (rc=%s); retrying with software decode.", rc1)
                cmd1, cmd2 = _strip_hw_args(cmd1), _strip_hw_args(cmd2)
                rc1 = _ffmpeg_run_with_progress(cmd1, duration_s or 0.0, "Pass 1/2", cwd=temp_dir,
                                                progress_cb=_cb_pass(0.0, 0.5, "pass1"), job_id=job_id, stage="pass1")
            if rc1 != 0:
                return False
            # Pass 1 succeeded — mark the stats reusable for later bitrate retries
            # of this same job (shared pass-log mode only; the caller owns cleanup).
            if not _owns_temp:
                try:
                    with open(_p1_sentinel, "w") as _sf:
                        _sf.write("1")
                except Exception:
                    pass
            # Pass 2 — actual encode with real-time progress display.
            rc2 = _ffmpeg_run_with_progress(cmd2, duration_s or 0.0, "Pass 2/2", cwd=temp_dir,
                                            progress_cb=_cb_pass(50.0, 0.5, "pass2"), job_id=job_id, stage="pass2")
        return rc2 == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    finally:
        # Only remove the temp dir we created. A shared pass-log dir is owned by
        # the caller (the size loop) so its stats survive for the next retry.
        if _owns_temp:
            import shutil
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass




def _handbrake_encode(
    input_path: str,
    output_path: str,
    *,
    encoder: str | None,
    bitrate: int | None,
    crf: int | None,
    width: int | None,
    fps: float | None,
    audio_bitrate: int | None,
    audio_copy: bool,
    two_pass: bool,
    turbo: bool,
) -> bool:
    """
    Minimal HandBrakeCLI encode. Uses x264/x265 only, mapped from requested encoder.
    """
    import shutil, subprocess, os
    hb = HANDBRAKE_CLI if os.path.isfile(HANDBRAKE_CLI) else shutil.which("HandBrakeCLI") or shutil.which("HandBrakeCLI.exe")
    if not hb:
        return False

    e = (encoder or "").lower()
    hb_encoder = "x265" if ("265" in e or "hevc" in e) else "x264"

    cmd = [hb, "-i", input_path, "-o", output_path, "-e", hb_encoder, "--optimize"]
    if width:
        cmd += ["-w", str(int(width))]
    if fps:
        cmd += ["-r", str(int(round(float(fps))))]

    if two_pass and bitrate:
        cmd += ["-b", str(int(bitrate)//1000), "-2"]
        if turbo:
            cmd += ["--turbo"]
    elif crf is not None:
        cmd += ["-q", str(int(crf))]
    elif bitrate:
        cmd += ["-b", str(int(bitrate)//1000)]

    if audio_copy:
        cmd += ["-E", "copy"]
    elif audio_bitrate and int(audio_bitrate) > 0:
        cmd += ["-E", "av_aac", "-B", str(int(audio_bitrate)//1000)]
    else:
        cmd += ["-a", "none"]

    r = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ok = (r.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0)

    # If passthrough isn't supported for the input, retry with AVC AAC encode
    if (not ok) and audio_copy:
        try:
            # swap audio to aac quickly
            cmd_fallback = [c for c in cmd if c != "copy"]
            # ensure encoder & bitrate exist
            if "-E" in cmd_fallback:
                i = cmd_fallback.index("-E")
                cmd_fallback[i+1] = "av_aac"
            else:
                cmd_fallback += ["-E", "av_aac"]
            if audio_bitrate and int(audio_bitrate) > 0:
                if "-B" in cmd_fallback:
                    j = cmd_fallback.index("-B")
                    cmd_fallback[j+1] = str(int(audio_bitrate)//1000)
                else:
                    cmd_fallback += ["-B", str(int(audio_bitrate)//1000)]
            r2 = _sp_run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ok = (r2.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0)
        except Exception:
            ok = False

    return ok





def _sp_run(cmd, *args, **kwargs):
    return _run_logged(cmd, *args, **kwargs)


def bridge_gui_logger(widget):
    
    class _TkHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = _normalize_text(self.format(record))
                widget.configure(state="normal")
                widget.insert("end", msg + "\n")
                widget.see("end")
                widget.configure(state="disabled")
            except Exception:
                pass
    h = _TkHandler()
    h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
    h.setLevel(logging.INFO)
    LOG.addHandler(h)
    return h

def log_info(msg): LOG.info(msg)
def log_warn(msg): LOG.warning(msg)
def log_err(msg): LOG.error(msg)
def log_exc(msg="Unhandled exception"): LOG.exception(msg)

def log_tool_paths(handbrake, ffmpeg, ffprobe):
    LOG.info("Tool paths - HandBrakeCLI=%s | ffmpeg=%s | ffprobe=%s", handbrake, ffmpeg, ffprobe)

def bridge_gui_logger_color(widget):
    

    try:
        widget.tag_configure("INFO",    foreground=FG)
        widget.tag_configure("DEBUG",   foreground="#7f8ea3")   # muted, recedes
        widget.tag_configure("WARNING", foreground=WARN)
        widget.tag_configure("ERROR",   foreground=ERROR)
        widget.tag_configure("CRITICAL",foreground="#ffffff", background="#b00020")
        widget.tag_configure("DIV",     foreground="#4a5163")   # section dividers
        widget.configure(background=_hsl_shift(CARD_BG, l_mul=0.98), fg=FG, insertbackground=FG)
    except Exception:
        pass

    class _TkColorHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = _normalize_text(self.format(record))
                lvl = record.levelname.upper()
                widget.configure(state="normal")
                widget.insert("end", msg + "\n", (lvl,))
                widget.see("end")
                widget.configure(state="disabled")
            except Exception:
                pass

    h = _TkColorHandler()
    h.setFormatter(logging.Formatter("%(asctime)s   %(levelname)-7s %(message)s", "%H:%M:%S"))
    h.setLevel(logging.INFO)
    LOG.addHandler(h)
    return h

_TOASTER = None
def notify_info(title, msg, duration=3):
    
    global _TOASTER
    try:
        from win10toast import ToastNotifier
        if _TOASTER is None:
            _TOASTER = ToastNotifier()
        _TOASTER.show_toast(str(_normalize_text(title or "BitCrusher")),
                            str(_normalize_text(msg or "")),
                            duration=int(duration or 3),
                            threaded=True,
                            icon_path=None)
    except Exception as e:
        try:
            LOG.warning(f"Toast suppressed: {e} | {title}: {msg}")
        except Exception:
            pass

def notify_warn(title, msg, duration=4):
    notify_info(title, msg, duration)

def notify_error(title, msg, duration=5):
    try:
        notify_info(title, msg, duration)
    except Exception:
        pass
    try:
        LOG.error(str(msg))
    except Exception:
        pass




def _bin_ok(bin_path, args=("-version",)):
    try:
        p = _sp_run([bin_path, *args], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, startupinfo=si, creationflags=NO_WIN)
        return p.returncode == 0
    except Exception:
        return False

import csv as _csv
import math
import threading as _th

class EnsembleRegressor:
    
    def __init__(self, gb=None, alpha=0.5, w=None, b=0.0, mu=None, sigma=None, clip=(16,40)):
        self.gb = gb  # may be None
        self.alpha = float(alpha)
        self.w = None if w is None else np.asarray(w, dtype=float).copy()
        self.b = float(b or 0.0)
        self.mu = None if mu is None else np.asarray(mu, dtype=float).copy()
        self.sigma = None if sigma is None else np.asarray(sigma, dtype=float).copy()
        self.clip = tuple(clip or (16,40))

    def _phi(self, X):

        X = np.asarray(X, dtype=float)
        if X.ndim == 1: X = X.reshape(1,-1)
        n, d = X.shape
        k = min(d, 6)
        feats = [X, X*X, np.log1p(np.abs(X))]

        pair = []
        if k >= 2:
            for i in range(k):
                for j in range(i+1, k):
                    pair.append((X[:,i] * X[:,j]).reshape(n,1))
        if pair:
            feats.append(np.hstack(pair))
        Z = np.hstack(feats)

        if self.mu is None or self.sigma is None:
            self.mu = Z.mean(axis=0)
            self.sigma = Z.std(axis=0) + 1e-8
        Zs = (Z - self.mu) / self.sigma
        return Zs

    def predict(self, X_raw):
        X_raw = np.asarray(X_raw, dtype=float)
        if X_raw.ndim == 1: X_raw = X_raw.reshape(1,-1)

        gb_pred = 0.0
        if self.gb is not None:
            try:
                gb_pred = self.gb.predict(X_raw)
            except Exception:
                gb_pred = 0.0
        else:
            gb_pred = 0.0

        Zs = self._phi(X_raw)
        lin = Zs.dot(self.w) + self.b if self.w is not None else np.zeros((X_raw.shape[0],), dtype=float)

        y = self.alpha * np.asarray(gb_pred).reshape(-1) + (1.0 - self.alpha) * lin.reshape(-1)

        y = np.clip(y, float(self.clip[0]), float(self.clip[1]))
        return y

class CRFPredictor:
    def __init__(self, store_dir=None):
        if store_dir is None:
            store_dir = HEURISTICS_DIR
        os.makedirs(store_dir, exist_ok=True)
        self.csv_path = os.path.join(store_dir, "encodes.csv")
        self.model_path = os.path.join(store_dir, "crf_model.pkl")
        self.model = None  # EnsembleRegressor
        self.lock = _th.Lock()
        self._load()

    def _load(self):
        try:
            import joblib
            if os.path.exists(self.model_path):
                obj = joblib.load(self.model_path)
                if isinstance(obj, EnsembleRegressor):
                    self.model = obj
                elif hasattr(obj, "predict"):

                    self.model = EnsembleRegressor(gb=obj, alpha=1.0)
                else:
                    self.model = None
        except Exception:
            self.model = None

    def _save(self):
        try:
            import joblib
            if self.model is not None:
                joblib.dump(self.model, self.model_path)
        except Exception:
            pass

    @staticmethod
    def _feat_vec(dur, w, h, fps, in_bps, target_bytes, tune_code, sample_kbps):

        dur = float(max(0.01, dur))
        w = float(max(1.0, w)); h = float(max(1.0, h)); fps = float(max(0.1, fps))
        area = w * h
        ar = w / max(1.0, h)
        in_bps = float(max(1.0, in_bps))
        target_v_bps = max(100_000.0, (target_bytes * 8.0) / dur)
        bppf_in = in_bps / max(1.0, area * fps)              # input bits per pixel-frame
        bppf_tg = target_v_bps / max(1.0, area * fps)        # target bits per pixel-frame
        den = in_bps / max(1.0, area)                        # spatial density proxy
        s_kbps = float(sample_kbps)

        return [
            dur, w, h, fps, area, math.sqrt(area), ar,
            in_bps, target_bytes, target_v_bps, bppf_in, bppf_tg, den,
            float(tune_code), s_kbps
        ]

    @staticmethod
    def _rule_crf(dur, w, h, fps, in_bps, target_bytes, tune_code, sample_kbps):

        dur = float(max(0.01, dur))
        area = max(1.0, float(w) * float(h))
        fps = float(max(0.1, fps))
        target_v_bps = max(100_000.0, (target_bytes * 8.0) / dur)
        bppf = target_v_bps / (area * fps)

        if bppf >= 0.35: base = 19
        elif bppf >= 0.18: base = 22
        elif bppf >= 0.10: base = 24
        elif bppf >= 0.06: base = 27
        elif bppf >= 0.035: base = 29
        else: base = 32

        if int(tune_code) == 2:      # grain
            base -= 2
        elif int(tune_code) == 1:    # animation
            base += 1
        if float(sample_kbps) > 0:   # size targeting → bias toward quality
            base -= 1
        return int(min(max(base, 16), 40))

    def predict(self, dur, w, h, fps, in_bps, target_bytes, tune_code, sample_kbps, default_crf=22):

        fvec = self._feat_vec(dur, w, h, fps, in_bps, target_bytes, tune_code, sample_kbps)
        rule = self._rule_crf(dur, w, h, fps, in_bps, target_bytes, tune_code, sample_kbps)
        with self.lock:
            mdl = self.model
        if mdl is None:
            return int(rule if rule else default_crf)
        try:
            import numpy as _np
            X = _np.asarray([fvec], dtype=float)
            pred = float(mdl.predict(X)[0])
        except Exception:
            pred = float(rule)
        out = 0.30 * float(rule) + 0.70 * pred
        return int(min(max(round(out), 16), 40))

    def log_example(self, dur, w, h, fps, in_bps, target_bytes, tune_code, sample_kbps, used_crf):
        header = ["dur","w","h","fps","in_bps","target_bytes","tune","sample_kbps","crf"]
        try:
            new_file = not os.path.exists(self.csv_path)
            with open(self.csv_path, "a", newline="") as f:
                wr = _csv.writer(f)
                if new_file: wr.writerow(header)
                wr.writerow([dur, w, h, fps, in_bps, target_bytes, tune_code, sample_kbps, used_crf])
        except Exception:
            pass

    def fit_from_disk(self):

        if not os.path.exists(self.csv_path):
            return
        X_raw, y = [], []
        try:
            with open(self.csv_path, "r", newline="") as f:
                rd = _csv.DictReader(f)
                for r in rd:
                    X_raw.append(self._feat_vec(
                        float(r["dur"]), float(r["w"]), float(r["h"]), float(r["fps"]),
                        float(r["in_bps"]), float(r["target_bytes"]), float(r["tune"]), float(r["sample_kbps"])
                    ))
                    y.append(float(r["crf"]))
            if not X_raw: return
            import numpy as _np
            X_raw = _np.asarray(X_raw, dtype=float)
            y = _np.asarray(y, dtype=float)

            n = len(y)
            idx_split = max(1, int(n * 0.8))
            X_tr, X_va = X_raw[:idx_split], X_raw[idx_split:]
            y_tr, y_va = y[:idx_split], y[idx_split:]

            gb = None
            if _SkHGBR is not None and X_tr.shape[0] >= 30:
                try:
                    gb = _SkHGBR(max_depth=4, learning_rate=0.08, max_iter=400, min_samples_leaf=12)
                    gb.fit(X_tr, y_tr)
                except Exception:
                    gb = None

            tmp = EnsembleRegressor(gb=None, alpha=1.0)
            Z_tr = tmp._phi(X_tr)
            lam = 1e-2
            I = _np.eye(Z_tr.shape[1], dtype=float)
            A = Z_tr.T @ Z_tr + lam * I
            b = Z_tr.T @ y_tr
            coefs = _np.linalg.pinv(A) @ b
            bias = 0.0  # centered by standardization
            mu, sigma = tmp.mu, tmp.sigma

            def _pred_mix(alpha):
                model = EnsembleRegressor(gb=gb, alpha=alpha, w=coefs, b=bias, mu=mu, sigma=sigma)
                return model.predict(X_va)
            best_alpha = 0.5
            if len(y_va) >= 5:
                alphas = _np.linspace(0.0, 1.0, 11)
                errs = []
                for a in alphas:
                    pred = _pred_mix(a)
                    mse = float(_np.mean((pred - y_va)**2))
                    errs.append(mse)
                best_alpha = float(alphas[int(_np.argmin(_np.asarray(errs)))])

            final_tmp = EnsembleRegressor(gb=None, alpha=1.0)
            Z_all = final_tmp._phi(X_raw)
            mu_all, sigma_all = final_tmp.mu, final_tmp.sigma
            A = Z_all.T @ Z_all + 1e-2 * _np.eye(Z_all.shape[1], dtype=float)
            b = Z_all.T @ y
            coefs_all = _np.linalg.pinv(A) @ b
            mdl = EnsembleRegressor(gb=gb, alpha=best_alpha, w=coefs_all, b=0.0, mu=mu_all, sigma=sigma_all)
            self.model = mdl
            self._save()
        except Exception:

            pass

_CRF_PREDICTOR = None
def get_crf_predictor():
    global _CRF_PREDICTOR
    if _CRF_PREDICTOR is None:
        _CRF_PREDICTOR = CRFPredictor()
    return _CRF_PREDICTOR

def _tune_to_code(tune_str: str) -> int:
    t = (tune_str or "film").lower()
    if "anim" in t: return 1
    if "grain" in t: return 2
    return 0  # film/none

def train_crf_offline_async():
    _th.Thread(target=lambda: get_crf_predictor().fit_from_disk(), daemon=True).start()

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None

import pystray

import tkinter as tk
from tkinter import filedialog, messagebox, ttk, Toplevel, Label
from tkinter.scrolledtext import ScrolledText


import logging

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    _fix_bad_logging_formatters()
    try:
        root_logger = logging.getLogger()
        root_logger.addFilter(_MojibakeFilter())
    except Exception:
        pass
    logger = logging.getLogger('BitCrusher')
    try:
        logger.addFilter(_MojibakeFilter())
    except Exception:
        pass
    return logger

def _jsonl_log(event: str, data: dict | None = None):
    try:
        os.makedirs("logs", exist_ok=True)
        path = os.path.join("logs", f"run_{datetime.now().strftime('%Y%m%d')}.jsonl")
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event}
        if data:
            rec.update(data)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def aggregate_lifetime_stats(log_dir: str | None = None) -> dict:
    """
    Read every logs/run_*.jsonl and roll up the 'encode_end' history into
    lifetime totals for the in-app Stats tab. Pure/offline — just parses the
    JSONL the pipeline already writes. Returns a dict with:
      count, total_original, total_compressed, bytes_saved, overall_ratio,
      total_time, by_type{video/audio/image:{count,original,compressed}},
      vmaf{count,avg,buckets{...}}, encoders{name:count}, files_first/last ts.
    """
    import glob as _glob
    base = log_dir or globals().get("_LOG_DIR") or "logs"
    agg = {
        "count": 0, "total_original": 0, "total_compressed": 0,
        "bytes_saved": 0, "overall_ratio": 0.0, "total_time": 0.0,
        "by_type": {}, "vmaf": {"count": 0, "avg": 0.0, "buckets": {}},
        "encoders": {}, "first_ts": None, "last_ts": None,
    }
    # VMAF buckets from worst to best (label -> [lo, hi)).
    _vbuckets = [("<80", -1e9, 80.0), ("80–90", 80.0, 90.0), ("90–95", 90.0, 95.0),
                 ("95–98", 95.0, 98.0), ("98+", 98.0, 1e9)]
    for _lbl, _, _ in _vbuckets:
        agg["vmaf"]["buckets"][_lbl] = 0
    _vmaf_sum = 0.0
    try:
        files = sorted(_glob.glob(os.path.join(base, "run_*.jsonl")))
    except Exception:
        files = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        d = json.loads(ln)
                    except Exception:
                        continue
                    if d.get("event") != "encode_end":
                        continue
                    try:
                        o = int(d.get("original_size") or 0)
                        c = int(d.get("compressed_size") or 0)
                    except Exception:
                        continue
                    if o <= 0 or c <= 0:
                        continue
                    agg["count"] += 1
                    agg["total_original"] += o
                    agg["total_compressed"] += c
                    try:
                        agg["total_time"] += float(d.get("time_taken") or 0.0)
                    except Exception:
                        pass
                    ts = d.get("ts")
                    if ts:
                        if agg["first_ts"] is None or ts < agg["first_ts"]:
                            agg["first_ts"] = ts
                        if agg["last_ts"] is None or ts > agg["last_ts"]:
                            agg["last_ts"] = ts
                    t = str(d.get("type") or "other").lower()
                    bt = agg["by_type"].setdefault(t, {"count": 0, "original": 0, "compressed": 0})
                    bt["count"] += 1; bt["original"] += o; bt["compressed"] += c
                    enc = str(d.get("encoder") or "").strip().lower()
                    if enc:
                        agg["encoders"][enc] = agg["encoders"].get(enc, 0) + 1
                    v = d.get("vmaf")
                    if isinstance(v, (int, float)) and v > 0:
                        agg["vmaf"]["count"] += 1
                        _vmaf_sum += float(v)
                        for lbl, lo, hi in _vbuckets:
                            if lo <= float(v) < hi:
                                agg["vmaf"]["buckets"][lbl] += 1
                                break
        except Exception:
            continue
    agg["bytes_saved"] = agg["total_original"] - agg["total_compressed"]
    if agg["total_original"] > 0:
        agg["overall_ratio"] = agg["total_compressed"] / agg["total_original"]
    if agg["vmaf"]["count"] > 0:
        agg["vmaf"]["avg"] = _vmaf_sum / agg["vmaf"]["count"]
    return agg

def _privacy_args(preset: str | None):
    p = str(preset or "default").lower()
    if p == "strict":
        return [
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-write_tmcd", "0",
            "-fflags", "+bitexact",
            "-flags:v", "+bitexact",
            "-flags:a", "+bitexact",
        ]
    elif p == "keep":
        return []
    else:  # default
        return ["-map_metadata", "-1"]

def _remux_copy(src: str, dst: str, extra_args: list[str] | None = None) -> bool:

    def _cmd_base():
        cmd = [FFMPEG, "-y", "-ignore_unknown", "-i", src, "-c", "copy"]
        if extra_args:
            cmd += list(extra_args)

        if str(dst).lower().endswith(".partial") or str(dst).lower().endswith(".mp4"):
            cmd += ["-f", "mp4"]

        cmd += ["-movflags", "+faststart", "-map_metadata", "-1", "-map_chapters", "-1"]
        return cmd

    variants = []

    v1 = _cmd_base() + [
        "-map", "0:v:0?", "-map", "0:a:0?", "-sn", "-dn", "-map", "-0:tmcd", "-map", "-0:d", dst
    ]
    variants.append(v1)

    v2 = _cmd_base() + ["-map", "0:v:0?", "-map", "0:a:0?", dst]
    variants.append(v2)

    v3 = _cmd_base() + [dst]
    variants.append(v3)

    tmp_dst = dst
    need_mv = False
    if str(dst).lower().endswith(".partial"):
        root, ext = os.path.splitext(dst)  # ext = ".partial"
        if root.lower().endswith(".mp4"):
            tmp_dst = root  # drop .partial for the actual encode
            need_mv = True
            v4 = _cmd_base()

            if "-f" not in v4:
                v4 += ["-f", "mp4"]
            v4.append(tmp_dst)
            variants.append(v4)

    for cmd in variants:
        p = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, startupinfo=si, creationflags=NO_WIN)
        if p.returncode == 0 and (os.path.exists(cmd[-1]) and os.path.getsize(cmd[-1]) > 0):

            if need_mv and os.path.exists(tmp_dst):
                try:
                    if os.path.exists(dst):
                        os.remove(dst)
                except Exception:
                    pass
                try:
                    os.replace(tmp_dst, dst)
                except Exception:

                    try:
                        import shutil as _sh
                        _sh.copyfile(tmp_dst, dst)
                        os.remove(tmp_dst)
                    except Exception:
                        return False
            return True
    return False


def _remux_smart(src: str, dst: str, privacy_args: list[str] | None = None) -> bool:

    import os, shutil, subprocess

    # Window-suppression for the ffmpeg child (Windows). These were previously
    # only defined in an unreachable block after `return False`, which made
    # `subprocess`/`si`/`NO_WIN` unbound function-locals — so _run_and_check
    # raised NameError on EVERY call and the whole passthrough remux silently
    # failed (callers caught it and fell through to a full re-encode).
    si = None
    NO_WIN = 0
    try:
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            NO_WIN = 0x08000000
    except Exception:
        si, NO_WIN = None, 0

    def _mk_base_maps():
        base = [
            "-ignore_unknown",
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-map", "0:v:0?",
            "-map", "0:a:0?",
            "-sn", "-dn",
            "-map", "-0:tmcd",
            "-map", "-0:d",
            "-movflags", "+faststart",
        ]
        if privacy_args:
            for a in privacy_args:
                if a not in base:
                    base.append(a)
        return base

    real_dst = dst
    need_rename = False
    if str(dst).lower().endswith(".partial") and str(dst).lower().endswith(".mp4.partial"):
        real_dst = dst[:-len(".partial")]  # strip only the ".partial"
        need_rename = True

    def _run_and_check(cmd):
        p = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, startupinfo=si, creationflags=NO_WIN)
        ok = (p.returncode == 0) and os.path.exists(cmd[-1]) and os.path.getsize(cmd[-1]) > 0
        return ok

    base_maps = _mk_base_maps()

    cmd1 = [FFMPEG, "-y", "-i", src, "-c", "copy", *base_maps, "-f", "mp4", real_dst]
    if _run_and_check(cmd1):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    cmd2 = [FFMPEG, "-y", "-i", src, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", *base_maps, "-f", "mp4", real_dst]
    if _run_and_check(cmd2):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    cmd3 = [
        FFMPEG, "-y", "-i", src,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-vf", "format=yuv420p", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        *base_maps, "-f", "mp4", real_dst
    ]
    if _run_and_check(cmd3):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    base_relaxed = ["-ignore_unknown", "-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart"]

    cmd4a = [FFMPEG, "-y", "-i", src, "-c", "copy", *base_relaxed, "-f", "mp4", real_dst]
    if _run_and_check(cmd4a):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    cmd4b = [FFMPEG, "-y", "-i", src, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", *base_relaxed, "-f", "mp4", real_dst]
    if _run_and_check(cmd4b):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    cmd4c = [
        FFMPEG, "-y", "-i", src,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-vf", "format=yuv420p", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        *base_relaxed, "-f", "mp4", real_dst
    ]
    if _run_and_check(cmd4c):
        if need_rename:
            try:
                if os.path.exists(dst): os.remove(dst)
            except Exception:
                pass
            try:
                os.replace(real_dst, dst)
            except Exception:
                try: shutil.copyfile(real_dst, dst); os.remove(real_dst)
                except Exception: return False
        return True

    return False


def _fix_bad_logging_formatters():
    
    safe_fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S"
    )
    root = logging.getLogger()
    for h in list(root.handlers):
        try:
            f = getattr(h, "formatter", None)
            if f and isinstance(getattr(f, "_fmt", None), str) and "%H" in f._fmt and "%(asctime)s" not in f._fmt:
                h.setFormatter(safe_fmt)
        except Exception:

            try:
                h.setFormatter(safe_fmt)
            except Exception:
                pass

try:
    from sklearn.linear_model import SGDRegressor as _SkSGD
except Exception:
    _SkSGD = None
try:
    from sklearn.ensemble import HistGradientBoostingRegressor as _SkHGBR
except Exception:
    _SkHGBR = None

# _make_sgd_model() + HeuristicLearner (a per-file CRF "seed learner") were removed
# 2026-07-06: the learner never trained (nothing consumed its CRF on size-targeted
# encodes, its model file was never written) and only emitted a misleading
# "CRF seed model untrained" warning every run. The ABR pipeline (abr_cache +
# quality_model) is the real, working rate learner. (_SkHGBR is still imported
# above — it's used by the advisor model, not by this removed code.)
# (class body removed with the learner)








       
from tkinter import simpledialog
import json
from tkinter import filedialog
import tkinter as tk
from tkinter import ttk, filedialog
from tkinter.scrolledtext import ScrolledText
from pathlib import Path
import pystray
from PIL import Image
import sys
import platform
import threading
from PIL import Image
import pystray
from tkinterdnd2 import DND_FILES, TkinterDnD
from win10toast import ToastNotifier
from win10toast import ToastNotifier

try:
    from win10toast import ToastNotifier as _TN
    _orig_toast = _TN.show_toast
    def _safe_toast(self, title, msg, duration=5, icon_path=None, threaded=True):
        try:
            _orig_toast(self, str(_normalize_text(title or "BitCrusher")), str(_normalize_text(msg or "")),
                        duration=int(duration or 5), icon_path=icon_path, threaded=True)
            return 1
        except Exception as e:
            try: LOG.warning("Toast suppressed: %r", e)
            except Exception: pass
            return 1
    _TN.show_toast = _safe_toast
except Exception:
    pass


import threading
from plyer import notification

import subprocess, platform


import os

def ensure_runtime_dirs():
    base_path = os.path.expanduser("~")  # Or wherever you want them
    settings_path = os.path.join(base_path, "BitCrusherSettings", "user_settings")
    heuristic_path = os.path.join(base_path, "BitCrusherSettings", "heuristics")

    os.makedirs(settings_path, exist_ok=True)
    os.makedirs(heuristic_path, exist_ok=True)

    return settings_path, heuristic_path



    def start(self): pass
    def stop(self): pass

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except ImportError:
    TkinterDnD = None
    DND_FILES = None


import os
import json
import platform

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

HEURISTICS_DIR    = os.path.join(SCRIPT_DIR, "heuristics")
USER_SETTINGS_DIR = os.path.join(SCRIPT_DIR, "user_settings")
os.makedirs(HEURISTICS_DIR, exist_ok=True)
os.makedirs(USER_SETTINGS_DIR, exist_ok=True)


def default_handbrake():
    return "HandBrakeCLI.exe" if platform.system() == "Windows" else "HandBrakeCLI"

def default_ffprobe():
    return "ffprobe.exe"    if platform.system() == "Windows" else "ffprobe"

def default_ffmpeg():
    return "ffmpeg.exe"     if platform.system() == "Windows" else "ffmpeg"

_FFMPEG_VERSION_CACHE: dict = {}

def ffmpeg_build_version() -> str:
    """Cached (per-process) ffmpeg build/version string, for ledger provenance
    -- a silent encoder-build upgrade can shift the rate-distortion curve, and
    without this tag there was no way to tell an old prediction's inputs from
    a new one's."""
    if "v" in _FFMPEG_VERSION_CACHE:
        return _FFMPEG_VERSION_CACHE["v"]
    v = ""
    try:
        _, _, ff = load_paths()
    except Exception:
        ff = default_ffmpeg()
    try:
        r = subprocess.run([ff or default_ffmpeg(), "-version"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, timeout=5)
        first_line = (r.stdout or "").splitlines()[0] if r.stdout else ""
        v = first_line.replace("ffmpeg version ", "").strip()[:80]
    except Exception:
        v = ""
    _FFMPEG_VERSION_CACHE["v"] = v
    return v

def load_paths():
    print(f"Looking for config.json at: {CONFIG_PATH}")

    if os.path.exists(CONFIG_PATH):
        print("Found config.json, contents:")
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        print(json.dumps(cfg, indent=2))
        hb = cfg.get("handbrake") or default_handbrake()
        fp = cfg.get("ffprobe")   or default_ffprobe()
        ff = cfg.get("ffmpeg")    or default_ffmpeg()
        return hb, fp, ff

    print("config.json not found; checking tools/ folder.")
    tools_dir = os.path.join(SCRIPT_DIR, "tools")

    hb_name = "HandBrakeCLI.exe" if platform.system() == "Windows" else "HandBrakeCLI"
    fp_name = "ffprobe.exe"    if platform.system() == "Windows" else "ffprobe"
    ff_name = "ffmpeg.exe"     if platform.system() == "Windows" else "ffmpeg"

    hb_path = os.path.join(tools_dir, hb_name)
    fp_path = os.path.join(tools_dir, fp_name)
    ff_path = os.path.join(tools_dir, ff_name)

    hb = hb_path if os.path.isfile(hb_path) else default_handbrake()
    fp = fp_path if os.path.isfile(fp_path) else default_ffprobe()
    ff = ff_path if os.path.isfile(ff_path) else default_ffmpeg()

    print(f"Using HandBrakeCLI at: {hb}")
    print(f"Using ffprobe    at: {fp}")
    print(f"Using ffmpeg     at: {ff}")

    return hb, fp, ff

HANDBRAKE_CLI, FFPROBE, FFMPEG = load_paths()
log_tool_paths(HANDBRAKE_CLI, FFMPEG, FFPROBE)
_set_ffmpeg_exec_path(FFMPEG)

def resource_path(rel_path: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, rel_path)

TOOLS_DIR          = resource_path("tools")
DEFAULT_HANDBRAKE  = os.path.join(TOOLS_DIR, "HandBrakeCLI.exe")
DEFAULT_FFMPEG     = os.path.join(TOOLS_DIR, "ffmpeg.exe")
DEFAULT_FFPROBE    = os.path.join(TOOLS_DIR, "ffprobe.exe")

MAX_SIZE_MB_DEFAULT = 10

SIZE_UNITS = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
TARGET_SIZE_MARGIN_RATIO = 0.005
TARGET_SIZE_MARGIN_MIN_BYTES = 16 * 1024
TARGET_SIZE_MARGIN_MAX_BYTES = 128 * 1024

def bytes_from_value_unit(val, unit):
    
    try:
        v = float(str(val).strip())
    except Exception:
        v = 0.0
    u = (str(unit or "MB").strip().upper())
    mul = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }.get(u, 1024**2)

    b = int(max(0, v) * mul)
    return b



def apply_target_size_margin(target_bytes):
    """
    Keep outputs slightly below platform hard limits (e.g. 10 MB upload caps)
    to avoid \"over the limit by a few bytes\" rejections.
    """
    t = int(max(1, target_bytes))
    margin = int(max(
        TARGET_SIZE_MARGIN_MIN_BYTES,
        min(TARGET_SIZE_MARGIN_MAX_BYTES, float(t) * float(TARGET_SIZE_MARGIN_RATIO)),
    ))
    return max(1, t - margin)


def human_bytes(n: int) -> str:
    n = int(max(0, n))
    for unit, mul in [("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)]:
        if n >= mul:
            return f"{n / mul:.2f} {unit}"
    return f"{n} B"


def _sanitize_int(s, default=0):
    try:
        return int(float(str(s).strip()))
    except Exception:
        return int(default)

DEFAULT_AUDIO_BITRATE = 128 * 1000
DEFAULT_CRF = 22
MIN_ACCEPTABLE_CRF = 28
ITERATIVE_MAX_ATTEMPTS = 6  # maximum iterations if binary search fails

PRESETS = {
    # Real-world upload caps (2026). apply_target_size_margin() shaves a small
    # safety margin so we sit just under each platform's hard limit.
    "Custom (use size below)": None,
    "Discord — Free (10 MB)": 10,
    "Discord — Nitro Basic (50 MB)": 50,
    "Discord — Nitro (500 MB)": 500,
    "WhatsApp (16 MB)": 16,
    "Email attachment (25 MB)": 25,
    "Gmail / Outlook (20 MB)": 20,
    "Telegram (2 GB)": 2000,
    "Twitter / X (512 MB)": 512,
    "Reddit video (1 GB)": 1000,
    "Imgur (200 MB)": 200,
    "Slack — Free (1 GB)": 1000,
    "Tiny file (5 MB)": 5,
    "Best quality (50 MB)": 50,
}


ADVANCED_DEFAULTS = {
    "auto_retry": True,
    "overshoot_ratio": 1.00,
    "two_pass_fallback": True,
    "grain_filter": True,
    "auto_retry_done": False,
    "two_pass_forced": False,   # ← COMMA HERE
    "iterative_max_attempts": 4,
    "encoder": "x264",
    "iterative": False,
    "two_pass": False,
    "manual_crf": "",
    "manual_bitrate": "",
    "output_prefix": "",
    "output_suffix": "_discord_ready",
    "audio_format": "auto",
    "image_format": "jpg",
    "concurrent": False,
    "auto_output_folder": False,
    "guetzli": False,
    "pngopt": False,
    "auto_jpeg": False,
    "hwaccel": "CPU",
    "measure_quality": True,   # measure VMAF of the final output
    "min_vmaf": 0,             # 0 = off; otherwise spend spare size budget to reach this VMAF
    # Which VMAF number the min_vmaf floor / transparency gate optimizes. "window"
    # (worst ~2s scene) beats the "average trap" where a good mean hides an ugly
    # scene. window|p5|p1|harmonic|mean.
    "vmaf_objective": "window",
    # Artifact/texture-aware preprocessing: probe-validated deband/deblock/denoise
    # prefilters, kept only when they beat the unfiltered encode at the same
    # bitrate. Skipped in fast mode.
    "smart_preproc": True,
    # Learned first-attempt bitrate seeding from the outcome ledger (stage 2a).
    # Acts only with >=3 similar past encodes, clamped, controller-corrected.
    "learned_seed": True,
    # AV1 film-grain synthesis: auto = probe the source and enable when grain is
    # measurably compressible (denoise + re-synthesize = better quality at the
    # same size on film grain / old cartoons); off = never; force = always.
    "film_grain": "auto",
    # Second perceptual metric (XPSNR, built into ffmpeg) measured alongside VMAF
    # as a cross-check — flags encodes where VMAF and XPSNR disagree (VMAF being
    # fooled). Never gates the encode; logged for the ledger. Off = VMAF only.
    "perceptual_crosscheck": True,
    # Advisory pre-flight guardrail (stage 2b): warns on predicted quality
    # collapse / size-cap overshoot and suggests a better codec on race-skipped
    # paths, from the ledger. Advisory only — never changes the encode itself.
    "preflight_advice": True,
    # Last-resort ceiling guard: when even the minimum feasible bitrate at native
    # resolution overshoots the size cap (tiny target / short clip), step the
    # resolution down and re-encode rather than ship an oversized file. Bounded
    # by max_job_seconds; only kept when the result actually lands under the cap.
    "ceiling_downscale_retry": True,
    "auto_codec": True,        # VMAF-probe the chosen codec vs AV1 and keep the winner
    "scene_zones": True,       # per-scene bitrate zones for x264/x265 two-pass
    "hw_decode": True,         # GPU-accelerated decode of the source (encode stays as chosen)
    "quality_mode": "max",     # fast | balanced | max (pack the size cap, measured)
    "max_job_seconds": 5400,   # wall-clock budget per file (0 = unlimited); stops
                               # retry/refine/packing improvement passes when hit
    # Accept-window under the ceiling: results within this band stop the retry
    # loop. 0.80% used to declare a 98.5%-full file a "miss" and burn 4 more
    # full re-encodes chasing ~0.1 VMAF; Max mode's packing pass still tops up.
    "target_tolerance_pct": 1.50,
    "target_tolerance_min_bytes": 120000,
    "max_target_attempts": 8,
    "pdf_force_rasterize": True,
    "pdf_tolerance": 0.10,
    "pdf_min_dpi": 90,
    "pdf_max_dpi": 300,
    "pdf_linearize_if_close": True,
    "pdf_raster_min_dpi": 110,
    "pdf_raster_max_dpi": 220,
    "pdf_raster_min_q": 60,
    "pdf_raster_max_q": 90,
    "pdf_raster_max_iters": 7,
    # --- Batch-1 quality-of-life features -----------------------------------
    # Discord playback-compatibility: when ON, restrict the codec race to
    # h264+aac (mp4) so the result always plays inline on Discord mobile/old
    # clients, accepting the size cost. OFF by default (never silently caps
    # quality; the user opts in).
    "discord_compat": False,
    # Multi-audio-track handling: sources with >1 audio track (e.g. OBS/ShadowPlay
    # game+mic) used to silently drop track 2. "keepfirst" maps track 0 explicitly;
    # "mix" downmixes every audio track into one via amix.
    "audio_track_mode": "keepfirst",   # keepfirst | mix
    # Embed a sibling .lrc lyric file into the output tags of audio encodes.
    "embed_lyrics": True,
    # After a successful encode, place the output file on the Windows clipboard
    # (CF_HDROP) so one Ctrl+V drops it into Discord.
    "copy_to_clipboard": False,
}


def _normalize_drop_path(p: str) -> str:

    try:
        s = str(p).strip()
        if (s.startswith("{") and s.endswith("}")) or (s.startswith()) or (s.startswith()):
            s = s[1:-1]
        return os.path.normpath(s)
    except Exception:
        return str(p)


# =====================================================================
# Batch-1 feature helpers: clipboard (CF_HDROP), multi-audio-track, lyrics
# =====================================================================

def set_clipboard_files(paths) -> bool:
    """
    Put one or more real files on the Windows clipboard as CF_HDROP, so a single
    Ctrl+V pastes them into Explorer, Discord, chat apps, etc. Native (ctypes) —
    no third-party dependency and works from a worker thread. No-op returning
    False on non-Windows or on any failure. Offline by design.
    """
    if os.name != "nt":
        return False
    try:
        files = [os.path.abspath(p) for p in (paths or [])
                 if isinstance(p, str) and p and os.path.exists(p)]
    except Exception:
        files = []
    if not files:
        return False
    try:
        import ctypes, struct
        from ctypes import wintypes

        CF_HDROP = 15
        GMEM_MOVEABLE = 0x0002

        # DROPFILES header (20 bytes) + double-null-terminated wide file list.
        # struct DROPFILES { DWORD pFiles; POINT pt; BOOL fNC; BOOL fWide; }
        # pFiles = offset to the file list (== sizeof(DROPFILES) == 20);
        # fWide = 1 marks the list as UTF-16.
        joined = "\0".join(files) + "\0\0"
        file_bytes = joined.encode("utf-16-le")
        header = struct.pack("<Iiiii", 20, 0, 0, 0, 1)
        payload = header + file_bytes

        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32
        # Declare handle/pointer types explicitly — the ctypes default (c_int) is
        # 32-bit and silently truncates 64-bit handles/pointers, which made
        # SetClipboardData receive a bad HGLOBAL and fail on 64-bit Python.
        k32.GlobalAlloc.restype = wintypes.HGLOBAL
        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalLock.restype = wintypes.LPVOID
        k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalFree.restype = wintypes.HGLOBAL
        k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        u32.OpenClipboard.restype = wintypes.BOOL
        u32.OpenClipboard.argtypes = [wintypes.HWND]
        u32.EmptyClipboard.restype = wintypes.BOOL
        u32.CloseClipboard.restype = wintypes.BOOL
        u32.SetClipboardData.restype = wintypes.HANDLE
        u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

        h_global = k32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not h_global:
            return False
        ptr = k32.GlobalLock(h_global)
        if not ptr:
            k32.GlobalFree(h_global)
            return False
        ctypes.memmove(ptr, payload, len(payload))
        k32.GlobalUnlock(h_global)

        if not u32.OpenClipboard(None):
            k32.GlobalFree(h_global)
            return False
        try:
            u32.EmptyClipboard()
            if not u32.SetClipboardData(CF_HDROP, h_global):
                # Ownership only transfers to the clipboard on success; free on failure.
                k32.GlobalFree(h_global)
                return False
        finally:
            u32.CloseClipboard()
        return True
    except Exception:
        return False


def _count_audio_streams(input_path: str) -> int:
    """Number of audio streams in a media file (ffprobe). 0 on failure."""
    try:
        out = _sp_check_output(
            [FFPROBE, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", input_path],
            text=True, startupinfo=si, creationflags=NO_WIN)
        return len([ln for ln in (out or "").splitlines() if ln.strip()])
    except Exception:
        return 0


def _audio_track_plan(input_path: str, advanced_options: dict) -> dict:
    """
    Decide how to map audio for a multi-track source. Returns a dict:
      {"n": <stream count>, "mode": "keepfirst"|"mix", "multi": bool, "notice": str|None}
    Only sources with >1 audio track get a notice / non-default mapping; single-track
    sources are unaffected (mode "keepfirst", multi False, notice None).
    """
    mode = str((advanced_options or {}).get("audio_track_mode")
               or ADVANCED_DEFAULTS.get("audio_track_mode", "keepfirst")).strip().lower()
    if mode not in ("keepfirst", "mix"):
        mode = "keepfirst"
    n = _count_audio_streams(input_path)
    multi = n > 1
    notice = None
    if multi:
        if mode == "mix":
            notice = (f"[Audio] {n} audio tracks found - mixing them into one "
                      f"(amix) so nothing is dropped (e.g. game + mic).")
        else:
            notice = (f"[Audio] {n} audio tracks found - keeping track 1 only. "
                      f"Switch multi-track mode to 'mix' to merge them.")
    return {"n": n, "mode": mode, "multi": multi, "notice": notice}


def _audio_map_ffmpeg_args(plan: dict, audio_copy_ref: dict | None = None) -> list[str]:
    """
    Build the ffmpeg stream-map/filter args for a multi-track plan, to be spliced
    into a video encode command right after the input. Single-track / keepfirst
    with a single track returns an explicit-but-harmless map. For "mix" it returns
    an amix filter_complex; the caller must re-encode audio (amix can't -c:a copy),
    so audio_copy_ref["audio_copy"] is forced False when provided.
    """
    plan = plan or {}
    mode = plan.get("mode", "keepfirst")
    n = int(plan.get("n") or 0)
    if mode == "mix" and n > 1:
        if isinstance(audio_copy_ref, dict):
            audio_copy_ref["audio_copy"] = False
        return ["-filter_complex",
                f"[0:a]amix=inputs={n}:normalize=0[bcaout]",
                "-map", "0:v:0?", "-map", "[bcaout]"]
    # keepfirst (default): map video + the first audio track explicitly so
    # ffmpeg's "pick the stream with the most channels" default can't grab the
    # wrong track and silently drop track 1.
    return ["-map", "0:v:0?", "-map", "0:a:0?"]


def _read_sibling_lrc(input_path: str) -> str | None:
    """
    Return the text of a .lrc lyric file sitting next to input_path (same stem),
    or None. Offline, tolerant of the common encodings .lrc ships in.
    """
    try:
        base, _ = os.path.splitext(input_path)
    except Exception:
        return None
    for cand in (base + ".lrc", base + ".LRC"):
        try:
            if os.path.isfile(cand):
                for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
                    try:
                        with open(cand, "r", encoding=enc) as f:
                            txt = f.read().strip()
                        if txt:
                            return txt
                    except Exception:
                        continue
        except Exception:
            continue
    return None


def _embed_lyrics_into(output_path: str, lyrics: str, status_cb=None) -> bool:
    """
    Add a `lyrics` metadata tag to an existing audio file in place (stream copy +
    -metadata), remuxing through a temp file. ffmpeg maps the generic `lyrics`
    key to the container-appropriate frame (USLT / ©lyr / vorbis comment). No-op
    returning False if the container can't be rewritten. Offline.
    """
    if not lyrics or not output_path or not os.path.isfile(output_path):
        return False
    try:
        root, ext = os.path.splitext(output_path)
        tmp = root + "._bc_lrc_" + ext
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-i", output_path, "-map", "0", "-c", "copy",
               "-metadata", f"lyrics={lyrics}", tmp]
        proc = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, startupinfo=si, creationflags=NO_WIN)
        if proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, output_path)
            if callable(status_cb):
                try:
                    status_cb("[Lyrics] Embedded lyrics from sibling .lrc into the output tags.",
                              level="INFO")
                except Exception:
                    pass
            return True
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    except Exception:
        pass
    return False


# =====================================================================
# Single-instance IPC + Explorer "Send to BitCrusher" integration
# =====================================================================

_BC_IPC_HOST = "127.0.0.1"
_BC_IPC_PORT = 49222          # dedicated GUI hand-off port (agent HTTP uses 8765)
_BC_STARTUP_FILES: list[str] = []   # set by --enqueue when no instance was running


def _bc_ipc_send(paths, timeout: float = 1.0) -> bool:
    """
    Hand a list of file paths to an already-running BitCrusher GUI over the local
    loopback IPC port. Returns True if a running instance accepted them, False if
    no instance is listening (caller should then launch the GUI itself).
    """
    import socket
    try:
        real = [os.path.abspath(p) for p in (paths or [])
                if isinstance(p, str) and p and os.path.exists(p)]
    except Exception:
        real = []
    if not real:
        return False
    try:
        with socket.create_connection((_BC_IPC_HOST, _BC_IPC_PORT), timeout=timeout) as sk:
            body = ("BCENQUEUE\n" + "\n".join(real) + "\n").encode("utf-8")
            sk.sendall(body)
            return True
    except Exception:
        return False


def _sendto_shortcut_path() -> str:
    """Path to the per-user SendTo shortcut (…\\SendTo\\BitCrusher.lnk)."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "Microsoft", "Windows", "SendTo", "BitCrusher.lnk")


def _sendto_launch_target() -> tuple[str, str]:
    """
    (target_exe, arguments) that a SendTo shortcut should invoke. Explorer appends
    the selected file path(s) after `arguments`, giving `<exe> <args> "file"`.
    Prefers pythonw.exe (no console flash) when running from source; uses the
    frozen exe directly when packaged.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, "--enqueue"
    exe = sys.executable or "python.exe"
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(pyw):
        exe = pyw
    script = os.path.abspath(__file__)
    return exe, f'"{script}" --enqueue'


def register_send_to() -> tuple[bool, str]:
    """
    Create the 'Send to > BitCrusher' shortcut for the current user (no admin
    needed — it just drops a .lnk in the user's SendTo folder). Returns (ok, msg).
    """
    if os.name != "nt":
        return False, "Send To integration is Windows-only."
    lnk = _sendto_shortcut_path()
    target, arguments = _sendto_launch_target()
    workdir = os.path.dirname(os.path.abspath(__file__))
    icon = os.path.join(workdir, "icon.png")
    try:
        os.makedirs(os.path.dirname(lnk), exist_ok=True)
        # Build the shortcut via WScript.Shell (ships with Windows — no pywin32).
        ps = (
            "$w = New-Object -ComObject WScript.Shell; "
            f"$s = $w.CreateShortcut('{lnk}'); "
            f"$s.TargetPath = '{target}'; "
            f"$s.Arguments = '{arguments}'; "
            f"$s.WorkingDirectory = '{workdir}'; "
            f"$s.Description = 'Send file(s) to BitCrusher'; "
            f"$s.Save()"
        )
        proc = _sp_run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       startupinfo=si, creationflags=NO_WIN)
        if proc.returncode == 0 and os.path.isfile(lnk):
            return True, f"'Send to > BitCrusher' installed.\n{lnk}"
        return False, f"Shortcut creation failed: {(proc.stderr or '').strip()[:200]}"
    except Exception as e:
        return False, f"Send To registration error: {e}"


def unregister_send_to() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Send To integration is Windows-only."
    lnk = _sendto_shortcut_path()
    try:
        if os.path.isfile(lnk):
            os.remove(lnk)
            return True, "'Send to > BitCrusher' removed."
        return True, "'Send to > BitCrusher' was not installed."
    except Exception as e:
        return False, f"Send To removal error: {e}"


from functools import lru_cache

def install_drop_highlight(frame):
    normal_bg = frame.cget("background") if str(frame.cget("style")) == "" else None
    def _enter(_e=None):
        try:
            frame.configure(style="Card.TFrame")
        except Exception:
            if normal_bg is not None:
                frame.configure(background=_hsl_shift(CARD_BG, l_mul=1.08))
    def _leave(_e=None):
        try:
            frame.configure(style="Card.TFrame")
        except Exception:
            if normal_bg is not None:
                frame.configure(background=CARD_BG)
    frame.bind("<Enter>", _enter)
    frame.bind("<Leave>", _leave)
    return frame



@lru_cache(maxsize=64)
def _cached_thumb(path, maxsize=(512, 288)):
    im = Image.open(path)
    im.thumbnail(maxsize, Image.LANCZOS)
    return ImageTk.PhotoImage(im)

def update_preview(self, *_):
    try:
        sel = self.queue_box.curselection()
    except Exception:
        sel = ()
    if not sel:
        self.update_status("No file selected.", level="INFO")
        return
    try:
        fpath = self.queue_box.get(sel[0])
        self.update_status(f"Selected: {os.path.basename(fpath)}", level="INFO")
    except Exception:
        pass


import colorsys, json, tkinter as tk, tkinter.filedialog as fd
from tkinter import ttk
from tkinter import simpledialog as sd

THEMES = {
    "Dark": {
        "APP_BG":"#14161A", "CARD_BG":"#1C1F24",
        "FG":"#E6E8EB", "FG_SUB":"#A6ABB3",
        "ACCENT":"#7C5CFF", "ACCENT_2":"#3DDC97",
        "ERROR":"#FF6B6B", "WARN":"#FFB020",
        "TITLE":"#C9B8FF"
    },
    "Light": {  # high-contrast light, no “blown” whites
        "APP_BG":"#F4F6F9", "CARD_BG":"#FFFFFF",
        "FG":"#1F2328", "FG_SUB":"#5A6470",
        "ACCENT":"#4C5BD4", "ACCENT_2":"#139D6F",
        "ERROR":"#C62828", "WARN":"#B46913",
        "TITLE":"#3949AB"
    },
    "Autumn": {
        "APP_BG":"#1E1510", "CARD_BG":"#2A1C14",
        "FG":"#F3E9DC", "FG_SUB":"#D8C7B6",
        "ACCENT":"#E07A5F", "ACCENT_2":"#F2CC8F",
        "ERROR":"#FF6B6B", "WARN":"#F4A261",
        "TITLE":"#F2A679"
    },
    "Winter": {
        "APP_BG":"#0E141B", "CARD_BG":"#15202B",
        "FG":"#E4F1FF", "FG_SUB":"#A8C0D6",
        "ACCENT":"#58A6FF", "ACCENT_2":"#7EE1D2",
        "ERROR":"#FF6B6B", "WARN":"#FFB020",
        "TITLE":"#9AD1FF"
    },
    "Midnight": {
        "APP_BG":"#0B0E14", "CARD_BG":"#131722",
        "FG":"#E2E8F0", "FG_SUB":"#94A3B8",
        "ACCENT":"#38BDF8", "ACCENT_2":"#A78BFA",
        "ERROR":"#F87171", "WARN":"#FBBF24",
        "TITLE":"#7DD3FC"
    },
    "OLED": {
        "APP_BG":"#000000", "CARD_BG":"#101010",
        "FG":"#EDEDED", "FG_SUB":"#9A9A9A",
        "ACCENT":"#00D4AA", "ACCENT_2":"#FF6BCB",
        "ERROR":"#FF5C5C", "WARN":"#FFC53D",
        "TITLE":"#5EEAD4"
    },
    "Nord": {
        "APP_BG":"#2E3440", "CARD_BG":"#3B4252",
        "FG":"#ECEFF4", "FG_SUB":"#AEB6C4",
        "ACCENT":"#88C0D0", "ACCENT_2":"#A3BE8C",
        "ERROR":"#BF616A", "WARN":"#EBCB8B",
        "TITLE":"#8FBCBB"
    },
    "Dracula": {
        "APP_BG":"#21222C", "CARD_BG":"#282A36",
        "FG":"#F8F8F2", "FG_SUB":"#9FA3BC",
        "ACCENT":"#BD93F9", "ACCENT_2":"#50FA7B",
        "ERROR":"#FF5555", "WARN":"#F1FA8C",
        "TITLE":"#FF79C6"
    },
    "Mocha": {
        "APP_BG":"#1E1E2E", "CARD_BG":"#27273A",
        "FG":"#CDD6F4", "FG_SUB":"#9399B2",
        "ACCENT":"#CBA6F7", "ACCENT_2":"#94E2D5",
        "ERROR":"#F38BA8", "WARN":"#F9E2AF",
        "TITLE":"#B4BEFE"
    },
}

def _themes_dir():
    d = os.path.join(USER_SETTINGS_DIR, "themes")
    os.makedirs(d, exist_ok=True)
    return d

def _validate_theme_dict(d: dict) -> bool:
    req = {"APP_BG","CARD_BG","FG","FG_SUB","ACCENT","ACCENT_2","ERROR","WARN","TITLE"}
    return isinstance(d, dict) and req.issubset(d.keys()) and all(isinstance(d[k], str) for k in req)

def load_user_themes_at_startup():
    
    d = _themes_dir()
    try:
        for fn in os.listdir(d):
            if not fn.lower().endswith(".json"):
                continue
            p = os.path.join(d, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if _validate_theme_dict(data):
                    name = os.path.splitext(fn)[0]
                    THEMES[name] = data
            except Exception:
                pass
    except Exception:
        pass

load_user_themes_at_startup()

APP_BG=CARD_BG=FG=FG_SUB=ACCENT=ACCENT_2=ERROR=WARN=TITLE=None

def _hsl_shift(hex_color: str, h_delta=0.0, s_mul=1.0, l_mul=1.0) -> str:
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    h,l,s = colorsys.rgb_to_hls(r,g,b)
    h = (h + h_delta) % 1.0
    s = max(0.0, min(1.0, s * s_mul))
    l = max(0.0, min(1.0, l * l_mul))
    r,g,b = colorsys.hls_to_rgb(h,l,s)
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

def _use_palette(name: str):
    
    global APP_BG, CARD_BG, FG, FG_SUB, ACCENT, ACCENT_2, ERROR, WARN, TITLE
    p = THEMES.get(name, THEMES["Dark"])
    APP_BG, CARD_BG = p["APP_BG"], p["CARD_BG"]
    FG, FG_SUB      = p["FG"], p["FG_SUB"]
    ACCENT, ACCENT_2= p["ACCENT"], p["ACCENT_2"]
    ERROR, WARN     = p["ERROR"], p["WARN"]
    TITLE           = p["TITLE"]

def _is_light_color(hex_color: str) -> bool:
    """True if the colour is perceptually light (so dark text should sit on it)."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 150
    except Exception:
        return False


def _contrast_fg(bg: str) -> str:
    """Pick black or white text for maximum contrast against the given background."""
    return "#101215" if _is_light_color(bg) else "#FFFFFF"


def apply_theme(style: ttk.Style, theme_name: str="Dark"):

    _use_palette(theme_name)
    style.theme_use("clam")

    light_mode = _is_light_color(APP_BG)
    # Theme-derived neutrals (previously hardcoded dark values that broke light themes).
    border_col  = _hsl_shift(CARD_BG, l_mul=(0.85 if light_mode else 1.35))
    disabled_bg = _hsl_shift(CARD_BG, l_mul=(0.94 if light_mode else 1.12))
    disabled_fg = _hsl_shift(FG_SUB,  l_mul=(1.15 if light_mode else 0.75))
    hover_card  = _hsl_shift(CARD_BG, l_mul=(0.96 if light_mode else 1.10))
    btn_fg      = _contrast_fg(ACCENT)

    style.configure(".", background=APP_BG, foreground=FG, bordercolor=border_col,
                    troughcolor=CARD_BG, focuscolor=ACCENT, selectbackground=ACCENT,
                    selectforeground=btn_fg, insertcolor=FG)
    style.configure("TFrame", background=APP_BG)
    style.configure("Card.TFrame", background=CARD_BG)
    style.configure("TLabel", background=APP_BG, foreground=FG)
    style.configure("Sub.TLabel", background=APP_BG, foreground=FG_SUB)
    style.configure("Title.TLabel", background=APP_BG, foreground=TITLE, font=("Segoe UI Semibold", 20))

    theme = THEMES.get(theme_name, THEMES.get("Dark", {}))
    RADIUS = int(theme.get("_RADIUS", 6))
    PAD    = float(theme.get("_PADDING_SCALE", 1.00))
    BORD   = int(theme.get("_BORDER_WIDTH", 1))


    btn_bg  = _hsl_shift(ACCENT, l_mul=0.88)
    btn_bg2 = ACCENT
    style.configure("TButton",
        font=("Segoe UI", 10, "bold"), padding=(int(12*PAD), int(8*PAD)), borderwidth=BORD,
        background=btn_bg, foreground=btn_fg, bordercolor=border_col, focuscolor=btn_fg)
    style.map("TButton",
        background=[("pressed", _hsl_shift(ACCENT, l_mul=0.75)), ("active", btn_bg2), ("disabled", disabled_bg)],
        foreground=[("disabled", disabled_fg)])

    style.configure("Ghost.TButton",
        font=("Segoe UI", 10), padding=(int(10*PAD), int(6*PAD)), borderwidth=BORD,
        background=CARD_BG, foreground=FG, bordercolor=border_col, relief="flat")
    style.map("Ghost.TButton",
        background=[("active", hover_card)])

    entry_bg    = _hsl_shift(CARD_BG, l_mul=(0.98 if light_mode else 1.06))
    entry_bg_ro = _hsl_shift(CARD_BG, l_mul=(0.97 if light_mode else 1.02))
    entry_fg_dis= disabled_fg

    style.configure("Dark.TEntry",
        fieldbackground=entry_bg, foreground=FG, padding=int(6*PAD), borderwidth=BORD,
        bordercolor=border_col, relief="flat", insertcolor=FG)
    style.map("Dark.TEntry",
        fieldbackground=[("focus", entry_bg), ("!focus", entry_bg), ("disabled", disabled_bg)],
        bordercolor=[("focus", ACCENT)],
        foreground=[("disabled", entry_fg_dis)])
    # Style plain TEntry/TCombobox/TSpinbox too so unstyled widgets match the theme.
    style.configure("TEntry",
        fieldbackground=entry_bg, foreground=FG, padding=int(6*PAD), borderwidth=BORD,
        bordercolor=border_col, relief="flat", insertcolor=FG)
    style.map("TEntry",
        bordercolor=[("focus", ACCENT)],
        foreground=[("disabled", entry_fg_dis)])

    for _cb_style in ("Dark.TCombobox", "TCombobox"):
        style.configure(_cb_style,
            fieldbackground=entry_bg, background=CARD_BG, foreground=FG,
            padding=int(4*PAD), borderwidth=BORD, bordercolor=border_col, relief="flat",
            arrowcolor=FG_SUB, insertcolor=FG)
        style.map(_cb_style,
            fieldbackground=[("readonly", entry_bg_ro), ("!readonly", entry_bg)],
            bordercolor=[("focus", ACCENT)],
            arrowcolor=[("active", ACCENT)],
            foreground=[("disabled", entry_fg_dis)])

    style.configure("TSpinbox",
        fieldbackground=entry_bg, background=CARD_BG, foreground=FG,
        padding=int(4*PAD), borderwidth=BORD, bordercolor=border_col, relief="flat",
        arrowcolor=FG_SUB, insertcolor=FG)
    style.map("TSpinbox",
        bordercolor=[("focus", ACCENT)],
        arrowcolor=[("active", ACCENT)],
        foreground=[("disabled", entry_fg_dis)])

    style.configure("Accent.Horizontal.TProgressbar",
        troughcolor=CARD_BG, background=ACCENT, bordercolor=CARD_BG,
        lightcolor=ACCENT, darkcolor=_hsl_shift(ACCENT, l_mul=0.8), thickness=10)
    style.configure("Horizontal.TProgressbar",
        troughcolor=CARD_BG, background=ACCENT, bordercolor=CARD_BG,
        lightcolor=ACCENT, darkcolor=_hsl_shift(ACCENT, l_mul=0.8))

    style.configure("TCheckbutton", background=APP_BG, foreground=FG,
                    indicatorcolor=entry_bg, focuscolor=APP_BG)
    style.map("TCheckbutton",
        indicatorcolor=[("selected", ACCENT)],
        foreground=[("disabled", FG_SUB)])
    style.configure("TRadiobutton", background=APP_BG, foreground=FG,
                    indicatorcolor=entry_bg, focuscolor=APP_BG)
    style.map("TRadiobutton",
        indicatorcolor=[("selected", ACCENT)],
        foreground=[("disabled", FG_SUB)])

    # Notebook tabs: flat, accent-underlined selection instead of grey clam defaults.
    style.configure("TNotebook", background=APP_BG, borderwidth=0, tabmargins=(8, 6, 8, 0))
    style.configure("TNotebook.Tab",
        background=CARD_BG, foreground=FG_SUB, padding=(int(14*PAD), int(7*PAD)),
        borderwidth=0, font=("Segoe UI", 10))
    style.map("TNotebook.Tab",
        background=[("selected", _hsl_shift(ACCENT, l_mul=0.88)), ("active", hover_card)],
        foreground=[("selected", btn_fg), ("active", FG)])

    # Scrollbars: slim, theme-tinted.
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar",
            background=_hsl_shift(CARD_BG, l_mul=(0.88 if light_mode else 1.25)),
            troughcolor=APP_BG, bordercolor=APP_BG, arrowcolor=FG_SUB, relief="flat",
            width=11, arrowsize=11)
        style.map(f"{orient}.TScrollbar",
            background=[("active", ACCENT)],
            arrowcolor=[("active", btn_fg)])

    style.configure("TScale", background=APP_BG, troughcolor=CARD_BG)
    style.configure("Horizontal.TScale", background=APP_BG, troughcolor=CARD_BG)
    style.configure("TSeparator", background=border_col)
    style.configure("TMenubutton", background=CARD_BG, foreground=FG,
                    bordercolor=border_col, arrowcolor=FG_SUB,
                    padding=(int(10*PAD), int(6*PAD)))
    style.map("TMenubutton", background=[("active", hover_card)])

    style.configure("Treeview",
        background=entry_bg, fieldbackground=entry_bg, foreground=FG,
        bordercolor=border_col, borderwidth=BORD, rowheight=int(28*PAD),
        font=("Segoe UI", 9))
    style.map("Treeview",
        background=[("selected", ACCENT)],
        foreground=[("selected", btn_fg)])
    style.configure("Treeview.Heading",
        background=CARD_BG, foreground=FG_SUB, relief="flat",
        font=("Segoe UI", 9, "bold"), padding=(int(6*PAD), int(5*PAD)))
    style.map("Treeview.Heading", background=[("active", hover_card)])

    # Standard labelframe layout (custom layouts double-render the label);
    # the label sits on the top-left of the border like a section heading.
    style.configure("Card.TLabelframe", background=APP_BG, borderwidth=BORD, relief="solid",
                    bordercolor=border_col, labeloutside=False)
    style.configure("Card.TLabelframe.Label", background=APP_BG, foreground=FG_SUB,
                    font=("Segoe UI", 9, "bold"), padding=(int(6*PAD), 0))
    style.configure("TLabelframe", background=APP_BG, borderwidth=BORD, relief="flat",
                    bordercolor=border_col)
    style.configure("TLabelframe.Label", background=APP_BG, foreground=FG_SUB)

def retheme_runtime(self, style: ttk.Style, theme_name: str):
    
    apply_theme(style, theme_name)

    try:
        self.root.configure(bg=APP_BG)
    except Exception:
        pass

    try:
        if hasattr(self, "title_label"):
            self.title_label.configure(style="Title.TLabel")
    except Exception:
        pass
    try:
        self.queue_box.configure(
            bg=_hsl_shift(CARD_BG, l_mul=1.0), fg=FG,
            highlightthickness=0, borderwidth=0,
            selectbackground=_hsl_shift(ACCENT, l_mul=1.0),
            selectforeground="#ffffff"
        )
    except Exception:
        pass
    try:
        self.log_text.configure(
            background=_hsl_shift(CARD_BG, l_mul=0.98),
            foreground=FG, insertbackground=FG,
            highlightthickness=0, borderwidth=0
        )
    except Exception:
        pass
    try:
        self.preview_label.configure(bg=_hsl_shift(CARD_BG, l_mul=0.98), fg=FG)
    except Exception:
        pass

    import tkinter as tk
    entry_bg    = _hsl_shift(CARD_BG, l_mul=1.06 if CARD_BG != "#FFFFFF" else 0.98)
    entry_bg_ro = _hsl_shift(CARD_BG, l_mul=1.02 if CARD_BG != "#FFFFFF" else 0.97)

    def _retint(w):
        # Windows that paint their own colours (the Theme Lab's swatches and
        # preview panel) opt out — the recursive walk used to stomp their
        # hand-painted tk.Labels with APP_BG, turning every swatch dark.
        if getattr(w, "_bc_no_retint", False):
            return
        try:

            if isinstance(w, (tk.Frame, tk.Toplevel, tk.Canvas)):
                try: w.configure(bg=APP_BG)
                except Exception: pass
            if isinstance(w, tk.Label):
                try: w.configure(bg=APP_BG, fg=FG)
                except Exception: pass
            if isinstance(w, tk.LabelFrame):
                try: w.configure(bg=CARD_BG, fg=FG)
                except Exception: pass

            if isinstance(w, tk.Entry):
                try:
                    w.configure(bg=entry_bg, fg=FG, insertbackground=FG,
                                disabledbackground=entry_bg_ro,
                                highlightthickness=0, borderwidth=1, relief="flat")
                except Exception: pass
            if isinstance(w, tk.Checkbutton):
                try:
                    w.configure(bg=CARD_BG, fg=FG,
                                activebackground=CARD_BG, activeforeground=FG,
                                selectcolor=CARD_BG, highlightthickness=0, borderwidth=0)
                except Exception: pass
            if isinstance(w, tk.Listbox):
                try:
                    w.configure(bg=entry_bg, fg=FG, highlightthickness=0, borderwidth=0,
                                selectbackground=ACCENT, selectforeground=_contrast_fg(ACCENT))
                except Exception: pass
            if isinstance(w, tk.Text):
                try:
                    w.configure(bg=_hsl_shift(CARD_BG, l_mul=0.98), fg=FG, insertbackground=FG,
                                highlightthickness=0, borderwidth=0,
                                selectbackground=ACCENT, selectforeground=_contrast_fg(ACCENT))
                except Exception: pass
            if isinstance(w, tk.Spinbox):
                try:
                    w.configure(bg=entry_bg, fg=FG, insertbackground=FG,
                                buttonbackground=CARD_BG, highlightthickness=0, relief="flat")
                except Exception: pass
            if isinstance(w, tk.Button):
                try:
                    w.configure(bg=CARD_BG, fg=FG, activebackground=_hsl_shift(CARD_BG, l_mul=1.1),
                                activeforeground=FG, highlightthickness=0, relief="flat")
                except Exception: pass
        except Exception:
            pass

        for c in w.winfo_children():
            _retint(c)

    try:
        _retint(self.root)
    except Exception:
        pass

def _validate_theme_dict(d: dict) -> bool:
    req = {"APP_BG","CARD_BG","FG","FG_SUB","ACCENT","ACCENT_2","ERROR","WARN","TITLE"}
    return isinstance(d, dict) and req.issubset(d.keys()) and all(isinstance(d[k], str) for k in req)

def save_current_theme_as(self):
    
    name = getattr(self, "theme_var", None).get() if hasattr(self, "theme_var") else "Dark"
    try:
        p = fd.asksaveasfilename(defaultextension=".json",
                                 filetypes=[("JSON", "*.json")],
                                 initialfile=f"{name}.json",
                                 title="Save Current Theme As...")
        if not p: return
        with open(p, "w", encoding="utf-8") as f:
            json.dump(THEMES[name], f, indent=2)
        log_info(f"Saved theme '{name}' to: {p}")
    except Exception:
        log_exc("Failed to save theme")

def load_custom_theme(self):
    
    try:
        p = fd.askopenfilename(filetypes=[("JSON", "*.json")], title="Load Theme JSON")
        if not p: return
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not _validate_theme_dict(data):
            log_err("Invalid theme file. Missing required keys.")
            return

        import os
        name = os.path.splitext(os.path.basename(p))[0]
        THEMES[name] = data

        if hasattr(self, "rebuild_themes_menu"): self.rebuild_themes_menu()
        self.theme_var.set(name)
        animated_retheme(self, name)
        log_info(f"Loaded custom theme '{name}' from: {p}")
    except Exception:
        log_exc("Failed to load theme")


def pulsate(widget, base=1.0, delta=0.05, period=16, _dir=1):
    try:
        scale = base + delta*_dir
        widget.tk.call(widget, 'scale', 0, 0, scale, scale)
    except Exception:
        pass  # not every widget supports tk scale; safe to ignore
    widget.after(period, lambda: pulsate(widget, base, delta, period, -_dir))


def shimmer_progressbar(pb):

    def _loop():
        try:
            pb.step(1)
        except Exception:
            return
        pb.after(15, _loop)
    _loop()


def fade_window(win, start=0.0, end=1.0, dur_ms=220):
    steps = max(1, int(dur_ms/16))
    delta = (end-start)/steps
    def _step(i=0, val=start):
        try:
            win.wm_attributes('-alpha', max(0.0, min(1.0, val)))
        except Exception:
            return
        if i < steps:
            win.after(16, _step, i+1, val+delta)
    _step()


def snackbar(root, text, millis=1800, kind="info"):
    bg = {"info": _hsl_shift(CARD_BG, l_mul=1.15), "warn": WARN, "error": ERROR}.get(kind, CARD_BG)
    bar = tk.Label(root, text=text, bg=bg, fg=_contrast_fg(bg),
                   font=("Segoe UI", 10, "bold"), padx=12, pady=8)
    bar.place(relx=0.5, rely=1.0, anchor="s", relwidth=0.6, y=-12)

    def kill():
        try: bar.destroy()
        except: pass
    root.after(millis, kill)


def _probe_audio_meta(input_path: str) -> dict:

    if get_media_type(input_path) != "audio":
        return {
            "duration": 0.0,
            "bitrate": DEFAULT_AUDIO_BITRATE,
            "sr": 48000,
            "ch": 2,
            "codec": "",
        }

    if not FFPROBE:
        return {
            "duration": 0.0,
            "bitrate": DEFAULT_AUDIO_BITRATE,
            "sr": 48000,
            "ch": 2,
            "codec": "",
        }

    cmd = [
        FFPROBE, "-v", "error",
        "-select_streams", "a:0",
        "-show_entries",
        "format=duration,bit_rate:stream=codec_name,bit_rate,sample_rate,channels",
        "-of", "json", input_path
    ]

    try:
        p = _sp_run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=si,
            creationflags=NO_WIN
        )
        if p.returncode != 0:

            log_err(f"ffprobe failed (rc={p.returncode}): {p.stderr.strip() if p.stderr else 'no stderr'}")
            return {
                "duration": 0.0,
                "bitrate": DEFAULT_AUDIO_BITRATE,
                "sr": 48000,
                "ch": 2,
                "codec": "",
            }
        js = json.loads(p.stdout or "{}")
    except Exception as e:
        log_err(f"ffprobe exec failed: {e}")
        return {
            "duration": 0.0,
            "bitrate": DEFAULT_AUDIO_BITRATE,
            "sr": 48000,
            "ch": 2,
            "codec": "",
        }

    fmt = (js.get("format") or {}) if isinstance(js, dict) else {}
    streams = (js.get("streams") or []) if isinstance(js, dict) else []
    s = streams[0] if streams else {}

    def _int(x, d):
        try:
            return int(x)
        except Exception:
            try:
                return int(float(x))
            except Exception:
                return d

    def _float(x, d):
        try:
            return float(x)
        except Exception:
            return d

    return {
        "duration": _float(fmt.get("duration"), 0.0),
        "bitrate":  _int(fmt.get("bit_rate") or s.get("bit_rate"), DEFAULT_AUDIO_BITRATE),
        "sr":       _int(s.get("sample_rate"), 48000),
        "ch":       _int(s.get("channels"), 2),
        "codec":    (s.get("codec_name") or "").lower(),
    }




def _should_copy_audio(target_bytes:int, dur:float, meta:dict) -> bool:
    
    try:
        if not target_bytes or not dur or dur <= 0:
            return False
        codec = (meta.get("codec") or "").lower()
        if codec not in {"aac", "opus", "vorbis"}:
            return False
        a_bits = int(meta.get("bitrate") or 0) * float(dur)
        return a_bits / 8.0 < 0.10 * target_bytes
    except Exception:
        return False

def _adaptive_two_pass(new_w:int, target_bitrate:int, force:bool=False) -> bool:
    # Prefer true 2-pass more broadly; it stabilizes size targeting on high-res screen captures.
    if force:
        return True
    if new_w >= 1920:          # 1080p+ (includes 1440p, 4K)
        return target_bitrate > 0 and target_bitrate <= 8_000_000
    elif new_w >= 1280:        # 720p–1080p
        return target_bitrate > 0 and target_bitrate <= 4_000_000
    else:                       # sub-720p
        return target_bitrate > 0 and target_bitrate <= 2_000_000


def _supports_true_two_pass(encoder: str) -> bool:
    """
    Only software encoders with real two-pass support.

    SVT-AV1 is included because its 1-pass VBR is too loose for size targeting
    on hard content — but only when the concrete AV1 encoder is actually SVT
    (libaom 2-pass exists but crawls, and the iterative pipeline runs many
    passes, so it stays on the single-shot path).
    """
    e = (encoder or "").lower()
    if any(k in e for k in ("x264", "libx264", "x265", "libx265", "libvpx-vp9", "vp9")):
        return True
    if any(k in e for k in ("svt-av1", "svtav1", "libsvtav1")):
        return True
    if "av1" in e and not any(k in e for k in ("aom", "nvenc", "qsv", "amf", "vaapi")):
        try:
            return best_av1_encoder() == "libsvtav1"
        except Exception:
            return False
    return False


_ENCODER_CANON = {
    "x264": "x264", "libx264": "x264", "h264": "x264", "avc": "x264",
    "x265": "x265", "libx265": "x265", "hevc": "x265", "h265": "x265",
    "av1": "av1", "svt-av1": "svt-av1", "svtav1": "svt-av1", "libsvtav1": "svt-av1",
    "aom-av1": "aom-av1", "libaom-av1": "aom-av1",
    "vp9": "vp9", "libvpx-vp9": "vp9",
    "vvc": "vvc", "libvvenc": "vvc", "h266": "vvc",
}


def _merge_params_string(base: str, overrides: str) -> str:
    """
    Merge a colon-separated key=value params string (x264-params/x265-params
    style) onto another, key-by-key: overrides replace matching keys in base
    and unmatched override keys are appended. Order of base is preserved.
    """
    def _parts(s: str):
        return [p for p in (s or "").split(":") if p]
    merged: dict[str, str] = {}
    order: list[str] = []
    for p in _parts(base):
        k = p.split("=", 1)[0]
        if k not in merged:
            order.append(k)
        merged[k] = p
    for p in _parts(overrides):
        k = p.split("=", 1)[0]
        if k not in merged:
            order.append(k)
        merged[k] = p
    return ":".join(merged[k] for k in order)


def _canonical_encoder(encoder: str | None) -> str:
    """
    Canonical tag for a requested encoder. The old _software_quality_encoder
    silently coerced EVERYTHING that wasn't x265 into x264 — including explicit
    svt-av1/vp9/vvc/NVENC requests, so picking SVT-AV1 in the GUI produced an
    x264 file. The user's choice is now honored; only unknown names fall back
    to x264.
    """
    e = (encoder or "").strip().lower()
    if e in _ENCODER_CANON:
        return _ENCODER_CANON[e]
    if e.endswith(("_nvenc", "_qsv", "_amf")):
        return e  # explicit hardware encoder: honored as-is
    if "265" in e or "hevc" in e:
        return "x265"
    if "av1" in e:
        return "av1"
    return "x264"


def _software_quality_encoder(encoder: str | None) -> str:
    # Back-compat alias; see _canonical_encoder.
    return _canonical_encoder(encoder)


def vmaf_quality_label(score: float) -> str:
    """Human-readable bucket for a VMAF score (0-100)."""
    s = float(score)
    if s >= 95.0:  return "visually lossless"
    if s >= 90.0:  return "excellent"
    if s >= 80.0:  return "good"
    if s >= 70.0:  return "fair"
    if s >= 60.0:  return "mediocre"
    return "poor"



# =====================================================================
# VMAF model resolution (prefer VMAF v1 when the build/model is available)
# =====================================================================
# Netflix released VMAF v1 (Jun 2026): NEG-by-default, banding + chroma aware,
# unified viewing-distance model. libvmaf's built-in identifier for it varies by
# build, and older builds don't embed it at all, so we RESOLVE the best available
# model once (validated against the actual ffmpeg) instead of hardcoding one.
#
# Precedence (env BC_VMAF_MODEL or module pref, default "auto"):
#   - a raw "model=" value (contains '=')      -> used verbatim
#   - "default"/"v0.6.1"                        -> build default (vmaf_v0.6.1)
#   - "neg"                                     -> vmaf_v0.6.1neg (anti-gaming, today)
#   - "4k"                                      -> vmaf_4k_v0.6.1
#   - "v1"                                      -> require v1 (file or embedded); warn if absent
#   - "auto" (default)                          -> v1 if available, else v0.6.1 (no scale change)
#
# To enable v1 before your ffmpeg embeds it, drop the model .json into
# tools/vmaf_models/ (offline; no download) or set BC_VMAF_MODEL.
_VMAF_V1_CANDIDATES = ["vmaf_v1", "vmaf_v1neg", "vmaf_v1_neg",
                       "vmaf_v1_hd", "vmaf_v1_1080p", "vmaf_v1_4k", "vmaf_4k_v1"]
_VMAF_MODEL_PREF = None            # GUI/CLI may set this; env still wins
_VMAF_MODEL_CACHE = {"resolved": None, "logged": False}


def set_vmaf_model_pref(pref: str | None):
    """Set the preferred VMAF model ('auto'|'v1'|'neg'|'4k'|'default'|raw). Resets
    the resolution cache so the next measurement re-resolves."""
    global _VMAF_MODEL_PREF
    _VMAF_MODEL_PREF = (str(pref).strip() if pref else None)
    _VMAF_MODEL_CACHE["resolved"] = None
    _VMAF_MODEL_CACHE["logged"] = False


def _vmaf_models_dir() -> str:
    try:
        return os.path.join(_SCRIPT_DIR, "tools", "vmaf_models")
    except Exception:
        return os.path.join("tools", "vmaf_models")


def _first_v1_model_file() -> str | None:
    """A locally-dropped VMAF v1 model .json (offline enable path), or None."""
    try:
        d = _vmaf_models_dir()
        if os.path.isdir(d):
            cands = [f for f in os.listdir(d)
                     if f.lower().endswith(".json") and "v1" in f.lower()]
            if cands:
                return os.path.join(d, sorted(cands)[0])
    except Exception:
        pass
    return None


def _escape_vmaf_opt_path(p: str) -> str:
    # ffmpeg's filter-option parser treats ':' and '\\' specially; normalise and
    # escape the drive-letter colon so a Windows path survives model=path=...
    return os.path.abspath(p).replace("\\", "/").replace(":", "\\:")


def _vmaf_model_loads(model_arg: str, ff: str) -> bool:
    """Quick 1-frame probe: does libvmaf actually accept this model= value on the
    current build? Empty arg == build default, always OK. File models are probed
    with cwd set to the model's own directory and a bare-filename reference —
    Windows drive-colon paths break the filter-option parser no matter how they
    are escaped (same reason log_path runs from a temp cwd)."""
    if not model_arg:
        return True
    if not ff:
        return False
    run_cwd = None
    if model_arg.startswith("path="):
        p = model_arg[len("path="):]
        if not os.path.isfile(p):
            return False
        run_cwd = os.path.dirname(os.path.abspath(p))
        model_arg = f"path={os.path.basename(p)}"
    _null_dev = "NUL" if os.name == "nt" else "/dev/null"
    # 320x180 x 3 frames, explicit yuv420p: VMAF v1's SpEED/CAMBI features have
    # minimum-size requirements a 64x64 single-frame probe fails ("image too
    # small"), which would wrongly mark a perfectly good model as unloadable.
    _src = "testsrc2=size=320x180:rate=5:duration=1"
    filt = (f"[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];"
            f"[a][b]libvmaf=model={model_arg}:n_threads=1")
    cmd = [ff, "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", _src, "-f", "lavfi", "-i", _src,
           "-lavfi", filt, "-frames:v", "3", "-f", "null", _null_dev]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           cwd=run_cwd, startupinfo=si, creationflags=NO_WIN)
        return int(getattr(r, "returncode", 1) or 0) == 0
    except Exception:
        return False


def resolve_vmaf_model(ff: str | None = None) -> str:
    """
    Resolve the libvmaf `model=...` argument to use (or '' for the build default
    vmaf_v0.6.1). Validated against the real ffmpeg and cached per process, so we
    never inject a model that would make VMAF calls fail.
    """
    if _VMAF_MODEL_CACHE["resolved"] is not None:
        return _VMAF_MODEL_CACHE["resolved"]
    ff = ff or FFMPEG
    pref = str(os.environ.get("BC_VMAF_MODEL") or _VMAF_MODEL_PREF or "auto").strip()
    low = pref.lower()
    resolved, note = "", None

    def _ok(arg: str) -> bool:
        return _vmaf_model_loads(arg, ff)

    def _try_v1() -> str | None:
        f = _first_v1_model_file()
        if f:
            # Raw absolute path; per-call plumbing rewrites it to a bare-filename
            # reference from a safe cwd (Windows drive colons break the parser).
            arg = f"path={os.path.abspath(f)}"
            if _ok(arg):
                return arg
        for name in _VMAF_V1_CANDIDATES:
            if _ok(f"version={name}"):
                return f"version={name}"
        return None

    if "=" in pref:
        if _ok(pref):
            resolved, note = pref, f"custom ({pref})"
        else:
            note = f"custom model '{pref}' not accepted by this build; using v0.6.1"
    elif low in ("default", "v0.6.1", "vmaf_v0.6.1", ""):
        resolved = ""
    elif low == "neg":
        if _ok("version=vmaf_v0.6.1neg"):
            resolved, note = "version=vmaf_v0.6.1neg", "v0.6.1 NEG (no-enhancement-gain)"
        else:
            note = "NEG model unavailable; using v0.6.1"
    elif low == "4k":
        if _ok("version=vmaf_4k_v0.6.1"):
            resolved, note = "version=vmaf_4k_v0.6.1", "4K v0.6.1"
    elif low in ("v1", "vmaf_v1"):
        v1 = _try_v1()
        if v1:
            resolved, note = v1, f"VMAF v1 ({v1})"
        else:
            note = ("VMAF v1 requested but not available in this ffmpeg/libvmaf build — "
                    "update ffmpeg or drop the v1 model .json into tools/vmaf_models/. "
                    "Using v0.6.1 for now.")
    else:  # "auto"
        v1 = _try_v1()
        if v1:
            resolved, note = v1, f"VMAF v1 ({v1})"
        else:
            resolved = ""  # safe: keep the calibrated v0.6.1 scale until v1 exists

    _VMAF_MODEL_CACHE["resolved"] = resolved
    if note and not _VMAF_MODEL_CACHE["logged"]:
        _VMAF_MODEL_CACHE["logged"] = True
        try:
            logging.getLogger("BitCrusher").info("VMAF model: %s", note)
            _cb = globals().get("status_callback")
            if callable(_cb):
                # A non-default model shifts the score scale — flag it once so the
                # user knows reference numbers / min-VMAF thresholds may need a nudge.
                lvl = "WARNING" if ("using v0.6.1" in note and "v1" in note.lower()) else "INFO"
                _cb(f"[VMAF] {note}", level=lvl)
        except Exception:
            pass
    return resolved


def _vmaf_model_opt(ff: str | None = None, work_dir: str | None = None) -> str:
    """
    The 'model=...:' fragment to splice into a libvmaf filter, or '' for default.
    File-based models: the model json is COPIED into `work_dir` (the directory the
    ffmpeg VMAF call runs from, same as log_path) and referenced by bare filename —
    a Windows absolute path in the filter option breaks its parser at the drive
    colon regardless of escaping.
    """
    m = resolve_vmaf_model(ff)
    if not m:
        return ""
    if m.startswith("path="):
        src = m[len("path="):]
        base = os.path.basename(src)
        if work_dir:
            try:
                dst = os.path.join(work_dir, base)
                if not os.path.exists(dst):
                    shutil.copyfile(src, dst)
                return f"model=path={base}:"
            except Exception:
                return ""      # can't stage the model — measure with the default
        return ""              # no safe cwd available — default model
    return f"model={m}:"


def compute_vmaf(reference_path: str, distorted_path: str, *,
                 sample_fps: float = 12.0,
                 duration_s: float = 0.0,
                 ffmpeg: str | None = None) -> dict | None:
    """
    Measure the perceptual quality of `distorted_path` (the compressed output)
    against `reference_path` (the original) using libvmaf.

    Returns {"vmaf": float, "label": str, "harmonic": float|None} or None when
    VMAF can't be computed (filter missing, mismatch, error). The distorted
    stream is scaled to the reference resolution and both streams are resampled
    to `sample_fps` so frame counts align even when the encode changed
    resolution or frame rate — this also makes measurement several times faster
    than full-rate scoring with negligible accuracy loss.
    """
    ff = ffmpeg or FFMPEG
    if not ff or not _ffmpeg_has_filter("libvmaf"):
        return None
    try:
        if not (os.path.exists(reference_path) and os.path.exists(distorted_path)):
            return None
    except Exception:
        return None

    # Reference resolution + frame rate (VMAF needs both streams at the same size).
    ref_w = ref_h = 0
    ref_fps = 30.0
    try:
        _vst_ref = _probe_video_stream(reference_path)
        ref_w = int(_vst_ref.get("width") or 0)
        ref_h = int(_vst_ref.get("height") or 0)
        try:
            ref_fps = float(Fraction(_vst_ref.get("avg_frame_rate") or "30/1"))
        except Exception:
            ref_fps = 30.0
    except Exception:
        return None
    if ref_w <= 0 or ref_h <= 0:
        return None

    n_threads = max(1, min(16, (os.cpu_count() or 4)))
    fps = max(1.0, float(sample_fps))
    # Align BOTH streams to a common timeline before sampling:
    #   setpts=PTS-STARTPTS  — rebase to t=0. Critical: many downloaded/re-muxed
    #     files carry a start-PTS offset (e.g. one-frame 0.04s delay from an edit
    #     list). The encoder strips it, so distorted starts at 0 while the source
    #     starts at 0.04 — libvmaf's framesync then pairs every distorted frame
    #     against the reference frame one position off, scoring rapid-cut scenes
    #     ~0 and dragging a genuinely-fine encode down by 20-30 VMAF (a real file
    #     measured 57 instead of ~82). Frame-INDEX decimation (select mod n) did
    #     NOT fix this: the offset frame is decoded asymmetrically between the two
    #     inputs, so the indices themselves drift.
    #   fps=sample_fps      — resample both to the SAME fixed rate off that common
    #     origin, so identical wall-clock frames are compared even on VFR sources
    #     (the case index-decimation was originally meant to protect — the missing
    #     piece was the PTS rebase, not the sampling method). Also cheaper than
    #     full-rate scoring, with negligible accuracy loss.
    _sync = f"setpts=PTS-STARTPTS,fps={fps:g}"
    # Write the log to a bare filename inside a temp working dir: a full Windows
    # path like C:/... contains a colon that libvmaf's filter-option parser
    # treats as an option separator, so we run with cwd set to the temp dir.
    work_dir = tempfile.mkdtemp(prefix="bc_vmaf_")
    log_name = "vmaf.json"
    log_path = os.path.join(work_dir, log_name)
    ref_abs = os.path.abspath(reference_path)
    dist_abs = os.path.abspath(distorted_path)
    # 0 = distorted (first input), 1 = reference (second input).
    lavfi = (
        f"[0:v]scale={ref_w}:{ref_h}:flags=bicubic,{_sync},format=yuv420p[dist];"
        f"[1:v]{_sync},format=yuv420p[ref];"
        f"[dist][ref]libvmaf={_vmaf_model_opt(ff, work_dir)}n_threads={n_threads}:log_fmt=json:log_path={log_name}"
    )
    cmd = [ff, "-hide_banner", "-loglevel", "error"]
    # Cap measurement window on very long videos for speed (sampled, not full).
    # MUST cap BOTH inputs: capping only one made framesync repeat the shorter
    # stream's last frame against minutes of moving reference — VMAF collapsed
    # to ~20 on perfectly good encodes of long videos.
    _t_cap = (["-t", "120"] if (duration_s and duration_s > 180.0) else [])
    _null_dev = "NUL" if os.name == "nt" else "/dev/null"
    cmd += _t_cap + ["-i", dist_abs] + _t_cap + ["-i", ref_abs] \
        + ["-lavfi", lavfi, "-an", "-sn", "-f", "null", _null_dev]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             cwd=work_dir, startupinfo=si, creationflags=NO_WIN)
        if getattr(res, "returncode", 1) != 0:
            return None
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass

    # Parse mean VMAF across libvmaf JSON schema variants.
    mean = harmonic = None
    try:
        pooled = (data.get("pooled_metrics") or {}).get("vmaf") or {}
        if "mean" in pooled:
            mean = float(pooled["mean"])
        if "harmonic_mean" in pooled:
            harmonic = float(pooled["harmonic_mean"])
    except Exception:
        pass
    # Per-frame values (always, when present) so we can compute the FLOOR metrics
    # that beat the "average trap": a clip can average 97 while one hard scene
    # sits at 70 — the worst rolling window is what a viewer actually notices.
    vals = []
    try:
        for fr in (data.get("frames") or []):
            v = (fr.get("metrics") or {}).get("vmaf")
            if v is not None:
                vals.append(float(v))
    except Exception:
        vals = []
    if mean is None and vals:
        mean = sum(vals) / len(vals)
    if mean is None:
        try:
            mean = float(data.get("VMAF score"))
        except Exception:
            return None

    # Reliability guard: near-zero per-frame VMAF is almost never real (even a
    # heavily-crushed encode scores 20-40, not ~0). A non-trivial fraction of
    # ~0 frames means the two streams were misaligned (start-PTS offset, VFR
    # drift, wrong-length inputs) — a MEASUREMENT fault, not quality. Flag it so
    # callers can distrust the number and, crucially, keep it out of the learning
    # ledger instead of poisoning quality predictions with a phantom collapse.
    _zero_frac = (sum(1 for v in vals if v < 2.0) / len(vals)) if vals else 0.0
    _reliable = _zero_frac < 0.02

    # Worst rolling ~2s window + low percentiles (scene-floor proxies, no re-encode).
    p1 = p5 = min_window = None
    _mw_idx = None
    if vals:
        win = max(4, int(round(2.0 * max(1.0, float(sample_fps)))))
        p1, p5, min_window, _mw_idx = _vmaf_low_metrics(vals, win)
    _spread = (round(float(mean) - float(min_window), 2)
               if (min_window is not None and mean is not None) else None)
    # Locate the worst window in source time. compute_vmaf decimates to sample_fps,
    # so output-frame index i maps back to source time ≈ i / sample_fps (the select
    # keeps every step-th frame and setpts re-times to sample_fps). For sources
    # capped at 120s the offset is still within the measured window.
    _mw_at = (round(float(_mw_idx) / max(1.0, float(sample_fps)), 1)
              if _mw_idx is not None else None)

    # Downsampled VMAF-over-time series (<= 64 points) for the result-dashboard
    # sparkline. Averaged into buckets so a single dip isn't lost; kept small so
    # it's cheap to store in the ledger and render.
    _series = _downsample_series(vals, 64) if vals else None
    # Time the series spans (source seconds of the measured region), so a
    # renderer can map min_window_at back onto the downsampled points regardless
    # of how many buckets survived.
    _series_span = (round(len(vals) / max(1.0, float(sample_fps)), 3)
                    if _series else None)

    return {"vmaf": round(float(mean), 2),
            "harmonic": (round(float(harmonic), 2) if harmonic is not None else None),
            "p1": (round(float(p1), 2) if p1 is not None else None),
            "p5": (round(float(p5), 2) if p5 is not None else None),
            "min_window": (round(float(min_window), 2) if min_window is not None else None),
            "min_window_at": _mw_at,
            "spread": _spread,
            "series": _series,
            "series_span_s": _series_span,
            "reliable": bool(_reliable),
            "zero_frac": round(_zero_frac, 4),
            "label": vmaf_quality_label(mean)}


def _downsample_series(vals: list, max_points: int) -> list:
    """Bucket-average a per-frame series down to <= max_points values (rounded),
    preserving overall shape for a sparkline without storing every frame."""
    try:
        n = len(vals)
        if n == 0:
            return []
        if n <= max_points:
            return [round(float(v), 1) for v in vals]
        step = n / float(max_points)
        out = []
        for i in range(max_points):
            a = int(i * step)
            b = max(a + 1, int((i + 1) * step))
            chunk = vals[a:b]
            if chunk:
                out.append(round(sum(float(x) for x in chunk) / len(chunk), 1))
        return out
    except Exception:
        return []


def compute_xpsnr(reference_path: str, distorted_path: str, *,
                  sample_fps: float = 12.0, duration_s: float = 0.0,
                  ffmpeg: str | None = None) -> dict | None:
    """
    Second-opinion perceptual metric: XPSNR (extended perceptually-weighted PSNR,
    ITU-T standardized, built into ffmpeg — no external binary). Orthogonal to
    VMAF: it weights error by local perceptual masking rather than a learned
    model, so it disagrees with VMAF exactly where VMAF is weakest/gameable
    (anime, screen/text, over-sharpening). Same stream alignment as compute_vmaf
    (setpts=PTS-STARTPTS + fps rebase — see that function's fix). Returns the
    luma channel as {"xpsnr", "min_window", "min_window_at", "spread", "series",
    "series_span_s", "reliable"} (dB; higher is better), or None.
    """
    ff = ffmpeg or FFMPEG
    if not ff or not _ffmpeg_has_filter("xpsnr"):
        return None
    try:
        if not (os.path.exists(reference_path) and os.path.exists(distorted_path)):
            return None
    except Exception:
        return None
    try:
        _vst = _probe_video_stream(reference_path)
        ref_w = int(_vst.get("width") or 0)
        ref_h = int(_vst.get("height") or 0)
    except Exception:
        return None
    if ref_w <= 0 or ref_h <= 0:
        return None

    fps = max(1.0, float(sample_fps))
    _sync = f"setpts=PTS-STARTPTS,fps={fps:g}"
    work = tempfile.mkdtemp(prefix="bc_xpsnr_")
    stats_name = "xpsnr.log"
    stats_path = os.path.join(work, stats_name)
    lavfi = (
        f"[0:v]scale={ref_w}:{ref_h}:flags=bicubic,{_sync},format=yuv420p[d];"
        f"[1:v]{_sync},format=yuv420p[r];"
        f"[d][r]xpsnr=stats_file={stats_name}"
    )
    _t_cap = (["-t", "120"] if (duration_s and duration_s > 180.0) else [])
    _null_dev = "NUL" if os.name == "nt" else "/dev/null"
    cmd = [ff, "-hide_banner", "-loglevel", "error"] \
        + _t_cap + ["-i", os.path.abspath(distorted_path)] \
        + _t_cap + ["-i", os.path.abspath(reference_path)] \
        + ["-lavfi", lavfi, "-an", "-sn", "-f", "null", _null_dev]
    vals: list[float] = []
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             cwd=work, startupinfo=si, creationflags=NO_WIN)
        if getattr(res, "returncode", 1) != 0:
            return None
        with open(stats_path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.search(r"XPSNR\s+y:\s*([0-9.]+|inf)", ln)
                if not m:
                    continue
                tok = m.group(1)
                # Identical frames report 'inf'; cap so they don't skew the mean.
                vals.append(60.0 if tok == "inf" else float(tok))
    except Exception:
        return None
    finally:
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass

    if not vals:
        return None
    mean = sum(vals) / len(vals)
    win = max(4, int(round(2.0 * max(1.0, float(sample_fps)))))
    _p1, _p5, min_window, _mw_idx = _vmaf_low_metrics(vals, win)
    _spread = (round(float(mean) - float(min_window), 2)
               if min_window is not None else None)
    _mw_at = (round(float(_mw_idx) / max(1.0, float(sample_fps)), 1)
              if _mw_idx is not None else None)
    _series = _downsample_series(vals, 64)
    _series_span = round(len(vals) / max(1.0, float(sample_fps)), 3) if _series else None
    # A block of ~0 dB frames is misalignment, same signature as the VMAF guard.
    _zero_frac = sum(1 for v in vals if v < 5.0) / len(vals)
    return {"xpsnr": round(float(mean), 2),
            "min_window": (round(float(min_window), 2) if min_window is not None else None),
            "min_window_at": _mw_at,
            "spread": _spread,
            "series": _series,
            "series_span_s": _series_span,
            "reliable": bool(_zero_frac < 0.02),
            "label": xpsnr_quality_label(mean)}


def xpsnr_quality_label(db: float) -> str:
    """Rough perceptual bucket for an XPSNR-Y value (dB). Calibrated loosely —
    XPSNR is used as a relative cross-check, not an absolute gate."""
    try:
        d = float(db)
    except Exception:
        return "unknown"
    if d >= 42.0:
        return "visually lossless"
    if d >= 37.0:
        return "excellent"
    if d >= 32.0:
        return "good"
    if d >= 27.0:
        return "fair"
    return "poor"


def _vmaf_low_metrics(vals: list, win: int):
    """From per-frame VMAF values return (p1, p5, worst_rolling_window_mean,
    worst_window_start_index). The window mean resists single-frame outliers
    that would tank a raw min; the start index locates the valley in the stream."""
    n = len(vals)
    if n == 0:
        return None, None, None, None

    def _pct(sorted_vals, q):
        if not sorted_vals:
            return None
        idx = int(max(0, min(len(sorted_vals) - 1, round(q / 100.0 * (len(sorted_vals) - 1)))))
        return sorted_vals[idx]

    sv = sorted(vals)
    p1 = _pct(sv, 1.0)
    p5 = _pct(sv, 5.0)
    w = max(1, min(int(win), n))
    if w >= n:
        return p1, p5, sum(vals) / n, 0
    # Rolling window mean via a running sum; keep the smallest and where it started.
    run = sum(vals[:w])
    min_window = run / w
    min_idx = 0
    for i in range(w, n):
        run += vals[i] - vals[i - w]
        m = run / w
        if m < min_window:
            min_window = m
            min_idx = i - w + 1
    return p1, p5, min_window, min_idx


def vmaf_floor_score(vmaf: dict | None, objective: str = "window") -> float | None:
    """
    Pick the scalar a size-target / quality-floor decision should optimize, per
    the configured objective, with a graceful fallback chain so it always yields
    a number when any VMAF exists:
      window  -> worst ~2s rolling window (default; attacks the average trap)
      p5/p1   -> low percentile of frame scores
      harmonic-> harmonic mean (dips toward low scores)
      mean    -> arithmetic mean (the classic "average trap" number)
    """
    if not vmaf:
        return None
    obj = str(objective or "window").strip().lower()
    order = {
        "window": ("min_window", "p5", "harmonic", "vmaf"),
        "p5":     ("p5", "p1", "min_window", "harmonic", "vmaf"),
        "p1":     ("p1", "p5", "min_window", "harmonic", "vmaf"),
        "harmonic": ("harmonic", "min_window", "vmaf"),
        "mean":   ("vmaf",),
    }.get(obj, ("min_window", "p5", "harmonic", "vmaf"))
    for k in order:
        v = vmaf.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


_VMAF_OBJECTIVE_PREF = None


def set_vmaf_objective_pref(pref: str | None):
    global _VMAF_OBJECTIVE_PREF
    _VMAF_OBJECTIVE_PREF = (str(pref).strip() if pref else None)


def resolve_vmaf_objective(advanced_options: dict | None = None) -> str:
    """Objective name from job opts > env BC_VMAF_OBJECTIVE > module pref > 'window'."""
    for src in ((advanced_options or {}).get("vmaf_objective"),
                os.environ.get("BC_VMAF_OBJECTIVE"),
                _VMAF_OBJECTIVE_PREF):
        s = str(src or "").strip().lower()
        if s in ("window", "p5", "p1", "harmonic", "mean"):
            return s
    return "window"


def _ffmpeg_encoder_set() -> set:
    """All video encoder names the installed ffmpeg exposes (cached)."""
    cache = getattr(_ffmpeg_encoder_set, "_cache", None)
    if cache is None:
        cache = set()
        try:
            out = _sp_check_output([FFMPEG, "-hide_banner", "-encoders"],
                                   text=True, startupinfo=si, creationflags=NO_WIN)
            for line in (out or "").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].startswith("V"):
                    cache.add(parts[1].strip())
        except Exception:
            pass
        setattr(_ffmpeg_encoder_set, "_cache", cache)
    return cache


_HW_DECODE_BROKEN = False  # set on first hw-decode failure; all later jobs use software decode


def _mark_hw_decode_broken():
    global _HW_DECODE_BROKEN
    _HW_DECODE_BROKEN = True


def _available_hwaccels() -> set:
    """Hardware acceleration methods the installed ffmpeg exposes (cached)."""
    cache = getattr(_available_hwaccels, "_cache", None)
    if cache is None:
        cache = set()
        try:
            out = _sp_check_output([FFMPEG, "-hide_banner", "-hwaccels"], text=True,
                                   startupinfo=si, creationflags=NO_WIN)
            for line in (out or "").splitlines():
                s = line.strip().lower()
                if s and "acceleration" not in s:
                    cache.add(s)
        except Exception:
            pass
        setattr(_available_hwaccels, "_cache", cache)
    return cache


def _hw_decode_args(advanced_options: dict | None) -> list:
    """
    ['-hwaccel', X] input-side args for GPU decode, or [] when disabled or
    unavailable. Deliberately no -hwaccel_output_format: decoded frames come
    back to system memory so every software filter (scale, tonemap, ...) keeps
    working — only the H.264/HEVC/VP9/AV1 *decode* moves off the CPU.
    """
    if _HW_DECODE_BROKEN:
        return []
    if not bool((advanced_options or {}).get("hw_decode", True)):
        return []
    have = _available_hwaccels()
    if os.name == "nt" and "d3d11va" in have:
        return ["-hwaccel", "d3d11va"]
    if have:
        return ["-hwaccel", "auto"]
    return []


def _strip_hw_args(cmd: list) -> list:
    c = list(cmd)
    try:
        i = c.index("-hwaccel")
        del c[i:i + 2]
    except ValueError:
        pass
    return c


def best_av1_encoder() -> str | None:
    """
    The best AV1 encoder actually present in this ffmpeg build, preferring
    SVT-AV1 (fast + excellent) > libaom (slow + best) > hardware. BitCrusher used
    to hardcode libsvtav1, which is missing from many builds (e.g. gyan
    'essentials'), so AV1 would silently fail/fall back to HEVC.
    """
    have = _ffmpeg_encoder_set()
    for enc in ("libsvtav1", "libaom-av1", "av1_nvenc", "av1_qsv", "av1_amf"):
        if enc in have:
            return enc
    return None


# Grain probe: denoise must save at least this fraction of bytes for the source
# to count as "grainy enough" to synthesize. Measured discrimination on probes:
# clean content lands ~0.97, real grain ~0.60-0.80.
_FILM_GRAIN_RATIO_THR = 0.90


def _probe_film_grain(input_path: str, *, scale_width: int, duration_s: float,
                      cancel_cb=None, status_cb=None) -> dict | None:
    """
    Decide whether to synthesize film grain, by MEASUREMENT rather than the
    graininess feature (which doesn't track real grain — the synthetic-grain
    torture clip scored lower than clean footage). Encode short probe segments
    with SVT-AV1 twice at the same CRF — with vs without film-grain-denoise —
    and compare size: grainy content shrinks a lot when the grain is stripped,
    clean content barely moves. Returns {"level": int, "size_ratio": float}
    when grain is worth re-synthesizing (denoise saved >10%), else None. Grain
    is AV1-only (SVT), so this no-ops on other encoders.
    """
    ff = FFMPEG
    if not ff or best_av1_encoder() != "libsvtav1":
        return None
    dur = float(duration_s or 0.0)
    if dur < 3.0:
        return None
    seg = max(3.0, min(6.0, 0.06 * dur))
    starts = ([max(0.0, dur * 0.35 - seg / 2.0), max(0.0, dur * 0.70 - seg / 2.0)]
              if dur >= 4.0 * seg else [max(0.0, dur * 0.5 - seg / 2.0)])
    _scale = (["-vf", f"scale={int(scale_width)}:-2"] if int(scale_width or 0) > 0 else [])
    work = tempfile.mkdtemp(prefix="bc_grain_")
    tot_plain = tot_den = 0
    try:
        for i, t0 in enumerate(starts):
            if callable(cancel_cb) and cancel_cb():
                return None
            for tag, params in (("p", "tune=0:film-grain=8"),
                                ("d", "tune=0:film-grain=8:film-grain-denoise=1")):
                out = os.path.join(work, f"g{i}{tag}.mp4")
                cmd = ([ff, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{t0:.3f}", "-t", f"{seg:.3f}",
                        "-i", os.path.abspath(input_path), "-an"] + _scale
                       + ["-c:v", "libsvtav1", "-preset", "8", "-crf", "32",
                          "-svtav1-params", params, "-pix_fmt", "yuv420p10le", out])
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   startupinfo=si, creationflags=NO_WIN)
                if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
                    return None
                sz = os.path.getsize(out)
                if tag == "p":
                    tot_plain += sz
                else:
                    tot_den += sz
        if tot_plain <= 0:
            return None
        ratio = tot_den / tot_plain
        if ratio >= _FILM_GRAIN_RATIO_THR:
            return None
        # More savings = more grain = stronger re-synthesis. ratio 0.88 -> ~14,
        # 0.77 -> ~28, <=0.67 -> capped at 40.
        level = int(min(40, max(8, round((1.0 - ratio) * 120))))
        return {"level": level, "size_ratio": round(ratio, 3)}
    except Exception:
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _probe_codec_args(codec_tag: str, av1_enc: str | None, video_bps: int):
    """
    Probe-encode args (codec, ffmpeg_args, suffix) for codec ranking.

    Fairness matters more than speed here: x264/x265 single-pass ABR on a short
    clip can blow 30-40% past the budget (measured: x264 used 38% more bits than
    SVT-AV1 at the same -b:v and "won" the race with them), so the x26x probes
    get a maxrate/bufsize cap. AV1 probes now go into .mp4 — the .mkv container
    produced non-monotonic timestamps that broke VMAF frame alignment.
    """
    t = (codec_tag or "").lower()
    bps = int(video_bps)
    cap = ["-maxrate", str(int(bps * 1.10)), "-bufsize", str(int(bps * 2))]
    if t in ("av1", "svt-av1", "aom-av1") and av1_enc:
        if av1_enc == "libsvtav1":
            return av1_enc, ["-c:v", "libsvtav1", "-preset", "6", "-svtav1-params", "tune=0"], ".mp4"
        if av1_enc == "libaom-av1":
            return av1_enc, ["-c:v", "libaom-av1", "-cpu-used", "6", "-row-mt", "1", "-tile-columns", "1"], ".mp4"
        return av1_enc, ["-c:v", av1_enc, "-preset", "p5"], ".mp4"
    if t in ("x265", "hevc", "libx265"):
        return "libx265", ["-c:v", "libx265", "-preset", "medium", "-x265-params", "log-level=none"] + cap, ".mp4"
    return "libx264", ["-c:v", "libx264", "-preset", "medium"] + cap, ".mp4"


def _probe_vmaf_fullrate(ff: str, ref_path: str, dist_path: str) -> float | None:
    """
    Full-rate VMAF for short probe clips. The sampled/decimated path in
    compute_vmaf mis-aligned frames on AV1 probe clips (SVT-AV1 measured 33
    decimated vs 70.8 full-rate on the same file), which made the codec race
    systematically pick the worse codec. Probe segments are only a few seconds
    long, so scoring every frame is affordable and exact.
    """
    try:
        _vst = _probe_video_stream(ref_path)
        rw, rh = int(_vst.get("width") or 0), int(_vst.get("height") or 0)
    except Exception:
        return None
    if rw <= 0 or rh <= 0:
        return None
    n_threads = max(1, min(16, (os.cpu_count() or 4)))
    work_dir = tempfile.mkdtemp(prefix="bc_vmafp_")
    log_name = "vmaf.json"
    lavfi = (
        f"[0:v]scale={rw}:{rh}:flags=bicubic,setpts=PTS-STARTPTS,format=yuv420p[dist];"
        f"[1:v]setpts=PTS-STARTPTS,format=yuv420p[ref];"
        f"[dist][ref]libvmaf={_vmaf_model_opt(ff, work_dir)}n_threads={n_threads}:log_fmt=json:log_path={log_name}"
    )
    _null_dev = "NUL" if os.name == "nt" else "/dev/null"
    cmd = [ff, "-hide_banner", "-loglevel", "error",
           "-i", os.path.abspath(dist_path), "-i", os.path.abspath(ref_path),
           "-lavfi", lavfi, "-an", "-sn", "-f", "null", _null_dev]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             cwd=work_dir, startupinfo=si, creationflags=NO_WIN)
        if getattr(res, "returncode", 1) != 0:
            return None
        with open(os.path.join(work_dir, log_name), "r", encoding="utf-8") as f:
            data = json.load(f)
        pooled = (data.get("pooled_metrics") or {}).get("vmaf") or {}
        if "mean" in pooled:
            return float(pooled["mean"])
        frames = data.get("frames") or []
        vals = [float((fr.get("metrics") or {}).get("vmaf")) for fr in frames
                if (fr.get("metrics") or {}).get("vmaf") is not None]
        return (sum(vals) / len(vals)) if vals else None
    except Exception:
        return None
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


def _probe_ref_clip(ff, input_path, *, t0, seg, scale_width, own_dir,
                    ref_cache=None, ref_cache_dir=None):
    """
    Lossless reference clip for a probe segment at delivery resolution. The codec
    race and the preproc A/B both need the SAME reference — it depends only on
    source + segment + scale (codec-independent) — so a shared (ref_cache,
    ref_cache_dir) lets the two stages extract each segment once per job instead
    of twice. Returns a clip path, or None on failure.
    """
    key = (os.path.abspath(input_path), int(scale_width or 0),
           round(float(t0), 3), round(float(seg), 3))
    if isinstance(ref_cache, dict):
        hit = ref_cache.get(key)
        if hit and os.path.exists(hit) and os.path.getsize(hit) > 0:
            return hit
    dest_dir = ref_cache_dir if (ref_cache_dir and os.path.isdir(ref_cache_dir)) else own_dir
    name = (f"ref_{int(scale_width or 0)}_{int(round(float(t0) * 1000))}"
            f"_{int(round(float(seg) * 1000))}.mp4")
    ref_clip = os.path.join(dest_dir, name)
    if not (os.path.exists(ref_clip) and os.path.getsize(ref_clip) > 0):
        _scale_vf = (["-vf", f"scale={int(scale_width)}:-2"] if int(scale_width or 0) > 0 else [])
        rc = subprocess.run(
            [ff, "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{float(t0):.3f}", "-t", f"{float(seg):.3f}",
             "-i", os.path.abspath(input_path), "-an"] + _scale_vf +
            ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", ref_clip],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=si, creationflags=NO_WIN)
        if rc.returncode != 0 or not os.path.exists(ref_clip):
            return None
    if isinstance(ref_cache, dict):
        ref_cache[key] = ref_clip
    return ref_clip


def choose_best_codec_by_vmaf(input_path: str, *, duration_s: float, video_bps: int,
                              candidates: list[str], scale_width: int = 0,
                              incumbent: str | None = None, switch_margin: float = 0.75,
                              status_cb=None, result_sink: dict | None = None,
                              ref_cache: dict | None = None,
                              ref_cache_dir: str | None = None) -> str | None:
    """
    Probe short representative clips with each candidate codec at the SAME video
    bitrate and return the codec tag with the highest VMAF (best quality for the
    bit budget). Returns None if probing isn't possible (caller keeps its choice).

    The race is run at the DELIVERY resolution (scale_width), not the source's:
    a 4K source that will ship at 2560w must be judged at 2560w, otherwise the
    probe measures a completely different operating point. Two segments are
    scored (early + late) so a single unlucky scene can't decide the winner, and
    an incumbent is only dethroned when the challenger wins by switch_margin —
    re-running the same job should not flip-flop between codecs on noise.
    """
    ff = FFMPEG
    if not ff or not _ffmpeg_has_filter("libvmaf"):
        return None
    dur = float(duration_s or 0.0)
    if dur <= 1.0 or int(video_bps) <= 0:
        return None

    av1_enc = best_av1_encoder()
    cand = [c for c in candidates if not (("av1" in c.lower()) and not av1_enc)]
    cand = list(dict.fromkeys(cand))  # de-dup, preserve order
    if len(cand) < 2:
        return None

    seg = max(3.0, min(6.0, 0.06 * dur))
    if dur >= 4.0 * seg:
        starts = [max(0.0, dur * 0.30 - seg / 2.0), max(0.0, dur * 0.65 - seg / 2.0)]
    else:
        starts = [max(0.0, dur * 0.5 - seg / 2.0)]

    _scale_vf = []
    if int(scale_width or 0) > 0:
        _scale_vf = ["-vf", f"scale={int(scale_width)}:-2"]

    work = tempfile.mkdtemp(prefix="bc_codecpick_")
    try:
        scores: dict[str, list[float]] = {}
        sizes: dict[str, int] = {}
        for si_n, t0 in enumerate(starts):
            # Lossless reference at the delivery resolution for this segment
            # (shared with the preproc A/B when the operating point matches).
            ref_clip = _probe_ref_clip(ff, input_path, t0=t0, seg=seg,
                                       scale_width=int(scale_width or 0), own_dir=work,
                                       ref_cache=ref_cache, ref_cache_dir=ref_cache_dir)
            if not ref_clip:
                continue
            for tag in cand:
                enc_name, cargs, suffix = _probe_codec_args(tag, av1_enc, int(video_bps))
                clip = os.path.join(work, f"cand_{tag}_{si_n}{suffix}")
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.3f}", "-t", f"{seg:.3f}",
                       "-i", os.path.abspath(input_path), "-an"] + _scale_vf + ["-pix_fmt", "yuv420p"] + \
                      cargs + ["-b:v", str(int(video_bps)), clip]
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   startupinfo=si, creationflags=NO_WIN)
                if r.returncode != 0 or not os.path.exists(clip) or os.path.getsize(clip) == 0:
                    continue
                vm = _probe_vmaf_fullrate(ff, ref_clip, clip)
                if vm is not None:
                    scores.setdefault(tag, []).append(float(vm))
                    sizes[tag] = sizes.get(tag, 0) + os.path.getsize(clip)

        # Bit-normalized scoring. Encoders don't hit -b:v exactly on short probe
        # segments: SVT-AV1's VBR runs hot (observed 2.32MB vs x265's 0.52MB at
        # the SAME target), so raw VMAF rewarded whichever codec overshot the
        # budget hardest — not the one with the best quality-per-bit. Credit a
        # codec that undershot and penalise one that overshot, both relative to
        # the target bytes, so the race compares codecs at a common bit budget.
        _VMAF_PER_DOUBLING = 4.0  # ~VMAF points gained per 2x bitrate in this range
        raw: dict[str, float] = {}
        results: dict[str, float] = {}
        for k, v in scores.items():
            if not v:
                continue
            raw_v = sum(v) / len(v)
            tgt_bytes = max(1.0, float(video_bps) * float(seg) * float(len(v)) / 8.0)
            act_bytes = max(1.0, float(sizes.get(k, 0)))
            adj = _VMAF_PER_DOUBLING * math.log2(tgt_bytes / act_bytes)
            adj = max(-8.0, min(8.0, adj))  # cap so one wild clip can't dominate
            raw[k] = raw_v
            results[k] = raw_v + adj
        if not results:
            return None
        winner = max(results.items(), key=lambda kv: kv[1])[0]
        # Every race is a labeled experiment; hand the full scoreboard to the
        # caller so the outcome ledger can learn from it (it used to be thrown
        # away after the pick).
        if isinstance(result_sink, dict):
            result_sink["scores"] = {k: round(v, 2) for k, v in results.items()}
            result_sink["raw"] = {k: round(v, 2) for k, v in raw.items()}
        inc = (incumbent or "").lower()
        if inc and inc in results and winner != inc:
            if results[winner] < results[inc] + float(switch_margin):
                winner = inc  # challenger didn't clear the margin; stay put
        if callable(status_cb):
            tbl = ", ".join(
                f"{k}={results[k]:.1f} (raw {raw[k]:.1f}, {sizes.get(k, 0) / 1e6:.2f}MB)"
                for k in sorted(results, key=lambda kk: -results[kk]))
            status_cb(f"[Codec] VMAF probe @ {int(video_bps)//1000}kbps, "
                      f"{int(scale_width) if scale_width else 'src'}w, {len(starts)} segment(s), "
                      f"bit-normalized → {tbl}. Picking {winner}.", "INFO")
        return winner
    except Exception:
        return None
    finally:
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass


def _rmtree_quiet(path: str | None) -> None:
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# =====================================================================
# Artifact/texture-aware preprocessing (VMAF-validated)
# =====================================================================
# Re-compressed sources arrive pre-loaded with banding/blocking, and dense
# texture (game footage, grainy captures) starves the encoder at Discord-sized
# targets — it spends the whole budget faithfully reproducing noise. Targeted
# prefilters (deband / deblock / denoise) let those bits go to structure
# instead. The catch: prefilters can also HURT (smearing real detail), so no
# filter ships on faith — every candidate chain is A/B probed against the
# unfiltered encode at the real operating point (same encoder, same bitrate,
# same delivery resolution, scored with full-rate VMAF against the pristine
# source) and kept only when it measurably wins. With the VMAF v1 model active
# the validator also SEES banding (CAMBI), so deband decisions are honest.

# Feature-gate thresholds (measured scales: banding_risk 0..1, graininess 0..~1,
# blockiness 0..~24, spatial_complexity 0..~10; bpp = bits per pixel per frame).
_PREPROC_BAND_THR = 0.35        # banding_risk above this → deband candidate
_PREPROC_BLOCK_THR = 10.0       # blockiness above this → deblock candidate
_PREPROC_GRAIN_THR = 0.25       # graininess above this (when starved) → denoise
_PREPROC_ENTROPY_THR = 7.0      # entropy_p95 (0..8) above this = dense texture
_PREPROC_BPP_STARVED = 0.11     # below this bpp texture starts to crumble
_PREPROC_BPP_CRUSHED = 0.055    # below this even moderate texture is hopeless
_PREPROC_KEEP_MARGIN = 0.4      # VMAF points a chain must win by to be kept

_PREPROC_FILTERS = {
    "deband":        "deband=1thr=0.015:2thr=0.015:3thr=0.015:range=16:blur=1",
    "deblock":       "deblock=filter=strong:block=8",
    "denoise_light": "hqdn3d=1.5:1.2:3.0:2.5",
    "denoise_med":   "hqdn3d=3.0:2.0:6.0:4.5",
}


def _preproc_candidates(feats: dict, *, video_bps: int, width: int, height: int,
                        fps: float, allow_denoise: bool = True) -> list[dict]:
    """
    Decide which prefilters the measured content indicates, strongest first.
    Returns [{"name", "vf", "why", "severity"}]; empty list = nothing indicated.
    Pure decision logic — validation (the probe A/B) happens separately.
    """
    feats = feats or {}
    try:
        band = float(feats.get("banding_risk", 0.0) or 0.0)
        grain = float(feats.get("graininess", 0.0) or 0.0)
        block = float(feats.get("blockiness", 0.0) or 0.0)
        cx = float(feats.get("spatial_complexity", 0.0) or 0.0)
        entropy = float(feats.get("entropy_p95", 0.0) or 0.0)
    except Exception:
        return []
    try:
        bpp = float(video_bps) / max(1.0, float(width) * float(height) * max(1.0, float(fps)))
    except Exception:
        bpp = 1.0

    out: list[dict] = []
    if block >= _PREPROC_BLOCK_THR:
        out.append({"name": "deblock", "vf": _PREPROC_FILTERS["deblock"],
                    "why": f"blockiness {block:.1f}", "severity": block / _PREPROC_BLOCK_THR})
    if allow_denoise:
        starved = bpp < _PREPROC_BPP_STARVED
        crushed = bpp < _PREPROC_BPP_CRUSHED
        # Dense-texture starvation is signalled by ENTROPY, not the grain score:
        # structured texture (game surfaces, foliage, concrete) reads as near-max
        # entropy while graininess stays low — measured on the corpus, the
        # pathological game capture sat at entropy 7.24/8 with grain 0.04.
        texture_hard = (entropy >= _PREPROC_ENTROPY_THR) or (cx >= 6.0)
        if (grain >= _PREPROC_GRAIN_THR and starved) or (crushed and texture_hard):
            key = "denoise_med" if crushed else "denoise_light"
            out.append({"name": key, "vf": _PREPROC_FILTERS[key],
                        "why": f"grain {grain:.2f} / entropy {entropy:.1f} / cx {cx:.1f} at {bpp:.3f} bpp",
                        "severity": (_PREPROC_BPP_STARVED / max(1e-6, bpp))
                                    * max(grain, entropy / 8.0, cx / 10.0)})
    if band >= _PREPROC_BAND_THR:
        out.append({"name": "deband", "vf": _PREPROC_FILTERS["deband"],
                    "why": f"banding risk {band:.2f}", "severity": band / _PREPROC_BAND_THR})

    # Drop filters this ffmpeg build doesn't ship, order strongest-first, and fix
    # the chain order for combination: deblock (source artifact) -> denoise
    # (noise) -> deband (banding shows once noise is gone).
    out = [c for c in out if _ffmpeg_has_filter(c["vf"].split("=", 1)[0])]
    out.sort(key=lambda c: -float(c.get("severity") or 0.0))
    return out


def _preproc_chain(cands: list[dict]) -> str:
    """Combine candidates into one -vf fragment in the artifact-correct order."""
    order = {"deblock": 0, "denoise_light": 1, "denoise_med": 1, "deband": 2}
    return ",".join(c["vf"] for c in sorted(cands, key=lambda c: order.get(c["name"], 9)))


def _preproc_probe_variants(input_path: str, variants: dict, *, encoder: str,
                            video_bps: int, scale_width: int, duration_s: float,
                            status_cb=None, cancel_cb=None,
                            ref_cache: dict | None = None,
                            ref_cache_dir: str | None = None) -> dict | None:
    """
    A/B the prefilter variants against the unfiltered baseline at the job's real
    operating point. Mirrors the codec race: 1-2 representative segments, a
    lossless reference at delivery resolution (from the UNFILTERED source — the
    honest yardstick), each variant encoded at the same -b:v with the job's
    encoder family, scored with full-rate VMAF and bit-normalized (a variant
    that made the encoder undershoot gets credit, overshoot gets debited).

    Returns {"baseline": score, "<variant>": score, "_sizes": {...}} or None
    when probing isn't possible.
    """
    ff = FFMPEG
    if not ff or not _ffmpeg_has_filter("libvmaf"):
        return None
    dur = float(duration_s or 0.0)
    if dur < 3.0 or int(video_bps) <= 0 or not variants:
        return None

    e = (encoder or "x264").lower()
    tag = ("av1" if "av1" in e else "x265" if ("265" in e or "hevc" in e) else "x264")
    av1_enc = best_av1_encoder() if tag == "av1" else None
    if tag == "av1" and not av1_enc:
        tag = "x264"

    seg = max(3.0, min(6.0, 0.06 * dur))
    if dur >= 4.0 * seg:
        starts = [max(0.0, dur * 0.30 - seg / 2.0), max(0.0, dur * 0.65 - seg / 2.0)]
    else:
        starts = [max(0.0, dur * 0.5 - seg / 2.0)]

    _scale = (f"scale={int(scale_width)}:-2," if int(scale_width or 0) > 0 else "")
    names = ["baseline"] + list(variants.keys())
    scores: dict[str, list[float]] = {}
    sizes: dict[str, int] = {}

    work = tempfile.mkdtemp(prefix="bc_preproc_")
    try:
        for si_n, t0 in enumerate(starts):
            if callable(cancel_cb) and cancel_cb():
                return None
            ref_clip = _probe_ref_clip(ff, input_path, t0=t0, seg=seg,
                                       scale_width=int(scale_width or 0), own_dir=work,
                                       ref_cache=ref_cache, ref_cache_dir=ref_cache_dir)
            if not ref_clip:
                continue
            for name in names:
                if callable(cancel_cb) and cancel_cb():
                    return None
                vf_pre = variants.get(name, "")
                vf_full = (_scale + vf_pre).rstrip(",") if (name != "baseline") else _scale.rstrip(",")
                enc_name, cargs, suffix = _probe_codec_args(tag, av1_enc, int(video_bps))
                clip = os.path.join(work, f"v_{si_n}_{name}{suffix}")
                cmd = ([ff, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{t0:.3f}", "-t", f"{seg:.3f}",
                        "-i", os.path.abspath(input_path), "-an"]
                       + (["-vf", vf_full] if vf_full else [])
                       + ["-pix_fmt", "yuv420p"] + cargs
                       + ["-b:v", str(int(video_bps)), clip])
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   startupinfo=si, creationflags=NO_WIN)
                if r.returncode != 0 or not os.path.exists(clip) or os.path.getsize(clip) == 0:
                    continue
                vm = _probe_vmaf_fullrate(ff, ref_clip, clip)
                if vm is not None:
                    scores.setdefault(name, []).append(float(vm))
                    sizes[name] = sizes.get(name, 0) + os.path.getsize(clip)

        if "baseline" not in scores or not scores["baseline"]:
            return None
        _VMAF_PER_DOUBLING = 4.0
        out: dict = {"_sizes": dict(sizes)}
        for name, v in scores.items():
            raw_v = sum(v) / len(v)
            tgt_bytes = max(1.0, float(video_bps) * float(seg) * float(len(v)) / 8.0)
            act_bytes = max(1.0, float(sizes.get(name, 0)))
            adj = _VMAF_PER_DOUBLING * math.log2(tgt_bytes / act_bytes)
            out[name] = raw_v + max(-8.0, min(8.0, adj))
        return out
    except Exception:
        return None
    finally:
        _rmtree_quiet(work)


def decide_preprocessing(input_path: str, feats: dict, *, encoder: str,
                         video_bps: int, scale_width: int, height: int, width: int,
                         fps: float, duration_s: float, advanced_options: dict | None = None,
                         status_cb=None, cancel_cb=None,
                         ref_cache: dict | None = None,
                         ref_cache_dir: str | None = None) -> tuple[str | None, dict]:
    """
    Full artifact-aware preprocessing decision:
      1. gate candidate filters on measured content features + bit starvation;
      2. probe-validate the combined chain against the unfiltered baseline;
      3. if the combo fails, try the single strongest candidate;
      4. ship a chain only when it beats baseline by _PREPROC_KEEP_MARGIN.
    Returns (vf_chain_or_None, decision_info_for_logging).
    """
    adv = advanced_options or {}
    info: dict = {"candidates": [], "kept": None, "scores": None}
    allow_denoise = bool(adv.get("grain_filter", ADVANCED_DEFAULTS.get("grain_filter", True)))
    cands = _preproc_candidates(feats, video_bps=int(video_bps), width=int(width or 0),
                                height=int(height or 0), fps=float(fps or 30.0),
                                allow_denoise=allow_denoise)
    info["candidates"] = [{k: c[k] for k in ("name", "why")} for c in cands]
    if not cands:
        return None, info

    if callable(status_cb):
        status_cb("[Preproc] Indicated: "
                  + "; ".join(f"{c['name']} ({c['why']})" for c in cands)
                  + " — validating on probe segments...", "INFO")

    # Variant set: the combined chain, the single strongest candidate, and — when
    # a denoise is in play — the other denoise strength too (structured texture
    # is sensitive to strength; let the probe pick instead of guessing).
    variants: dict[str, str] = {}
    if len(cands) > 1:
        variants["+".join(c["name"] for c in cands)] = _preproc_chain(cands)
    variants[cands[0]["name"]] = cands[0]["vf"]
    for c in cands:
        if c["name"].startswith("denoise"):
            alt = "denoise_light" if c["name"] == "denoise_med" else "denoise_med"
            variants.setdefault(alt, _PREPROC_FILTERS[alt])
            break

    scores = _preproc_probe_variants(
        input_path, variants, encoder=encoder, video_bps=int(video_bps),
        scale_width=int(scale_width or 0), duration_s=float(duration_s or 0.0),
        status_cb=status_cb, cancel_cb=cancel_cb,
        ref_cache=ref_cache, ref_cache_dir=ref_cache_dir)
    info["scores"] = scores
    if not scores:
        if callable(status_cb):
            status_cb("[Preproc] Probe validation unavailable; encoding source as-is.", "INFO")
        return None, info

    base = float(scores.get("baseline") or 0.0)
    best_name, best_score = None, None
    for name in variants:
        v = scores.get(name)
        if v is not None and (best_score is None or float(v) > best_score):
            best_name, best_score = name, float(v)

    if best_name is not None and best_score >= base + _PREPROC_KEEP_MARGIN:
        info["kept"] = best_name
        if callable(status_cb):
            status_cb(f"[Preproc] {best_name} kept: probe VMAF {base:.1f} -> "
                      f"{best_score:.1f} (+{best_score - base:.1f}) at the same bitrate.", "INFO")
        return variants[best_name], info

    _gain = ((best_score - base) if best_score is not None else 0.0)
    if callable(status_cb):
        status_cb(f"[Preproc] No measurable gain (best {_gain:+.1f} VMAF vs baseline); "
                  f"encoding source as-is.", "INFO")
    return None, info


def _prepare_cover_file(input_path: str, work_dir: str, max_bytes: int) -> tuple[str, int, int] | None:
    """
    Extract the source album art and, if it's bigger than max_bytes, downscale it
    so embedding it doesn't blow a small size target. Album art is routinely
    3000x3000 / multiple MB — embedding that verbatim into a 2 MB target made the
    output overshoot no matter how low the audio bitrate went (the size search
    only tunes audio). Returns (jpg_path, width, height) or None (no cover / too
    tight to bother). A cover already under budget is kept as-is.
    """
    ff = FFMPEG
    if not ff or max_bytes < 8 * 1024:
        return None
    try:
        vst = _probe_video_stream(input_path) or {}
    except Exception:
        vst = {}
    codec = str(vst.get("codec_name") or "").lower()
    if codec not in ("mjpeg", "png", "jpeg", "jpg", "bmp"):
        return None
    W0, H0 = int(vst.get("width") or 0), int(vst.get("height") or 0)
    if W0 <= 0 or H0 <= 0:
        return None
    # Extract to a REAL extension — ffmpeg can't guess a muxer from ".img", so a
    # generic name silently produces nothing (that was an earlier bug here).
    src_ext = "png" if codec == "png" else "jpg"
    src = os.path.join(work_dir, f"cover_src.{src_ext}")
    try:
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", input_path, "-an", "-map", "0:v:0", "-c", "copy", src],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       startupinfo=si, creationflags=NO_WIN)
        if not (os.path.exists(src) and os.path.getsize(src) > 0):
            return None
        # Already small enough: use verbatim.
        if os.path.getsize(src) <= max_bytes:
            return (src, W0, H0)
        # Downscale to meet the byte budget (JPEG; drops alpha, fine for art).
        for dim, q in ((1000, 4), (800, 4), (640, 5), (500, 6), (400, 7), (300, 8)):
            if dim >= max(W0, H0) and dim != 300:
                continue
            out = os.path.join(work_dir, f"cover_{dim}.jpg")
            subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
                            "-vf", f"scale={dim}:-1", "-q:v", str(q), out],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           startupinfo=si, creationflags=NO_WIN)
            if os.path.exists(out) and 0 < os.path.getsize(out) <= max_bytes:
                w = min(dim, W0)
                h = int(round(H0 * (w / float(W0)))) or 1
                return (out, w, h)
        return None
    except Exception:
        return None


def _build_opus_cover_meta(input_path: str, cover: tuple[str, int, int] | None, work_dir: str) -> str | None:
    """
    Build an ffmetadata file carrying the source's tags PLUS a (size-capped)
    album art as a base64 METADATA_BLOCK_PICTURE — the only way opus/ogg can hold
    cover art (it can't carry a copied picture stream like mp3/m4a). The base64
    blob is too large for a command-line argument, so it must ride in a file.
    """
    import base64, struct
    ff = FFMPEG
    if not ff or not cover:
        return None
    cover_path, W, H = cover
    meta_path = os.path.join(work_dir, "covermeta.txt")
    try:
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", input_path, "-f", "ffmetadata", meta_path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       startupinfo=si, creationflags=NO_WIN)
        if not os.path.exists(meta_path):
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write(";FFMETADATA1\n")
        with open(cover_path, "rb") as f:
            data = f.read()
        mime = "image/png" if cover_path.lower().endswith(".png") else "image/jpeg"
        u32 = lambda x: struct.pack(">I", int(x))
        # FLAC picture block: type=3 (front cover), mime, empty desc, w/h/depth/colors, data.
        block = (u32(3) + u32(len(mime)) + mime.encode("ascii") + u32(0)
                 + u32(W) + u32(H) + u32(24) + u32(0) + u32(len(data)) + data)
        b64 = base64.b64encode(block).decode("ascii")
        with open(meta_path, "a", encoding="utf-8") as f:
            f.write("METADATA_BLOCK_PICTURE=" + b64 + "\n")
        return meta_path
    except Exception:
        return None


def _encode_audio_once(input_path: str, output_path: str, *,
                       encoder: str, bitrate_bps: int, sr: int,
                       channels: int, vbr_mode: str, loudnorm: bool,
                       highpass_hz: int | None, lowpass_hz: int | None,
                       extra_filters: list[str] | None = None,
                       privacy_preset: str | None = None,
                       opus_cover_meta: str | None = None,
                       cover_file: str | None = None) -> tuple[bool, int, str]:
    

    af = []

    if highpass_hz and highpass_hz > 0:
        af.append(f"highpass=f={highpass_hz}")
    if lowpass_hz and lowpass_hz > 0:
        af.append(f"lowpass=f={lowpass_hz}")

    if loudnorm:
        af.append("dynaudnorm=p=1")

    if extra_filters:
        af.extend(extra_filters)

    af_chain = ",".join(af) if af else None

    # Metadata + cover art. The old path used "-vn" (drops the album cover, which
    # is stored as an attached-picture video stream) AND the privacy default
    # "-map_metadata -1" (strips title/artist/album) — so every compressed track
    # came out naked. For music that's a data-loss bug: tags/art are the point
    # and aren't privacy-sensitive. Preserve both by default; only "strict"
    # privacy strips. Cover art can only ride containers with a picture stream —
    # ogg/opus cannot carry a copied mjpeg, so opus keeps tags but not the image.
    _strict_privacy = str(privacy_preset or "").lower() == "strict"
    _out_ext = os.path.splitext(output_path)[1].lower().lstrip(".")
    _is_ogg = _out_ext in ("opus", "ogg")
    # Cover art as a copied picture stream works for mp3/m4a/flac; ogg/opus can't
    # carry it that way, so it rides as a base64 METADATA_BLOCK_PICTURE injected
    # from opus_cover_meta (built by _extract_opus_cover_meta) instead.
    _cover_ok = (not _strict_privacy) and (not _is_ogg)
    _use_opus_meta = bool(opus_cover_meta) and _is_ogg and (not _strict_privacy) \
        and os.path.exists(opus_cover_meta or "")
    # A prepared (size-capped) cover file is embedded as a second image input so
    # a small target isn't blown by a multi-MB source cover. Without one, the
    # source's own picture stream is copied verbatim (used by the FLAC path).
    _use_cover_file = _cover_ok and bool(cover_file) and os.path.exists(cover_file or "")

    cmd = [FFMPEG, "-y", "-i", input_path]
    if _use_opus_meta:
        cmd += ["-i", opus_cover_meta]        # tags + embedded picture live here
    elif _use_cover_file:
        cmd += ["-i", cover_file]             # input 1 = size-capped album art
    cmd += ["-map", "0:a:0"]
    if _use_cover_file:
        cmd += ["-map", "1:v:0"]
    elif _cover_ok:
        cmd += ["-map", "0:v?"]               # verbatim source cover (lossless/FLAC path)
    cmd += ["-c:a", encoder]

    if encoder == "libopus":
        if vbr_mode == "off":
            cmd += ["-vbr", "off"]
        elif vbr_mode == "constrained":
            cmd += ["-vbr", "constrained"]
        else:
            cmd += ["-vbr", "on"]
        cmd += ["-compression_level", "10", "-application", "audio"]
    elif encoder == "flac":
        cmd += ["-compression_level", "8"]    # lossless: max compression effort

    if channels in (1, 2):
        cmd += ["-ac", str(channels)]
    if sr:
        cmd += ["-ar", str(sr)]

    if encoder != "flac":                     # FLAC is lossless — bitrate is N/A
        cmd += ["-b:a", str(int(bitrate_bps))]

    if af_chain:
        cmd += ["-af", af_chain]

    if _use_cover_file:
        cmd += ["-c:v", "mjpeg"]              # re-encode the capped cover
        if _out_ext in ("m4a", "mp4", "aac"):
            cmd += ["-disposition:v:0", "attached_pic"]
    elif _cover_ok:
        cmd += ["-c:v", "copy"]
        if _out_ext in ("m4a", "mp4", "aac"):
            cmd += ["-disposition:v:0", "attached_pic"]

    if _strict_privacy:
        cmd += _privacy_args("strict")        # strips tags + cover (opt-in)
    elif _use_opus_meta:
        cmd += ["-map_metadata", "1"]         # tags + cover picture from the meta file
    else:
        cmd += ["-map_metadata", "0"]         # carry title/artist/album/...

    cmd += [output_path]


    proc = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, startupinfo=si, creationflags=NO_WIN)
    ok = (proc.returncode == 0) and os.path.exists(output_path)
    size = os.path.getsize(output_path) if ok else 0
    tail = "\n".join((proc.stderr or "").splitlines()[-15:])
    return ok, size, tail



def _retry_figurine(n, total=7) -> str:
    """
    A tiny escalating mood meter for the size-retry loop: calm on the first
    retry, progressively more exasperated, and the classic table flip on the
    last attempt (or the 7th). Middle stages stay pure-ASCII so they render in
    any font; the flip uses box-drawing glyphs every monospace font ships.
    """
    try:
        n = int(n)
        total = int(total) if total else 7
    except Exception:
        return ""
    if n <= 0:
        return ""
    if n >= total or n >= 7:
        return "(╯°□°)╯ ┻━┻"   # (╯°□°)╯ ┻━┻
    faces = {1: "(-_-)", 2: "(>_>)", 3: "(>_<)", 4: "(o_O)",
             5: "(>_<)#", 6: "\\(°□°)/"}
    return faces.get(n, "(-_-)")


def _plain_status(msg: str):
    """
    Translate a technical status line into plain language for the Activity feed,
    so a non-technical user sees "Comparing codecs to find the best quality..."
    instead of "[Codec] VMAF probe @ 605kbps...". Returns friendly text, or None
    to hide pure-noise lines (ffmpeg command dumps, per-frame progress, internal
    diagnostics). Unknown-but-relevant lines fall through cleaned.
    """
    t = _normalize_text(str(msg)).strip()
    if not t:
        return None
    low = t.lower()

    _HIDE = ("ffmpeg two-pass", "ffmpeg.exe", "-c:v ", "passlogfile", "preflight",
             "heuristics:", "profile model", "planner skipped", "ml heuristic",
             "using encoder=", "effective=", "two-pass seed", "seed ->",
             "[pass ", "microprobe", "guardrail", "[seed]", "scene-aware",
             "[zones]", "-> ffmpeg", "-x265-params", "-x264-params", "-svtav1")
    if any(k in low for k in _HIDE):
        return None

    def _m(pat):
        return re.search(pat, t, re.I)

    if "compressing video" in low:  return "Compressing your video..."
    if "compressing audio" in low:  return "Compressing your audio..."
    if "compressing image" in low:  return "Compressing your image..."
    m = _m(r"starting encode @ target ~\s*([\d.]+\s?[KMG]?B)")
    if m: return f"Target size: {m.group(1)}."
    if "vmaf probe" in low or "comparing codec" in low:
        return "Comparing codecs to find the best quality for the size..."
    m = _m(r"auto-codec:\s*([\w-]+)\s+beat")
    if m: return f"Best codec chosen: {m.group(1).upper()} (best quality per MB)."
    if "delivery width" in low:
        return "Adjusting the resolution to suit the chosen codec..."
    if "native ffmpeg two-pass" in low or "two-pass" in low:
        return "Analyzing the file in two passes for the best quality..."
    if "[refine]" in low or "undershot target" in low:
        return "Fine-tuning to land closer to your target size..."
    m = _m(r"\[size\].*?retry\s*(\d+)\s*/\s*(\d+)")
    if m:
        _n, _tot = int(m.group(1)), int(m.group(2))
        return f"Adjusting the file size (attempt {_n})...  {_retry_figurine(_n, _tot)}"
    m = _m(r"\[size\].*?retry\s*(\d+)")
    if m: return f"Adjusting the file size (attempt {m.group(1)})...  {_retry_figurine(int(m.group(1)))}"
    if "[pack]" in low:
        return "Using the leftover space to add more quality..."
    if "measuring vmaf" in low:
        return "Checking the final quality..."
    m = _m(r"vmaf\s*([\d.]+)\s*\(([\w ]+)\)")
    if m: return f"Quality score: {round(float(m.group(1)))} out of 100 ({m.group(2).strip()})."
    m = _m(r"audio quality:\s*(.+?)\s*$")
    if m: return f"Quality: {m.group(1).strip()}."
    if "album art" in low:
        return "Keeping the album art."
    if "source already fits" in low:
        return "Already small enough - kept the original to avoid quality loss."
    if "lossless flac" in low:
        return "Saved as lossless FLAC - no quality lost."
    if "bitrate ceiling" in low:
        return "Reached the smallest size this format allows for this track."
    if "hw decode failed" in low or "software decode" in low:
        return "Switched to software decoding (this is normal)."
    if "primary path failed" in low:
        return "Adjusting the encoding approach..."
    m = _m(r"audio compressed to (.+?) in ([\d.]+)s")
    if m: return f"Done - audio compressed to {m.group(1)} in {m.group(2)}s."
    m = _m(r"compress done in ([\d.]+)s")
    if m: return f"Finished in {m.group(1)} seconds."
    m = _m(r"all files processed\.?\s*(\d+)/(\d+)\s*ok")
    if m: return f"All done - {m.group(1)} of {m.group(2)} files finished."
    if "cancelled" in low:
        return "Cancelled."
    if low.startswith("failed") or low.startswith("error") or " error" in low:
        return t  # surface errors verbatim (already cleaned)
    return t


def log_message(log_widget, msg, level="INFO"):
    msg = _normalize_text(msg)
    level = str(level or "INFO").upper()
    timestamp = time.strftime("%H:%M:%S")
    logging.log(getattr(logging, level, logging.INFO), msg)
    if not log_widget:
        return
    try:
        log_widget.configure(state="normal")
        # Divider before each new job so the log reads as clean sections.
        if msg.lower().startswith(("compressing video:", "compressing audio:", "compressing image:")):
            try:
                if log_widget.index("end-1c") not in ("1.0", "0.0"):
                    log_widget.insert("end", "─" * 52 + "\n", ("DIV",))
            except Exception:
                pass
        _fig = ""
        try:
            _rm = re.search(r"retry\s*(\d+)\s*/\s*(\d+)", msg, re.I)
            if _rm:
                _fig = "   " + _retry_figurine(int(_rm.group(1)), int(_rm.group(2)))
        except Exception:
            _fig = ""
        line = f"{timestamp}   {level:<7} {msg}{_fig}\n"
        try:
            log_widget.insert("end", line, (level,))
        except Exception:
            log_widget.insert("end", line)
        log_widget.see("end")
        log_widget.configure(state="disabled")
    except Exception:
        pass



# Standard delivery-width ladder for the last-resort ceiling downscale-retry.
_STD_WIDTHS = [3840, 2560, 1920, 1600, 1280, 1024, 960, 854, 640, 480, 426]


def next_lower_std_width(cur: int) -> int:
    """Largest standard delivery width strictly below `cur`; falls back to 80%
    (rounded down to even) and returns 0 once no sane lower width remains. Used
    by the ceiling downscale-retry to step resolution down when a target cannot
    be met at native size."""
    cur = int(cur or 0)
    for _sw in _STD_WIDTHS:
        if _sw < cur:
            return _sw
    nxt = int(cur * 0.8) & ~1
    return nxt if nxt >= 256 else 0

def get_video_metadata(filepath: str):

    if get_media_type(filepath) != "video":
        return 0.0, 0, 0, 0, 0.0

    data = _probe_media_cached(filepath)
    fmt_info = data.get("format", {}) or {}
    stream_info = _probe_video_stream(filepath) or {}
    try:
        duration = float(fmt_info.get("duration", 0) or 0)
    except Exception:
        duration = 0.0
    width = int(stream_info.get("width", 1280) or 1280)
    height = int(stream_info.get("height", 720) or 720)
    try:
        bitrate = int(stream_info.get("bit_rate") or fmt_info.get("bit_rate") or 5_000_000)
    except Exception:
        bitrate = 5_000_000
    framerate_raw = stream_info.get("avg_frame_rate", "30/1")
    try:
        framerate = float(Fraction(framerate_raw))
    except Exception:
        framerate = 30.0
    return duration, width, height, bitrate, framerate

def extract_video_duration(path):
    # Read duration from the shared cached probe (one ffprobe per file/mtime/size)
    # instead of spawning a fresh ffprobe on every call — this is hit repeatedly
    # in the encode path (metadata fallback, shrink probe, VMAF windows).
    try:
        fmt = (_probe_media_cached(path).get("format") or {})
        d = float(fmt.get("duration") or 0.0)
        return d if d > 0 else None
    except Exception:
        return None

def calculate_bitrate(duration, target_bytes, audio_bitrate, input_path=None):
    

    if not duration or duration <= 0:
        if input_path:
            probed = extract_video_duration(input_path)
            if probed and probed > 0:
                duration = probed

    if not duration or duration <= 0:
        duration = 60.0

    video_bits = (target_bytes * 8) - (audio_bitrate * duration)
    video_bits = max(video_bits, 0)

    return max(int(video_bits / duration), 100_000)


def determine_audio_bitrate(input_bitrate: int) -> int:
    if input_bitrate < 1_000_000:
        return 64 * 1000
    elif input_bitrate < 1_500_000:
        return 96 * 1000
    return DEFAULT_AUDIO_BITRATE

def determine_tune_profile(width: int, height: int, filename: str) -> str:
    lower = filename.lower()
    if "anime" in lower or "cartoon" in lower:
        return "animation"
    elif "cam" in lower or "grain" in lower:
        return "grain"
    return "film"

def determine_frame_rate(framerate: float, width: int, duration: float, target_bitrate: int | None = None):
    # Preserve source cadence unless bitrate is clearly too constrained for high-FPS output.
    if framerate <= 0:
        return None
    if target_bitrate is None or target_bitrate <= 0:
        return None
    if framerate < 50:
        return None

    if width >= 1920 and target_bitrate < 1_000_000:
        return 30
    if width >= 1280 and target_bitrate < 700_000:
        return 30
    if target_bitrate < 450_000 and duration > 15:
        return 24
    return None


def determine_resolution(width: int, height: int, target_bitrate: int, fps_hint: float | None = None,
                         encoder: str | None = None, complexity: float | None = None) -> int:
    if width <= 0:
        return width
    if target_bitrate <= 0:
        return width

    fps = float(fps_hint or 30.0)
    fps = max(12.0, min(120.0, fps))
    pix_rate = float(width * max(1, int(height))) * fps
    bpppf = float(target_bitrate) / max(1.0, pix_rate)

    # Content-aware: flat/simple content (screen recordings, UI, flat cartoons)
    # compresses CHEAPLY at native resolution — downscaling it only smears its
    # sharp text/edges for no real bitrate saving. The Shutter face-off caught
    # BitCrusher losing a screen rec (2166->1280, VMAF 81) to a native-res 2-pass
    # (VMAF 94). spatial_complexity separates it cleanly (~1 flat vs ~2.5+ natural),
    # so boost the effective bits/pixel for LOW-complexity content to keep its
    # resolution. Floored at 1.0 — never penalizes complex content.
    try:
        _cx = float(complexity) if complexity is not None else 0.0
    except Exception:
        _cx = 0.0
    if _cx > 0.0:
        bpppf *= max(1.0, min(2.2, 2.2 / max(1.0, _cx)))

    # Encoders differ hugely in how few bits/pixel they can survive on: x264
    # falls apart well before x265, and AV1 keeps working below both. Scale the
    # downscale thresholds accordingly (higher factor = downscales sooner).
    _e = (encoder or "x264").lower()
    if "av1" in _e:
        _f = 0.80
    elif any(k in _e for k in ("265", "hevc", "vp9", "vvc")):
        _f = 1.00
    else:  # x264 / h264 family
        _f = 1.40
    bpppf /= _f

    # Prefer keeping resolution unless compression pressure is severe.
    if width >= 3840:
        if bpppf < 0.022:
            return 1920
        if bpppf < 0.034:
            return 2560
    if width > 2560:
        if bpppf < 0.017:
            return 1920
        if bpppf < 0.025:
            return 2304
    if width > 1920:
        if bpppf < 0.010:
            return 1280
        if bpppf < 0.014:
            return 1600
    if width > 1280:
        if bpppf < 0.006:
            return 960
        if bpppf < 0.0085:
            return 1152
        if bpppf < 0.011:
            return 1280
    return width

def _ff_args_unique(args: list[str] | None) -> list[str]:
    if not args:
        return []
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if i + 1 < len(args) and a.startswith("-") and not args[i + 1].startswith("-"):

            pair = (a, args[i + 1])

            if any(out[j] == pair[0] and out[j + 1] == pair[1] for j in range(0, len(out) - 1, 2)):
                i += 2
                continue
            out.extend(pair)
            i += 2
        else:

            if a not in out:
                out.append(a)
            i += 1
    return out



def compress_with_handbrake(
    input_path: str,
    output_path: str,
    audio_bitrate: int | None = None,
    encoder: str | None = "x264",
    crf: int | None = None,
    bitrate: int | None = None,
    width: int | None = None,
    fps: float | None = None,
    tune: str | None = None,
    two_pass: bool = False,
    hwaccel: str | None = None,
    audio_copy: bool = False,
    early_abort_guard: tuple[int, int] | None = None,
    turbo: bool | None = None,
    advanced_options: dict | None = None,
) -> bool:

    
    import os, shutil, subprocess, glob

    # Resolve ffmpeg the same way the rest of the app does (explicit override >
    # the configured FFMPEG, which prefers tools/ > PATH). Mixing binaries here
    # broke encoder detection: probes saw one build's encoders while encodes ran
    # another build without them.
    _ff_pref = str((advanced_options or {}).get("ffmpeg_path") or "").strip()
    ffmpeg = ((shutil.which(_ff_pref) if _ff_pref else None)
              or (FFMPEG if os.path.isfile(FFMPEG) else shutil.which(FFMPEG))
              or shutil.which("ffmpeg"))
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH")

    raw_enc = (encoder or (advanced_options or {}).get("encoder") or "x264")

    def _norm(enc_str: str) -> str:
        s_raw = (enc_str or "").strip().lower()
        import re
        s = re.sub(r"\s*\(.*?\)\s*", "", s_raw)  # drop parentheses
        def has(*keys: str) -> bool:
            return any(k in s_raw for k in keys) or any(k in s for k in keys)

        if "nvenc" in s_raw:
            if has("av1"):  return "av1_nvenc"
            if has("265","hevc","h.265"): return "hevc_nvenc"
            return "h264_nvenc"
        if "qsv" in s_raw:
            if has("av1"):  return "av1_qsv"
            if has("265","hevc","h.265"): return "hevc_qsv"
            return "h264_qsv"
        if "amf" in s_raw:
            if has("av1"):  return "av1_amf"
            if has("265","hevc","h.265"): return "hevc_amf"
            return "h264_amf"
        if "vaapi" in s_raw:
            if has("av1"):  return "av1_vaapi"
            if has("265","hevc","h.265"): return "hevc_vaapi"
            return "h264_vaapi"

        if has("vvc","vvenc","x266","266","h.266"): return "vvc"
        if has("av1","svt-av1","aom-av1","libaom"): return "av1"
        if has("vp9","libvpx"): return "vp9"
        if has("x265","hevc","h265","h.265"): return "x265"
        if has("x264","avc","h264","h.264"): return "x264"
        return "x264"

    enc = _norm(raw_enc)

    vcodec_map = {

        "x264": "libx264", "libx264": "libx264",
        "x265": "libx265", "libx265": "libx265", "hevc": "libx265",
        "av1": "libsvtav1", "svt-av1": "libsvtav1", "libsvtav1": "libsvtav1",
        "aom-av1": "libaom-av1", "libaom-av1": "libaom-av1",
        "vp9": "libvpx-vp9", "libvpx-vp9": "libvpx-vp9",
        "vvc": "libvvenc", "x266": "libvvenc", "vvenc": "libvvenc", "libvvenc": "libvvenc",

        "h264_nvenc": "h264_nvenc", "hevc_nvenc": "hevc_nvenc", "av1_nvenc": "av1_nvenc",

        "h264_qsv": "h264_qsv", "hevc_qsv": "hevc_qsv", "av1_qsv": "av1_qsv",

        "h264_amf": "h264_amf", "hevc_amf": "hevc_amf", "av1_amf": "av1_amf",

        "h264_vaapi": "h264_vaapi", "hevc_vaapi": "hevc_vaapi", "av1_vaapi": "av1_vaapi",
    }
    desired_vcodec = vcodec_map.get(enc, "libx264")

    _ffmpeg_enc_cache = getattr(compress_with_handbrake, "_enc_cache", None)
    if _ffmpeg_enc_cache is None:
        try:
            out = _sp_check_output([FFMPEG, "-hide_banner", "-encoders"], text=True, startupinfo=si, creationflags=NO_WIN)
            have = set()
            for line in (out or "").splitlines():

                parts = line.split()
                if len(parts) >= 2 and parts[0].startswith("V"):
                    have.add(parts[1].strip())
            _ffmpeg_enc_cache = have
        except Exception:
            _ffmpeg_enc_cache = set()
        setattr(compress_with_handbrake, "_enc_cache", _ffmpeg_enc_cache)

    def _has_enc(name: str) -> bool:
        return name in _ffmpeg_enc_cache if _ffmpeg_enc_cache else False

    fallbacks_by_family = {
        "vvc":       ["libvvenc", "libx265", "libx264"],
        "x265":      ["libx265", "libx264"],
        "av1":       ["libsvtav1", "libaom-av1", "libx265", "libx264"],
        "vp9":       ["libvpx-vp9", "libx265", "libx264"],
        "x264":      ["libx264"],
        "h264_nvenc":["h264_nvenc", "libx264"],
        "hevc_nvenc":["hevc_nvenc", "libx265", "libx264"],
        "av1_nvenc": ["av1_nvenc", "libsvtav1", "libaom-av1", "libx265", "libx264"],
        "h264_qsv":  ["h264_qsv", "libx264"],
        "hevc_qsv":  ["hevc_qsv", "libx265", "libx264"],
        "av1_qsv":   ["av1_qsv", "libsvtav1", "libaom-av1", "libx265", "libx264"],
        "h264_amf":  ["h264_amf", "libx264"],
        "hevc_amf":  ["hevc_amf", "libx265", "libx264"],
        "av1_amf":   ["av1_amf", "libsvtav1", "libaom-av1", "libx265", "libx264"],
        "h264_vaapi":["h264_vaapi", "libx264"],
        "hevc_vaapi":["hevc_vaapi", "libx265", "libx264"],
        "av1_vaapi": ["av1_vaapi", "libsvtav1", "libaom-av1", "libx265", "libx264"],
    }
    family = enc
    candidates = fallbacks_by_family.get(family, [desired_vcodec, "libx264"])

    vcodec = None
    for cand in candidates:
        if _has_enc(cand):
            vcodec = cand
            break

    vcodec = vcodec or desired_vcodec

    if two_pass and (vcodec == "libvvenc" or any(tag in vcodec for tag in ("_nvenc","_qsv","_amf","_vaapi"))):
        two_pass = False

    try:
        status_cb = (advanced_options or {}).get("status_cb") or globals().get("status_callback")
        if callable(status_cb):
            status_cb(f"Using encoder={raw_enc} -> normalized={enc} -> ffmpeg -c:v {vcodec}", level="INFO")
    except Exception:
        pass


    if callable(globals().get("status_callback", None)):
        try: status_callback(f"Using encoder={enc} -> ffmpeg -c:v {vcodec}", level="INFO")
        except Exception: pass

    _preset_default = "slow" if vcodec == "libx265" else "medium"
    preset = str((advanced_options or {}).get("preset", _preset_default))

    use_bitrate_mode = bitrate is not None
    v_bitrate = None
    if use_bitrate_mode:
        v_bitrate = int(max(24_000, int(bitrate)))

    out_ext = os.path.splitext(output_path)[1].lower()
    mp4_like = out_ext in (".mp4", ".m4v", ".mov", "")

    # Multi-audio-track mapping (keep-first / mix). amix can't stream-copy, so a
    # "mix" plan forces an audio re-encode regardless of the audio_copy request.
    _atplan = (advanced_options or {}).get("_audio_track_plan") or {}
    _acopy_ref = {"audio_copy": bool(audio_copy)}
    audio_map_args = _audio_map_ffmpeg_args(_atplan, _acopy_ref) if _atplan.get("multi") else []
    audio_copy = _acopy_ref["audio_copy"]

    a_args: list[str] = []
    if audio_copy:
        a_args = ["-c:a", "copy"]
    else:

        a_br = int(audio_bitrate) if audio_bitrate else 128_000

        _ladder = [64000, 96000, 128000, 160000, 192000, 224000, 256000, 320000]
        a_br = min(_ladder, key=lambda x: abs(x - a_br))
        # Opus beats AAC at every bitrate; use it whenever the container allows.
        if out_ext in (".mkv", ".webm") and _has_enc("libopus"):
            a_args = ["-c:a", "libopus", "-b:a", f"{a_br//1000}k"]
        else:
            a_args = ["-c:a", "aac", "-b:a", f"{a_br//1000}k"]

    vf_parts: list[str] = []
    # HDR -> SDR tone-map first (in linear light) when the target codec is 8-bit,
    # so the result isn't washed out. Skipped for 10-bit-capable codecs.
    if _probe_is_hdr_path(input_path):
        _tm = _hdr_tonemap_vf(vcodec)
        if _tm:
            vf_parts.append(_tm)
            try:
                status_cb = (advanced_options or {}).get("status_cb") or globals().get("status_callback")
                if callable(status_cb):
                    status_cb("[HDR] HDR source + 8-bit codec -> tone-mapping BT.2020->BT.709.", level="INFO")
            except Exception:
                pass
    if width:
        vf_parts.append(f"scale={int(width)}:-2")
    # Validated artifact-aware prefilters (set by decide_preprocessing).
    _pre_vf = str((advanced_options or {}).get("preproc_vf") or "").strip()
    if _pre_vf:
        vf_parts.append(_pre_vf)
    vf_arg: list[str] = ["-vf", ",".join(vf_parts)] if vf_parts else []

    r_arg: list[str] = []
    if fps:

        r_arg = ["-r", str(int(round(float(fps))))]

    _v_args, v_ratectrl = _codec_video_args(
        vcodec,
        preset=preset,
        tune=tune,
        crf=crf,
        v_bitrate=(v_bitrate if use_bitrate_mode else None),
        fps=fps,
        advanced_options=advanced_options,
    )
    v_common: list[str] = ["-c:v", vcodec] + _v_args

    def _run(cmd: list[str]) -> None:
        _cb = (advanced_options or {}).get("progress_cb")
        _jid = (advanced_options or {}).get("job_id")

        # Early-abort runner. If early_abort_guard is set, add progress-aware monitoring.
        if early_abort_guard:
            target_bytes, under_abs = int(early_abort_guard[0]), int(early_abort_guard[1])
            dur_s = float((advanced_options or {}).get("duration_s") or 0.0)
            # If the command lacks -progress, add it here (last flags win).
            if "-progress" not in cmd:
                cmd = list(cmd) + ["-progress", "pipe:1"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                out_time = 0.0
                total_size = 0
                _last_cb = 0.0
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        if proc.poll() is not None:
                            break
                        continue
                    l = line.strip()
                    if l.startswith("out_time_ms="):
                        try:
                            out_time = int(l.split("=",1)[1]) / 1_000_000.0
                        except Exception:
                            pass
                        if callable(_cb) and dur_s > 0 and out_time > 0:
                            _now = time.monotonic()
                            if _now - _last_cb >= 1.0:
                                _last_cb = _now
                                try:
                                    _cb(_jid, {"stage": "encoding",
                                               "pct": min(100.0, out_time / dur_s * 100.0),
                                               "detail": "Encode"})
                                except Exception:
                                    pass
                    elif l.startswith("total_size="):
                        try:
                            total_size = int(l.split("=",1)[1])
                        except Exception:
                            pass
                    elif l == "progress=end":
                        break

                    # Guard only if we know duration
                    if dur_s and out_time > max(2.0, 0.06*dur_s):
                        # Fraction of stream processed so far
                        frac = min(0.99, out_time / dur_s)
                        # Acceptable running window:
                        # upper bound tracks (target) and lower bound is (target - under_abs)
                        upper = (target_bytes) * frac
                        lower = max(0, (target_bytes - under_abs)) * frac
                        # Overshoot guard with small slack; undershoot guard with larger slack to avoid false positives
                        if total_size > upper * 1.08:
                            proc.terminate()
                            proc.wait(timeout=2)
                            raise subprocess.CalledProcessError(returncode=255, cmd=cmd, output="early_abort:overshoot")
                        if total_size < lower * 0.90 and frac >= 0.25:
                            # Do NOT abort on undershoot. Let the encode finish so the size controller
                            # gets a real observation to learn from.
                            pass
                # Finalize
                rc = proc.wait()
                if rc != 0:
                    raise subprocess.CalledProcessError(returncode=rc, cmd=cmd, output="ffmpeg failed")
                return
            finally:
                try:
                    proc.kill()
                except Exception:
                    pass
        else:
            _dur = float((advanced_options or {}).get("duration_s") or 0.0)
            if callable(_cb) and _dur > 0:
                rc = _ffmpeg_run_with_progress(cmd, _dur, "Encode",
                                               progress_cb=_cb, job_id=_jid, stage="encoding")
                if rc != 0:
                    raise subprocess.CalledProcessError(returncode=rc, cmd=cmd, output="ffmpeg failed")
            else:
                _sp_run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    try:
        if os.path.exists(output_path):
            os.remove(output_path)
    except Exception:
        pass

    _adv_ph = advanced_options or {}
    _progress_cb = _adv_ph.get("progress_cb")
    _job_id = _adv_ph.get("job_id")

    # Prefer the dedicated helper for software true two-pass encoders.
    if two_pass and use_bitrate_mode and vcodec in ("libx264", "libx265", "libvpx-vp9", "libsvtav1"):
        try:
            return bool(_ffmpeg_two_pass_encode(
                input_path=input_path,
                output_path=output_path,
                encoder=vcodec,
                bitrate=int(v_bitrate or 0),
                width=width,
                fps=fps,
                tune=tune,
                audio_bitrate=audio_bitrate,
                audio_copy=audio_copy,
                preset=preset,
                turbo=bool(turbo),
                duration_s=float(_adv_ph.get("duration_s") or 0.0),
                progress_cb=_progress_cb,
                job_id=_job_id,
                advanced_options=advanced_options,
            ))
        except Exception:
            return False

    mux_args = ["-movflags", "+faststart"] if mp4_like else []
    try:
        cmd = ([ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
               + _hw_decode_args(advanced_options)
               + ["-i", input_path, "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn"])
        cmd += v_common + vf_arg + r_arg + v_ratectrl + audio_map_args + a_args + mux_args + [output_path]
        try:
            _run(cmd)
        except Exception:
            # First suspect: GPU decode (exotic source/driver). Retry software.
            if "-hwaccel" in cmd:
                _mark_hw_decode_broken()
                logging.getLogger("BitCrusher").warning(
                    "HW decode failed; retrying with software decode.")
                cmd = _strip_hw_args(cmd)
                try:
                    _run(cmd)
                except Exception:
                    pass
                else:
                    cmd = None  # success marker
            if cmd is not None:
                # 10-bit can fail on 8-bit-only encoder builds; retry once in 8-bit.
                retried = False
                for _pf in ("yuv420p10le", "p010le"):
                    if _pf in cmd:
                        cmd8 = list(cmd)
                        i = cmd8.index(_pf)
                        del cmd8[i - 1:i + 1]
                        _run(cmd8)
                        retried = True
                        break
                if not retried:
                    raise
    except Exception:
        return False

    try:
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception:
        return False


def get_media_type(input_path: str) -> str:
    ext = os.path.splitext(input_path)[1].lower()
    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".3g2", ".mpeg", ".mpg"}
    audio_exts = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".wma", ".m4a", ".opus", ".alac", ".aiff", ".aif"}
    image_exts = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".heic", ".heif", ".jxl", ".raw", ".avif"}
    doc_exts   = {".pdf"}
    if ext in video_exts:  return "video"
    if ext in audio_exts:  return "audio"
    if ext in image_exts:  return "image"
    if ext in doc_exts:    return "document"
    return "unknown"



def parse_dnd_files(data: str) -> list:
    data = data.strip()
    files = re.findall(r'\{([^}]+)\}', data)
    if not files:
        files = data.replace("\r", "\n").split("\n")
    cleaned = []
    for f in files:
        f = f.strip().strip("{}")
        if f.startswith("file:///"):
            f = f[8:]
        elif f.startswith("file://"):
            f = f[7:]
        if os.name == "nt":
            f = f.replace("/", "\\")
        cleaned.append(os.path.normpath(f))
    return cleaned

def binary_search_video_crf(input_path: str, temp_output: str, audio_bitrate: int,
                            encoder: str, width: int, fps: int, tune: str, two_pass: bool,
                            target_size_bytes: int, low: int, high: int,
                            status_callback, cancel_callback) -> int:
    best_crf = None
    while low <= high:
        mid = (low + high) // 2
        if cancel_callback():
            status_callback("Video compression cancelled during binary search.", level="WARNING")
            return None
        status_callback(f"Testing CRF={mid}...")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        try:
            compress_with_handbrake(
                input_path, temp_output, audio_bitrate,
                encoder=encoder, crf=mid, width=width,
                fps=fps, tune=tune, two_pass=two_pass
            )
        except Exception as err:
            status_callback(f"Error at CRF {mid}: {err}", level="ERROR")
            return None
        if not os.path.exists(temp_output):
            status_callback("No output produced for CRF " + str(mid), level="ERROR")
            return None
        size = os.path.getsize(temp_output)
        status_callback(f"CRF {mid} produced {format_bytes(size)}")
        if size > target_size_bytes:
            low = mid + 1
        else:
            best_crf = mid
            if size >= target_size_bytes * 0.9:
                return mid
            high = mid - 1
    return best_crf

def binary_search_audio_bitrate(input_path: str, temp_output: str, audio_encoder: str,
                                low: int, high: int, target_size_bytes: int,
                                status_callback, cancel_callback) -> int:
    best_bitrate = None
    while low <= high:
        mid = (low + high) // 2
        if cancel_callback():
            status_callback("Audio compression cancelled during binary search.", level="WARNING")
            return None
        status_callback(f"Testing bitrate: {mid}bps...")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        cmd = [FFMPEG, "-y", "-i", input_path, "-vn", "-c:a", audio_encoder, "-b:a", str(mid), temp_output]
        result = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=si, creationflags=NO_WIN)
        if result.returncode != 0:
            status_callback(f"ffmpeg error at bitrate {mid}: {result.stderr.strip()}", level="ERROR")
            return None
        if not os.path.exists(temp_output):
            status_callback("No output produced for bitrate " + str(mid), level="ERROR")
            return None
        size = os.path.getsize(temp_output)
        status_callback(f"Bitrate {mid} produced {format_bytes(size)}")
        if size > target_size_bytes:
            high = mid - 1
        else:
            best_bitrate = mid
            if size >= target_size_bytes * 0.9:
                return mid
            low = mid + 1
    return best_bitrate


def compress_audio_files(self):
    files = [f for f in self.file_queue if f.lower().endswith(self.supported_audio_formats)]
    if not files:
        self.log_info("No audio files to compress.")
        return

    self.log_info(f"Starting audio compression: {len(files)} files.")
    with ThreadPoolExecutor(max_workers=int(getattr(self, "settings", {}).get("parallel_workers", 1))) as pool:
        futures = [pool.submit(self.compress_audio, f) for f in files]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                self.log_exception(f"Audio compression crash: {e}")



def compress_image_files(self):
    files = [f for f in self.file_queue if f.lower().endswith(self.supported_image_formats)]
    if not files:
        self.log_info("No image files to compress.")
        return

    self.log_info(f"Starting image compression: {len(files)} files.")
    with ThreadPoolExecutor(max_workers=int(getattr(self, "settings", {}).get("parallel_workers", 1))) as pool:
        futures = [pool.submit(self.compress_image, f) for f in files]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                self.log_exception(f"Image compression crash: {e}")


from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count

def compress_video_files(self):
    files = [f for f in self.file_queue if f.lower().endswith(self.supported_video_formats)]
    if not files:
        self.log_info("No video files to compress.")
        return

    self.log_info(f"Starting video compression: {len(files)} files.")
    with ThreadPoolExecutor(max_workers=int(getattr(self, "settings", {}).get("parallel_workers", 1))) as pool:

        out_dir = (self.save_path.get() if hasattr(self, "save_path") else os.path.dirname(files[0]) or os.getcwd())
        tgt     = (self._get_target_bytes() if hasattr(self, "_get_target_bytes") else 10 * 1024 * 1024)
        adv     = (self.gather_advanced_options() if hasattr(self, "gather_advanced_options") else {})
        wh      = (self.webhook_url.get() if hasattr(self, "webhook_url") and getattr(self, "use_webhook", None) and self.use_webhook.get() else "")

        futures = []
        for f in files:
            _adv = dict(adv)
            _adv.update(getattr(self, "per_file_opts", {}).get(f, {}))
            futures.append(pool.submit(self.compress_file_task, f, out_dir, tgt, wh, _adv))
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                self.log_exception(f"Video compression crash: {e}")


import math

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

# --- unified output-naming helper (prefix/suffix always applied, all media) ---
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
    from pathlib import Path
    stem = Path(input_path).stem
    prefix = str(adv.get("output_prefix", "") or "")
    suffix = str(adv.get("output_suffix", "") or "")
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
    # _build_output_path only guards a candidate against colliding with ITS OWN
    # input path -- it has no notion of a sibling duplicate's already-written
    # output. Two duplicate source files that happen to share a basename
    # (e.g. "clip.mp4" copied into two different folders) produce the exact
    # same candidate from _bc_build_output_path, so a reuse-copy would collide
    # with the canonical's own output (SameFileError, or a silent overwrite,
    # depending on write order). Disambiguate the same way _build_output_path
    # already disambiguates against its own input: an incrementing suffix.
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


def _best_audio_codec(container: str, audio_fmt_pref: str) -> str:
    """
    Returns the best audio codec for the given container and user preference.
    - "auto": picks Opus for MKV/WebM (where it has full support) and AAC for MP4.
    - Any explicit value (e.g. "aac", "opus", "mp3") is passed through unchanged.
    """
    pref = (audio_fmt_pref or "auto").strip().lower()
    if pref and pref != "auto":
        return pref
    if (container or "mp4").lower() in ("mkv", "webm"):
        return "opus"
    return "aac"



def _ledger_log_failure(input_path: str, features: dict, stage: str,
                        error_code: str, error_message: str) -> None:
    """Best-effort ledger record for an ABORTED encode (undecodable source,
    infeasible budget, exhausted fallback chain) — closes the learning
    system's biggest blind spot: previously the ledger only ever saw
    successful encodes, so nothing recorded what fails or why. Called right
    before the real error propagates; must never itself raise or mask it."""
    try:
        from outcome_ledger import build_record, build_op, ledger_append
        stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
        rec = build_record(
            input_path=input_path,
            features=(features or {}),
            src={},
            op=build_op(target_bytes=0, encoder_req="", encoder_eff="",
                       width=0, height=0, fps=0.0, v_bps=0, audio_bps=0,
                       audio_copy=False, preset=None, quality_mode=None,
                       preproc=None, film_grain=None, film_grain_ratio=None,
                       spotlight=None, dur=0.0),
            attempts=[], race=None,
            outcome={"success": False, "error_stage": str(stage),
                    "error_code": str(error_code),
                    "error_message": str(error_message)[:300]},
            shadow=None, vmaf_model="vmaf_v0.6.1")
        with _STATS_LOCK:
            ledger_append(stats_dir, rec)
    except Exception:
        pass


def compress_video(input_path: str, save_path: str, status_cb,
                   target_size_mb: int, webhook_url: str,
                   advanced_options: dict, cancel_cb) -> dict:
    

    status_cb(f"Compressing video: {input_path}")
    import os
    os.environ["BC_CURRENT_INPUT"] = str(input_path)

    _progress_cb = (advanced_options or {}).get("progress_cb")
    _job_id = (advanced_options or {}).get("job_id") or str(input_path)

    def _emit(stage: str, **kw) -> None:
        if callable(_progress_cb):
            try:
                _progress_cb(_job_id, {"stage": stage, **kw})
            except Exception:
                pass


    bitrate = None
    audio_br = None
    audio_copy = False
    t_start = time.time()
    try:
        _v = float(target_size_mb)

        target_bytes = int(_v) if _v >= (128 * 1024) else int(_v * 1024 * 1024)
        target_bytes = max(1, target_bytes)
    except Exception:
        target_bytes = 10 * 1024 * 1024  # sane fallback (10 MB)
    target_bytes = apply_target_size_margin(target_bytes)

    # Quality mode: fast (quick single shot) | balanced (no-overshoot targeting)
    # | max (balanced + pack the leftover budget, higher AV1 effort). Legacy
    # setting value "quality_first" maps to "max".
    _qmode = str((advanced_options or {}).get("quality_mode")
                 or ADVANCED_DEFAULTS.get("quality_mode") or "max").strip().lower()
    if _qmode in ("quality_first", "", "qualityfirst"):
        _qmode = "max"
    if _qmode not in ("fast", "balanced", "max"):
        _qmode = "max"
    advanced_options["quality_mode"] = _qmode
    if _qmode == "fast":
        advanced_options.setdefault("iterative_max_attempts", 3)
        try:
            if not float((advanced_options or {}).get("min_vmaf") or 0.0):
                advanced_options["measure_quality"] = False
        except Exception:
            pass

    # === Discord playback-compatibility (opt-in) ============================
    # Pin the output to H.264 video + AAC audio in an MP4 so it always plays
    # inline on Discord mobile / old clients. This overrides the encoder choice
    # and disables the codec race — the size cost is one the user opted into.
    _discord_compat = bool((advanced_options or {}).get(
        "discord_compat", ADVANCED_DEFAULTS.get("discord_compat", False)))
    if _discord_compat:
        _prev_enc = str((advanced_options or {}).get("encoder") or "x264")
        if _prev_enc.lower() not in ("x264", "libx264", "h264"):
            status_cb("[Discord] Discord-compatible mode: forcing H.264 + AAC (MP4) for guaranteed "
                      "inline playback (size cost accepted).", "INFO")
        advanced_options["encoder"] = "x264"
        advanced_options["auto_codec"] = False
        advanced_options["codec_pinned"] = True
        advanced_options["audio_format"] = "aac"
        advanced_options["output_container"] = "mp4"
        advanced_options["source_candidate_encoder"] = "x264"

    # === Spotlight mode (keep everything, boost the marked range) ===========
    # Requires x264/x265 zones, which must be disjoint - so a Spotlight file
    # pins its codec, disables scene-zones (Spotlight owns zoning), and skips
    # the codec race. The zone itself is injected after the planner runs.
    _spot_raw = (advanced_options or {}).get("spotlight_range")
    if _spot_raw:
        try:
            _spa, _spb = _parse_trim_range(str(_spot_raw))
            advanced_options["_spotlight_secs"] = (_spa, _spb)
            advanced_options["scene_zones"] = False
            advanced_options["auto_codec"] = False
            advanced_options["codec_pinned"] = True
            _enc_now = str((advanced_options or {}).get("encoder") or "x264").lower()
            if _enc_now not in ("x264", "libx264", "h264", "x265", "libx265", "hevc"):
                status_cb(f"[Spotlight] Encoder {_enc_now} has no rate-control zones; "
                          f"using x264 for this file.", "INFO")
                advanced_options["encoder"] = "x264"
        except ValueError as _spe:
            status_cb(f"[Spotlight] Invalid range '{_spot_raw}' ({_spe}); ignored.",
                      "WARNING")
            advanced_options.pop("_spotlight_secs", None)

    # === Multi-audio-track plan =============================================
    # Sources with >1 audio track (OBS/ShadowPlay game+mic) used to silently drop
    # track 2. Decide keep-first vs mix once, up front, and let the encoders map
    # audio accordingly. Notify the user either way.
    try:
        _atrack_plan = _audio_track_plan(input_path, advanced_options)
        advanced_options["_audio_track_plan"] = _atrack_plan
        if _atrack_plan.get("notice"):
            status_cb(_atrack_plan["notice"], "INFO")
    except Exception:
        advanced_options["_audio_track_plan"] = {"n": 0, "mode": "keepfirst",
                                                 "multi": False, "notice": None}

    _jsonl_log("start_job", {"type": "video", "input": input_path, "target_bytes": target_bytes,
                             "quality_mode": _qmode})

    # === Fail-fast on an undecodable/unreadable source ======================
    # get_video_metadata() never raises on a bad stream - it silently returns
    # fabricated defaults (10s/1280x720/30fps), so a corrupt source used to
    # sail through into the full primary + 5-fallback encode chain and burn
    # several minutes before a generic "Encode failed" error. A cheap ffprobe
    # (already cached, so this doesn't add a second probe later) is enough to
    # catch a genuinely unreadable file up front without rejecting unusual-but
    # -valid streams that ffmpeg can still decode.
    _probe_streams = _probe_media_cached(input_path).get("streams") or []
    if not _probe_streams:
        _ledger_log_failure(input_path, {}, "probe", "probe_undecodable",
                            f"Source undecodable or unreadable: {input_path}")
        raise RuntimeError(f"[Probe] Source undecodable or unreadable: {input_path}")
    # Extension-based routing (get_media_type) only looks at the filename, so an
    # audio file saved with a video extension (e.g. a voice memo exported as
    # .mp4) still lands here. It has streams (audio), so the check above passes,
    # but with no video stream, width/height never get repaired downstream and
    # the encode is attempted against a degenerate 0x0 geometry — guaranteed to
    # fail through every fallback tier before raising a generic error. Catch it
    # here, cheaply, using the probe we already have.
    if not any(str(s.get("codec_type") or "") == "video" for s in _probe_streams):
        _ledger_log_failure(input_path, {}, "probe", "probe_no_video_stream",
                            f"Source has no video stream: {input_path}")
        raise RuntimeError(f"[Probe] Source has no video stream: {input_path}")

    # scene-zones (heuristic fallback): compute once per input; the planner's
    # zone export later overrides these when it produces params. Values are
    # mirrored into advanced_options (per-job) — the env vars remain only as a
    # legacy fallback and are unsafe under concurrent jobs.
    if bool((advanced_options or {}).get("scene_zones", True)):
        try:
            from ml_heuristics import build_scene_params  # returns (x264_params_str, qpfile_path_or_None)
            _xparams, _qpfile = build_scene_params(input_path)
            if _xparams:
                advanced_options["x264_params"] = _xparams
                os.environ["BC_X264_PARAMS"] = _xparams
            if _qpfile:
                advanced_options["qpfile"] = _qpfile
                os.environ["BC_QPFILE"] = _qpfile
        except Exception:
            pass
    try:
        if target_bytes > 0 and os.path.getsize(input_path) <= int(target_bytes * 0.99):
            suffix = datetime.now().strftime("%H%M%S")
            out_file = _build_output_path("video", input_path, save_path, advanced_options, default_ext="mp4")
            tmp_final = out_file + ".partial"
            if os.path.exists(tmp_final):
                os.remove(tmp_final)

            privacy_preset = advanced_options.get("privacy_preset")
            ok = _remux_smart(input_path, tmp_final, _privacy_args(privacy_preset))
            if not ok:
                status_cb("Passthrough remux failed; falling back to near-lossless re-encode (CRF 18).", level="WARNING")

                status_cb("Passthrough failed -> doing CRF 18 near-lossless transcode for MP4 compatibility.", "WARNING")

                if os.path.exists(tmp_final):
                    try:
                        os.remove(tmp_final)
                    except Exception:
                        pass

                _ok_lossless = compress_with_handbrake(
                    input_path=input_path,
                    output_path=tmp_final,
                    encoder="x264",
                    bitrate=None,
                    crf=18,
                    two_pass=False,
                    width=None,
                    fps=None,
                    audio_bitrate=192_000,
                    audio_copy=False,
                    advanced_options={"audio_format": "aac"},
                )

                if not _ok_lossless or not os.path.exists(tmp_final):
                    _ledger_log_failure(input_path, {}, "encode",
                                        "near_lossless_fallback_failed",
                                        "Near-lossless fallback encode failed")
                    raise RuntimeError("Near-lossless fallback encode failed")

                out_file = _build_output_path("video", input_path, save_path,
                                              advanced_options or {}, default_ext="mp4")
                shutil.move(tmp_final, out_file)
                final_size = os.path.getsize(out_file)

                try:
                    stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
                except Exception:
                    stats_dir = os.path.join(os.getcwd(), "user_settings", "stats")
                try:
                    with _STATS_LOCK:
                        learn_from_result(
                            stats_dir,
                            encoder="x264",
                            container="mp4",
                            target_bytes=int(os.path.getsize(input_path)),
                            actual_bytes=int(final_size),
                            width_hint=None,
                            fps_hint=None
                        )
                except Exception:
                    pass

                return {
                    "ok": True,
                    "output": out_file,
                    "final_size": final_size,
                }
            else:
                shutil.move(tmp_final, out_file)
                final_size = os.path.getsize(out_file)

            # === Source-as-candidate: try to BEAT the kept original on size ===
            # Passthrough kept the untouched (transparent, in-budget) source. In max
            # mode a substantial fitting source may still shrink a lot at visually
            # transparent quality — try ONE CRF encode and adopt it only if it's
            # clearly smaller (<90%), still under the ceiling, and stays transparent.
            _delivered_shrink = False
            try:
                if (_qmode == "max"
                        and final_size >= max(2 * 1024 * 1024, int(target_bytes * 0.25))
                        and bool((advanced_options or {}).get("measure_quality", True))):
                    _prev_final = int(final_size)
                    _sc_floor = float((advanced_options or {}).get("source_candidate_vmaf") or 97.0)
                    _sc_crf = int((advanced_options or {}).get("source_candidate_crf") or 22)
                    # Use an EFFICIENT codec for the shrink probe — the whole point is
                    # to undercut the source's size, so default to x265 (much smaller
                    # than x264 at equal quality) unless the user explicitly forced one.
                    _sc_enc = str((advanced_options or {}).get("source_candidate_encoder") or "x265")
                    _shrink_tmp = out_file + ".shrink.mp4"
                    if os.path.exists(_shrink_tmp):
                        os.remove(_shrink_tmp)
                    status_cb(f"[Race] Source fits ({format_bytes(_prev_final)}); testing a transparent "
                              f"{_sc_enc} CRF-{_sc_crf} re-encode to see if it shrinks with no visible loss.", "INFO")
                    _ok_sc = compress_with_handbrake(
                        input_path=input_path, output_path=_shrink_tmp,
                        encoder=_sc_enc, bitrate=None, crf=_sc_crf, two_pass=False,
                        width=None, fps=None, audio_bitrate=128_000, audio_copy=False,
                        advanced_options={**(advanced_options or {}), "audio_format": "aac"},
                    )
                    if _ok_sc and os.path.exists(_shrink_tmp):
                        _sc_size = int(os.path.getsize(_shrink_tmp))
                        _sc_smaller = (_sc_size < int(_prev_final * 0.90)) and (_sc_size <= int(target_bytes))
                        _sc_vmaf = None
                        if _sc_smaller:
                            try:
                                _scv = compute_vmaf(input_path, _shrink_tmp,
                                                    duration_s=float(extract_video_duration(input_path) or 0.0))
                                _sc_vmaf = float(_scv["vmaf"]) if _scv else None
                            except Exception:
                                _sc_vmaf = None
                        if _sc_smaller and (_sc_vmaf is None or _sc_vmaf >= _sc_floor):
                            os.replace(_shrink_tmp, out_file)
                            final_size = int(os.path.getsize(out_file))
                            _delivered_shrink = True
                            status_cb(f"[Race] Re-encode beat the original: {format_bytes(final_size)} "
                                      f"(was {format_bytes(_prev_final)})"
                                      + (f", VMAF {_sc_vmaf:.1f}" if _sc_vmaf is not None else "")
                                      + " — shipping the smaller transparent file.", "INFO")
                        else:
                            try:
                                os.remove(_shrink_tmp)
                            except Exception:
                                pass
                            status_cb(f"[Race] Kept the original - re-encode ({format_bytes(_sc_size)}"
                                      + (f", VMAF {_sc_vmaf:.1f}" if _sc_vmaf is not None else "")
                                      + f") wasn't a clear win vs {format_bytes(_prev_final)}.", "INFO")
            except Exception:
                pass

            # Only log a learning outcome when an actual encode ran (the shrink
            # race won). A pure passthrough remux never exercised the encoder's
            # rate/quality behavior, so it carries no information for the rate
            # model — and when the shrink race DID win, the file on disk was
            # produced by _sc_enc, not the originally-requested encoder; the
            # ledger/rate-model must attribute to the encoder that actually ran.
            if _delivered_shrink:
                try:
                    _container = (os.path.splitext(out_file)[1] or ".mp4").lstrip(".").lower() or "mp4"
                    _encoder   = _canonical_encoder(_sc_enc)
                    _stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
                    with _STATS_LOCK:
                        learn_from_result(_stats_dir, _encoder, _container, int(target_bytes), int(final_size),
                                          width_hint=None, fps_hint=None)
                    _jsonl_log("learned", {"encoder": _encoder, "container": _container,
                                           "target_bytes": int(target_bytes), "actual_bytes": int(final_size)})
                    _adj = guardrail_adjust(int(final_size), int(target_bytes))
                    if _adj is not None:
                        _jsonl_log("guardrail_suggest", {"scale": float(_adj)})
                except Exception:
                    pass

            stats = {
                "original_size": os.path.getsize(input_path),
                "compressed_size": final_size,
                "ceiling_exceeded": bool(int(final_size) > int(target_bytes)),
                "used_crf": None,
                "duration": None,
                "width": None,
                "height": None,
                "bitrate": None,
                "framerate": None,
                "output_path": out_file,
                "passthrough": (not _delivered_shrink),
            }
            _jsonl_log("encode_end", {"type": "video", **stats})

            if webhook_url:
                _wh_ok = _post_webhook_hardened(webhook_url, json_payload=stats, file_path=out_file)
                try:
                    from outcome_ledger import record_webhook_outcome as _ol_wh
                    _ol_wh(os.path.join(USER_SETTINGS_DIR, "stats"), input_path, _wh_ok)
                except Exception:
                    pass
            return stats
    except Exception:
        pass

    try:
        dur, w, h, br, fr = get_video_metadata(input_path)
    except Exception:
        status_cb("Failed to extract metadata; using defaults", level="WARNING")
        dur, w, h, br, fr = 10.0, 1280, 720, 5_000_000, 30.0
    if not dur or dur <= 0:
        probed = extract_video_duration(input_path)
        dur = probed if probed and probed > 0 else 10.0

    base_crf = None
    mc = str(advanced_options.get("manual_crf") or "").strip()
    if mc.isdigit():
        base_crf = int(mc)
    else:
        base_crf = DEFAULT_CRF

    # suggested_crf is only surfaced for logging on the (rare) non-bitrate path;
    # the ABR/two-pass path below drives size via bitrate, not CRF. A former
    # quick_size_estimate() probe used to nudge this CRF from a sample encode, but
    # that function was removed long ago — the call raised NameError on every run
    # and was silently swallowed, so the nudge never applied. Drop the dead probe.
    suggested_crf = base_crf

    audio_meta = _probe_audio_meta(input_path)
    ch = (audio_meta or {}).get("ch") or 2
    sr = (audio_meta or {}).get("sr") or 48000
    enc_name = (advanced_options.get("encoder") or "x264")

    # === Result cache: seed repeat jobs from prior outcomes (skips probing) ===
    # cache_store has recorded (file, target, encoder) -> (v_bps, final_size)
    # for every retried job; this is the read side that was never implemented.
    _cache_rec = None
    try:
        _t_mb_c = int(target_bytes / 1024 / 1024)
        _stats_dir_c = os.path.join(USER_SETTINGS_DIR, "stats")
        _enc_req_c = _canonical_encoder(str((advanced_options or {}).get("encoder") or "x264"))
        # cache_store records the EFFECTIVE encoder (the codec race may have
        # switched away from the requested one), so look up every plausible tag
        # and take the newest — a requested/effective key mismatch used to mean
        # the cache never hit and every repeat job re-probed and re-decided.
        if _enc_req_c in ("x264", "x265") and bool((advanced_options or {}).get("auto_codec", True)):
            _lookup_tags = list(dict.fromkeys(
                [_enc_req_c, "x264", "x265", "svt-av1", "aom-av1", "av1", "vp9"]))
        else:
            # Explicit/pinned encoder choice: only reuse results from that
            # encoder, never let a cached race winner override the request.
            _lookup_tags = [_enc_req_c]
        _cands = [cache_lookup(_stats_dir_c, input_path, _t_mb_c, _tag) for _tag in _lookup_tags]
        _cands = [r for r in _cands if isinstance(r, dict)]
        _cands.sort(key=lambda r: int(r.get("ts") or 0), reverse=True)
        for _rec in _cands:
            _fs, _vb = int(_rec.get("final_size") or 0), int(_rec.get("v_bps") or 0)
            if _vb <= 0 or _fs <= 0:
                continue
            # Only trust prior results that landed inside the no-overshoot window.
            if not (0.85 * target_bytes <= _fs <= target_bytes):
                continue
            _rw, _rf = int(_rec.get("width") or 0), float(_rec.get("fps") or 0.0)
            if _rw and w and abs(_rw - w) > 0.10 * max(_rw, w):
                continue
            if _rf and fr and abs(_rf - fr) > 0.10 * max(_rf, fr):
                continue
            _cache_rec = _rec
            break
    except Exception:
        _cache_rec = None

    if _cache_rec:
        _scale_c = max(0.90, min(1.10, float(target_bytes) / max(1.0, float(_cache_rec["final_size"]))))
        advanced_options["_skip_probe"] = True
        advanced_options["_cached_seed_v"] = int(max(80_000, _cache_rec["v_bps"] * _scale_c))
        advanced_options["_cached_encoder"] = str(_cache_rec.get("encoder") or "")
        status_cb(f"[Cache] Seeded from a prior encode of this file: {_cache_rec.get('encoder')} @ "
                  f"{advanced_options['_cached_seed_v']} bps (landed {_cache_rec['final_size']} bytes "
                  f"for the same target). Skipping probes.", "INFO")

    _out_container = (advanced_options.get("output_container") or "mp4").lower()
    _audio_fmt = _best_audio_codec(_out_container, advanced_options.get("audio_format") or "auto")

    if _should_copy_audio(target_bytes, dur, audio_meta):
        audio_copy = True

        v_bps, a_bps_suggest, ov = choose_bitrates(
            duration_s=dur,
            target_bytes=target_bytes,
            encoder=enc_name,
            container=_out_container,
            channels=ch,
            sample_rate=sr,
            audio_fmt=_audio_fmt,
            stats_dir=os.path.join(USER_SETTINGS_DIR, "stats"),
            width_hint=w,
            fps_hint=fr,
            audio_copy_bps=int((audio_meta or {}).get("bitrate") or 0),
            input_path=input_path,
            skip_probe=bool((advanced_options or {}).get("_skip_probe")),
            )
        audio_br = 0
        try:
            os.environ["BC_LAST_A_BPS"] = str(int(a_bps_suggest))
        except Exception:
            pass
    else:
        audio_copy = False
        v_bps, a_bps_suggest, ov = choose_bitrates(
            duration_s=dur,
            target_bytes=target_bytes,
            encoder=enc_name,
            container=_out_container,
            channels=ch,
            sample_rate=sr,
            audio_fmt=_audio_fmt,
            stats_dir=os.path.join(USER_SETTINGS_DIR, "stats"),
            width_hint=w,
            fps_hint=fr,
            input_path=input_path,
            skip_probe=bool((advanced_options or {}).get("_skip_probe")),
            )
        audio_br = a_bps_suggest
        try:
            os.environ["BC_LAST_A_BPS"] = str(int(audio_br))
        except Exception:
            pass
    target_bitrate = int(v_bps)
    bitrate = target_bitrate  # ensure guardrails can read a base VBR


    fps = determine_frame_rate(fr, w, dur, target_bitrate)
    tune = determine_tune_profile(w, h, input_path)
    requested_encoder = (advanced_options.get("encoder")
                         or advanced_options.get("video_encoder")
                         or advanced_options.get("codec")
                         or "x264")
    quality_encoder = _canonical_encoder(str(requested_encoder))

    # x264 tunes (film/animation/stillimage) are invalid for other encoders.
    if str(quality_encoder).lower() != "x264":
        _t = (tune or "").lower()
        if _t in ("film", "animation", "stillimage"):
            tune = None

    encoder = quality_encoder
    new_w = determine_resolution(w, h, target_bitrate, fps_hint=fps, encoder=str(encoder))
    new_h = int(round(h * (new_w / max(1.0, float(w)))))
    new_h -= new_h % 2
    # A cache hit already knows which codec won for this exact job — reuse it
    # instead of racing codecs again.
    _cached_enc = str((advanced_options or {}).get("_cached_encoder") or "").lower()
    if _cached_enc and _cached_enc != str(encoder).lower():
        status_cb(f"[Cache] Reusing prior winning codec: {_cached_enc}", "INFO")
        encoder = _cached_enc
    hwaccel = "CPU"
    if str(encoder).lower() in ("x265", "libx265", "hevc") and not (advanced_options or {}).get("preset"):
        advanced_options["preset"] = "slow"
    force_two = bool(advanced_options.get("two_pass") or advanced_options.get("two_pass_forced"))
    two_pass = _adaptive_two_pass(new_w, target_bitrate, force=force_two)
    # Default to quality-first exact-size behavior: always attempt true two-pass on software encoders.
    two_pass = True

    tmp_final = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    _feats_ctx: dict = {}

    status_cb(f"Encoding BITRATE={target_bitrate} bps (two-pass={two_pass}), width={new_w}, encoder={encoder}, hw={hwaccel}")
    status_cb(f"[Codec] Quality-first encoder policy: requested={requested_encoder} -> effective={encoder}", level="INFO")
    status_cb(f"Preflight: passlog_safe=x265:{str(encoder).lower() in ('x265','libx265','hevc')} "
              f"min_close_bytes={int((advanced_options or {}).get('min_close_bytes') or 120_000)}", level="INFO")

    try:
        feats, scale_mul, crf_bias, audio_min_kbps = analyze_and_advise(input_path)
        _feats_ctx = dict(feats or {})

        target_bitrate = int(max(140_000, min(20_000_000, target_bitrate * scale_mul)))
        bitrate = target_bitrate

        try:
            container_factor = 1.03

            _aud_bps_guess = (int((audio_meta or {}).get("bitrate") or 0) if audio_copy
                              else int(audio_br or a_bps_suggest or 128000))
            _aud_bytes = int((_aud_bps_guess / 8.0) * max(1.0, float(dur)))
            _vid_bytes = int((target_bitrate / 8.0) * max(1.0, float(dur)) * (1.0 / container_factor))
            _pred_total = int((_aud_bytes + _vid_bytes) * container_factor)
            if target_bytes and _pred_total > int(target_bytes * 0.995):
                _cap = max(0.60, min(1.00, (target_bytes / float(_pred_total)) * 0.98))
                target_bitrate = max(140_000, int(target_bitrate * _cap))
                bitrate = target_bitrate
        except Exception:
            pass

        if not audio_copy and audio_br and audio_br < audio_min_kbps*1000:
            audio_br = audio_min_kbps * 1000

        advanced_options["crf_bias_hint"] = int(crf_bias)
        try:
            advanced_options["graininess_score"] = float((_feats_ctx or {}).get("graininess", 0.0) or 0.0)
        except Exception:
            pass
        # Now that content complexity is known, re-decide the delivery resolution:
        # flat/simple content (screen recordings, UI, cartoons) should keep native
        # resolution rather than downscale (the initial call above had no features).
        try:
            _cx_now = float((_feats_ctx or {}).get("spatial_complexity", 0.0) or 0.0)
            _nw_cx = determine_resolution(w, h, target_bitrate, fps_hint=fps,
                                          encoder=str(encoder), complexity=_cx_now)
            if int(_nw_cx) != int(new_w):
                status_cb(f"[Res] Low-complexity content (cx={_cx_now:.2f}): width {new_w} -> {_nw_cx} "
                          f"(keeping detail instead of downscaling).", "INFO")
                new_w = int(_nw_cx)
                new_h = int(round(h * (new_w / max(1.0, float(w))))); new_h -= new_h % 2
        except Exception:
            pass
        status_cb(f"Heuristics: scalex{scale_mul:.2f}, crf_bias={crf_bias}, audio>={audio_min_kbps}kbps")
    except Exception as _ml_e:
        status_cb(f"ML heuristic analysis skipped: {type(_ml_e).__name__}", level="DEBUG")




    # Optional profile model: tune preset knobs from content + budget.
    try:
        _content = {
            "difficulty": float(min(1.0, max(0.0, float((_feats_ctx or {}).get("spatial_complexity", 5.0)) / 10.0))),
            "grain_sensitive": bool(float((_feats_ctx or {}).get("graininess", 0.0) or 0.0) >= 0.30),
        }
        _prof = select_profile(
            content=_content,
            encoder=str(encoder),
            width=int(new_w or w or 0),
            fps=float(fps or fr or 30.0),
            budget_kbps=float(target_bitrate) / 1000.0,
            conservative_bias=0.5,
        )
        if isinstance(_prof, dict):
            _ppreset = _prof.get("preset")
            _ptune = _prof.get("tune")
            if _ppreset and not (advanced_options or {}).get("preset"):
                advanced_options["preset"] = str(_ppreset)
            if _ptune and not (advanced_options or {}).get("tune"):
                tune = str(_ptune)
            # Content-aware AQ/psy-RD tuning: select_profile() computes this for
            # x264 but nothing downstream ever read it (only preset/tune were
            # consumed here). x264 only — x265's equivalent output is NOT wired
            # in: BitCrusherV9.py's x265 two-pass path (~1575) carries a measured
            # comment that x265 defaults beat psy-rd/aq overrides by ~2 VMAF, so
            # applying select_profile()'s x265-params here would silently revert
            # that measured result. Merged onto the existing tuned default (not
            # replaced) so mbtree/deblock/rc-lookahead/etc. are preserved.
            _pxp = _prof.get("x264-params")
            if _pxp:
                _x264_base_default = ("aq-mode=3:aq-strength=1.00:mbtree=1:deblock=-1,-1:psy-rd=1.10,0.15:"
                                       "rc-lookahead=80:qcomp=0.70:ipratio=1.30:pbratio=1.20:trellis=2:bframes=8:ref=5")
                _x264_existing = str((advanced_options or {}).get("x264_params") or "").strip() or _x264_base_default
                advanced_options["x264_params"] = _merge_params_string(_x264_existing, str(_pxp))
    except Exception as _prof_e:
        status_cb(f"Profile model skipped: {type(_prof_e).__name__}", level="DEBUG")
    manual_bps = int(target_bitrate)
    _manual_locked = False

    try:
        if advanced_options and advanced_options.get("manual_bitrate"):
            manual_bps = int(float(advanced_options["manual_bitrate"]))
            _manual_locked = True
    except Exception:
        pass
    # --bitrate / GUI manual bitrate is an explicit user override: it must win
    # over every heuristic bitrate estimate below (microprobe, planner, ledger
    # seed), same "honor explicit input" policy already applied to encoder
    # choice. The feasibility clamp further down still applies — it's a hard
    # physical constraint (can't fit more bits than the size cap allows), not
    # a heuristic guess, so an infeasible manual request is still capped/rejected.
    if _manual_locked and advanced_options is not None:
        advanced_options["_manual_override"] = True
        advanced_options["_manual_bitrate_requested"] = int(manual_bps)

    # === Microprobe: predict bit budget & choose strategy (fast) ===
    try:
        hb_path, ffprobe_path, ffmpeg_path = load_paths()
    except Exception:
        ffprobe_path = default_ffprobe()
        ffmpeg_path  = default_ffmpeg()

    _cached_seed_v = int((advanced_options or {}).get("_cached_seed_v") or 0)
    if _cached_seed_v > 0:
        # A prior encode of this exact (file, target, encoder) already told us
        # the right bitrate; synthesize a high-confidence prediction instead of
        # re-probing. The planner still refines it below.
        mpred = {"video_bps": _cached_seed_v, "crf": 24.0, "confidence": 0.9,
                 "curve_points": [], "source": "abr_cache"}
    else:
        _emit("probing")
        mpred = predict_crf_and_bitrate(
            ffmpeg=ffmpeg_path,
            ffprobe=ffprobe_path,
            path=input_path,
            target_bytes=int(target_bytes),
            duration=float(dur),
            width=int(w), height=int(h),
            fps=float(fr or 0.0),
            audio_bps=int(audio_br or 128_000),
            container_overhead=1.02,
            scale_width=int(new_w or w),
            fps_out=float(fps or fr or 0.0)
        )

    # Use the microprobe's video bitrate estimate as the starting point
    # (unless the user set an explicit manual bitrate — that wins outright).
    try:
        _mp_bps = int(max(160_000, mpred.get("video_bps", float(target_bitrate))))
    except Exception:
        _mp_bps = int(target_bitrate)
    if advanced_options is not None:
        advanced_options["_advised_v_bps"] = _mp_bps
    target_bitrate = int(manual_bps) if _manual_locked else _mp_bps


    # Optional planner integration: derive conservative seed rates + params.
    # Scene zones (per-scene bitrate allocation) apply to x264/x265 two-pass
    # encodes; the planner computes them but was never enabled (BC_SCENE_SPLIT
    # had no writer anywhere).
    _scene_zones_on = (bool((advanced_options or {}).get("scene_zones", True))
                       and str(encoder).lower() in ("x264", "libx264", "h264",
                                                    "x265", "libx265", "hevc"))
    try:
        from smart_rate import load_stats
        _stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
        _stats_map = load_stats(_stats_dir)
        _scene_hint = None
        try:
            from ml_heuristics import analyze_scenes
            _scene_hint = analyze_scenes(input_path, encoder=str(encoder), fps_hint=float(fps or fr or 0.0))
        except Exception:
            _scene_hint = None

        if _scene_zones_on:
            os.environ["BC_SCENE_SPLIT"] = "1"
        _plan = plan_encode(PlanInputs(
            target_bytes=int(target_bytes),
            duration_s=float(dur),
            encoder=str(encoder),
            container="mp4",
            width=int(new_w or w or 0),
            height=int(new_h or h or 0),
            fps=float(fps or fr or 0.0),
            audio_bps_hint=int(audio_br or a_bps_suggest or 128_000),
            probe=mpred,
            scene=_scene_hint,
            stats=_stats_map,
            settings_dir=USER_SETTINGS_DIR,
        ))

        _plan_bps = int(max(160_000, int(_plan.video_bps)))
        if advanced_options is not None:
            advanced_options["_advised_v_bps"] = _plan_bps
        target_bitrate = int(manual_bps) if _manual_locked else _plan_bps
        bitrate = int(target_bitrate)
        if not audio_copy:
            audio_br = int(max(48_000, int(_plan.audio_bps)))

        _ep = _plan.encoder_params if isinstance(_plan.encoder_params, dict) else {}
        if _ep.get("preset") and not (advanced_options or {}).get("preset"):
            advanced_options["preset"] = str(_ep.get("preset"))
        if _ep.get("tune"):
            tune = str(_ep.get("tune"))
        if _ep.get("x264_params"):
            # Merge onto encoder_profiles' AQ/psy-RD tuning (set above) instead
            # of overwriting it — the zones string and the AQ/psy params are
            # different keys in the same colon-separated x264-params value.
            _prior264 = str((advanced_options or {}).get("x264_params") or "").strip()
            _zones264 = str(_ep.get("x264_params"))
            advanced_options["x264_params"] = f"{_prior264}:{_zones264}" if _prior264 else _zones264
            os.environ["BC_X264_PARAMS"] = advanced_options["x264_params"]
        # x265 zone/params output used to be silently dropped here.
        if _ep.get("x265_params"):
            _prior265 = str((advanced_options or {}).get("x265_params") or "").strip()
            _zones265 = str(_ep.get("x265_params"))
            advanced_options["x265_params"] = f"{_prior265}:{_zones265}" if _prior265 else _zones265
            os.environ["BC_X265_PARAMS"] = advanced_options["x265_params"]
        if _scene_zones_on and (_ep.get("x264_params") or _ep.get("x265_params")):
            status_cb("[Zones] Scene-aware bitrate zones injected into encoder params.", "INFO")
    except Exception as _plan_e:
        status_cb(f"Planner skipped: {type(_plan_e).__name__}", level="DEBUG")
    finally:
        try:
            os.environ.pop("BC_SCENE_SPLIT", None)
        except Exception:
            pass

    # === Spotlight zone injection ===========================================
    # After the planner has settled encoder params, take over zoning with the
    # user's boosted range. Rate control redistributes the SAME total budget:
    # the marked range gets ~1.5x the bits, the rest pays for it.
    _spot_secs = (advanced_options or {}).get("_spotlight_secs")
    if _spot_secs:
        try:
            _spa, _spb = float(_spot_secs[0]), float(_spot_secs[1])
            _fps_eff = float(fps or fr or 30.0)
            _skey, _sval = _spotlight_zone_params(
                str((advanced_options or {}).get(
                    "x265_params" if str(encoder).lower() in ("x265", "libx265", "hevc")
                    else "x264_params") or ""),
                _spa, _spb, _fps_eff, float(dur or 0.0), str(encoder))
            advanced_options[_skey] = _sval
            os.environ["BC_X265_PARAMS" if _skey == "x265_params" else "BC_X264_PARAMS"] = _sval
            status_cb(f"[Spotlight] Boosting {_fmt_ts(_spa)}-{_fmt_ts(_spb)} "
                      f"(x{_SPOTLIGHT_BOOST:g} rate zone); the rest of the video "
                      f"carries the cost under the same cap.", "INFO")
            _jsonl_log("spotlight", {"input": input_path, "start": _spa, "end": _spb,
                                     "boost": _SPOTLIGHT_BOOST, "params_key": _skey})
        except Exception as _sz_e:
            status_cb(f"[Spotlight] Zone injection failed ({type(_sz_e).__name__}); "
                      f"encoding without the boost.", "WARNING")

    # Shared probe-reference cache: the codec race and the preproc A/B both
    # extract lossless reference clips at the same segments/delivery resolution.
    # One cache (dict + dir) lets the second stage reuse the first's refs instead
    # of re-extracting them. Keyed by (source, scale_width, segment) so a codec
    # race that changes the delivery width naturally gets its own reference.
    _probe_ref_cache: dict = {}
    _probe_ref_dir = None
    try:
        _probe_ref_dir = tempfile.mkdtemp(prefix="bc_probref_")
    except Exception:
        _probe_ref_dir = None

    # === Smart codec auto-pick (VMAF-measured) ===
    # Race the chosen codec against AV1 on a short clip at the real bit budget and
    # keep whichever gives higher VMAF-per-bit. AV1 usually wins big, but only
    # when the build/clip actually benefits — so we measure instead of assuming.
    _auto_codec = bool((advanced_options or {}).get("auto_codec", True))
    _codec_pinned = bool((advanced_options or {}).get("codec_pinned"))
    if str(encoder).lower() not in ("x264", "x265"):
        # An explicit AV1/VP9/VVC/hardware choice is honored, never raced away.
        _auto_codec = False
    if (advanced_options or {}).get("_cached_encoder"):
        _auto_codec = False  # cache already picked the winner for this job
    if str((advanced_options or {}).get("quality_mode") or "").lower() == "fast":
        _auto_codec = False  # fast mode: no codec race
    if _auto_codec:
        # Switching to AV1 only pays off when the encode itself is tractable:
        # the size-targeting pipeline runs MANY full encodes, and libaom (the
        # slow reference encoder) crawls at ~1 fps for long 1080p content.
        # Race AV1 only with a fast encoder (SVT/NVENC/QSV/AMF) or short clips.
        _race_av1 = True
        try:
            _av1_enc = best_av1_encoder()
            if not _av1_enc:
                _race_av1 = False
            elif _av1_enc == "libaom-av1" and float(dur or 0.0) > 60.0:
                status_cb("[Codec] Skipping AV1 race: only libaom available and the "
                          "clip is long — iterative size-targeting with libaom would "
                          "take hours. Install an ffmpeg build with SVT-AV1 to enable it.",
                          "INFO")
                _race_av1 = False
        except Exception:
            _race_av1 = False
    if _auto_codec and not _codec_pinned:
        try:
            _cur_tag = "x265" if str(encoder).lower() in ("x265", "libx265", "hevc") else "x264"
            # Full race: incumbent vs the other h26x vs AV1 (when tractable).
            _cands = [_cur_tag, ("x265" if _cur_tag == "x264" else "x264")]
            if _race_av1:
                _cands.append("av1")
            # Ecosystem market-share skip: don't spend time racing a codec
            # that has never once won for this content class after enough
            # history (skip_race_candidates), the incumbent always stays in
            # (it's what ships if racing is skipped outright below).
            try:
                from outcome_ledger import skip_race_candidates as _ol_skip_race
                _never_wins = _ol_skip_race(os.path.join(USER_SETTINGS_DIR, "stats"),
                                            os.environ.get("BC_CONTENT_CLASS") or None, _cands)
                _never_wins.discard(_cur_tag)
                if _never_wins:
                    status_cb(f"[Codec] Skipping race for {sorted(_never_wins)}: never won a race for "
                              f"this content class across enough prior history.", "DEBUG")
                    _cands = [c for c in _cands if c not in _never_wins]
            except Exception:
                pass
            _race_sink: dict = {}
            _winner = choose_best_codec_by_vmaf(
                input_path,
                duration_s=float(dur),
                video_bps=int(target_bitrate),
                candidates=_cands,
                scale_width=int(new_w or 0),
                incumbent=_cur_tag,
                status_cb=status_cb,
                result_sink=_race_sink,
                ref_cache=_probe_ref_cache,
                ref_cache_dir=_probe_ref_dir,
            )
            if _race_sink.get("scores"):
                advanced_options["_race_scores"] = _race_sink["scores"]
            if _winner and _winner != _cur_tag:
                status_cb(f"[Race] Auto-codec: {_winner} beat {_cur_tag} on quality-per-bit; switching.", "INFO")
                encoder = _winner
                if str(_winner).lower() != "x264":
                    # x264 tunes (film/animation/...) are invalid elsewhere.
                    _t = (tune or "").lower()
                    if _t in ("film", "animation", "stillimage"):
                        tune = None
                # The winner may tolerate a different resolution for this budget
                # (x264 needs more bits/pixel than x265; AV1 needs fewer).
                _new_w2 = determine_resolution(w, h, target_bitrate, fps_hint=fps, encoder=str(encoder),
                                               complexity=float((_feats_ctx or {}).get("spatial_complexity", 0.0) or 0.0))
                if int(_new_w2) != int(new_w):
                    status_cb(f"[Codec] Delivery width {new_w} -> {_new_w2} for {encoder}.", "INFO")
                    new_w = _new_w2
                    new_h = int(round(h * (new_w / max(1.0, float(w)))))
                    new_h -= new_h % 2
        except Exception as _ac_e:
            status_cb(f"Auto-codec skipped: {type(_ac_e).__name__}", "DEBUG")

    # === Feasibility clamp: long video + small target ===
    # Hard bitrate floors (140-160 kbps) elsewhere in the pipeline made some
    # jobs mathematically impossible (e.g. 10-minute video @ 10 MB needs
    # ~70 kbps video) — every retry hit the same floor and the output stayed
    # 45% over the ceiling. Cap the video bitrate to what actually fits.
    try:
        _aud_bps_eff = (int((audio_meta or {}).get("bitrate") or 128_000) if audio_copy
                        else int(audio_br or a_bps_suggest or 128_000))
        _feasible_v_bps = int((float(target_bytes) * 8.0 / max(1.0, float(dur)) - _aud_bps_eff) * 0.94)
    except Exception:
        _feasible_v_bps = 10_000_000
    # Floor at HALF the feasible rate: the controller must be able to go BELOW
    # the seed to correct encoder overshoot (rate control is sloppy at low bps).
    _v_floor = int(max(24_000, min(140_000, _feasible_v_bps // 2)))
    # Hard infeasibility: once _feasible_v_bps drops below 48 kbps, _v_floor's
    # own 24 kbps minimum is forced ABOVE what actually fits the target, so
    # every retry lands on the same floor and overshoots identically — no
    # amount of retrying or downscaling changes an absolute bitrate floor.
    # Shared by CLI and GUI since this runs inside compress_video() itself.
    # Threshold matches _v_floor's own clamp (max(24_000, ...)): below 24_000,
    # the floor is forced ABOVE what fits. 48_000 was 2x too conservative and
    # false-positive-rejected feasible jobs in [24_000, 48_000).
    if _feasible_v_bps < 24_000:
        _ledger_log_failure(input_path, _feats_ctx, "budget", "budget_infeasible",
                            f"Target {target_bytes} bytes infeasible for {dur:.1f}s of video")
        raise RuntimeError(
            f"[Budget] Target {target_bytes} bytes is infeasible for {dur:.1f}s of video: "
            f"even at the minimum viable bitrate (~{_v_floor} bps) the output cannot fit under "
            f"the target. Raise the target size, shorten the clip, or lower audio bitrate.")
    if 0 < _feasible_v_bps < int(target_bitrate):
        status_cb(f"[Budget] Small target for this duration: capping video to "
                  f"~{max(24_000, _feasible_v_bps)//1000} kbps so the size cap is reachable"
                  + (" (quality will be limited)." if _feasible_v_bps < 100_000 else "."),
                  "WARNING")
        target_bitrate = int(max(24_000, _feasible_v_bps))
        bitrate = int(target_bitrate)

    # Decide two-pass adaptively; enable turbo for the first pass to save time
    two_pass = _adaptive_two_pass(int(new_w or w), int(target_bitrate))
    turbo_two_pass = bool(two_pass)
    _under_abs = int((advanced_options or {}).get("min_close_bytes") or max(120_000, int(target_bytes * 0.02)))
    early_guard = (int(target_bytes), _under_abs)

    # === Artifact/texture-aware preprocessing (VMAF-validated) ===
    # Now that encoder / bitrate / delivery width are FINAL, decide whether a
    # prefilter chain (deband/deblock/denoise) improves quality-per-bit at this
    # exact operating point. Validated on probe segments — nothing ships unless
    # it measurably beats the unfiltered encode. The kept chain rides in
    # advanced_options so every retry/refine/packing pass uses it consistently.
    advanced_options.pop("preproc_vf", None)
    _smart_pre = bool((advanced_options or {}).get(
        "smart_preproc", ADVANCED_DEFAULTS.get("smart_preproc", True)))
    if _smart_pre and _qmode != "fast" and float(dur or 0.0) >= 3.0:
        try:
            _pre_chain, _pre_info = decide_preprocessing(
                input_path, _feats_ctx,
                encoder=str(encoder), video_bps=int(target_bitrate),
                scale_width=int(new_w or 0), width=int(w or 0), height=int(h or 0),
                fps=float(fps or fr or 30.0), duration_s=float(dur or 0.0),
                advanced_options=advanced_options,
                status_cb=status_cb, cancel_cb=cancel_cb,
                ref_cache=_probe_ref_cache, ref_cache_dir=_probe_ref_dir)
            if _pre_chain:
                advanced_options["preproc_vf"] = _pre_chain
                advanced_options["_preproc_label"] = str(_pre_info.get("kept") or "")
            _jsonl_log("preproc_decision", {
                "input": input_path, "kept": _pre_info.get("kept"),
                "candidates": _pre_info.get("candidates"),
                "scores": {k: (round(v, 2) if isinstance(v, (int, float)) else v)
                           for k, v in (_pre_info.get("scores") or {}).items()
                           if k != "_sizes"},
            })
        except Exception as _pp_e:
            status_cb(f"[Preproc] Skipped: {type(_pp_e).__name__}", "DEBUG")

    # === Film-grain synthesis decision (AV1 only, measured) =================
    # Grain is incompressible: at a fixed size cap it steals bits from the real
    # signal. SVT/libaom AV1 can strip it before encoding and re-synthesize it
    # from a model on playback — the bits go to the picture, the grain comes back
    # for free. Decide by MEASUREMENT (a probe that compares denoise-on vs -off
    # size), not the graininess feature (which doesn't track real grain). Skipped
    # when the encoder isn't AV1, in fast mode, or when the preproc chain already
    # denoises (don't strip grain twice). NOTE: VMAF reads a grain-denoised encode
    # slightly lower (it counts grain as detail), so the win shows as picture
    # quality at size, not as a higher VMAF number — perceptual validation is the
    # planned SSIMULACRA2 work.
    advanced_options.pop("_film_grain", None)
    _fg_mode = str((advanced_options or {}).get(
        "film_grain", ADVANCED_DEFAULTS.get("film_grain", "auto"))).lower()
    _enc_is_av1 = ("av1" in str(encoder).lower()
                   and best_av1_encoder() in ("libsvtav1", "libaom-av1"))
    _pre_denoises = "denoise" in str((advanced_options or {}).get("_preproc_label") or "").lower()
    if _enc_is_av1 and _fg_mode != "off" and _qmode != "fast" and not _pre_denoises:
        try:
            if _fg_mode == "force":
                advanced_options["_film_grain"] = {"level": 14, "size_ratio": None}
                status_cb("[Grain] Film-grain synthesis forced (level 14).", "INFO")
            else:
                _fg = _probe_film_grain(
                    input_path, scale_width=int(new_w or 0),
                    duration_s=float(dur or 0.0), cancel_cb=cancel_cb, status_cb=status_cb)
                if _fg:
                    advanced_options["_film_grain"] = _fg
                    status_cb(
                        f"[Grain] Grainy source (stripping grain saves "
                        f"{(1 - _fg['size_ratio']) * 100:.0f}% on the probe); synthesizing "
                        f"film grain at level {_fg['level']} — bits go to the picture, grain "
                        f"is re-added on playback.", "INFO")
        except Exception as _fg_e:
            status_cb(f"[Grain] Probe skipped: {type(_fg_e).__name__}", "DEBUG")

    # === Outcome-ledger prediction (learning stage 2a: LIVE) ================
    # Predict this content's first-attempt size deviation from past outcomes
    # and seed the first attempt with it. Promoted from shadow mode after the
    # cold-start batch measured it beating the baseline (mean error 4.5% vs
    # 6.9%, within-5% 72% vs 50%). Guardrails: needs >=3 similar encodes, the
    # correction is clamped, upward corrections respect the feasibility cap,
    # and the size controller still corrects every later attempt. Predictions
    # keep being logged against reality, so shadow_report stays a permanent
    # self-audit; disable with learned_seed=False / --no-learned-seed.
    _ol_dev, _ol_n = 1.0, 0  # safe defaults if the predictor errors before assigning below
    try:
        from outcome_ledger import predict_deviation as _ol_predict, seed_adjust as _ol_seed
        _ol_stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
        # Operating-point flags for neighbour matching: film-grain synthesis,
        # preprocessing and spotlight change size/quality materially, so they
        # steer which past encodes count as comparable. All resolved above.
        _ol_flags = {
            "film_grain": ((advanced_options or {}).get("_film_grain") or {}).get("level"),
            "preproc": (advanced_options or {}).get("_preproc_label"),
            "spotlight": bool((advanced_options or {}).get("_spotlight_secs")),
        }
        _ol_dev, _ol_n = _ol_predict(
            _ol_stats_dir, _feats_ctx or {}, str(encoder),
            int(new_w or w or 0), int(new_h or h or 0),
            float(fps or fr or 30.0), float(target_bitrate), op_flags=_ol_flags)
        _ol_acted = False
        _ol_seed_on = bool((advanced_options or {}).get(
            "learned_seed", ADVANCED_DEFAULTS.get("learned_seed", True))) and not _manual_locked
        if _ol_seed_on:
            _ol_new_bps, _ol_acted = _ol_seed(
                float(target_bitrate), _ol_dev, _ol_n,
                cap_bps=(float(_feasible_v_bps) if _feasible_v_bps and _feasible_v_bps > 0 else None))
            if _ol_acted:
                status_cb(f"[Ledger] First-attempt bitrate seeded {target_bitrate} -> "
                          f"{_ol_new_bps} bps (learned deviation x{_ol_dev:.3f} from "
                          f"{_ol_n} similar encodes).", "INFO")
                target_bitrate = int(_ol_new_bps)
                bitrate = int(target_bitrate)
        advanced_options["_ledger_shadow"] = {"dev_pred": _ol_dev, "n": int(_ol_n),
                                              "acted": bool(_ol_acted)}
        if _ol_n > 0 and not _ol_acted:
            status_cb(f"[Ledger] Prediction x{_ol_dev:.3f} from {_ol_n} similar encodes "
                      f"(below act threshold or negligible).", "DEBUG")
    except Exception:
        advanced_options["_ledger_shadow"] = None

    # === Pre-flight guardrail (learning stage 2b: ADVISORY) =================
    # With the encoder, bitrate and delivery resolution now final, ask the
    # ledger how this content/codec has fared before at a comparable operating
    # point. Two signals, both advisory (this NEVER changes the encode on its
    # own — the codec race already MEASURES when it can; prediction only speaks
    # up on race-skipped paths and honours an explicit/pinned encoder):
    #   - warn when the worst-scene quality is predicted to collapse, or the
    #     content/encoder historically overshoots the size cap (the old-film-at-
    #     3MB failure mode the cold-start batch surfaced);
    #   - suggest a better-scoring codec when the encoder is free to change and
    #     no race ran to settle it.
    if bool((advanced_options or {}).get(
            "preflight_advice", ADVANCED_DEFAULTS.get("preflight_advice", True))):
        try:
            from outcome_ledger import preflight_advice as _ol_advice
            _race_ran = bool((advanced_options or {}).get("_race_scores"))
            _enc_locked = bool(_codec_pinned or _race_ran
                               or str(encoder).lower() not in ("x264", "x265", "libx265", "hevc"))
            _pf_model = resolve_vmaf_model() or "version=vmaf_v0.6.1"
            _pf_flags = {
                "film_grain": ((advanced_options or {}).get("_film_grain") or {}).get("level"),
                "preproc": (advanced_options or {}).get("_preproc_label"),
                "spotlight": bool((advanced_options or {}).get("_spotlight_secs")),
            }
            _adv = _ol_advice(
                os.path.join(USER_SETTINGS_DIR, "stats"), _feats_ctx or {},
                str(encoder), int(new_w or w or 0), int(new_h or h or 0),
                float(fps or fr or 30.0), float(target_bitrate),
                float(target_bytes), candidates=["x264", "x265", "av1"],
                vmaf_model=_pf_model, encoder_locked=_enc_locked, op_flags=_pf_flags)
            for _w in _adv.get("warnings", []):
                status_cb(f"[Preflight] {_w}", "WARNING")
            if _adv.get("codec_suggestion"):
                _cs = _adv["codec_suggestion"]
                _csw = (_adv.get("scores", {}).get(_cs) or {}).get("worst")
                status_cb(
                    f"[Preflight] History favours {_cs} for this content at this size"
                    + (f" (predicted worst-scene VMAF ~{_csw:.0f})" if _csw is not None else "")
                    + f"; current encoder is {encoder}. Enable auto-codec or pick {_cs} to use it.",
                    "INFO")
            advanced_options["_preflight"] = {
                "warnings": _adv.get("warnings", []),
                "codec_suggestion": _adv.get("codec_suggestion"),
                "chosen": _adv.get("chosen"), "scores": _adv.get("scores")}
        except Exception:
            advanced_options["_preflight"] = None

    # === Shared two-pass pass-log for the whole size-convergence loop ========
    # Every two-pass encode below (initial, seed, refine, retries, downscale,
    # packing, quality-floor) shares one pass-1 analysis per encode signature
    # (codec/resolution/preset/tune/filters/fps) — only the target bitrate
    # differs between them, and pass-1 stats are bitrate-independent. This keys
    # the reuse; a resolution/filter change transparently forces a fresh pass 1.
    # Roughly halves the loop's total encode time on slow presets (x265 "slow").
    _pl_reuse_on = bool((advanced_options or {}).get(
        "twopass_passlog_reuse", ADVANCED_DEFAULTS.get("twopass_passlog_reuse", True)))
    # Bench/debug override: BC_TWOPASS_PL_REUSE=0 forces the old behaviour (a
    # fresh pass 1 on every retry) so the reuse speedup can be A/B measured.
    _pl_env = os.environ.get("BC_TWOPASS_PL_REUSE")
    if _pl_env is not None and str(_pl_env).strip().lower() in ("0", "false", "no", "off"):
        _pl_reuse_on = False
    _pl_shared_dir = None
    if _pl_reuse_on:
        try:
            _pl_shared_dir = tempfile.mkdtemp(prefix="bc_2ppl_")
            advanced_options["_twopass_passlog_dir"] = _pl_shared_dir
            advanced_options["_twopass_reuse_stats"] = True
        except Exception:
            _pl_shared_dir = None

    # === Encode ===
    ok = False
    seeded_from_bitrate_observation = False
    # Prefer real two-pass when supported
    if two_pass and _supports_true_two_pass(encoder):
        status_cb("Using native ffmpeg two-pass.", level="INFO")
        status_cb(f"Two-pass seed -> v_bitrate={int(target_bitrate)} a_bitrate={int(audio_br or 0)} width={new_w} fps={fps} tune={tune} enc_preset={(advanced_options or {}).get('preset','medium')}", level="INFO")
        ok = _ffmpeg_two_pass_encode(
            input_path=input_path,
            output_path=tmp_final,
            encoder=encoder,
            bitrate=int(target_bitrate),
            width=new_w,
            fps=fps,
            tune=tune,
            audio_bitrate=audio_br,
            audio_copy=audio_copy,
            preset=str((advanced_options or {}).get("preset", ("slow" if str(encoder).lower() in ("x265","libx265","hevc") else "medium"))),
            turbo=turbo_two_pass,
            duration_s=float(dur or 0.0),
            progress_cb=(advanced_options or {}).get("progress_cb"),
            job_id=(advanced_options or {}).get("job_id"),
            advanced_options=advanced_options,
        )
        seeded_from_bitrate_observation = bool(ok)
    else:
        ok = compress_with_handbrake(
            input_path=input_path,
            output_path=tmp_final,
            audio_bitrate=audio_br,
            encoder=encoder,
            crf=None,                              # bitrate mode
            bitrate=int(target_bitrate),
            width=new_w,
            fps=fps,
            tune=tune,
            two_pass=False,                        # handled above when supported
            hwaccel=hwaccel,
            audio_copy=audio_copy,
            early_abort_guard=early_guard,
            turbo=False,
            advanced_options={**(advanced_options or {}), "duration_s": float(dur)},
        )
        seeded_from_bitrate_observation = bool(ok)

    if not ok:
        # Fallback 1: retry bitrate path without early-abort.
        status_cb("[Encode] Primary path failed; retrying bitrate path without early-abort.", level="WARNING")
        ok = compress_with_handbrake(
            input_path=input_path,
            output_path=tmp_final,
            audio_bitrate=audio_br,
            encoder=encoder,
            crf=None,
            bitrate=int(target_bitrate),
            width=new_w,
            fps=fps,
            tune=tune,
            two_pass=False,
            hwaccel=hwaccel,
            audio_copy=audio_copy,
            early_abort_guard=None,
            turbo=False,
            advanced_options={**(advanced_options or {}), "duration_s": float(dur)},
        )
        seeded_from_bitrate_observation = bool(ok)

    crf_guess = int(max(14, min(34, round(float(mpred.get("crf", 24.0))))))

    if not ok:
        # Fallback 2: force software bitrate path (size-target first).
        status_cb(f"[Encode] Primary bitrate path failed; retrying software {encoder} bitrate mode.", level="WARNING")
        ok = compress_with_handbrake(
            input_path=input_path,
            output_path=tmp_final,
            audio_bitrate=audio_br,
            encoder=encoder,
            crf=None,
            bitrate=int(target_bitrate),
            width=new_w,
            fps=fps,
            tune=tune,
            two_pass=True,
            hwaccel=hwaccel,
            audio_copy=audio_copy,
            early_abort_guard=None,
            turbo=False,
            advanced_options={**(advanced_options or {}), "duration_s": float(dur), "preset": (advanced_options or {}).get("preset", "slow")},
        )
        seeded_from_bitrate_observation = bool(ok)

    if not ok:
        # Fallback 3: HandBrakeCLI bitrate mode (size-target first).
        status_cb("[Encode] Software bitrate path failed; trying HandBrakeCLI bitrate next.", level="WARNING")
        ok = _handbrake_encode(
            input_path=input_path,
            output_path=tmp_final,
            encoder=encoder,
            bitrate=int(target_bitrate),
            crf=None,
            width=new_w,
            fps=fps,
            audio_bitrate=audio_br,
            audio_copy=audio_copy,
            two_pass=True,
            turbo=False,
        )
        seeded_from_bitrate_observation = bool(ok)

    if not ok:
        # Fallback 4: single-pass CRF (last resort when bitrate paths fail).
        status_cb(f"[Encode] Bitrate-target fallbacks failed; trying last-resort CRF={crf_guess}.", level="WARNING")
        ok = compress_with_handbrake(
            input_path=input_path,
            output_path=tmp_final,
            audio_bitrate=audio_br,
            encoder=encoder,
            crf=crf_guess,
            bitrate=None,
            width=new_w,
            fps=fps,
            tune=tune,
            two_pass=False,
            hwaccel=hwaccel,
            audio_copy=audio_copy,
            early_abort_guard=None,
            turbo=False,
            advanced_options={**(advanced_options or {}), "duration_s": float(dur)},
        )
        seeded_from_bitrate_observation = False

    if not ok:
        # Fallback 5: ultra-minimal ffmpeg emergency.
        status_cb("[Encode] CRF fallback failed; falling back to direct minimal ffmpeg encode.", level="WARNING")
        ok = _ffmpeg_emergency_encode(
            input_path=input_path,
            output_path=tmp_final,
            encoder=encoder,
            bitrate=None,
            crf=crf_guess,
            width=new_w,
            fps=fps,
            audio_bitrate=audio_br,
            audio_copy=audio_copy,
        )
        seeded_from_bitrate_observation = False
        if not ok:
            _ledger_log_failure(input_path, _feats_ctx, "encode", "encode_chain_exhausted",
                                "Encode failed (bitrate + software fallback + CRF + ffmpeg emergency)")
            raise RuntimeError("Encode failed (bitrate + software fallback + CRF + ffmpeg emergency)")




    out_file = _build_output_path("video", input_path, save_path, advanced_options, default_ext="mp4")
    # Belt-and-suspenders: never move the fresh encode onto the source file.
    # _build_output_path already disambiguates, but any future caller that
    # hands us a colliding path would otherwise destroy the original AND make
    # the VMAF stage compare the file against itself.
    if os.path.normcase(os.path.abspath(out_file)) == os.path.normcase(os.path.abspath(input_path)):
        _stem = Path(input_path).stem
        _ext = os.path.splitext(out_file)[1] or ".mp4"
        out_file = os.path.join(os.path.dirname(out_file) or ".", f"{_stem}_compressed{_ext}")
        status_cb("[Output] Target path matched the source; writing to "
                  f"'{os.path.basename(out_file)}' to protect the original.", "WARNING")
    shutil.move(tmp_final, out_file)

    final_size = os.path.getsize(out_file)
    # live size guardrail + one-shot refine for undershoot
    from smart_rate import guardrail_adjust

    # 1) Live guardrail: NEVER raise the hard target above the user request
    hard_target_bytes = int(target_bytes)
    soft_target_bytes = int(target_bytes)
    used_seed_v = int(bitrate or target_bitrate)
    # Bitrate that produced the CURRENTLY KEPT file (used by the result cache
    # and the Max-Quality packing search). Previously only refine updated it.
    accepted_v_bps = int(used_seed_v)

    # Per-attempt ledger trail: every bitrate/resolution attempt across the
    # whole size-targeting pipeline (primary, seed-calibration, refine, retry
    # loop, downscale, packing), with an accept/reject reason. Built as its
    # own explicit list rather than threaded through SizeController._obs so
    # the tuned retry-loop internals stay untouched — this list is purely
    # additive bookkeeping for the ledger.
    _ledger_attempts: list = [(int(used_seed_v), int(final_size), True, "primary")]

    # Wall-clock budget: improvement passes (refine/retry/pack) stop once the
    # job has run this long; whatever valid output exists is kept. Guards
    # against pathological cases (e.g. libaom on long content).
    try:
        _job_budget_s = float((advanced_options or {}).get("max_job_seconds",
                              ADVANCED_DEFAULTS.get("max_job_seconds", 5400)) or 0.0)
    except Exception:
        _job_budget_s = 5400.0

    def _over_budget(label: str) -> bool:
        if _job_budget_s <= 0:
            return False
        elapsed = time.time() - t_start
        if elapsed > _job_budget_s:
            status_cb(f"[Budget] {label} skipped: job has run {elapsed/60.0:.0f} min "
                      f"(budget {_job_budget_s/60.0:.0f} min). Keeping current output.", "WARNING")
            return True
        return False
    _adj = guardrail_adjust(final_size, hard_target_bytes, tol=0.005)
    if _adj and final_size > hard_target_bytes:
        # only shrink a soft budget on overshoot; do not inflate target on undershoot
        soft_target_bytes = max(int(hard_target_bytes * _adj), int(hard_target_bytes * 0.96))
    else:
        soft_target_bytes = hard_target_bytes

    _tol_pct = float((advanced_options or {}).get("target_tolerance_pct") or float(ADVANCED_DEFAULTS.get("target_tolerance_pct", 0.80)))
    _tol_min = int((advanced_options or {}).get("target_tolerance_min_bytes") or int((advanced_options or {}).get("min_close_bytes") or ADVANCED_DEFAULTS.get("target_tolerance_min_bytes", 120000)))
    _near_tol = max(int(hard_target_bytes * (_tol_pct / 100.0)), int(_tol_min))

    def _within_near_target(actual_bytes: int) -> bool:
        # The user's target is a hard ceiling: accept only outputs at or under
        # the target that land within the near window below it. (A symmetric
        # window used to accept files up to _near_tol bytes OVER the target.)
        a = int(actual_bytes)
        return a <= int(hard_target_bytes) and (int(hard_target_bytes) - a) <= int(_near_tol)

    def _is_better_size(new_bytes: int, old_bytes: int) -> bool:
        # Prefer under-ceiling results; among under-ceiling, larger (closer to
        # target) wins; among overshoots, the smaller overshoot wins.
        hard = int(hard_target_bytes)
        new_b, old_b = int(new_bytes), int(old_bytes)
        if (new_b <= hard) != (old_b <= hard):
            return new_b <= hard
        return (new_b > old_b) if new_b <= hard else (new_b < old_b)






    # Ensure the size controller is seeded with a bitrate-based observation.
    if not seeded_from_bitrate_observation and (bitrate or target_bitrate):
        status_cb("[Seed] Initial output came from non-bitrate fallback; running one bitrate calibration pass.", "INFO")
        cal_tmp = out_file + ".seed.mp4"
        if os.path.exists(cal_tmp):
            os.remove(cal_tmp)
        _ok_seed = compress_with_handbrake(
            input_path=input_path,
            output_path=cal_tmp,
            encoder=encoder,
            bitrate=int(used_seed_v),
            crf=None,
            two_pass=True,
            width=new_w,
            fps=fps,
            audio_bitrate=(None if audio_copy else int(max(64_000, (audio_br or 128_000)))),
            audio_copy=audio_copy,
            tune=tune,
            hwaccel=hwaccel,
            advanced_options={**(advanced_options or {}), "duration_s": float(dur)},
        )
        if _ok_seed and os.path.exists(cal_tmp):
            os.replace(cal_tmp, out_file)
            final_size = os.path.getsize(out_file)
            seeded_from_bitrate_observation = True
            _ledger_attempts.append((int(used_seed_v), int(final_size), True, "seed_calibration"))
        else:
            status_cb("[Seed] Bitrate calibration pass failed; continuing with best available output.", "WARNING")
    # 2) One-shot refine pass if we undershot by >1% (aims at 99% of the
    #    ceiling, not 100%, so the refine itself doesn't overshoot the target)
    if (final_size < int(hard_target_bytes * 0.99) and (bitrate or target_bitrate)
            and not _over_budget("Refine")):
        scale = max(1.005, min(1.22, (float(hard_target_bytes) * 0.99) / max(1.0, float(final_size))))
        new_v_bitrate  = max(int(_v_floor), int((bitrate or target_bitrate) * scale))
        new_a_bitrate  = None if audio_copy else int(max(64_000, (audio_br or 128_000)))
        status_cb(f"[Refine] Undershot target ({final_size} < {target_bytes}). Re-encode with {new_v_bitrate} bps.", "INFO")
        _emit("refining")

        retry_tmp = out_file + ".refine.mp4"
        if os.path.exists(retry_tmp):
            os.remove(retry_tmp)

        _ok_refine = compress_with_handbrake(
            input_path=input_path,
            output_path=retry_tmp,
            encoder=encoder,
            bitrate=new_v_bitrate,
            crf=None,              # true bitrate refine
            two_pass=True,         # tighter allocation on refine
            turbo=True,            # pass 1 one preset notch faster; pass 2 full quality
            width=new_w,
            fps=fps,
            tune=tune,             # refine used to DROP the tune, so its size/quality
            hwaccel=hwaccel,       # wasn't comparable with the seed/retry encodes
            audio_bitrate=new_a_bitrate,
            audio_copy=audio_copy,
            advanced_options={**(advanced_options or {}), "duration_s": float(dur)},
        )
        if _ok_refine and os.path.exists(retry_tmp):
            _refine_size = os.path.getsize(retry_tmp)
            _refine_accepted = _is_better_size(_refine_size, final_size)
            _ledger_attempts.append((int(new_v_bitrate), int(_refine_size), bool(_refine_accepted),
                                     "refine" if _refine_accepted else "refine_worse_than_best"))
            if _refine_accepted:
                os.replace(retry_tmp, out_file)
                final_size = _refine_size
                used_seed_v = int(new_v_bitrate)
                accepted_v_bps = int(new_v_bitrate)
            else:
                # The refine landed over the ceiling (or otherwise worse) —
                # never replace a good under-target file with an overshoot.
                try:
                    os.remove(retry_tmp)
                except Exception:
                    pass
                status_cb(f"[Refine] Re-encode produced {_refine_size} bytes (worse vs ceiling {hard_target_bytes}); keeping previous output.", "INFO")
        else:
            status_cb("[Refine] Re-encode attempt did not produce a usable file; keeping previous output.", "WARNING")
    # === Size control: bounded, monotone retries (no overshoot) ===
    # Build a controller seeded from the first encode result
    try:
        aud_bps_guess = (int((audio_meta or {}).get("bitrate") or 0)
                         if audio_copy else int(audio_br or 128_000))
    except Exception:
        aud_bps_guess = int(audio_br or 128_000)

    _qmode_ctl = str((advanced_options or {}).get("quality_mode") or "max").lower()
    _max_attempts_ctl = int((advanced_options or {}).get("iterative_max_attempts")
                            or (3 if _qmode_ctl == "fast" else max(7, ITERATIVE_MAX_ATTEMPTS)))

    # Warm-start the controller's bytes-per-bit search window from the ledger's
    # own size-deviation prediction (_ol_dev/_ol_n, already computed above)
    # instead of always starting from the same cold [0.35, 1.80] range. This is
    # a DIFFERENT lever than the existing live bitrate seed (_ol_seed): that one
    # picks the first attempt's bitrate; this narrows the controller's internal
    # k-estimate bounds so fewer retries are needed to bracket a good value,
    # even when it's already seeded well. Same >=3-neighbor trust gate as the
    # rest of the ledger's live-acting predictions (seed_adjust's min_n=3).
    _k_low_ctl, _k_high_ctl = 0.55, 1.25  # SizeController's own cold-start defaults
    if _ol_n >= 3 and _ol_dev:
        try:
            _aud_frac = float(aud_bps_guess) / max(1.0, float(used_seed_v))
            _k_prior = max(0.35, min(1.80, float(_ol_dev) * (1.0 + _aud_frac) - _aud_frac))
            _k_low_ctl = max(0.35, _k_prior * 0.85)
            _k_high_ctl = min(1.80, _k_prior * 1.15)
        except Exception:
            _k_low_ctl, _k_high_ctl = 0.55, 1.25

    controller = SizeController(
        _k_low=_k_low_ctl, _k_high=_k_high_ctl,
        # soft_target_bytes (tightened by guardrail_adjust after a same-run
        # overshoot, always <= hard_target_bytes) so the controller's own
        # bisection aims a bit under the ceiling next time instead of repeating
        # the same overshoot. Final accept/reject below still checks the real
        # hard_target_bytes independently, so this only tightens aim, never
        # loosens the ceiling.
        target_bytes=int(soft_target_bytes),
        duration_s=float(dur),
        audio_bps=int(aud_bps_guess),
        container_overhead=1.02,
        min_v_bitrate=int(_v_floor),
        safety=0.995,
        max_iter=_max_attempts_ctl,
        close_tol=0.0,
        min_close_bytes=int((advanced_options or {}).get("min_close_bytes") or 120_000),
        quality_mode=("quality_first" if _qmode_ctl != "fast" else "balanced"),
        target_policy="no_overshoot_near_max",
        target_tolerance_pct=float((advanced_options or {}).get("target_tolerance_pct") or float(ADVANCED_DEFAULTS.get("target_tolerance_pct", 0.80))),
        target_tolerance_min_bytes=int((advanced_options or {}).get("target_tolerance_min_bytes") or int((advanced_options or {}).get("min_close_bytes") or ADVANCED_DEFAULTS.get("target_tolerance_min_bytes", 120000))),
        max_target_attempts=_max_attempts_ctl,
    )

    controller.set_initial(int(used_seed_v), int(final_size))

    attempt = 0
    last_attempt_size = int(final_size)
    while (not _within_near_target(int(final_size))) and controller.should_retry(actual_bytes=int(last_attempt_size)):
        if _over_budget("Retry"):
            break
        attempt += 1
        new_v_bitrate, new_a_bitrate = controller.next(int(last_attempt_size))
        _emit("retrying", attempt=attempt)
        status_cb(f"[Size] Best so far {final_size} vs ceiling {hard_target_bytes} "
                  f"(last attempt {last_attempt_size}). "
                  f"Retry {attempt}/{controller.max_iter} at {new_v_bitrate} bps.", "WARNING")

        retry_tmp = out_file + ".retry.mp4"
        if os.path.exists(retry_tmp):
            os.remove(retry_tmp)

        _ok_retry = compress_with_handbrake(
            input_path=input_path,
            output_path=retry_tmp,
            encoder=encoder,
            bitrate=int(new_v_bitrate),
            crf=None,                  # true bitrate retry
            two_pass=True,             # force real 2-pass for tight size on retries
            turbo=True,                # fast pass 1 on retries (stats stay compatible)
            early_abort_guard=None,    # never early-abort retries
            hwaccel=hwaccel,
            width=new_w,
            fps=fps,
            audio_bitrate=(None if audio_copy else int(new_a_bitrate)),
            audio_copy=audio_copy,
            tune=tune,                 # (already sanitized above for x265)
            advanced_options={**(advanced_options or {}), "duration_s": float(dur)},
        )


        if _ok_retry and os.path.exists(retry_tmp):
            last_attempt_size = os.path.getsize(retry_tmp)
            _retry_accepted = _is_better_size(last_attempt_size, final_size)
            _retry_reason = ("retry" if _retry_accepted
                             else ("retry_over_ceiling" if last_attempt_size > hard_target_bytes
                                   else "retry_worse_than_best"))
            _ledger_attempts.append((int(new_v_bitrate), int(last_attempt_size),
                                     bool(_retry_accepted), _retry_reason))
            if _retry_accepted:
                os.replace(retry_tmp, out_file)
                final_size = int(last_attempt_size)
                accepted_v_bps = int(new_v_bitrate)
            else:
                # Keep the better previous file; the attempt still feeds the
                # controller so the next bitrate estimate improves.
                try:
                    os.remove(retry_tmp)
                except Exception:
                    pass
                status_cb(f"[Retry] Attempt landed {last_attempt_size} bytes (worse vs ceiling {hard_target_bytes}); keeping previous output.", "INFO")
            if _within_near_target(int(final_size)):
                status_cb(f"[Retry] Near target reached ({final_size} vs ceiling {hard_target_bytes}, window={_near_tol} bytes under). Stopping retries.", "INFO")
                break
        else:
            status_cb("[Retry] Attempt did not produce a usable file; stopping further retries.", "WARNING")
            break

        # lightweight learning + cache
        try:
            stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
        except Exception:
            stats_dir = os.path.join(os.getcwd(), "user_settings", "stats")
        try:
            with _STATS_LOCK:
                learn_from_result(stats_dir,
                                  encoder=(encoder or "x264"),
                                  container="mp4",
                                  target_bytes=int(target_bytes),
                                  actual_bytes=int(final_size),
                                  width_hint=int(new_w or w or 0),
                                  fps_hint=float(fps or fr or 0.0),
                                  klass_hint=(os.environ.get("BC_CONTENT_CLASS") or None))
        except Exception:
            pass
        try:
            with _STATS_LOCK:
                cache_store(stats_dir, input_path, int(target_bytes/1024/1024), (encoder or "x264"),
                            int(new_v_bitrate), int(new_w or w or 0), float(fps or fr or 0.0), int(final_size))
        except Exception:
            pass

    # === Last-resort ceiling guard: downscale-and-retry ====================
    # Every bitrate at the current resolution was tried and the best result is
    # STILL over the ceiling (rate control can overshoot a very low target at a
    # high resolution — the encoder cannot compress that much detail that far).
    # Stepping the resolution down lets rate control actually reach the feasible
    # bitrate, honouring the never-over invariant instead of shipping an
    # oversized file. Toggle-gated, budget-bounded; only a genuinely-under
    # result is ever kept (via _is_better_size), and new_w/new_h are updated so
    # the ledger records the EFFECTIVE resolution.
    _downscale_on = bool((advanced_options or {}).get(
        "ceiling_downscale_retry", ADVANCED_DEFAULTS.get("ceiling_downscale_retry", True)))
    if (int(final_size) > int(hard_target_bytes) and _downscale_on
            and (bitrate or target_bitrate)):

        def _ds_try(cand_w, cand_h, v_bps, tag):
            """Encode at a downscaled resolution; keep only if it beats the best
            result under the ceiling. Returns the produced size, or None."""
            nonlocal final_size, accepted_v_bps, new_w, new_h
            _tmp = out_file + f".{tag}.mp4"
            if os.path.exists(_tmp):
                try: os.remove(_tmp)
                except Exception: pass
            _ok = compress_with_handbrake(
                input_path=input_path, output_path=_tmp, encoder=encoder,
                bitrate=int(v_bps), crf=None, two_pass=True, turbo=True,
                early_abort_guard=None, hwaccel=hwaccel, width=int(cand_w), fps=fps,
                audio_bitrate=(None if audio_copy else int(aud_bps_guess)),
                audio_copy=audio_copy, tune=tune,
                advanced_options={**(advanced_options or {}), "duration_s": float(dur)})
            if not (_ok and os.path.exists(_tmp)):
                return None
            _sz = os.path.getsize(_tmp)
            _ds_accepted = _is_better_size(_sz, final_size)
            _ledger_attempts.append((int(v_bps), int(_sz), bool(_ds_accepted),
                                     "downscale" if _ds_accepted else "downscale_worse_than_best"))
            if _ds_accepted:
                os.replace(_tmp, out_file)
                final_size = int(_sz)
                accepted_v_bps = int(v_bps)
                new_w, new_h = int(cand_w), int(cand_h)
            else:
                try: os.remove(_tmp)
                except Exception: pass
            return int(_sz)

        _ds_w = int(new_w or w or 0)
        _seed_v = (int(max(80_000, int(_feasible_v_bps)))
                   if (_feasible_v_bps and _feasible_v_bps > 0)
                   else int(used_seed_v or 500_000))
        for _ds_step in (1, 2):
            if int(final_size) <= int(hard_target_bytes) or _over_budget("Downscale"):
                break
            _cand_w = next_lower_std_width(_ds_w)
            if not _cand_w or _cand_w >= _ds_w:
                break
            _cand_h = int(round((h or 0) * _cand_w / (w or 1)))
            if _cand_h % 2:
                _cand_h += 1
            _ds_w = _cand_w
            status_cb(f"[Ceiling] {final_size} bytes still over ceiling {hard_target_bytes}; "
                      f"downscaling to {_cand_w}x{_cand_h} and re-encoding (step {_ds_step}).", "WARNING")
            _ds_ctl = SizeController(
                target_bytes=int(hard_target_bytes), duration_s=float(dur),
                audio_bps=int(aud_bps_guess),
                min_v_bitrate=int(_v_floor),
                quality_mode=("quality_first" if _qmode_ctl != "fast" else "balanced"),
                target_policy="no_overshoot_near_max",
                max_target_attempts=3)
            _sz = _ds_try(_cand_w, _cand_h, _seed_v, f"ds{_ds_step}s")
            if _sz is None:
                continue
            _ds_ctl.set_initial(int(_seed_v), int(_sz))
            _last = int(_sz)
            _it = 0
            while (int(final_size) > int(hard_target_bytes)
                   and _ds_ctl.should_retry(actual_bytes=_last)
                   and _it < 3 and not _over_budget("Downscale")):
                _it += 1
                _nv, _ = _ds_ctl.next(_last)
                _r = _ds_try(_cand_w, _cand_h, _nv, f"ds{_ds_step}_{_it}")
                if _r is None:
                    break
                _last = int(_r)
            if int(final_size) <= int(hard_target_bytes):
                status_cb(f"[Ceiling] Fit under target at {new_w}x{new_h}: {final_size} bytes.", "INFO")
                break

    if int(final_size) > int(hard_target_bytes):
        status_cb(f"[Size] CEILING EXCEEDED: {final_size} bytes vs target {hard_target_bytes} bytes "
                  f"(over by {int(final_size) - int(hard_target_bytes)}). Retry/downscale exhausted without "
                  f"reaching the target; shipping the smallest overshoot found.", "ERROR")
    elif not _within_near_target(int(final_size)):
        status_cb(f"[Size] Final output remains outside target window: {final_size} vs {hard_target_bytes} (window=+/-{_near_tol} bytes).", "WARNING")

    # === Max Quality: pack the remaining budget (measured search) ===
    # After the size loop lands under the cap, keep spending the leftover bytes
    # in a bounded bitrate search — accept only strictly better fills under the
    # ceiling, stop on diminishing VMAF returns (<0.1) or >=99% fill.
    _FILL_GOAL = int(hard_target_bytes * 0.99)
    if (str((advanced_options or {}).get("quality_mode") or "").lower() == "max"
            and final_size <= hard_target_bytes and final_size < _FILL_GOAL
            and float(dur or 0.0) > 0.0):
        try:
            _audio_bytes_pk = int((aud_bps_guess / 8.0) * float(dur))
        except Exception:
            _audio_bytes_pk = int((128_000 / 8.0) * float(dur))
        _v_ceil = int(min(12_000_000, max(accepted_v_bps * 1.8, accepted_v_bps + 100_000)))
        try:
            if br and int(br) > 0:  # never exceed the source's own video bitrate
                _v_ceil = int(min(_v_ceil, max(int(br), int(accepted_v_bps * 1.02))))
        except Exception:
            pass
        _base_vmaf = None
        _base_floor = None
        _obj = resolve_vmaf_objective(advanced_options)
        if bool((advanced_options or {}).get("measure_quality", True)):
            try:
                _bv = compute_vmaf(input_path, out_file, duration_s=float(dur or 0.0))
                _base_vmaf = float(_bv["vmaf"]) if _bv else None
                _base_floor = vmaf_floor_score(_bv, _obj)
            except Exception:
                _base_vmaf = _base_floor = None
        # Adaptive early-exit: if the file is already perceptually transparent,
        # spending the leftover budget buys nothing the eye can see. Skip the
        # packing search entirely — smaller output, no wasted encode passes.
        # Judge transparency on the FLOOR (worst scene), not the mean: a clip that
        # averages 98 but has a 70-scene is NOT transparent and should keep packing.
        _TRANSPARENCY_VMAF = float((advanced_options or {}).get("transparency_vmaf") or 98.0)
        for _pk_n in range(1, 4):
            if _base_floor is not None and _base_floor >= _TRANSPARENCY_VMAF:
                if _pk_n == 1:
                    status_cb(f"[Pack] Already transparent (worst-scene VMAF {_base_floor:.2f} >= "
                              f"{_TRANSPARENCY_VMAF:.0f}); skipping budget packing — "
                              f"extra bytes would be invisible.", "INFO")
                break
            try:
                if callable(cancel_cb) and cancel_cb():
                    break
            except Exception:
                pass
            if _over_budget("Packing"):
                break
            _k_hat = controller.estimate_k()
            _v_try = int(((0.995 * hard_target_bytes / 1.02) - _audio_bytes_pk) * 8.0
                         / max(1e-9, _k_hat * float(dur)))
            _v_try = max(_v_try, int(accepted_v_bps * 1.02))
            _v_try = min(_v_try, _v_ceil)
            if _v_try < int(accepted_v_bps * 1.02):
                status_cb("[Pack] No meaningful bitrate headroom left; stopping.", "INFO")
                break
            _emit("packing", attempt=_pk_n)
            status_cb(f"[Pack] Attempt {_pk_n}/3: spending leftover budget at {_v_try} bps "
                      f"(fill {final_size * 100.0 / hard_target_bytes:.1f}%).", "INFO")
            pack_tmp = out_file + ".pack.mp4"
            if os.path.exists(pack_tmp):
                os.remove(pack_tmp)
            _ok_pack = compress_with_handbrake(
                input_path=input_path,
                output_path=pack_tmp,
                encoder=encoder,
                bitrate=int(_v_try),
                crf=None,
                two_pass=True,
                turbo=True,
                width=new_w,
                fps=fps,
                audio_bitrate=(None if audio_copy else int(max(64_000, (audio_br or 128_000)))),
                audio_copy=audio_copy,
                tune=tune,
                hwaccel=hwaccel,
                advanced_options={**(advanced_options or {}), "duration_s": float(dur)},
            )
            if not (_ok_pack and os.path.exists(pack_tmp) and os.path.getsize(pack_tmp) > 0):
                status_cb("[Pack] Attempt did not produce a usable file; stopping.", "WARNING")
                break
            _pk_size = os.path.getsize(pack_tmp)
            controller.record_external(int(_v_try), int(_pk_size))
            if _pk_size > hard_target_bytes:
                _ledger_attempts.append((int(_v_try), int(_pk_size), False, "pack_over_ceiling"))
                try:
                    os.remove(pack_tmp)
                except Exception:
                    pass
                _v_ceil = int(_v_try) - 1
                status_cb(f"[Pack] Overshot ceiling ({_pk_size} > {hard_target_bytes}); refining estimate.", "INFO")
                continue
            if _pk_size <= final_size:
                _ledger_attempts.append((int(_v_try), int(_pk_size), False, "pack_no_gain"))
                try:
                    os.remove(pack_tmp)
                except Exception:
                    pass
                status_cb("[Pack] Encoder did not use the extra bits; stopping.", "INFO")
                break
            _pk_vmaf = None
            if _base_vmaf is not None:
                try:
                    _pv = compute_vmaf(input_path, pack_tmp, duration_s=float(dur or 0.0))
                    _pk_vmaf = float(_pv["vmaf"]) if _pv else None
                except Exception:
                    _pk_vmaf = None
            _ledger_attempts.append((int(_v_try), int(_pk_size), True, "pack"))
            os.replace(pack_tmp, out_file)
            final_size = int(_pk_size)
            accepted_v_bps = int(_v_try)
            status_cb(f"[Pack] Accepted: {final_size} bytes "
                      f"({final_size * 100.0 / hard_target_bytes:.1f}% of cap)"
                      + (f", VMAF {_pk_vmaf:.2f}" if _pk_vmaf is not None else "") + ".", "INFO")
            if _pk_vmaf is not None and _base_vmaf is not None and (_pk_vmaf - _base_vmaf) < 0.10:
                status_cb(f"[Pack] VMAF gain below 0.1 ({_pk_vmaf:.2f} vs {_base_vmaf:.2f}); "
                          "diminishing returns — stopping.", "INFO")
                break
            if _pk_vmaf is not None:
                _base_vmaf = _pk_vmaf
            if final_size >= _FILL_GOAL:
                status_cb("[Pack] Budget packed (>=99% of the cap).", "INFO")
                break

    try:
        _container = (os.path.splitext(out_file)[1] or ".mp4").lstrip(".").lower() or "mp4"
        # Attribute learning to the encoder that ACTUALLY encoded, not the one
        # the user requested — a svt-av1 request that raced to x264 used to be
        # recorded as svt-av1, poisoning the rate model and the seed cache.
        _encoder   = str(encoder or advanced_options.get("encoder") or "x264")
        _stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
        with _STATS_LOCK:
            # fps may be None (encoder kept source rate) — float(None) used to
            # silently kill this whole block, so learning/caching never happened.
            _klass_hint = os.environ.get("BC_CONTENT_CLASS") or None
            learn_from_result(_stats_dir, _encoder, _container, int(target_bytes), int(final_size),
                              width_hint=int(new_w or w or 0), fps_hint=float(fps or fr or 0.0),
                              klass_hint=_klass_hint)
            # update_overshoot was the only writer of stats["overshoot"], but had
            # zero call sites anywhere — planner.py's get_dynamic_overshoot() read
            # a permanently-inert default=1.00. Wire it so the "learned overshoot
            # correction" the planner comment describes actually happens.
            try:
                _ov_stats = load_stats(_stats_dir)
                update_overshoot(_ov_stats, _encoder, _container, int(target_bytes), int(final_size),
                                 width=int(new_w or w or 0), fps=float(fps or fr or 0.0),
                                 klass=_klass_hint)
                save_stats(_stats_dir, _ov_stats)
            except Exception:
                pass
        # Always record the outcome so repeat jobs can skip probing entirely
        # (previously only retried jobs ever wrote the abr cache).
        try:
            with _STATS_LOCK:
                cache_store(_stats_dir, input_path, int(target_bytes / 1024 / 1024),
                            str(encoder or "x264"), int(accepted_v_bps),
                            int(new_w), float(fps or fr or 0.0), int(final_size))
        except Exception:
            pass
        _jsonl_log("learned", {"encoder": _encoder, "container": _container,
                               "target_bytes": int(target_bytes), "actual_bytes": int(final_size)})
        _adj = guardrail_adjust(int(final_size), int(target_bytes))
        if _adj is not None:
            _jsonl_log("guardrail_suggest", {"scale": float(_adj)})
    except Exception:
        pass

    # === Perceptual quality measurement (VMAF) + optional quality floor ===
    vmaf_result = None
    _measure_quality = bool((advanced_options or {}).get("measure_quality", True))
    try:
        _min_vmaf = float((advanced_options or {}).get("min_vmaf") or 0.0)
    except Exception:
        _min_vmaf = 0.0
    _obj = resolve_vmaf_objective(advanced_options)
    xpsnr_result = None
    if _measure_quality:
        try:
            status_cb("[Quality] Measuring VMAF against the original...", "INFO")
            _emit("vmaf")
            vmaf_result = compute_vmaf(input_path, out_file, duration_s=float(dur or 0.0))
            if vmaf_result and vmaf_result.get("reliable") is False:
                status_cb(
                    f"[Quality] VMAF measurement looks UNRELIABLE "
                    f"({vmaf_result.get('zero_frac', 0) * 100:.0f}% of frames scored ~0 — "
                    f"usually a source timestamp/frame-alignment quirk, not real quality). "
                    f"Ignoring this score (not used for gating or learning).", "WARNING")
                vmaf_result = None
            if vmaf_result:
                _fl = vmaf_floor_score(vmaf_result, _obj)
                _at = vmaf_result.get("min_window_at")
                _at_txt = ""
                if isinstance(_at, (int, float)):
                    _at_txt = f" @ {int(_at)//60}:{int(_at)%60:02d}"
                _fl_txt = (f", worst-scene {_fl:.1f}{_at_txt}"
                           if (_fl is not None and _obj != "mean") else "")
                status_cb(f"[Quality] VMAF {vmaf_result['vmaf']:.1f} ({vmaf_result['label']}){_fl_txt}.", "INFO")
                # Second-opinion perceptual metric (XPSNR): orthogonal to VMAF and
                # built into ffmpeg. Cross-check only — never gates or packs. When
                # VMAF rates high but XPSNR rates low, VMAF is probably being fooled
                # (over-sharpening / anime / screen-text — its known weak spots).
                if bool((advanced_options or {}).get(
                        "perceptual_crosscheck", ADVANCED_DEFAULTS.get("perceptual_crosscheck", True))):
                    try:
                        xpsnr_result = compute_xpsnr(input_path, out_file, duration_s=float(dur or 0.0))
                    except Exception:
                        xpsnr_result = None
                    if xpsnr_result and xpsnr_result.get("reliable"):
                        status_cb(f"[Quality] XPSNR {xpsnr_result['xpsnr']:.1f} dB "
                                  f"({xpsnr_result['label']}) - perceptual cross-check.", "INFO")
                        try:
                            _vm = float(vmaf_result.get("vmaf") or 0.0)
                            _xp = float(xpsnr_result.get("xpsnr") or 0.0)
                        except Exception:
                            _vm = _xp = 0.0
                        if _vm >= 90.0 and _xp < 28.0:
                            status_cb(
                                "[Quality] Metrics DISAGREE: VMAF rates this encode high but XPSNR "
                                "rates it low — VMAF may be over-scoring (sharpening / anime / "
                                "screen-text are its blind spots). Worth an eyeball.", "WARNING")
        except Exception as _vm_e:
            status_cb(f"[Quality] VMAF measurement skipped: {type(_vm_e).__name__}", "DEBUG")

        # Quality floor: spend spare budget until the WORST scene (the configured
        # floor objective — default worst ~2s window) clears min_vmaf, not just the
        # average. This is the fix for the VMAF "average trap": a clip averaging 96
        # with a 78-scene used to pass a floor of 92 and ship the ugly scene.
        _cur_floor = vmaf_floor_score(vmaf_result, _obj)
        if (vmaf_result and _min_vmaf > 0.0 and _cur_floor is not None and _cur_floor < _min_vmaf
                and final_size < int(hard_target_bytes * 0.97) and (bitrate or target_bitrate)):
            headroom = max(1.005, min(1.30, float(hard_target_bytes) * 0.985 / max(1.0, float(final_size))))
            boost_v = max(int(_v_floor), int((used_seed_v or target_bitrate) * headroom))
            status_cb(f"[Quality] Below floor (worst-scene VMAF {_cur_floor:.1f} < {_min_vmaf:.0f}, "
                      f"mean {vmaf_result['vmaf']:.1f}); re-encoding at {boost_v} bps to use spare "
                      f"size budget.", "INFO")
            q_tmp = out_file + ".qfloor.mp4"
            try:
                if os.path.exists(q_tmp):
                    os.remove(q_tmp)
            except Exception:
                pass
            _ok_q = compress_with_handbrake(
                input_path=input_path, output_path=q_tmp,
                encoder=encoder, bitrate=int(boost_v), crf=None, two_pass=True,
                width=new_w, fps=fps, tune=tune,
                audio_bitrate=(None if audio_copy else int(max(64_000, (audio_br or 128_000)))),
                audio_copy=audio_copy, hwaccel=hwaccel,
                advanced_options={**(advanced_options or {}), "duration_s": float(dur)},
            )
            if _ok_q and os.path.exists(q_tmp):
                q_size = os.path.getsize(q_tmp)
                # Only accept if it stays under the ceiling and actually improves quality.
                if q_size <= int(hard_target_bytes):
                    q_vmaf = None
                    try:
                        q_vmaf = compute_vmaf(input_path, q_tmp, duration_s=float(dur or 0.0))
                    except Exception:
                        q_vmaf = None
                    _q_floor = vmaf_floor_score(q_vmaf, _obj)
                    # Accept only if the boost lifts the WORST scene (the thing the
                    # floor is about), not merely the average.
                    if q_vmaf and _q_floor is not None and _cur_floor is not None and _q_floor >= _cur_floor:
                        os.replace(q_tmp, out_file)
                        final_size = q_size
                        vmaf_result = q_vmaf
                        _cur_floor = _q_floor
                        status_cb(f"[Quality] Improved worst-scene VMAF to {_q_floor:.1f} "
                                  f"(mean {q_vmaf['vmaf']:.1f}, {format_bytes(q_size)}).", "INFO")
                    else:
                        try: os.remove(q_tmp)
                        except Exception: pass
                else:
                    try: os.remove(q_tmp)
                    except Exception: pass
                    status_cb("[Quality] Boost pass overshot the ceiling; keeping previous output.", "INFO")
        if vmaf_result and _min_vmaf > 0.0 and _cur_floor is not None and _cur_floor < _min_vmaf:
            status_cb(f"[Quality] Worst-scene VMAF {_cur_floor:.1f} (mean {vmaf_result['vmaf']:.1f}) is below "
                      f"the {_min_vmaf:.0f} floor — the size target doesn't leave room for higher quality.",
                      "WARNING")

    # === No-inflate guarantee (source as candidate zero) ===
    # Never deliver a file larger than a source that already fit under the ceiling
    # — even if the fast-path passthrough above was bypassed (e.g. it raised and
    # fell through to a full encode, which is exactly how the old inflation bug
    # shipped). The untouched source is always a valid, transparent, in-budget
    # candidate; if the encode can't beat it on size, hand back the original.
    try:
        _src_sz_final = int(os.path.getsize(input_path))
    except Exception:
        _src_sz_final = 0
    if _src_sz_final and _src_sz_final <= int(hard_target_bytes) and int(final_size) > _src_sz_final:
        _keep_tmp = out_file + ".keepsrc"
        try:
            if os.path.exists(_keep_tmp):
                os.remove(_keep_tmp)
        except Exception:
            pass
        _kept = False
        try:
            if (_remux_smart(input_path, _keep_tmp, _privacy_args(advanced_options.get("privacy_preset")))
                    and os.path.exists(_keep_tmp)):
                os.replace(_keep_tmp, out_file)
                _kept = True
        except Exception:
            _kept = False
        if not _kept:
            try:
                shutil.copy2(input_path, out_file)
                _kept = True
            except Exception:
                _kept = False
        if _kept:
            final_size = int(os.path.getsize(out_file))
            vmaf_result = None  # delivering the original stream; VMAF vs itself is moot
            status_cb("[Race] Kept the original - a re-encode would have inflated a source "
                      "that already fit under the target.", "INFO")

    stats = {
        "original_size": os.path.getsize(input_path),
        "compressed_size": final_size,
        # True only when the ceiling invariant was actually violated (final_size
        # > hard_target_bytes) after retries/downscale exhausted. Callers should
        # surface this as a failure, not a quiet log line.
        "ceiling_exceeded": bool(int(final_size) > int(hard_target_bytes)),
        # ABR/two-pass encodes have no CRF; logging the heuristic suggestion
        # (always ~22) as "used_crf" made every run look identical in the logs.
        "used_crf": (suggested_crf if not (bitrate or target_bitrate) else None),
        "video_bps": int(accepted_v_bps or used_seed_v or target_bitrate or 0),
        "duration": dur,
        "width": new_w,
        "height": int(round(h * new_w / w)) if w else h,
        "bitrate": br,
        "frame_rate": fr,
        "output_path": out_file,
        "vmaf": (vmaf_result["vmaf"] if vmaf_result else None),
        "vmaf_label": (vmaf_result["label"] if vmaf_result else None),
        # Floor metrics + spread: instrumentation to decide whether the heavy
        # per-segment re-encode engine (step 2) is ever worth building. A large
        # spread = the average trap actually bit on this file.
        "vmaf_p5": (vmaf_result.get("p5") if vmaf_result else None),
        "vmaf_min_window": (vmaf_result.get("min_window") if vmaf_result else None),
        "vmaf_min_window_at": (vmaf_result.get("min_window_at") if vmaf_result else None),
        "vmaf_spread": (vmaf_result.get("spread") if vmaf_result else None),
        "xpsnr": (xpsnr_result.get("xpsnr") if xpsnr_result else None),
        "xpsnr_min_window": (xpsnr_result.get("min_window") if xpsnr_result else None),
        "vmaf_objective": _obj,
        "preproc": (str((advanced_options or {}).get("_preproc_label") or "") or None),
        "encoder": str(encoder or ""),
        "encode_seconds": round(time.time() - t_start, 1),
        "target_bytes": int(hard_target_bytes),
        "quality_mode": str((advanced_options or {}).get("quality_mode") or ""),
    }
    _jsonl_log("encode_end", {"type": "video", **stats})

    # === Outcome ledger (learning stage 1) ==================================
    # One rich record per completed encode: features + full EFFECTIVE operating
    # point + every retry observation + race scoreboard + v1 VMAF outcome.
    try:
        from outcome_ledger import build_record, ledger_append, build_op, recent_prior_ts
        _ol_stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
        try:
            from ml_heuristics import _bc_file_sig as _ol_sig_fn
            _ol_input_sig = _ol_sig_fn(input_path)
        except Exception:
            _ol_input_sig = None
        _vm_tag = resolve_vmaf_model() or "version=vmaf_v0.6.1"
        if _vm_tag.startswith("path="):
            _vm_tag = os.path.splitext(os.path.basename(_vm_tag[5:]))[0]
        elif _vm_tag.startswith("version="):
            _vm_tag = _vm_tag[len("version="):]
        _ol_attempts = list(_ledger_attempts or [])
        _ol_aud_bps = (int((audio_meta or {}).get("bitrate") or 128_000) if audio_copy
                       else int(audio_br or 128_000))
        _ol_final_v_bps = int(accepted_v_bps or target_bitrate or 0)

        # Measured-vs-predicted mux overhead (self-audit, read-only -- see
        # build_op's docstring note; overhead.py's own learned-update wiring
        # is a separate, out-of-scope fix).
        _overhead_predicted, _overhead_measured = None, None
        try:
            from overhead import get_overhead_factor as _ol_overhead_pred
            _overhead_predicted = _ol_overhead_pred(USER_SETTINGS_DIR, _out_container,
                                                    int(new_w or 0), int(new_h or 0), float(fps or fr or 0.0))
            _core_bytes = (float(_ol_final_v_bps) + float(_ol_aud_bps)) * max(0.1, float(dur or 0.0)) / 8.0
            if _core_bytes > 0 and final_size:
                _overhead_measured = float(final_size) / _core_bytes
        except Exception:
            pass

        # Predicted-vs-actual for every active shadow predictor, not just the
        # ledger's own dev_pred: probe_predictor's rate fit (previously had no
        # calibration tracking anywhere) and ai_advisor's quality prediction at
        # the FINAL effective operating point (its early call, inside
        # choose_bitrates_advised, predicts for a pre-heuristic bitrate that
        # never ships). Both are read-only, shadow-only — logged, never acted on.
        try:
            _probe_dev_pred = float(mpred.get("video_bps")) if isinstance(mpred, dict) and mpred.get("video_bps") else None
        except Exception:
            _probe_dev_pred = None
        _probe_dev_actual = float(_ol_final_v_bps) if _ol_final_v_bps else None
        try:
            from ai_advisor import predict_effective_quality as _ol_adv_q
            _advisor_q_pred = _ol_adv_q(_feats_ctx or {}, _ol_final_v_bps, _ol_aud_bps)
        except Exception:
            _advisor_q_pred = None

        # ai_advisor's Ridge model (_MODEL) was never actually trained in
        # production: its only prior caller, cache_store_advised (aliased
        # `cache_store` above), fires before VMAF is measured in this
        # function, so measured_quality was always None and post_encode_learn
        # always skipped (by design -- it refuses to self-label). VMAF IS
        # measured by now (vmaf_result, used for outcome.vmaf below), so learn
        # from it here instead. Still a no-op whenever VMAF wasn't measured.
        try:
            from ai_advisor import post_encode_learn as _ol_advisor_learn
            _ol_advisor_learn(
                input_path=input_path, output_path=out_file, encoder=str(encoder or ""),
                target_bytes=int(hard_target_bytes), actual_bytes=int(final_size or 0),
                a_bps_used=int(_ol_aud_bps), v_bps_used=_ol_final_v_bps,
                measured_quality=(vmaf_result or {}).get("vmaf"))
        except Exception:
            pass

        # Content class (screen_ui/film_grain/sports_action/flat_camera/general,
        # set by ai_advisor.choose_bitrates_advised earlier in this encode) --
        # tagging it on the record is what makes any FUTURE per-class accuracy
        # analysis (which predictor to trust for which content) possible at
        # all; without it, ledger records carry raw features but nothing a
        # meta-predictor could group by.
        _ol_klass = os.environ.get("BC_CONTENT_CLASS") or None

        # Meta-predictor, shadow-only: log which predictor's aggregate logged
        # accuracy (shadow_report's mean-abs-err per predictor, extended in
        # this same phase) currently looks best, WITHOUT routing to it -- live
        # routing is future work once there's a per-content-class accuracy
        # comparison (this record's new content_class tag) to justify it, same
        # promotion bar seed_adjust already had to clear.
        _ol_meta_favored = None
        try:
            from outcome_ledger import shadow_report as _ol_sr
            _sr = _ol_sr(_ol_stats_dir)
            _candidates = {"ledger_dev": _sr.get("pred_mean_abs_err"),
                          "probe": (_sr.get("probe") or {}).get("mean_abs_pct_err"),
                          "advisor": (_sr.get("advisor") or {}).get("mean_abs_vmaf_err")}
            _scored = {k: v for k, v in _candidates.items() if v is not None}
            if _scored:
                _ol_meta_favored = min(_scored, key=_scored.get)
        except Exception:
            _ol_meta_favored = None

        _ol_shadow = dict((advanced_options or {}).get("_ledger_shadow") or {})
        _ol_shadow["probe_dev_pred"] = _probe_dev_pred
        _ol_shadow["probe_dev_actual"] = _probe_dev_actual
        _ol_shadow["advisor_q_pred"] = _advisor_q_pred
        _ol_shadow["meta_predictor_favored"] = _ol_meta_favored

        # Implicit reject signal: same input re-sent through recently usually
        # means the prior result wasn't kept. Forward-pointing only.
        try:
            _ol_reencode_of = recent_prior_ts(_ol_stats_dir, input_path, lookback_hours=24.0)
        except Exception:
            _ol_reencode_of = None

        _ol_rec = build_record(
            input_path=input_path,
            features=(_feats_ctx or {}),
            src={"codec": str(_probe_video_stream(input_path).get("codec_name") or ""),
                 "w": int(w or 0), "h": int(h or 0), "fps": float(fr or 0.0),
                 "dur": float(dur or 0.0), "bitrate": int(br or 0),
                 "size": int(os.path.getsize(input_path)) if os.path.exists(input_path) else 0,
                 "input_sig": _ol_input_sig,
                 # Full source color/HDR characterization -- already computed
                 # by extract_media_features (ml_heuristics.py), previously
                 # dropped entirely at this call site.
                 "pix_fmt": (_feats_ctx or {}).get("pix_fmt"),
                 "color_range": (_feats_ctx or {}).get("color_range"),
                 "color_primaries": (_feats_ctx or {}).get("color_primaries"),
                 "profile": (_feats_ctx or {}).get("profile"),
                 "codec_name": (_feats_ctx or {}).get("codec_name"),
                 "is_hdr": bool((_feats_ctx or {}).get("is_hdr"))},
            # EFFECTIVE operating point: encoder_eff is the encoder that actually
            # ran (codec-race winner), never the request — see build_op.
            op=build_op(
                target_bytes=hard_target_bytes,
                encoder_req=requested_encoder,
                encoder_eff=encoder,
                width=new_w, height=new_h,
                fps=(fps or fr),
                v_bps=_ol_final_v_bps,
                audio_bps=_ol_aud_bps, audio_copy=audio_copy,
                preset=(advanced_options or {}).get("preset"),
                quality_mode=(advanced_options or {}).get("quality_mode"),
                preproc=(advanced_options or {}).get("_preproc_label"),
                film_grain=((advanced_options or {}).get("_film_grain") or {}).get("level"),
                film_grain_ratio=((advanced_options or {}).get("_film_grain") or {}).get("size_ratio"),
                spotlight=(advanced_options or {}).get("_spotlight_secs"),
                dur=dur,
                manual_bitrate_requested=(advanced_options or {}).get("_manual_bitrate_requested"),
                advised_v_bps=(advanced_options or {}).get("_advised_v_bps"),
                override_applied=bool((advanced_options or {}).get("_manual_override")),
                # Any attempt beyond the primary (retry/refine/downscale/pack)
                # ran with turbo=True (faster, lower-quality first pass) —
                # a real quality knob degraded under retry pressure.
                degraded=bool(len(_ledger_attempts or []) > 1),
                two_pass=bool(two_pass), encoder_version=ffmpeg_build_version(),
                hwaccel=hwaccel,
                overhead_predicted=_overhead_predicted, overhead_measured=_overhead_measured),
            attempts=_ol_attempts,
            race=(advanced_options or {}).get("_race_scores"),
            outcome={"success": True,
                     "size": int(final_size or 0),
                     "vmaf": (vmaf_result or {}).get("vmaf"),
                     "harmonic": (vmaf_result or {}).get("harmonic"),
                     "p5": (vmaf_result or {}).get("p5"),
                     "min_window": (vmaf_result or {}).get("min_window"),
                     "min_window_at": (vmaf_result or {}).get("min_window_at"),
                     "spread": (vmaf_result or {}).get("spread"),
                     "series": (vmaf_result or {}).get("series"),
                     "series_span_s": (vmaf_result or {}).get("series_span_s"),
                     "xpsnr": (xpsnr_result or {}).get("xpsnr"),
                     "xpsnr_min_window": (xpsnr_result or {}).get("min_window"),
                     "encode_seconds": round(time.time() - t_start, 1),
                     "reencode_of_prior_ts": _ol_reencode_of,
                     # Flywheel leading indicator: fewer retries -> faster
                     # ledger growth -> better predictions -> fewer retries.
                     "retries_per_encode": max(0, len(_ledger_attempts or []) - 1),
                     "content_class": _ol_klass},
            shadow=_ol_shadow,
            vmaf_model=_vm_tag)
        with _STATS_LOCK:
            ledger_append(_ol_stats_dir, _ol_rec)
    except Exception as _ol_e:
        status_cb(f"[Ledger] Record skipped: {type(_ol_e).__name__}", "DEBUG")


    if webhook_url:
        _wh_ok = _post_webhook_hardened(webhook_url, json_payload=stats, file_path=out_file)
        try:
            from outcome_ledger import record_webhook_outcome as _ol_wh
            _ol_wh(os.path.join(USER_SETTINGS_DIR, "stats"), input_path, _wh_ok)
        except Exception:
            pass


    status_cb(f"Compress done in {time.time()-t_start:.1f}s")
    _emit("done", pct=100.0)
    try:
        os.environ.pop("BC_CURRENT_INPUT", None)
    except Exception:
        pass
    # Tear down the shared two-pass pass-log dir (holds pass-1 stats reused across
    # this job's retries) and drop the per-job flags that pointed encoders at it.
    try:
        if _pl_shared_dir and os.path.isdir(_pl_shared_dir):
            shutil.rmtree(_pl_shared_dir, ignore_errors=True)
    except Exception:
        pass
    try:
        if _probe_ref_dir and os.path.isdir(_probe_ref_dir):
            shutil.rmtree(_probe_ref_dir, ignore_errors=True)
    except Exception:
        pass
    if isinstance(advanced_options, dict):
        advanced_options.pop("_twopass_passlog_dir", None)
        advanced_options.pop("_twopass_reuse_stats", None)
    return stats





_LOSSLESS_AUDIO_CODECS = {
    "flac", "alac", "wav", "pcm_s16le", "pcm_s24le", "pcm_s32le",
    "pcm_f32le", "ape", "tta", "wavpack", "truehd", "mlp",
}


def _audio_transparency_label(encoder: str, bitrate_bps: int, channels: int) -> str:
    """
    Rough perceptual-quality bucket for a lossy audio encode, so the summary can
    say "transparent" the way video says VMAF. Thresholds are per-channel and
    codec-aware (opus is the most efficient, mp3 the least)."""
    per_ch = float(bitrate_bps) / max(1, int(channels))
    e = (encoder or "").lower()
    if "opus" in e:
        t = 96_000
    elif "aac" in e:
        t = 128_000
    else:  # mp3 / other
        t = 160_000
    if per_ch >= t:
        return "transparent (perceptually lossless)"
    if per_ch >= t * 0.75:
        return "excellent"
    if per_ch >= t * 0.5:
        return "good"
    return "compressed"


def compress_audio(input_path: str, save_path: str, status_callback,
                   target_size_mb: int, webhook_url: str,
                   advanced_options: dict, cancel_callback) -> dict:

    status_callback(f"Compressing audio: {input_path}")
    t0 = time.time()

    try:

        try:
            _v = float(target_size_mb)

            target_bytes = int(_v) if _v >= (128 * 1024) else int(_v * 1024 * 1024)
            target_bytes = max(1, target_bytes)
        except Exception:
            target_bytes = 10 * 1024 * 1024

    except Exception:
        target_bytes = 10 * 1024 * 1024  # 10 MB fallback
    target_bytes = apply_target_size_margin(target_bytes)

    _jsonl_log("start_job", {"type": "audio", "input": input_path, "target_bytes": target_bytes})

    meta = _probe_audio_meta(input_path)
    duration = max(1.0, float(meta.get("duration", 0.0)))  # avoid division by zero
    orig_bps = max(32_000, int(meta.get("bitrate", DEFAULT_AUDIO_BITRATE)))

    # --- Smart source: don't degrade audio that already fits --------------------
    # Re-encoding to a lossy codec only loses quality when the source already
    # meets the size goal: a lossless master (FLAC/ALAC/WAV) would become lossy
    # for nothing, and a lossy source would pick up a second generation of
    # artifacts. Keep the original bytes instead (tags + cover travel with it).
    try:
        _src_size = int(os.path.getsize(input_path))
    except Exception:
        _src_size = 0
    _src_codec = str(meta.get("codec") or "").lower()
    _src_ext = os.path.splitext(input_path)[1].lower().lstrip(".") or "bin"
    _force_reencode = bool(advanced_options.get("audio_force_reencode"))
    if _src_size and _src_size <= target_bytes and not _force_reencode:
        _pass_out = _bc_build_output_path(input_path, save_path, advanced_options, default_ext=_src_ext)
        try:
            shutil.copy2(input_path, _pass_out)
            fin = int(os.path.getsize(_pass_out))
            _is_lossless = _src_codec in _LOSSLESS_AUDIO_CODECS
            status_callback(
                f"Source already fits the target ({format_bytes(fin)} ≤ "
                f"{format_bytes(target_bytes)}); kept the original "
                f"{'lossless ' if _is_lossless else ''}file to avoid re-compression quality loss.",
                level="INFO")
            stats = {
                "filename": os.path.basename(_pass_out),
                "original_size": _src_size,
                "compressed_size": fin,
                "ratio": fin / max(1, _src_size),
                "time_taken": time.time() - t0,
                "output_path": _pass_out,
                "note": "kept original (already under target; re-encode would only lose quality)",
            }
            _jsonl_log("encode_end", {"type": "audio", **stats})
            if webhook_url:
                _post_webhook_hardened(webhook_url, json_payload=stats, file_path=_pass_out)
            return stats
        except Exception as _pe:
            status_callback(f"Passthrough copy failed ({type(_pe).__name__}); re-encoding instead.",
                            level="WARNING")

    # --- Lossless-preserving path -----------------------------------------------
    # The source is lossless but bigger than the target (so the passthrough above
    # didn't fire). Before going lossy, see if a re-compressed FLAC — at the
    # source's own rate/channels — fits under the target. If it does, the user
    # gets a smaller file with ZERO quality loss instead of a lossy squeeze.
    # (Skip when the source is already FLAC: re-FLAC won't shrink it further.)
    if (_src_codec in _LOSSLESS_AUDIO_CODECS and _src_codec != "flac"
            and not _force_reencode):
        _flac_tmp = os.path.join(save_path, "._bc_flac_try_.flac")
        try:
            if os.path.exists(_flac_tmp):
                os.remove(_flac_tmp)
            _okf, _szf, _ = _encode_audio_once(
                input_path, _flac_tmp, encoder="flac", bitrate_bps=0,
                sr=0, channels=0,  # 0 = keep source rate/channels (stay lossless)
                vbr_mode="off", loudnorm=False, highpass_hz=None, lowpass_hz=None)
            if _okf and 0 < _szf <= target_bytes:
                _flac_out = _bc_build_output_path(input_path, save_path, advanced_options, default_ext="flac")
                os.replace(_flac_tmp, _flac_out)
                status_callback(
                    f"Re-compressed to lossless FLAC ({format_bytes(_szf)} ≤ "
                    f"{format_bytes(target_bytes)}) — fits the target with zero quality loss.",
                    level="INFO")
                stats = {
                    "filename": os.path.basename(_flac_out),
                    "original_size": _src_size or os.path.getsize(input_path),
                    "compressed_size": int(_szf),
                    "ratio": int(_szf) / max(1, _src_size or 1),
                    "time_taken": time.time() - t0,
                    "output_path": _flac_out,
                    "audio_bitrate": 0,
                    "quality_label": "lossless (FLAC)",
                    "note": "re-compressed lossless FLAC fit under target",
                }
                _jsonl_log("encode_end", {"type": "audio", **stats})
                if webhook_url:
                    _post_webhook_hardened(webhook_url, json_payload=stats, file_path=_flac_out)
                return stats
            else:
                if os.path.exists(_flac_tmp):
                    os.remove(_flac_tmp)
        except Exception:
            try:
                if os.path.exists(_flac_tmp):
                    os.remove(_flac_tmp)
            except Exception:
                pass

    fmt = (advanced_options.get("audio_format", "opus") or "opus").lower()
    audio_mode = (advanced_options.get("audio_mode", "auto") or "auto").lower()   # auto|music|speech
    vbr_mode   = (advanced_options.get("audio_vbr", "on") or "on").lower()        # on|constrained|off
    loudnorm   = bool(advanced_options.get("audio_loudnorm", False))
    downmix    = bool(advanced_options.get("audio_downmix_mono", audio_mode == "speech"))
    max_sr     = int(advanced_options.get("audio_max_sr", 48000))                 # cap SR to 48k by default

    if fmt == "mp3":
        encoder, ext = "libmp3lame", "mp3"
    elif fmt == "aac":
        encoder, ext = "aac", "aac"
    elif fmt == "m4a":
        encoder, ext = "aac", "m4a"
    else:
        encoder, ext = "libopus", "opus"

    out_ch = 1 if downmix else min(2, meta["ch"] or 2)

    # Prepare a size-capped album cover ONCE. Album art is routinely 3000x3000 /
    # multiple MB; embedding it verbatim made small targets overshoot no matter
    # how low the audio bitrate went (the size search only tunes audio). The
    # cover budget scales with the target so art stays a small slice of it.
    _audio_work_dir = None
    _cover_path = None          # capped image for stream formats (mp3/m4a)
    _opus_cover_meta = None     # ffmetadata (tags + picture) for opus
    _cover_bytes = 0
    _privacy_strict = str(advanced_options.get("privacy_preset") or "").lower() == "strict"
    if not _privacy_strict:
        try:
            _audio_work_dir = tempfile.mkdtemp(prefix="bc_audiometa_")
            _cover_budget = min(400 * 1024, max(0, int(target_bytes * 0.06)))
            _cap = _prepare_cover_file(input_path, _audio_work_dir, _cover_budget)
            if _cap:
                _cover_path = _cap[0]
                _cover_bytes = os.path.getsize(_cover_path)
                if encoder == "libopus":
                    _opus_cover_meta = _build_opus_cover_meta(input_path, _cap, _audio_work_dir)
                status_callback(f"Album art preserved (~{max(1, _cover_bytes // 1024)} KB embedded).", level="INFO")
        except Exception:
            _cover_path = None
            _opus_cover_meta = None
            _cover_bytes = 0

    in_sr  = meta["sr"] or 48000
    out_sr = min(max_sr, 48000 if in_sr >= 44100 else 44100)

    hp = 80 if out_ch == 1 else None
    lp = 20000

    headroom = 1.03
    # Reserve the cover's in-container cost so the audio budget targets only the
    # space that's actually left. Opus stores the picture base64-encoded inside a
    # vorbis comment (~1.34x the raw bytes), so it must reserve more than the raw
    # file size; mp3/m4a copy the bytes with only small framing overhead.
    _cover_reserve = int(_cover_bytes * (1.37 if encoder == "libopus" else 1.06))
    init_bps = int((max(1, target_bytes - _cover_reserve) * 8 / duration) / headroom)

    if encoder == "libopus":

        lo = 24_000 if out_ch == 1 else 32_000
        hi = 192_000 if out_ch == 1 else 256_000
    elif encoder == "aac":
        # Floors low enough to reach aggressive targets on long tracks; the size
        # search still prefers the highest bitrate that fits, so normal targets
        # are unaffected. (128k mp3 floor used to overshoot a 2 MB / 5-min track.)
        lo, hi = (32_000 if out_ch == 1 else 40_000), (224_000 if out_ch == 1 else 320_000)
    else:  # mp3
        lo, hi = (40_000 if out_ch == 1 else 48_000), (256_000 if out_ch == 1 else 320_000)

    if encoder == "libopus":

        max_cap = 510_000
    elif encoder == "aac":

        max_cap = 512_000
    else:

        max_cap = 320_000

    calc_hi = max(hi, int(init_bps * 1.25))
    hi = min(calc_hi, max_cap)

    if lo >= hi:
        lo = max(16_000, hi - 32_000)

    if audio_mode == "speech":
        lo = max(lo - 16_000, 16_000)
        hi = max(hi - 32_000, lo + 16_000)
    elif audio_mode == "music":
        lo = min(lo + 16_000, hi - 16_000)

    target_bps = max(lo, min(init_bps, hi))

    base = os.path.splitext(os.path.basename(input_path))[0]
    prefix = advanced_options.get("output_prefix", "")
    suffix = advanced_options.get("output_suffix", "")
    final_out = _bc_build_output_path(input_path, save_path, advanced_options, default_ext=ext)

    tmp_out = os.path.join(save_path, f"._tmp_audio_encode_.{ext}")
    best_out = os.path.join(save_path, f"._bc_best_audio_.{ext}")
    for _p in (tmp_out, best_out):
        if os.path.exists(_p):
            os.remove(_p)

    tries = 0
    max_tries = 6
    best_ok = None  # (size, bitrate, path_is_tmp_bool)

    low_bps, high_bps = lo, hi
    current_bps = target_bps

    while tries < max_tries:
        if cancel_callback():
            status_callback("Audio compression cancelled.", level="WARNING")
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
            _rmtree_quiet(_audio_work_dir)
            return {}

        tries += 1
        if os.path.exists(tmp_out):
            os.remove(tmp_out)

        status_callback(f"Try {tries}/{max_tries}: {current_bps//1000} kbps, "
                        f"{out_sr} Hz, {'mono' if out_ch==1 else 'stereo'} "
                        f"(encoder={encoder}, vbr={vbr_mode})")

        ok, size, err_tail = _encode_audio_once(
            input_path, tmp_out,
            encoder=encoder,
            bitrate_bps=current_bps,
            sr=out_sr,
            channels=out_ch,
            vbr_mode=vbr_mode,
            loudnorm=loudnorm,
            highpass_hz=hp,
            lowpass_hz=lp,
            extra_filters=None,
            opus_cover_meta=_opus_cover_meta,
            cover_file=_cover_path
        )

        if not ok:
            status_callback(f"ffmpeg error on pass {tries}:\n{err_tail}", level="ERROR")

            break

        status_callback(
            f"Output size: {format_bytes(size)} "
            f"(target {format_bytes(max(1, int(target_bytes)))})"
        )

        if size <= target_bytes:
            # Keep the LARGEST under-target result (best quality, closest to the
            # cap) AND save its bytes — the loop climbs bitrate after this, so the
            # last iteration is usually over target; delivering tmp_out blindly
            # used to ship that overshoot.
            if best_ok is None or size > best_ok[0]:
                best_ok = (size, current_bps, True)
                try:
                    shutil.copyfile(tmp_out, best_out)
                except Exception:
                    pass

            if size >= int(target_bytes * 0.95):
                break

            low_bps = max(low_bps, current_bps)

            current_bps = min(high_bps, int((current_bps + high_bps) / 2))

            if current_bps >= high_bps or (high_bps - current_bps) < 1000:
                status_callback(
                    f"Reached {encoder} bitrate ceiling (~{high_bps//1000} kbps); "
                    f"best achievable ≈ {format_bytes(size)} for this track.",
                    level="WARNING"
                )
                break
        else:

            high_bps = min(high_bps, current_bps)
            current_bps = max(low_bps, int((low_bps + current_bps) / 2))

    if not best_ok:
        status_callback("Falling back to ABR binary search...")

        chosen_format = "opus" if encoder == "libopus" else ("aac" if encoder == "aac" else "mp3")
        audio_encoder = "libopus" if chosen_format == "opus" else ("aac" if chosen_format == "aac" else "libmp3lame")

        temp_output = os.path.join(save_path, f"_temp_audio_bs_.{ext}")
        if os.path.exists(temp_output):
            os.remove(temp_output)

        best_bitrate = binary_search_audio_bitrate(
            input_path, temp_output, audio_encoder,
            32_000, hi, target_bytes,
            status_callback, cancel_callback
        )  # :contentReference[oaicite:2]{index=2}

        if best_bitrate is None:

            best_bitrate = target_bps

        if os.path.exists(temp_output):
            os.remove(temp_output)
        ok, _, err_tail = _encode_audio_once(
            input_path, temp_output,
            encoder=encoder,
            bitrate_bps=best_bitrate,
            sr=out_sr, channels=out_ch,
            vbr_mode=("constrained" if encoder == "libopus" else "off"),
            loudnorm=False, highpass_hz=None, lowpass_hz=None,
            opus_cover_meta=_opus_cover_meta, cover_file=_cover_path
        )
        if not ok:
            status_callback(f"ffmpeg error (final encode):\n{err_tail}", level="ERROR")
            _rmtree_quiet(_audio_work_dir)
            return {}
        os.replace(temp_output, final_out)
    else:
        # Deliver the SAVED best under-target file, not whatever tmp_out ended on.
        os.replace(best_out if os.path.exists(best_out) else tmp_out, final_out)

    # Hard-ceiling guard: coarse codec granularity (mp3 ABR framing), the
    # embedded cover, or a bitrate floor can leave the output a hair over target.
    # Derive the fitting bitrate DIRECTLY from duration (deterministic) rather
    # than scaling a guessed base, and allow dropping below the quality floor —
    # the user asked for a hard size ceiling.
    _final_bps = int(best_ok[1]) if best_ok else int(target_bps)
    if encoder != "flac":
        for _g in range(3):
            _cur_sz = os.path.getsize(final_out)
            if _cur_sz <= target_bytes:
                break
            _needed = int((max(1, target_bytes - _cover_reserve) * 8 / max(1.0, duration)) * (0.96 - 0.03 * _g))
            _needed = max(16_000, min(_needed, _final_bps - 3_000))
            if _needed < 16_000 or _needed >= _final_bps:
                break  # can't shrink further without going sub-usable
            _final_bps = _needed
            _gtmp = os.path.join(save_path, f"._bc_ceil_{_g}.{ext}")
            try:
                if os.path.exists(_gtmp):
                    os.remove(_gtmp)
                _okg, _szg, _ = _encode_audio_once(
                    input_path, _gtmp, encoder=encoder, bitrate_bps=_final_bps,
                    sr=out_sr, channels=out_ch, vbr_mode=vbr_mode, loudnorm=loudnorm,
                    highpass_hz=hp, lowpass_hz=lp,
                    opus_cover_meta=_opus_cover_meta, cover_file=_cover_path)
                if _okg and 0 < _szg <= target_bytes:
                    os.replace(_gtmp, final_out)
                    break
                elif os.path.exists(_gtmp):
                    os.remove(_gtmp)
            except Exception:
                pass

    _rmtree_quiet(_audio_work_dir)
    for _p in (tmp_out, best_out):
        try:
            if os.path.exists(_p):
                os.remove(_p)
        except Exception:
            pass
    took = time.time() - t0
    fin_size = os.path.getsize(final_out)
    _q_label = _audio_transparency_label(encoder, _final_bps, out_ch)
    status_callback(f"Audio compressed to {format_bytes(fin_size)} in {took:.1f}s "
                    f"(≈{_final_bps//1000} kbps {encoder.replace('lib','')}, quality: {_q_label})")
    # Dedicated quality line so the plain-language Progress feed shows an audio
    # quality readout too (video gets one from VMAF; audio's is a label).
    status_callback(f"Audio quality: {_q_label}")

    stats = {
        "filename": os.path.basename(final_out),
        "original_size": os.path.getsize(input_path),
        "compressed_size": fin_size,
        "ratio": fin_size / max(1, os.path.getsize(input_path)),
        "time_taken": took,
        "output_path": final_out,
        "audio_bitrate": _final_bps,
        "quality_label": _q_label,
    }

    _jsonl_log("encode_end", {"type": "audio", **stats})



    if webhook_url:
        _post_webhook_hardened(webhook_url, json_payload=stats, file_path=final_out)

    return stats



def compress_image(input_path: str, save_path: str, status_callback,
                   target_size_mb: int, webhook_url: str,
                   advanced_options: dict, cancel_callback) -> dict:
    status_callback(f"Compressing image: {input_path}")
    t_start = time.time()
    try:
        _v = float(target_size_mb)

        target_size_bytes = int(_v) if _v >= (128 * 1024) else int(_v * 1024 * 1024)
        target_size_bytes = max(1, target_size_bytes)
    except Exception:
        target_size_bytes = 10 * 1024 * 1024
    target_size_bytes = apply_target_size_margin(target_size_bytes)
    filename = os.path.basename(input_path)
    name, _ = os.path.splitext(filename)
    out_prefix = advanced_options.get("output_prefix", "")
    out_suffix = advanced_options.get("output_suffix", "")
    image_format = advanced_options.get("image_format", "jpg")
    if advanced_options.get("auto_jpeg"):
        image_format = "jpg"
    output_file = _bc_build_output_path(input_path, save_path, advanced_options, default_ext=image_format)

    try:
        im = Image.open(input_path)

        try:
            is_anim = bool(getattr(im, "is_animated", False)) and int(getattr(im, "n_frames", 1)) > 1
        except Exception:
            is_anim = False

        if is_anim:
            status_callback("Animated image detected -> routing to video compressor.", level="INFO")
            try:
                im.close()
            except Exception:
                pass
            v_adv = dict(advanced_options or {})
            v_adv.setdefault("container", "mp4")
            v_adv.setdefault("encoder", "x264")
            return compress_video(
                input_path, save_path, status_callback, target_size_mb, webhook_url, v_adv, cancel_callback
            )
    except Exception as e:
        status_callback("Could not open image: " + str(e), level="ERROR")
        return {}


    quality = 85
    attempts = 0
    success = False
    temp_output = None

    while attempts < ITERATIVE_MAX_ATTEMPTS:
        if cancel_callback():
            status_callback("Image compression cancelled.", level="WARNING")
            return {}

        temp_output = output_file + ".tmp"
        fmt = (image_format or "jpg").lower()
        if fmt in {"jpg", "jpeg", "png", "webp"}:
            temp_output = output_file + ".tmp." + fmt

        if fmt == "avif":
            temp_output = output_file + "_tmp.avif"

            low, high = 24, 50
            best = None
            while low <= high:
                mid = (low + high)//2
                if cancel_callback():
                    status_callback("Image compression cancelled.", level="WARNING"); return {}
                if os.path.exists(temp_output):
                    try: os.remove(temp_output)
                    except Exception: pass
                cmd = [FFMPEG, "-y", "-i", input_path, "-frames:v", "1", "-c:v", "libaom-av1", "-still-picture", "1",
                       "-crf", str(mid), "-b:v", "0", temp_output]
                p = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=si, creationflags=NO_WIN)
                if p.returncode != 0 or not os.path.exists(temp_output):
                    status_callback(f"AVIF encode failed at CRF {mid}", level="ERROR"); return {}
                size = os.path.getsize(temp_output)
                status_callback(f"AVIF trial CRF={mid} -> {format_bytes(size)} (target {format_bytes(target_size_bytes)})")
                if size <= target_size_bytes:
                    best = mid; break
                low = mid + 1
            if best is None:

                best = min(max(low, 24), 58)

            try:
                if os.path.exists(output_file): os.remove(output_file)
            except Exception:
                pass
            os.replace(temp_output, output_file)
            took = time.time() - t_start
            fin = os.path.getsize(output_file)
            stats = {"filename": os.path.basename(output_file), "original_size": os.path.getsize(input_path),
                     "compressed_size": fin, "ratio": fin/max(1, os.path.getsize(input_path)), "time_taken": took, "output_path": output_file}
            _jsonl_log("encode_end", {"type":"image_avif", **stats})
            if webhook_url: _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
            status_callback(f"Image (AVIF) compressed to {format_bytes(fin)} in {took:.1f}s")
            return stats
        pil_fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(fmt, fmt.upper())
        im.convert("RGB").save(temp_output, pil_fmt, quality=quality, optimize=True)
        size = os.path.getsize(temp_output)
        status_callback(
            f"Image size: {format_bytes(size)} at quality {quality} "
            f"(target {format_bytes(max(1, int(target_size_bytes)))})"
        )




        if size <= target_size_bytes:
            success = True
            try:
                if os.path.exists(output_file):
                    os.remove(output_file)
            except Exception:
                pass
            os.replace(temp_output, output_file)
            took = time.time() - t_start
            fin = os.path.getsize(output_file)
            stats = {"filename": os.path.basename(output_file), "original_size": os.path.getsize(input_path),
                     "compressed_size": fin, "ratio": fin/max(1, os.path.getsize(input_path)),
                     "time_taken": took, "output_path": output_file}
            _jsonl_log("encode_end", {"type": "image", **stats})
            if webhook_url:
                _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
            status_callback(f"Image compressed to {format_bytes(fin)} in {took:.1f}s")
            return stats
        quality = max(10, quality - 10)
        attempts += 1

    if not success:
        status_callback("Image compression failed.", level="ERROR")
        if temp_output and os.path.exists(temp_output):
            os.remove(temp_output)
        return {}

    if advanced_options.get("guetzli") and image_format.lower() == "jpg":
        guetzli_out = output_file + "_guetzli"
        try:
            res = _sp_run(["guetzli", temp_output, guetzli_out], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=si, creationflags=NO_WIN)
            if res.returncode == 0:
                os.replace(guetzli_out, output_file)
                status_callback("Guetzli optimization applied.")
            else:
                status_callback("Guetzli failed: " + res.stderr.strip(), level="WARNING")
        except Exception as e:
            status_callback("Guetzli error: " + str(e), level="WARNING")
    elif advanced_options.get("pngopt") and image_format.lower() == "png":
        pngquant_out = output_file + "_pngquant.png"
        try:

            res1 = _sp_run(
                ["pngquant", "--quality=65-80", "--speed", "1", temp_output, "--output", pngquant_out],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=si, creationflags=NO_WIN
            )

            if res1.returncode == 0:

                res2 = _sp_run(
                    ["zopflipng", "--iterations=500", "--lossy_transparent", "--lossy_8bit", pngquant_out, output_file],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=si, creationflags=NO_WIN
                )

                if res2.returncode == 0:
                    status_callback("PNGQuant + Zopfli optimization applied.")
                else:
                    status_callback("Zopfli failed: " + res2.stderr.strip(), level="WARNING")
            else:
                status_callback("PNGQuant failed: " + res1.stderr.strip(), level="WARNING")
        except Exception as e:
            status_callback("PNGQuant/Zopfli error: " + str(e), level="WARNING")
    else:

        shutil.move(temp_output, output_file)


    t_end = time.time()
    final_size = os.path.getsize(output_file)
    status_callback(f"Image compressed: {format_bytes(final_size)}")

    stats = {
        "filename": filename,
        "original_size": os.path.getsize(input_path),
        "compressed_size": final_size,
        "ratio": final_size / os.path.getsize(input_path),
        "time_taken": t_end - t_start,
        "output_path": output_file
    }

    _jsonl_log("encode_end", {"type": "image", **stats})


    if webhook_url:
        _post_webhook_hardened(webhook_url, file_path=output_file)

    return stats

def _which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None

def compress_pdf(input_path: str, save_path: str, status_callback,
                 target_size_mb: int, webhook_url: str,
                 advanced_options: dict, cancel_callback) -> dict:
    

    adv = dict(advanced_options or {})
    status_callback(f"Compressing PDF: {input_path}")

    gs = _which("gswin64c", "gswin32c", "gs")
    if not gs:
        status_callback("Ghostscript not found. Install Ghostscript or add it to PATH.", level="ERROR")
        return {}

    try:
        os.makedirs(save_path, exist_ok=True)
    except Exception:
        pass

    src_size = os.path.getsize(input_path)
    target_bytes = max(1, int(target_size_mb * 1024 * 1024))
    target_bytes = apply_target_size_margin(target_bytes)
    tol = float(adv.get("pdf_tolerance", 0.10))            # ±10%
    min_dpi = int(adv.get("pdf_min_dpi", 90))
    max_dpi = int(adv.get("pdf_max_dpi", 300))
    linearize_if_close = bool(adv.get("pdf_linearize_if_close", True))

    filename = os.path.basename(input_path)
    name, _ = os.path.splitext(filename)
    out_prefix = adv.get("output_prefix", "")
    out_suffix = adv.get("output_suffix", "")
    output_file = os.path.join(save_path, f"{out_prefix}{name}{out_suffix}.pdf")

    if src_size <= int(target_bytes * (1.0 + tol)) and linearize_if_close:
        try:
            if os.path.abspath(input_path) != os.path.abspath(output_file):
                shutil.copy2(input_path, output_file)
        except Exception:
            output_file = input_path
        stats = {
            "filename": os.path.basename(output_file),
            "original_size": src_size,
            "compressed_size": os.path.getsize(output_file),
            "ratio": os.path.getsize(output_file) / max(1, src_size),
            "time_taken": 0.0,
            "output_path": output_file,
            "note": "Kept original (already near target).",
        }
        _jsonl_log("encode_end", {"type": "pdf", **stats})
        if webhook_url:
            _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
        status_callback(f"PDF already near target - kept original ({format_bytes(stats['compressed_size'])}).")
        return stats

    def _pdfset_for_dpi(dpi_val: int) -> str:
        if dpi_val <= 120:
            return "/screen"
        if dpi_val <= 180:
            return "/ebook"
        return "/printer"

    def _gs_trial(out_path: str, dpi_val: int) -> tuple[bool, int]:
        pdfset = _pdfset_for_dpi(dpi_val)
        cmd = [
            gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6",
            "-dDetectDuplicateImages=true",
            "-dDownsampleColorImages=true", "-dColorImageDownsampleType=/Bicubic", f"-dColorImageResolution={dpi_val}",
            "-dDownsampleGrayImages=true",  "-dGrayImageDownsampleType=/Bicubic",  f"-dGrayImageResolution={dpi_val}",
            "-dDownsampleMonoImages=true",  "-dMonoImageDownsampleType=/Subsample", "-dMonoImageResolution=300",
            "-dAutoRotatePages=/None", "-dOptimize=true",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-dPDFSETTINGS={pdfset}",
            f"-sOutputFile={out_path}", input_path
        ]
        p = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=si, creationflags=NO_WIN)
        if p.returncode != 0 or (not os.path.exists(out_path)):

            job_log = adv.get("job_log")
            if job_log:
                try:
                    with open(job_log, "a", encoding="utf-8") as lf:
                        lf.write(" ".join(cmd) + "\n")
                        if p.stdout: lf.write(p.stdout + "\n")
                        if p.stderr: lf.write(p.stderr + "\n")
                except Exception:
                    pass
            return False, 0
        return True, os.path.getsize(out_path)

    try:
        with open(input_path, "rb") as f:
            head = f.read(2_000_000)
        looks_text_vector = (b"/Subtype /Image" not in head) and (b"/XObject" not in head)
    except Exception:
        looks_text_vector = False

    if looks_text_vector:
        want_raster_flag = adv.get("pdf_force_rasterize")
        want_raster = True if (want_raster_flag is None) else (str(want_raster_flag).lower() in {"1","true","yes"})
        need_shrink = target_bytes < int(src_size * 0.9)
        if want_raster and need_shrink:
            ok = _rasterize_pdf_to_target(
                input_path=input_path,
                output_file=output_file,
                target_bytes=target_bytes,
                gs=gs,
                status_callback=status_callback,
                adv=adv,
                cancel_callback=cancel_callback,
            )
            if ok:
                took = max(0.0, time.time() - os.path.getmtime(output_file))
                stats = {
                    "filename": os.path.basename(output_file),
                    "original_size": src_size,
                    "compressed_size": os.path.getsize(output_file),
                    "ratio": os.path.getsize(output_file) / max(1, src_size),
                    "time_taken": took,
                    "output_path": output_file,
                    "note": "Rasterized pages to hit target.",
                }
                _jsonl_log("encode_end", {"type": "pdf", **stats})
                if webhook_url:
                    _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
                status_callback(f"PDF rasterized to {format_bytes(stats['compressed_size'])} (target {format_bytes(target_bytes)})")
                return stats

        try:
            if os.path.abspath(input_path) != os.path.abspath(output_file):
                shutil.copy2(input_path, output_file)
        except Exception:
            output_file = input_path
        stats = {
            "filename": os.path.basename(output_file),
            "original_size": src_size,
            "compressed_size": os.path.getsize(output_file),
            "ratio": os.path.getsize(output_file) / max(1, src_size),
            "time_taken": 0.0,
            "output_path": output_file,
            "note": "Kept original: vector/text-only; rasterization disabled/unavailable.",
        }
        _jsonl_log("encode_end", {"type": "pdf", **stats})
        if webhook_url:
            _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
        status_callback("PDF appears text/vector-only - kept original to avoid size increase.")
        return stats

    probe_tmp = output_file + ".probe.pdf"
    trial_tmp = output_file + ".trial.pdf"
    for pth in (probe_tmp, trial_tmp):
        if os.path.exists(pth):
            try: os.remove(pth)
            except Exception: pass

    status_callback("Probing PDF @ 300 DPI (/printer)")
    ok_probe, probe_size = _gs_trial(probe_tmp, 300)
    if not ok_probe:
        status_callback("PDF probe failed; aborting PDF compression.", level="ERROR")
        return {}

    best_path = None
    best_size = None
    if probe_size < src_size and probe_size <= target_bytes:
        best_path = probe_tmp
        best_size = probe_size

    low, high = min_dpi, max_dpi
    TRIALS_MAX = 8

    for _ in range(TRIALS_MAX):
        if cancel_callback():
            status_callback("PDF compression cancelled.", level="WARNING")
            for pth in (probe_tmp, trial_tmp):
                try:
                    if os.path.exists(pth): os.remove(pth)
                except Exception: pass
            return {}

        mid = (low + high) // 2

        if 280 <= mid <= 320:
            ok, size = True, probe_size
            cur_path = probe_tmp
        else:
            if os.path.exists(trial_tmp):
                try: os.remove(trial_tmp)
                except Exception: pass
            status_callback(f"PDF trial @ {mid} DPI ({_pdfset_for_dpi(mid)})")
            ok, size = _gs_trial(trial_tmp, mid)
            cur_path = trial_tmp
            if not ok:
                high = max(min_dpi, mid - 10)
                continue

        if size >= src_size:
            high = max(min_dpi, mid - 10)
            continue

        if size <= target_bytes and (best_size is None or size < best_size):
            best_path, best_size = cur_path, size

        if target_bytes * (1.0 - tol) <= size <= target_bytes:
            best_path, best_size = cur_path, size
            break

        if size > target_bytes:
            high = max(min_dpi, mid - 10)
        else:
            low = min(max_dpi, mid + 10)

    if best_path is None:

        if probe_size < src_size:
            best_path, best_size = probe_tmp, probe_size
        else:
            try:
                if os.path.abspath(input_path) != os.path.abspath(output_file):
                    shutil.copy2(input_path, output_file)
            except Exception:
                output_file = input_path
            stats = {
                "filename": os.path.basename(output_file),
                "original_size": src_size,
                "compressed_size": os.path.getsize(output_file),
                "ratio": os.path.getsize(output_file) / max(1, src_size),
                "time_taken": 0.0,
                "output_path": output_file,
                "note": "Kept original: no beneficial recompress result.",
            }
            _jsonl_log("encode_end", {"type": "pdf", **stats})
            if webhook_url:
                _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
            status_callback("No beneficial PDF compression found - kept original.")
            for pth in (probe_tmp, trial_tmp):
                try:
                    if os.path.exists(pth): os.remove(pth)
                except Exception: pass
            return stats

    try:
        if os.path.exists(output_file):
            os.remove(output_file)
    except Exception:
        pass
    os.replace(best_path, output_file)

    for pth in (probe_tmp, trial_tmp):
        if pth != output_file:
            try:
                if os.path.exists(pth): os.remove(pth)
            except Exception:
                pass

    took = max(0.0, time.time() - os.path.getmtime(output_file))
    stats = {
        "filename": os.path.basename(output_file),
        "original_size": src_size,
        "compressed_size": os.path.getsize(output_file),
        "ratio": os.path.getsize(output_file) / max(1, src_size),
        "time_taken": took,
        "output_path": output_file,
    }
    _jsonl_log("encode_end", {"type": "pdf", **stats})
    if webhook_url:
        _post_webhook_hardened(webhook_url, json_payload=stats, file_path=output_file)
    status_callback(f"PDF compressed to {format_bytes(stats['compressed_size'])} (target {format_bytes(target_bytes)})")
    return stats



def _rasterize_pdf_to_target(input_path: str, output_file: str, target_bytes: int,
                             gs: str, status_callback, adv: dict, cancel_callback) -> bool:
    
    import tempfile, glob
    tmpdir = tempfile.mkdtemp(prefix="bc_pdf_rast_")
    try:
        dpi_min = int(adv.get("pdf_raster_min_dpi", 110))
        dpi_max = int(adv.get("pdf_raster_max_dpi", 220))
        q_min   = int(adv.get("pdf_raster_min_q", 60))
        q_max   = int(adv.get("pdf_raster_max_q", 90))
        max_iters = int(adv.get("pdf_raster_max_iters", 7))

        best_pdf = None
        best_size = None

        for _ in range(max_iters):
            if cancel_callback():
                status_callback("PDF rasterization cancelled.", level="WARNING")
                return False

            dpi = (dpi_min + dpi_max) // 2
            q   = (q_min + q_max) // 2

            img_pattern = os.path.join(tmpdir, "page-%06d.jpg")
            for f in glob.glob(os.path.join(tmpdir, "page-*.jpg")):
                try: os.remove(f)
                except Exception: pass
            status_callback(f"Raster trial DPI={dpi}, Q={q}")
            cmd = [gs, "-dNOPAUSE", "-dQUIET", "-dBATCH", "-sDEVICE=jpeg", f"-r{dpi}", f"-dJPEGQ={q}", "-o", img_pattern, input_path]
            p = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=si, creationflags=NO_WIN)
            if p.returncode != 0:
                status_callback("Ghostscript rasterization failed.", level="ERROR")
                return False

            jpgs = sorted(glob.glob(os.path.join(tmpdir, "page-*.jpg")))
            if not jpgs:
                status_callback("No pages rendered during rasterization.", level="ERROR")
                return False

            images = []
            for j in jpgs:
                im = Image.open(j)
                if im.mode != "RGB":
                    im = im.convert("RGB")
                images.append(im.copy())
                im.close()

            tmp_pdf = output_file + ".tmp.pdf"
            try:
                if os.path.exists(tmp_pdf):
                    os.remove(tmp_pdf)
            except Exception:
                pass
            if len(images) == 1:
                images[0].save(tmp_pdf, "PDF", resolution=72.0, save_all=False)
            else:
                images[0].save(tmp_pdf, "PDF", resolution=72.0, save_all=True, append_images=images[1:])
            for im in images:
                try: im.close()
                except Exception: pass

            size = os.path.getsize(tmp_pdf)
            status_callback(f"Raster result -> {format_bytes(size)} (target {format_bytes(target_bytes)})")

            if size <= target_bytes and (best_size is None or size < best_size):
                best_pdf, best_size = tmp_pdf, size
                dpi_min = min(dpi_max, dpi + 10)
                q_min   = min(q_max, q + 5)
            else:
                dpi_max = max(dpi_min, dpi - 10)
                q_max   = max(q_min, q - 5)

        if best_pdf and os.path.exists(best_pdf):
            try:
                if os.path.exists(output_file):
                    os.remove(output_file)
            except Exception:
                pass
            os.replace(best_pdf, output_file)
            return True
        return False
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass




def settings_window():
    win = Toplevel()
    win.title("Settings")
    win.geometry("300x200")
    Label(win, text="Settings go here").pack(pady=20)
    Button(win, text="Close", command=win.destroy).pack(pady=10)


# =====================================================================
# Trim-aware compression ("don't encode the boring parts")
# =====================================================================
# Every encoder-side trick fights for single-digit quality-per-bit percentages;
# cutting duration is a 2-10x lever (half the clip = double the bitrate under
# the same cap). A trim range produces a stream-copied intermediate (fast, zero
# quality loss) that the whole pipeline then consumes unchanged — features,
# codec race, preprocessing, VMAF and packing all operate on the trimmed
# content automatically. The source file is never modified.

def _parse_timespec(s: str) -> float:
    """'SS(.d)', 'MM:SS(.d)' or 'HH:MM:SS(.d)' -> seconds. Raises ValueError."""
    t = str(s or "").strip()
    if not t:
        raise ValueError("empty time")
    parts = t.split(":")
    if len(parts) > 3 or any(p.strip() == "" for p in parts):
        raise ValueError(f"bad time '{s}'")
    try:
        nums = [float(p) for p in parts]
    except Exception:
        raise ValueError(f"bad time '{s}'")
    secs = 0.0
    for n in nums:
        secs = secs * 60.0 + n
    if secs < 0:
        raise ValueError(f"negative time '{s}'")
    return secs


def _parse_trim_range(s: str) -> tuple[float, float]:
    """'START-END' (each SS / MM:SS / HH:MM:SS) -> (start_s, end_s)."""
    t = str(s or "").strip()
    if "-" not in t:
        raise ValueError("expected START-END (e.g. 1:42-2:05)")
    a_raw, b_raw = t.split("-", 1)
    a, b = _parse_timespec(a_raw), _parse_timespec(b_raw)
    if b <= a:
        raise ValueError(f"end ({b_raw.strip()}) must be after start ({a_raw.strip()})")
    return a, b


def _prev_keyframe_time(input_path: str, t: float) -> float:
    """
    The last video keyframe at or before t (seconds). Stream-copy trims can only
    start on a keyframe; snapping backward gives a short lead-in instead of a
    broken first GOP. Reads only a few seconds of the file (read_intervals).
    Falls back to a 2.5s-early guess when probing fails.
    """
    if t <= 0.1:
        return 0.0
    try:
        lo = max(0.0, t - 8.0)
        out = _sp_check_output(
            [FFPROBE, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
             "-show_entries", "frame=pts_time", "-of", "csv=p=0",
             "-read_intervals", f"{lo:.3f}%{t + 0.5:.3f}", input_path],
            text=True, startupinfo=si, creationflags=NO_WIN)
        kfs = []
        for ln in (out or "").splitlines():
            ln = ln.strip().rstrip(",")
            if ln:
                try:
                    kfs.append(float(ln))
                except Exception:
                    pass
        prior = [k for k in kfs if k <= t + 0.001]
        if prior:
            return max(0.0, max(prior))
    except Exception:
        pass
    return max(0.0, t - 2.5)


def make_trim_intermediate(input_path: str, start_s: float, end_s: float, *,
                           fade: bool = False, media_type: str = "video",
                           status_cb=None) -> tuple[str, str] | None:
    """
    Cut [start_s, end_s] of the source into a temp intermediate the pipeline
    compresses instead of the full file. Returns (intermediate_path, temp_dir)
    — caller removes temp_dir when done — or None on failure.

    Default: stream copy (fast, zero generation loss). Video starts snap back
    to the previous keyframe (a short lead-in); the end is exact.
    fade=True: frame-exact re-encode at visually-lossless settings with a 0.5s
    audio+video fade at both ends (the polish mode for music/rhythm clips).
    """
    def _say(msg, level="INFO"):
        if callable(status_cb):
            try:
                status_cb(msg, level)
            except Exception:
                pass

    try:
        stem, ext = os.path.splitext(os.path.basename(input_path))
        work = tempfile.mkdtemp(prefix="bc_trim_")
        _FADE = 0.5

        if fade:
            dur = max(0.2, end_s - start_s)
            _af = (f"afade=t=in:st=0:d={_FADE},"
                   f"afade=t=out:st={max(0.0, dur - _FADE):.3f}:d={_FADE}")
            if media_type == "audio":
                out = os.path.join(work, f"{stem}_clip.flac")
                cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                       "-ss", f"{start_s:.3f}", "-i", input_path, "-t", f"{dur:.3f}",
                       "-af", _af, "-c:a", "flac", "-map_metadata", "0", out]
            else:
                out = os.path.join(work, f"{stem}_clip.mp4")
                _vf = (f"fade=t=in:st=0:d={_FADE},"
                       f"fade=t=out:st={max(0.0, dur - _FADE):.3f}:d={_FADE}")
                cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                       "-ss", f"{start_s:.3f}", "-i", input_path, "-t", f"{dur:.3f}",
                       "-vf", _vf, "-af", _af,
                       "-c:v", "libx264", "-crf", "12", "-preset", "veryfast",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
                       "-movflags", "+faststart", out]
            _say(f"[Trim] Cutting {start_s:.1f}s-{end_s:.1f}s frame-exact with "
                 f"{_FADE:.1f}s fades (near-lossless re-encode).")
        else:
            if media_type == "audio":
                cut_from = start_s          # audio frames cut cleanly, no keyframes
                maps = ["-map", "0"]
                out = os.path.join(work, f"{stem}_clip{ext or '.mka'}")
            else:
                cut_from = _prev_keyframe_time(input_path, start_s)
                maps = ["-map", "0:v:0?", "-map", "0:a?", "-sn", "-dn"]
                out = os.path.join(work, f"{stem}_clip{ext or '.mp4'}")
            dur = max(0.2, end_s - cut_from)
            cmd = ([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{cut_from:.3f}", "-i", input_path, "-t", f"{dur:.3f}"]
                   + maps + ["-c", "copy", "-ignore_unknown",
                             "-avoid_negative_ts", "make_zero", out])
            lead = start_s - cut_from
            _say(f"[Trim] Cutting {start_s:.1f}s-{end_s:.1f}s via stream copy"
                 + (f" (start snapped to keyframe, {lead:.1f}s lead-in)" if lead > 0.05 else "")
                 + " - zero quality loss.")

        proc = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, startupinfo=si, creationflags=NO_WIN)
        if getattr(proc, "returncode", 1) == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            return out, work
        _rmtree_quiet(work)
        return None
    except Exception:
        return None


# ---- Trim suggestion markers (audio-energy analysis) ------------------------
# Offline heuristic ASSISTANT for picking a trim range: windowed RMS loudness
# per audio track, z-scored per track so a quiet mic and loud game audio are
# comparable. Track 2+ (the OBS/ShadowPlay mic convention) is weighted higher —
# a mic spike over steady game audio is the best available "something just
# happened" signal. These are suggestions, never automatic cuts: semantically
# important-but-silent moments (a great play in a card game) are undetectable
# by any signal analysis, so manual trim stays the primary interface.

def _fmt_ts(seconds: float) -> str:
    s = max(0, int(round(float(seconds))))
    return f"{s // 60}:{s % 60:02d}"


def _rank_energy_windows(tracks: list, win_s: float, clip_seconds: float,
                         top_n: int = 3, mic_weight: float = 1.5,
                         total_s: float | None = None) -> list[dict]:
    """
    Rank candidate clip windows from per-track RMS series (one value per win_s).
    Pure logic (testable): z-score each track, weight non-primary tracks (mic)
    higher, pick the strongest well-separated peaks, and cut a clip_seconds
    window around each (35% lead-in / 65% follow-through).
    Returns [{"start","end","score","track"}] sorted by score, strongest first.
    """
    tracks = [list(t) for t in (tracks or []) if t]
    if not tracks:
        return []
    n = min(len(t) for t in tracks)
    if n < 3:
        return []

    zs = []
    for t in tracks:
        vals = t[:n]
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        std = var ** 0.5
        zs.append([((v - mean) / std) if std > 1e-9 else 0.0 for v in vals])

    combined = []
    track_of = []
    for i in range(n):
        best, bt = None, 0
        for ti, z in enumerate(zs):
            w = mic_weight if ti >= 1 else 1.0
            v = w * z[i]
            if best is None or v > best:
                best, bt = v, ti
        combined.append(best)
        track_of.append(bt)
    # Light smoothing so a single hot window doesn't outrank a sustained burst.
    sm = [(combined[max(0, i - 1)] + combined[i] + combined[min(n - 1, i + 1)]) / 3.0
          for i in range(n)]

    total = float(total_s if total_s is not None else n * win_s)
    min_sep = max(1, int(round((clip_seconds * 0.8) / win_s)))
    order = sorted(range(n), key=lambda i: -sm[i])
    picked: list[int] = []
    for i in order:
        if sm[i] < 0.75:      # below a meaningful spike; don't invent highlights
            break
        if all(abs(i - p) >= min_sep for p in picked):
            picked.append(i)
        if len(picked) >= top_n:
            break

    out = []
    for i in picked:
        t = i * win_s
        start = max(0.0, t - 0.35 * clip_seconds)
        end = min(total, start + clip_seconds)
        start = max(0.0, end - clip_seconds)
        # Attribute the track at the RAW peak near the picked (smoothed) index —
        # smoothing can tie-shift the pick one window off the spike, where the
        # baseline noise of another track would mislabel the source.
        j = max(range(max(0, i - 1), min(n, i + 2)), key=lambda x: combined[x])
        out.append({"start": round(start, 1), "end": round(end, 1),
                    "score": round(sm[i], 2), "track": track_of[j]})
    return out


def _audio_energy_tracks(input_path: str, n_tracks: int, win_s: float = 0.5) -> list:
    """Windowed RMS per audio track via a mono 8 kHz PCM decode (tiny + fast)."""
    out = []
    sr = 8000
    for ti in range(max(1, int(n_tracks))):
        try:
            cmd = [FFMPEG, "-v", "error", "-i", input_path, "-map", f"0:a:{ti}",
                   "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
            r = _sp_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        startupinfo=si, creationflags=NO_WIN)
            raw = getattr(r, "stdout", b"") or b""
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            w = int(sr * win_s)
            k = len(data) // w
            if k <= 0:
                out.append([])
                continue
            rms = np.sqrt(np.mean(np.square(data[:k * w].reshape(k, w)), axis=1))
            out.append([float(v) for v in rms])
        except Exception:
            out.append([])
    return [t for t in out if t] or []


def suggest_trim_ranges(input_path: str, *, clip_seconds: float = 20.0,
                        top_n: int = 3, status_cb=None) -> list[dict]:
    """
    Suggest up to top_n candidate trim ranges from audio energy. Adds a human
    "why" to each. Returns [] when there is nothing meaningful to point at
    (silent/uniform audio) — the caller should say so, never guess.
    """
    try:
        mt = get_media_type(input_path)
        if mt == "video":
            dur = float(get_video_metadata(input_path)[0] or 0.0)
        elif mt == "audio":
            dur = float((_probe_audio_meta(input_path) or {}).get("duration") or 0.0)
        else:
            return []
    except Exception:
        dur = 0.0
    if dur <= clip_seconds * 1.2:
        return []       # nothing to cut away
    n_a = _count_audio_streams(input_path)
    if n_a <= 0:
        if callable(status_cb):
            status_cb("[Suggest] No audio track to analyze - set the trim manually.", "INFO")
        return []
    win_s = 0.5
    tracks = _audio_energy_tracks(input_path, min(n_a, 3), win_s=win_s)
    cands = _rank_energy_windows(tracks, win_s, float(clip_seconds),
                                 top_n=top_n, total_s=dur)
    for c in cands:
        c["why"] = ("mic/track-2 spike" if c.get("track", 0) >= 1 else "audio energy peak")
        c["range"] = f"{_fmt_ts(c['start'])}-{_fmt_ts(c['end'])}"
    if callable(status_cb):
        if cands:
            status_cb("[Suggest] Candidate moments: "
                      + "; ".join(f"{c['range']} ({c['why']}, score {c['score']})"
                                  for c in cands), "INFO")
        else:
            status_cb("[Suggest] No clear audio peaks found - set the trim manually.", "INFO")
    return cands


# ---- Spotlight mode (keep the full video, boost the moment) ------------------
# For "I can't cut context but THIS range must look good": an x264/x265 rate-
# control zone boosts the marked range; the rest of the video pays for it under
# the same cap. Zones exist only for x264/x265, and x264 zones must be disjoint,
# so a Spotlight file pins its codec and takes over zoning (scene-zones off).

_SPOTLIGHT_BOOST = 1.5
_X264_BASE_PARAMS = ("aq-mode=3:aq-strength=1.00:mbtree=1:deblock=-1,-1:psy-rd=1.10,0.15:"
                     "rc-lookahead=80:qcomp=0.70:ipratio=1.30:pbratio=1.20:trellis=2:bframes=8:ref=5")
_X265_BASE_PARAMS = "rc-lookahead=80:bframes=8:ref=5"


def _spotlight_zone_params(existing: str, start_s: float, end_s: float,
                           fps_eff: float, duration_s: float, encoder: str) -> tuple[str, str]:
    """
    Build the (params_key, params_value) carrying the Spotlight zone for the
    given encoder. Any zones already present in `existing` are stripped (x264
    zones must be disjoint; Spotlight owns zoning for this file); when there are
    no existing params the encoder's standard base params are used so the boost
    doesn't silently discard the tuned defaults.
    """
    is265 = str(encoder or "").lower() in ("x265", "libx265", "hevc")
    key = "x265_params" if is265 else "x264_params"
    fps_eff = max(1.0, float(fps_eff or 30.0))
    dur = float(duration_s or end_s)
    a = max(0.0, min(float(start_s), dur))
    b = max(a + (1.0 / fps_eff), min(float(end_s), dur))
    fs = int(a * fps_eff)
    fe = max(fs + 1, int(b * fps_eff))
    zone = f"zones={fs},{fe},b={_SPOTLIGHT_BOOST:g}"
    base = str(existing or "").strip()
    if not base:
        base = _X265_BASE_PARAMS if is265 else _X264_BASE_PARAMS
    base = ":".join(p for p in base.split(":") if p and not p.startswith("zones="))
    return key, (base + ":" + zone if base else zone)


def auto_compress(input_path: str, save_path: str, status_callback,
                  target_size_mb: int, webhook_url: str,
                  advanced_options: dict, cancel_callback) -> dict:
    

    try:
        opts = dict(advanced_options or {})
        unit = str(opts.get("size_unit", "MB")).upper()
        value = float(target_size_mb)

        # Most internal callers pass raw bytes already. Respect explicit flag,
        # and use a heuristic fallback for legacy call sites.
        if bool(opts.get("_target_is_bytes", False)) or value >= (128 * 1024):
            target_bytes = int(max(1, value))
        else:
            target_bytes = bytes_from_value_unit(value, unit)

        _target_mb = max(0.0001, float(target_bytes) / float(1024 ** 2))
    except Exception:
        _target_mb = 10.0  # fallback default


    media_type = get_media_type(input_path)
    _orig_input = input_path
    _trim_dir = None

    # Trim-aware compression: cut the requested range into a stream-copied
    # intermediate and let the ENTIRE pipeline (features, codec race, preproc,
    # VMAF, packing) consume the trimmed content transparently. The source
    # file is never modified.
    _tr_raw = (advanced_options or {}).get("trim_range")
    if _tr_raw and (advanced_options or {}).get("spotlight_range"):
        status_callback("[Trim] Both trim and spotlight are set - trim wins; "
                        "spotlight ignored for this file.", level="WARNING")
        try:
            advanced_options.pop("spotlight_range", None)
        except Exception:
            pass
    if _tr_raw and media_type in ("video", "audio"):
        try:
            _a, _b = _parse_trim_range(str(_tr_raw))
        except ValueError as _te:
            status_callback(f"[Trim] Invalid trim range '{_tr_raw}' ({_te}); "
                            f"compressing the full file.", level="WARNING")
            _a = _b = None
        if _a is not None:
            _res = make_trim_intermediate(
                input_path, _a, _b,
                fade=bool((advanced_options or {}).get("trim_fade")),
                media_type=media_type, status_cb=status_callback)
            if _res:
                input_path, _trim_dir = _res
                _jsonl_log("trim", {"input": _orig_input, "start": _a, "end": _b,
                                    "fade": bool((advanced_options or {}).get("trim_fade")),
                                    "intermediate_bytes": os.path.getsize(input_path)})
            else:
                status_callback("[Trim] Could not create the trimmed intermediate; "
                                "compressing the full file.", level="WARNING")
    elif _tr_raw:
        status_callback(f"[Trim] Trim is not applicable to {media_type} files; ignored.",
                        level="DEBUG")

    try:
        return _auto_compress_dispatch(
            media_type, input_path, _orig_input, save_path, status_callback,
            _target_mb, webhook_url, advanced_options, cancel_callback)
    finally:
        _rmtree_quiet(_trim_dir)


def _auto_compress_dispatch(media_type, input_path, _orig_input, save_path,
                            status_callback, _target_mb, webhook_url,
                            advanced_options, cancel_callback) -> dict:
    if media_type == "video":
        return compress_video(input_path, save_path, status_callback,
                              _target_mb, webhook_url,
                              advanced_options, cancel_callback)
    elif media_type == "audio":
        _astats = compress_audio(input_path, save_path, status_callback,
                                 _target_mb, webhook_url,
                                 advanced_options, cancel_callback)
        # Lyric embedding: if a .lrc sits next to the source and the feature is on,
        # fold it into the output tags. Single chokepoint so it covers every audio
        # path (passthrough, lossless FLAC, lossy re-encode) uniformly. Offline.
        # (Looked up next to the ORIGINAL file — a trim intermediate lives in a
        # temp dir with no sibling .lrc.)
        try:
            if bool((advanced_options or {}).get(
                    "embed_lyrics", ADVANCED_DEFAULTS.get("embed_lyrics", True))):
                _lrc = _read_sibling_lrc(_orig_input)
                _out = (_astats or {}).get("output_path") if isinstance(_astats, dict) else None
                if _lrc and _out and os.path.isfile(_out):
                    if _embed_lyrics_into(_out, _lrc, status_callback):
                        try:
                            _astats["compressed_size"] = os.path.getsize(_out)
                            _astats["lyrics_embedded"] = True
                        except Exception:
                            pass
        except Exception:
            pass
        return _astats
    elif media_type == "image":
        return compress_image(input_path, save_path, status_callback,
                              _target_mb, webhook_url,
                              advanced_options, cancel_callback)
    elif media_type == "document":
        return compress_pdf(input_path, save_path, status_callback,
                            _target_mb, webhook_url,
                            advanced_options, cancel_callback)

    else:
        status_callback(f"Unsupported file type: {input_path}", level="ERROR")
        return {}


class QueueTree(ttk.Treeview):
    """
    Treeview-based job queue with a Listbox-compatible facade.

    Historical code treats the queue as a tk.Listbox holding full paths
    (insert/get/delete/size/curselection/...). This widget keeps that exact
    API so every existing call site works unchanged, while presenting a rich
    multi-column view (status, progress, ETA, size, VMAF). Row identity:
    iid == the normalized full path.
    """

    COLS = ("status", "progress", "eta", "size", "vmaf")

    def __init__(self, parent, **kw):
        kw.pop("selectmode", None)
        # Swallow Listbox-only construction options.
        for _lb_only in ("bg", "fg", "highlightthickness", "borderwidth", "bd",
                         "activestyle", "relief", "selectbackground", "selectforeground"):
            kw.pop(_lb_only, None)
        super().__init__(parent, columns=self.COLS, show="tree headings",
                         selectmode="extended", **kw)
        self.heading("#0", text="File")
        self.column("#0", width=150, minwidth=100, stretch=True)
        for col, txt, w, mw, anchor, stretch in (
            ("status", "Status", 108, 72, "w", False),
            ("progress", "%", 40, 36, "e", False),
            ("eta", "ETA", 52, 44, "e", False),
            ("size", "Size", 72, 56, "e", False),
            ("vmaf", "VMAF", 54, 44, "e", False),
        ):
            self.heading(col, text=txt)
            self.column(col, width=w, minwidth=mw, stretch=stretch, anchor=anchor)
        # Status-tinted rows.
        try:
            self.tag_configure("done", foreground="#3DDC97")
            self.tag_configure("failed", foreground="#FF6B6B")
            self.tag_configure("active", foreground="#58A6FF")
        except Exception:
            pass

    # ---- Listbox-compatible facade -------------------------------------
    def _iids(self) -> list:
        return list(self.get_children(""))

    def insert(self, index="end", item=None, **kw):
        if item is None and kw:
            # Treeview-native call-through (not used by queue code).
            return super().insert("", index, **kw)
        path = str(item)
        if self.exists(path):
            return path
        pos = "end" if index in ("end", None) else index
        return super().insert("", pos, iid=path,
                              text=(os.path.basename(path) or path),
                              values=("pending", "", "", "", ""))

    def get(self, first, last=None):
        iids = self._iids()
        try:
            i = int(first)
        except (TypeError, ValueError):
            return str(first)
        if last is None:
            return iids[i] if 0 <= i < len(iids) else ""
        j = len(iids) - 1 if last == "end" else int(last)
        return tuple(iids[i:j + 1])

    def size(self) -> int:
        return len(self._iids())

    def curselection(self) -> tuple:
        iids = self._iids()
        sel = self.selection()
        return tuple(iids.index(s) for s in sel if s in iids)

    def selection_clear(self, first=None, last=None):
        try:
            self.selection_remove(*self.selection())
        except Exception:
            pass

    def selection_set(self, first, last=None):
        iids = self._iids()
        try:
            i = int(first)
            if 0 <= i < len(iids):
                super().selection_set(iids[i])
        except (TypeError, ValueError):
            super().selection_set(first)

    def activate(self, index):
        iids = self._iids()
        try:
            i = int(index)
            if 0 <= i < len(iids):
                self.focus(iids[i])
        except (TypeError, ValueError):
            self.focus(index)

    def nearest(self, y) -> int:
        iid = self.identify_row(y)
        iids = self._iids()
        return iids.index(iid) if iid in iids else -1

    def delete(self, first=None, last=None):
        iids = self._iids()
        if first is None:
            return
        if isinstance(first, str) and not str(first).isdigit() and self.exists(first):
            return super().delete(first)
        try:
            i = int(first)
        except (TypeError, ValueError):
            return
        if last is None:
            if 0 <= i < len(iids):
                super().delete(iids[i])
            return
        j = len(iids) - 1 if last == "end" else int(last)
        for iid in iids[i:j + 1]:
            try:
                super().delete(iid)
            except Exception:
                pass

    def configure(self, cnf=None, **kw):
        # Filter Listbox-only styling options that retheme code still sends.
        for _lb_only in ("bg", "fg", "background", "foreground", "highlightthickness",
                         "borderwidth", "bd", "activestyle", "relief",
                         "selectbackground", "selectforeground"):
            kw.pop(_lb_only, None)
            if isinstance(cnf, dict):
                cnf.pop(_lb_only, None)
        if cnf:
            return super().configure(cnf, **kw)
        return super().configure(**kw)

    config = configure

    # ---- Rich-row updates ----------------------------------------------
    def job_update(self, path: str, *, status=None, progress=None, eta=None,
                   size=None, vmaf=None):
        iid = str(path)
        if not self.exists(iid):
            return
        if status is not None:
            _st = _normalize_text(str(status))     # keep the queue column emoji-free
            self.set(iid, "status", _st)
            s = _st.lower()
            tag = ("done" if s.startswith("done") else
                   "failed" if s.startswith(("fail", "error")) else
                   "active" if s not in ("pending", "cancelled") else "")
            try:
                self.item(iid, tags=((tag,) if tag else ()))
            except Exception:
                pass
        if progress is not None:
            self.set(iid, "progress", (f"{float(progress):.0f}%" if progress != "" else ""))
        if eta is not None:
            self.set(iid, "eta", (f"~{int(eta)}s" if isinstance(eta, (int, float)) and eta > 0 else str(eta)))
        if size is not None:
            self.set(iid, "size", (human_bytes(int(size)) if isinstance(size, (int, float)) and size else str(size)))
        if vmaf is not None:
            self.set(iid, "vmaf", (f"{float(vmaf):.1f}" if isinstance(vmaf, (int, float)) else str(vmaf)))


class DinoRunner:
    """
    A tiny Chrome-style T-Rex endless runner on a Tk Canvas — pure eye-candy for
    the dead space in the Activity area, toggled from Advanced Options. Fully
    self-contained: it owns its animation loop and stops cleanly when hidden or
    destroyed. Space / Up / click to jump; run into a cactus and it's game over.
    """
    def __init__(self, parent, *, height=84, bg="#181820", fg="#c8c8d0", accent="#4caf7d"):
        import tkinter as tk
        self._tk = tk
        self.h = int(height)
        self.bg, self.fg, self.accent = bg, fg, accent
        self.canvas = tk.Canvas(parent, height=self.h, bg=bg, highlightthickness=0, bd=0)
        self.ground_y = self.h - 16
        self.running = False
        self._after = None
        self._w = 600
        self._reset()
        self.canvas.bind("<Configure>", lambda e: setattr(self, "_w", max(200, e.width)))
        for seq in ("<Button-1>", "<space>", "<Up>", "<KeyPress-space>", "<KeyPress-Up>"):
            self.canvas.bind(seq, self._jump)
        self.canvas.configure(takefocus=1)
        self.canvas.bind("<Enter>", lambda e: self.canvas.focus_set())

    def _reset(self):
        self.dino_y = 0.0      # vertical offset above the ground (<= 0 when airborne)
        self.vel = 0.0
        self.on_ground = True
        self.obstacles = []
        self.spawn = 30
        self.score = 0
        self.base_speed = 5.0
        self.speed = self.base_speed
        self.game_over = False
        self.legphase = 0
        # Retry linkage: BitCrusher's size-retry loop nudges this up (see
        # set_retry_pressure); the boost eases in/out and self-decays.
        self.retry_level = 0
        self._retry_boost = 0.0
        self._retry_decay = 0

    def set_retry_pressure(self, attempts):
        """Called from the GUI when the size-retry loop escalates on the current
        job. Each retry makes the runner a little faster — the harder BitCrusher
        fights the size cap, the harder the game gets — but it's capped so it
        stays beatable, and it decays back down once the retries stop."""
        try:
            n = max(0, int(attempts))
        except Exception:
            n = 0
        self.retry_level = max(self.retry_level, min(n, 8))
        self._retry_decay = 0

    def _jump(self, _e=None):
        try:
            self.canvas.focus_set()
        except Exception:
            pass
        if self.game_over:
            self._reset()
            return
        if self.on_ground:
            self.vel = -7.6
            self.on_ground = False

    def start(self):
        if self.running:
            return
        self.running = True
        self._loop()

    def stop(self):
        self.running = False
        if self._after is not None:
            try:
                self.canvas.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _loop(self):
        if not self.running:
            return
        try:
            self._update()
            self._draw()
        except Exception:
            pass
        try:
            self._after = self.canvas.after(33, self._loop)   # ~30 fps
        except Exception:
            self._after = None

    def _update(self):
        if self.game_over:
            return
        self.vel += 0.70                       # gravity
        self.dino_y += self.vel
        if self.dino_y >= 0:
            self.dino_y = 0.0
            self.vel = 0.0
            self.on_ground = True
        self.legphase = (self.legphase + 1) % 12
        # Ease the retry boost in/out, and slowly bleed off retry pressure so the
        # game relaxes once BitCrusher stops firing retries (~5s per level).
        self._retry_decay += 1
        if self._retry_decay >= 150:
            self._retry_decay = 0
            if self.retry_level > 0:
                self.retry_level -= 1
        self._retry_boost += (self.retry_level * 0.6 - self._retry_boost) * 0.05
        # Live speed: base + gentle score ramp + retry boost, capped to stay beatable.
        self.speed = min(13.0, self.base_speed + (self.score // 220) * 0.35 + self._retry_boost)
        self.spawn -= 1
        if self.spawn <= 0:
            import random
            self.obstacles.append(float(self._w + 20))
            self.spawn = random.randint(42, 92)
        self.obstacles = [x - self.speed for x in self.obstacles if x > -30]
        self.score += 1
        for x in self.obstacles:              # collision: near dino AND not high enough
            if 22 < x < 54 and self.dino_y > -15:
                self.game_over = True
                break

    def _draw(self):
        c = self.canvas
        try:
            c.delete("all")
        except Exception:
            return
        gy = self.ground_y
        c.create_line(0, gy, self._w, gy, fill=self.fg, width=1)
        # a couple of ground speckles for motion
        for i in range(0, self._w, 60):
            sx = (i - (self.score * self.speed) % 60)
            c.create_line(sx, gy + 5, sx + 6, gy + 5, fill=self.fg)
        dx, dy = 40, gy + self.dino_y
        ac, bgc = self.accent, self.bg
        def r(x1, y1, x2, y2, fill=ac):
            c.create_rectangle(dx + x1, dy + y1, dx + x2, dy + y2, fill=fill, outline="")
        # --- Chrome-style blocky T-Rex (facing right), feet on the baseline ---
        r(-16, -15, -10, -11)                 # tail (lower step)
        r(-13, -19, -8,  -14)                 # tail (upper step)
        r(-10, -18, 2,   -6)                  # torso
        r(-1,  -24, 5,   -15)                 # neck / back hump
        r(2,   -27, 14,  -18)                 # head
        r(11,  -23, 18,  -19)                 # snout
        r(13,  -19, 18,  -18, bgc)            # mouth line (open jaw)
        r(9,   -25, 12,  -22, bgc)            # eye socket
        r(10,  -24, 11,  -23, ac)             # pupil
        r(1,   -13, 5,   -10)                 # tiny arm
        if not self.on_ground:                # airborne: legs tucked together
            r(-6, -6, -3, -1)
            r(-1, -6, 2,  -1)
        elif self.legphase < 6:               # running: back leg planted, front lifted
            r(-6, -6, -3, 0); r(-7, 0, -2, 1)
            r(-1, -6, 2, -3)
        else:
            r(-6, -6, -3, -3)
            r(-1, -6, 2, 0); r(-2, 0, 3, 1)
        for x in self.obstacles:              # saguaro cactus (anchored to ground)
            c.create_rectangle(x - 3, gy - 16, x + 3, gy, fill=self.fg, outline="")   # trunk
            c.create_rectangle(x - 8, gy - 12, x - 3, gy - 8, fill=self.fg, outline="")  # left arm
            c.create_rectangle(x - 8, gy - 14, x - 6, gy - 8, fill=self.fg, outline="")  # left elbow
            c.create_rectangle(x + 3, gy - 14, x + 8, gy - 10, fill=self.fg, outline="") # right arm
            c.create_rectangle(x + 6, gy - 16, x + 8, gy - 10, fill=self.fg, outline="") # right elbow
        c.create_text(self._w - 8, 11, text=f"{self.score // 10:05d}", anchor="e",
                      fill=self.fg, font=("Consolas", 9))
        if self.retry_level > 0 and not self.game_over:   # retry-pressure turbo cue
            c.create_text(8, 11, text="»" * min(3, self.retry_level), anchor="w",
                          fill=self.accent, font=("Consolas", 10, "bold"))
        if self.game_over:
            c.create_text(self._w / 2, self.h / 2 - 5, text="G A M E   O V E R",
                          fill=self.fg, font=("Consolas", 11, "bold"))
            c.create_text(self._w / 2, self.h / 2 + 11, text="space / click to retry",
                          fill=self.fg, font=("Consolas", 8))


class CompressorGUI:

    HB_URL = "https://github.com/HandBrake/HandBrake/releases/download/1.7.0/HandBrakeCLI-1.7.0-win64.zip"
    # Full-featured build (BtbN GPL): includes SVT-AV1 (fast AV1), libvmaf and
    # the wide filter set — required for the AV1 auto-pick on long content.
    # (gyan.dev's "full" build is only distributed as .7z, which the installer
    # cannot extract; BtbN ships an equivalent build as a plain .zip.)
    FF_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

    def install_tool(self, name, url):
        
        tools_dir = os.path.join(SCRIPT_DIR, "tools")
        os.makedirs(tools_dir, exist_ok=True)
        dest_zip = os.path.join(tools_dir, f"{name}.zip")
        self.update_status(f"Downloading {name}...", level="INFO")
        try:
            r = requests.get(url)
            with open(dest_zip, "wb") as f:
                f.write(r.content)
            with zipfile.ZipFile(dest_zip, "r") as zip_ref:
                zip_ref.extractall(tools_dir)
            self.update_status(f"{name} installed.", level="INFO")
        except Exception as e:
            self.update_status(f"Failed to install {name}: {e}", level="ERROR")

    def check_dependencies(self):
        import shutil

        hb = DEFAULT_HANDBRAKE  if os.path.exists(DEFAULT_HANDBRAKE) else shutil.which("HandBrakeCLI") or shutil.which("HandBrakeCLI.exe")
        ff = DEFAULT_FFMPEG     if os.path.exists(DEFAULT_FFMPEG)    else shutil.which("ffmpeg")        or shutil.which("ffmpeg.exe")
        fp = DEFAULT_FFPROBE    if os.path.exists(DEFAULT_FFPROBE)   else shutil.which("ffprobe")       or shutil.which("ffprobe.exe")

        hb = hb or "HandBrakeCLI.exe"
        ff = ff or "ffmpeg.exe"
        fp = fp or "ffprobe.exe"

        self.handbrake_path = hb
        self.ffmpeg_path    = ff
        self.ffprobe_path   = fp

        

        if not shutil.which(HANDBRAKE_CLI) or not os.path.isfile(HANDBRAKE_CLI):
            self.install_tool("HandBrakeCLI", self.HB_URL)
        if not shutil.which(FFMPEG) or not os.path.isfile(FFMPEG):
            self.install_tool("ffmpeg", self.FF_URL)
            

    def setup_directories(self):
        import os
        for folder in ["user_settings", "heuristics"]:
            if not os.path.exists(folder):
                os.makedirs(folder)
    
    def browse_watch_folder(self):
        
        from tkinter import filedialog, messagebox
        folder = filedialog.askdirectory(parent=self.root, title="Select watch folder")
        if folder:
            self.watch_folder.set(folder)
            self.update_status(f"Watch folder set to: {folder}")
        else:

            self.watch_var.set(False)
            messagebox.showinfo("Watch Folder", "No folder selected.")

    def open_save_folder(self):
        import os, sys, subprocess
        from tkinter import messagebox

        raw = self.save_path.get() if hasattr(self.save_path, "get") else str(self.save_path)
        path = os.path.abspath(os.path.expanduser(raw.strip() or "."))

        def _status(msg, level="INFO"):
            try:
                if hasattr(self, "update_status"):
                    self.update_status(msg, level=level)
                elif hasattr(self, "log_widget"):

                    self.log_widget.configure(state="normal")
                    self.log_widget.insert("end", f"[{level}] {msg}\n")
                    self.log_widget.see("end")
                    self.log_widget.configure(state="disabled")
                else:
                    print(f"[{level}] {msg}")
            except Exception:
                print(f"[{level}] {msg}")

        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            _status(f"Failed to ensure save folder exists: {e}", level="ERROR")
            try:
                messagebox.showerror("Open Save Folder", f"Could not create folder:\n{path}\n\n{e}")
            except Exception:
                pass
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore
            elif sys.platform == "darwin":
                _sp_run(["open", path], check=False)
            else:
                _sp_run(["xdg-open", path], check=False)
            _status(f"Opened save folder: {path}")
        except Exception as e:
            _status(f"Failed to open save folder: {e}", level="ERROR")
            try:
                messagebox.showerror("Open Save Folder", f"Could not open folder:\n{path}\n\n{e}")
            except Exception:
                pass
    def _t(self, key: str, default: str | None = None) -> str:
        code = self.lang_var.get() if hasattr(self, "lang_var") else "en"
        raw = LANG.get(code, {}).get(key)
        env = LANG_BUILTIN["en"].get(key)
        val = raw if raw is not None else (env if env is not None else (default if default is not None else key))
        norm = _normalize_text(val)
        if _mojibake_score(norm) > 0 and env is not None:
            return _normalize_text(env)
        if code == "en" and any(ord(ch) > 127 for ch in norm):
            if env is not None:
                return _normalize_text(env)
            if default is not None:
                return _normalize_text(default)
        return norm

    def _rebuild_ui_for_language(self):
        try:
            for w in list(self.root.winfo_children()):
                w.destroy()
        except Exception:
            pass

        self.setup_ui()
        self.setup_menu()
    def _on_language_change(self):
        _load_lang_packs()
        code = self.lang_var.get()
        if code not in LANG:
            code = "en"
            self.lang_var.set(code)

        _save_language_choice(code)

        try:
            self.settings = getattr(self, "settings", {}) or {}
            self.settings["language"] = code
            self.save_settings()
        except Exception:
            pass
        self._rebuild_ui_for_language()
    def _get_target_bytes(self):
        

        try:
            raw = self.target_size_var.get() if hasattr(self, "target_size_var") else self.settings.get("target_size", 10)
        except Exception:
            raw = self.settings.get("target_size", 10)
        try:
            val = float(str(raw).strip())
        except Exception:
            val = 10.0

        try:
            unit = self.size_unit_var.get() if hasattr(self, "size_unit_var") else self.settings.get("size_unit", "MB")
        except Exception:
            unit = self.settings.get("size_unit", "MB")
        unit = (str(unit or "MB").upper())
        if unit not in {"B","KB","MB","GB","TB"}:
            unit = "MB"

        b = bytes_from_value_unit(val, unit)

        # A non-viable target (blank/0/negative field, or a fat-fingered unit)
        # used to fall through as 0 -> clamped to 1 byte, which then crushed the
        # file to the codec floor chasing an impossible size (observed: a 34 KB
        # voice note degraded to 16 kbps for a "1-byte" target). Anything under
        # ~2 KB is never a real media target, so fall back to the default; for a
        # small source that then lands in the keep-original passthrough.
        if b < 2048:
            b = bytes_from_value_unit(10.0, "MB")

        return int(b)



    
    def setup_ui(self):
        self.queue_box = None
        import os
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        from tkinter.scrolledtext import ScrolledText

        if not hasattr(self, "root") or self.root is None:
            self.root = tk.Tk()
        self.theme_var = tk.StringVar(value="Dark")   # default theme
        self.lang_var = tk.StringVar(value=_load_language_choice("en"))
        _load_lang_packs()
        if self.lang_var.get() not in LANG:
            self.lang_var.set("en")
        self.root.configure(bg="#14161A")             # initial bg; gets overridden by apply_theme
        self.style = ttk.Style(self.root)

        saved_theme = (self.settings.get("theme") if hasattr(self, "settings") else None) or "Dark"
        self.theme_var = tk.StringVar(value=saved_theme)

        apply_theme(self.style, self.theme_var.get())
        try:
            retheme_runtime(self, self.style, self.theme_var.get())
        except Exception:
            pass
        try:
            self.root.configure(bg=APP_BG)
        except Exception:
            pass

        if not hasattr(self, "preset_var"):        self.preset_var = tk.StringVar(value=next(iter(PRESETS)))
        if not hasattr(self, "target_size_var"):   self.target_size_var = tk.IntVar(value=PRESETS[self.preset_var.get()])
        if not hasattr(self, "save_path"):         self.save_path = tk.StringVar(value=os.path.join(SCRIPT_DIR, "output"))
        if not hasattr(self, "size_unit_var"):
            self.size_unit_var = tk.StringVar(value=(self.settings.get("size_unit", "MB") if hasattr(self, "settings") and isinstance(self.settings, dict) else "MB"))
        if not hasattr(self, "profile_var"):       self.profile_var = tk.StringVar(value="")
        if not hasattr(self, "watch_var"):         self.watch_var = tk.BooleanVar(value=False)
        self.per_file_opts = {}
        if not hasattr(self, "watch_folder"):      self.watch_folder = tk.StringVar(value=SCRIPT_DIR)
        if not hasattr(self, "webhook_url"):       self.webhook_url = ""
        if not hasattr(self, "webhook_var"):       self.webhook_var = tk.StringVar(value=self.webhook_url)
        if not hasattr(self, "file_list"): self.file_list = []
        if not hasattr(self, "per_file_opts"): self.per_file_opts = {}  # path -> dict overrides

        def _adv_bool(name, key):
            if not hasattr(self, name):
                setattr(self, name, tk.BooleanVar(value=bool(ADVANCED_DEFAULTS.get(key, False))))
        def _adv_str(name, key):
            if not hasattr(self, name):
                setattr(self, name, tk.StringVar(value=str(ADVANCED_DEFAULTS.get(key, ""))))

        _adv_str ("adv_encoder",            "encoder")
        _adv_bool("adv_iterative",          "iterative")
        _adv_bool("adv_two_pass",           "two_pass")
        _adv_str ("adv_manual_crf",         "manual_crf")
        _adv_str ("adv_manual_bitrate",     "manual_bitrate")
        _adv_str ("adv_output_prefix",      "output_prefix")
        _adv_str ("adv_output_suffix",      "output_suffix")
        _adv_str ("adv_audio_format",       "audio_format")
        _adv_str ("adv_image_format",       "image_format")
        _adv_bool("adv_concurrent",         "concurrent")
        _adv_bool("adv_auto_output_folder", "auto_output_folder")
        _adv_bool("adv_guetzli",            "guetzli")
        _adv_bool("adv_pngopt",             "pngopt")
        _adv_bool("adv_auto_jpeg",          "auto_jpeg")
        _adv_bool("adv_scene_zones",        "scene_zones")
        _adv_bool("adv_hw_decode",          "hw_decode")
        if not hasattr(self, "adv_two_pass_fallback"): self.adv_two_pass_fallback = tk.BooleanVar(value=bool(ADVANCED_DEFAULTS.get("two_pass_fallback", True)))
        if not hasattr(self, "adv_auto_retry"):        self.adv_auto_retry        = tk.BooleanVar(value=bool(ADVANCED_DEFAULTS.get("auto_retry", True)))

        self.root.grid_columnconfigure(0, weight=1)

        header = tk.Frame(self.root, bg=APP_BG)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))

        # Static title (the old typewriter animation left it truncated whenever
        # the window lost focus mid-cycle).
        self.title_label = ttk.Label(header, text="BitCrusher", style="Title.TLabel")
        self.title_label.pack(side="left")
        ttk.Label(header, text="  exact-size video compression", style="Sub.TLabel").pack(side="left", pady=(8, 0))

        # Right side of the header: quality mode + quick actions.
        if not hasattr(self, "adv_quality_mode"):
            self.adv_quality_mode = tk.StringVar(value="max")
        ttk.Button(header, text="User Guide", style="Ghost.TButton",
                   command=getattr(self, "show_user_guide", lambda: None)).pack(side="right", padx=(8, 0))
        ttk.Button(header, text="Advanced…", style="Ghost.TButton",
                   command=getattr(self, "open_advanced_options", lambda: None)).pack(side="right", padx=(8, 0))
        _qwrap = tk.Frame(header, bg=APP_BG)
        _qwrap.pack(side="right", padx=(0, 12))
        ttk.Label(_qwrap, text="Quality:", style="Sub.TLabel").pack(side="left", padx=(0, 6))
        for _qv, _qt in (("fast", "Fast"), ("balanced", "Balanced"), ("max", "Max")):
            ttk.Radiobutton(_qwrap, text=_qt, value=_qv,
                            variable=self.adv_quality_mode).pack(side="left", padx=(0, 6))

        self.root.grid_rowconfigure(1, weight=1)
        content = tk.Frame(self.root, bg=APP_BG)
        content.grid(row=1, column=0, sticky="nsew")

        ctrl = tk.Frame(content, bg=APP_BG)
        ctrl.pack(fill="x", padx=16, pady=(6, 8))

        ttk.Label(ctrl, text=self._t("lbl.preset", "Preset:")).pack(side="left")
        self.preset_combo = ttk.Combobox(
            ctrl,
            textvariable=self.preset_var,
            state="readonly",
            width=24,
            values=sorted(list(PRESETS.keys()))
        )
        self.preset_combo.pack(side="left", padx=(6, 16))
        self.preset_combo.bind("<<ComboboxSelected>>",
            lambda _: getattr(self, "set_preset", lambda _=None: None)(self.preset_var.get())
        )

        ttk.Label(ctrl, text=self._t("lbl.target_size", "Target Size:")).pack(side="left")

        self.size_unit_var = tk.StringVar(
            value=self.settings.get("size_unit","MB") if hasattr(self,"settings") and isinstance(self.settings,dict) else "MB"
        )

        size_frame = tk.Frame(ctrl, bg=APP_BG)
        size_frame.pack(side="left", padx=(6, 16))

        ttk.Entry(size_frame, textvariable=self.target_size_var, width=7, style="Dark.TEntry").pack(side="left")
        ttk.Combobox(
            size_frame,
            textvariable=self.size_unit_var,
            values=["KB","MB","GB","TB"],
            width=4,
            state="readonly"
        ).pack(side="left", padx=(6, 0))

        ttk.Button(ctrl, text="Estimate", style="Ghost.TButton",
                   command=lambda: getattr(self, "_estimate_queue", lambda: None)()).pack(side="left", padx=(0, 16))

        ttk.Label(ctrl, text="Save to:").pack(side="left")
        self.save_entry = ttk.Entry(ctrl, textvariable=self.save_path, style="Dark.TEntry")
        self.save_entry.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(ctrl, text="Browse…", style="Ghost.TButton",
                   command=self.select_output_dir).pack(side="left", padx=(4, 0))

        paned = ttk.Panedwindow(content, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        left  = tk.Frame(paned, bg=APP_BG)
        mid   = tk.Frame(paned, bg=APP_BG)
        right = tk.Frame(paned, bg=APP_BG)

        tk.Label(right, text="Display", bg=APP_BG, fg=FG, anchor="w").pack(fill="x", padx=12, pady=(12, 0))

        self.display_mode_var = tk.StringVar(value="Quality Metrics")
        mode_row = tk.Frame(right, bg=APP_BG); mode_row.pack(fill="x", padx=12, pady=(6, 8))
        ttk.Label(mode_row, text="Mode:").pack(side="left")
        mode_cbx = ttk.Combobox(mode_row, textvariable=self.display_mode_var, state="readonly",
                                values=["Quality Metrics","Advisor Insights","History","Visual Compare"], width=22)
        mode_cbx.pack(side="left", padx=(6,0))
        mode_cbx.bind("<<ComboboxSelected>>", lambda e: self._rebuild_display_panel())

        self.preview_container = tk.Frame(right, bg=CARD_BG, bd=1, relief="solid", highlightthickness=0)
        self.preview_container.pack(fill="both", expand=True, padx=12, pady=(6, 12))






        def _clear_container():
            for w in self.preview_container.winfo_children():
                try: w.destroy()
                except Exception: pass

        def _mk_metrics():

            wrap = tk.Frame(self.preview_container, bg=CARD_BG); wrap.pack(fill="both", expand=True)
            self._metrics_text = tk.Text(wrap, height=16, relief="flat", bd=0, bg=CARD_BG, fg=FG)
            self._metrics_text.pack(fill="both", expand=True, padx=10, pady=10)
            self._metrics_text.configure(state="disabled")

        def _mk_insights():
            wrap = tk.Frame(self.preview_container, bg=CARD_BG); wrap.pack(fill="both", expand=True)
            self._insights_text = tk.Text(wrap, height=16, relief="flat", bd=0, bg=CARD_BG, fg=FG)
            self._insights_text.pack(fill="both", expand=True, padx=10, pady=10)
            self._insights_text.configure(state="disabled")

        def _mk_history():
            from tkinter import ttk as _ttk
            wrap = tk.Frame(self.preview_container, bg=CARD_BG); wrap.pack(fill="both", expand=True)
            cols = ("time","file","target_mb","encoder","actual_mb","overshoot")
            self._hist = _ttk.Treeview(wrap, columns=cols, show="headings", height=10)
            for c, w in zip(cols, (150, 220, 90, 80, 90, 90)):
                self._hist.heading(c, text=c); self._hist.column(c, width=w, anchor="w")
            self._hist.pack(fill="both", expand=True, padx=10, pady=10)

        def _mk_compare():
            wrap = tk.Frame(self.preview_container, bg=CARD_BG); wrap.pack(fill="both", expand=True)
            ttk.Label(wrap, text="Compare the last output with the source.").pack(anchor="w", padx=10, pady=(10,6))
            btn = ttk.Button(wrap, text="Open Visual Compare", command=self._open_visual_compare_for_selection)
            btn.pack(anchor="w", padx=10, pady=(0,10))

        self._mk_metrics   = _mk_metrics
        self._mk_insights  = _mk_insights
        self._mk_history   = _mk_history
        self._mk_compare   = _mk_compare

        def _rebuild_display_panel():
            _clear_container()
            m = self.display_mode_var.get()
            if m == "Quality Metrics":   _mk_metrics()
            elif m == "Advisor Insights":_mk_insights()
            elif m == "History":         _mk_history()
            else:                        _mk_compare()

            self._refresh_display_panel()

        self._rebuild_display_panel = _rebuild_display_panel

        paned.add(left,  weight=3)
        paned.add(mid,   weight=4)
        paned.add(right, weight=3)

        self.paned = paned
        self.root.after(0, self._set_default_layout)

        self.root.update_idletasks()
        try:

            paned.paneconfigure(left,  stretch="always")
            paned.paneconfigure(mid,   stretch="always")
            paned.paneconfigure(right, stretch="always")

            paned.paneconfigure(left,  minsize=480)
            paned.paneconfigure(mid,   minsize=340)
            paned.paneconfigure(right, minsize=300)

            total = paned.winfo_width() or content.winfo_width() or self.root.winfo_width() or 1400

            paned.sashpos(0, max(480, int(total * 0.42)))
            paned.sashpos(1, max(paned.sashpos(0) + 340, int(total * 0.72)))
        except Exception:
            pass

        tk.Label(left, text=self._t("lbl.queue","Queue"), bg=APP_BG, fg=FG, anchor="w").pack(fill="x", padx=12, pady=(12, 0))

        self.drop_frame = tk.Frame(left, bg=CARD_BG, bd=1, relief="solid", highlightthickness=0)
        self.drop_frame.pack(fill="both", expand=True, padx=12, pady=(8, 12))

        self.queue_box = QueueTree(self.drop_frame)
        self._queue_scroll = ttk.Scrollbar(self.drop_frame, orient="vertical",
                                           command=self.queue_box.yview)
        self.queue_box.configure(yscrollcommand=self._queue_scroll.set)
        self._queue_scroll.pack(side="right", fill="y")
        self.job_rows: dict = {}
        self.queue_menu = tk.Menu(self.root, tearoff=0)
        self.queue_menu.add_command(label="Set encoder for this file...", command=lambda: self._queue_set("encoder"))
        self.queue_menu.add_command(label="Set container/format for this file...", command=lambda: self._queue_set("container"))
        self.queue_menu.add_command(label="Set prefix for this file...",  command=lambda: self._queue_set("output_prefix"))
        self.queue_menu.add_command(label="Set suffix for this file...",  command=lambda: self._queue_set("output_suffix"))
        self.queue_menu.add_command(label="Trim / clip range for this file...", command=lambda: self._queue_set_trim())
        self.queue_menu.add_command(label="Suggest trim ranges (audio peaks)...", command=lambda: self._queue_suggest_trim())
        self.queue_menu.add_command(label="Spotlight quality range for this file...", command=lambda: self._queue_set_spotlight())
        self.queue_menu.add_separator()
        self.queue_menu.add_command(label="Reset per-file overrides", command=lambda: self._queue_reset_overrides())
        self.queue_menu.add_command(label="Open Output Folder", command=self.open_save_folder)
        self.queue_menu.add_command(label="Remove from Queue",  command=self.remove_selected)
        def _on_queue_context(event):
            try:
                i = self.queue_box.nearest(event.y)
                self.queue_box.selection_clear(0, "end")
                self.queue_box.selection_set(i)
                self.queue_box.activate(i)
            except Exception:
                pass
            try:
                self.queue_menu.tk_popup(event.x_root, event.y_root)
            finally:
                try: self.queue_menu.grab_release()
                except Exception: pass

        def _queue_set(key: str):
            from tkinter import simpledialog
            sel = list(self.queue_box.curselection())
            if not sel:
                return
            try:
                # Always look up the full path from file_list by index, not from listbox display text
                path = self.file_list[sel[0]]
            except (IndexError, AttributeError):
                return
            if not hasattr(self, "per_file_opts") or self.per_file_opts is None:
                self.per_file_opts = {}
            cur = (self.per_file_opts.get(path, {}) or {}).get(key, "")
            # parent=self.root is critical on Windows — without it the dialog appears
            # behind the main window, making the app appear frozen/crashed
            val = simpledialog.askstring(
                "Per-file override",
                f"{key} for:\n{os.path.basename(path)}",
                initialvalue=str(cur),
                parent=self.root,
            )
            if val is None:
                return
            self.per_file_opts.setdefault(path, {})[key] = val.strip()
            try: self.update_status(f"Per-file: {os.path.basename(path)} -> {key}={val.strip()}")
            except Exception: pass
            try: self._save_queue()
            except Exception: pass

        def _queue_reset_overrides():
            sel = list(self.queue_box.curselection())
            if not sel or not getattr(self, "per_file_opts", None):
                return
            for i in sel:
                try:
                    path = self.file_list[i]
                    if self.per_file_opts.pop(path, None) is not None:
                        self.update_status(f"Per-file overrides cleared: {os.path.basename(path)}")
                except Exception:
                    pass
            try:
                self._save_queue()
            except Exception:
                pass

        def _queue_set_trim():
            from tkinter import simpledialog, messagebox
            sel = list(self.queue_box.curselection())
            if not sel:
                return
            try:
                path = self.file_list[sel[0]]
            except (IndexError, AttributeError):
                return
            if not hasattr(self, "per_file_opts") or self.per_file_opts is None:
                self.per_file_opts = {}
            cur = (self.per_file_opts.get(path, {}) or {}).get("trim_range", "")
            val = simpledialog.askstring(
                "Trim / clip range",
                "Compress only this range of:\n"
                f"{os.path.basename(path)}\n\n"
                "Format: START-END (e.g. 1:42-2:05 or 12-31).\n"
                "The whole size budget goes to the kept range.\n"
                "Leave blank to clear the trim.",
                initialvalue=str(cur),
                parent=self.root,
            )
            if val is None:
                return
            val = val.strip()
            if not val:
                if (self.per_file_opts.get(path, {}) or {}).pop("trim_range", None) is not None:
                    self.update_status(f"Per-file: trim cleared for {os.path.basename(path)}")
                try: self._save_queue()
                except Exception: pass
                return
            try:
                _a, _b = _parse_trim_range(val)
            except ValueError as e:
                try:
                    messagebox.showerror("Trim range", f"Invalid range: {e}", parent=self.root)
                except Exception:
                    pass
                return
            self.per_file_opts.setdefault(path, {})["trim_range"] = val
            self.update_status(f"Per-file: {os.path.basename(path)} trim={val} "
                               f"({_b - _a:.1f}s kept)")
            try: self._save_queue()
            except Exception: pass

        def _queue_selected_path():
            sel = list(self.queue_box.curselection())
            if not sel:
                return None
            try:
                return self.file_list[sel[0]]
            except (IndexError, AttributeError):
                return None

        def _queue_set_spotlight():
            from tkinter import simpledialog, messagebox
            path = _queue_selected_path()
            if not path:
                return
            if not hasattr(self, "per_file_opts") or self.per_file_opts is None:
                self.per_file_opts = {}
            cur = (self.per_file_opts.get(path, {}) or {}).get("spotlight_range", "")
            val = simpledialog.askstring(
                "Spotlight quality range",
                "Keep the WHOLE video, but boost quality in this range\n"
                "(the rest of the video pays for it under the same cap):\n"
                f"{os.path.basename(path)}\n\n"
                "Format: START-END (e.g. 1:42-2:05). Uses x264/x265 rate zones.\n"
                "Leave blank to clear.",
                initialvalue=str(cur), parent=self.root)
            if val is None:
                return
            val = val.strip()
            if not val:
                if (self.per_file_opts.get(path, {}) or {}).pop("spotlight_range", None) is not None:
                    self.update_status(f"Per-file: spotlight cleared for {os.path.basename(path)}")
                try: self._save_queue()
                except Exception: pass
                return
            try:
                _parse_trim_range(val)
            except ValueError as e:
                try:
                    messagebox.showerror("Spotlight range", f"Invalid range: {e}", parent=self.root)
                except Exception:
                    pass
                return
            self.per_file_opts.setdefault(path, {})["spotlight_range"] = val
            self.update_status(f"Per-file: {os.path.basename(path)} spotlight={val}")
            try: self._save_queue()
            except Exception: pass

        def _queue_suggest_trim():
            from tkinter import messagebox
            path = _queue_selected_path()
            if not path:
                return
            self.update_status(f"[Suggest] Analyzing audio energy of {os.path.basename(path)}...")

            def _work():
                try:
                    cands = suggest_trim_ranges(path, clip_seconds=20.0,
                                                status_cb=lambda m, l="INFO": self.update_status(m, level=l))
                except Exception:
                    cands = []
                self.root.after(0, lambda: _show(cands))

            def _show(cands):
                if not cands:
                    try:
                        messagebox.showinfo(
                            "Suggest trim",
                            "No clear audio peaks found.\n\n"
                            "Silent or uniform-loudness moments (e.g. a great play in a "
                            "quiet game) can't be detected by signal analysis - set the "
                            "trim manually via 'Trim / clip range for this file...'.",
                            parent=self.root)
                    except Exception:
                        pass
                    return
                win = tk.Toplevel(self.root)
                win.title("Suggested trim ranges")
                win.transient(self.root)
                tk.Label(win, text=f"Candidate moments in {os.path.basename(path)}:",
                         anchor="w", justify="left").pack(fill="x", padx=12, pady=(10, 4))
                _choice = tk.StringVar(value=cands[0]["range"])
                for c in cands:
                    ttk.Radiobutton(
                        win, value=c["range"], variable=_choice,
                        text=f"{c['range']}   ({c['why']}, score {c['score']})"
                    ).pack(anchor="w", padx=18, pady=2)
                btns = tk.Frame(win); btns.pack(fill="x", padx=12, pady=10)

                def _apply():
                    rng = _choice.get()
                    if not hasattr(self, "per_file_opts") or self.per_file_opts is None:
                        self.per_file_opts = {}
                    self.per_file_opts.setdefault(path, {})["trim_range"] = rng
                    self.update_status(f"Per-file: {os.path.basename(path)} trim={rng} (from suggestion)")
                    try: self._save_queue()
                    except Exception: pass
                    win.destroy()

                ttk.Button(btns, text="Apply as trim", command=_apply).pack(side="right", padx=(6, 0))
                ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")

            threading.Thread(target=_work, name="bc_suggest", daemon=True).start()

        # expose nested helpers as instance attributes for event bindings/menu commands
        self._on_queue_context = _on_queue_context
        self._queue_set = _queue_set
        self._queue_set_trim = _queue_set_trim
        self._queue_set_spotlight = _queue_set_spotlight
        self._queue_suggest_trim = _queue_suggest_trim
        self._queue_reset_overrides = _queue_reset_overrides

        self.queue_box.bind("<Button-3>", self._on_queue_context)
        self.queue_box.pack(fill="both", expand=True, padx=6, pady=6)
        self._rebuild_display_panel()
        self.queue_box.bind("<<TreeviewSelect>>", lambda e: self._schedule_display_refresh())
        self.queue_box.bind("<Double-Button-1>", lambda e: self._schedule_display_refresh())


        # Primary action row: big Start, Stop beside it.
        start_row = tk.Frame(left, bg=APP_BG)
        start_row.pack(side="bottom", fill="x", padx=12, pady=(4, 12))

        ttk.Button(
            start_row,
            text="▶  " + self._t("btn.start", "Start Compression"),
            command=getattr(self, "start_compression", lambda: None)
        ).pack(side="left", expand=True, fill="x", padx=(0, 8))

        ttk.Button(
            start_row,
            text=self._t("btn.stop", "Stop"),
            style="Ghost.TButton",
            command=getattr(self, "stop_compression", lambda: None)
        ).pack(side="left", padx=(0, 0), ipadx=10)

        # Queue management toolbar (secondary/ghost buttons).
        qbtns = tk.Frame(left, bg=APP_BG)
        qbtns.pack(side="bottom", fill="x", padx=12, pady=(6, 4))

        ttk.Button(qbtns, text="+ " + self._t("btn.add_files","Add Files…"), style="Ghost.TButton",
                   command=getattr(self, "add_files", lambda: None)).pack(side="left", padx=(0, 6))
        ttk.Button(qbtns, text=self._t("btn.remove_selected","Remove"), style="Ghost.TButton",
                   command=getattr(self, "remove_selected", lambda: None)).pack(side="left", padx=(0, 6))
        ttk.Button(qbtns, text=self._t("btn.clear","Clear"), style="Ghost.TButton",
                   command=getattr(self, "clear_queue", lambda: None)).pack(side="left", padx=(0, 6))
        ttk.Button(qbtns, text="▼", width=3, style="Ghost.TButton",
                   command=lambda: getattr(self, "move_selection", lambda *_: None)(+1)).pack(side="right", padx=(6, 0))
        ttk.Button(qbtns, text="▲", width=3, style="Ghost.TButton",
                   command=lambda: getattr(self, "move_selection", lambda *_: None)(-1)).pack(side="right")

        try:
            if TkinterDnD and hasattr(self.root, "drop_target_register"):
                for w in (self.drop_frame, self.queue_box, self.root):
                    if hasattr(w, "drop_target_register"):
                        w.drop_target_register(DND_FILES)
                        w.dnd_bind("<<Drop>>", getattr(self, "drop_file_handler", lambda *_: None))
        except Exception:
            pass

        from tkinter.scrolledtext import ScrolledText
        self._activity_label = tk.Label(mid, text="Activity", bg=APP_BG, fg=FG, anchor="w")
        self._activity_label.pack(fill="x", padx=12, pady=(12, 0))

        # Overall queue progress lives at the bottom of the middle pane.
        self.progress = ttk.Progressbar(mid, style="Accent.Horizontal.TProgressbar",
                                        mode="determinate")
        self.progress.pack(side="bottom", fill="x", padx=12, pady=(6, 12))

        _mid_nb = ttk.Notebook(mid)
        _mid_nb.pack(fill="both", expand=True, padx=12, pady=(8, 0))

        # Optional hidden T-Rex runner in the dead space above the Activity feed
        # (toggled from Advanced Options). Recreate cleanly if setup_ui re-runs.
        try:
            if getattr(self, "dino_runner", None) is not None:
                self.dino_runner.stop()
        except Exception:
            pass
        if not hasattr(self, "dino_game_var"):
            self.dino_game_var = tk.IntVar(
                value=1 if (getattr(self, "settings", {}) or {}).get("dino_game", False) else 0)
        self._mid_frame = mid
        self.dino_runner = DinoRunner(mid, bg=_hsl_shift(CARD_BG, l_mul=0.90),
                                      fg=FG_SUB, accent=(globals().get("ACCENT") or "#4caf7d"))
        self._apply_dino()

        # Plain-language view for everyone: friendly proportional font, roomy
        # line spacing, a left margin, and a blank line between files.
        _tab_feed = tk.Frame(_mid_nb, bg=CARD_BG)
        _mid_nb.add(_tab_feed, text="  Progress  ")
        self.stage_text = ScrolledText(_tab_feed, height=16, wrap="word",
                                       bg=_hsl_shift(CARD_BG, l_mul=0.98), fg=FG,
                                       insertbackground=FG, relief="flat", borderwidth=0,
                                       font=("Segoe UI", 10), spacing1=3, spacing3=5,
                                       padx=10, pady=8)
        self.stage_text.pack(fill="both", expand=True, padx=2, pady=2)
        self.stage_text.config(state="disabled")

        # Technical detail for power users: monospace so the time/level/message
        # columns line up, tight spacing, section dividers between jobs.
        _tab_log = tk.Frame(_mid_nb, bg=CARD_BG)
        _mid_nb.add(_tab_log, text="  Details  ")
        self.log_text = ScrolledText(_tab_log, height=10, bg=_hsl_shift(CARD_BG, l_mul=0.96),
                                     fg=FG, insertbackground=FG, relief="flat", borderwidth=0,
                                     state="disabled", font=("Consolas", 9),
                                     spacing1=1, spacing3=1, padx=8, pady=6)
        self.log_text.pack(fill="both", expand=True, padx=2, pady=2)
        self.log_widget = self.log_text
        self.Log_widget = self.log_widget
        bridge_gui_logger_color(self.log_widget)

        # Lifetime stats: read-only roll-up of the run_*.jsonl encode history
        # (total bytes saved, VMAF distribution, encoder win-rates). Offline.
        _tab_stats = tk.Frame(_mid_nb, bg=CARD_BG)
        _mid_nb.add(_tab_stats, text="  Stats  ")
        _sbar = tk.Frame(_tab_stats, bg=CARD_BG); _sbar.pack(fill="x", padx=2, pady=(4, 0))
        ttk.Button(_sbar, text="Refresh", style="Ghost.TButton",
                   command=lambda: self.refresh_lifetime_stats()).pack(side="right")
        self.stats_view = ScrolledText(_tab_stats, height=14, wrap="word",
                                       bg=_hsl_shift(CARD_BG, l_mul=0.98), fg=FG,
                                       insertbackground=FG, relief="flat", borderwidth=0,
                                       font=("Consolas", 10), spacing1=2, spacing3=2,
                                       padx=10, pady=8)
        self.stats_view.pack(fill="both", expand=True, padx=2, pady=2)
        self.stats_view.config(state="disabled")
        try:
            self.refresh_lifetime_stats()
        except Exception:
            pass

        wb = ttk.LabelFrame(right, text=self._t("panel.webhook","Webhook"), style="Card.TLabelframe")
        wb.pack(fill="x", padx=12, pady=(12, 10))
        ttk.Label(wb, text=self._t("lbl.webhook_url","Discord/Webhook URL"), style="Sub.TLabel").pack(anchor="w", padx=10, pady=(8, 0))
        ttk.Entry(wb, textvariable=self.webhook_var, style="Dark.TEntry").pack(fill="x", padx=10, pady=(4, 10))

        wf = ttk.LabelFrame(right, text=self._t("panel.watcher","Folder Watcher"), style="Card.TLabelframe")
        wf.pack(fill="x", padx=12, pady=(0, 10))
        self.watch_chk = ttk.Checkbutton(
            wf, text=self._t("lbl.enable_watcher","Enable watcher"), variable=self.watch_var,
            onvalue=True, offvalue=False,
            command=getattr(self, "toggle_watch_folder", lambda: None)
        )
        self.watch_chk.pack(anchor="w", padx=10, pady=(8, 4))
        wrow = tk.Frame(wf, bg=APP_BG); wrow.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Entry(wrow, textvariable=self.watch_folder, style="Dark.TEntry").pack(side="left", fill="x", expand=True)
        ttk.Button(wrow, text="…", width=3, style="Ghost.TButton",
                   command=getattr(self, "browse_watch_folder", lambda: None)).pack(side="left", padx=(6, 0))
        if not hasattr(self, "pipeline_var"):
            self.pipeline_var = tk.BooleanVar(value=bool((getattr(self, "settings", {}) or {}).get("pipeline_mode", False)))
        self.pipeline_chk = ttk.Checkbutton(
            wf, text=self._t("lbl.pipeline_mode", "Pipeline mode (auto-compress watched files + webhook)"),
            variable=self.pipeline_var, onvalue=True, offvalue=False,
            command=lambda: self.settings.__setitem__("pipeline_mode", bool(self.pipeline_var.get())))
        self.pipeline_chk.pack(anchor="w", padx=10, pady=(0, 10))

        pf = ttk.LabelFrame(right, text=self._t("panel.profiles","Profiles"), style="Card.TLabelframe")
        pf.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Entry(pf, textvariable=self.profile_var, style="Dark.TEntry").pack(fill="x", padx=10, pady=(8, 4))
        prow = tk.Frame(pf, bg=APP_BG); prow.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(prow, text=self._t("btn.save","Save"), style="Ghost.TButton",
                   command=getattr(self, "save_profile", lambda: None)).pack(side="left")
        ttk.Button(prow, text=self._t("btn.load","Load"), style="Ghost.TButton",
                   command=getattr(self, "load_profile", lambda: None)).pack(side="left", padx=6)

        ttk.Button(right, text="Open Save Folder", style="Ghost.TButton",
                   command=getattr(self, "open_save_folder", lambda: None)).pack(fill="x", padx=12, pady=(0, 12))

        if getattr(self, "queue_box", None) is not None and self.queue_box.size() == 0:
            try:
                getattr(self, "set_preset", lambda *_a, **_k: None)(self.preset_var.get())
            except Exception:
                pass

            try:
                self.webhook_url = self.webhook_var.get()
            except Exception:
                pass

            try:
                self._on_save_dir_changed()
            except Exception:
                pass

        # Final retheme so widgets created above (notebook tabs, entries,
        # scrolled texts) pick up the palette — the first pass ran before
        # most of the UI existed.
        try:
            retheme_runtime(self, self.style, self.theme_var.get())
        except Exception:
            pass

        # Make the quality score stand out in the plain-language feed so a
        # non-technical user skimming a long batch can't miss it.
        try:
            self.stage_text.tag_configure(
                "QSCORE", foreground=(globals().get("ACCENT") or "#4caf7d"),
                font=("Segoe UI", 10, "bold"), spacing1=4, spacing3=4)
        except Exception:
            pass

        # Render any restored / already-queued files into the freshly built
        # queue widget. setup_ui runs more than once (init + main launch), and
        # each rebuild creates an EMPTY queue_box — without this, files kept in
        # file_list (restored from last session, or added before a rebuild) stay
        # invisible even though they're really queued and will compress. This
        # was the "file not showing but still compresses" bug.
        try:
            if getattr(self, "file_list", None):
                self.refresh_queue_box()
            else:
                self._load_queue()
        except Exception:
            pass









    def _pause_title(self, *_):
        job = getattr(self, "_title_job", None)
        if job:
            self.root.after_cancel(job)
            self._title_job = None


    def download_from_youtube(self):
        
        from tkinter import simpledialog, messagebox

        url = simpledialog.askstring("YouTube Download", "Enter YouTube URL:")
        if not url:
            return

        choice = simpledialog.askstring(
            "Format",
            "Choose download format:\n• audio\n• video\n• audio+video"
        )
        if not choice:
            return
        choice = choice.strip().lower()
        if choice not in ("audio", "video", "audio+video"):
            messagebox.showerror("Invalid Choice", "Enter exactly: audio, video, or audio+video.")
            return

        temp_dir = tempfile.mkdtemp(prefix="yt_")
        if choice == "audio":
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s")
            }
        elif choice == "video":
            ydl_opts = {
                "format": "bestvideo/best",
                "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s")
            }
        else:  # audio+video
            ydl_opts = {
                "format": "best",
                "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s")
            }

        self.update_status(f"Downloading from YouTube: {url} ({choice})")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_path = ydl.prepare_filename(info)
        except Exception as e:
            self.log_error(f"YouTube download failed: {e}")
            messagebox.showerror("Download Error", str(e))
            return

        save_dir = self.save_path.get()
        try:
            tgt_size = int(self.target_size.get())
        except ValueError:
            from __main__ import MAX_SIZE_MB_DEFAULT
            tgt_size = MAX_SIZE_MB_DEFAULT

        adv_opts = self.gather_advanced_options()
        threading.Thread(
            target=lambda: self._compress_downloaded(downloaded_path, save_dir, tgt_size, adv_opts),
            daemon=True
        ).start()

    def _compress_downloaded(self, input_path, save_dir, target_size_mb, adv_opts):
        

        self.update_status(f"Compressing downloaded file: {input_path}")
        target_size_bytes = target_size_mb * 1024 * 1024
        actual_size = os.path.getsize(input_path)
        if actual_size <= target_size_bytes:
            self.update_status(f"Skipping compression - file is {format_bytes(actual_size)}, under target.")
            shutil.copy(input_path, os.path.join(save_dir, os.path.basename(input_path)))
            return
        stats = auto_compress(
            input_path,
            save_dir,
            self.update_status,    # callback writes to your on-screen log :contentReference[oaicite:2]{index=2}
            target_size_bytes,
            "",                    # no webhook for YT downloads
            {**(adv_opts or {}), "_target_is_bytes": True},
            lambda: False          # never cancel
        )

        if not stats:
            return

        orig = stats.get("original_size", 0)
        comp = stats.get("compressed_size", 0)
        ratio = comp / orig if orig else 0
        took  = stats.get("time_taken", stats.get("duration", 0))

        def insert_row():
            self.stats_table.insert("", "end", values=(
                os.path.basename(input_path),
                format_bytes(orig),
                format_bytes(comp),
                f"{ratio:.2f}",
                f"{took:.1f}"
            ))
        self.root.after(0, insert_row)

    def start_youtube_download(self):
        
        url = self.yt_url_var.get().strip()
        fmt = self.yt_format_var.get().strip().lower()
        if not url or fmt not in ("audio", "video", "audio+video"):
            self.log_error("Invalid YouTube URL or format")
            return

        threading.Thread(
            target=self.download_from_youtube,
            args=(url, fmt),
            daemon=True
        ).start()

    def download_from_youtube(self, url, choice):
        
        temp_dir = tempfile.mkdtemp(prefix="yt_")
        ydl_opts = {"outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s")}
        if choice == "audio":
            ydl_opts["format"] = "bestaudio/best"
        elif choice == "video":
            ydl_opts["format"] = "bestvideo/best"
        else:  # audio+video
            ydl_opts["format"] = "best"

        self.update_status(f"Downloading from YouTube: {url} ({choice})")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_path = ydl.prepare_filename(info)
        except Exception as e:
            self.log_error(f"YouTube download failed: {e}")
            return

        save_dir = self.save_path.get()
        try:
            tgt_size = int(self.target_size.get())
        except ValueError:
            from __main__ import MAX_SIZE_MB_DEFAULT
            tgt_size = MAX_SIZE_MB_DEFAULT

        adv_opts = self.gather_advanced_options()
        self._compress_downloaded(downloaded_path, save_dir, tgt_size, adv_opts)



    def drop_file_handler(self, raw_data):
        

        self.logger.debug(f"Raw DnD drop data: {raw_data}")

        paths = parse_dnd_files(raw_data if isinstance(raw_data, str) else "")

        for p in paths:
            if os.path.isfile(p):
                self.logger.info(f"Adding dropped file to queue: {p}")

                self.queue_files([p])
            else:
                self.logger.warning(f"Dropped item is not a file: {p}")






    def start_compression(self):
        
        if getattr(self, "compression_running", False):
            self.update_status("Compression already running.", level="WARNING")
            return

        try:
            target_bytes = int(self._get_target_bytes())
        except Exception:
            target_bytes = 10 * 1024 * 1024  # 10 MB fallback
        save_dir = getattr(self, "save_path_var", None)
        save_dir = save_dir.get() if hasattr(save_dir, "get") else getattr(self, "output_dir", "")

        snapshot = list(getattr(self, "file_list", []) or [])
        norm = [_normalize_drop_path(p) for p in snapshot if isinstance(p, str)]
        files = [p for p in norm if os.path.isfile(p)]

        if not files:
            self.update_status("No valid files to process (queue empty or paths invalid).", level="ERROR")
            try:
                from tkinter import messagebox as mbox
                mbox.showerror("BitCrusher", "No valid files to process.\nAdd files to the queue first.")
            except Exception:
                pass
            return

        save_dir = ""
        try:
            save_dir = (self.save_path.get() if hasattr(self, "save_path") else "").strip()
        except Exception:
            save_dir = ""
        if not save_dir:
            save_dir = os.path.dirname(files[0]) or os.getcwd()

        self._thread_target_bytes = int(target_bytes)
        self._thread_save_path = save_dir
        self._thread_file_list = files[:]  # immutable snapshot

        try:
            adv = self.gather_advanced_options()
        except Exception:
            adv = {}
        self._thread_adv_options = dict(adv or {})
        self.advanced_options = dict(self._thread_adv_options)

        self.update_status(f"Starting encode @ target ~{human_bytes(self._thread_target_bytes)}", level="INFO")
        try:
            self.ensure_progress_bars()
            try:
                self.progress.stop()
            except Exception:
                pass
            self.progress["mode"] = "determinate"
            self.progress["maximum"] = max(1, len(files))
            self.progress["value"] = 0
        except Exception:
            pass

        self.compression_running = True
        th = threading.Thread(target=self.compress_all, name="compress_all", daemon=True)
        th.start()
        self.update_status(f"Worker started for {len(files)} file(s).", level="DEBUG")

        def _prep_after_start():
            try:
                self.ensure_progress_bars()

                try:
                    total = max(1, len(self._thread_file_list))
                    self.progress["maximum"] = total
                    self.progress["value"] = 0
                    try:
                        self.progress.stop()
                    except Exception:
                        pass
                except Exception:
                    pass

                try:
                    self.update_status(f"Starting compression for {len(self._thread_file_list)} file(s)...", level="INFO")
                except Exception:
                    pass
            except Exception:
                pass
        self.root.after(0, _prep_after_start)

        logging.info(f"[GUI] Launching compression thread. files={len(self._thread_file_list)} save_dir={self._thread_save_path}")


    def drop_file_handler(self, data):
        

        paths = parse_dnd_files(data if isinstance(data, str) else "")
        for p in paths:
            if os.path.isfile(p):

                self.queue_files([p])




    def __init__(self, root=None):
        import tkinter as tk
        from pathlib import Path
        import os, sys, platform, threading, logging
        from win10toast import ToastNotifier

        self.logger = setup_logging()
        if root is None:

            try:
                from tkinterdnd2 import TkinterDnD
                root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
            except Exception:
                root = tk.Tk()
        self.root = root

        try:
            import tkinter as tk
            if not hasattr(self, "theme_var"):
                self.theme_var = tk.StringVar(value="Dark")
        except Exception:
            pass

        self.iterative_var     = tk.BooleanVar(value=False)
        self.two_pass_var      = tk.BooleanVar(value=False)
        self.concurrent_var    = tk.BooleanVar(value=False)
        self.auto_output_var   = tk.BooleanVar(value=False)
        self.guetzli_var       = tk.BooleanVar(value=False)
        self.pngopt_var        = tk.BooleanVar(value=False)
        self.auto_jpeg_var     = tk.BooleanVar(value=False)

        self.webhook_var       = tk.StringVar(value="")
        self.watch_var         = tk.BooleanVar(value=False)
        self.pipeline_var      = tk.BooleanVar(value=bool((getattr(self, "settings", {}) or {}).get("pipeline_mode", False)))
        self.watch_folder      = tk.StringVar(value="")
        self.profile_var       = tk.StringVar(value="")

        self.manual_crf        = tk.StringVar(value="")
        self.manual_bitrate    = tk.StringVar(value="")
        self.prefix_entry_var  = tk.StringVar(value="")
        self.suffix_entry_var  = tk.StringVar(value="_discord_ready")

        self.encoder_var       = tk.StringVar(value="x265")
        self.audio_fmt_var     = tk.StringVar(value="opus")
        self.image_fmt_var     = tk.StringVar(value="jpg")
        
        self.style = ttk.Style(self.root)   # don't apply yet; we’ll apply after loading settings

        self.settings_dir  = USER_SETTINGS_DIR  # e.g. .../tescompressor3/user_settings
        os.makedirs(self.settings_dir, exist_ok=True)
        self.settings_path = os.path.join(self.settings_dir, "settings.json")

        self.settings = {
            "output_dir": str(Path.home()),
            "watch_folder": "",
            "enable_watch": False,
            "preset": list(PRESETS.keys())[0],
            "target_size": MAX_SIZE_MB_DEFAULT,
            "webhook_url": ""
        }
        try:
            if os.path.isfile(self.settings_path):
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.saved_profiles = dict((self.settings or {}).get("profiles", {}))
                    chosen = (self.settings or {}).get("theme", "Dark")
                    self.theme_var = tk.StringVar(value=chosen)
                    apply_theme(self.style, chosen)

                    if not hasattr(self, "theme_var"):
                        self.theme_var = tk.StringVar(value="Dark")

                    self.queue_container  = getattr(self, "queue_container",  self.main_left if hasattr(self, "main_left") else self.root)
                    self.log_container    = getattr(self, "log_container",    self.log_text if hasattr(self, "log_text") else self.root)
                    self.preview_container= getattr(self, "preview_container",self.preview_label if hasattr(self, "preview_label") else self.root)

                    init_aesthetics(self)

                    self.root.configure(bg=APP_BG)
                    retheme_runtime(self, self.style, chosen)

                    self.THEMES = THEMES
                    self.apply_theme = apply_theme
                    self.retheme_runtime = retheme_runtime
                    self.fade_window = fade_window


        except Exception:
            pass

        if not hasattr(self, "webhook_var"):
            self.webhook_var  = tk.StringVar(value=getattr(self, "webhook_url", "") or "")
            self.webhook_url  = self.webhook_var  # keep old code working

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if hasattr(self, "ensure_tray_icon"):
            self.ensure_tray_icon(start_if_needed=False)

        try:
            self.root.tk.eval('package require tkdnd')
        except Exception:
            pass

        self.root.withdraw()  # hide until setup is complete

        self.BASE_DIR = os.path.dirname(
            sys.executable if getattr(sys, 'frozen', False)
            else os.path.abspath(__file__)
        )

        self.cancel_flag     = False
        self.file_list       = []
        self.stats_list      = []
        self.save_path       = tk.StringVar(value=str(Path.home()))
        self.target_size_var = tk.StringVar(value=str(MAX_SIZE_MB_DEFAULT))
        self.preset_var      = tk.StringVar(value=list(PRESETS.keys())[0])

        self.selected_preset = self.preset_var
        self.target_size     = self.target_size_var

        self.webhook_url     = tk.StringVar(value="")
        self.use_webhook     = tk.IntVar(value=0)

        self.adv_encoder     = tk.StringVar(value=ADVANCED_DEFAULTS["encoder"])
        self.adv_iterative   = tk.IntVar(value=1 if ADVANCED_DEFAULTS["iterative"] else 0)
        self.adv_two_pass    = tk.IntVar(value=1 if ADVANCED_DEFAULTS["two_pass"] else 0)
        self.adv_manual_crf  = tk.StringVar(value=ADVANCED_DEFAULTS["manual_crf"])
        self.adv_manual_bitrate = tk.StringVar(value=ADVANCED_DEFAULTS["manual_bitrate"])
        self.adv_output_prefix  = tk.StringVar(value=ADVANCED_DEFAULTS["output_prefix"])
        self.adv_output_suffix  = tk.StringVar(value=ADVANCED_DEFAULTS["output_suffix"])
        self.adv_audio_format   = tk.StringVar(value=ADVANCED_DEFAULTS["audio_format"])
        self.adv_image_format   = tk.StringVar(value=ADVANCED_DEFAULTS["image_format"])

        self._syncing_prefix = False
        self._syncing_suffix = False

        def _b2a_prefix(*_):
            if getattr(self, "_syncing_prefix", False): return
            self._syncing_prefix = True
            try:
                pv = (self.prefix_entry_var.get() if hasattr(self, "prefix_entry_var") else "")
                if hasattr(self, "adv_output_prefix") and pv != self.adv_output_prefix.get():
                    self.adv_output_prefix.set(pv)
            finally:
                self._syncing_prefix = False

        def _a2b_prefix(*_):
            if getattr(self, "_syncing_prefix", False): return
            self._syncing_prefix = True
            try:
                av = self.adv_output_prefix.get() if hasattr(self, "adv_output_prefix") else ""
                if hasattr(self, "prefix_entry_var") and av != self.prefix_entry_var.get():
                    self.prefix_entry_var.set(av)
            finally:
                self._syncing_prefix = False

        def _b2a_suffix(*_):
            if getattr(self, "_syncing_suffix", False): return
            self._syncing_suffix = True
            try:
                sv = (self.suffix_entry_var.get() if hasattr(self, "suffix_entry_var") else "")
                if hasattr(self, "adv_output_suffix") and sv != self.adv_output_suffix.get():
                    self.adv_output_suffix.set(sv)
            finally:
                self._syncing_suffix = False

        def _a2b_suffix(*_):
            if getattr(self, "_syncing_suffix", False): return
            self._syncing_suffix = True
            try:
                av = self.adv_output_suffix.get() if hasattr(self, "adv_output_suffix") else ""
                if hasattr(self, "suffix_entry_var") and av != self.suffix_entry_var.get():
                    self.suffix_entry_var.set(av)
            finally:
                self._syncing_suffix = False

        if hasattr(self, "prefix_entry_var") and hasattr(self, "adv_output_prefix"):
            self.prefix_entry_var.trace_add("write", lambda *args: _b2a_prefix())
            self.adv_output_prefix.trace_add("write", lambda *args: _a2b_prefix())
        if hasattr(self, "suffix_entry_var") and hasattr(self, "adv_output_suffix"):
            self.suffix_entry_var.trace_add("write", lambda *args: _b2a_suffix())
            self.adv_output_suffix.trace_add("write", lambda *args: _a2b_suffix())

        self.adv_concurrent     = tk.IntVar(value=1 if ADVANCED_DEFAULTS["concurrent"] else 0)
        self.adv_auto_output    = tk.IntVar(value=1 if ADVANCED_DEFAULTS["auto_output_folder"] else 0)
        self.adv_guetzli        = tk.IntVar(value=1 if ADVANCED_DEFAULTS["guetzli"] else 0)
        self.adv_pngopt         = tk.IntVar(value=1 if ADVANCED_DEFAULTS["pngopt"] else 0)
        self.adv_auto_jpeg      = tk.IntVar(value=1 if ADVANCED_DEFAULTS["auto_jpeg"] else 0)
        self.adv_grain_filter   = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("grain_filter", True) else 0)
        self.adv_scene_zones    = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("scene_zones", True) else 0)
        self.adv_hw_decode      = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("hw_decode", True) else 0)
        # Batch-1 quality-of-life toggles
        self.adv_smart_preproc   = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("smart_preproc", True) else 0)
        # Learning-system toggles (parity with CLI --no-learned-seed / --no-preflight)
        self.adv_learned_seed    = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("learned_seed", True) else 0)
        self.adv_preflight       = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("preflight_advice", True) else 0)
        self.adv_ceiling_downscale = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("ceiling_downscale_retry", True) else 0)
        self.adv_discord_compat  = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("discord_compat", False) else 0)
        self.adv_embed_lyrics    = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("embed_lyrics", True) else 0)
        self.adv_copy_clipboard  = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("copy_to_clipboard", False) else 0)
        self.adv_audio_track_mode = tk.StringVar(value=str(ADVANCED_DEFAULTS.get("audio_track_mode", "keepfirst")))
        _qm_def = str(ADVANCED_DEFAULTS.get("quality_mode", "max") or "max").strip().lower()
        self.adv_quality_mode   = tk.StringVar(value=("max" if _qm_def in ("quality_first", "max") else
                                                      _qm_def if _qm_def in ("fast", "balanced") else "max"))

        self.log_filter_var = tk.StringVar(value="INFO")
        self.profile_var    = tk.StringVar(value="")
        self.av_format_var  = tk.StringVar(value="audio+video")

        self.watch_folder   = tk.StringVar(value="")
        self.enable_watch   = tk.BooleanVar(value=False)

        self.save_path.set(self.settings.get("output_dir", self.save_path.get()))
        self.watch_folder.set(self.settings.get("watch_folder", self.watch_folder.get()))
        self.enable_watch.set(self.settings.get("enable_watch", False))

        self.enable_watch_compress = self.enable_watch

        self.notifier  = ToastNotifier()
        self.all_logs  = []
        self.stop_event = threading.Event()
        self.settings_path = os.path.join(USER_SETTINGS_DIR, "settings.json")
        data = {}

        self.setup_directories()
        self.setup_style()
        self.setup_ui()
        self.setup_drag_and_drop()

        minimized_startup = "--minimized" in sys.argv
        try:
            if minimized_startup and platform.system() == "Windows":
                self.root.withdraw()
            else:
                self.root.deiconify()
        except tk.TclError:
            logging.warning("Tried to show window after it was destroyed.")

        self.root.title("BitCrusher V9")

        self.root.geometry("1400x820")
        self.root.minsize(1180, 650)
        self.root.resizable(True, True)
        self.root.geometry("1200x800")
        self.root.configure(bg="#2C2F33")

        self.default_crf = DEFAULT_CRF

        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind("<<Drop>>", self.drop_file_handler)

        self.settings = self.load_settings()
        self.preset_var.set(self.settings.get("preset", list(PRESETS.keys())[0]))
        self.target_size_var.set(self.settings.get("target_size", str(MAX_SIZE_MB_DEFAULT)))
        if "size_unit" in data:
            try: self.size_unit_var.set(data["size_unit"])
            except Exception: pass

        import tkinter as tk  # ensure tk is in scope here

        self.webhook_var = tk.StringVar(value=self.settings.get("webhook_url", ""))
        self.webhook_url = self.webhook_var.get()

        def _sync_webhook_from_var(*_):
            try:
                self.webhook_url = self.webhook_var.get()
            except Exception:
                pass
        self.webhook_var.trace_add("write", lambda *_: _sync_webhook_from_var())

        self.use_webhook.set(self.settings.get("use_webhook", 0))
        adv = self.settings.get("advanced", {})
        self.adv_encoder.set(adv.get("encoder", "x264"))
        self.adv_iterative.set(1 if adv.get("iterative") else 0)
        self.adv_two_pass.set(1 if adv.get("two_pass") else 0)
        self.adv_manual_crf.set(adv.get("manual_crf", ""))
        self.adv_manual_bitrate.set(adv.get("manual_bitrate", ""))
        self.adv_output_prefix.set(adv.get("output_prefix", ""))
        self.adv_output_suffix.set(adv.get("output_suffix", "_discord_ready"))
        self.adv_audio_format.set(adv.get("audio_format", "aac"))
        self.adv_image_format.set(adv.get("image_format", "jpg"))
        self.adv_concurrent.set(1 if adv.get("concurrent") else 0)
        self.adv_auto_output.set(1 if adv.get("auto_output_folder") else 0)
        self.adv_guetzli.set(1 if adv.get("guetzli") else 0)
        self.adv_pngopt.set(1 if adv.get("pngopt") else 0)
        self.adv_auto_jpeg.set(1 if adv.get("auto_jpeg") else 0)
        self.adv_scene_zones.set(1 if adv.get("scene_zones", True) else 0)
        self.adv_hw_decode.set(1 if adv.get("hw_decode", True) else 0)
        self.save_path.set(self.settings.get("output_dir", ""))


        self.webhook = DiscordWebhookClient(self.settings.get("webhook_url", ""))

        # VMAF model preference (auto = prefer v1 when the build has it, else v0.6.1).
        try:
            set_vmaf_model_pref(self.settings.get("vmaf_model", "auto"))
        except Exception:
            pass
        # VMAF floor objective for the min-VMAF gate (window = worst-scene).
        try:
            set_vmaf_objective_pref(self.settings.get("vmaf_objective", "window"))
        except Exception:
            pass

        self.watch_folders = self.settings.get("watch_folders", [])
        self.watcher = FolderWatcher(
            on_file_ready=lambda fp: self._enqueue_from_watcher(fp),
            status_cb=lambda m: self.update_status(m, level="INFO"),
            notify_cb=lambda title, msg: notify_info(title, msg),
            exts=(".mp4", ".mkv", ".mov", ".avi", ".webm",
                  ".mp3", ".flac", ".wav", ".m4a", ".aac",
                  ".jpg", ".jpeg", ".png", ".gif", ".webp"),
            min_bytes=1024,
            ignore_globs=("*.part", "*.tmp", "~$*", "*.crdownload", "*.download"),
            stable_secs=1.25
        )

        for _p in (self.watch_folders or []):
            try:
                self.watcher.add_path(_p)
            except Exception:
                pass

        if self.settings.get("watch_enabled", False):
            self.watcher.start()

        self.watch_folder.set(self.settings.get("watch_folder", ""))
        self.enable_watch.set(self.settings.get("enable_watch", False))

        self.setup_tray_icon()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if self.enable_watch.get():
            self.start_folder_watcher()
        try:
            self._start_ipc_server()   # single-instance Send-To hand-off listener
        except Exception:
            pass
        self.setup_menu()
        self.check_dependencies()

        self.tray_icon = None
        self._tray_icon_ready = False
        self._tray_thread = None

        # Restore the queue (and per-file overrides) from the previous session.
        try:
            self._load_queue()
        except Exception:
            pass






    def _notify(self, title, msg, duration=5, icon_path=None):
        threading.Thread(target=lambda: self.notifier.show_toast(_normalize_text(title), _normalize_text(msg),
                                      icon_path=icon_path, duration=duration),
                         daemon=True).start()

    def select_files(self):
        from tkinter import filedialog
        files = filedialog.askopenfilenames(
            title="Select media files",
            filetypes=[
                ("All Media", "*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm *.m4v *.3gp *.3g2 *.mpeg *.mpg "
                              "*.mp3 *.wav *.aac *.ogg *.flac *.wma *.m4a *.opus *.alac *.aiff *.aif "
                              "*.jpg *.jpeg *.jfif *.png *.webp *.gif *.bmp *.tiff *.tif *.heic *.heif *.jxl *.raw *.avif *.pdf"),
                ("Video", "*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm *.m4v *.3gp *.3g2 *.mpeg *.mpg"),
                ("Audio", "*.mp3 *.wav *.aac *.ogg *.flac *.wma *.m4a *.opus *.alac *.aiff *.aif"),
                ("Images", "*.jpg *.jpeg *.jfif *.png *.webp *.gif *.bmp *.tiff *.tif *.heic *.heif *.jxl *.raw *.avif"),
                ("Documents", "*.pdf"),
                ("All files", "*.*"),
            ]

        )
        if files:

            self.queue_files(files)


    def setup_drag_and_drop(self):

        widgets = [w for w in (getattr(self, "drop_frame", None), getattr(self, "queue_box", None)) if w is not None] # add more if needed

        for w in widgets:
            try:

                ok = False
                try:
                    ver = self.root.tk.call('package', 'require', 'tkdnd')
                    ok = bool(ver)
                except Exception:
                    ok = False

                if ok:
                    from tkinterdnd2 import DND_FILES
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind('<<Drop>>', lambda e: self.drop_file_handler(e.data))
                else:

                    pass
            except Exception as e:

                self.logger.warning(f"Drag-and-drop disabled on this widget: {e}")




    def setup_tray_icon(self):

        icon_path = resource_path("icon.png")
        image = Image.open(resource_path("icon.png"))
        menu = pystray.Menu(
            pystray.MenuItem("Show BitCrusher", self.on_show),
            pystray.MenuItem("Quit",         self.on_quit)
        )
        self.tray = pystray.Icon("BitCrusher", image, "BitCrusher V10", menu)

        threading.Thread(target=self.tray.run, daemon=True).start()

    def on_exit(self):
        
        try:
            self.save_settings()
        except Exception:
            pass
        try:
            self.stop_folder_watcher()
        except Exception:
            pass
        try:
            self.root.quit()   # let mainloop() return
        except Exception:
            pass
        return 0

    def on_quit(self, icon=None, item=None):
        
        try:
            self.save_settings()
        except Exception:
            pass
        try:
            self.root.after(0, self._shutdown_and_exit)
        except Exception:
            self._shutdown_and_exit()

    def on_close(self):

        from tkinter import messagebox
        try:
            self._save_queue()
        except Exception:
            pass
        try:
            resp = messagebox.askyesnocancel(
                "Close or Minimize?",
                "Do you want to exit the app?\n\nYes = Exit\nNo = Minimize to tray\nCancel = Keep running"
            )
        except Exception:
            resp = True
        if resp is True:
            self._shutdown_and_exit()
        elif resp is False:
            try:
                self.ensure_tray_icon(start_if_needed=True)
                self.root.iconify()
            except Exception:
                pass
        else:
            return

    def ensure_tray_icon(self, start_if_needed=False):
        
        if getattr(self, "_tray_icon_ready", False):
            return
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            self._tray_icon_ready = False
            return

        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        drw = ImageDraw.Draw(img)
        drw.ellipse((4, 4, 28, 28), fill=(0, 122, 204, 255))

        def _restore(_icon=None, _item=None):
            self.root.after(0, self.restore_from_tray)

        def _exit(_icon=None, _item=None):
            self.root.after(0, self._shutdown_and_exit)

        menu = pystray.Menu(
            pystray.MenuItem("Restore", _restore, default=True),
            pystray.MenuItem("Exit", _exit)
        )
        self.tray_icon = pystray.Icon("compressor_gui", img, "Compressor", menu)

        if start_if_needed and not getattr(self, "_tray_thread", None):
            import threading
            self._tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
            self._tray_thread.start()
        self._tray_icon_ready = True

    def restore_from_tray(self):
        
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        try:
            if getattr(self, "tray_icon", None):
                self.tray_icon.stop()
        except Exception:
            pass
        finally:
            self._tray_icon_ready = False
            self._tray_thread = None

    def _shutdown_and_exit(self):
        
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True

        try:
            self.save_settings()
        except Exception:
            pass
        try:
            self.stop_folder_watcher()
        except Exception:
            pass
        try:
            if getattr(self, "tray_icon", None):

                try:
                    self.tray_icon.stop()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

        try:
            import os
            os._exit(0)
        except Exception:
            pass







    def on_show(self, icon, item):
        
        try:
            self.root.deiconify()
        except (tk.TclError, RuntimeError):
            logging.warning("Tried to show window from tray, but it was already destroyed.")
        return 0

    def on_quit(self, icon=None, item=None):
        try:
            self.save_settings()
        except Exception:
            pass

        try:
            self.root.after(0, self._shutdown_and_exit)
        except Exception:
            self._shutdown_and_exit()


    def log_info(self, msg):
        if hasattr(self, "logger"):
            self.logger.info(msg)

    def log_warn(self, msg):
        if hasattr(self, "logger"):
            self.logger.warning(msg)

    def log_error(self, msg):
        if hasattr(self, "logger"):
            self.logger.error(msg)

    def log_debug(self, msg):
        if hasattr(self, "logger"):
            self.logger.debug(msg)

    def log_critical(self, msg):
        if hasattr(self, "logger"):
            self.logger.critical(msg)

    def log_exception(self, msg):
        if hasattr(self, "logger"):
            self.logger.exception(msg)

    def select_output_dir(self):
            from tkinter import filedialog, messagebox
            try:
                directory = filedialog.askdirectory(parent=self.root, title="Select Output Folder")
                if directory:
                    self.save_path.set(directory)
                    if hasattr(self, "update_status"):
                        self.update_status(f"Save folder set: {directory}")
                    elif hasattr(self, "log_info"):
                        self.log_info(f"Save folder set: {directory}")
            except Exception as e:
                try:
                    if hasattr(self, "log_exception"):
                        self.log_exception(f"Browse error: {e}")
                finally:
                    try:
                        messagebox.showerror("Browse error", str(e))
                    except Exception:
                        pass


    def open_advanced(self):
        try:

            if hasattr(self, "_ensure_advanced_vars"):
                self._ensure_advanced_vars()
        except Exception:
            pass
        self.open_advanced_options()


    def _ensure_advanced_vars(self):
        
        import tkinter as tk

        try:
            adv = dict(ADVANCED_DEFAULTS)
        except Exception:
            adv = {}
        try:
            if hasattr(self, "settings") and isinstance(self.settings, dict):
                adv.update(self.settings.get("advanced", {}) or {})
        except Exception:
            pass

        spec = [
            ("encoder",              "adv_encoder",          tk.StringVar, "encoder"),
            ("iterative",            "adv_iterative",        tk.IntVar,    "iterative"),
            ("two_pass",             "adv_two_pass",         tk.IntVar,    "two_pass"),
            ("two_pass_fallback",    "adv_two_pass_fallback",tk.IntVar,    "two_pass_fallback"),
            ("manual_crf",           "adv_manual_crf",       tk.StringVar, "manual_crf"),
            ("manual_bitrate",       "adv_manual_bitrate",   tk.StringVar, "manual_bitrate"),
            ("output_prefix",        "adv_output_prefix",    tk.StringVar, "output_prefix"),
            ("output_suffix",        "adv_output_suffix",    tk.StringVar, "output_suffix"),
            ("audio_format",         "adv_audio_format",     tk.StringVar, "audio_format"),
            ("pngopt",               "adv_pngopt",           tk.IntVar,    "pngopt"),
            ("auto_jpeg",            "adv_auto_jpeg",        tk.IntVar,    "auto_jpeg"),
            ("concurrent",           "adv_concurrent",       tk.IntVar,    "concurrent"),
            ("auto_output_folder",   "adv_auto_output",      tk.IntVar,    "auto_output_folder"),
            ("scene_zones",          "adv_scene_zones",      tk.IntVar,    "scene_zones"),
            ("hw_decode",            "adv_hw_decode",        tk.IntVar,    "hw_decode"),
        ]

        for key, attr, Var, dkey in spec:
            if not hasattr(self, attr) or not isinstance(getattr(self, attr), Var):
                try:
                    setattr(self, attr, Var(value=adv.get(dkey, ADVANCED_DEFAULTS.get(dkey))))
                except Exception:

                    v = Var()
                    try:
                        v.set(ADVANCED_DEFAULTS.get(dkey))
                    except Exception:
                        pass
                    setattr(self, attr, v)
        if not hasattr(self, "adv_quality_first"):
            try:
                _qm = str(adv.get("quality_mode", ADVANCED_DEFAULTS.get("quality_mode", "max")) or "").strip().lower()
                # quality-first == any mode except "fast" (legacy boolean view of the tri-state)
                self.adv_quality_first = tk.IntVar(value=0 if _qm == "fast" else 1)
            except Exception:
                self.adv_quality_first = tk.IntVar(value=1)
        if not hasattr(self, "adv_quality_mode"):
            try:
                _qm = str(adv.get("quality_mode", ADVANCED_DEFAULTS.get("quality_mode", "max")) or "").strip().lower()
                self.adv_quality_mode = tk.StringVar(value=(_qm if _qm in ("fast", "balanced", "max") else "max"))
            except Exception:
                self.adv_quality_mode = tk.StringVar(value="max")
        # Back-compat aliases from older variable names.
        if hasattr(self, "adv_audio_fmt") and not hasattr(self, "adv_audio_format"):
            self.adv_audio_format = self.adv_audio_fmt
        elif hasattr(self, "adv_audio_fmt") and hasattr(self, "adv_audio_format"):
            try:
                self.adv_audio_format.set(self.adv_audio_fmt.get())
            except Exception:
                pass
        if hasattr(self, "adv_auto_output_folder") and not hasattr(self, "adv_auto_output"):
            self.adv_auto_output = self.adv_auto_output_folder
        elif hasattr(self, "adv_auto_output_folder") and hasattr(self, "adv_auto_output"):
            try:
                self.adv_auto_output.set(self.adv_auto_output_folder.get())
            except Exception:
                pass


    def queue_file(self, filepath):

        self.queue_files([filepath])


    def _enqueue_from_watcher(self, filepath: str):
        # Watchdog callbacks arrive on a non-Tk thread; queue mutations touch
        # the Treeview, so re-dispatch to the main thread.
        try:
            if threading.current_thread() is not threading.main_thread():
                self.root.after(0, lambda p=filepath: self._enqueue_from_watcher(p))
                return
        except Exception:
            pass
        try:
            self._apply_watch_rules_to_file(filepath)
            self.queue_file(filepath)
            self.update_status(f"Queued via Watcher: {filepath}")
            try:
                notify_info("BitCrusher", f"Queued via Watcher:\n{os.path.basename(filepath)}", duration=4)
            except Exception:
                pass
            # Pipeline mode: zero-touch watch → compress → webhook. Debounce so a
            # burst of files dropped together starts ONE batch, not one per file.
            if self._pipeline_enabled():
                self._pipeline_kick()
        except Exception as e:
            self.update_status(f"Watcher enqueue failed: {e}", level="ERROR")

    def _pipeline_enabled(self) -> bool:
        try:
            if hasattr(self, "pipeline_var"):
                return bool(self.pipeline_var.get())
        except Exception:
            pass
        return bool((getattr(self, "settings", {}) or {}).get("pipeline_mode", False))

    def _pipeline_kick(self):
        """Debounced auto-start of the queue for pipeline mode (main thread)."""
        try:
            if getattr(self, "_pipeline_timer", None) is not None:
                try:
                    self.root.after_cancel(self._pipeline_timer)
                except Exception:
                    pass
            self._pipeline_timer = self.root.after(1500, self._pipeline_start_if_idle)
        except Exception:
            # No event loop available (headless/tests) — start directly.
            self._pipeline_start_if_idle()

    def _pipeline_start_if_idle(self):
        self._pipeline_timer = None
        try:
            if getattr(self, "compression_running", False):
                return  # a batch is already draining the queue; new files ride along
            if not self._pipeline_enabled():
                return
            self.update_status("[Pipeline] Auto-starting compression for watched files.",
                               level="INFO")
            self.start_compression()
        except Exception as e:
            self.update_status(f"Pipeline auto-start failed: {e}", level="ERROR")

    def _start_ipc_server(self):
        """
        Listen on the loopback IPC port so a later 'Send to BitCrusher' invocation
        (or `--enqueue`) hands its files to THIS running instance instead of
        launching a second copy. If the bind fails, another instance already owns
        the port — we simply don't listen (single-instance handoff still works,
        just pointed at that other instance).
        """
        import socket, threading
        if getattr(self, "_ipc_srv", None) is not None:
            return

        def _serve():
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # Deliberately NOT SO_REUSEADDR: on Windows that would let a second
                # instance also bind, defeating the single-owner guarantee.
                srv.bind((_BC_IPC_HOST, _BC_IPC_PORT))
                srv.listen(8)
            except OSError:
                return  # another instance is the listener; nothing to do here
            self._ipc_srv = srv
            while not getattr(self, "_ipc_stop", False):
                try:
                    conn, _ = srv.accept()
                except Exception:
                    break
                try:
                    conn.settimeout(3.0)
                    chunks = []
                    while True:
                        b = conn.recv(4096)
                        if not b:
                            break
                        chunks.append(b)
                    data = b"".join(chunks).decode("utf-8", "replace")
                except Exception:
                    data = ""
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
                lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
                if lines and lines[0] == "BCENQUEUE":
                    for p in lines[1:]:
                        self.root.after(0, lambda pp=p: self._ipc_enqueue(pp))

        threading.Thread(target=_serve, name="bc_ipc", daemon=True).start()

    def _ipc_enqueue(self, path: str):
        """Enqueue a file handed over by Send-To / --enqueue (main thread)."""
        try:
            if not path or not os.path.isfile(path):
                return
            self._apply_watch_rules_to_file(path)
            self.queue_file(path)
            self.update_status(f"Queued via Send To: {os.path.basename(path)}")
            try:
                self.root.deiconify(); self.root.lift(); self.root.focus_force()
            except Exception:
                pass
            if self._pipeline_enabled():
                self._pipeline_kick()
        except Exception as e:
            self.update_status(f"Send-To enqueue failed: {e}", level="ERROR")

    def register_send_to_menu(self):
        from tkinter import messagebox
        ok, msg = register_send_to()
        try:
            (messagebox.showinfo if ok else messagebox.showerror)("Send To", msg)
        except Exception:
            self.update_status(msg, level=("INFO" if ok else "ERROR"))

    def unregister_send_to_menu(self):
        from tkinter import messagebox
        ok, msg = unregister_send_to()
        try:
            messagebox.showinfo("Send To", msg)
        except Exception:
            self.update_status(msg, level="INFO")

    # ---- Watcher rules -------------------------------------------------------
    # Optional per-file conditions applied to files picked up by the folder
    # watcher (or Send-To): size/duration → target-size or encoder overrides,
    # plus a custom output folder. Every field is optional; blanks mean
    # "fall back to the global setting".
    _WATCH_RULE_KEYS = ("save_dir", "target_mb", "encoder",
                        "big_mb", "big_target", "small_mb", "small_target",
                        "long_min", "long_enc", "short_min", "short_enc")

    def _ensure_watch_rule_vars(self):
        """Create the dialog StringVars, (re)synced from the committed settings so a
        previously-cancelled edit doesn't linger when the dialog re-opens."""
        import tkinter as tk
        saved = {}
        try:
            saved = dict((getattr(self, "settings", {}) or {}).get("watch_rules", {}) or {})
        except Exception:
            saved = {}
        if not getattr(self, "wr_vars", None):
            self.wr_vars = {k: tk.StringVar() for k in self._WATCH_RULE_KEYS}
        for k in self._WATCH_RULE_KEYS:
            try:
                self.wr_vars[k].set(str(saved.get(k, "") or ""))
            except Exception:
                pass

    def _gather_watch_rules(self) -> dict:
        """Committed watcher-rule values (from settings) as stripped strings. This
        is what runtime rule-matching reads, so uncommitted dialog edits or a
        cancelled dialog never take effect."""
        saved = (getattr(self, "settings", {}) or {}).get("watch_rules", {}) or {}
        return {k: str(saved.get(k, "") or "").strip() for k in self._WATCH_RULE_KEYS}

    def _save_watch_rules(self):
        """Commit the dialog StringVars into settings (called on the OK button)."""
        vars_ = getattr(self, "wr_vars", None)
        if not vars_:
            return
        try:
            self.settings["watch_rules"] = {
                k: str(vars_[k].get()).strip()
                for k in self._WATCH_RULE_KEYS if str(vars_[k].get()).strip()
            }
        except Exception:
            pass

    @staticmethod
    def _wr_num(val):
        try:
            s = str(val).strip()
            return float(s) if s != "" else None
        except Exception:
            return None

    def _probe_duration_seconds(self, path) -> float:
        try:
            mt = get_media_type(path)
            if mt == "video":
                dur, _w, _h, _br, _fr = get_video_metadata(path)
                return float(dur or 0.0)
            if mt == "audio":
                return float((_probe_audio_meta(path) or {}).get("duration") or 0.0)
        except Exception:
            pass
        return 0.0

    def _watch_rules_overrides(self, path) -> dict:
        """
        Resolve the watcher rules against one file into per-file overrides:
        {encoder?, _watch_target_bytes?, _watch_save_dir?}. Conditional rules
        (size/duration) win over the plain watched defaults; anything left blank
        leaves the global setting in charge.
        """
        rules = self._gather_watch_rules()
        if not any(rules.values()):
            return {}
        overrides = {}

        save_dir = rules.get("save_dir") or ""
        if save_dir:
            overrides["_watch_save_dir"] = save_dir

        # Target size: watched default, then size-conditional overrides.
        target_mb = self._wr_num(rules.get("target_mb"))
        try:
            size_mb = os.path.getsize(path) / (1024.0 * 1024.0)
        except Exception:
            size_mb = 0.0
        big_mb, big_t = self._wr_num(rules.get("big_mb")), self._wr_num(rules.get("big_target"))
        small_mb, small_t = self._wr_num(rules.get("small_mb")), self._wr_num(rules.get("small_target"))
        if big_mb is not None and big_t is not None and size_mb > big_mb:
            target_mb = big_t
        if small_mb is not None and small_t is not None and size_mb < small_mb:
            target_mb = small_t
        if target_mb is not None and target_mb > 0:
            overrides["_watch_target_bytes"] = int(target_mb * 1024 * 1024)

        # Encoder: watched default, then duration-conditional overrides.
        encoder = (rules.get("encoder") or "").strip()
        long_min, short_min = self._wr_num(rules.get("long_min")), self._wr_num(rules.get("short_min"))
        if (long_min is not None and rules.get("long_enc")) or (short_min is not None and rules.get("short_enc")):
            dur_min = self._probe_duration_seconds(path) / 60.0
            if long_min is not None and rules.get("long_enc") and dur_min > long_min:
                encoder = rules["long_enc"].strip()
            if short_min is not None and rules.get("short_enc") and dur_min < short_min:
                encoder = rules["short_enc"].strip()
        if encoder:
            overrides["encoder"] = encoder
            # A watcher rule that names an encoder is an explicit choice — pin it
            # so the VMAF codec race can't quietly swap it for something else.
            overrides["codec_pinned"] = True
            overrides["auto_codec"] = False

        return overrides

    def _apply_watch_rules_to_file(self, path):
        """Compute and stash per-file overrides for a watcher/Send-To file."""
        try:
            ov = self._watch_rules_overrides(path)
        except Exception:
            ov = {}
        if not ov:
            return
        if not hasattr(self, "per_file_opts") or not isinstance(getattr(self, "per_file_opts", None), dict):
            self.per_file_opts = {}
        cur = dict(self.per_file_opts.get(path, {}) or {})
        cur.update(ov)
        self.per_file_opts[path] = cur
        try:
            bits = []
            if "_watch_target_bytes" in ov:
                bits.append(f"target {human_bytes(ov['_watch_target_bytes'])}")
            if "encoder" in ov:
                bits.append(f"encoder {ov['encoder']}")
            if "_watch_save_dir" in ov:
                bits.append(f"→ {ov['_watch_save_dir']}")
            if bits:
                self.update_status(f"Watcher rule applied to {os.path.basename(path)}: "
                                   + ", ".join(bits), level="INFO")
        except Exception:
            pass

    def _refresh_display_panel(self):
        
        fpath = None
        try:
            qb = getattr(self, "queue_box", None)
            if qb is not None:
                sel = qb.curselection()
                if sel:
                    try:
                        fpath = qb.get(sel[0])
                    except Exception:
                        fpath = None
        except Exception:
            fpath = None

        mode = self.display_mode_var.get() if hasattr(self, "display_mode_var") else "Quality Metrics"
        try:
            if mode == "Quality Metrics":
                self._populate_metrics(fpath)
            elif mode == "Advisor Insights":
                self._populate_insights(fpath)
            elif mode == "History":
                self._populate_history()
            else:
                pass
        except Exception:
            pass

    def _set_default_layout(self):
        
        try:
            paned = getattr(self, "paned", None)
            if paned is None:
                return

            self.root.update_idletasks()
            total = max(960, self.root.winfo_width())

            w_left  = int(total * 0.40)
            w_mid   = int(total * 0.34)

            paned.sashpos(0, w_left)

            paned.sashpos(1, w_left + w_mid)

            try:
                paned.paneconfig(paned.panes()[0], minsize=360)   # left
                paned.paneconfig(paned.panes()[1], minsize=320)   # mid
                paned.paneconfig(paned.panes()[2], minsize=300)   # right
            except Exception:
                pass
        except Exception:
            pass


    def _last_encode_lines(self, fpath) -> list:
        """Session batch result for this file, as display lines (fast, no I/O)."""
        out = []
        try:
            _norm_sel = _normalize_drop_path(fpath)
            for _r in reversed(list(getattr(self, "batch_results", []) or [])):
                if _normalize_drop_path(str(_r.get("path") or "")) == _norm_sel:
                    out.append("")
                    out.append("Last encode:")
                    if _r.get("ok"):
                        _ib, _ob = int(_r.get("in_bytes") or 0), int(_r.get("out_bytes") or 0)
                        if _ib and _ob:
                            out.append(f"  {human_bytes(_ib)} -> {human_bytes(_ob)} "
                                       f"({_ob * 100.0 / _ib:.0f}% of source)")
                        _v = _r.get("vmaf")
                        if isinstance(_v, (int, float)):
                            out.append(f"  VMAF: {float(_v):.1f}")
                        if _r.get("encoder"):
                            out.append(f"  Encoder: {_r.get('encoder')}")
                        out.append(f"  Time: {float(_r.get('secs') or 0.0):.0f}s")
                    else:
                        out.append(f"  FAILED: {_r.get('error') or 'unknown error'}")
                    break
        except Exception:
            pass
        return out

    def _populate_metrics(self, fpath):
        # Runs on the main thread from a debounced selection event. The heavy
        # work (ffprobe feature extraction + advisor bitrate solve) is pushed to
        # a worker thread so clicking a queue row never freezes the UI — that
        # freeze was also what made drag-and-drop feel broken.
        if not hasattr(self, "_metrics_text"):
            return
        txt = self._metrics_text

        def _set(s):
            try:
                txt.configure(state="normal")
                txt.delete("1.0", "end")
                txt.insert("end", s)
                txt.configure(state="disabled")
            except Exception:
                pass

        if not fpath or not os.path.exists(fpath):
            _set("No file selected.")
            return

        key = _normalize_drop_path(fpath)
        cache = self.__dict__.setdefault("_metrics_cache", {})
        if key in cache:
            _set("\n".join(cache[key] + self._last_encode_lines(fpath)))
            return

        _set(f"Analyzing {os.path.basename(fpath)}…")
        token = int(self.__dict__.get("_metrics_token", 0)) + 1
        self._metrics_token = token

        try:
            tgt_bytes = int(self._get_target_bytes())
        except Exception:
            tgt_bytes = 10 * 1024 * 1024
        enc = (self.adv_encoder.get() if hasattr(self, "adv_encoder") else "x265") or "x265"

        def _work():
            base_lines = []
            try:
                from ml_heuristics import extract_media_features
                from ai_advisor import choose_bitrates_advised as _choose
                feats = extract_media_features(fpath) or {}
                dur = float(feats.get("duration", 0.0) or 0.0)
                base_lines = [
                    f"File: {os.path.basename(fpath)}",
                    f"Duration: {dur:.1f}s   |   {int(feats.get('width',0) or 0)}"
                    f"x{int(feats.get('height',0) or 0)} @ {float(feats.get('fps',0.0) or 0.0):.2f} fps",
                ]
                try:
                    v_bps, a_bps, ov = _choose(
                        dur, tgt_bytes, encoder=enc,
                        stats_dir=os.path.join(USER_SETTINGS_DIR, "stats"),
                        width_hint=int(feats.get("width", 0) or 0),
                        fps_hint=float(feats.get("fps", 0.0) or 0.0),
                        input_path=fpath)
                    if v_bps:
                        base_lines.append(f"Planned bitrate → video {int(v_bps)//1000} kbps · "
                                          f"audio {int(a_bps)//1000} kbps  (overshoot guard {ov:.2f})")
                except Exception:
                    pass
            except Exception as e:
                base_lines = [f"Could not analyze file: {type(e).__name__}"]
            cache[key] = base_lines
            if int(self.__dict__.get("_metrics_token", 0)) == token:
                self._ui(_set, "\n".join(base_lines + self._last_encode_lines(fpath)))

        threading.Thread(target=_work, daemon=True, name="bc_metrics").start()

    def _schedule_display_refresh(self, delay_ms: int = 150):
        """Debounce queue-selection so rapid clicks don't stack heavy refreshes."""
        job = self.__dict__.get("_disp_job")
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._disp_job = self.root.after(delay_ms, self._refresh_display_panel)

    def _populate_insights(self, fpath):
        if not hasattr(self, "_insights_text"): return
        import textwrap, datetime as _dt
        self._insights_text.configure(state="normal"); self._insights_text.delete("1.0","end")
        if not fpath or not os.path.exists(fpath):
            self._insights_text.insert("end", "No file selected.")
            self._insights_text.configure(state="disabled"); return
        try:
            from ai_advisor import advisor_preview_for_gui as _advisor_popup
        except Exception:
            _advisor_popup = None
        tips = [
            "• High motion detected? Consider raising target size by 10–20% or enabling grain preset.",
            "• Dialogue-heavy? Lower audio to 96–128 kbps stereo; move bits to video.",
            "• Low-entropy static scenes compress well; you can drop CRF or target by ~10%."
        ]
        self._insights_text.insert("end", "Advisor Insights\n\n" + "\n".join(tips))
        self._insights_text.configure(state="disabled")

    def _populate_history(self):
        try:
            from smart_rate import load_stats as _load_stats
            stats = _load_stats(os.path.join(self._ScriptDir, ".smart"))
        except Exception:
            stats = {"overshoot": {}}
        if not hasattr(self, "_hist"): return
        for r in self._hist.get_children(): self._hist.delete(r)
        ov = stats.get("overshoot", {}) or {}
        for key, val in sorted(ov.items()):
            enc, cont, resB, fpsB = key.split("|")
            self._hist.insert("", "end", values=(
                time.strftime("%Y-%m-%d %H:%M", time.localtime(stats.get("updated_at", int(time.time())))),
                f"{resB}@{fpsB}", "-", enc, "-", f"{float(val):.3f}"
            ))

    def _open_visual_compare_for_selection(self):
        
        import os, glob
        import tkinter as tk
        from tkinter import messagebox as mbox

        try:
            from visual_compare import open_compare_viewer as _ext_view
        except Exception:
            _ext_view = None

        qb = getattr(self, "queue_box", None)
        if qb is None:
            return

        sel = qb.curselection()
        if not sel:
            try:
                mbox.showinfo("Visual Compare", "Select a source file in the queue first.")
            except Exception:
                pass
            return

        src = qb.get(sel[0])

        outp = getattr(self, "_last_output_path", None)

        if not outp or not os.path.exists(outp):
            try:
                save_dir = self.save_path.get() if hasattr(self, "save_path") else (os.path.dirname(src) or ".")
            except Exception:
                save_dir = os.path.dirname(src) or "."
            stem = os.path.splitext(os.path.basename(src))[0]
            try:
                candidates = sorted(
                    glob.glob(os.path.join(save_dir, f"*{stem}*")),
                    key=lambda p: os.path.getmtime(p),
                    reverse=True,
                )
                outp = candidates[0] if candidates else None
            except Exception:
                outp = None

        if not (os.path.exists(src) and outp and os.path.exists(outp)):
            try:
                mbox.showwarning("Visual Compare", "No recent output found for this input.")
            except Exception:
                pass
            return

        dur_hint = 60.0
        try:
            from ml_heuristics import extract_media_features
            feats = extract_media_features(src)
            dur_hint = float(feats.get("duration", 60.0) or 60.0)
        except Exception:
            pass

        if _ext_view:
            _ext_view(self.root, original_path=src, compressed_path=outp, duration_hint=dur_hint)
        else:

            self._fallback_compare_viewer(src, outp, duration_hint=dur_hint)

    def _fallback_compare_viewer(self, original_path: str, compressed_path: str, duration_hint: float = 60.0):
        
        import os, glob, tempfile, shutil, subprocess
        import tkinter as tk
        from tkinter import ttk, messagebox as mbox
        try:
            from PIL import Image, ImageTk
        except Exception:
            mbox.showerror("Visual Compare", "Pillow (PIL) is required for the fallback viewer.")
            return

        top = tk.Toplevel(self.root)
        top.title("Visual Compare — Fallback")
        try:
            top.configure(bg="#2C2F33")
        except Exception:
            pass
        top.geometry("1200x700")

        left = tk.Label(top, bg="#000")
        right = tk.Label(top, bg="#000")
        left.pack(side="left", expand=True, fill="both")
        right.pack(side="right", expand=True, fill="both")

        bar = ttk.Frame(top); bar.pack(side="bottom", fill="x")
        status_lbl = ttk.Label(bar, text=f"{os.path.basename(original_path)}  ⇄  {os.path.basename(compressed_path)}")
        status_lbl.pack(side="left", padx=10, pady=6)

        tmpdir = tempfile.mkdtemp(prefix="bc_compare_")
        o_dir = os.path.join(tmpdir, "orig")
        c_dir = os.path.join(tmpdir, "comp")
        os.makedirs(o_dir, exist_ok=True); os.makedirs(c_dir, exist_ok=True)

        clip = float(duration_hint or 60.0)
        clip = max(2.0, min(15.0, clip))  # keep it snappy
        fps = 12
        vf = f"scale=540:-2,fps={fps}"

        def _extract(src, outdir):
            pat = os.path.join(outdir, "f_%05d.jpg")
            cmd = [FFMPEG, "-hide_banner", "-y", "-ss", "0", "-t", f"{clip:.2f}", "-i", src, "-vf", vf, pat]
            try:
                _sp_check_output(cmd, stderr=subprocess.STDOUT)
            except Exception as e:
                self.log_error(f"ffmpeg frame extract failed: {e}") if hasattr(self, "log_error") else None

        _extract(original_path, o_dir)
        _extract(compressed_path, c_dir)

        lf = sorted(glob.glob(os.path.join(o_dir, "f_*.jpg")))
        rf = sorted(glob.glob(os.path.join(c_dir, "f_*.jpg")))
        total = min(len(lf), len(rf))
        if total == 0:
            try:
                mbox.showerror("Visual Compare", "No frames extracted — cannot preview.")
            except Exception:
                pass
            shutil.rmtree(tmpdir, ignore_errors=True)
            top.destroy()
            return

        max_frames = min(total, 180)  # ≤15s at 12fps
        left_imgs = [ImageTk.PhotoImage(Image.open(p)) for p in lf[:max_frames]]
        right_imgs = [ImageTk.PhotoImage(Image.open(p)) for p in rf[:max_frames]]
        total = min(len(left_imgs), len(right_imgs))

        idx = {"i": 0, "playing": True}

        def _draw(i: int):
            try:
                left.configure(image=left_imgs[i]); left.image = left_imgs[i]
                right.configure(image=right_imgs[i]); right.image = right_imgs[i]
            except Exception:
                pass

        def _tick():
            if not idx["playing"]:
                top.after(80, _tick); return
            idx["i"] = (idx["i"] + 1) % total
            _draw(idx["i"])
            top.after(int(1000 / fps), _tick)

        def _toggle():
            idx["playing"] = not idx["playing"]
            btn_play.configure(text=("Pause" if idx["playing"] else "Play"))

        def _on_close():
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass
            try:
                top.destroy()
            except Exception:
                pass

        btn_play = ttk.Button(bar, text="Pause", command=_toggle); btn_play.pack(side="left", padx=8, pady=6)
        btn_close = ttk.Button(bar, text="Close", command=_on_close); btn_close.pack(side="left", padx=8, pady=6)

        _draw(0)
        top.after(int(1000 / fps), _tick)
        top.protocol("WM_DELETE_WINDOW", _on_close)


    def queue_files(self, filepaths):
        
        for filepath in filepaths:
            if (
                os.path.isfile(filepath)
                and get_media_type(filepath) != "unknown"
                and filepath not in self.file_list
            ):
                _norm = _normalize_drop_path(filepath)
                try:
                    if not hasattr(self, "file_list"):
                        self.file_list = []
                    if _norm not in self.file_list:
                        self.file_list.append(_norm)
                except Exception:
                    pass
                self.queue_box.insert("end", _norm)





    def make_responsive(self):
        

        for r in range(6):
            self.root.grid_rowconfigure(r, weight=0)

        self.root.grid_rowconfigure(1, weight=3)  # drop+queue+preview
        self.root.grid_rowconfigure(4, weight=2)  # stats table
        self.root.grid_rowconfigure(5, weight=1)  # log pane

        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=1)


    def setup_menu(self):
        import tkinter as tk

        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label=self._t("menu.clear_queue","Clear Queue"), command=self.clear_queue)
        file_menu.add_command(label="Scan Queue for Duplicates...", command=self.scan_for_duplicates)
        file_menu.add_command(label=self._t("menu.exit","Exit"), command=self.on_exit)
        menubar.add_cascade(label=self._t("menu.file","File"), menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label=self._t("menu.configure_paths","Configure Paths"), command=self.open_settings_dialog)
        settings_menu.add_command(label=self._t("menu.save_profile","Save Profile"), command=self.save_profile)
        settings_menu.add_command(label=self._t("menu.load_profile","Load Profile"), command=self.load_profile)
        settings_menu.add_command(label="Advanced Options...", command=self.open_advanced)
        settings_menu.add_separator()
        settings_menu.add_command(label="Add 'Send to BitCrusher' (right-click menu)",
                                  command=self.register_send_to_menu)
        settings_menu.add_command(label="Remove 'Send to BitCrusher'",
                                  command=self.unregister_send_to_menu)
        menubar.add_cascade(label=self._t("menu.settings","Settings"), menu=settings_menu)

        themes_menu = tk.Menu(menubar, tearoff=0)
        for name in list(THEMES.keys()):
            themes_menu.add_radiobutton(
                label=name,
                variable=self.theme_var,
                value=name,
                command=lambda n=name: self._on_theme_select(n)
            )
        themes_menu.add_separator()
        themes_menu.add_command(label=self._t("menu.theme_lab","Theme Lab..."), command=lambda: open_theme_lab(self))
        themes_menu.add_command(label=self._t("menu.save_theme","Save Current Theme..."), command=lambda: save_current_theme_as(self))
        themes_menu.add_command(label=self._t("menu.load_theme","Load Theme JSON..."), command=lambda: load_custom_theme(self))
        menubar.add_cascade(label=self._t("menu.themes","Themes"), menu=themes_menu)

        guide_menu = tk.Menu(menubar, tearoff=0)
        guide_menu.add_command(label=self._t("menu.user_guide","User Guide"), command=self.show_user_guide)
        menubar.add_cascade(label=self._t("menu.guide","Guide"), menu=guide_menu)

        viewm = tk.Menu(menubar, tearoff=0)
        viewm.add_command(label=self._t("menu.dashboard","Dashboard"), command=self.show_dashboard)
        menubar.add_cascade(label=self._t("menu.view","View"), menu=viewm)

        lang_menu = tk.Menu(menubar, tearoff=0)
        for code in _language_codes_ordered():
            lang_menu.add_radiobutton(
                label=_language_menu_label(code),
                variable=self.lang_var,
                value=code,
                command=self._on_language_change
            )
        lang_menu.add_separator()
        lang_menu.add_command(label=self._t("menu.open_i18n_folder","Open i18n Folder..."),
                              command=lambda: _open_folder(_i18n_dir()))
        lang_menu.add_command(label=self._t("menu.export_lang_templates","Export Language Templates..."),
                              command=lambda: (_export_lang_templates([c for c,_ in LANG_CODES if c!="en"]),
                                               _open_folder(_i18n_dir())))
        lang_menu.add_command(label=self._t("menu.language_manager","Language Manager..."),
                              command=self.open_language_manager)
        menubar.add_cascade(label=self._t("menu_language","Language"), menu=lang_menu)

        self.root.config(menu=menubar)
        init_aesthetics(self)

        try:
            items = [menubar.entrycget(i, "label") for i in range(menubar.index("end")+1)]
            LOG.info("Menubar entries: %s", items)
        except Exception:
            pass

    def open_language_manager(self):
        import tkinter as tk
        from tkinter import ttk, messagebox

        _load_lang_packs()

        win = tk.Toplevel(self.root)
        win.title("Language Manager")
        win.geometry("720x420")
        win.transient(self.root)

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        cols = ("code", "name", "coverage", "source")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        tree.heading("code", text="Code")
        tree.heading("name", text="Display Name")
        tree.heading("coverage", text="Coverage")
        tree.heading("source", text="Source")
        tree.column("code", width=90, anchor="w")
        tree.column("name", width=300, anchor="w")
        tree.column("coverage", width=120, anchor="w")
        tree.column("source", width=120, anchor="w")
        tree.pack(fill="both", expand=True)

        hint = tk.Label(
            frame,
            text="Coverage is relative to English base keys. Edit user_settings/i18n/<code>.json and click Reload.",
            anchor="w",
            justify="left",
        )
        hint.pack(fill="x", pady=(8, 4))

        btns = tk.Frame(frame)
        btns.pack(fill="x", pady=(4, 0))

        def _refresh_rows():
            _load_lang_packs()
            tree.delete(*tree.get_children())
            for code in _language_codes_ordered():
                tree.insert(
                    "",
                    "end",
                    values=(
                        code,
                        LANG_DISPLAY.get(code, LANG_CODE_NAME.get(code, code)),
                        f"{int(LANG_COVERAGE.get(code, 0))}%",
                        LANG_SOURCE.get(code, "fallback"),
                    ),
                )

        def _use_selected():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Language Manager", "Select a language first.")
                return
            vals = tree.item(sel[0], "values")
            if not vals:
                return
            code = str(vals[0])
            self.lang_var.set(code)
            self._on_language_change()
            try:
                win.destroy()
            except Exception:
                pass

        ttk.Button(btns, text="Reload", command=_refresh_rows).pack(side="left")
        ttk.Button(btns, text="Export Templates", command=lambda: (_export_lang_templates(), _refresh_rows())).pack(side="left", padx=6)
        ttk.Button(btns, text="Open i18n Folder", command=lambda: _open_folder(_i18n_dir())).pack(side="left", padx=6)
        ttk.Button(btns, text="Use Selected", command=_use_selected).pack(side="right")

        _refresh_rows()
    def show_dashboard(self):
        
        import tkinter as tk
        from tkinter import ttk

        if getattr(self, "_dash_win", None) and tk.Toplevel.winfo_exists(self._dash_win):
            try:
                self._dash_win.lift()
                self._dash_win.focus_force()
            except Exception:
                pass
            return

        win = tk.Toplevel(self.root)
        self._dash_win = win
        win.title("BitCrusher - Dashboard")
        win.geometry("520x360")
        win.resizable(False, False)

        container = ttk.Frame(win, padding=12)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text="Runtime Metrics", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        rows = [
            ("Queue (pending)", "queue_pending"),
            ("Processing", "processing"),
            ("Processed (this run)", "processed"),
            ("Average Size Ratio", "avg_ratio"),
            ("Average Time / File", "avg_time"),
            ("Watcher", "watcher"),
            ("Watched Folders", "watch_dirs"),
            ("Webhook", "webhook"),
        ]
        self._dash_vars = {}
        r = 1
        for label, key in rows:
            ttk.Label(container, text=label + ":").grid(row=r, column=0, sticky="w", padx=(0, 10), pady=4)
            var = tk.StringVar(value="—")
            self._dash_vars[key] = var
            ttk.Label(container, textvariable=var).grid(row=r, column=1, sticky="w", pady=4)
            r += 1

        ttk.Separator(container).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(10, 8))
        r += 1
        ttk.Button(container, text="Close", command=win.destroy).grid(row=r, column=1, sticky="e")

        def _queue_len():
            for attr in ("queue_files", "file_queue", "files_to_process", "pending_files", "queued_files"):
                q = getattr(self, attr, None)
                if isinstance(q, (list, tuple, set)):
                    return len(q)
                if hasattr(q, "__len__"):
                    try:
                        return len(q)
                    except Exception:
                        pass
            return 0

        def _is_processing():
            for attr in ("_is_processing", "is_processing", "processing"):
                v = getattr(self, attr, None)
                if isinstance(v, bool):
                    return v
            for attr in ("_worker_running", "worker_running"):
                v = getattr(self, attr, None)
                if isinstance(v, bool):
                    return v
            return False

        def _processed_count():
            lst = getattr(self, "stats_list", None)
            return len(lst) if isinstance(lst, list) else 0

        def _avg_ratio_and_time():
            lst = getattr(self, "stats_list", None)
            if not isinstance(lst, list) or not lst:
                return ("—", "—")
            ratios, times = [], []
            for rec in lst:
                try:
                    if "ratio" in rec:
                        ratios.append(float(rec["ratio"]))
                    elif "original_size" in rec and "compressed_size" in rec:
                        o = float(rec["original_size"]) or 1.0
                        c = float(rec["compressed_size"])
                        ratios.append(c / o)
                    if "time_taken" in rec:
                        times.append(float(rec["time_taken"]))
                except Exception:
                    pass
            avg_r = (sum(ratios) / len(ratios)) if ratios else None
            avg_t = (sum(times) / len(times)) if times else None
            r_txt = f"{avg_r*100:.1f}%" if isinstance(avg_r, float) else "—"
            t_txt = f"{avg_t:.2f}s" if isinstance(avg_t, float) else "—"
            return (r_txt, t_txt)

        def _watcher_status():
            enabled = bool(self.settings.get("watch_enabled", False))
            active = False
            try:
                w = getattr(self, "watcher", None)
                active = bool(getattr(w, "_running", False))
            except Exception:
                pass
            return "On (active)" if enabled and active else ("On (idle)" if enabled else "Off")

        def _watch_dirs():
            try:
                dirs = list(self.settings.get("watch_folders", []) or [])
            except Exception:
                dirs = []
            return str(len(dirs)) if dirs else "0"

        def _webhook_status():
            url = ""
            try:
                url = self.settings.get("webhook_url", "") or getattr(self, "webhook_url_var", None).get()
            except Exception:
                pass
            if url:
                masked = url[:40] + "..." if len(url) > 41 else url
                return f"Configured ({masked})"
            return "Not set"

        def _refresh():
            try:
                self._dash_vars["queue_pending"].set(str(_queue_len()))
                self._dash_vars["processing"].set("Yes" if _is_processing() else "No")
                self._dash_vars["processed"].set(str(_processed_count()))
                r_txt, t_txt = _avg_ratio_and_time()
                self._dash_vars["avg_ratio"].set(r_txt)
                self._dash_vars["avg_time"].set(t_txt)
                self._dash_vars["watcher"].set(_watcher_status())
                self._dash_vars["watch_dirs"].set(_watch_dirs())
                self._dash_vars["webhook"].set(_webhook_status())
            except Exception:
                pass
            try:
                win.after(1000, _refresh)
            except Exception:
                pass

        _refresh()



    def open_language_manager(self):
        import tkinter as tk
        from tkinter import ttk, messagebox

        _load_lang_packs()

        win = tk.Toplevel(self.root)
        win.title("Language Manager")
        win.geometry("720x420")
        win.transient(self.root)

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        cols = ("code", "name", "coverage", "source")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        tree.heading("code", text="Code")
        tree.heading("name", text="Display Name")
        tree.heading("coverage", text="Coverage")
        tree.heading("source", text="Source")
        tree.column("code", width=90, anchor="w")
        tree.column("name", width=300, anchor="w")
        tree.column("coverage", width=120, anchor="w")
        tree.column("source", width=120, anchor="w")
        tree.pack(fill="both", expand=True)

        hint = tk.Label(
            frame,
            text="Coverage is relative to English base keys. Edit user_settings/i18n/<code>.json and click Reload.",
            anchor="w",
            justify="left",
        )
        hint.pack(fill="x", pady=(8, 4))

        btns = tk.Frame(frame)
        btns.pack(fill="x", pady=(4, 0))

        def _refresh_rows():
            _load_lang_packs()
            tree.delete(*tree.get_children())
            for code in _language_codes_ordered():
                tree.insert(
                    "",
                    "end",
                    values=(
                        code,
                        LANG_DISPLAY.get(code, LANG_CODE_NAME.get(code, code)),
                        f"{int(LANG_COVERAGE.get(code, 0))}%",
                        LANG_SOURCE.get(code, "fallback"),
                    ),
                )

        def _use_selected():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Language Manager", "Select a language first.")
                return
            vals = tree.item(sel[0], "values")
            if not vals:
                return
            code = str(vals[0])
            self.lang_var.set(code)
            self._on_language_change()
            try:
                win.destroy()
            except Exception:
                pass

        ttk.Button(btns, text="Reload", command=_refresh_rows).pack(side="left")
        ttk.Button(btns, text="Export Templates", command=lambda: (_export_lang_templates(), _refresh_rows())).pack(side="left", padx=6)
        ttk.Button(btns, text="Open i18n Folder", command=lambda: _open_folder(_i18n_dir())).pack(side="left", padx=6)
        ttk.Button(btns, text="Use Selected", command=_use_selected).pack(side="right")

        _refresh_rows()
    def show_dashboard(self):
        
        import tkinter as tk
        from tkinter import ttk

        if getattr(self, "_dash_win", None) and tk.Toplevel.winfo_exists(self._dash_win):
            try:
                self._dash_win.lift()
                self._dash_win.focus_force()
            except Exception:
                pass
            return

        win = tk.Toplevel(self.root)
        self._dash_win = win
        win.title("BitCrusher - Dashboard")
        win.geometry("520x360")
        win.resizable(False, False)

        container = ttk.Frame(win, padding=12)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text="Runtime Metrics", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        rows = [
            ("Queue (pending)", "queue_pending"),
            ("Processing", "processing"),
            ("Processed (this run)", "processed"),
            ("Average Size Ratio", "avg_ratio"),
            ("Average Time / File", "avg_time"),
            ("Watcher", "watcher"),
            ("Watched Folders", "watch_dirs"),
            ("Webhook", "webhook"),
        ]
        self._dash_vars = {}
        r = 1
        for label, key in rows:
            ttk.Label(container, text=label + ":").grid(row=r, column=0, sticky="w", padx=(0, 10), pady=4)
            var = tk.StringVar(value="—")
            self._dash_vars[key] = var
            ttk.Label(container, textvariable=var).grid(row=r, column=1, sticky="w", pady=4)
            r += 1

        ttk.Separator(container).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(10, 8))
        r += 1
        ttk.Button(container, text="Close", command=win.destroy).grid(row=r, column=1, sticky="e")

        def _queue_len():

            for attr in ("queue_files", "file_queue", "files_to_process", "pending_files", "queued_files"):
                q = getattr(self, attr, None)
                if isinstance(q, (list, tuple, set)):
                    return len(q)

                if hasattr(q, "__len__"):
                    try:
                        return len(q)
                    except Exception:
                        pass
            return 0

        def _is_processing():
            for attr in ("_is_processing", "is_processing", "processing"):
                v = getattr(self, attr, None)
                if isinstance(v, bool):
                    return v

            for attr in ("_worker_running", "worker_running"):
                v = getattr(self, attr, None)
                if isinstance(v, bool):
                    return v
            return False

        def _processed_count():
            lst = getattr(self, "stats_list", None)
            return len(lst) if isinstance(lst, list) else 0

        def _avg_ratio_and_time():
            lst = getattr(self, "stats_list", None)
            if not isinstance(lst, list) or not lst:
                return ("—", "—")
            ratios, times = [], []
            for rec in lst:
                try:
                    if "ratio" in rec:
                        ratios.append(float(rec["ratio"]))
                    elif "original_size" in rec and "compressed_size" in rec:
                        o = float(rec["original_size"]) or 1.0
                        c = float(rec["compressed_size"])
                        ratios.append(c / o)
                    if "time_taken" in rec:
                        times.append(float(rec["time_taken"]))
                except Exception:
                    pass
            avg_r = (sum(ratios) / len(ratios)) if ratios else None
            avg_t = (sum(times) / len(times)) if times else None
            r_txt = f"{avg_r*100:.1f}%" if isinstance(avg_r, float) else "—"
            t_txt = f"{avg_t:.2f}s" if isinstance(avg_t, float) else "—"
            return (r_txt, t_txt)

        def _watcher_status():
            enabled = bool(self.settings.get("watch_enabled", False))
            active = False
            try:
                w = getattr(self, "watcher", None)
                active = bool(getattr(w, "_running", False))
            except Exception:
                pass
            return "On (active)" if enabled and active else ("On (idle)" if enabled else "Off")

        def _watch_dirs():
            dirs = []
            try:
                dirs = list(self.settings.get("watch_folders", []) or [])
            except Exception:
                pass
            return str(len(dirs)) if dirs else "0"

        def _webhook_status():
            url = ""
            try:
                url = self.settings.get("webhook_url", "") or getattr(self, "webhook_url_var", None).get()
            except Exception:
                pass
            if url:
                masked = url[:40] + "..." if len(url) > 41 else url
                return f"Configured ({masked})"
            return "Not set"

        def _refresh():
            try:
                self._dash_vars["queue_pending"].set(str(_queue_len()))
                self._dash_vars["processing"].set("Yes" if _is_processing() else "No")
                self._dash_vars["processed"].set(str(_processed_count()))
                r_txt, t_txt = _avg_ratio_and_time()
                self._dash_vars["avg_ratio"].set(r_txt)
                self._dash_vars["avg_time"].set(t_txt)
                self._dash_vars["watcher"].set(_watcher_status())
                self._dash_vars["watch_dirs"].set(_watch_dirs())
                self._dash_vars["webhook"].set(_webhook_status())
            except Exception:
                pass

            try:
                win.after(1000, _refresh)
            except Exception:
                pass

        _refresh()




    def show_user_guide(self):
        
        try:

            base = os.path.dirname(os.path.abspath(__file__))
            candidates = [
                os.path.join(base, "docs", "USER_GUIDE.html"),
                os.path.join(base, "docs", "UserGuide.html"),
                os.path.join(base, "docs", "user_guide.html"),
                os.path.join(base, "USER_GUIDE.html"),
                os.path.join(base, "UserGuide.html"),
                os.path.join(base, "user_guide.html"),
                os.path.join(base, "docs", "USER_GUIDE.md"),
                os.path.join(base, "README.md"),
            ]

            for p in candidates:
                if os.path.isfile(p):
                    try:

                        if os.name == "nt" and p.lower().endswith((".html", ".htm", ".md")):
                            os.startfile(p)  # type: ignore[attr-defined]
                        else:
                            webbrowser.open_new_tab("file://" + os.path.abspath(p).replace("\\", "/"))
                        return
                    except Exception:
                        pass

            from tkinter import messagebox as mbox
            mbox.showinfo(
                "BitCrusher - Quick Guide",
                (
                    "1) Add files with \"Add Files...\", or drag and drop into the queue.\n"
                    "2) Choose a target size (MB) or a preset.\n"
                    "3) (Optional) Open Advanced Options to tweak encoder/CRF/audio.\n"
                    "4) Click \"Start Compression\".\n\n"
                    "Tips:\n"
                    "- Folder Watcher will auto-queue new files when enabled (Settings).\n"
                    "- Webhook URL (Settings -> Webhook) posts start/success/failure to Discord.\n"
                    "- Use Profiles (Settings -> Save/Load Profile) to store your favorite setup."
                )
            )
        except Exception as e:
            try:
                from tkinter import messagebox as mbox
                mbox.showerror("User Guide", f"Could not open guide:\n{e}")
            except Exception:
                pass




    def rebuild_themes_menu(self):
        
        try:
            menubar = self.root.nametowidget(self.root['menu'])

            self.setup_menu()  # your setup_menu already constructs the full menubar
        except Exception:
            pass



    def _on_theme_select(self, name: str):
        

        self.theme_var.set(name)

        try:
            animated_retheme(self, name)
        except Exception:
            try:
                apply_theme(self.style, name)
                retheme_runtime(self, self.style, name)
            except Exception:
                pass

        self.settings = getattr(self, "settings", {}) or {}
        self.settings["theme"] = name
        try:
            self.save_settings()
        except Exception:
            pass

        try:
            _save_theme_choice(name)
        except Exception:
            pass

        from pathlib import Path

    def load_settings(self) -> dict:
        
        from pathlib import Path

        defaults = {
            "theme": "Dark",
            "output_dir": str(Path.home()),
            "watch_folder": "",
            "enable_watch": False,
            "preset": next(iter(PRESETS.keys())),
            "target_size": MAX_SIZE_MB_DEFAULT,
            "webhook_url": "",
            "use_webhook": 0,
            "advanced": dict(ADVANCED_DEFAULTS),
        }
        data = dict(defaults)

        try:
            if os.path.isfile(self.settings_path):
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    disk = json.load(f) or {}

                try:

                    try:
                        saved_unit = None
                        if isinstance(disk, dict):
                            saved_unit = disk.get("size_unit", None)
                            if saved_unit:
                                data["size_unit"] = str(saved_unit).upper()
                        if not hasattr(self, "size_unit_var"):
                            import tkinter as tk
                            self.size_unit_var = tk.StringVar(value=(data.get("size_unit", "MB")))
                        else:
                            self.size_unit_var.set(str(data.get("size_unit", "MB")))
                    except Exception:
                        pass
                except Exception:
                    pass

                if isinstance(disk, dict):
                    for k in ("theme", "output_dir", "watch_folder", "enable_watch",
                              "preset", "target_size", "webhook_url", "use_webhook"):
                        if k in disk:
                            data[k] = disk[k]
                    adv = dict(ADVANCED_DEFAULTS)
                    adv.update(disk.get("advanced", {}) or {})
                    data["advanced"] = adv
        except Exception as e:
            try: LOG.error(f"Failed to load settings: {e}")
            except Exception: pass

        try:
            if not hasattr(self, "theme_var"):
                self.theme_var = tk.StringVar(value=data["theme"])
            else:
                self.theme_var.set(data["theme"])
            apply_theme(self.style, data["theme"])
            self.root.configure(bg=APP_BG)
            try:
                from ui_aesthetics import retheme_runtime
                retheme_runtime(self, self.style, data["theme"])
            except Exception:
                pass
        except Exception:
            pass

        try: self.save_path.set(data["output_dir"])
        except Exception: pass
        try: self.watch_folder.set(data["watch_folder"])
        except Exception: pass
        try: self.enable_watch.set(1 if data["enable_watch"] else 0)
        except Exception: pass

        try: self.target_size_var.set(str(data["target_size"]))
        except Exception: pass

        preset = data.get("preset", next(iter(PRESETS.keys())))
        preset_mb = PRESETS.get(preset)
        if isinstance(preset_mb, int) and int(data["target_size"]) != int(preset_mb):
            preset = next((k for k in PRESETS if str(k).lower().startswith("custom")), "Custom (use size below)")
        try: self.preset_var.set(preset)
        except Exception: pass

        try:
            if hasattr(self, "webhook_var"):
                self.webhook_var.set(data.get("webhook_url", ""))
            else:
                self.webhook_var = tk.StringVar(value=data.get("webhook_url", ""))
        except Exception: pass
        try: self.use_webhook.set(int(data.get("use_webhook", 0)))
        except Exception: pass

        adv = data.get("advanced", {})
        try: self.adv_iterative.set(1 if adv.get("iterative") else 0)
        except Exception: pass
        try: self.adv_two_pass.set(1 if adv.get("two_pass") else 0)
        except Exception: pass
        try: self.adv_two_pass_fallback.set(1 if adv.get("two_pass_fallback", True) else 0)
        except Exception: pass
        try: self.adv_auto_retry.set(1 if adv.get("auto_retry", True) else 0)
        except Exception: pass
        try: self.adv_grain_filter.set(1 if adv.get("grain_filter", True) else 0)
        except Exception: pass
        try: self.adv_concurrent.set(1 if adv.get("concurrent") else 0)
        except Exception: pass
        try: self.adv_auto_output.set(1 if adv.get("auto_output_folder") else 0)
        except Exception: pass
        try: self.adv_guetzli.set(1 if adv.get("guetzli") else 0)
        except Exception: pass
        try: self.adv_pngopt.set(1 if adv.get("pngopt") else 0)
        except Exception: pass
        try: self.adv_auto_jpeg.set(1 if adv.get("auto_jpeg") else 0)
        except Exception: pass
        try: self.adv_scene_zones.set(1 if adv.get("scene_zones", True) else 0)
        except Exception: pass
        try: self.adv_hw_decode.set(1 if adv.get("hw_decode", True) else 0)
        except Exception: pass
        try: self.adv_discord_compat.set(1 if adv.get("discord_compat", False) else 0)
        except Exception: pass
        try: self.adv_smart_preproc.set(1 if adv.get("smart_preproc", True) else 0)
        except Exception: pass
        try: self.adv_learned_seed.set(1 if adv.get("learned_seed", True) else 0)
        except Exception: pass
        try: self.adv_preflight.set(1 if adv.get("preflight_advice", True) else 0)
        except Exception: pass
        try: self.adv_ceiling_downscale.set(1 if adv.get("ceiling_downscale_retry", True) else 0)
        except Exception: pass
        try: self.adv_embed_lyrics.set(1 if adv.get("embed_lyrics", True) else 0)
        except Exception: pass
        try: self.adv_copy_clipboard.set(1 if adv.get("copy_to_clipboard", False) else 0)
        except Exception: pass
        try:
            _atm = str(adv.get("audio_track_mode", "keepfirst") or "keepfirst").strip().lower()
            self.adv_audio_track_mode.set(_atm if _atm in ("keepfirst", "mix") else "keepfirst")
        except Exception: pass
        try:
            _qm = str(adv.get("quality_mode", ADVANCED_DEFAULTS.get("quality_mode", "quality_first")) or "").strip().lower()
            self.adv_quality_first.set(0 if _qm in ("balanced", "fast") else 1)
            if hasattr(self, "adv_quality_mode"):
                self.adv_quality_mode.set(_qm if _qm in ("fast", "balanced", "max") else "max")
        except Exception: pass

        try: self.adv_encoder.set(adv.get("encoder", "x264"))
        except Exception: pass
        try: self.adv_manual_crf.set(adv.get("manual_crf", ""))
        except Exception: pass
        try: self.adv_manual_bitrate.set(adv.get("manual_bitrate", ""))
        except Exception: pass
        try: self.adv_output_prefix.set(adv.get("output_prefix", ""))
        except Exception: pass
        try: self.adv_output_suffix.set(adv.get("output_suffix", "_discord_ready"))
        except Exception: pass
        try: self.adv_audio_format.set(adv.get("audio_format", "aac"))
        except Exception: pass
        try: self.adv_image_format.set(adv.get("image_format", "jpg"))
        except Exception: pass

        return data


    def save_settings(self) -> None:
        
        try:

            if not hasattr(self, "size_unit_var"):
                try:
                    import tkinter as tk
                    self.size_unit_var = tk.StringVar(value="MB")
                except Exception:
                    class _UnitDummy:
                        def get(self): return "MB"
                    self.size_unit_var = _UnitDummy()

            out_dir      = self.save_path.get() if hasattr(self.save_path, "get") else str(self.save_path)
            watch_dir    = self.watch_folder.get() if hasattr(self.watch_folder, "get") else str(self.watch_folder)
            enable_watch = bool(self.enable_watch.get()) if hasattr(self.enable_watch, "get") else bool(self.enable_watch)
            preset       = self.preset_var.get() if hasattr(self.preset_var, "get") else str(self.preset_var)

            try:

                size_unit = str(self.size_unit_var.get())
                try:
                    target_size = int(self.target_size_var.get())
                except Exception:
                    target_size = 1
            except Exception:
                target_size = MAX_SIZE_MB_DEFAULT

            webhook      = (self.webhook_var.get() if hasattr(self, "webhook_var") and hasattr(self.webhook_var, "get")
                            else str(getattr(self, "webhook_url", "")))
            theme        = str(self.theme_var.get()) if hasattr(self, "theme_var") else "Dark"
            use_webhook  = int(self.use_webhook.get()) if hasattr(self, "use_webhook") and hasattr(self.use_webhook, "get") else 0
            _saved_adv = dict((((getattr(self, "settings", {}) or {}).get("advanced", {})) or {}))
            _quality_first = bool(self.adv_quality_first.get()) if hasattr(self, "adv_quality_first") else True

            adv = {
                "auto_retry":          bool(self.adv_auto_retry.get())        if hasattr(self, "adv_auto_retry")        else ADVANCED_DEFAULTS.get("auto_retry", True),
                "two_pass_fallback":   bool(self.adv_two_pass_fallback.get()) if hasattr(self, "adv_two_pass_fallback") else ADVANCED_DEFAULTS.get("two_pass_fallback", True),
                "grain_filter":        bool(self.adv_grain_filter.get())      if hasattr(self, "adv_grain_filter")      else ADVANCED_DEFAULTS.get("grain_filter", True),
                "encoder":             str(self.adv_encoder.get())            if hasattr(self, "adv_encoder")            else ADVANCED_DEFAULTS.get("encoder", "x264"),
                "iterative":           bool(self.adv_iterative.get())         if hasattr(self, "adv_iterative")         else ADVANCED_DEFAULTS.get("iterative", False),
                "two_pass":            bool(self.adv_two_pass.get())          if hasattr(self, "adv_two_pass")          else ADVANCED_DEFAULTS.get("two_pass", False),
                "manual_crf":          str(self.adv_manual_crf.get())         if hasattr(self, "adv_manual_crf")        else "",
                "manual_bitrate":      str(self.adv_manual_bitrate.get())     if hasattr(self, "adv_manual_bitrate")    else "",
                "output_prefix":       str(self.adv_output_prefix.get())      if hasattr(self, "adv_output_prefix")     else "",
                "output_suffix":       str(self.adv_output_suffix.get())      if hasattr(self, "adv_output_suffix")     else "_discord_ready",
                "audio_format":        str(self.adv_audio_format.get())       if hasattr(self, "adv_audio_format")      else "aac",
                "image_format":        str(self.adv_image_format.get())       if hasattr(self, "adv_image_format")      else "jpg",
                "concurrent":          bool(self.adv_concurrent.get())        if hasattr(self, "adv_concurrent")        else False,
                "auto_output_folder":  bool(self.adv_auto_output.get())       if hasattr(self, "adv_auto_output")       else False,
                "guetzli":             bool(self.adv_guetzli.get())           if hasattr(self, "adv_guetzli")           else False,
                "pngopt":              bool(self.adv_pngopt.get())            if hasattr(self, "adv_pngopt")            else False,
                "auto_jpeg":           bool(self.adv_auto_jpeg.get())         if hasattr(self, "adv_auto_jpeg")         else False,
                "scene_zones":         bool(self.adv_scene_zones.get())       if hasattr(self, "adv_scene_zones")       else True,
                "hw_decode":           bool(self.adv_hw_decode.get())         if hasattr(self, "adv_hw_decode")         else True,
                "discord_compat":      bool(self.adv_discord_compat.get())    if hasattr(self, "adv_discord_compat")    else False,
                "smart_preproc":       bool(self.adv_smart_preproc.get())     if hasattr(self, "adv_smart_preproc")     else True,
                "learned_seed":        bool(self.adv_learned_seed.get())      if hasattr(self, "adv_learned_seed")      else True,
                "preflight_advice":    bool(self.adv_preflight.get())         if hasattr(self, "adv_preflight")         else True,
                "ceiling_downscale_retry": bool(self.adv_ceiling_downscale.get()) if hasattr(self, "adv_ceiling_downscale") else True,
                "embed_lyrics":        bool(self.adv_embed_lyrics.get())      if hasattr(self, "adv_embed_lyrics")      else True,
                "copy_to_clipboard":   bool(self.adv_copy_clipboard.get())    if hasattr(self, "adv_copy_clipboard")    else False,
                "audio_track_mode":    (str(self.adv_audio_track_mode.get() or "keepfirst").strip().lower()
                                        if hasattr(self, "adv_audio_track_mode") else "keepfirst"),
                "quality_mode":        (str(self.adv_quality_mode.get() or "max").strip().lower()
                                        if hasattr(self, "adv_quality_mode")
                                        else ("max" if _quality_first else "balanced")),
                "target_policy":       ("legacy" if (hasattr(self, "adv_quality_mode")
                                                     and str(self.adv_quality_mode.get()).strip().lower() == "fast")
                                        else "no_overshoot_near_max"),
                # 0.80 was the old shipped default — migrate it to the current
                # default; anything else is a deliberate user setting.
                "target_tolerance_pct": (float(ADVANCED_DEFAULTS.get("target_tolerance_pct", 1.50))
                                         if abs(float(_saved_adv.get("target_tolerance_pct", 0.80)) - 0.80) < 1e-6
                                         else float(_saved_adv.get("target_tolerance_pct"))),
                "target_tolerance_min_bytes": int(float(_saved_adv.get("target_tolerance_min_bytes", ADVANCED_DEFAULTS.get("target_tolerance_min_bytes", 120000)))),
                "max_target_attempts": int(float(_saved_adv.get("max_target_attempts", ADVANCED_DEFAULTS.get("max_target_attempts", 8)))),
            }

            payload = {
                "theme":        theme,
                "ui_theme":     theme,
                "output_dir":   out_dir,
                "watch_folder": watch_dir,
                "enable_watch": enable_watch,
                "preset":       preset,
                "target_size":  target_size,
                "webhook_url":  webhook,
                "use_webhook":  use_webhook,
                "advanced":     dict(adv),
                "profiles":     dict(getattr(self, "saved_profiles", {})),
                "language":     self.lang_var.get(),
                "size_unit":    (self.size_unit_var.get() if hasattr(self, "size_unit_var") else "MB"),
                "dino_game":    (bool(int(self.dino_game_var.get())) if hasattr(self, "dino_game_var")
                                 else bool((getattr(self, "settings", {}) or {}).get("dino_game", False))),
                "pipeline_mode": (bool(self.pipeline_var.get()) if hasattr(self, "pipeline_var")
                                  else bool((getattr(self, "settings", {}) or {}).get("pipeline_mode", False))),
                "watch_rules":  dict((getattr(self, "settings", {}) or {}).get("watch_rules", {}) or {}),
            }


            

            os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
            tmp = self.settings_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, self.settings_path)

            self.settings = payload
            try: LOG.info("Settings saved -> %s", self.settings_path)
            except Exception: pass
        except Exception as e:
            try: LOG.error(f"Failed to save settings: {e}")
            except Exception: pass

    def gather_advanced_options(self) -> dict:
        
        opts = {}

        try:
            val = ""
            if hasattr(self, "adv_encoder"):
                val = (self.adv_encoder.get() or "").strip()
            elif hasattr(self, "encoder_var"):
                val = (self.encoder_var.get() or "").strip()
            if not val:
                val = self.settings.get("encoder", "") or self.settings.get("advanced", {}).get("encoder", "")
            opts["encoder"] = (val or "x264")
        except Exception:
            opts["encoder"] = self.settings.get("encoder", "x264")

        try:
            crf_raw = (self.crf_var.get() if hasattr(self, "crf_var") else self.settings.get("manual_crf", ""))
            crf_raw = str(crf_raw).strip()
            if crf_raw:
                opts["manual_crf"] = str(int(crf_raw))
        except Exception:

            pass

        try:
            opts["two_pass"] = bool(self.two_pass_var.get()) if hasattr(self, "two_pass_var") else bool(self.settings.get("two_pass", False))
        except Exception:
            pass

        try:
            ov = None
            if hasattr(self, "overshoot_var"):
                ov = float(self.overshoot_var.get())
            elif "overshoot_ratio" in self.settings:
                ov = float(self.settings["overshoot_ratio"])
            if ov is not None:
                opts["overshoot_ratio"] = max(0.90, min(1.15, ov))
        except Exception:
            pass

        try:
            if hasattr(self, "adv_hwaccel"):
                opts["hwaccel"] = self.adv_hwaccel.get()
            else:
                opts["hwaccel"] = self.settings.get("hwaccel", "CPU")
        except Exception:
            opts["hwaccel"] = "CPU"

        # --- Batch-1 quality-of-life toggles --------------------------------
        try:
            opts["discord_compat"] = (bool(self.adv_discord_compat.get())
                                      if hasattr(self, "adv_discord_compat")
                                      else bool(self.settings.get("discord_compat",
                                                ADVANCED_DEFAULTS.get("discord_compat", False))))
        except Exception:
            opts["discord_compat"] = bool(ADVANCED_DEFAULTS.get("discord_compat", False))

        try:
            opts["smart_preproc"] = (bool(self.adv_smart_preproc.get())
                                     if hasattr(self, "adv_smart_preproc")
                                     else bool(self.settings.get("smart_preproc",
                                               ADVANCED_DEFAULTS.get("smart_preproc", True))))
        except Exception:
            opts["smart_preproc"] = bool(ADVANCED_DEFAULTS.get("smart_preproc", True))

        # Learning-system + ceiling-guard toggles (parity with CLI flags).
        for _k, _var in (("learned_seed", "adv_learned_seed"),
                         ("preflight_advice", "adv_preflight"),
                         ("ceiling_downscale_retry", "adv_ceiling_downscale")):
            try:
                opts[_k] = (bool(getattr(self, _var).get()) if hasattr(self, _var)
                            else bool(self.settings.get(_k, ADVANCED_DEFAULTS.get(_k, True))))
            except Exception:
                opts[_k] = bool(ADVANCED_DEFAULTS.get(_k, True))

        try:
            _atm = (self.adv_audio_track_mode.get() if hasattr(self, "adv_audio_track_mode")
                    else self.settings.get("audio_track_mode",
                                           ADVANCED_DEFAULTS.get("audio_track_mode", "keepfirst")))
            _atm = str(_atm or "keepfirst").strip().lower()
            opts["audio_track_mode"] = _atm if _atm in ("keepfirst", "mix") else "keepfirst"
        except Exception:
            opts["audio_track_mode"] = "keepfirst"

        try:
            opts["embed_lyrics"] = (bool(self.adv_embed_lyrics.get())
                                    if hasattr(self, "adv_embed_lyrics")
                                    else bool(self.settings.get("embed_lyrics",
                                              ADVANCED_DEFAULTS.get("embed_lyrics", True))))
        except Exception:
            opts["embed_lyrics"] = bool(ADVANCED_DEFAULTS.get("embed_lyrics", True))

        try:
            opts["copy_to_clipboard"] = (bool(self.adv_copy_clipboard.get())
                                         if hasattr(self, "adv_copy_clipboard")
                                         else bool(self.settings.get("copy_to_clipboard",
                                                   ADVANCED_DEFAULTS.get("copy_to_clipboard", False))))
        except Exception:
            opts["copy_to_clipboard"] = bool(ADVANCED_DEFAULTS.get("copy_to_clipboard", False))

        return opts

    def _profiles_file(self) -> str:
        
        try:
            base = os.path.join(os.getcwd(), "user_settings")
            os.makedirs(base, exist_ok=True)
            return os.path.join(base, "profiles.json")
        except Exception:
            return "profiles.json"

    def _read_profiles(self) -> dict:
        pf = self._profiles_file()
        try:
            if os.path.isfile(pf):
                with open(pf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def _write_profiles(self, data: dict) -> None:
        pf = self._profiles_file()
        try:
            with open(pf, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            try:
                self._notify("Save Profile Error", str(e))
            except Exception:
                pass

    def save_profile(self):
        
        try:
            name = sd.askstring("Save Profile", "Enter profile name:")
            if not name:
                return
            name = name.strip()
            if not name:
                return

            snap = dict(self.settings)

            profiles = self._read_profiles()
            profiles[name] = snap
            self._write_profiles(profiles)

            try:
                self._notify("Profile Saved", f"Saved profile: {name}")
            except Exception:
                pass
        except Exception as e:
            self.log(f"Profile save failed: {e}", level="ERROR")

    def load_profile(self):
        
        try:
            profiles = self._read_profiles()
            if not profiles:
                self._notify("Load Profile", "No profiles saved yet.")
                return

            names = sorted(profiles.keys(), key=str.lower)
            prompt = "Available profiles:\n- " + "\n- ".join(names) + "\n\nType a name to load:"
            name = sd.askstring("Load Profile", prompt)
            if not name:
                return
            name = name.strip()
            if name not in profiles:
                self._notify("Load Profile", f"Profile not found: {name}")
                return

            new_settings = profiles[name]
            if not isinstance(new_settings, dict):
                self._notify("Load Profile", f"Invalid profile data: {name}")
                return

            self.settings.update(new_settings)

            self.save_settings()

            try:

                self.webhook.set_url(self.settings.get("webhook_url","") or getattr(self, "webhook_url_var", tk.StringVar(value="")).get())
            except Exception:
                pass

            try:
                self.stop_folder_watcher()
            except Exception:
                pass
            try:

                if hasattr(self, "watch_folders"):
                    self.watch_folders = self.settings.get("watch_folders", [])
                else:
                    self.watch_folders = self.settings.setdefault("watch_folders", [])

                for _p in (self.watch_folders or []):
                    try:
                        self.watcher.add_path(_p)
                    except Exception:
                        pass
                if self.settings.get("watch_enabled", False):
                    self.start_folder_watcher()
            except Exception:
                pass

            try:
                if hasattr(self, "watch_folder"):
                    self.watch_folder.set(self.settings.get("watch_folder", ""))
                if hasattr(self, "enable_watch"):
                    self.enable_watch.set(self.settings.get("enable_watch", False))
                if hasattr(self, "save_path"):
                    self.save_path.set(self.settings.get("output_dir", self.save_path.get()))
                if hasattr(self, "theme_var"):
                    self.theme_var.set(self.settings.get("theme", self.theme_var.get()))
            except Exception:
                pass

            try:
                self._notify("Profile Loaded", f"Loaded profile: {name}")
            except Exception:
                pass
        except Exception as e:
            self.log(f"Profile load failed: {e}", level='ERROR')





    def open_settings_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("450x300")
        win.transient(self.root)
        win.grab_set()
        pad = {"padx": 10, "pady": 5}

        tk.Label(win, text="Default Save Folder:").grid(row=0, column=0, sticky="e", **pad)
        save_entry = tk.Entry(win, width=40)
        save_entry.insert(0, self.settings.get("output_dir", ""))
        save_entry.grid(row=0, column=1, **pad)
        def browse_save():
            d = filedialog.askdirectory(parent=win)
            if d:
                save_entry.delete(0, tk.END)
                save_entry.insert(0, d)
        ttk.Button(win, text="Browse...", command=browse_save).grid(row=0, column=2, **pad)

        auto_del_var = tk.BooleanVar(value=self.settings.get("auto_delete", False))
        ttk.Checkbutton(win, text="Auto-delete originals", variable=auto_del_var)\
            .grid(row=1, column=1, sticky="w", **pad)

        tk.Label(win, text="Watch Folder:").grid(row=2, column=0, sticky="e", **pad)
        watch_entry = tk.Entry(win, width=40)
        watch_entry.insert(0, self.settings.get("watch_folder", ""))
        watch_entry.grid(row=2, column=1, **pad)
        def browse_watch():
            d = filedialog.askdirectory(parent=win)
            if d:
                watch_entry.delete(0, tk.END)
                watch_entry.insert(0, d)
        ttk.Button(win, text="Browse...", command=browse_watch).grid(row=2, column=2, **pad)

        watch_on_var = tk.BooleanVar(value=self.settings.get("enable_watch", False))
        ttk.Checkbutton(win, text="Enable Watch-Folder", variable=watch_on_var)\
            .grid(row=3, column=1, sticky="w", **pad)

    def on_save():
        self.settings["output_dir"]   = save_entry.get()
        self.settings["auto_delete"]  = auto_del_var.get()
        self.settings["watch_folder"] = watch_entry.get()
        self.settings["enable_watch"] = watch_on_var.get()
        self.settings["theme"]        = self.theme_var.get()

        self.save_path.set(self.settings["output_dir"])
        self.watch_folder.set(self.settings["watch_folder"])
        self.enable_watch.set(self.settings.get("enable_watch", False))

        self.save_settings()

        try:
            self.webhook.set_url(self.settings.get("webhook_url", "")
                                 or self.webhook_url_var.get())
        except Exception:
            pass

        if self.settings["enable_watch"]:
            self.stop_folder_watcher()
            self.start_folder_watcher()
        else:
            self.stop_folder_watcher()

        win.destroy()

        btns = tk.Frame(win)
        btns.grid(row=4, column=0, columnspan=3, pady=20)
        tk.Button(btns, text="Save", command=on_save).pack(side="left", padx=10)
        ttk.Button(btns, text=_("title.cancel"), command=win.destroy).pack(side="right", padx=10)

        win.wait_window()




    def compress_file_task(self, filepath, output_folder, target_size, webhook, adv_options):
        import os, time, logging
        from pathlib import Path

        t0 = time.time()
        logging.info(f"Compressing: {filepath}")
        os.environ["BC_CURRENT_INPUT"] = str(filepath)
        try:
            _per = getattr(self, "per_file_opts", {}).get(filepath, {})
            if _per:
                if isinstance(adv_options, dict):
                    adv_options.update(_per)
                else:
                    adv_options = dict(_per)
        except Exception:
            pass

        try:
            wh = webhook if webhook else getattr(self, "webhook", None)
            if wh:
                wh.send_text(f"Compressing: {os.path.basename(filepath)}")
        except Exception:
            pass

        try:

            target_bytes = int(target_size) if isinstance(target_size, (int, float)) and int(target_size) > 0 else self._get_target_bytes()

            try:
                t_mb = max(0, int(target_bytes // (1024 * 1024)))
            except Exception:
                t_mb = 0
            if isinstance(adv_options, dict) and t_mb > 0:

                adv_options["two_pass"] = True

                try:
                    ov = float(adv_options.get("overshoot_ratio", 1.00))
                except Exception:
                    ov = 1.00
                adv_options["overshoot_ratio"] = max(0.90, min(1.15, ov))
        except Exception:
            target_bytes = self._get_target_bytes()

        try:
            src_bytes = os.path.getsize(filepath)
            target_bytes = int(max(1, target_bytes))
            ratio = (target_bytes / float(src_bytes)) if src_bytes else 1.0

            ext = os.path.splitext(filepath)[1].lower()
            is_audio = ext in {".flac", ".wav", ".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wma", ".alac", ".aiff", ".aif"}

            if ratio < 0.06:

                try:
                    self.update_status(
                        f"[WARN] Target {human_bytes(target_bytes)} is very small "
                        f"({ratio*100:.1f}% of source {human_bytes(src_bytes)}); quality may suffer."
                    , level="WARN")
                except Exception:
                    pass

                if not is_audio:
                    # Informational only: compress_video() itself now hard-fails
                    # (RuntimeError) if the target is genuinely infeasible against
                    # real duration/bitrate math, so we no longer silently rewrite
                    # the user's requested target_bytes here — that hid failures
                    # instead of surfacing them, and diverged from CLI (which had
                    # no equivalent clamp and just shipped an oversized file).
                    from tkinter import messagebox as mbox
                    self._ask_on_main(lambda: mbox.showwarning(
                        self.tr("unreal.title"),
                        f"{self.tr('unreal.header')}\n\n"
                        f"{self.tr('unreal.original')}: {human_bytes(src_bytes)}\n"
                        f"{self.tr('unreal.target')}: {human_bytes(target_bytes)}\n\n"
                        f"{self.tr('unreal.why')}\n"
                        f"{self.tr('unreal.why.v')}\n"
                        f"{self.tr('unreal.why.a')}\n"
                        f"{self.tr('unreal.why.m')}\n\n"
                        f"{self.tr('unreal.better')}\n"
                        f"{self.tr('unreal.opt.aim')}\n"
                        f"{self.tr('unreal.opt.scale')}\n"
                        f"{self.tr('unreal.opt.codec')}"
                    ), default=None)
        except Exception:
            pass


        self._notify("Compression Started", f"Processing {os.path.basename(filepath)}")
        try:
            self.update_status(f"Starting encode @ target ~{human_bytes(target_bytes)} ({ratio*100:.1f}% of source)")
        except Exception:
            pass

        mt = get_media_type(filepath)
        if mt == "video":
            try:
                dur, w, h, br, fr = get_video_metadata(filepath)
            except Exception:
                dur = w = h = br = fr = 0
            # Re-encoding detection: warn if source codec == target codec at much lower bitrate.
            try:
                _src_codec = str(_probe_video_stream(filepath).get("codec_name") or "").strip().lower()
                _tgt_enc = str((adv_options or {}).get("encoder", "") or "")
                # Estimate target video bitrate from total target bytes and duration.
                _target_v_bps = int(max(0, (target_bytes * 8) / max(1.0, float(dur or 1)))) if dur else 0
                _reenc_warn = _detect_reencoding_risk(_src_codec, _tgt_enc, int(br or 0), _target_v_bps)
                if _reenc_warn:
                    LOG.warning("Re-encoding risk: %s", _reenc_warn)
                    self.update_status(f"[Warn] Re-encoding warning: {_reenc_warn}", level="WARNING")
                    from tkinter import messagebox as _mbox
                    _proceed = self._ask_on_main(
                        lambda: _mbox.askyesno(
                            "Re-encoding Warning",
                            f"{_reenc_warn}\n\nProceed anyway?",
                            icon="warning"
                        ),
                        default=True,
                    )
                    if not _proceed:
                        self.update_status("[Cancel] Encode cancelled by user (re-encoding risk).", level="INFO")
                        return {}
            except Exception:
                pass
        else:
            dur = w = h = br = fr = 0

        features = {
            "duration": dur,
            "width": w,
            "height": h,
            "bitrate": br,
            "frame_rate": fr
        }

        try:
            t_mb = max(1, int(target_bytes // (1024 * 1024)))
        except Exception:
            t_mb = 1
        # This CRF only seeds the size estimator; the ABR pipeline predicts the
        # actual bitrate (and, for size-targeted encodes, CRF isn't even used).
        # The old per-file "CRF seed learner" never trained — nothing consumed its
        # output, its model file was never written — and only emitted a misleading
        # "untrained" warning every run. Removed; use the default seed directly.
        used_crf = self.default_crf

        adv_options = adv_options.copy()
        adv_options["manual_crf"] = str(used_crf)

        try:
            adv_options["hwaccel"] = (self.adv_hwaccel.get() if hasattr(self, "adv_hwaccel") else adv_options.get("hwaccel", "CPU"))
        except Exception:
            adv_options["hwaccel"] = adv_options.get("hwaccel", "CPU")

        adv_options["encoder"] = (adv_options.get("encoder") or "x264")
        adv_options["_target_is_bytes"] = True

        # Per-file log prefix so interleaved concurrent job logs stay readable.
        _prefix = ""
        try:
            if int(getattr(self, "_active_workers", 1) or 1) > 1:
                _prefix = f"[{os.path.basename(filepath)}] "
        except Exception:
            pass

        def _status(msg, level="INFO"):
            self.update_status(f"{_prefix}{msg}", level=level)

        stats = {}
        try:

            self._last_target_bytes = int(target_bytes)

            stats = auto_compress(
                filepath,
                output_folder,
                _status,
                target_bytes,
                wh,
                adv_options,
                (lambda: bool(self.compression_cancelled))
            )

            if stats and stats.get("compressed_size") is not None:

                if stats.get("ceiling_exceeded"):
                    _status(f"CEILING EXCEEDED: {os.path.basename(filepath)} compressed to "
                            f"{format_bytes(stats['compressed_size'])}, over the {format_bytes(int(target_bytes))} "
                            f"target. Retry/downscale could not fit this content under the target.", level="ERROR")

                rec = {
                    "filename":        os.path.basename(filepath),
                    "original_size":   stats["original_size"],
                    "compressed_size": stats["compressed_size"],
                    "ratio":           stats["compressed_size"] / stats["original_size"],
                    "time_taken":      time.time() - t0,
                    "vmaf":            stats.get("vmaf"),
                    "ceiling_exceeded": bool(stats.get("ceiling_exceeded")),
                }
                self.stats_list.append(rec)

                size_str = format_bytes(stats["compressed_size"])
                _vmaf_val = stats.get("vmaf")
                vmaf_str = (f" • VMAF {_vmaf_val:.0f} ({stats.get('vmaf_label','')})"
                            if isinstance(_vmaf_val, (int, float)) else "")

                try:
                    out_file = (stats.get("output_path") or stats.get("out_path") or stats.get("output"))
                except Exception:
                    out_file = None
                if out_file and os.path.isfile(out_file):
                    self._last_output_path = out_file
                else:

                    try:
                        from pathlib import Path
                        import glob, os
                        stem = Path(filepath).stem
                        save_dir_guess = output_folder if os.path.isdir(output_folder) else (self.save_path.get() if hasattr(self, "save_path") else ".")
                        cand = sorted(glob.glob(os.path.join(save_dir_guess, f"*{stem}*")), key=lambda p: os.path.getmtime(p), reverse=True)
                        if cand:
                            self._last_output_path = cand[0]
                    except Exception:
                        pass
                self._notify(
                    "Compression Completed",
                    f"{rec['filename']} → {size_str} (CRF {used_crf}){vmaf_str}"
                )

                # Copy-result-to-clipboard (CF_HDROP): drop the finished file on the
                # Windows clipboard so one Ctrl+V pastes it into Discord. Opt-in.
                try:
                    _clip_on = bool((adv_options or {}).get(
                        "copy_to_clipboard", ADVANCED_DEFAULTS.get("copy_to_clipboard", False)))
                    _clip_target = getattr(self, "_last_output_path", None)
                    if _clip_on and _clip_target and os.path.isfile(_clip_target):
                        if set_clipboard_files([_clip_target]):
                            self.update_status(
                                f"[Clipboard] Copied to clipboard - Ctrl+V to paste {os.path.basename(_clip_target)} into Discord.",
                                level="INFO")
                except Exception:
                    pass

                try:
                    wh = webhook if webhook else getattr(self, "webhook", None)
                    if wh:
                        out_file = stats.get("output_path") or stats.get("out_path") or stats.get("output")
                        msg = f"Done: {os.path.basename(filepath)}" + (
                              f" → {os.path.basename(out_file)}" if out_file else f" → {size_str}"
                        )
                        wh.send_text(msg)
                        if out_file and os.path.isfile(out_file):
                            wh.send_file(out_file, description=msg)

                            try:
                                self.update_status(f"Finished {os.path.basename(filepath)}", level="INFO")
                            except Exception:
                                pass
                except Exception:
                    pass
            else:
                self._notify(
                    "Compression Failed",
                    f"{os.path.basename(filepath)} (no output)"
                )

                try:
                    wh = webhook if webhook else getattr(self, "webhook", None)
                    if wh:
                        wh.send_text(f"Failed: {os.path.basename(filepath)} - no output file.")
                except Exception:
                    raise RuntimeError("Encode failed (no output)")
                    pass

            return stats

        except Exception as e:

            try:
                wh = webhook if webhook else getattr(self, "webhook", None)
                if wh:
                    wh.send_text(f"Failed: {os.path.basename(filepath)} - check logs.")
            except Exception:
                pass

            logging.error(f"Compression error for {filepath}: {e}")
            self._notify(
                "Compression Failed",
                f"{os.path.basename(filepath)}: {e}"
            )

            return {}





    def compress_file(self, input_path, output_path):
        try:
            handbrake_path = self.get_handbrake_path()
            command = [
                handbrake_path,
                '-i', input_path,
                '-o', output_path,
                '-e', 'x264',
                '-q', '22',
                '--optimize',
                '--preset', 'Very Fast 1080p30'
            ]
            result = _sp_run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise Exception(result.stderr)
        except Exception as e:
            raise RuntimeError(f"Compression failed for {input_path}: {e}")

    def process_queue(self):
        if not self.files:
            messagebox.showwarning("Warning", "No files in queue.")
            return

        def compress_files():
            for file in self.files:
                if self.stop_event.is_set():
                    self.log("Compression canceled by user.")
                    break
                try:
                    ext = os.path.splitext(file)[1]
                    output_file = self.get_output_filename(file, ext)
                    self.log(f"Compressing: {file}")
                    notify_info(
                        title="BitCrusher",
                        msg=f"Started compressing:\n{os.path.basename(file)}",
                        duration=3
                    )

                    self.compress_file(file, output_file)
                    self.log(f"Done: {output_file}")

                    try:
                        if os.path.isfile(output_file):
                            self._last_output_path = output_file
                    except Exception:
                        pass
                    notify_info(
                        title="BitCrusher",
                        msg=f"Finished compressing:\n{os.path.basename(file)}",
                        duration=3
                    )

                except Exception as e:
                    self.log(f"Error: {e}")
                    notify_error(
                        title="BitCrusher - Error",
                        msg="Compression failed! Check logs.",
                        duration=5
                    )
            self.stop_event.clear()

        self.stop_event.clear()
        self.processing_thread = threading.Thread(target=compress_files)
        self.processing_thread.start()

    def get_output_filename(self, input_file, ext=None):
        base = os.path.splitext(input_file)[0]
        ext = ext if ext else os.path.splitext(input_file)[1]
        return f"{base}_compressed{ext}"


    def setup_style(self):
        
        from tkinter import ttk

        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "TButton",
            background="#7289DA",
            foreground="white",
            relief="flat",
            padding=6,
            font=("Segoe UI", 10)
        )
        style.map(
            "TButton",
            background=[("active", "#99AAB5")],
            foreground=[("active", "white")]
        )

        style.configure(
            "TLabel",
            background="#2C2F33",
            foreground="white",
            font=("Segoe UI", 10)
        )

        style.configure(
            "TEntry",
            fieldbackground="#99AAB5",
            foreground="black",
            padding=3
        )
        style.configure(
            "TCombobox",
            fieldbackground="#99AAB5",
            foreground="black",
            padding=3
        )

        style.configure("TSeparator",            background="#2C2F33")
        style.configure("Horizontal.TSeparator", background="#2C2F33")
        style.configure("Vertical.TSeparator",   background="#2C2F33")

        style.configure(
            "Treeview",
            background="#2C2F33",
            fieldbackground="#2C2F33",
            foreground="white",
            font=("Segoe UI", 10)
        )
        style.configure(
            "Treeview.Heading",
            background="#23272A",
            foreground="white",
            font=("Segoe UI", 10, "bold")
        )








    def animate_title(self):
        text = "BitCrusher V9"
        i   = getattr(self, "_title_i", 0)
        d   = getattr(self, "_title_dir", 1)

        self.title_label.configure(text=text[:i])

        if d > 0 and i < len(text):
            i += 1; delay = 80
        elif d < 0 and i > 0:
            i -= 1; delay = 80
        else:
            d *= -1; delay = 1000  # longer end pause

        self._title_i, self._title_dir = i, d

        if self.root.focus_displayof() is not None:
            self._title_job = self.root.after(delay, self.animate_title)





    def check_dependencies(self):
        
        tools = {
            "HandBrakeCLI": HANDBRAKE_CLI,
            "ffprobe":      FFPROBE,
            "ffmpeg":       FFMPEG
        }
        missing = [name for name, exe in tools.items() if not shutil.which(exe)]
        if not missing:
            return
        msg = "Missing tools detected:\n" + "\n".join(missing) + "\n\nInstall now?"
        if messagebox.askyesno("Dependencies Missing", msg):
            for name in missing:
                self.install_tool(name)
            messagebox.showinfo("Install Complete",
                                "Tools installed. Please restart the app.")
            self.root.quit()


    def cancel_queue(self):
        if hasattr(self, 'processing_thread') and self.processing_thread.is_alive():
            self.stop_event.set()
            self.log("Cancel requested.")
            messagebox.showinfo("Cancel", "Queue cancel requested.")
        else:
            messagebox.showinfo("Cancel", "No active compression to cancel.")



    def install_tool(self, name: str):
        
        import hashlib
        from zipfile import ZipFile, is_zipfile

        tool_urls = {
            "HandBrakeCLI": {
                "url": "https://github.com/HandBrake/HandBrake/releases/download/1.7.3/HandBrakeCLI-1.7.3-win-x86_64.zip",
                "exe": "HandBrakeCLI.exe"
            },
            "ffmpeg": {
                "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
                "exe": "ffmpeg.exe"
            },
            "ffprobe": {
                "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
                "exe": "ffprobe.exe"
            }
        }

        info = tool_urls.get(name)
        if not info:
            self.update_status(f"Unknown tool: {name}", level="ERROR")
            return


        tools_dir = Path(SCRIPT_DIR) / "tools"
        tools_dir.mkdir(exist_ok=True)

        exe_path = tools_dir / info["exe"]
        zip_path = tools_dir / f"{name}.zip"
        url = info["url"]

        if exe_path.exists():
            self.update_status(f"{name} already installed at: {exe_path}")
            return

        self.update_status(f"Downloading {name}...")

        for attempt in range(3):
            try:
                r = requests.get(url, stream=True, timeout=30)
                r.raise_for_status()
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(1024 * 1024):
                        f.write(chunk)
                break
            except Exception as e:
                if attempt == 2:
                    self.update_status(f"Failed to download {name}: {e}", level="ERROR")
                    return
                time.sleep(2)

        if not is_zipfile(zip_path):
            self.update_status(f"Corrupted or invalid ZIP: {zip_path}", level="ERROR")
            zip_path.unlink(missing_ok=True)
            return

        self.update_status(f"Extracting {name}...")
        try:
            with ZipFile(zip_path, "r") as zip_ref:
                members = [m for m in zip_ref.namelist() if m.endswith(info["exe"])]
                if not members:
                    self.update_status(f"{info['exe']} not found in ZIP", level="ERROR")
                    return
                for member in members:
                    zip_ref.extract(member, tools_dir)

                    extracted = tools_dir / member
                    flattened = tools_dir / info["exe"]
                    extracted.rename(flattened)
            zip_path.unlink(missing_ok=True)
            self.update_status(f"{name} installed to {exe_path}")
        except Exception as e:
            self.update_status(f"Extraction failed: {e}", level="ERROR")
            zip_path.unlink(missing_ok=True)




    def setup_shortcuts(self):
        self.root.bind("<Control-o>", lambda e: self.add_files())
        self.root.bind("<Control-s>", lambda e: self.start_compression())
        self.root.bind("<Control-p>", lambda e: self.toggle_pause())
        self.root.bind("<Escape>",    lambda e: self.cancel_compression())

    def toggle_pause(self):
        
        self.paused = not getattr(self, "paused", False)
        self.update_status("Paused" if self.paused else "Resumed")

    def select_watch_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.watch_folder.set(folder)
            self.update_status(f"Watch folder set to: {folder}")
            if self.enable_watch.get():
                self.stop_folder_watcher()
                self.start_folder_watcher()


    def set_preset(self, value):

        v = str(value or "")
        if v.lower().startswith("custom"):
            return
        mb = PRESETS.get(v)
        if isinstance(mb, int):
            self.target_size_var.set(str(mb))
            # Platform presets are always expressed in MB — sync the unit so a
            # leftover "GB"/"KB" selection doesn't silently mis-scale the target.
            try:
                if hasattr(self, "size_unit_var"):
                    self.size_unit_var.set("MB")
            except Exception:
                pass
            try:
                if hasattr(self, "ui_info"):
                    self.ui_info(f"Target set to {mb} MB for {v.split(' (')[0]}")
            except Exception:
                pass






    def toggle_watch(self):
        if self.enable_watch.get():
            if not self.watch_folder.get():
                messagebox.showwarning("Watch Folder","Please select a folder first.")
                self.enable_watch.set(False)
            else:
                self.start_folder_watcher()
        else:
            self.stop_folder_watcher()


    def handle_drop(self, event):
        raw_data = event.data
        self.update_status(f"Drag-and-Drop raw data: {raw_data}", level="DEBUG")
        notification.notify(
            title="BitCrusher",
            message="File dropped into BitCrusher!",
            timeout=2
        )

        for f in parse_dnd_files(raw_data):
            if os.path.exists(f) and f not in self.file_list:
                _norm = _normalize_drop_path(filepath)
                try:
                    if not hasattr(self, "file_list"):
                        self.file_list = []
                    if _norm not in self.file_list:
                        self.file_list.append(_norm)
                except Exception:
                    pass
                self.queue_box.insert("end", _norm)


    def notify(title, message):
        try:
            notification.notify(
                title=title,
                message=message,
                timeout=5  # seconds
            )
        except Exception as e:
            print(f"[NOTIFY ERROR] {e}")

    def drop_file_handler(self, event):
        
        raw = getattr(event, "data", event)
        self.logger.debug(f"Raw DnD data: {raw!r}")

        try:
            paths = self.root.tk.splitlist(raw)
        except tk.TclError:
            paths = [raw]

        for p in paths:
            path = p.strip("{}")
            if path.lower().startswith("file:///"):
                path = path[8:]
            elif path.lower().startswith("file://"):
                path = path[7:]
            path = os.path.normpath(path)

            if os.path.isfile(path):
                if path not in getattr(self, "file_list", []):

                    _norm = _normalize_drop_path(path)
                    try:
                        if not hasattr(self, "file_list"):
                            self.file_list = []
                        if _norm not in self.file_list:
                            self.file_list.append(_norm)
                    except Exception:
                        pass
                    self.queue_box.insert("end", _norm)

                    self.logger.info(f"Queued via DnD: {path}")
                else:
                    self.logger.info(f"Already queued: {path}")
            else:
                self.logger.warning(f"Ignored drop: not a file: {path}")

        try:
            self._save_queue()
        except Exception:
            pass
        return "break"






    def add_files(self):
        paths = filedialog.askopenfilenames(filetypes=[("Media files", 
            "*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm *.m4v *.3gp *.3g2 *.mpeg *.mpg "
            "*.mp3 *.wav *.aac *.ogg *.flac *.wma *.m4a *.opus *.alac *.aiff *.aif "
            "*.jpg *.jpeg *.jfif *.png *.webp *.gif *.bmp *.tiff *.tif *.heic *.heif *.jxl *.raw *.avif "
            "*.pdf")])

        for path in paths:
            if path not in self.file_list:
                self.file_list.append(path)
                self.queue_box.insert("end", path)
                notification.notify(
                    title="BitCrusher - File Added",
                    message=f"{os.path.basename(path)} added to queue.",
                    timeout=3
                )
        self._save_queue()


    def remove_selected(self):
        indices = list(self.queue_box.curselection())
        for i in reversed(indices):
            self.queue_box.delete(i)
            del self.file_list[i]
        self._save_queue()

    def move_up(self):
        selections = self.queue_box.curselection()
        for i in selections:
            if i > 0:
                self.file_list[i], self.file_list[i-1] = self.file_list[i-1], self.file_list[i]
        self.refresh_queue_box()
        self._save_queue()

    def move_down(self):
        selections = list(self.queue_box.curselection())
        for i in reversed(selections):
            if i < len(self.file_list) - 1:
                self.file_list[i], self.file_list[i+1] = self.file_list[i+1], self.file_list[i]
        self.refresh_queue_box()
        self._save_queue()

    def clear_queue(self):
        self.file_list.clear()
        self.queue_box.delete(0, "end")
        self.job_rows = {}
        self._save_queue()

    def refresh_queue_box(self):
        self.queue_box.delete(0, "end")
        for f in self.file_list:
            _norm = _normalize_drop_path(f)
            self.queue_box.insert("end", _norm)
            try:
                if not hasattr(self, "file_list"):
                    self.file_list = []
                if _norm not in self.file_list:
                    self.file_list.append(_norm)
            except Exception:
                pass
            # Restore rich-row state (status/VMAF/...) across rebuilds.
            try:
                cached = (getattr(self, "job_rows", {}) or {}).get(_norm)
                if cached:
                    self.queue_box.job_update(_norm, **cached)
            except Exception:
                pass

    # ---- Rich queue-row updates (main thread only) ----------------------
    def _job_update(self, path, *, status=None, progress=None, eta=None,
                    size=None, vmaf=None):
        """Update one queue row's status columns; remembers state in job_rows."""
        try:
            _norm = _normalize_drop_path(str(path))
        except Exception:
            _norm = str(path)
        row = self.job_rows.setdefault(_norm, {}) if hasattr(self, "job_rows") else {}
        for k, v in (("status", status), ("progress", progress), ("eta", eta),
                     ("size", size), ("vmaf", vmaf)):
            if v is not None:
                row[k] = v
        try:
            self.queue_box.job_update(_norm, status=status, progress=progress,
                                      eta=eta, size=size, vmaf=vmaf)
        except Exception:
            pass

    def _ui(self, fn, *args, **kwargs):
        """Schedule a callable on the Tk main thread (safe from worker threads)."""
        try:
            self.root.after(0, lambda: fn(*args, **kwargs))
        except Exception:
            pass

    def _ask_on_main(self, prompt_fn, default=True, timeout_s=120.0):
        """
        Run a modal prompt on the Tk main thread and return its result; safe to
        call from worker threads. Returns `default` on timeout/failure. When
        multiple jobs run concurrently, prompts are skipped entirely (a modal
        would stall sibling encodes) and `default` is returned.
        """
        try:
            if int(getattr(self, "_active_workers", 1) or 1) > 1:
                return default
        except Exception:
            pass
        if threading.current_thread() is threading.main_thread():
            try:
                return prompt_fn()
            except Exception:
                return default
        box = {}
        evt = threading.Event()

        def _run():
            try:
                box["r"] = prompt_fn()
            except Exception:
                box["r"] = default
            finally:
                evt.set()

        try:
            self.root.after(0, _run)
            evt.wait(timeout=timeout_s)
        except Exception:
            return default
        return box.get("r", default)

    # ---- Learning UI: pre-flight estimate (F2) + result dashboard (F3) ----
    def _current_target_bytes(self) -> int:
        """Target size the queue is set to, in bytes (honours the unit combo)."""
        try:
            _v = float(self.target_size_var.get())
        except Exception:
            _v = 10.0
        unit = (self.size_unit_var.get() if hasattr(self, "size_unit_var") else "MB")
        mult = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}.get(unit, 1024 ** 2)
        return max(1, int(_v * mult))

    def _estimate_queue(self):
        """F2 pre-flight panel: predict size/VMAF/worst/time per codec for the
        queued files at the current target, from the ledger — no encoding."""
        import tkinter as tk
        from tkinter import ttk
        try:
            from outcome_ledger import estimate_encode as _est
            from ml_heuristics import extract_media_features as _emf
        except Exception:
            return
        files = list(getattr(self, "file_list", []) or [])[:8]
        if not files:
            try:
                self.update_status("Add files to the queue first to estimate.")
            except Exception:
                pass
            return
        tgt = self._current_target_bytes()
        model = resolve_vmaf_model() or "version=vmaf_v0.6.1"
        stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")

        win = tk.Toplevel(self.root)
        win.title("Pre-flight estimate (from the ledger)")
        win.transient(self.root)
        win.geometry("640x360")
        ttk.Label(win, text=f"Predicted outcome at {human_bytes(tgt)} — no encoding, "
                            f"learned from past encodes.").pack(anchor="w", padx=12, pady=(12, 4))
        cols = ("codec", "size", "vmaf", "worst", "time", "n")
        tree = ttk.Treeview(win, columns=cols, show="tree headings", height=12)
        tree.heading("#0", text="File")
        tree.column("#0", width=200, stretch=True)
        for c, txt, w in (("codec", "Codec", 70), ("size", "~Size", 80),
                          ("vmaf", "VMAF", 70), ("worst", "Worst", 70),
                          ("time", "~Time", 70), ("n", "n", 40)):
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor="e", stretch=False)
        tree.pack(fill="both", expand=True, padx=12, pady=6)

        any_data = False
        for f in files:
            parent = tree.insert("", "end", text=os.path.basename(f), open=True)
            try:
                feats = _emf(f) or {}
            except Exception:
                feats = {}
            w = int(feats.get("width") or 0)
            h = int(feats.get("height") or 0)
            fps = float(feats.get("fps") or 30.0)
            dur = float(feats.get("duration") or 0.0)
            if not (w and h and dur):
                tree.insert(parent, "end", text="", values=("—", "not video / no probe", "", "", "", ""))
                continue
            v_bps = max(24_000, int((tgt * 8.0 / max(1.0, dur) - 128_000) * 0.94))
            for enc in ("x264", "x265", "av1"):
                est = _est(stats_dir, feats, enc, w, h, fps, v_bps, tgt, dur, vmaf_model=model)
                if est["n"] < 1:
                    tree.insert(parent, "end", text="",
                                values=(enc, "no history", "", "", "", "0"))
                    continue
                any_data = True
                tree.insert(parent, "end", text="", values=(
                    est["encoder"],
                    f"{est['size_bytes'] / 1048576.0:.2f} MB",
                    (f"{est['mean']:.1f}" if est["mean"] is not None else "—"),
                    (f"{est['worst']:.0f}" if est["worst"] is not None else "—"),
                    (f"{est['seconds']:.0f}s" if est["seconds"] is not None else "—"),
                    str(est["n"])))
        if not any_data:
            ttk.Label(win, text="No comparable history in the ledger yet — estimates appear "
                                "as you encode similar content.").pack(anchor="w", padx=14, pady=(0, 6))
        ttk.Button(win, text="Close", command=win.destroy).pack(side="right", padx=12, pady=(0, 12))

    def _latest_ledger_record(self, input_path: str):
        """Newest ledger record whose input basename matches (for the dashboard)."""
        try:
            import outcome_ledger as ol
            base = os.path.basename(str(input_path or "")).lower()
            recs = ol.ledger_load(os.path.join(USER_SETTINGS_DIR, "stats"))
            match = [r for r in recs
                     if os.path.basename(str(r.get("input") or "")).lower() == base]
            return match[-1] if match else None
        except Exception:
            return None

    def _show_result_dashboard(self, input_path: str):
        """F3 result dashboard: VMAF-over-time sparkline with the worst window
        flagged + a codec-race scoreboard, for one finished file."""
        import tkinter as tk
        from tkinter import ttk
        try:
            import dashboard as _db
        except Exception:
            return
        rec = self._latest_ledger_record(input_path)
        if not rec:
            try:
                self.update_status("No ledger record for that file yet.")
            except Exception:
                pass
            return
        m = _db.build_dashboard_model(rec)

        win = tk.Toplevel(self.root)
        win.title(f"Result — {os.path.basename(str(input_path))}")
        win.transient(self.root)
        win.geometry("720x430")
        accent = "#4caf7d"
        danger = "#d9655b"
        muted = "#8a8a96"

        head = (f"{m['encoder'] or '-'}   •   mean VMAF "
                f"{m['mean'] if m['mean'] is not None else '—'}   •   "
                f"worst {m['worst'] if m['worst'] is not None else '—'} ({m['band']})"
                + (f"   •   {human_bytes(m['size_bytes'])}" if m.get('size_bytes') else "")
                + (f"   •   {m['encode_seconds']:.0f}s" if m.get('encode_seconds') else ""))
        ttk.Label(win, text=head).pack(anchor="w", padx=14, pady=(12, 2))
        ttk.Label(win, text="VMAF over time — red marks the worst window",
                  foreground=muted).pack(anchor="w", padx=14, pady=(6, 2))

        cw, ch = 680, 170
        c = tk.Canvas(win, width=cw, height=ch, bg=APP_BG, highlightthickness=0, bd=0)
        c.pack(padx=14, pady=(0, 8))
        series = m.get("series") or []
        if series:
            lo = max(0.0, min(series) - 3.0)
            hi = min(100.0, max(series) + 3.0)
            pts = _db.sparkline_points(series, cw, ch, y_min=lo, y_max=hi, pad=16.0)
            for g in (lo + (hi - lo) * 0.25, lo + (hi - lo) * 0.75):
                yy = 16.0 + (ch - 32.0) * (1.0 - (g - lo) / max(1e-6, hi - lo))
                c.create_line(16, yy, cw - 16, yy, fill="#2a2a34")
                c.create_text(cw - 18, yy - 7, text=f"{g:.0f}", fill=muted, anchor="e", font=("", 8))
            flat = [coord for xy in pts for coord in xy]
            if len(flat) >= 4:
                c.create_line(*flat, fill=accent, width=2, smooth=True)
            mk = m.get("worst_marker")
            if mk and pts:
                mx, my = pts[min(mk["index"], len(pts) - 1)]
                c.create_line(mx, 12, mx, ch - 12, fill=danger, dash=(2, 2))
                c.create_oval(mx - 4, my - 4, mx + 4, my + 4, fill=danger, outline="")
                c.create_text(mx, 20, text=f"worst {mk['value']:.0f}", fill=danger, font=("", 9))
        else:
            c.create_text(cw / 2, ch / 2, text="no VMAF series for this encode",
                          fill=muted)

        board = m.get("scoreboard") or []
        if board:
            ttk.Label(win, text="Codec race — VMAF-per-bit on the probe clip",
                      foreground=muted).pack(anchor="w", padx=14, pady=(4, 4))
            bf = tk.Frame(win, bg=APP_BG)
            bf.pack(fill="x", padx=14)
            best = board[0]["score"]
            worst_s = board[-1]["score"]
            span = max(1e-6, best - worst_s)
            for r in board:
                row = tk.Frame(bf, bg=APP_BG)
                row.pack(fill="x", pady=2)
                tk.Label(row, text=r["encoder"], width=7, anchor="w", bg=APP_BG,
                         fg=("#e6e6ee" if r["is_winner"] else muted)).pack(side="left")
                bar = tk.Canvas(row, width=380, height=16, bg=APP_BG, highlightthickness=0)
                bar.pack(side="left", padx=6)
                frac = (r["score"] - worst_s) / span
                bar.create_rectangle(0, 2, 380, 14, fill="#22222c", outline="")
                bar.create_rectangle(0, 2, max(6, 380 * frac), 14,
                                     fill=(accent if r["is_winner"] else "#3a3a46"), outline="")
                lbl = (f"{r['score']:.1f}  best" if r["is_winner"]
                       else f"{r['score']:.1f}  {r['delta']:.1f}")
                tk.Label(row, text=lbl, width=14, anchor="e", bg=APP_BG,
                         fg=(accent if r["is_winner"] else muted)).pack(side="left")
        else:
            ttk.Label(win, text="No codec race ran for this encode (pinned / cached path).",
                      foreground=muted).pack(anchor="w", padx=14, pady=(4, 4))
        ttk.Button(win, text="Close", command=win.destroy).pack(side="right", padx=12, pady=10)

    def scan_for_duplicates(self):
        """Advisory-only batch dedup review: scan the current queue for
        byte-identical files and let the user confirm reusing an already-
        encoded output instead of re-encoding confirmed duplicates. Runs only
        on explicit menu action -- never automatically -- and writes nothing
        to per_file_opts until Apply is clicked (Cancel/close is a no-op).
        Hashing runs on a worker thread (full-file SHA-256 over a real queue
        can take real wall-clock time) so the GUI stays responsive; the
        review window itself is built on the main thread once hashing ends."""
        from ml_heuristics import build_batch_dedup_index as _bc_dedup_index

        snapshot = list(getattr(self, "file_list", []) or [])
        norm = [_normalize_drop_path(p) for p in snapshot if isinstance(p, str)]
        norm = list(dict.fromkeys(norm))  # a file queued twice is not a duplicate of itself
        files = [p for p in norm if os.path.isfile(p)]
        if not files:
            messagebox.showinfo("Scan for Duplicates", "Queue is empty - add files first.")
            return

        busy = tk.Toplevel(self.root)
        busy.title("Scanning for Duplicates")
        busy.transient(self.root)
        busy.geometry("360x90")
        ttk.Label(busy, text=f"Hashing {len(files)} file(s)...").pack(expand=True, pady=20)
        busy.update_idletasks()

        def _worker():
            try:
                groups = _bc_dedup_index(files)
                err = None
            except Exception as e:
                groups, err = [], e
            self._ui(self._present_dedup_review, busy, groups, err)

        threading.Thread(target=_worker, daemon=True).start()

    def _present_dedup_review(self, busy_win, groups, err=None):
        """Main-thread continuation of scan_for_duplicates(), invoked once
        background hashing completes."""
        try:
            busy_win.destroy()
        except Exception:
            pass

        if err is not None:
            messagebox.showerror("Scan for Duplicates", f"Duplicate scan failed: {err}")
            return
        if not groups:
            messagebox.showinfo("Scan for Duplicates", "No byte-identical duplicates found in the queue.")
            return

        win = tk.Toplevel(self.root)
        win.title("Duplicate Files Found")
        win.transient(self.root)
        win.grab_set()  # modal: queue edits mid-review would orphan row_info's path keys
        win.geometry("740x400")

        tree_frame = ttk.Frame(win)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        cols = ("group", "duplicate_of", "action")
        tree = ttk.Treeview(tree_frame, columns=cols, show="tree headings", height=10)
        tree.heading("#0", text="File")
        tree.column("#0", width=260, stretch=True)
        for c, txt, w in (("group", "Group", 60), ("duplicate_of", "Duplicate Of", 220),
                          ("action", "Action", 90)):
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor="w", stretch=False)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        row_info = {}  # item_id -> {"path", "canonical", "confirm"}
        for gi, grp in enumerate(groups, start=1):
            canonical, dupes = grp[0], grp[1:]
            for d in dupes:
                item = tree.insert("", "end", text=os.path.basename(d),
                                   values=(gi, os.path.basename(canonical), "Skip"))
                row_info[item] = {"path": d, "canonical": canonical, "confirm": False}

        n_dupes = sum(len(g) - 1 for g in groups)
        ttk.Label(win, text=f"{len(groups)} duplicate group(s) found ({n_dupes} file(s) "
                            "could be skipped). Select rows, choose Confirm Reuse or Skip - "
                            "nothing happens until Apply.").pack(anchor="w", padx=14, pady=(0, 8))

        def _set_action(action):
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Scan for Duplicates", "Select one or more rows first.")
                return
            for item in sel:
                info = row_info.get(item)
                if not info:
                    continue
                info["confirm"] = (action == "Confirm Reuse")
                tree.set(item, "action", action)

        def _apply():
            if not hasattr(self, "per_file_opts") or not isinstance(getattr(self, "per_file_opts", None), dict):
                self.per_file_opts = {}
            n = 0
            for info in row_info.values():
                if info["confirm"]:
                    cur = dict(self.per_file_opts.get(info["path"], {}) or {})
                    cur["_dedup_canonical_source"] = info["canonical"]
                    self.per_file_opts[info["path"]] = cur
                    n += 1
            self.update_status(f"[Dedup] {n} file(s) marked to reuse a canonical encode.", level="INFO")
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btns, text="Confirm Reuse", command=lambda: _set_action("Confirm Reuse")).pack(side="left")
        ttk.Button(btns, text="Skip", command=lambda: _set_action("Skip")).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Apply", command=_apply).pack(side="right")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 6))

    def _show_batch_summary(self):
        """Post-queue summary: per-file results + totals (main thread only)."""
        results = list(getattr(self, "batch_results", []) or [])
        results = [r for r in results if r.get("error") != "cancelled"]
        if not results:
            return
        try:
            if self.root.state() == "iconic":  # minimized/tray: don't pop a window
                return
        except Exception:
            pass

        win = tk.Toplevel(self.root)
        win.title("Batch Summary")
        win.transient(self.root)
        win.geometry("720x380")

        cols = ("in", "out", "ratio", "vmaf", "time")
        tree = ttk.Treeview(win, columns=cols, show="tree headings", height=10)
        tree.heading("#0", text="File")
        tree.column("#0", width=250, stretch=True)
        for c, txt, w in (("in", "Input", 90), ("out", "Output", 90),
                          ("ratio", "Ratio", 70), ("vmaf", "VMAF", 70), ("time", "Time", 70)):
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor="e", stretch=False)

        tot_in = tot_out = 0
        vmafs = []
        item_paths = {}
        for r in results:
            i_b, o_b = int(r.get("in_bytes") or 0), int(r.get("out_bytes") or 0)
            if r.get("ok"):
                tot_in += i_b
                tot_out += o_b
            v = r.get("vmaf")
            if isinstance(v, (int, float)):
                vmafs.append(float(v))
            _reused = bool(r.get("reused_duplicate_of"))
            _item = tree.insert("", "end",
                        text=os.path.basename(str(r.get("path") or "")) + (" (reused)" if _reused else ""),
                        values=((human_bytes(i_b) if i_b else "—"),
                                (human_bytes(o_b) if o_b else ("FAILED" if not r.get("ok") else "—")),
                                (f"{o_b * 100.0 / i_b:.0f}%" if (i_b and o_b) else "—"),
                                (f"{float(v):.1f}" if isinstance(v, (int, float)) else ("dedup" if _reused else "—")),
                                ("reused" if _reused else f"{float(r.get('secs') or 0.0):.0f}s")))
            item_paths[_item] = str(r.get("path") or "")
        tree.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        # Double-click / Details -> per-file result dashboard (F3).
        def _open_details(_evt=None):
            sel = tree.focus()
            p = item_paths.get(sel)
            if p:
                self._show_result_dashboard(p)
        tree.bind("<Double-1>", _open_details)

        saved = max(0, tot_in - tot_out)
        parts = []
        if tot_in:
            parts.append(f"Total saved: {human_bytes(saved)} "
                         f"({saved * 100.0 / max(1, tot_in):.0f}% smaller)")
        if vmafs:
            parts.append(f"VMAF avg {sum(vmafs) / len(vmafs):.1f} / min {min(vmafs):.1f}")
        ok_n = sum(1 for r in results if r.get("ok"))
        parts.append(f"{ok_n}/{len(results)} succeeded")
        ttk.Label(win, text="   •   ".join(parts)).pack(anchor="w", padx=14, pady=(0, 8))

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btns, text="Open Save Folder",
                   command=getattr(self, "open_save_folder", lambda: None)).pack(side="left")

        def _details_selected():
            sel = tree.focus()
            p = item_paths.get(sel)
            if p:
                self._show_result_dashboard(p)
        ttk.Button(btns, text="View Details",
                   command=_details_selected).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")

    # ---- Queue persistence ----------------------------------------------
    def _queue_json_path(self) -> str:
        return os.path.join(USER_SETTINGS_DIR, "queue.json")

    def _save_queue(self):
        try:
            data = {
                "version": 1,
                "files": list(getattr(self, "file_list", []) or []),
                "per_file_opts": dict(getattr(self, "per_file_opts", {}) or {}),
            }
            p = self._queue_json_path()
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            LOG.debug("Queue save failed", exc_info=True)

    def _load_queue(self):
        try:
            p = self._queue_json_path()
            if not os.path.isfile(p):
                return
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            files = [str(x) for x in (data.get("files") or []) if isinstance(x, str)]
            files = [x for x in files if os.path.isfile(x)]
            if not files:
                return
            opts = data.get("per_file_opts") or {}
            self.per_file_opts = {k: v for k, v in opts.items()
                                  if isinstance(v, dict) and k in files}
            if not hasattr(self, "file_list") or self.file_list is None:
                self.file_list = []
            for x in files:
                if x not in self.file_list:
                    self.file_list.append(x)
            self.refresh_queue_box()
            if not getattr(self, "_queue_restore_announced", False):
                self.update_status(f"Restored {len(files)} queued file(s) from last session.")
                self._queue_restore_announced = True
        except Exception:
            LOG.debug("Queue restore failed", exc_info=True)





    def refresh_lifetime_stats(self):
        """Render the run_*.jsonl roll-up into the Stats tab. Offline, read-only."""
        view = getattr(self, "stats_view", None)
        if view is None:
            return

        def _hb(n):
            try:
                return human_bytes(int(n))
            except Exception:
                return str(n)

        def _bar(frac, width=24):
            frac = max(0.0, min(1.0, float(frac or 0.0)))
            fill = int(round(frac * width))
            return "█" * fill + "·" * (width - fill)

        try:
            a = aggregate_lifetime_stats()
        except Exception as e:
            a = None
        lines = []
        if not a or a.get("count", 0) == 0:
            lines.append("No completed encodes recorded yet.")
            lines.append("")
            lines.append("Stats accumulate here after your first compression —")
            lines.append("total bytes saved, VMAF distribution, and encoder win-rates.")
        else:
            saved = a["bytes_saved"]
            pct_saved = (saved / a["total_original"] * 100.0) if a["total_original"] else 0.0
            lines.append("LIFETIME TOTALS")
            lines.append("─" * 46)
            lines.append(f"  Files compressed : {a['count']:,}")
            lines.append(f"  Original size    : {_hb(a['total_original'])}")
            lines.append(f"  Compressed size  : {_hb(a['total_compressed'])}")
            lines.append(f"  Space saved      : {_hb(saved)}  ({pct_saved:.1f}%)")
            lines.append(f"  Overall ratio    : {a['overall_ratio']*100:.1f}% of original")
            if a.get("first_ts") and a.get("last_ts"):
                lines.append(f"  Span             : {a['first_ts'][:10]} → {a['last_ts'][:10]}")
            lines.append("")

            if a["by_type"]:
                lines.append("BY MEDIA TYPE")
                lines.append("─" * 46)
                for t in sorted(a["by_type"], key=lambda k: -a["by_type"][k]["count"]):
                    bt = a["by_type"][t]
                    r = (bt["compressed"] / bt["original"] * 100.0) if bt["original"] else 0.0
                    lines.append(f"  {t:<7} {bt['count']:>4} files   "
                                 f"{_hb(bt['original'])} → {_hb(bt['compressed'])}  ({r:.0f}%)")
                lines.append("")

            vm = a["vmaf"]
            if vm["count"] > 0:
                lines.append(f"VMAF DISTRIBUTION   (measured on {vm['count']} encodes, avg {vm['avg']:.1f})")
                lines.append("─" * 46)
                vmax = max(vm["buckets"].values()) or 1
                for lbl in ("<80", "80–90", "90–95", "95–98", "98+"):
                    n = vm["buckets"].get(lbl, 0)
                    lines.append(f"  {lbl:<6} {_bar(n / vmax)} {n}")
                lines.append("")

            if a["encoders"]:
                total_enc = sum(a["encoders"].values()) or 1
                lines.append(f"ENCODER WIN-RATES   ({total_enc} encodes with a recorded codec)")
                lines.append("─" * 46)
                for enc in sorted(a["encoders"], key=lambda k: -a["encoders"][k]):
                    n = a["encoders"][enc]
                    lines.append(f"  {enc:<9} {_bar(n / total_enc)} {n}  ({n/total_enc*100:.0f}%)")
                lines.append("")

            if a.get("total_time", 0) > 0:
                lines.append(f"Recorded encode time: {a['total_time']/3600:.1f} h")

        text = "\n".join(lines)
        try:
            view.config(state="normal")
            view.delete("1.0", "end")
            view.insert("1.0", text)
            view.config(state="disabled")
        except Exception:
            pass

    def log(self, msg, level="INFO"):
        # Back-compat shim: several call sites (profile save/load error handlers,
        # the legacy queue processor, cancel_queue, and the folder watcher) call
        # self.log(), but CompressorGUI's real status sink is update_status().
        # Without this, those paths raised AttributeError: 'CompressorGUI' object
        # has no attribute 'log' (observed crashing the folder-watcher enqueue).
        try:
            self.update_status(msg, level=level)
        except Exception:
            try:
                LOG.info(str(msg))
            except Exception:
                pass

    def update_status(self, msg, level="INFO"):
        # Tk widgets may only be touched from the main thread; worker threads
        # re-dispatch through the event loop.
        try:
            if threading.current_thread() is not threading.main_thread():
                self.root.after(0, lambda m=msg, l=level: self.update_status(m, l))
                return
        except Exception:
            pass

        msg = _normalize_text(msg)
        level = str(level or "INFO").upper()
        try:

            lw = getattr(self, "Log_widget", None) or getattr(self, "log_widget", None)
            if lw is not None:
                log_message(lw, msg, level)
            else:

                import logging as _logging
                lvl = getattr(_logging, level, _logging.INFO)
                LOG.log(lvl, str(msg))
        except Exception:
            try:
                LOG.info(str(msg))
            except Exception:
                pass

        try:
            self.all_logs.append((level, msg))
        except Exception:
            try:
                self.all_logs = [(level, msg)]
            except Exception:
                pass

        if hasattr(self, "stage_text"):
            try:
                _friendly = _plain_status(msg)
                if _friendly:
                    self.stage_text.config(state="normal")
                    # A blank line before each new file keeps the feed readable.
                    if _friendly.startswith("Compressing your"):
                        try:
                            if self.stage_text.index("end-1c") not in ("1.0", "0.0"):
                                self.stage_text.insert("end", "\n")
                        except Exception:
                            pass
                    _tag = ("QSCORE",) if _friendly.startswith(("Quality score:", "Quality:")) else ()
                    self.stage_text.insert("end", "   " + _friendly + "\n", _tag)
                    self.stage_text.see("end")
                    self.stage_text.config(state="disabled")
            except Exception:
                pass







    def _apply_dino(self):
        """Show/start or hide/stop the hidden T-Rex runner per the toggle."""
        d = getattr(self, "dino_runner", None)
        if d is None:
            return
        try:
            enabled = bool(int(self.dino_game_var.get())) if hasattr(self, "dino_game_var") else False
        except Exception:
            enabled = False
        try:
            if enabled:
                _before = getattr(self, "_activity_label", None)
                if _before is not None and _before.winfo_exists():
                    d.canvas.pack(side="top", fill="x", padx=12, pady=(10, 0), before=_before)
                else:
                    d.canvas.pack(side="top", fill="x", padx=12, pady=(10, 0))
                d.start()
            else:
                d.stop()
                d.canvas.pack_forget()
        except Exception:
            pass

    def _dino_retry(self, attempt):
        """Feed the size-retry loop's attempt count to the runner so it speeds up
        while BitCrusher is fighting the size cap (no-op if the game is off)."""
        d = getattr(self, "dino_runner", None)
        if d is None:
            return
        try:
            if bool(int(self.dino_game_var.get())):
                d.set_retry_pressure(attempt)
        except Exception:
            pass

    def _toggle_dino(self):
        """Advanced-Options checkbox handler: persist the choice and apply live."""
        try:
            self.save_settings()   # payload now carries "dino_game"
        except Exception:
            pass
        self._apply_dino()

    def apply_log_filter(self, filter_val):
        self.log_widget.config(state="normal")
        self.log_widget.delete(1.0, "end")
        for lev, msg in self.all_logs:
            if filter_val == "ALL" or lev == filter_val:
                timestamp = time.strftime("%H:%M:%S")
                _lv = str(lev or "INFO").upper()
                line = f"{timestamp}   {_lv:<7} {_normalize_text(msg)}\n"
                try:
                    self.log_widget.insert("end", line, (_lv,))
                except Exception:
                    self.log_widget.insert("end", line)
        self.log_widget.config(state="disabled")

    def open_advanced_options(self):
        import tkinter as tk
        from tkinter import ttk

        try:
            if not hasattr(self, "adv_encoder"):         self.adv_encoder = tk.StringVar(value=ADVANCED_DEFAULTS.get("encoder", "x264"))
            if not hasattr(self, "adv_iterative"):       self.adv_iterative = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("iterative", False) else 0)
            if not hasattr(self, "adv_two_pass"):        self.adv_two_pass = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("two_pass", False) else 0)
            if not hasattr(self, "adv_manual_crf"):      self.adv_manual_crf = tk.StringVar(value=str(ADVANCED_DEFAULTS.get("manual_crf", "")))
            if not hasattr(self, "adv_manual_bitrate"):  self.adv_manual_bitrate = tk.StringVar(value=str(ADVANCED_DEFAULTS.get("manual_bitrate", "")))
            if not hasattr(self, "adv_output_prefix"):   self.adv_output_prefix = tk.StringVar(value=str(ADVANCED_DEFAULTS.get("output_prefix", "")))
            if not hasattr(self, "adv_output_suffix"):   self.adv_output_suffix = tk.StringVar(value=str(ADVANCED_DEFAULTS.get("output_suffix", "_discord_ready")))
            if not hasattr(self, "adv_audio_format"):    self.adv_audio_format = tk.StringVar(value=str(ADVANCED_DEFAULTS.get("audio_format", "aac")))
            if not hasattr(self, "adv_image_format"):    self.adv_image_format = tk.StringVar(value=str(ADVANCED_DEFAULTS.get("image_format", "jpg")))
            if not hasattr(self, "adv_concurrent"):      self.adv_concurrent = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("concurrent", False) else 0)
            if not hasattr(self, "adv_auto_output"):     self.adv_auto_output = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("auto_output_folder", False) else 0)
            if not hasattr(self, "adv_guetzli"):         self.adv_guetzli = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("guetzli", False) else 0)
            if not hasattr(self, "adv_pngopt"):          self.adv_pngopt = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("pngopt", False) else 0)
            if not hasattr(self, "adv_auto_jpeg"):       self.adv_auto_jpeg = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("auto_jpeg", False) else 0)
            if not hasattr(self, "adv_quality_first"):
                self.adv_quality_first = tk.IntVar(value=1 if str(ADVANCED_DEFAULTS.get("quality_mode", "quality_first")).strip().lower() == "quality_first" else 0)
            if not hasattr(self, "adv_measure_quality"): self.adv_measure_quality = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("measure_quality", True) else 0)
            if not hasattr(self, "adv_auto_codec"):      self.adv_auto_codec = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("auto_codec", True) else 0)
            if not hasattr(self, "adv_min_vmaf"):        self.adv_min_vmaf = tk.IntVar(value=int(ADVANCED_DEFAULTS.get("min_vmaf", 0)))
            if not hasattr(self, "adv_hwaccel"):         self.adv_hwaccel = tk.StringVar(value=str(ADVANCED_DEFAULTS.get("hwaccel", "CPU")))
        except Exception:
            pass

        adv = tk.Toplevel(self.root)
        adv.title("Advanced Options")
        try:
            adv.configure(bg=APP_BG)
        except Exception:
            pass
        adv.transient(self.root)
        adv.grab_set()
        adv.lift()
        adv.focus_force()
        # Size to fit the screen (leave room for taskbar); resizable.
        try:
            _sh = self.root.winfo_screenheight()
        except Exception:
            _sh = 900
        _h = max(480, min(760, _sh - 120))
        adv.geometry(f"600x{_h}")
        adv.minsize(560, 420)

        # Fixed button bar at the bottom (packed first so it never gets clipped
        # by the scrolling content above it).
        btnbar = ttk.Frame(adv, padding=(12, 8))
        btnbar.pack(side="bottom", fill="x")

        # Scrollable body: a canvas + vertical scrollbar holds all the option
        # sections, so the dialog can't grow taller than the screen.
        _body = ttk.Frame(adv)
        _body.pack(side="top", fill="both", expand=True)
        _canvas = tk.Canvas(_body, bg=APP_BG, highlightthickness=0, borderwidth=0)
        _vsb = ttk.Scrollbar(_body, orient="vertical", command=_canvas.yview)
        _canvas.configure(yscrollcommand=_vsb.set)
        _vsb.pack(side="right", fill="y")
        _canvas.pack(side="left", fill="both", expand=True)

        container = ttk.Frame(_canvas, padding=12, style="Card.TFrame")
        _win = _canvas.create_window((0, 0), window=container, anchor="nw")
        for c in (0, 1):
            container.grid_columnconfigure(c, weight=1)

        def _on_body_config(_e=None):
            _canvas.configure(scrollregion=_canvas.bbox("all"))
        container.bind("<Configure>", _on_body_config)
        # Keep the inner frame as wide as the canvas viewport.
        _canvas.bind("<Configure>", lambda e: _canvas.itemconfigure(_win, width=e.width))

        # Mouse-wheel scrolling while the pointer is over the dialog.
        def _on_wheel(e):
            try:
                _canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        _canvas.bind_all("<MouseWheel>", _on_wheel)
        adv.bind("<Destroy>", lambda e: (_canvas.unbind_all("<MouseWheel>") if e.widget is adv else None), add="+")

        video = ttk.LabelFrame(container, text="Video", padding=10)
        audio = ttk.LabelFrame(container, text="Audio", padding=10)
        output = ttk.LabelFrame(container, text="Output & Naming", padding=10)
        images = ttk.LabelFrame(container, text="Images", padding=10)
        misc   = ttk.LabelFrame(container, text="Misc", padding=10)
        watchrules = ttk.LabelFrame(container, text="Watcher Rules (folder watcher / Send-To)", padding=10)

        video.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        audio.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        output.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        images.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        misc.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        watchrules.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        ttk.Label(video, text="Encoder").grid(row=0, column=0, sticky="e", padx=6, pady=4)

        enc_values = [
            "x264", "x265",
            "svt-av1", "aom-av1",  # AV1 (SVT, AOM)
            "vp9",                 # VP9 (libvpx-vp9)
            "vvc",                 # VVC (libvvenc)
            "h264_nvenc", "hevc_nvenc", "av1_nvenc",  # NVIDIA NVENC
            "h264_qsv", "hevc_qsv", "av1_qsv",        # Intel Quick Sync
            "h264_amf", "hevc_amf", "av1_amf"         # AMD AMF
        ]
        enc_combo = ttk.Combobox(video, textvariable=self.adv_encoder, values=enc_values, state="readonly", width=18)
        enc_combo.grid(row=0, column=1, sticky="ew", padx=6, pady=4)

        ttk.Label(video, text="Manual CRF").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(video, textvariable=self.adv_manual_crf, width=10).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(video, text="Manual Bitrate (bps)").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(video, textvariable=self.adv_manual_bitrate, width=14).grid(row=2, column=1, sticky="w", padx=6, pady=4)

        ttk.Checkbutton(video, text="Enable Two-pass", variable=self.adv_two_pass).grid(row=3, column=0, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(video, text="Iterative Size Targeting", variable=self.adv_iterative).grid(row=3, column=1, sticky="w", padx=6, pady=4)
        if not hasattr(self, "adv_quality_mode"):
            self.adv_quality_mode = tk.StringVar(value="max")
        _qrow = ttk.Frame(video)
        _qrow.grid(row=5, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Label(_qrow, text="Quality mode:").pack(side="left", padx=(0, 8))
        for _qval, _qtxt in (("fast", "Fast"), ("balanced", "Balanced"), ("max", "Max Quality (slow)")):
            ttk.Radiobutton(_qrow, text=_qtxt, value=_qval, variable=self.adv_quality_mode).pack(side="left", padx=(0, 10))
        # Keep the legacy boolean in sync for old settings readers.
        def _sync_qf(*_a):
            try:
                self.adv_quality_first.set(0 if self.adv_quality_mode.get() == "fast" else 1)
            except Exception:
                pass
        try:
            self.adv_quality_mode.trace_add("write", _sync_qf)
        except Exception:
            pass

        ttk.Checkbutton(video, text="Auto-pick best codec (VMAF probe vs AV1)", variable=self.adv_auto_codec).grid(row=8, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        if hasattr(self, "adv_scene_zones"):
            ttk.Checkbutton(video, text="Scene-aware bitrate zones (x264/x265)", variable=self.adv_scene_zones).grid(row=9, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        if hasattr(self, "adv_hw_decode"):
            ttk.Checkbutton(video, text="GPU-accelerated decode of source", variable=self.adv_hw_decode).grid(row=10, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(video, text="Measure quality (VMAF) after encode", variable=self.adv_measure_quality).grid(row=6, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Label(video, text="Min VMAF (0 = off)").grid(row=7, column=0, sticky="e", padx=6, pady=4)
        _vmaf_row = ttk.Frame(video); _vmaf_row.grid(row=7, column=1, sticky="w", padx=6, pady=4)
        ttk.Spinbox(_vmaf_row, from_=0, to=100, textvariable=self.adv_min_vmaf, width=6, wrap=False).pack(side="left")
        ttk.Label(_vmaf_row, text="  (e.g. 92 = keep quality high, growing toward the size limit)",
                  style="Sub.TLabel").pack(side="left")

        ttk.Label(video, text="Hardware").grid(row=4, column=0, sticky="e", padx=6, pady=4)
        hw_values = ["CPU", "NVENC", "QSV", "AMF", "VAAPI"]
        ttk.Combobox(video, textvariable=self.adv_hwaccel, values=hw_values, state="readonly", width=10).grid(row=4, column=1, sticky="w", padx=6, pady=4)

        if not hasattr(self, "adv_discord_compat"):
            self.adv_discord_compat = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("discord_compat", False) else 0)
        ttk.Checkbutton(video, text="Discord-compatible (force H.264 + AAC / MP4)",
                        variable=self.adv_discord_compat).grid(row=11, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        if not hasattr(self, "adv_smart_preproc"):
            self.adv_smart_preproc = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("smart_preproc", True) else 0)
        ttk.Checkbutton(video, text="Artifact-aware preprocessing (VMAF-validated deband/denoise)",
                        variable=self.adv_smart_preproc).grid(row=12, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        if not hasattr(self, "adv_learned_seed"):
            self.adv_learned_seed = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("learned_seed", True) else 0)
        ttk.Checkbutton(video, text="Learned first-attempt bitrate seeding (outcome ledger)",
                        variable=self.adv_learned_seed).grid(row=13, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        if not hasattr(self, "adv_preflight"):
            self.adv_preflight = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("preflight_advice", True) else 0)
        ttk.Checkbutton(video, text="Pre-flight quality/size advice (advisory only)",
                        variable=self.adv_preflight).grid(row=14, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        if not hasattr(self, "adv_ceiling_downscale"):
            self.adv_ceiling_downscale = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("ceiling_downscale_retry", True) else 0)
        ttk.Checkbutton(video, text="Downscale-and-retry if size cap can't be met at native resolution",
                        variable=self.adv_ceiling_downscale).grid(row=15, column=0, columnspan=2, sticky="w", padx=6, pady=4)

        ttk.Label(audio, text="Audio Format").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        audio_values = ["aac", "opus", "mp3"]
        ttk.Combobox(audio, textvariable=self.adv_audio_format, values=audio_values, state="readonly", width=10).grid(row=0, column=1, sticky="w", padx=6, pady=4)

        if not hasattr(self, "adv_audio_track_mode"):
            self.adv_audio_track_mode = tk.StringVar(value=str(ADVANCED_DEFAULTS.get("audio_track_mode", "keepfirst")))
        ttk.Label(audio, text="Multi-track audio").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        _atrow = ttk.Frame(audio); _atrow.grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(_atrow, text="Keep first", value="keepfirst",
                        variable=self.adv_audio_track_mode).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(_atrow, text="Mix all (amix)", value="mix",
                        variable=self.adv_audio_track_mode).pack(side="left")
        if not hasattr(self, "adv_embed_lyrics"):
            self.adv_embed_lyrics = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("embed_lyrics", True) else 0)
        ttk.Checkbutton(audio, text="Embed sibling .lrc lyrics into tags",
                        variable=self.adv_embed_lyrics).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=4)

        ttk.Label(output, text="Output Prefix").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(output, textvariable=self.adv_output_prefix, width=20).grid(row=0, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(output, text="Output Suffix").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(output, textvariable=self.adv_output_suffix, width=20).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        if not hasattr(self, "adv_copy_clipboard"):
            self.adv_copy_clipboard = tk.IntVar(value=1 if ADVANCED_DEFAULTS.get("copy_to_clipboard", False) else 0)
        ttk.Checkbutton(output, text="Copy result to clipboard (Ctrl+V into Discord)",
                        variable=self.adv_copy_clipboard).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=4)

        ttk.Label(images, text="Image Format").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        img_values = ["jpg", "png", "webp"]
        ttk.Combobox(images, textvariable=self.adv_image_format, values=img_values, state="readonly", width=10).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(images, text="PNG Optimize", variable=self.adv_pngopt).grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(images, text="Auto JPEG", variable=self.adv_auto_jpeg).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Checkbutton(misc, text="Concurrent Compression", variable=self.adv_concurrent).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(misc, text="Auto Create Output Folder", variable=self.adv_auto_output).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        if hasattr(self, "adv_grain_filter"):
            ttk.Checkbutton(misc, text="Grain-aware denoise filter", variable=self.adv_grain_filter).grid(row=1, column=0, sticky="w", padx=6, pady=4)
        if not hasattr(self, "dino_game_var"):
            self.dino_game_var = tk.IntVar(value=1 if (getattr(self, "settings", {}) or {}).get("dino_game", False) else 0)
        ttk.Checkbutton(misc, text="Hidden dino runner (Activity area)", variable=self.dino_game_var,
                        command=self._toggle_dino).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        # --- Watcher Rules -------------------------------------------------
        # Optional conditions applied only to files the folder watcher / Send-To
        # bring in. Every field can be left blank (= use the global setting).
        self._ensure_watch_rule_vars()
        _enc_choices = ("", "x264", "x265", "svt-av1", "aom-av1", "vp9",
                        "h264_nvenc", "hevc_nvenc", "av1_nvenc")
        ttk.Label(watchrules, text="Leave any field blank to fall back to the global setting.",
                  style="Sub.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 6))

        # Custom output folder for watched files.
        ttk.Label(watchrules, text="Save watched files to").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        _wsd_row = ttk.Frame(watchrules); _wsd_row.grid(row=1, column=1, columnspan=3, sticky="ew", padx=6, pady=4)
        ttk.Entry(_wsd_row, textvariable=self.wr_vars["save_dir"], width=34).pack(side="left", fill="x", expand=True)
        def _pick_wr_dir():
            try:
                d = filedialog.askdirectory(title="Folder for watched-file output")
                if d:
                    self.wr_vars["save_dir"].set(d)
            except Exception:
                pass
        ttk.Button(_wsd_row, text="…", width=3, style="Ghost.TButton", command=_pick_wr_dir).pack(side="left", padx=(6, 0))

        # Watched-file defaults.
        ttk.Label(watchrules, text="Default target size (MB)").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(watchrules, textvariable=self.wr_vars["target_mb"], width=8).grid(row=2, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(watchrules, text="Default encoder").grid(row=2, column=2, sticky="e", padx=6, pady=4)
        ttk.Combobox(watchrules, textvariable=self.wr_vars["encoder"], values=_enc_choices,
                     state="readonly", width=12).grid(row=2, column=3, sticky="w", padx=6, pady=4)

        ttk.Separator(watchrules, orient="horizontal").grid(row=3, column=0, columnspan=4, sticky="ew", pady=6)

        # Size-conditional target overrides.
        ttk.Label(watchrules, text="If larger than (MB)").grid(row=4, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(watchrules, textvariable=self.wr_vars["big_mb"], width=8).grid(row=4, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(watchrules, text="→ target (MB)").grid(row=4, column=2, sticky="e", padx=6, pady=4)
        ttk.Entry(watchrules, textvariable=self.wr_vars["big_target"], width=8).grid(row=4, column=3, sticky="w", padx=6, pady=4)

        ttk.Label(watchrules, text="If smaller than (MB)").grid(row=5, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(watchrules, textvariable=self.wr_vars["small_mb"], width=8).grid(row=5, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(watchrules, text="→ target (MB)").grid(row=5, column=2, sticky="e", padx=6, pady=4)
        ttk.Entry(watchrules, textvariable=self.wr_vars["small_target"], width=8).grid(row=5, column=3, sticky="w", padx=6, pady=4)

        # Duration-conditional encoder overrides.
        ttk.Label(watchrules, text="If longer than (min)").grid(row=6, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(watchrules, textvariable=self.wr_vars["long_min"], width=8).grid(row=6, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(watchrules, text="→ encoder").grid(row=6, column=2, sticky="e", padx=6, pady=4)
        ttk.Combobox(watchrules, textvariable=self.wr_vars["long_enc"], values=_enc_choices,
                     state="readonly", width=12).grid(row=6, column=3, sticky="w", padx=6, pady=4)

        ttk.Label(watchrules, text="If shorter than (min)").grid(row=7, column=0, sticky="e", padx=6, pady=4)
        ttk.Entry(watchrules, textvariable=self.wr_vars["short_min"], width=8).grid(row=7, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(watchrules, text="→ encoder").grid(row=7, column=2, sticky="e", padx=6, pady=4)
        ttk.Combobox(watchrules, textvariable=self.wr_vars["short_enc"], values=_enc_choices,
                     state="readonly", width=12).grid(row=7, column=3, sticky="w", padx=6, pady=4)
        for _c in range(4):
            watchrules.grid_columnconfigure(_c, weight=1)

        def _close_and_save():
            try:
                self.advanced_options = self.gather_advanced_options()
            except Exception:
                pass
            try:
                self._save_watch_rules()
            except Exception:
                pass
            adv.destroy()
        ttk.Button(btnbar, text="OK", command=_close_and_save).pack(side="right", padx=(6, 0))
        ttk.Button(btnbar, text="Cancel", style="Ghost.TButton", command=adv.destroy).pack(side="right")

        _on_body_config()
        adv.wait_window()



    def cancel_compression(self):
        self.cancel_flag = True
        self.update_status("Cancel requested.")

    def compression_cancelled(self):
        return self.cancel_flag

    def compress_all(self):

        self.cancel_flag = False
        self.compression_cancelled = False
        self.paused = False

        files = list(getattr(self, "_thread_file_list", []) or [])
        files = [_normalize_drop_path(p) for p in files if isinstance(p, str)]
        files = [p for p in files if os.path.isfile(p)]
        # Confirmed dedup duplicates must dispatch after their canonical so
        # _dedup_canonical_outputs already has an entry by the time _run_one()
        # looks it up (sequential/single-worker case; under concurrent workers
        # completion order isn't guaranteed regardless -- _run_one() falls
        # back to a normal encode when the canonical isn't ready yet, so
        # correctness never depends on this, only the time-saving optimization does).
        _po_for_order = getattr(self, "per_file_opts", None) or {}
        _is_dup = lambda p: isinstance(_po_for_order.get(p), dict) and "_dedup_canonical_source" in _po_for_order[p]
        if any(_is_dup(p) for p in files):
            files = [p for p in files if not _is_dup(p)] + [p for p in files if _is_dup(p)]
        total = len(files)

        if total == 0:
            self._ui(self.update_status, "No files in queue to compress.", level="ERROR")
            self.compression_running = False
            return

        def _prep():
            try:
                self.ensure_progress_bars()
                try:
                    self.progress.stop()
                except Exception:
                    pass
                self.progress["mode"] = "determinate"
                self.progress["maximum"] = max(1, total)
                self.progress["value"] = 0
            except Exception:
                pass
        self.root.after(0, _prep)

        adv_base = dict(getattr(self, "_thread_adv_options", None)
                        or getattr(self, "advanced_options", None) or {})
        tgt = int(getattr(self, "_thread_target_bytes", 0) or self._get_target_bytes())
        webhook = (self.webhook_url.get()
                   if hasattr(self, "webhook_url") and self.use_webhook.get() else "")

        workers = 1
        if adv_base.get("concurrent"):
            workers = max(1, min(3, (os.cpu_count() or 8) // 8))
        self._active_workers = workers

        # Throttle progress-only row updates to ~4 Hz per job.
        _last_row_update: dict = {}

        def _progress_event(job_id, ev):
            try:
                stage = str((ev or {}).get("stage") or "")
                kw = {}
                if ev.get("pct") is not None:
                    kw["progress"] = float(ev["pct"])
                if ev.get("eta_s"):
                    kw["eta"] = int(ev["eta_s"])
                label = {
                    "probing": "probing",
                    "pass1": "encoding 1/2",
                    "pass2": "encoding 2/2",
                    "encoding": "encoding",
                    "retrying": (f"retry {ev.get('attempt')}" if ev.get("attempt") else "retrying"),
                    "refining": "refining",
                    "packing": (f"packing {ev.get('attempt')}" if ev.get("attempt") else "packing"),
                    "vmaf": "measuring",
                }.get(stage)
                if label:
                    kw["status"] = label
                if stage in ("retrying", "packing") and ev.get("attempt"):
                    self._ui(self._dino_retry, int(ev.get("attempt")))
                if not kw:
                    return
                now = time.monotonic()
                if "status" not in kw and now - _last_row_update.get(job_id, 0.0) < 0.25:
                    return
                _last_row_update[job_id] = now
                self._ui(self._job_update, job_id, **kw)
            except Exception:
                pass

        t_start = time.time()

        # Batch dedup: source path -> {"output_path", "target", "adv"} for every
        # real (non-reused) encode that completes, filled in as _run_one()
        # finishes. A confirmed duplicate (per_file_opts' _dedup_canonical_source)
        # looks itself up here before encoding; if the canonical hasn't finished
        # yet (e.g. concurrent workers) it falls through to a normal encode --
        # correctness over speed.
        _dedup_canonical_outputs: dict = {}
        _DEDUP_SETTINGS_KEYS = ("encoder", "codec_pinned", "output_prefix", "output_suffix",
                                "container", "trim_range", "spotlight_range")

        def _run_one(idx, path):
            if self.cancel_flag or self.compression_cancelled:
                self._ui(self._job_update, path, status="cancelled")
                return {"path": path, "ok": False, "in_bytes": 0, "out_bytes": 0,
                        "vmaf": None, "encoder": None, "secs": 0.0, "error": "cancelled"}
            out_dir = getattr(self, "_thread_save_path", "") or os.path.dirname(path) or os.getcwd()
            adv = dict(adv_base)
            _po = getattr(self, "per_file_opts", None)
            if isinstance(_po, dict) and path in _po:
                adv.update(_po[path])
                if "_dedup_canonical_source" in _po[path]:
                    # Consume from the PERSISTENT store now, not just this local
                    # adv copy -- compress_file_task() independently re-merges
                    # per_file_opts[path] into adv_options, so leaving the key
                    # there would resurrect a stale marker on a future run.
                    _po[path] = {k: v for k, v in _po[path].items() if k != "_dedup_canonical_source"}
            _dedup_canon_src = adv.pop("_dedup_canonical_source", None)
            # Watcher-rule per-file overrides (custom folder / target size for
            # files brought in by the folder watcher or Send-To).
            _file_target = tgt
            try:
                _wsd = adv.pop("_watch_save_dir", None)
                if _wsd:
                    try:
                        os.makedirs(_wsd, exist_ok=True)
                    except Exception:
                        pass
                    if os.path.isdir(_wsd):
                        out_dir = _wsd
                _wtb = adv.pop("_watch_target_bytes", None)
                if _wtb and int(_wtb) > 0:
                    _file_target = int(_wtb)
            except Exception:
                _file_target = tgt

            if _dedup_canon_src:
                _canon = _dedup_canonical_outputs.get(_dedup_canon_src)
                _settings_match = bool(_canon) and _file_target == _canon.get("target") and all(
                    adv.get(k) == _canon["adv"].get(k) for k in _DEDUP_SETTINGS_KEYS)
                if _canon and _settings_match and os.path.isfile(_canon.get("output_path", "")):
                    try:
                        from ml_heuristics import _bc_content_hash as _dedup_hash
                        # TOCTOU guard: re-verify content now, not just at scan
                        # time, in case the file changed between scan and encode.
                        if _dedup_hash(path) == _dedup_hash(_dedup_canon_src):
                            _dup_out = _bc_build_output_path(path, out_dir, adv,
                                                             default_ext=os.path.splitext(_canon["output_path"])[1].lstrip("."))
                            _dup_out = _dedup_safe_output_path(_dup_out, _canon["output_path"])
                            shutil.copyfile(_canon["output_path"], _dup_out)
                            in_b = os.path.getsize(path) if os.path.isfile(path) else 0
                            out_b = os.path.getsize(_dup_out) if os.path.isfile(_dup_out) else 0
                            self._ui(self._job_update, path, status="done", progress=100, eta="", size=out_b)
                            self.update_status(f"[Dedup] Reused canonical output for "
                                              f"{os.path.basename(path)}", level="INFO")
                            return {"path": path, "ok": True, "in_bytes": in_b, "out_bytes": out_b,
                                   "vmaf": None, "encoder": None, "secs": 0.0, "error": None,
                                   "output_path": _dup_out, "reused_duplicate_of": _dedup_canon_src}
                        else:
                            self.update_status(f"[Dedup] {os.path.basename(path)} no longer matches "
                                              f"its canonical (changed since scan) - encoding normally.",
                                              level="INFO")
                    except Exception as _dedup_e:
                        self.update_status(f"[Dedup] Reuse failed for {os.path.basename(path)} "
                                          f"({_dedup_e}) - encoding normally instead.", level="WARN")
                        # fall through to a normal encode
                elif _canon and not _settings_match:
                    self.update_status(f"[Dedup] {os.path.basename(path)}'s settings differ from its "
                                      f"canonical's - encoding normally instead of reusing.", level="INFO")

            try:
                os.makedirs(os.path.join(USER_SETTINGS_DIR, "logs", "jobs"), exist_ok=True)
                adv["job_log"] = os.path.join(
                    USER_SETTINGS_DIR, "logs", "jobs",
                    f"{int(time.time())}_{os.path.basename(path)}.log")
            except Exception:
                pass
            adv["job_id"] = path
            adv["progress_cb"] = _progress_event

            self._ui(self._job_update, path, status="starting", progress=0)
            self.update_status(f"[>] Processing {os.path.basename(path)} ({idx}/{total})", level="INFO")
            t0 = time.time()
            try:
                stats = self.compress_file_task(
                    filepath=path, output_folder=out_dir,
                    target_size=_file_target, webhook=webhook, adv_options=adv) or {}
                ok = bool(stats.get("compressed_size"))
                res = {"path": path, "ok": ok,
                       "in_bytes": int(stats.get("original_size") or 0),
                       "out_bytes": int(stats.get("compressed_size") or 0),
                       "vmaf": stats.get("vmaf"),
                       "encoder": stats.get("encoder"),
                       "secs": time.time() - t0, "error": None if ok else "no output"}
                _out_path = stats.get("output_path") or stats.get("out_path") or stats.get("output")
                if ok and _out_path:
                    res["output_path"] = _out_path
                    _dedup_canonical_outputs[path] = {"output_path": _out_path, "target": _file_target,
                                                      "adv": {k: adv.get(k) for k in _DEDUP_SETTINGS_KEYS}}
            except Exception as e:
                res = {"path": path, "ok": False, "in_bytes": 0, "out_bytes": 0,
                       "vmaf": None, "encoder": None,
                       "secs": time.time() - t0, "error": str(e)}
                self.update_status(f"[X] Compression error for {path}: {e}", level="ERROR")

            if res["ok"]:
                self._ui(self._job_update, path, status="done", progress=100, eta="",
                         size=(res["out_bytes"] or None),
                         vmaf=(res["vmaf"] if isinstance(res["vmaf"], (int, float)) else None))
            else:
                _st = "cancelled" if (self.cancel_flag or self.compression_cancelled) else "failed"
                self._ui(self._job_update, path, status=_st, eta="")
            return res

        results = []
        if workers <= 1:
            for idx, path in enumerate(files, start=1):
                results.append(_run_one(idx, path))
                self.root.after(0, self._bump_progress, len(results))
        else:
            self.update_status(f"[~] Concurrent mode: encoding up to {workers} files at once.", level="INFO")
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bc_job") as pool:
                futs = {pool.submit(_run_one, idx, path): path
                        for idx, path in enumerate(files, start=1)}
                done_n = 0
                for fut in as_completed(futs):
                    try:
                        results.append(fut.result())
                    except Exception as e:
                        results.append({"path": futs[fut], "ok": False, "in_bytes": 0,
                                        "out_bytes": 0, "vmaf": None, "encoder": None,
                                        "secs": 0.0, "error": str(e)})
                    done_n += 1
                    self.root.after(0, self._bump_progress, done_n)

        self.batch_results = results
        self._active_workers = 1
        processed = sum(1 for r in results if r.get("ok"))
        cancelled = sum(1 for r in results if r.get("error") == "cancelled")
        errors = sum(1 for r in results if not r.get("ok") and r.get("error") != "cancelled")
        dt = time.time() - t_start

        def _finish():
            try:
                self.progress.stop()
                self.progress["mode"] = "indeterminate"
            except Exception:
                pass
            if processed > 0:
                _extra = f", {cancelled} cancelled" if cancelled else ""
                try:
                    self.update_status(
                        f"[OK] All files processed. {processed}/{total} ok, {errors} errors{_extra} in {dt:.1f}s.",
                        level="INFO"
                    )
                except Exception:
                    pass
                try:
                    self.display_statistics()
                except Exception:
                    pass
                try:
                    self._show_batch_summary()
                except Exception:
                    pass
                try:
                    self.refresh_lifetime_stats()
                except Exception:
                    pass
            else:
                try:
                    self.update_status("No files were processed.", level="ERROR")
                except Exception:
                    pass

        self.root.after(0, _finish)
        self.compression_running = False


    def ensure_progress_bars(self):
        """Create the overall progress bar if the UI hasn't built one yet.
        (Historically this method didn't exist at all — every caller swallowed
        the AttributeError and the app simply never showed progress.)"""
        if getattr(self, "progress", None) is not None:
            return
        try:
            import tkinter as tk
            from tkinter import ttk
            self.progress = ttk.Progressbar(self.root, style="Accent.Horizontal.TProgressbar",
                                            mode="determinate")
            self.progress.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        except Exception:
            self.progress = None

    def _bump_progress(self, v):
        try:

            self.ensure_progress_bars()
            if hasattr(self, "progress") and self.progress:
                self.progress.configure(value=v)
        except Exception:
            pass


# watchdog is OPTIONAL — FolderWatcher falls back to polling when it's absent.
# This was an unconditional import, so a machine without watchdog couldn't even
# start BitCrusher (the whole module failed to load). Guard it.
try:
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent
    from watchdog.observers import Observer
except Exception:
    class FileSystemEventHandler:  # minimal stand-in so subclasses still define
        pass
    FileCreatedEvent = FileMovedEvent = ()   # isinstance(x, ()) is always False
    Observer = None
import queue as _queue
from fnmatch import fnmatch


class FolderWatcher:
    """
    Cross-platform folder watcher with include/exclude globs, recursive mode,
    modification-stability window, and debounce. Uses watchdog if present,
    else falls back to polling.
    """
    def __init__(self, on_file_ready, include_globs=None, exclude_globs=None,
                 recursive=True, stable_secs=1.0, debounce_ms=300,
                 poll_interval=0.8, status_cb=None, notify_cb=None,
                 exts=None, min_bytes=0, ignore_globs=None, **_k):
        self.on_file_ready = on_file_ready
        self.include = list(include_globs or ["*.mp4","*.mov","*.mkv","*.webm",
                                              "*.mp3","*.m4a","*.aac","*.wav",
                                              "*.jpg","*.jpeg","*.png","*.gif","*.webp","*.pdf"])
        self.exclude = list(exclude_globs or ["~*","*.tmp","*.part","*.crdownload"])
        self.recursive = bool(recursive)
        self.stable_secs = float(stable_secs)
        self.debounce_ms = int(debounce_ms)
        self.poll_interval = float(poll_interval)
        self._observer = None
        self._paths = set()
        self._seen = {}
        self._timer = None
        self._lock = threading.Lock()
        self._status = status_cb or (lambda *_: None)
        self._notify = notify_cb or (lambda *_: None)
        # --- legacy arg compatibility ---
        # mirror callbacks under the names used by older code paths
        self.status_cb = self._status
        self.notify_cb = self._notify

        # normalize legacy args
        self.exts = [e if str(e).startswith(".") else f".{e}" for e in (exts or [])]
        self.min_bytes = int(min_bytes or 0)
        self.ignore_globs = list(ignore_globs or [])

        # keep include globs in sync when extensions are provided
        if self.exts:
            try:
                self.include.extend([f"*{e.lower()}" for e in self.exts if e])
            except Exception:
                pass
        # --------------------------------

    def add_path(self, path: str):
        p = os.path.abspath(path)
        if not os.path.isdir(p):
            self._status(f"Watch path not a directory: {p}", "WARN"); return
        self._paths.add(p)

    def _match(self, name: str) -> bool:
        import fnmatch
        if any(fnmatch.fnmatch(name, pat) for pat in self.exclude): return False
        return any(fnmatch.fnmatch(name, pat) for pat in self.include)

    # watchdog backend
    def _start_watchdog(self):
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        class H(FileSystemEventHandler):
            def __init__(self, outer): self.outer = outer
            def on_created(self, e):  self.outer._mark(e.src_path)
            def on_modified(self, e): self.outer._mark(e.src_path)
            def on_moved(self, e):    self.outer._mark(getattr(e, "dest_path", e.src_path))
        self._observer = Observer()
        h = H(self)
        for p in self._paths:
            self._observer.schedule(h, p, recursive=self.recursive)
        self._observer.start()

    def _mark(self, path):
        try:
            if not os.path.isfile(path): return
            name = os.path.basename(path)
            if not self._match(name): return
            with self._lock:
                self._seen[path] = time.time()
            self._schedule_flush()
        except Exception:
            pass

    def _schedule_flush(self):
        import tkinter as tk
        try:
            if self._timer: self._timer.cancel()
        except Exception:
            pass
        self._timer = threading.Timer(self.debounce_ms/1000.0, self._flush_ready)
        self._timer.daemon = True
        self._timer.start()

    def _flush_ready(self):
        now = time.time()
        ready = []
        with self._lock:
            for p, ts in list(self._seen.items()):
                try:
                    mtime = os.path.getmtime(p)
                except Exception:
                    self._seen.pop(p, None); continue
                if now - mtime >= self.stable_secs:
                    ready.append(p); self._seen.pop(p, None)
        for p in sorted(set(ready)):
            try: self.on_file_ready(p)
            except Exception: pass
            try: self._notify(f"Detected new file: {p}")
            except Exception: pass

    # polling backend
    def _start_poll(self):
        def _loop():
            last = {}
            while self._observer is True:
                for base in list(self._paths):
                    for root, _dirs, files in os.walk(base) if self.recursive else [(base, [], os.listdir(base))]:
                        for n in files:
                            if not self._match(n): continue
                            fp = os.path.join(root, n)
                            try:
                                mt = os.path.getmtime(fp)
                            except Exception:
                                continue
                            prev = last.get(fp)
                            last[fp] = mt
                            if prev is None or mt > prev: self._mark(fp)
                time.sleep(self.poll_interval)
        self._observer = True
        t = threading.Thread(target=_loop, daemon=True); t.start()

    def start(self):
        try:
            import watchdog  # noqa
            self._start_watchdog()
            self._status("Folder watcher: watchdog backend")
        except Exception:
            self._start_poll()
            self._status("Folder watcher: polling backend")

    def stop(self):
        try:
            if self._timer: self._timer.cancel()
        except Exception: pass
        if hasattr(self._observer, "stop"):
            try: self._observer.stop(); self._observer.join(timeout=3)
            except Exception: pass
        self._observer = None


    def on_created(self, event):
        if isinstance(event, FileCreatedEvent) and not event.is_directory:
            self._enqueue(event.src_path)

    def on_moved(self, event):
        if isinstance(event, FileMovedEvent) and not event.is_directory:
            self._enqueue(event.dest_path)

    def _enqueue(self, path):
        try:
            p = os.path.abspath(path)
            name = os.path.basename(p)

            for pat in self.ignore_globs:
                if fnmatch(name.lower(), pat.lower()):
                    return

            if self.exts and not any(p.lower().endswith(e) for e in self.exts):
                return
            self._work.put(p, block=False)
            if self.status_cb: self.status_cb(f"Detected new file: {p}")
            if self.notify_cb: self.notify_cb("New file detected", p)
        except Exception:
            pass

    def _is_stable(self, p):
        try:
            st = os.stat(p)
            if st.st_size < self.min_bytes: return False
            last = self._seen.get(p)
            self._seen[p] = st.st_size
            return (last is not None and last == st.st_size)
        except Exception:
            return False

    def _drain(self):
        while True:
            try:
                p = self._work.get(timeout=0.5)
            except Exception:
                if not self._running: return
                continue

            t0 = time.time()
            stable = False
            while time.time() - t0 < max(3.0, self.stable_secs * 3):
                time.sleep(self.stable_secs)
                if self._is_stable(p):
                    stable = True
                    break
            if stable and os.path.isfile(p):
                try:
                    self.on_file_ready(p)
                except Exception:
                    LOG.exception("FolderWatcher callback failed for %s", p)


class DropZone(TkinterDnD.Tk):
    def __init__(self, file_callback):
        super().__init__()
        self.file_callback = file_callback
        self.withdraw()
        self.overrideredirect(True)
        self.geometry("1x1+10+10")
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self.handle_drop)
        self.after(1000, self.hide_near_tray)

    def handle_drop(self, event):
        files = self.tk.splitlist(event.data)
        for f in files:
            if os.path.isfile(f):
                self.file_callback(f)

    def hide_near_tray(self):
        if platform.system() == "Windows":
            screen_width = self.winfo_screenwidth()
            screen_height = self.winfo_screenheight()
            self.geometry(f"100x100+{screen_width - 120}+{screen_height - 140}")
        self.deiconify()




    def export_presets(self):
        
        fp = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON","*.json")])
        if not fp:
            return
        data = {"presets": PRESETS, "profiles": getattr(self, "saved_profiles", {})}
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.update_status(f"Presets exported to {fp}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export presets: {e}")

    def import_presets(self):
        
        fp = filedialog.askopenfilename(filetypes=[("JSON","*.json")])
        if not fp:
            return
        try:
            data = json.load(open(fp, "r", encoding="utf-8"))
            PRESETS.clear()
            PRESETS.update(data.get("presets", {}))
            if hasattr(self, "saved_profiles"):
                self.saved_profiles.clear()
                self.saved_profiles.update(data.get("profiles", {}))
            self.update_status("Presets imported.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to import presets: {e}")

    def toggle_theme(self):
        
        self.dark_mode.set(not self.dark_mode.get())
        self.apply_theme()
        self.save_settings()

    import colorsys
    APP_BG = "#0f1216"
    CARD_BG = "#161a20"
    FG      = "#E8EAED"
    FG_SUB  = "#A8B0BA"
    ACCENT  = "#7C5CFF"   # purple
    ACCENT_2= "#3DDC97"   # mint
    ERROR   = "#FF6B6B"
    WARN    = "#FFB020"

    def _hsl_shift(hex_color: str, h_delta=0.0, s_mul=1.0, l_mul=1.0) -> str:
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        h,l,s = colorsys.rgb_to_hls(r,g,b)
        h = (h + h_delta) % 1.0
        s = max(0.0, min(1.0, s * s_mul))
        l = max(0.0, min(1.0, l * l_mul))
        r,g,b = colorsys.hls_to_rgb(h,l,s)
        return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

    def apply_theme(style: ttk.Style):
        style.theme_use("clam")

        style.configure(
            "TNotebook.Tab",
            padding=(int(12*PAD), int(6*PAD)),
            foreground=FG,                     # high-contrast text on tabs
            background=_hsl_shift(APP_BG, l_mul=0.92),
            borderwidth=BORD
        )
        style.configure("TFrame", background=APP_BG)
        style.configure("Card.TFrame", background=CARD_BG)
        style.configure("TLabel", background=APP_BG, foreground=FG)
        style.configure("Sub.TLabel", background=APP_BG, foreground=FG_SUB)

        btn_bg  = _hsl_shift(ACCENT, l_mul=0.88)
        btn_bg2 = ACCENT
        style.configure("TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(int(12*PAD), int(8*PAD)), background=btn_bg, foreground="#ffffff",
            borderwidth=BORD, focusthickness=0)
        style.map("TButton",
            background=[("active", btn_bg2), ("disabled", "#2B2F36")],
            foreground=[("disabled", "#7a8088")])

        style.configure("Ghost.TButton",
            font=("Segoe UI", 10), padding=(int(10*PAD), int(6*PAD)), borderwidth=BORD,
            background=CARD_BG, foreground=FG,
            bordercolor="#2A2E34", relief="flat")
        style.map("Ghost.TButton",
            background=[("active", _hsl_shift(CARD_BG, l_mul=1.06))])

        style.configure("TEntry", fieldbackground=_hsl_shift(CARD_BG, l_mul=1.06),
                        bordercolor="#2A2E34", relief="flat", padding=int(6*PAD), borderwidth=BORD)
        style.configure("TCombobox", fieldbackground=_hsl_shift(CARD_BG, l_mul=1.06),
                        foreground=FG, background=CARD_BG)
        style.map("TCombobox",
            fieldbackground=[("readonly", _hsl_shift(CARD_BG, l_mul=1.02))])

        style.configure("Accent.Horizontal.TProgressbar",
            troughcolor=CARD_BG, background=ACCENT, bordercolor=CARD_BG,
            lightcolor=ACCENT, darkcolor=_hsl_shift(ACCENT, l_mul=0.8))

        style.configure("Card.TLabelframe",
                        background=CARD_BG,
                        borderwidth=0,   # removes that white line
                        relief="flat")   # ensure no groove outline
        style.configure("Card.TLabelframe.Label",
                        background=CARD_BG,
                        foreground=FG_SUB,
                        padding=(6,0))

        style.configure("TCheckbutton", background=APP_BG, foreground=FG)
        style.map("TCheckbutton", foreground=[("disabled", FG_SUB)])

        entry_bg    = _hsl_shift(CARD_BG, l_mul=1.06)
        entry_bg_ro = _hsl_shift(CARD_BG, l_mul=1.02)
        entry_fg_dis = "#7a8088"

        style.configure("Dark.TEntry",
            fieldbackground=entry_bg,
            foreground=FG,
            padding=int(6*PAD), borderwidth=BORD,
            bordercolor="#2A2E34",
            relief="flat")
        style.map("Dark.TEntry",
            fieldbackground=[("focus", entry_bg), ("!focus", entry_bg), ("disabled", _hsl_shift(CARD_BG, l_mul=1.0))],
            foreground=[("disabled", entry_fg_dis)])

        style.configure("Dark.TCombobox",
            fieldbackground=entry_bg,
            background=CARD_BG,
            foreground=FG,
            padding=int(4*PAD), borderwidth=BORD,
            bordercolor="#2A2E34",
            relief="flat")
        style.map("Dark.TCombobox",
            fieldbackground=[("readonly", entry_bg_ro), ("!readonly", entry_bg)],
            foreground=[("disabled", entry_fg_dis)])

        style.configure("Card.TLabelframe",
                        background=CARD_BG,
                        borderwidth=BORD,
                        relief="flat")
        style.configure("Card.TLabelframe.Label",
                        background=CARD_BG,
                        foreground=FG_SUB,
                        padding=(int(6*PAD), 0))

        style.layout("Card.TLabelframe", [
            ('Labelframe.padding', {'sticky': 'nswe', 'children': [
                ('Labelframe.label',  {'side': 'top', 'sticky': ''}),
                ('Labelframe.client', {'sticky': 'nswe'})
            ]})
        ])

        style.configure("Dark.TSeparator", background=_hsl_shift(CARD_BG, l_mul=1.02))






    def open_language_manager(self):
        import tkinter as tk
        from tkinter import ttk, messagebox

        _load_lang_packs()

        win = tk.Toplevel(self.root)
        win.title("Language Manager")
        win.geometry("720x420")
        win.transient(self.root)

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        cols = ("code", "name", "coverage", "source")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        tree.heading("code", text="Code")
        tree.heading("name", text="Display Name")
        tree.heading("coverage", text="Coverage")
        tree.heading("source", text="Source")
        tree.column("code", width=90, anchor="w")
        tree.column("name", width=300, anchor="w")
        tree.column("coverage", width=120, anchor="w")
        tree.column("source", width=120, anchor="w")
        tree.pack(fill="both", expand=True)

        hint = tk.Label(
            frame,
            text="Coverage is relative to English base keys. Edit user_settings/i18n/<code>.json and click Reload.",
            anchor="w",
            justify="left",
        )
        hint.pack(fill="x", pady=(8, 4))

        btns = tk.Frame(frame)
        btns.pack(fill="x", pady=(4, 0))

        def _refresh_rows():
            _load_lang_packs()
            tree.delete(*tree.get_children())
            for code in _language_codes_ordered():
                tree.insert(
                    "",
                    "end",
                    values=(
                        code,
                        LANG_DISPLAY.get(code, LANG_CODE_NAME.get(code, code)),
                        f"{int(LANG_COVERAGE.get(code, 0))}%",
                        LANG_SOURCE.get(code, "fallback"),
                    ),
                )

        def _use_selected():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Language Manager", "Select a language first.")
                return
            vals = tree.item(sel[0], "values")
            if not vals:
                return
            code = str(vals[0])
            self.lang_var.set(code)
            self._on_language_change()
            try:
                win.destroy()
            except Exception:
                pass

        ttk.Button(btns, text="Reload", command=_refresh_rows).pack(side="left")
        ttk.Button(btns, text="Export Templates", command=lambda: (_export_lang_templates(), _refresh_rows())).pack(side="left", padx=6)
        ttk.Button(btns, text="Open i18n Folder", command=lambda: _open_folder(_i18n_dir())).pack(side="left", padx=6)
        ttk.Button(btns, text="Use Selected", command=_use_selected).pack(side="right")

        _refresh_rows()
    def show_dashboard(self):
        
        win = Toplevel(self.root)
        win.title("Dashboard")
        total = len(self.stats_list)
        ratios = []
        for s in self.stats_list:
            orig = s.get("orig_size", 1)
            comp = s.get("compressed_size", 0)
            ratios.append(comp / orig if orig else 0)
        avg_ratio = sum(ratios) / total if total else 0
        Label(win, text=f"Files processed: {total}").pack(padx=10, pady=5)
        Label(win, text=f"Avg compression ratio: {avg_ratio:.2f}").pack(padx=10, pady=5)
        columns = ("File","Ratio")
        tree = ttk.Treeview(win, columns=columns, show="headings")
        for col in columns:
            tree.heading(col, text=col)
        for s in self.stats_list[-5:]:
            f = os.path.basename(s.get("filepath",""))
            orig = s.get("orig_size",1)
            comp = s.get("compressed_size",0)
            ratio = comp/orig if orig else 0
            tree.insert("", "end", values=(f, f"{ratio:.2f}"))
        tree.pack(fill="both", expand=True, padx=10, pady=10)

    def main():

        setup_logging()

        if TkinterDnD:
            root = TkinterDnD.Tk()
        else:
            root = tk.Tk()

        app = CompressorGUI(root)
        root.mainloop()

import http.server, socketserver, json as _json

class _AgentState:
    paused = False
    cpu_cap = 85  # percent
    queue = []
    lock = _th.Lock()

def _agent_should_pause():
    try:
        return _AgentState.paused or psutil.cpu_percent(interval=1) > _AgentState.cpu_cap
    except Exception:
        return _AgentState.paused

class _SimpleHandler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = _json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/state"):
            with _AgentState.lock:
                self._send(200, {"paused": _AgentState.paused, "cpu_cap": _AgentState.cpu_cap, "queued": len(_AgentState.queue)})
        else:
            self._send(404, {"error":"not found"})

    def do_POST(self):
        if self.path.startswith("/pause"):
            _AgentState.paused = True
            self._send(200, {"ok": True, "paused": True})
        elif self.path.startswith("/resume"):
            _AgentState.paused = False
            self._send(200, {"ok": True, "paused": False})
        elif self.path.startswith("/cap/"):
            try:
                cap = int(self.path.split("/cap/")[1])
                _AgentState.cpu_cap = max(10, min(99, cap))
                self._send(200, {"ok":True, "cpu_cap":_AgentState.cpu_cap})
            except Exception:
                self._send(400, {"ok":False})
        else:
            self._send(404, {"error":"not found"})

def _agent_worker(out_dir, target_mb, adv_opts, webhook):
    while True:
        with _AgentState.lock:
            path = _AgentState.queue.pop(0) if _AgentState.queue else None
        if not path:
            time.sleep(0.5); continue

        while _agent_should_pause():
            time.sleep(2)
        try:
            media = get_media_type(path)
            if media == "video":
                auto_compress(path, out_dir, lambda m, level="INFO": None, int(target_mb) * 1024 * 1024, webhook, {**(adv_opts or {}), "_target_is_bytes": True}, lambda: False)
            elif media in ("audio","image"):
                auto_compress(path, out_dir, lambda m, level="INFO": None, int(target_mb) * 1024 * 1024, webhook, {**(adv_opts or {}), "_target_is_bytes": True}, lambda: False)

        except Exception as e:

            pass

class _AgentWatchHandler(FileSystemEventHandler):
    def __init__(self, out_dir, target_mb, adv_opts, webhook):
        self.out_dir = out_dir
        self.target_mb = target_mb
        self.adv_opts = adv_opts
        self.webhook = webhook
    def on_created(self, event):
        if not event.is_directory and os.path.isfile(event.src_path):
            with _AgentState.lock:
                _AgentState.queue.append(event.src_path)

def run_agent(watch_dir, out_dir, target_mb=10, webhook=""):

    adv_opts = {
        "encoder": "x264", "two_pass": False, "iterative": False,
        "manual_crf":"", "manual_bitrate":"", "output_prefix":"", "output_suffix":"_discord_ready",
        "audio_format":"aac", "image_format":"jpg", "concurrent": False,
        "auto_output_folder": False, "guetzli": False, "pngopt": False, "auto_jpeg": False,
    }

    os.makedirs(out_dir, exist_ok=True)

    _th.Thread(target=_agent_worker, args=(out_dir, target_mb, adv_opts, webhook), daemon=True).start()

    obs = Observer()
    obs.schedule(_AgentWatchHandler(out_dir, target_mb, adv_opts, webhook), watch_dir, recursive=False)
    obs.start()

    with socketserver.TCPServer(("127.0.0.1", 8765), _SimpleHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            obs.stop(); obs.join()


def _cli_status(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")

def _cli_cancel():

    return False

def _ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p

def _infer_save_dir(out_arg, first_input):
    if out_arg:
        return _ensure_dir(os.path.abspath(os.path.expanduser(out_arg)))

    base = os.path.dirname(os.path.abspath(first_input)) or "."
    return base

def _build_adv_from_args(args) -> dict:
    adv = dict(ADVANCED_DEFAULTS)  # start from your defaults
    if args.encoder:
        adv["encoder"] = args.encoder
    if args.hwaccel:
        adv["hwaccel"] = args.hwaccel
    if args.two_pass:
        adv["two_pass"] = True
    if args.force_two_pass:
        adv["two_pass_forced"] = True
    if args.crf is not None:
        adv["manual_crf"] = str(args.crf)
    if args.bitrate is not None:
        adv["manual_bitrate"] = str(int(args.bitrate))
    if args.audio_format:
        adv["audio_format"] = args.audio_format
    if args.image_format:
        adv["image_format"] = args.image_format
    if args.prefix is not None:
        adv["output_prefix"] = args.prefix
    if args.suffix is not None:
        adv["output_suffix"] = args.suffix

    # New pipeline options.
    adv["quality_mode"] = getattr(args, "quality", "max")
    if getattr(args, "no_auto_codec", False):
        adv["auto_codec"] = False
    if getattr(args, "no_scene_zones", False):
        adv["scene_zones"] = False
    if getattr(args, "no_hw_decode", False):
        adv["hw_decode"] = False
    if getattr(args, "no_measure", False):
        adv["measure_quality"] = False
    if int(getattr(args, "min_vmaf", 0) or 0) > 0:
        adv["min_vmaf"] = int(args.min_vmaf)
        adv["measure_quality"] = True

    if getattr(args, "no_preproc", False):
        adv["smart_preproc"] = False
    if getattr(args, "no_learned_seed", False):
        adv["learned_seed"] = False
    if getattr(args, "no_preflight", False):
        adv["preflight_advice"] = False
    if getattr(args, "no_ceiling_downscale", False):
        adv["ceiling_downscale_retry"] = False
    if getattr(args, "film_grain", None):
        adv["film_grain"] = str(args.film_grain)
    if getattr(args, "trim", None):
        adv["trim_range"] = str(args.trim)
    if getattr(args, "trim_fade", False):
        adv["trim_fade"] = True
    if getattr(args, "spotlight", None):
        adv["spotlight_range"] = str(args.spotlight)

    # Batch-1 quality-of-life options.
    if getattr(args, "discord_compat", False):
        adv["discord_compat"] = True
    _atm = str(getattr(args, "audio_track_mode", "") or "").strip().lower()
    if _atm in ("keepfirst", "mix"):
        adv["audio_track_mode"] = _atm
    if getattr(args, "no_lyrics", False):
        adv["embed_lyrics"] = False
    if getattr(args, "clipboard", False):
        adv["copy_to_clipboard"] = True

    return adv

def _expand_inputs(inputs: list[str]) -> list[str]:
    expanded = []
    for item in inputs:
        item = os.path.expanduser(item)

        if any(ch in item for ch in "*?[]"):
            expanded.extend(glob.glob(item, recursive=True))
        elif os.path.isdir(item):

            for root, _, files in os.walk(item):
                for f in files:
                    p = os.path.join(root, f)
                    if get_media_type(p) in {"video", "audio", "image"}:
                        expanded.append(p)
        else:
            expanded.append(item)

    seen = set()
    result = []
    for p in expanded:
        ap = os.path.abspath(p)
        if ap not in seen and os.path.exists(ap):
            seen.add(ap)
            result.append(ap)
    return result

def _print_summary(stats_list):
    if not stats_list:
        return
    print("\n=== Summary ===")
    for s in stats_list:
        try:
            in_sz = s.get("original_size")
            out_sz = s.get("compressed_size")
            ratio = (out_sz / in_sz) if in_sz else 0
            _tag = " [Dedup-Reused]" if s.get("reused_duplicate_of") else ""
            print(f"- {s.get('filename') or ''}{_tag}  "
                  f"{format_bytes(in_sz)} -> {format_bytes(out_sz)}  "
                  f"({ratio*100:.1f}% of original)")
        except Exception:
            pass

def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="BitCrusher",
        description="Fast media compression (video/audio/image) — GUI by default, CLI with args.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("inputs", nargs="*", help="Files/folders/globs to compress (if empty, GUI launches)")
    p.add_argument("-o", "--output", help="Output directory (default: alongside first input)")
    p.add_argument("-t", "--target-size", type=float, default=10.0,
                   help="Target size in MB (applies to each item)")
    p.add_argument("--encoder", choices=["x264","x265","av1","svt-av1","aom-av1","vp9","vvc",
                                         "h264_nvenc","hevc_nvenc","av1_nvenc",
                                         "h264_qsv","hevc_qsv","av1_qsv",
                                         "h264_amf","hevc_amf","av1_amf"],
                   help="Video encoder to use (default: auto-picked)")
    p.add_argument("--quality", choices=["fast","balanced","max"], default="max",
                   help="Quality mode: fast (quick), balanced, or max (pack the size cap, measured)")
    p.add_argument("--no-auto-codec", action="store_true",
                   help="Disable the VMAF-measured codec auto-pick (x265 vs AV1)")
    p.add_argument("--dedup-scan", action="store_true",
                   help="Before encoding, scan inputs for byte-identical duplicates "
                        "and offer to reuse an already-encoded output instead of "
                        "re-encoding confirmed matches (advisory-only, never auto-acts)")
    p.add_argument("--estimate", action="store_true",
                   help="Predict delivered size / VMAF / worst-scene / encode time from the "
                        "outcome ledger for each input WITHOUT encoding, then exit. "
                        "Estimates one codec (--encoder) or x264/x265/av1 when unset.")
    p.add_argument("--learning-trend", action="store_true",
                   help="Print whether the ledger's shadow predictors (size-deviation, "
                        "probe rate fit, advisor quality model) are getting more accurate "
                        "over time, then exit. No input files required.")
    p.add_argument("--ledger-audit", action="store_true",
                   help="Print anomalous encodes (where every active predictor missed) "
                        "and a VMAF-scale population report, then exit. No input files required.")
    p.add_argument("--min-vmaf", type=int, default=0,
                   help="Spend spare budget until output reaches this VMAF (0 = off)")
    p.add_argument("--no-measure", action="store_true",
                   help="Skip the VMAF quality measurement of the final output")
    p.add_argument("--vmaf-model", default=None,
                   help="VMAF model: auto (v1 if available, else v0.6.1) | v1 | neg | 4k | default | "
                        "a raw 'version=…'/'path=…' value. Non-default models shift the score scale.")
    p.add_argument("--vmaf-objective", choices=["window", "p5", "p1", "harmonic", "mean"], default=None,
                   help="Which VMAF number the min-VMAF floor optimizes: window=worst ~2s scene "
                        "(default, beats the average trap) | p5/p1 percentile | harmonic | mean")
    p.add_argument("--no-preproc", action="store_true",
                   help="Disable artifact-aware preprocessing (validated deband/deblock/denoise prefilters)")
    p.add_argument("--no-learned-seed", action="store_true",
                   help="Disable learned first-attempt bitrate seeding from the outcome ledger")
    p.add_argument("--no-preflight", action="store_true",
                   help="Disable the advisory pre-flight quality/size guardrail (ledger-based)")
    p.add_argument("--no-ceiling-downscale", action="store_true",
                   help="Disable the last-resort downscale-and-retry when the size cap "
                        "cannot be met at native resolution (an oversized file may then ship)")
    p.add_argument("--film-grain", choices=["auto", "off", "force"], default=None,
                   help="AV1 film-grain synthesis: auto (probe & enable on grainy sources, "
                        "default), off, or force. Denoises grain before encoding and re-adds it "
                        "on playback — better picture at the same size on film grain / old cartoons.")
    p.add_argument("--trim", default=None, metavar="START-END",
                   help="Compress only this range (e.g. 1:42-2:05 or 12-31). The whole size "
                        "budget goes to the kept range; the source file is never modified.")
    p.add_argument("--trim-fade", action="store_true",
                   help="Frame-exact trim with 0.5s audio+video fades at both ends "
                        "(default trim is a zero-loss stream copy snapped to a keyframe)")
    p.add_argument("--suggest-trim", nargs="?", const=20.0, type=float, default=None,
                   metavar="CLIP_SECONDS",
                   help="Analyze audio energy and print candidate trim ranges (default "
                        "20s clips), then exit without compressing. Mic tracks weigh extra.")
    p.add_argument("--spotlight", default=None, metavar="START-END",
                   help="Keep the whole video but boost quality in this range via an "
                        "x264/x265 rate zone; the rest pays for it under the same cap.")
    p.add_argument("--no-scene-zones", action="store_true",
                   help="Disable per-scene bitrate zones (x264/x265)")
    p.add_argument("--no-hw-decode", action="store_true",
                   help="Disable GPU-accelerated decode of the source")
    p.add_argument("--hwaccel", choices=["CPU","NVENC","QSV","AMF"], default="CPU",
                   help="Hardware acceleration hint (for GPU pipelines)")
    p.add_argument("--two-pass", action="store_true", help="Allow two-pass when beneficial")
    p.add_argument("--force-two-pass", action="store_true", help="Force two-pass regardless of heuristics")
    p.add_argument("--crf", type=int, help="Manual CRF (overrides prediction for video)")
    p.add_argument("--bitrate", type=int, help="Manual video bitrate in bps (forces ABR; may trigger 2-pass)")
    p.add_argument("--audio-format", choices=["opus","aac","m4a","mp3"], help="Preferred audio codec (m4a = AAC in an MP4 container, which keeps album art)")
    p.add_argument("--image-format", choices=["jpg","png","webp"], help="Preferred image output format")
    # nargs="?"/const="" so a bare `--suffix` means "no suffix" — PowerShell
    # silently drops empty-string args (`--suffix ""`), which would otherwise
    # error as "expected one argument".
    p.add_argument("--prefix", nargs="?", const="", default="", help="Output filename prefix")
    p.add_argument("--suffix", nargs="?", const="", default="_discord_ready", help="Output filename suffix (bare flag = none)")
    p.add_argument("--webhook", help="Webhook URL to POST results")
    p.add_argument("--discord-compat", action="store_true",
                   help="Force H.264 + AAC (MP4) for guaranteed inline Discord playback (size cost)")
    p.add_argument("--audio-track-mode", choices=["keepfirst", "mix"], default=None,
                   help="Multi-track audio: keep first track (default) or mix all tracks (amix)")
    p.add_argument("--no-lyrics", action="store_true",
                   help="Do not embed a sibling .lrc lyric file into audio output tags")
    p.add_argument("--clipboard", action="store_true",
                   help="Copy each finished file to the Windows clipboard (Ctrl+V into Discord)")
    p.add_argument("--enqueue", nargs="+", metavar="FILE",
                   help="Hand file(s) to a running BitCrusher window (used by 'Send to'); "
                        "launches the app if none is running")
    p.add_argument("--register-send-to", action="store_true",
                   help="Install the Explorer 'Send to > BitCrusher' shortcut, then exit")
    p.add_argument("--unregister-send-to", action="store_true",
                   help="Remove the Explorer 'Send to > BitCrusher' shortcut, then exit")
    p.add_argument("-j", "--jobs", type=int, default=1,
                   help="Encode this many files at once (parallel)")
    p.add_argument("-q", "--quiet", action="store_true", help="Less verbose CLI logging")
    p.add_argument("--version", action="store_true", help="Print version info and exit")
    return p

def cli_main():
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.version:
        print("BitCrusher CLI - powered by HandBrakeCLI/ffmpeg")
        return 0

    if getattr(args, "vmaf_model", None):
        set_vmaf_model_pref(args.vmaf_model)
    if getattr(args, "vmaf_objective", None):
        set_vmaf_objective_pref(args.vmaf_objective)

    # Explorer "Send to" integration management.
    if getattr(args, "register_send_to", False):
        ok, msg = register_send_to()
        print(msg)
        return 0 if ok else 1
    if getattr(args, "unregister_send_to", False):
        ok, msg = unregister_send_to()
        print(msg)
        return 0 if ok else 1

    if getattr(args, "learning_trend", False):
        from outcome_ledger import ledger_load as _ol_load
        from dashboard import build_trend_model as _ol_trend
        _stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
        _trend = _ol_trend(_ol_load(_stats_dir))
        _labels = {"ledger_dev": "Ledger size-deviation predictor",
                  "probe": "Probe rate-fit predictor",
                  "advisor": "AI-advisor quality predictor",
                  "retries_per_encode": "Retries per encode (flywheel speed)"}
        print("[Learning trend] (first-half vs second-half mean, chronological order)")
        for key, label in _labels.items():
            t = _trend.get(key) or {}
            n = t.get("n", 0)
            if not n:
                print(f"  {label}: no data yet")
                continue
            fm, sm = t.get("first_half_mean"), t.get("second_half_mean")
            verdict = "improving" if t.get("improving") else "not improving (or too little data to tell)"
            print(f"  {label}: n={n}  first-half={fm}  second-half={sm}  -> {verdict}")
        return 0

    if getattr(args, "ledger_audit", False):
        from outcome_ledger import detect_anomalies as _ol_anom, audit_vmaf_scale as _ol_scale
        _stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
        _anoms = _ol_anom(_stats_dir)
        print(f"[Ledger audit] {len(_anoms)} anomalous record(s) (every active predictor missed):")
        for a in _anoms[-20:]:
            print(f"  {a['ts']}  {a['input']}  missed_by={a['missed_by']}")
        _scale = _ol_scale(_stats_dir)
        print(f"[Ledger audit] VMAF-scale population: {_scale['counts']}")
        print(f"  note: {_scale['note']}")
        return 0

    # Send-To / single-instance hand-off: try to give the file(s) to a running
    # window; if none is listening, stash them and fall through to launch the GUI.
    if getattr(args, "enqueue", None):
        _paths = _expand_inputs(args.enqueue)
        if not _paths:
            print("Nothing to enqueue (no existing files matched).")
            return 1
        if _bc_ipc_send(_paths):
            print(f"Sent {len(_paths)} file(s) to the running BitCrusher window.")
            return 0
        # No running instance — launch the GUI with these queued.
        globals()["_BC_STARTUP_FILES"] = list(_paths)
        return None

    if not args.inputs:

        return None

    files = _expand_inputs(args.inputs)
    if not files:
        print("No matching files found.")
        return 1

    # Analysis-only mode: print candidate trim ranges and exit (no compression).
    if getattr(args, "suggest_trim", None) is not None:
        _clip_s = max(4.0, float(args.suggest_trim))
        _any = False
        for src in files:
            print(f"{os.path.basename(src)}:")
            cands = suggest_trim_ranges(src, clip_seconds=_clip_s)
            if not cands:
                print("  no clear audio peaks found - set the trim manually (--trim START-END)")
                continue
            _any = True
            for i, c in enumerate(cands, 1):
                print(f"  {i}) --trim {c['range']}   ({c['why']}, score {c['score']})")
        return 0 if _any else 1

    # Prediction-only mode: estimate size/VMAF/time from the ledger, no encode.
    if getattr(args, "estimate", False):
        from outcome_ledger import estimate_encode as _ol_est
        from outcome_ledger import lookup_by_signature as _ol_sig_lookup
        from outcome_ledger import nearest_neighbors as _ol_neighbors
        from ml_heuristics import _bc_file_sig as _ol_sig_fn
        _model = resolve_vmaf_model() or "version=vmaf_v0.6.1"
        _tgt_bytes = max(1, int(round(args.target_size))) * 1024 * 1024
        _encs = ([args.encoder] if getattr(args, "encoder", None)
                 else ["x264", "x265", "av1"])
        _stats_dir = os.path.join(USER_SETTINGS_DIR, "stats")
        _any = False
        for src in files:
            feats = extract_media_features(src) or {}
            w = int(feats.get("width") or 0)
            h = int(feats.get("height") or 0)
            fps = float(feats.get("fps") or 30.0)
            dur = float(feats.get("duration") or 0.0)
            if not (w and h and dur):
                print(f"{os.path.basename(src)}: not a video / probe failed - estimate unavailable")
                continue
            _aud = 128_000
            v_bps = max(24_000, int((_tgt_bytes * 8.0 / max(1.0, dur) - _aud) * 0.94))
            print(f"{os.path.basename(src)}  ({w}x{h} {fps:.3g}fps {dur:.0f}s)"
                  f"  ->  target {args.target_size:g} MB")
            try:
                _sig = _ol_sig_fn(src)
                _known = _ol_sig_lookup(_stats_dir, _sig, _tgt_bytes,
                                        encoder=(args.encoder if getattr(args, "encoder", None) else None))
                if _known:
                    print(f"   [known-good] this exact file was already encoded near this target: "
                          f"{_known['encoder']} @ {_known['v_bps']} bps -> {_known['size']} bytes "
                          f"({_known['ts']})")
            except Exception:
                pass
            for enc in _encs:
                est = _ol_est(_stats_dir, feats, enc, w, h, fps, v_bps,
                              _tgt_bytes, dur, vmaf_model=_model)
                fam = est["encoder"]
                if est["n"] < 1:
                    print(f"   {fam:5}  no comparable history yet")
                    continue
                _any = True
                sz = est["size_bytes"] / 1048576.0
                vm = f"VMAF ~{est['mean']:.1f}" if est["mean"] is not None else "VMAF n/a"
                wo = f"worst ~{est['worst']:.0f}" if est["worst"] is not None else "worst n/a"
                tm = f"~{est['seconds']:.0f}s" if est["seconds"] is not None else "time n/a"
                print(f"   {fam:5}  ~{sz:5.2f} MB  {vm:11}  {wo:10}  enc {tm:8}  (n={est['n']})")
                try:
                    _nn = _ol_neighbors(_stats_dir, feats, enc, w, h, fps, v_bps, k=3)
                    for _nb in _nn:
                        print(f"           - similar: {_nb['input']}  "
                              f"size_ratio x{_nb['size_ratio']:.2f}  (dist {_nb['dist']:.2f}, {_nb['ts']})")
                except Exception:
                    pass
        return 0 if _any else 1

    # Batch exact-match dedup: advisory-only, explicit-request (--dedup-scan)
    # only -- never runs otherwise, so plain single-file/batch encodes pay zero
    # hashing cost. _confirmed_reuse/_dedup_canonical_outputs stay empty dicts
    # when the flag is off, so _one()'s lookups below are always safe no-ops.
    _confirmed_reuse: dict = {}
    _dedup_canonical_outputs: dict = {}
    if getattr(args, "dedup_scan", False):
        from ml_heuristics import build_batch_dedup_index as _ol_dedup_index
        groups = _ol_dedup_index(files)
        if groups:
            _interactive = sys.stdin.isatty() and sys.stdout.isatty()
            for grp in groups:
                canonical, dupes = grp[0], grp[1:]
                print(f"[Dedup] {len(grp)} files look byte-identical:")
                print(f"  keep + encode: {canonical}")
                for d in dupes:
                    print(f"  duplicate:     {d}")
                if not _interactive:
                    print("  (non-interactive session -- skipping, encoding all normally)")
                    continue
                try:
                    ans = input("  Reuse the canonical encode for the duplicate(s) above? [y/N] ").strip().lower()
                except Exception:
                    ans = "n"
                if ans == "y":
                    for d in dupes:
                        _confirmed_reuse[d] = canonical
                else:
                    print("  Skipped -- all files in this group will encode independently.")
        else:
            print("[Dedup] No byte-identical duplicates found in this batch.")
        # Confirmed duplicates must dispatch after their canonical so the
        # canonical's output_path is already recorded in _dedup_canonical_outputs
        # by the time _one() looks it up (see _one below). Concurrent --jobs>1
        # doesn't guarantee this ordering; _one() falls back to a normal encode
        # if the canonical isn't done yet, so correctness never depends on it.
        if _confirmed_reuse:
            files = [f for f in files if f not in _confirmed_reuse] + \
                    [f for f in files if f in _confirmed_reuse]

    out_dir = _infer_save_dir(args.output, files[0])
    adv = _build_adv_from_args(args)
    target_mb = max(1, int(round(args.target_size)))
    stats_all = []

    if args.quiet:
        def status_cb(msg, level="INFO"):
            if level in ("ERROR", "CRITICAL", "WARNING"):
                _cli_status(msg, level)
    else:
        status_cb = _cli_status

    # Live one-line progress (only when not quiet and stdout is a terminal).
    _show_progress = (not args.quiet) and sys.stdout.isatty()

    def _make_progress_cb(label):
        state = {"last": -1}
        def _cb(_job_id, ev):
            if not _show_progress:
                return
            try:
                pct = int(ev.get("pct") or 0)
                stage = str(ev.get("stage") or "")
                if pct == state["last"]:
                    return
                state["last"] = pct
                eta = ev.get("eta_s")
                eta_s = f" ETA ~{int(eta)}s" if eta else ""
                sys.stdout.write(f"\r  {label}: {stage} {pct:3d}%{eta_s}      ")
                sys.stdout.flush()
            except Exception:
                pass
        return _cb

    def _one(src):
        name = os.path.basename(src)
        _canon_src = _confirmed_reuse.get(src)
        if _canon_src:
            _canon = _dedup_canonical_outputs.get(_canon_src)
            # No per-file settings variation exists in CLI mode (adv/target_mb
            # are uniform across the whole invocation), so a settings-compat
            # check would always trivially pass -- unlike the GUI path, which
            # does need one (watcher-rule per-file overrides).
            if _canon and os.path.isfile(_canon):
                try:
                    from ml_heuristics import _bc_content_hash as _dedup_hash
                    # TOCTOU guard: re-verify content now, not just at scan time.
                    if _dedup_hash(src) == _dedup_hash(_canon_src):
                        _dup_out = _bc_build_output_path(src, out_dir, adv,
                                                         default_ext=os.path.splitext(_canon)[1].lstrip("."))
                        _dup_out = _dedup_safe_output_path(_dup_out, _canon)
                        shutil.copyfile(_canon, _dup_out)
                        _cli_status(f"[Dedup] Reused canonical output for {name} "
                                   f"(duplicate of {os.path.basename(_canon_src)})")
                        return {"filename": name, "original_size": os.path.getsize(src),
                               "compressed_size": os.path.getsize(_dup_out),
                               "output_path": _dup_out, "reused_duplicate_of": _canon_src}
                    else:
                        _cli_status(f"[Dedup] {name} no longer matches its canonical "
                                   f"(changed since scan) - encoding normally.")
                except Exception as _dedup_e:
                    _cli_status(f"[Dedup] Reuse failed for {name} ({_dedup_e}) - "
                               f"encoding normally instead.", level="WARN")
                    # fall through to a normal encode
        job_adv = dict(adv)
        job_adv["job_id"] = src
        job_adv["progress_cb"] = _make_progress_cb(name)
        s = auto_compress(
            input_path=src,
            save_path=out_dir,
            status_callback=status_cb,
            target_size_mb=target_mb,
            webhook_url=(args.webhook or ""),
            advanced_options=job_adv,
            cancel_callback=_cli_cancel,
        ) or {}
        if _show_progress:
            sys.stdout.write("\n")
        s.setdefault("filename", name)
        try:
            s.setdefault("original_size", os.path.getsize(src))
        except Exception:
            pass
        # Prefer the real output path the pipeline reports; only fall back to a
        # filesystem check if it's missing.
        outp = s.get("output_path") or s.get("out_path") or s.get("output")
        if (not s.get("compressed_size")) and outp and os.path.exists(outp):
            try:
                s["compressed_size"] = os.path.getsize(outp)
            except Exception:
                pass
        if outp:
            _dedup_canonical_outputs[src] = outp
        if s.get("ceiling_exceeded"):
            status_cb(f"[FAIL] {name} - CEILING EXCEEDED: {s.get('compressed_size')} bytes vs "
                      f"target {int(target_mb * 1024 * 1024)} bytes.", level="ERROR")
        # Copy-result-to-clipboard (CF_HDROP) when requested (--clipboard).
        try:
            if bool(job_adv.get("copy_to_clipboard")) and outp and os.path.isfile(outp):
                if set_clipboard_files([outp]):
                    _cli_status(f"Copied to clipboard: {os.path.basename(outp)}")
        except Exception:
            pass
        return s

    jobs = max(1, int(getattr(args, "jobs", 1) or 1))
    if jobs > 1 and len(files) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        _cli_status(f"Encoding {len(files)} files, {jobs} at a time...")
        with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="bc_cli") as pool:
            futs = {pool.submit(_one, s): s for s in files}
            for fut in as_completed(futs):
                try:
                    stats_all.append(fut.result())
                except Exception as e:
                    status_cb(f"[FAIL] {futs[fut]} - {e}", level="ERROR")
    else:
        for src in files:
            try:
                stats_all.append(_one(src))
            except Exception as e:
                status_cb(f"[FAIL] {src} - {e}", level="ERROR")

    _print_summary(stats_all)
    _n_ok = sum(1 for s in stats_all if s.get("compressed_size"))
    _n_over = sum(1 for s in stats_all if s.get("ceiling_exceeded"))
    if _n_over:
        _cli_status(f"{_n_over} of {len(stats_all)} file(s) exceeded the size ceiling.", "ERROR")
    return 0 if (_n_ok and not _n_over) else 1


# ---------------------------------------------------------------------------
# Folder-watcher method repair.
# start/stop/toggle_folder_watcher were lost to an indentation slip:
# start_folder_watcher became a module-level function (and referenced a
# non-existent self.file_callback), while stop/toggle landed inside the
# FolderWatcher class — so NONE were CompressorGUI methods. The "Enable watcher"
# checkbox (getattr(self,"toggle_watch_folder",lambda:None)) was therefore a
# silent no-op and every self.start_folder_watcher() call raised AttributeError.
# Bind correct versions that drive the already-wired self.watcher (its
# on_file_ready -> _enqueue_from_watcher path works).
def _cgui_start_folder_watcher(self):
    import os
    folder = (self.watch_folder.get() if hasattr(self, "watch_folder") else "").strip()
    if not folder or not os.path.isdir(folder):
        self.update_status("Watch folder is not a valid directory.", level="WARNING")
        return
    try:
        if getattr(self.watcher, "_observer", None):   # don't stack observers
            self.watcher.stop()
    except Exception:
        pass
    try:
        self.watcher.add_path(folder)
        self.watcher.start()
        try:
            self.settings["watch_enabled"] = True
        except Exception:
            pass
        self.update_status(f"Watching folder: {folder}")
    except Exception as e:
        self.update_status(f"Failed to start folder watcher: {e}", level="ERROR")


def _cgui_stop_folder_watcher(self):
    try:
        if getattr(self, "watcher", None):
            self.watcher.stop()
        try:
            self.settings["watch_enabled"] = False
        except Exception:
            pass
        self.update_status("Folder watcher stopped.")
    except Exception:
        pass


def _cgui_toggle_watch_folder(self):
    import os
    from tkinter import messagebox
    try:
        on = bool(self.watch_var.get())
    except Exception:
        on = False
    if on:
        folder = (self.watch_folder.get() if hasattr(self, "watch_folder") else "").strip()
        if not folder or not os.path.isdir(folder):
            try:
                messagebox.showerror("Watch folder", "Please choose a valid folder to watch.")
            except Exception:
                pass
            try:
                self.watch_var.set(False)
            except Exception:
                pass
            return
        self.start_folder_watcher()
    else:
        self.stop_folder_watcher()


CompressorGUI.start_folder_watcher = _cgui_start_folder_watcher
CompressorGUI.stop_folder_watcher = _cgui_stop_folder_watcher
CompressorGUI.toggle_watch_folder = _cgui_toggle_watch_folder


if __name__ == "__main__":
    # CLI mode when arguments are given; GUI otherwise. cli_main() returns an
    # int exit code when it handled the run, or None to fall through to the GUI
    # (e.g. --version already printed, or no input files were supplied).
    if len(sys.argv) > 1:
        try:
            _rc = cli_main()
        except SystemExit:
            raise  # argparse --help / error already handled
        except KeyboardInterrupt:
            print("\nInterrupted.")
            sys.exit(130)
        except Exception as _e:
            import traceback
            print(f"[FATAL] CLI run failed: {_e}")
            traceback.print_exc()
            sys.exit(1)
        if _rc is not None:
            sys.exit(int(_rc))

    try:

        app = CompressorGUI()
        try:
            _BOOT_PHASE = False  # GUI constructed; suppress blocking crash popups from now on
        except Exception:
            pass

        if hasattr(app, "setup_ui"):
            app.setup_ui()
        if hasattr(app, "check_dependencies"):
            app.check_dependencies()

        # Files handed over by --enqueue when no instance was running yet.
        _startup_files = list(globals().get("_BC_STARTUP_FILES") or [])
        if _startup_files:
            def _enqueue_startup():
                for _p in _startup_files:
                    try:
                        app._ipc_enqueue(_p)
                    except Exception:
                        pass
            try:
                app.root.after(400, _enqueue_startup)
            except Exception:
                _enqueue_startup()

        app.root.mainloop()

    except Exception as e:
        import traceback
        print("[FATAL] Uncaught exception while launching GUI:", e)
        traceback.print_exc()















































