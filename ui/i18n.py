from __future__ import annotations

import json
import os
import subprocess
import sys

from support.text_utils import _mojibake_score, _normalize_text
from ui.ui_settings import _ui_json_path

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

        "btn.advanced":"Advanced…","btn.estimate":"Estimate","btn.refresh":"Refresh",
        "btn.open_visual_compare":"Open Visual Compare",
        "lbl.quality":"Quality:","lbl.display":"Display","lbl.mode":"Mode:","lbl.activity":"Activity",
        "qmode.fast":"Fast","qmode.balanced":"Balanced","qmode.max":"Max",
        "tab.progress":"Progress","tab.details":"Details","tab.stats":"Stats",
        "msg.compare_hint":"Compare the last output with the source.",

        "qmenu.set_encoder":"Set encoder for this file...","qmenu.set_container":"Set container/format for this file...",
        "qmenu.set_prefix":"Set prefix for this file...","qmenu.set_suffix":"Set suffix for this file...",
        "qmenu.trim":"Trim / clip range for this file...","qmenu.suggest_trim":"Suggest trim ranges (audio peaks)...",
        "qmenu.spotlight":"Spotlight quality range for this file...","qmenu.reset_overrides":"Reset per-file overrides",
        "qmenu.open_output":"Open Output Folder","qmenu.remove":"Remove from Queue",
        "menu.scan_dupes":"Scan Queue for Duplicates...","menu.advanced_options":"Advanced Options...",
        "menu.check_updates":"Check for Updates...","menu.add_sendto":"Add 'Send to BitCrusher' (right-click menu)",
        "menu.remove_sendto":"Remove 'Send to BitCrusher'",

        "dlg.watch_folder":"Watch Folder","dlg.trim_range":"Trim range","dlg.spotlight_range":"Spotlight range",
        "dlg.invalid_choice":"Invalid Choice","dlg.download_error":"Download Error","dlg.browse_error":"Browse error",
        "dlg.update_available":"Update available","dlg.check_updates":"Check for updates","dlg.send_to":"Send To",
        "dlg.language_manager":"Language Manager","dlg.warning":"Warning","dlg.deps_missing":"Dependencies Missing",
        "dlg.install_complete":"Install Complete","dlg.scan_dupes":"Scan for Duplicates",
        "dlg.export_sanitized":"Export Sanitized Logs","dlg.error":"Error",
        "msg.no_folder_selected":"No folder selected.","msg.enter_av":"Enter exactly: audio, video, or audio+video.",
        "msg.no_files_queue":"No files in queue.","msg.cancel_requested":"Queue cancel requested.",
        "msg.no_active_cancel":"No active compression to cancel.","msg.select_folder_first":"Please select a folder first.",
        "msg.queue_empty_add":"Queue is empty - add files first.","msg.no_dupes":"No byte-identical duplicates found in the queue.",
        "msg.select_rows_first":"Select one or more rows first.","msg.select_lang_first":"Select a language first.",
        "msg.choose_valid_folder":"Please choose a valid folder to watch.","msg.tools_installed":"Tools installed. Please restart the app.",
        "msg.update_unreachable":"Could not reach GitHub. Check your connection and try again.",
        "dlg.settings":"Settings","dlg.suggested_trim":"Suggested trim ranges",
        "dlg.visual_compare_fallback":"Visual Compare — Fallback","dlg.dashboard":"Dashboard",
        "dlg.dashboard_full":"BitCrusher - Dashboard","dlg.preflight":"Pre-flight estimate (from the ledger)",
        "dlg.scanning_dupes":"Scanning for Duplicates","dlg.dupes_found":"Duplicate Files Found",
        "dlg.batch_summary":"Batch Summary","dlg.advanced_options_win":"Advanced Options",

        "adv.sec.video":"Video","adv.sec.audio":"Audio","adv.sec.output":"Output & Naming",
        "adv.sec.images":"Images","adv.sec.misc":"Misc","adv.sec.watchrules":"Watcher Rules (folder watcher / Send-To)",
        "adv.encoder":"Encoder","adv.manual_crf":"Manual CRF","adv.manual_bitrate":"Manual Bitrate (bps)",
        "adv.two_pass":"Enable Two-pass","adv.iterative":"Iterative Size Targeting","adv.quality_mode":"Quality mode:",
        "adv.auto_codec":"Auto-pick best codec (VMAF probe vs AV1)","adv.scene_zones":"Scene-aware bitrate zones (x264/x265)",
        "adv.hw_decode":"GPU-accelerated decode of source","adv.measure":"Measure quality (VMAF) after encode",
        "adv.min_vmaf":"Min VMAF (0 = off)","adv.hardware":"Hardware",
        "adv.discord_compat":"Discord-compatible (force H.264 + AAC / MP4)",
        "adv.preproc":"Artifact-aware preprocessing (VMAF-validated deband/denoise)",
        "adv.learned_seed":"Learned first-attempt bitrate seeding (outcome ledger)",
        "adv.preflight":"Pre-flight quality/size advice (advisory only)",
        "adv.downscale_retry":"Downscale-and-retry if size cap can't be met at native resolution",
        "adv.audio_format":"Audio Format","adv.multitrack":"Multi-track audio","adv.keep_first":"Keep first",
        "adv.mix_all":"Mix all (amix)","adv.lyrics":"Embed sibling .lrc lyrics into tags",
        "adv.out_prefix":"Output Prefix","adv.out_suffix":"Output Suffix",
        "adv.clipboard":"Copy result to clipboard (Ctrl+V into Discord)",
        "adv.image_format":"Image Format","adv.png_opt":"PNG Optimize","adv.auto_jpeg":"Auto JPEG",
        "adv.concurrent":"Concurrent Compression","adv.auto_output":"Auto Create Output Folder",
        "adv.grain_filter":"Grain-aware denoise filter","adv.dino":"Hidden dino runner (Activity area)",
        "adv.wr.hint":"Leave any field blank to fall back to the global setting.","adv.wr.save_to":"Save watched files to",
        "adv.wr.target":"Default target size (MB)","adv.wr.encoder":"Default encoder",
        "adv.wr.larger":"If larger than (MB)","adv.wr.smaller":"If smaller than (MB)",
        "adv.wr.longer":"If longer than (min)","adv.wr.shorter":"If shorter than (min)",
        "btn.export_sanitized":"Export Sanitized Logs...","btn.ok":"OK","btn.cancel":"Cancel",
        "set.save_folder":"Default Save Folder:","set.auto_delete":"Auto-delete originals",
        "set.watch_folder":"Watch Folder:","set.enable_watch":"Enable Watch-Folder",

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


