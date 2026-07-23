"""Main-window view builder (C0 extraction from CompressorGUI.setup_ui).

The widget-building code below is moved VERBATIM from the monolith
(self -> gui rename only). All state, Tk variables and handlers stay on
the CompressorGUI instance - this module only constructs widgets.

Monolith globals (APP_BG, PRESETS, LANG, QueueTree, ...) are resolved
live from the host module at every build call via _sync_host_globals,
which matches the original semantics exactly: setup_ui always read
those globals at call time, and _rebuild_ui_for_language re-runs the
build after theme/language changes. Runtime re-theming still goes
through the host's _retint walk and never needs this module.
"""
from __future__ import annotations

import sys

_OWN = None


def _sync_host_globals(gui):
    """Mirror the host module's globals into this module (fresh each call)."""
    global _OWN
    g = globals()
    if _OWN is None:
        _OWN = set(g) | {"_OWN"}
    m = sys.modules[type(gui).__module__]
    for k, v in vars(m).items():
        if k.startswith("__") or k in _OWN:
            continue
        g[k] = v


def show_page(gui, name):
    """Raise the named sidebar page and reflect the selection in the nav.

    Nav highlight uses the Nav.TButton / NavActive.TButton ttk styles (defined
    in apply_theme) so it survives runtime retheming without any baked hex.
    """
    pages = getattr(gui, "_pages", None) or {}
    fr = pages.get(name)
    if fr is None:
        return
    try:
        fr.tkraise()
    except Exception:
        pass
    gui._active_page = name
    for n, btn in (getattr(gui, "_nav_buttons", None) or {}).items():
        try:
            btn.configure(style="NavActive.TButton" if n == name else "Nav.TButton")
        except Exception:
            pass


