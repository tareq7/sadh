import os
import json
import tempfile
from pathlib import Path

DEFAULT_CONFIG = {
    "groq_api_key": "",
    "model": "whisper-large-v3-turbo",
    "language": "auto",
    "dialect": "gazan",
    "hotkey": "<cmd>+h",
    "dictation_mode": "toggle",
    "auto_paste": True,
    "auto_fix_obvious": True,
    "screen_context": False,
    "reliable_chunking": True,
    "live_typing": True,
    "live_holdback_words": 0,
    "chunk_pause_ms": 220,
    "chunk_min_seconds": 1.6,
    "chunk_soft_max_seconds": 2.8,
    "chunk_hard_max_seconds": 4.0,
    "chunk_overlap_ms": 650,
    "chunk_voice_on": 0.035,
    "chunk_voice_off": 0.018,
    "chunk_retries": 1,
    "sound_cues": True,
    "custom_prompt": "",
    "strip_sentence_periods": True,
    "lowercase_sentence_starts": True,
    "ui_theme": "orb",
    "controller_size": "standard",
    "settings_size": "standard",
    "controller_position": "top_center",
    "ui_motion": "reduced",
    "start_with_windows": False,
}

import sys

if getattr(sys, "frozen", False):
    CONFIG_FILE_PATH = Path(sys.executable).resolve().parent / "config.json"
else:
    CONFIG_FILE_PATH = Path(__file__).resolve().parent / "config.json"

class ConfigManager:
    def __init__(self, path: Path = CONFIG_FILE_PATH):
        self.path = path
        self.config = self._load()

    def _load(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        # Check if local config.json exists, or fallback to bundled template in frozen mode
        load_source = None
        if self.path.exists():
            load_source = self.path
        elif getattr(sys, "frozen", False):
            bundled = Path(getattr(sys, "_MEIPASS", "")) / "config.json"
            if bundled.exists():
                load_source = bundled

        if load_source and load_source.exists():
            try:
                with open(load_source, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    cfg.update(saved)
                    # Migrate the old sci-fi HUD theme name to the new
                    # borderless reactive orb without exposing legacy branding.
                    if cfg.get("ui_theme") == "jarvis":
                        cfg["ui_theme"] = "orb"
            except Exception as e:
                print(f"[Config] Error loading {load_source}: {e}")
        
        # Fallback to .env file or environment variable if API key is not configured in file
        if not cfg.get("groq_api_key"):
            env_file = self.path.parent / ".env"
            if env_file.exists():
                try:
                    for line in env_file.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line.startswith("GROQ_API_KEY="):
                            cfg["groq_api_key"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break
                except Exception:
                    pass
            if not cfg.get("groq_api_key"):
                env_key = os.environ.get("GROQ_API_KEY", "").strip()
                if env_key:
                    cfg["groq_api_key"] = env_key
        return cfg

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="config-", suffix=".tmp", delete=False) as f:
                temp = f.name
                json.dump(self.config, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save()
