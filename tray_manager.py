import threading
from pathlib import Path
from PIL import Image, ImageDraw
import pystray

APP_NAME = "Sadh"
ICON_PATH = Path(__file__).resolve().parent / "assets" / "voxtype-icon.png"

def create_icon_image(is_recording=False):
    try:
        with Image.open(ICON_PATH) as source:
            image = source.convert("RGBA").resize(
                (64, 64), Image.Resampling.LANCZOS
            )
    except Exception:
        # Keep a simple fallback so tray startup survives a missing asset.
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, 60, 60), fill="#0c7dff")
        draw.rounded_rectangle((25, 15, 39, 36), radius=6, fill="#ffffff")
        draw.arc((18, 22, 46, 42), start=0, end=180, fill="#ffffff", width=3)
        draw.line((32, 42, 32, 50), fill="#ffffff", width=3)
        draw.line((24, 50, 40, 50), fill="#ffffff", width=3)

    if is_recording:
        draw = ImageDraw.Draw(image)
        draw.ellipse((45, 3, 62, 20), fill="#ff4b62", outline="#ffffff", width=2)
    return image

class TrayManager:
    def __init__(self, on_toggle_dictation, on_open_settings, on_quit):
        self.on_toggle_dictation = on_toggle_dictation
        self.on_open_settings = on_open_settings
        self.on_quit = on_quit
        self.icon = None

    def start(self):
        def _run():
            try:
                from .desktop_utils import attach_to_default_desktop
            except ImportError:
                from desktop_utils import attach_to_default_desktop
            attach_to_default_desktop()

            menu = pystray.Menu(
                pystray.MenuItem("Toggle Dictation", lambda: self.on_toggle_dictation()),
                pystray.MenuItem("Settings…", lambda: self.on_open_settings()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda: self.on_quit())
            )
            self.icon = pystray.Icon(
                APP_NAME, create_icon_image(False), f"{APP_NAME} · Voice Typing", menu
            )
            self.icon.run()

        threading.Thread(target=_run, daemon=True).start()

    def set_recording(self, recording: bool):
        if self.icon:
            self.icon.icon = create_icon_image(recording)

    def notify(self, message):
        if self.icon:
            try:
                self.icon.notify(message, APP_NAME)
            except Exception:
                pass

    def stop(self):
        if self.icon:
            self.icon.stop()