def build_main_view(gui):
    _sync_host_globals(gui)
    gui.queue_box = None
    import os
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter.scrolledtext import ScrolledText

    if not hasattr(gui, "root") or gui.root is None:
        gui.root = tk.Tk()
    gui.theme_var = tk.StringVar(value="Dark")   # default theme
    gui.lang_var = tk.StringVar(value=_load_language_choice("en"))
    _load_lang_packs()
    if gui.lang_var.get() not in LANG:
        gui.lang_var.set("en")
    gui.root.configure(bg="#14161A")             # initial bg; gets overridden by apply_theme
    gui.style = ttk.Style(gui.root)

    saved_theme = (gui.settings.get("theme") if hasattr(gui, "settings") else None) or "Dark"
    gui.theme_var = tk.StringVar(value=saved_theme)

    apply_theme(gui.style, gui.theme_var.get())
    # apply_theme just populated the host module's live palette globals
    # (APP_BG / CARD_BG / ACCENT / ...). On a truly-first run (no settings.json)
    # those were still None when _sync_host_globals ran at the top of this
    # function, so refresh our module-level copies now. Without this, later
    # _hsl_shift(CARD_BG) calls (e.g. the DinoRunner) get None and the whole
    # GUI fails to launch on a fresh install.
    _sync_host_globals(gui)
    try:
        retheme_runtime(gui, gui.style, gui.theme_var.get())
    except Exception:
        pass
    try:
        gui.root.configure(bg=APP_BG)
    except Exception:
        pass

    if not hasattr(gui, "preset_var"):        gui.preset_var = tk.StringVar(value=next(iter(PRESETS)))
    if not hasattr(gui, "target_size_var"):   gui.target_size_var = tk.IntVar(value=PRESETS[gui.preset_var.get()])
    if not hasattr(gui, "save_path"):         gui.save_path = tk.StringVar(value=os.path.join(SCRIPT_DIR, "output"))
    if not hasattr(gui, "size_unit_var"):
        gui.size_unit_var = tk.StringVar(value=(gui.settings.get("size_unit", "MB") if hasattr(gui, "settings") and isinstance(gui.settings, dict) else "MB"))
    if not hasattr(gui, "profile_var"):       gui.profile_var = tk.StringVar(value="")
    if not hasattr(gui, "watch_var"):         gui.watch_var = tk.BooleanVar(value=False)
    gui.per_file_opts = {}
    if not hasattr(gui, "watch_folder"):      gui.watch_folder = tk.StringVar(value=SCRIPT_DIR)
    if not hasattr(gui, "webhook_url"):       gui.webhook_url = ""
    if not hasattr(gui, "webhook_var"):       gui.webhook_var = tk.StringVar(value=gui.webhook_url)
    if not hasattr(gui, "file_list"): gui.file_list = []
    if not hasattr(gui, "per_file_opts"): gui.per_file_opts = {}  # path -> dict overrides

    def _adv_bool(name, key):
        if not hasattr(gui, name):
            setattr(gui, name, tk.BooleanVar(value=bool(ADVANCED_DEFAULTS.get(key, False))))
    def _adv_str(name, key):
        if not hasattr(gui, name):
            setattr(gui, name, tk.StringVar(value=str(ADVANCED_DEFAULTS.get(key, ""))))

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
    if not hasattr(gui, "adv_two_pass_fallback"): gui.adv_two_pass_fallback = tk.BooleanVar(value=bool(ADVANCED_DEFAULTS.get("two_pass_fallback", True)))
    if not hasattr(gui, "adv_auto_retry"):        gui.adv_auto_retry        = tk.BooleanVar(value=bool(ADVANCED_DEFAULTS.get("auto_retry", True)))

    gui.root.grid_columnconfigure(0, weight=1)

    header = tk.Frame(gui.root, bg=APP_BG)
    header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))

    # Static title (the old typewriter animation left it truncated whenever
    # the window lost focus mid-cycle).
    gui.title_label = ttk.Label(header, text="BitCrusher", style="Title.TLabel")
    gui.title_label.pack(side="left")

    # Right side of the header: quality mode + quick actions.
    if not hasattr(gui, "adv_quality_mode"):
        gui.adv_quality_mode = tk.StringVar(value="max")
    ttk.Button(header, text=gui._t("btn.user_guide", "User Guide"), style="Ghost.TButton",
               command=getattr(gui, "show_user_guide", lambda: None)).pack(side="right", padx=(8, 0))
    ttk.Button(header, text=gui._t("btn.advanced", "Advanced…"), style="Ghost.TButton",
               command=getattr(gui, "open_advanced_options", lambda: None)).pack(side="right", padx=(8, 0))
    _qwrap = tk.Frame(header, bg=APP_BG)
    _qwrap.pack(side="right", padx=(0, 12))
    ttk.Label(_qwrap, text=gui._t("lbl.quality", "Quality:"), style="Sub.TLabel").pack(side="left", padx=(0, 6))
    for _qv, _qt in (("fast", "Fast"), ("balanced", "Balanced"), ("max", "Max")):
        ttk.Radiobutton(_qwrap, text=gui._t("qmode." + _qv, _qt), value=_qv,
                        variable=gui.adv_quality_mode).pack(side="left", padx=(0, 6))

    gui.root.grid_rowconfigure(1, weight=1)
    content = tk.Frame(gui.root, bg=APP_BG)
    content.grid(row=1, column=0, sticky="nsew")

    # C1 relayout: left sidebar nav + a stack of page frames raised via
    # show_page(). Replaces the old 3-pane Panedwindow as the top structure.
    # The page content frames keep the historical local names left/mid/right
    # so the (verbatim) widget-building blocks below need no changes.
    content.grid_columnconfigure(1, weight=1)
    content.grid_rowconfigure(0, weight=1)

    sidebar = tk.Frame(content, bg=APP_BG, width=168)
    sidebar.grid(row=0, column=0, sticky="ns", padx=(10, 0), pady=8)
    sidebar.grid_propagate(False)

    pagehost = tk.Frame(content, bg=APP_BG)
    pagehost.grid(row=0, column=1, sticky="nsew")
    pagehost.grid_rowconfigure(0, weight=1)
    pagehost.grid_columnconfigure(0, weight=1)

    _nav_specs = [
        ("Queue",    gui._t("nav.queue",    "Queue")),
        ("Activity", gui._t("nav.activity", "Activity")),
        ("Stats",    gui._t("nav.stats",    "Stats")),
        ("Watcher",  gui._t("nav.watcher",  "Watcher")),
        ("Settings", gui._t("nav.settings", "Settings")),
    ]
    gui._pages = {}
    gui._nav_buttons = {}
    for _name, _label in _nav_specs:
        pg = tk.Frame(pagehost, bg=APP_BG)
        pg.grid(row=0, column=0, sticky="nsew")
        gui._pages[_name] = pg
        btn = ttk.Button(sidebar, text=_label, style="Nav.TButton",
                         command=lambda n=_name: show_page(gui, n))
        btn.pack(fill="x", pady=(0, 2))
        gui._nav_buttons[_name] = btn

    # Page content frames reuse the historical pane names.
    left  = gui._pages["Queue"]
    mid   = gui._pages["Activity"]
    right = gui._pages["Stats"]
    watcher_page  = gui._pages["Watcher"]
    settings_page = gui._pages["Settings"]

    ctrl = tk.Frame(left, bg=APP_BG)
    ctrl.pack(fill="x", padx=16, pady=(6, 8))

    ttk.Label(ctrl, text=gui._t("lbl.preset", "Preset:")).pack(side="left")
    gui.preset_combo = ttk.Combobox(
        ctrl,
        textvariable=gui.preset_var,
        state="readonly",
        width=24,
        values=sorted(list(PRESETS.keys()))
    )
    gui.preset_combo.pack(side="left", padx=(6, 16))
    gui.preset_combo.bind("<<ComboboxSelected>>",
        lambda _: getattr(gui, "set_preset", lambda _=None: None)(gui.preset_var.get())
    )

    ttk.Label(ctrl, text=gui._t("lbl.target_size", "Target Size:")).pack(side="left")

    gui.size_unit_var = tk.StringVar(
        value=gui.settings.get("size_unit","MB") if hasattr(gui,"settings") and isinstance(gui.settings,dict) else "MB"
    )

    size_frame = tk.Frame(ctrl, bg=APP_BG)
    size_frame.pack(side="left", padx=(6, 16))

    ttk.Entry(size_frame, textvariable=gui.target_size_var, width=7, style="Dark.TEntry").pack(side="left")
    ttk.Combobox(
        size_frame,
        textvariable=gui.size_unit_var,
        values=["KB","MB","GB","TB"],
        width=4,
        state="readonly"
    ).pack(side="left", padx=(6, 0))

    ttk.Button(ctrl, text=gui._t("btn.estimate", "Estimate"), style="Ghost.TButton",
               command=lambda: getattr(gui, "_estimate_queue", lambda: None)()).pack(side="left", padx=(0, 16))

    ttk.Label(ctrl, text=gui._t("lbl.save_to", "Save to:")).pack(side="left")
    gui.save_entry = ttk.Entry(ctrl, textvariable=gui.save_path, style="Dark.TEntry")
    gui.save_entry.pack(side="left", padx=6, fill="x", expand=True)
    ttk.Button(ctrl, text=gui._t("btn.browse", "Browse…"), style="Ghost.TButton",
               command=gui.select_output_dir).pack(side="left", padx=(4, 0))

    quick_row = tk.Frame(left, bg=APP_BG)
    quick_row.pack(fill="x", padx=16, pady=(0, 8))
    ttk.Label(quick_row, text=gui._t("lbl.discord_quick", "Discord:")).pack(side="left")
    for _preset_key, _mb_label in (
        ("Discord — Free (10 MB)", "10 MB"),
        ("Discord — Nitro Basic (50 MB)", "50 MB"),
        ("Discord — Nitro (500 MB)", "500 MB"),
    ):
        ttk.Button(
            quick_row, text=_mb_label, style="Ghost.TButton",
            command=lambda k=_preset_key: getattr(gui, "set_preset", lambda *_a, **_k: None)(k)
        ).pack(side="left", padx=(6, 0))

    tk.Label(right, text=gui._t("lbl.display", "Display"), bg=APP_BG, fg=FG, anchor="w").pack(fill="x", padx=12, pady=(12, 0))

    gui.display_mode_var = tk.StringVar(value="Quality Metrics")
    mode_row = tk.Frame(right, bg=APP_BG); mode_row.pack(fill="x", padx=12, pady=(6, 8))
    ttk.Label(mode_row, text=gui._t("lbl.mode", "Mode:")).pack(side="left")
    mode_cbx = ttk.Combobox(mode_row, textvariable=gui.display_mode_var, state="readonly",
                            values=["Quality Metrics","Advisor Insights","History","Visual Compare"], width=22)
    mode_cbx.pack(side="left", padx=(6,0))
    mode_cbx.bind("<<ComboboxSelected>>", lambda e: gui._rebuild_display_panel())

    gui.preview_container = tk.Frame(right, bg=CARD_BG, bd=1, relief="solid", highlightthickness=0)
    gui.preview_container.pack(fill="both", expand=True, padx=12, pady=(6, 12))






    def _clear_container():
        for w in gui.preview_container.winfo_children():
            try: w.destroy()
            except Exception: pass

    def _mk_metrics():

        wrap = tk.Frame(gui.preview_container, bg=CARD_BG); wrap.pack(fill="both", expand=True)
        gui._metrics_text = tk.Text(wrap, height=16, relief="flat", bd=0, bg=CARD_BG, fg=FG)
        gui._metrics_text.pack(fill="both", expand=True, padx=10, pady=10)
        gui._metrics_text.configure(state="disabled")

    def _mk_insights():
        wrap = tk.Frame(gui.preview_container, bg=CARD_BG); wrap.pack(fill="both", expand=True)
        gui._insights_text = tk.Text(wrap, height=16, relief="flat", bd=0, bg=CARD_BG, fg=FG)
        gui._insights_text.pack(fill="both", expand=True, padx=10, pady=10)
        gui._insights_text.configure(state="disabled")

    def _mk_history():
        from tkinter import ttk as _ttk
        wrap = tk.Frame(gui.preview_container, bg=CARD_BG); wrap.pack(fill="both", expand=True)
        cols = ("time","file","target_mb","encoder","actual_mb","overshoot")
        gui._hist = _ttk.Treeview(wrap, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (150, 220, 90, 80, 90, 90)):
            gui._hist.heading(c, text=c); gui._hist.column(c, width=w, anchor="w")
        gui._hist.pack(fill="both", expand=True, padx=10, pady=10)

    def _mk_compare():
        wrap = tk.Frame(gui.preview_container, bg=CARD_BG); wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text=gui._t("msg.compare_hint", "Compare the last output with the source.")).pack(anchor="w", padx=10, pady=(10,6))
        btn = ttk.Button(wrap, text=gui._t("btn.open_visual_compare", "Open Visual Compare"), command=gui._open_visual_compare_for_selection)
        btn.pack(anchor="w", padx=10, pady=(0,10))

    gui._mk_metrics   = _mk_metrics
    gui._mk_insights  = _mk_insights
    gui._mk_history   = _mk_history
    gui._mk_compare   = _mk_compare

    def _rebuild_display_panel():
        _clear_container()
        m = gui.display_mode_var.get()
        if m == "Quality Metrics":   _mk_metrics()
        elif m == "Advisor Insights":_mk_insights()
        elif m == "History":         _mk_history()
        else:                        _mk_compare()

        gui._refresh_display_panel()

    gui._rebuild_display_panel = _rebuild_display_panel

    # No Panedwindow anymore; pages are stacked and raised by show_page().
    gui.paned = None
    gui.root.update_idletasks()

    tk.Label(left, text=gui._t("lbl.queue","Queue"), bg=APP_BG, fg=FG, anchor="w").pack(fill="x", padx=12, pady=(12, 0))

    gui.drop_frame = tk.Frame(left, bg=CARD_BG, bd=1, relief="solid", highlightthickness=0)
    gui.drop_frame.pack(fill="both", expand=True, padx=12, pady=(8, 12))

    gui.queue_box = QueueTree(gui.drop_frame)
    gui._queue_scroll = ttk.Scrollbar(gui.drop_frame, orient="vertical",
                                       command=gui.queue_box.yview)
    gui.queue_box.configure(yscrollcommand=gui._queue_scroll.set)
    gui._queue_scroll.pack(side="right", fill="y")
    gui.job_rows: dict = {}
    gui.queue_menu = tk.Menu(gui.root, tearoff=0)
    gui.queue_menu.add_command(label=gui._t("qmenu.set_encoder", "Set encoder for this file..."), command=lambda: gui._queue_set("encoder"))
    gui.queue_menu.add_command(label=gui._t("qmenu.set_container", "Set container/format for this file..."), command=lambda: gui._queue_set("container"))
    gui.queue_menu.add_command(label=gui._t("qmenu.set_prefix", "Set prefix for this file..."),  command=lambda: gui._queue_set("output_prefix"))
    gui.queue_menu.add_command(label=gui._t("qmenu.set_suffix", "Set suffix for this file..."),  command=lambda: gui._queue_set("output_suffix"))
    gui.queue_menu.add_command(label=gui._t("qmenu.trim", "Trim / clip range for this file..."), command=lambda: gui._queue_set_trim())
    gui.queue_menu.add_command(label=gui._t("qmenu.suggest_trim", "Suggest trim ranges (audio peaks)..."), command=lambda: gui._queue_suggest_trim())
    gui.queue_menu.add_command(label=gui._t("qmenu.spotlight", "Spotlight quality range for this file..."), command=lambda: gui._queue_set_spotlight())
    gui.queue_menu.add_command(label=gui._t("qmenu.also_targets", "Also export at size(s) MB (e.g. 25 or 8,25)..."), command=lambda: gui._queue_set("also_targets"))
    gui.queue_menu.add_command(label=gui._t("qmenu.export_format", "Export as GIF/WebP (gif, webp, or blank)..."), command=lambda: gui._queue_set("export_format"))
    gui.queue_menu.add_separator()
    gui.queue_menu.add_command(label=gui._t("qmenu.reset_overrides", "Reset per-file overrides"), command=lambda: gui._queue_reset_overrides())
    gui.queue_menu.add_command(label=gui._t("qmenu.reset_status", "Reset status (re-encode)"), command=lambda: gui._queue_reset_status())
    gui.queue_menu.add_command(label=gui._t("qmenu.open_output", "Open Output Folder"), command=gui.open_save_folder)
    gui.queue_menu.add_command(label=gui._t("qmenu.remove", "Remove from Queue"),  command=gui.remove_selected)
    def _on_queue_context(event):
        try:
            i = gui.queue_box.nearest(event.y)
            gui.queue_box.selection_clear(0, "end")
            gui.queue_box.selection_set(i)
            gui.queue_box.activate(i)
        except Exception:
            pass
        try:
            gui.queue_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try: gui.queue_menu.grab_release()
            except Exception: pass

    def _queue_set(key: str):
        from tkinter import simpledialog
        sel = list(gui.queue_box.curselection())
        if not sel:
            return
        try:
            # Always look up the full path from file_list by index, not from listbox display text
            path = gui.file_list[sel[0]]
        except (IndexError, AttributeError):
            return
        if not hasattr(gui, "per_file_opts") or gui.per_file_opts is None:
            gui.per_file_opts = {}
        cur = (gui.per_file_opts.get(path, {}) or {}).get(key, "")
        # parent=gui.root is critical on Windows — without it the dialog appears
        # behind the main window, making the app appear frozen/crashed
        val = simpledialog.askstring(
            "Per-file override",
            f"{key} for:\n{os.path.basename(path)}",
            initialvalue=str(cur),
            parent=gui.root,
        )
        if val is None:
            return
        gui.per_file_opts.setdefault(path, {})[key] = val.strip()
        try: gui.update_status(f"Per-file: {os.path.basename(path)} -> {key}={val.strip()}")
        except Exception: pass
        try: gui._save_queue()
        except Exception: pass

    def _queue_reset_overrides():
        sel = list(gui.queue_box.curselection())
        if not sel or not getattr(gui, "per_file_opts", None):
            return
        for i in sel:
            try:
                path = gui.file_list[i]
                if gui.per_file_opts.pop(path, None) is not None:
                    gui.update_status(f"Per-file overrides cleared: {os.path.basename(path)}")
            except Exception:
                pass
        try:
            gui._save_queue()
        except Exception:
            pass

    def _queue_reset_status():
        sel = list(gui.queue_box.curselection())
        if not sel:
            return
        for i in sel:
            try:
                path = gui.file_list[i]
                gui._record_job_state(path, "pending")
                gui._job_update(path, status="pending", progress=None,
                                 eta="", size=None, vmaf=None)
                gui.update_status(f"Status reset (will re-encode): "
                                   f"{os.path.basename(path)}")
            except Exception:
                pass

    def _queue_set_trim():
        from tkinter import simpledialog, messagebox
        sel = list(gui.queue_box.curselection())
        if not sel:
            return
        try:
            path = gui.file_list[sel[0]]
        except (IndexError, AttributeError):
            return
        if not hasattr(gui, "per_file_opts") or gui.per_file_opts is None:
            gui.per_file_opts = {}
        cur = (gui.per_file_opts.get(path, {}) or {}).get("trim_range", "")
        val = simpledialog.askstring(
            "Trim / clip range",
            "Compress only this range of:\n"
            f"{os.path.basename(path)}\n\n"
            "Format: START-END (e.g. 1:42-2:05 or 12-31).\n"
            "The whole size budget goes to the kept range.\n"
            "Leave blank to clear the trim.",
            initialvalue=str(cur),
            parent=gui.root,
        )
        if val is None:
            return
        val = val.strip()
        if not val:
            if (gui.per_file_opts.get(path, {}) or {}).pop("trim_range", None) is not None:
                gui.update_status(f"Per-file: trim cleared for {os.path.basename(path)}")
            try: gui._save_queue()
            except Exception: pass
            return
        try:
            _a, _b = _parse_trim_range(val)
        except ValueError as e:
            try:
                messagebox.showerror(gui._t("dlg.trim_range", "Trim range"), f"Invalid range: {e}", parent=gui.root)
            except Exception:
                pass
            return
        gui.per_file_opts.setdefault(path, {})["trim_range"] = val
        gui.update_status(f"Per-file: {os.path.basename(path)} trim={val} "
                           f"({_b - _a:.1f}s kept)")
        try: gui._save_queue()
        except Exception: pass

    def _queue_selected_path():
        sel = list(gui.queue_box.curselection())
        if not sel:
            return None
        try:
            return gui.file_list[sel[0]]
        except (IndexError, AttributeError):
            return None

    def _queue_set_spotlight():
        from tkinter import simpledialog, messagebox
        path = _queue_selected_path()
        if not path:
            return
        if not hasattr(gui, "per_file_opts") or gui.per_file_opts is None:
            gui.per_file_opts = {}
        cur = (gui.per_file_opts.get(path, {}) or {}).get("spotlight_range", "")
        val = simpledialog.askstring(
            "Spotlight quality range",
            "Keep the WHOLE video, but boost quality in this range\n"
            "(the rest of the video pays for it under the same cap):\n"
            f"{os.path.basename(path)}\n\n"
            "Format: START-END (e.g. 1:42-2:05). Uses x264/x265 rate zones.\n"
            "Leave blank to clear.",
            initialvalue=str(cur), parent=gui.root)
        if val is None:
            return
        val = val.strip()
        if not val:
            if (gui.per_file_opts.get(path, {}) or {}).pop("spotlight_range", None) is not None:
                gui.update_status(f"Per-file: spotlight cleared for {os.path.basename(path)}")
            try: gui._save_queue()
            except Exception: pass
            return
        try:
            _parse_trim_range(val)
        except ValueError as e:
            try:
                messagebox.showerror(gui._t("dlg.spotlight_range", "Spotlight range"), f"Invalid range: {e}", parent=gui.root)
            except Exception:
                pass
            return
        gui.per_file_opts.setdefault(path, {})["spotlight_range"] = val
        gui.update_status(f"Per-file: {os.path.basename(path)} spotlight={val}")
        try: gui._save_queue()
        except Exception: pass

    def _queue_suggest_trim():
        from tkinter import messagebox
        path = _queue_selected_path()
        if not path:
            return
        gui.update_status(f"[Suggest] Analyzing audio energy of {os.path.basename(path)}...")

        def _work():
            try:
                cands = suggest_trim_ranges(path, clip_seconds=20.0,
                                            status_cb=lambda m, l="INFO": gui.update_status(m, level=l))
            except Exception:
                cands = []
            gui._ui(_show, cands)

        def _show(cands):
            if not cands:
                try:
                    messagebox.showinfo(
                        "Suggest trim",
                        "No clear audio peaks found.\n\n"
                        "Silent or uniform-loudness moments (e.g. a great play in a "
                        "quiet game) can't be detected by signal analysis - set the "
                        "trim manually via 'Trim / clip range for this file...'.",
                        parent=gui.root)
                except Exception:
                    pass
                return
            win = tk.Toplevel(gui.root)
            win.title(gui._t("dlg.suggested_trim", "Suggested trim ranges"))
            win.transient(gui.root)
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
                if not hasattr(gui, "per_file_opts") or gui.per_file_opts is None:
                    gui.per_file_opts = {}
                gui.per_file_opts.setdefault(path, {})["trim_range"] = rng
                gui.update_status(f"Per-file: {os.path.basename(path)} trim={rng} (from suggestion)")
                try: gui._save_queue()
                except Exception: pass
                win.destroy()

            ttk.Button(btns, text="Apply as trim", command=_apply).pack(side="right", padx=(6, 0))
            ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")

        threading.Thread(target=_work, name="bc_suggest", daemon=True).start()

    # expose nested helpers as instance attributes for event bindings/menu commands
    gui._on_queue_context = _on_queue_context
    gui._queue_set = _queue_set
    gui._queue_set_trim = _queue_set_trim
    gui._queue_set_spotlight = _queue_set_spotlight
    gui._queue_suggest_trim = _queue_suggest_trim
    gui._queue_reset_overrides = _queue_reset_overrides
    gui._queue_reset_status = _queue_reset_status

    gui.queue_box.bind("<Button-3>", gui._on_queue_context)
    gui.queue_box.pack(fill="both", expand=True, padx=6, pady=6)
    gui._rebuild_display_panel()
    gui.queue_box.bind("<<TreeviewSelect>>", lambda e: gui._schedule_display_refresh())
    gui.queue_box.bind("<Double-Button-1>", lambda e: gui._schedule_display_refresh())
    gui.queue_box.bind("<Control-v>", lambda e: getattr(gui, "paste_from_clipboard", lambda: None)())


    # Primary action row: big Start, Stop beside it.
    start_row = tk.Frame(left, bg=APP_BG)
    start_row.pack(side="bottom", fill="x", padx=12, pady=(4, 12))

    ttk.Button(
        start_row,
        text="▶  " + gui._t("btn.start", "Start Compression"),
        command=getattr(gui, "start_compression", lambda: None)
    ).pack(side="left", expand=True, fill="x", padx=(0, 8))

    ttk.Button(
        start_row,
        text=gui._t("btn.stop", "Stop"),
        style="Ghost.TButton",
        command=getattr(gui, "stop_compression", lambda: None)
    ).pack(side="left", padx=(0, 0), ipadx=10)

    # Queue management toolbar (secondary/ghost buttons).
    qbtns = tk.Frame(left, bg=APP_BG)
    qbtns.pack(side="bottom", fill="x", padx=12, pady=(6, 4))

    ttk.Button(qbtns, text="+ " + gui._t("btn.add_files","Add Files…"), style="Ghost.TButton",
               command=getattr(gui, "add_files", lambda: None)).pack(side="left", padx=(0, 6))
    ttk.Button(qbtns, text=gui._t("btn.paste_clipboard", "Paste"), style="Ghost.TButton",
               command=getattr(gui, "paste_from_clipboard", lambda: None)).pack(side="left", padx=(0, 6))
    ttk.Button(qbtns, text=gui._t("btn.remove_selected","Remove"), style="Ghost.TButton",
               command=getattr(gui, "remove_selected", lambda: None)).pack(side="left", padx=(0, 6))
    ttk.Button(qbtns, text=gui._t("btn.clear","Clear"), style="Ghost.TButton",
               command=getattr(gui, "clear_queue", lambda: None)).pack(side="left", padx=(0, 6))
    ttk.Button(qbtns, text=gui._t("btn.sort_eta","Sort: fastest first"), style="Ghost.TButton",
               command=getattr(gui, "sort_queue_by_eta", lambda: None)).pack(side="left", padx=(0, 6))
    ttk.Button(qbtns, text="▼", width=3, style="Ghost.TButton",
               command=lambda: getattr(gui, "move_selection", lambda *_: None)(+1)).pack(side="right", padx=(6, 0))
    ttk.Button(qbtns, text="▲", width=3, style="Ghost.TButton",
               command=lambda: getattr(gui, "move_selection", lambda *_: None)(-1)).pack(side="right")

    try:
        if TkinterDnD and hasattr(gui.root, "drop_target_register"):
            for w in (gui.drop_frame, gui.queue_box, gui.root):
                if hasattr(w, "drop_target_register"):
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", getattr(gui, "drop_file_handler", lambda *_: None))
    except Exception:
        pass

    from tkinter.scrolledtext import ScrolledText
    gui._activity_label = tk.Label(mid, text=gui._t("lbl.activity", "Activity"), bg=APP_BG, fg=FG, anchor="w")
    gui._activity_label.pack(fill="x", padx=12, pady=(12, 0))

    # Overall queue progress lives at the bottom of the middle pane.
    gui.progress = ttk.Progressbar(mid, style="Accent.Horizontal.TProgressbar",
                                    mode="determinate")
    gui.progress.pack(side="bottom", fill="x", padx=12, pady=(6, 12))

    _mid_nb = ttk.Notebook(mid)
    _mid_nb.pack(fill="both", expand=True, padx=12, pady=(8, 0))

    # Optional hidden T-Rex runner in the dead space above the Activity feed
    # (toggled from Advanced Options). Recreate cleanly if setup_ui re-runs.
    try:
        if getattr(gui, "dino_runner", None) is not None:
            gui.dino_runner.stop()
    except Exception:
        pass
    if not hasattr(gui, "dino_game_var"):
        gui.dino_game_var = tk.IntVar(
            value=1 if (getattr(gui, "settings", {}) or {}).get("dino_game", False) else 0)
    gui._mid_frame = mid
    gui.dino_runner = DinoRunner(mid, bg=_hsl_shift(CARD_BG, l_mul=0.90),
                                  fg=FG_SUB, accent=(globals().get("ACCENT") or "#4caf7d"))
    gui._apply_dino()

    # Plain-language view for everyone: friendly proportional font, roomy
    # line spacing, a left margin, and a blank line between files.
    _tab_feed = tk.Frame(_mid_nb, bg=CARD_BG)
    _mid_nb.add(_tab_feed, text=f"  {gui._t('tab.progress', 'Progress')}  ")
    gui.stage_text = ScrolledText(_tab_feed, height=16, wrap="word",
                                   bg=_hsl_shift(CARD_BG, l_mul=0.98), fg=FG,
                                   insertbackground=FG, relief="flat", borderwidth=0,
                                   font=("Segoe UI", 10), spacing1=3, spacing3=5,
                                   padx=10, pady=8)
    gui.stage_text.pack(fill="both", expand=True, padx=2, pady=2)
    gui.stage_text.config(state="disabled")

    # Technical detail for power users: monospace so the time/level/message
    # columns line up, tight spacing, section dividers between jobs.
    _tab_log = tk.Frame(_mid_nb, bg=CARD_BG)
    _mid_nb.add(_tab_log, text=f"  {gui._t('tab.details', 'Details')}  ")
    gui.log_text = ScrolledText(_tab_log, height=10, bg=_hsl_shift(CARD_BG, l_mul=0.96),
                                 fg=FG, insertbackground=FG, relief="flat", borderwidth=0,
                                 state="disabled", font=("Consolas", 9),
                                 spacing1=1, spacing3=1, padx=8, pady=6)
    gui.log_text.pack(fill="both", expand=True, padx=2, pady=2)
    gui.log_widget = gui.log_text
    gui.Log_widget = gui.log_widget
    bridge_gui_logger_color(gui.log_widget)

    # Lifetime stats: read-only roll-up of the run_*.jsonl encode history
    # (total bytes saved, VMAF distribution, encoder win-rates). Offline.
    _tab_stats = tk.Frame(_mid_nb, bg=CARD_BG)
    _mid_nb.add(_tab_stats, text=f"  {gui._t('tab.stats', 'Stats')}  ")
    _sbar = tk.Frame(_tab_stats, bg=CARD_BG); _sbar.pack(fill="x", padx=2, pady=(4, 0))
    ttk.Button(_sbar, text=gui._t("btn.refresh", "Refresh"), style="Ghost.TButton",
               command=lambda: gui.refresh_lifetime_stats()).pack(side="right")
    gui.stats_view = ScrolledText(_tab_stats, height=14, wrap="word",
                                   bg=_hsl_shift(CARD_BG, l_mul=0.98), fg=FG,
                                   insertbackground=FG, relief="flat", borderwidth=0,
                                   font=("Consolas", 10), spacing1=2, spacing3=2,
                                   padx=10, pady=8)
    gui.stats_view.pack(fill="both", expand=True, padx=2, pady=2)
    gui.stats_view.config(state="disabled")
    try:
        gui.refresh_lifetime_stats()
    except Exception:
        pass

    wb = ttk.LabelFrame(settings_page, text=gui._t("panel.webhook","Webhook"), style="Card.TLabelframe")
    wb.pack(fill="x", padx=12, pady=(12, 10))
    ttk.Label(wb, text=gui._t("lbl.webhook_url","Discord/Webhook URL"), style="Sub.TLabel").pack(anchor="w", padx=10, pady=(8, 0))
    ttk.Entry(wb, textvariable=gui.webhook_var, style="Dark.TEntry").pack(fill="x", padx=10, pady=(4, 10))

    wf = ttk.LabelFrame(watcher_page, text=gui._t("panel.watcher","Folder Watcher"), style="Card.TLabelframe")
    wf.pack(fill="x", padx=12, pady=(12, 10))
    gui.watch_chk = ttk.Checkbutton(
        wf, text=gui._t("lbl.enable_watcher","Enable watcher"), variable=gui.watch_var,
        onvalue=True, offvalue=False,
        command=getattr(gui, "toggle_watch_folder", lambda: None)
    )
    gui.watch_chk.pack(anchor="w", padx=10, pady=(8, 4))
    wrow = tk.Frame(wf, bg=APP_BG); wrow.pack(fill="x", padx=10, pady=(0, 10))
    ttk.Entry(wrow, textvariable=gui.watch_folder, style="Dark.TEntry").pack(side="left", fill="x", expand=True)
    ttk.Button(wrow, text="…", width=3, style="Ghost.TButton",
               command=getattr(gui, "browse_watch_folder", lambda: None)).pack(side="left", padx=(6, 0))
    if not hasattr(gui, "pipeline_var"):
        gui.pipeline_var = tk.BooleanVar(value=bool((getattr(gui, "settings", {}) or {}).get("pipeline_mode", False)))
    gui.pipeline_chk = ttk.Checkbutton(
        wf, text=gui._t("lbl.pipeline_mode", "Pipeline mode (auto-compress watched files + webhook)"),
        variable=gui.pipeline_var, onvalue=True, offvalue=False,
        command=lambda: gui.settings.__setitem__("pipeline_mode", bool(gui.pipeline_var.get())))
    gui.pipeline_chk.pack(anchor="w", padx=10, pady=(0, 10))

    pf = ttk.LabelFrame(settings_page, text=gui._t("panel.profiles","Profiles"), style="Card.TLabelframe")
    pf.pack(fill="x", padx=12, pady=(0, 10))
    ttk.Entry(pf, textvariable=gui.profile_var, style="Dark.TEntry").pack(fill="x", padx=10, pady=(8, 4))
    prow = tk.Frame(pf, bg=APP_BG); prow.pack(fill="x", padx=10, pady=(0, 10))
    ttk.Button(prow, text=gui._t("btn.save","Save"), style="Ghost.TButton",
               command=getattr(gui, "save_profile", lambda: None)).pack(side="left")
    ttk.Button(prow, text=gui._t("btn.load","Load"), style="Ghost.TButton",
               command=getattr(gui, "load_profile", lambda: None)).pack(side="left", padx=6)

    ttk.Button(settings_page, text=gui._t("btn.open_save", "Open Save Folder"), style="Ghost.TButton",
               command=getattr(gui, "open_save_folder", lambda: None)).pack(fill="x", padx=12, pady=(0, 12))

    if getattr(gui, "queue_box", None) is not None and gui.queue_box.size() == 0:
        try:
            getattr(gui, "set_preset", lambda *_a, **_k: None)(gui.preset_var.get())
        except Exception:
            pass

        try:
            gui.webhook_url = gui.webhook_var.get()
        except Exception:
            pass

        try:
            gui._on_save_dir_changed()
        except Exception:
            pass

    # Final retheme so widgets created above (notebook tabs, entries,
    # scrolled texts) pick up the palette — the first pass ran before
    # most of the UI existed.
    try:
        retheme_runtime(gui, gui.style, gui.theme_var.get())
    except Exception:
        pass

    # Make the quality score stand out in the plain-language feed so a
    # non-technical user skimming a long batch can't miss it.
    try:
        gui.stage_text.tag_configure(
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
        if getattr(gui, "file_list", None):
            gui.refresh_queue_box()
        else:
            gui._load_queue()
    except Exception:
        pass

    # Expose the shell hook and restore the nav selection. _rebuild_ui_for_language
    # destroys + rebuilds the whole view via setup_ui, so honour the page the user
    # was on before the rebuild (default to Queue on first build).
    gui.show_page = lambda name: show_page(gui, name)
    try:
        _restore = getattr(gui, "_active_page", None)
        if _restore not in getattr(gui, "_pages", {}):
            _restore = "Queue"
        show_page(gui, _restore)
    except Exception:
        pass









