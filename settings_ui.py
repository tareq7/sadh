import os
import sys
import webbrowser
import threading
import queue
import ctypes
import tkinter as tk
import customtkinter as ctk
from pathlib import Path

# The floating controller is a plain Tk window created before Settings.
# Prevent CustomTkinter from changing process DPI awareness later when the
# settings Toplevel is first opened; that late DPI switch was shrinking the
# already-visible controller on some Windows displays.
try:
    ctk.deactivate_automatic_dpi_awareness()
except Exception:
    pass

user32 = ctypes.windll.user32
user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetWindow.restype = ctypes.c_void_p
user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = ctypes.c_int

try:
    from .startup_manager import is_start_with_windows_enabled, set_start_with_windows
except ImportError:
    from startup_manager import is_start_with_windows_enabled, set_start_with_windows

class ModernSettingsWindow:
    SETTINGS_SIZE_PRESETS = {
        "compact": (500, 600),
        "standard": (620, 720),
        "large": (740, 820),
    }
    THEME_LABELS = {
        "Reactive Orb": "orb",
        "Current / Graphite": "minimal",
        "High Contrast": "contrast",
    }
    CONTROLLER_SIZE_LABELS = {
        "Compact": "compact",
        "Standard": "standard",
        "Large": "large",
    }
    SETTINGS_SIZE_LABELS = {
        "Compact": "compact",
        "Standard": "standard",
        "Large": "large",
    }
    POSITION_LABELS = {
        "Top center": "top_center",
        "Top right": "top_right",
        "Bottom center": "bottom_center",
    }
    MOTION_LABELS = {
        "Off": "off",
        "Reduced (faster)": "reduced",
        "Full": "full",
    }
    MODEL_LABELS = {"Turbo - faster": "whisper-large-v3-turbo", "Large v3 - higher accuracy": "whisper-large-v3"}
    LANGUAGE_LABELS = {
        "Match Windows typing language": "windows",
        "Auto detect speech": "auto",
        "Arabic": "ar",
        "English": "en",
    }

    def __init__(self, config_manager, on_settings_saved=None, parent=None):
        self.config_manager = config_manager
        self.on_settings_saved = on_settings_saved
        self.parent = parent
        self.window = None

    def show(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            self.window.focus_force()
            try:
                hwnd = self.window.winfo_id()
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.window = (
            ctk.CTkToplevel(master=self.parent)
            if self.parent
            else ctk.CTk()
        )
        self.window.title("Sadh — Settings | صَدْح")
        try:
            icon_path = Path(__file__).resolve().parent / "assets" / "voxtype-icon.png"
            self._icon_photo = tk.PhotoImage(file=str(icon_path))
            self.window.iconphoto(True, self._icon_photo)
        except Exception:
            self._icon_photo = None

        # Center window on physical screen
        try:
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
        except Exception:
            sw = self.window.winfo_screenwidth()
            sh = self.window.winfo_screenheight()

        size_key = self.config_manager.get("settings_size", "standard")
        dw, dh = self.SETTINGS_SIZE_PRESETS.get(
            size_key, self.SETTINGS_SIZE_PRESETS["standard"]
        )
        dx = max(0, (sw - dw) // 2)
        dy = max(0, (sh - dh) // 2)
        self.window.geometry(f"{dw}x{dh}+{dx}+{dy}")
        self.window.minsize(480, 520)
        self.window.resizable(True, True)
        self.window.title("Sadh — Settings | صَدْح")
        self.palette = {
            "bg": "#0d1118", "card": "#161d28", "border": "#2a3547",
            "accent": "#79e5ce", "accent_hover": "#9deedc",
            "input": "#101722", "muted": "#a3b1c5", "text": "#eef4fb",
            "selected": "#28564f",
        }
        if self.config_manager.get("ui_theme") == "contrast":
            self.palette.update(bg="#000000", card="#080808", border="#aaaaaa",
                                muted="#e0e0e0", text="#ffffff", input="#101010")
        self.window.configure(fg_color=self.palette["bg"])
        self.window.attributes("-topmost", True)

        self._build_ui()

        # Lift & focus. Keep Tk's normal owner relationship; ui_pill keeps its
        # hidden controller mapped offscreen while Settings is open.
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        try:
            self.window.update_idletasks()
            hwnd = user32.GetParent(self.window.winfo_id()) or self.window.winfo_id()
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    @staticmethod
    def _label_for(mapping, value, fallback):
        for label, key in mapping.items():
            if key == value:
                return label
        return fallback

    def _resize_settings_window(self, size_key):
        if not self.window or not self.window.winfo_exists():
            return
        dw, dh = self.SETTINGS_SIZE_PRESETS.get(
            size_key, self.SETTINGS_SIZE_PRESETS["standard"]
        )
        try:
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
        except Exception:
            sw = self.window.winfo_screenwidth()
            sh = self.window.winfo_screenheight()
        dx = max(0, (sw - dw) // 2)
        dy = max(0, (sh - dh) // 2)
        self.window.geometry(f"{dw}x{dh}+{dx}+{dy}")

    def _on_settings_size_changed(self, label):
        self._resize_settings_window(
            self.SETTINGS_SIZE_LABELS.get(label, "standard")
        )

    def _build_ui(self):
        p = self.palette
        self._preview_images = []
        header = ctk.CTkFrame(self.window, fg_color="transparent")
        header.pack(fill="x", padx=26, pady=(20, 12))
        ctk.CTkLabel(header, text="Sadh · صَدْح", text_color=p["text"],
                     font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(header, text="Speak naturally. Your words appear as you type.",
                     text_color=p["muted"], font=ctk.CTkFont(size=12)).pack(anchor="w")
        self.nav = ctk.CTkSegmentedButton(
            self.window, values=["Dictation", "Appearance", "Connection"],
            command=self._show_section, height=38, corner_radius=10,
            fg_color=p["input"], selected_color=p["selected"],
            selected_hover_color=p["selected"], unselected_color=p["input"],
            unselected_hover_color=p["border"], text_color=p["text"],
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.nav.pack(fill="x", padx=24, pady=(0, 10))

        footer = ctk.CTkFrame(self.window, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=24, pady=(10, 18))
        ctk.CTkLabel(footer, text="Ctrl + S to save", text_color=p["muted"],
                     font=ctk.CTkFont(size=11)).pack(side="left")
        ctk.CTkButton(
            footer, text="Save changes", command=self._save, width=134, height=38,
            corner_radius=9, fg_color=p["accent"], hover_color=p["accent_hover"],
            text_color="#08231e", font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="right", padx=(10, 0))
        ctk.CTkButton(
            footer, text="Cancel", command=self.window.destroy, width=76, height=38,
            corner_radius=9, fg_color=p["card"], hover_color=p["border"],
            text_color=p["text"],
        ).pack(side="right")

        self.page_host = ctk.CTkFrame(self.window, fg_color="transparent")
        self.page_host.pack(fill="both", expand=True, padx=18)
        self.pages = {
            name: ctk.CTkScrollableFrame(
                self.page_host, fg_color="transparent",
                scrollbar_button_color=p["border"],
                scrollbar_button_hover_color=p["muted"],
            ) for name in ("Dictation", "Appearance", "Connection")
        }
        self._build_dictation(self.pages["Dictation"])
        self._build_appearance(self.pages["Appearance"])
        self._build_connection(self.pages["Connection"])
        self.nav.set("Dictation")
        self._show_section("Dictation")
        self.window.bind("<Control-s>", lambda event: self._save())
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.bind("<Control-Tab>", self._next_section)

    def _show_section(self, name):
        for key, page in self.pages.items():
            if key == name:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()

    def _next_section(self, event=None):
        names = list(self.pages)
        name = names[(names.index(self.nav.get()) + 1) % len(names)]
        self.nav.set(name)
        self._show_section(name)
        return "break"

    def _section(self, parent, title, description=""):
        card = ctk.CTkFrame(parent, fg_color=self.palette["card"], corner_radius=14,
                           border_width=1, border_color=self.palette["border"])
        card.pack(fill="x", padx=2, pady=(0, 12))
        ctk.CTkLabel(card, text=title, anchor="w", text_color=self.palette["text"],
                     font=ctk.CTkFont(size=16, weight="bold")).pack(fill="x", padx=18, pady=(14, 0))
        if description:
            ctk.CTkLabel(card, text=description, justify="left", anchor="w",
                         wraplength=350, text_color=self.palette["muted"],
                         font=ctk.CTkFont(size=11)).pack(fill="x", padx=18, pady=(0, 8))
        return card

    def _option(self, parent, label, variable, values, command=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(7, 11))
        ctk.CTkLabel(row, text=label, anchor="w", text_color=self.palette["text"],
                     font=ctk.CTkFont(size=12)).pack(fill="x", pady=(0, 4))
        menu = ctk.CTkOptionMenu(
            row, values=list(values), variable=variable, command=command,
            height=35, corner_radius=8, font=ctk.CTkFont(size=12),
            fg_color=self.palette["input"], button_color=self.palette["border"],
            button_hover_color=self.palette["selected"], text_color=self.palette["text"],
            dropdown_fg_color=self.palette["input"],
            dropdown_hover_color=self.palette["selected"],
        )
        menu.pack(fill="x")
        return menu

    def _switch(self, parent, title, description, variable, command=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(8, 10))
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(row, text=title, anchor="w", text_color=self.palette["text"],
                     font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, sticky="w")
        toggle = ctk.CTkSwitch(
            row, text="", variable=variable, command=command, width=42,
            switch_width=36, switch_height=20,
            progress_color=self.palette["accent"], fg_color=self.palette["border"],
            button_color="#eff9f6", button_hover_color="#ffffff",
        )
        toggle.grid(row=0, column=1, rowspan=2, sticky="e", padx=(14, 0))
        if description:
            ctk.CTkLabel(row, text=description, justify="left", anchor="w",
                         wraplength=280, text_color=self.palette["muted"],
                         font=ctk.CTkFont(size=11)).grid(row=1, column=0, sticky="w")
        return toggle

    def _entry(self, parent, variable, placeholder):
        entry = ctk.CTkEntry(parent, textvariable=variable, placeholder_text=placeholder,
                            height=36, corner_radius=8, fg_color=self.palette["input"],
                            border_color=self.palette["border"], font=ctk.CTkFont(size=12))
        entry.pack(fill="x", padx=18, pady=(4, 14))
        return entry

    def _build_dictation(self, page):
        cfg = self.config_manager
        speech = self._section(page, "Speech", "Choose how WinVoice listens.")
        self.lang_var = ctk.StringVar(value=self._label_for(
            self.LANGUAGE_LABELS, cfg.get("language", "auto"), "Auto detect speech"))
        self.lang_cb = self._option(speech, "Language", self.lang_var, self.LANGUAGE_LABELS)
        ctk.CTkLabel(speech, text="Windows mode follows your typing language when supported by Whisper.",
                     wraplength=350, justify="left", text_color=self.palette["muted"],
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=18, pady=(0, 7))
        self.model_var = ctk.StringVar(value=self._label_for(
            self.MODEL_LABELS, cfg.get("model"), "Turbo - faster"))
        self.model_cb = self._option(speech, "Recognition", self.model_var, self.MODEL_LABELS)
        flow = self._section(page, "Typing flow", "Control when your words appear.")
        self.paste_var = ctk.BooleanVar(value=cfg.get("auto_paste", True))
        self.chunking_var = ctk.BooleanVar(value=cfg.get("reliable_chunking", True))
        self.live_var = ctk.BooleanVar(value=cfg.get("live_typing", True))
        self._switch(flow, "Insert into the active app", "Off: copy the transcript to your clipboard.",
                     self.paste_var, self._update_live_availability)
        self._switch(flow, "Transcribe in the background", "Process short chunks while you keep speaking.",
                     self.chunking_var, self._update_live_availability)
        self.live_switch = self._switch(flow, "Type while I speak",
                     "Insert completed chunks as soon as they are ready.",
                     self.live_var)
        self._update_live_availability()
        self.sound_var = ctk.BooleanVar(value=cfg.get("sound_cues", True))
        self._switch(flow, "Recording sounds", "A short cue at the start and end.", self.sound_var)
        words = self._section(page, "Your writing", "Small adjustments that keep your wording.")
        self.periods_var = ctk.BooleanVar(value=cfg.get("strip_sentence_periods", True))
        self.lowercase_var = ctk.BooleanVar(value=cfg.get("lowercase_sentence_starts", True))
        self.fix_var = ctk.BooleanVar(value=cfg.get("auto_fix_obvious", True))
        self._switch(words, "Skip sentence-ending periods", "", self.periods_var)
        self._switch(words, "Keep sentence starts lowercase", "", self.lowercase_var)
        self._switch(words, "Fix obvious spelling mistakes", "Only known names and narrowly defined typos.", self.fix_var)
        self.prompt_var = ctk.StringVar(value=cfg.get("custom_prompt", ""))
        ctk.CTkLabel(words, text="Names and terms (optional)", anchor="w",
                     text_color=self.palette["muted"], font=ctk.CTkFont(size=11)).pack(fill="x", padx=18, pady=(8, 0))
        self.prompt_entry = self._entry(words, self.prompt_var, "Names, brands and specialist terms")
        # Preserve the existing dialect preference; automatic dialect priming is disabled.
        dialects = {"None (Pure Whisper)": "none", "Gazan / Palestinian": "gazan",
                    "Levantine / Shami": "levantine", "Gulf / Khaleeji": "gulf",
                    "Modern Standard": "standard"}
        self.dialect_map = dialects
        self.dialect_var = ctk.StringVar(value=self._label_for(dialects, cfg.get("dialect"), "None (Pure Whisper)"))
        shortcuts = self._section(page, "Shortcuts", "Your controls stay available in any app.")
        self.hotkey_var = ctk.StringVar(value=cfg.get("hotkey", "<cmd>+h"))
        ctk.CTkLabel(shortcuts, text="Toggle recording", text_color=self.palette["muted"],
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=18, pady=(6, 0))
        self.hotkey_entry = self._entry(shortcuts, self.hotkey_var, "<cmd>+h")
        for key, action in (("Insert", "Hold to talk"), ("Esc", "Cancel dictation"),
                            ("Alt + Insert", "English"), ("Alt + Shift + Insert", "Arabic")):
            row = ctk.CTkFrame(shortcuts, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=(0, 9))
            ctk.CTkLabel(row, text=key, text_color=self.palette["accent"],
                         font=ctk.CTkFont(family="Consolas", size=11)).pack(side="left")
            ctk.CTkLabel(row, text=action, text_color=self.palette["muted"],
                         font=ctk.CTkFont(size=11)).pack(side="right")

    def _update_live_availability(self):
        if hasattr(self, "live_switch"):
            enabled = self.paste_var.get() and self.chunking_var.get()
            self.live_switch.configure(state="normal" if enabled else "disabled")

    def _build_appearance(self, page):
        cfg = self.config_manager
        preview = self._section(page, "Reactive Orb", "Transparent and voice reactive. Hidden when idle.")
        from orb_visual import preview_orb
        art = ctk.CTkFrame(preview, fg_color="transparent")
        art.pack(fill="x", padx=18, pady=(4, 14))
        for bg, label in (("#101722", "Dark surfaces"), ("#edf1f5", "Light surfaces")):
            panel = ctk.CTkFrame(art, fg_color=bg, corner_radius=12)
            panel.pack(side="left", expand=True, fill="both", padx=4)
            picture = ctk.CTkImage(light_image=preview_orb(128, bg), size=(128, 128))
            self._preview_images.append(picture)
            ctk.CTkLabel(panel, image=picture, text="").pack(pady=(8, 0))
            ctk.CTkLabel(panel, text=label, text_color="#a8b8ca" if bg == "#101722" else "#415168",
                         font=ctk.CTkFont(size=10)).pack(pady=(0, 10))
        interface = self._section(page, "Floating controller", "Changes apply when you save.")
        for attr, mapping, key, default, label in (
            ("ui_theme_var", self.THEME_LABELS, "ui_theme", "Reactive Orb", "Style"),
            ("controller_size_var", self.CONTROLLER_SIZE_LABELS, "controller_size", "Standard", "Size"),
            ("position_var", self.POSITION_LABELS, "controller_position", "Top center", "Position"),
            ("motion_var", self.MOTION_LABELS, "ui_motion", "Reduced (faster)", "Motion"),
        ):
            variable = ctk.StringVar(value=self._label_for(mapping, cfg.get(key), default))
            setattr(self, attr, variable)
            self._option(interface, label, variable, mapping)
        desktop = self._section(page, "Desktop", "Make WinVoice fit your routine.")
        self.settings_size_var = ctk.StringVar(value=self._label_for(
            self.SETTINGS_SIZE_LABELS, cfg.get("settings_size"), "Standard"))
        self._option(desktop, "Settings window", self.settings_size_var,
                     self.SETTINGS_SIZE_LABELS, self._on_settings_size_changed)
        self.startup_var = ctk.BooleanVar(value=is_start_with_windows_enabled())
        self._switch(desktop, "Start with Windows", "Ready in the tray when you sign in.", self.startup_var)

    def _build_connection(self, page):
        cfg = self.config_manager
        card = self._section(page, "Groq connection", "Audio is sent to Groq for transcription.")
        self.api_key_var = ctk.StringVar(value=cfg.get("groq_api_key", ""))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(6, 10))
        self.api_entry = ctk.CTkEntry(row, textvariable=self.api_key_var, show="*",
                            placeholder_text="Groq API key", height=36,
                            fg_color=self.palette["input"], border_color=self.palette["border"])
        self.api_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.show_key_btn = ctk.CTkButton(row, text="Show", command=self._toggle_show_key,
                            width=58, height=36, fg_color=self.palette["border"],
                            hover_color=self.palette["selected"])
        self.show_key_btn.pack(side="right")
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=(0, 10))
        self.test_key_btn = ctk.CTkButton(actions, text="Test connection", command=self._test_groq_key,
                            height=34, fg_color=self.palette["selected"], hover_color=self.palette["border"])
        self.test_key_btn.pack(side="left")
        ctk.CTkButton(actions, text="Get an API key", width=118, height=34,
                       command=lambda: webbrowser.open("https://console.groq.com/keys"),
                       fg_color="transparent", text_color=self.palette["accent"],
                       hover_color=self.palette["border"]).pack(side="right")
        self.test_status_lbl = ctk.CTkLabel(card,
                       text="Key saved" if self.api_key_var.get() else "Add a key to start dictating",
                       text_color=self.palette["muted"], font=ctk.CTkFont(size=11))
        self.test_status_lbl.pack(anchor="w", padx=18, pady=(0, 14))
        context = self._section(page, "Optional context", "Help recognize names from the active conversation.")
        self.context_var = ctk.BooleanVar(value=cfg.get("screen_context", False))
        self._switch(context, "Share a window crop with Groq",
                     "Sends the center crop once at recording start.", self.context_var)
        ctk.CTkLabel(context,
                     text="The crop may include private chat text. Small windows are skipped. "
                          "Keep this off for sensitive pages; typing never waits for context.",
                     justify="left", wraplength=350, text_color=self.palette["muted"],
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=18, pady=(0, 16))

    def _toggle_show_key(self):
        hidden = self.api_entry.cget("show") == "*"
        self.api_entry.configure(show="" if hidden else "*")
        self.show_key_btn.configure(text="Hide" if hidden else "Show")

    def _test_groq_key(self):
        key = self.api_key_var.get().strip()
        if not key:
            self.test_status_lbl.configure(text="Please enter an API key", text_color="#ff5555")
            return

        self.test_status_lbl.configure(text="Testing...", text_color=self.palette["accent"])
        self.test_key_btn.configure(state="disabled")

        results = queue.Queue()
        window = self.window
        status_label, test_button = self.test_status_lbl, self.test_key_btn

        def _worker():
            try:
                import httpx
                response = httpx.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=8.0,
                )
                if response.status_code == 200:
                    msg = "✓ Key Valid!"
                    color = "#4cc2ff"
                elif response.status_code in (401, 403):
                    msg = "✗ Invalid Key"
                    color = "#ff5555"
                else:
                    msg = f"✗ API Error {response.status_code}"
                    color = "#ff5555"
            except Exception:
                msg = "✗ Connection Error"
                color = "#ff5555"

            results.put((msg, color))

        def poll():
            if self.window is not window or not window.winfo_exists():
                return
            try:
                msg, color = results.get_nowait()
            except queue.Empty:
                window.after(50, poll)
                return
            status_label.configure(text=msg, text_color=color)
            test_button.configure(state="normal")
        window.after(50, poll)
        threading.Thread(target=_worker, daemon=True).start()

    def _save(self):
        raw_dialect = self.dialect_map.get(self.dialect_var.get(), "gazan")
        start_with_win = self.startup_var.get()

        # One atomic config write is faster and avoids a dozen fsync calls.
        self.config_manager.config.update({
            "groq_api_key": self.api_key_var.get().strip(),
            "model": self.MODEL_LABELS.get(self.model_var.get(), "whisper-large-v3-turbo"),
            "language": self.LANGUAGE_LABELS.get(self.lang_var.get(), "auto"),
            "dialect": raw_dialect,
            "hotkey": self.hotkey_var.get().strip(),
            "custom_prompt": self.prompt_var.get().strip(),
            "sound_cues": self.sound_var.get(),
            "reliable_chunking": self.chunking_var.get(),
            "live_typing": self.live_var.get(),
            "strip_sentence_periods": self.periods_var.get(),
            "lowercase_sentence_starts": self.lowercase_var.get(),
            "auto_fix_obvious": self.fix_var.get(),
            "auto_paste": self.paste_var.get(),
            "screen_context": self.context_var.get(),
            "ui_theme": self.THEME_LABELS.get(
                self.ui_theme_var.get(), "orb"
            ),
            "controller_size": self.CONTROLLER_SIZE_LABELS.get(
                self.controller_size_var.get(), "standard"
            ),
            "settings_size": self.SETTINGS_SIZE_LABELS.get(
                self.settings_size_var.get(), "standard"
            ),
            "controller_position": self.POSITION_LABELS.get(
                self.position_var.get(), "top_center"
            ),
            "ui_motion": self.MOTION_LABELS.get(
                self.motion_var.get(), "reduced"
            ),
            "start_with_windows": start_with_win,
        })
        self.config_manager.save()
        set_start_with_windows(start_with_win)

        if self.on_settings_saved:
            self.on_settings_saved()

        self.window.destroy()
        self.window = None

if __name__ == "__main__":
    from config_manager import ConfigManager
    import desktop_utils
    desktop_utils.attach_to_default_desktop()
    cfg = ConfigManager()
    win = ModernSettingsWindow(cfg)
    win.show()
    win.window.mainloop()