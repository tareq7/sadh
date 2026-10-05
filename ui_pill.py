import os
import math
import time
import ctypes
import threading
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk, ImageFont

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008

user32 = ctypes.windll.user32
user32.GetParent.argtypes = [ctypes.c_void_p]
user32.GetParent.restype = ctypes.c_void_p
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_uint]
user32.SetWindowPos.restype = ctypes.c_int
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.ShowWindow.restype = ctypes.c_int

try:
    from .settings_ui import ModernSettingsWindow
except ImportError:
    from settings_ui import ModernSettingsWindow

class VoicePillWindow:
    """Compact futuristic floating controller for fast voice dictation."""

    BASE_WIDTH = 318
    BASE_HEIGHT = 64
    ORB_BASE_SIZE = 152
    SIZE_PRESETS = {
        "compact": (276, 56),
        "standard": (318, 64),
        "large": (382, 76),
    }
    ORB_SIZE_PRESETS = {
        "compact": (108, 108),
        "standard": (148, 148),
        "large": (174, 174),
    }

    def __init__(self, config_manager, on_mic_toggle=None, on_settings_change=None, on_cancel=None):
        self.config_manager = config_manager
        self.on_mic_toggle = on_mic_toggle
        self.on_settings_change = on_settings_change
        self.on_cancel = on_cancel

        # Establish physical pixels before creating the HWND. A layered
        # bitmap on a DPI-virtualized window would be rescaled every frame.
        try:
            user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
        self.root = tk.Tk()
        self.root.title("Sadh")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # A color key makes the empty canvas fully transparent. Windows also
        # alpha-blends the orb pixels so even the glow is translucent.
        self.trans_color = "#000001"
        self._color_key_supported = False
        try:
            self.root.wm_attributes("-transparentcolor", self.trans_color)
            self._color_key_supported = True
        except Exception:
            self.trans_color = "#121212"

        self.root.configure(bg=self.trans_color)
        self._apply_window_opacity()

        # Physical controller size is configurable. Pill themes use the legacy
        # 318x64 logical surface; the reactive orb uses a square logical surface.
        self.base_width, self.base_height = self._logical_dimensions()
        self.width, self.height = self._controller_dimensions()
        self.pos_x, self.pos_y = self._preferred_position()
        self.root.geometry(f"{self.width}x{self.height}+{self.pos_x}+{self.pos_y}")
        # The controller is a fixed HUD surface. Settings/DPI initialization
        # must never be allowed to resize it implicitly.
        self.root.resizable(False, False)
        self.root.minsize(self.width, self.height)
        self.root.maxsize(self.width, self.height)

        # Drag state
        self._drag_data = {"x": 0, "y": 0, "dragging": False}

        # Rendering scale & state
        self.render_scale = 2
        self.state_mode = "idle"  # "idle", "listening", "transcribing", "done"
        self.audio_level = 0.0
        self._display_level = 0.0
        self.wave_phase = 0.0
        self.hover_element = None  # "close", "gear", "mic", "card"
        self._visible = False
        self._photo_ref = None

        # Settings can own a visible Toplevel while the idle controller is
        # parked outside the desktop instead of withdrawing Tk's root.
        self.settings_window = None
        self._settings_offscreen = False
        self._settings_original_geometry = None
        self._closing = False

        # Build Canvas
        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg=self.trans_color,
            highlightthickness=0,
            bd=0
        )
        self.canvas.pack(fill="both", expand=True)

        # Event bindings
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<Motion>", self._on_mouse_hover)
        self.canvas.bind("<Leave>", self._on_mouse_leave)
        self.root.bind("<Escape>", lambda e: self._on_close_click())
        self.root.bind("<Destroy>", self._on_window_destroy, add="+")

        # Load fonts
        self._load_fonts()

        # Initial render & hide
        self._apply_no_activate()
        self._redraw()
        self.hide()

        # Animation tick
        self._animate_loop()

    def _on_window_destroy(self, event):
        if event.widget is self.root:
            self._closing = True
            surface = getattr(self, "_layered_surface", None)
            if surface:
                surface.close()
                self._layered_surface = None

            settings = getattr(self, "settings_window", None)
            settings_toplevel = getattr(settings, "window", None)
            if settings_toplevel is not None:
                try:
                    if settings_toplevel.winfo_exists():
                        settings_toplevel.destroy()
                except tk.TclError:
                    pass
            self.settings_window = None

    def _is_orb_theme(self):
        return self.config_manager.get("ui_theme", "orb") == "orb"

    def _apply_window_opacity(self):
        # Tk combines the transparent color key and overall alpha on the
        # same layered window; retain full opacity for the legacy themes.
        surface = getattr(self, "_layered_surface", None)
        if surface:
            surface.restore_color_key()
            self._layered_surface = None
        self._native_alpha_failed = False
        alpha = 1.0
        try:
            self.root.wm_attributes("-alpha", alpha)
        except Exception as error:
            print(f"[UI] Could not set orb opacity: {error}")

    def _logical_dimensions(self):
        if self._is_orb_theme():
            return self.ORB_BASE_SIZE, self.ORB_BASE_SIZE
        return self.BASE_WIDTH, self.BASE_HEIGHT

    def _controller_dimensions(self):
        preset = self.config_manager.get("controller_size", "standard")
        presets = self.ORB_SIZE_PRESETS if self._is_orb_theme() else self.SIZE_PRESETS
        return presets.get(preset, presets["standard"])

    def _screen_size(self):
        try:
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            return self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _preferred_position(self):
        screen_w, screen_h = self._screen_size()
        position = self.config_manager.get("controller_position", "top_center")
        if position == "top_right":
            return max(8, screen_w - self.width - 22), 22
        if position == "bottom_center":
            return max(0, (screen_w - self.width) // 2), max(8, screen_h - self.height - 54)
        return max(0, (screen_w - self.width) // 2), 22

    def _apply_interface_preferences(self, reposition=True):
        self._interface_revision = getattr(self, "_interface_revision", 0) + 1
        self.base_width, self.base_height = self._logical_dimensions()
        self.width, self.height = self._controller_dimensions()
        if reposition:
            self.pos_x, self.pos_y = self._preferred_position()
        else:
            self.pos_x = self.root.winfo_x()
            self.pos_y = self.root.winfo_y()
        self.canvas.configure(width=self.width, height=self.height)
        self.root.minsize(self.width, self.height)
        self.root.maxsize(self.width, self.height)
        if self._settings_offscreen and not self._visible:
            geometry = f"{self.width}x{self.height}+20000+20000"
        else:
            geometry = (
                f"{self.width}x{self.height}+{int(self.pos_x)}+{int(self.pos_y)}"
            )
        self.root.geometry(geometry)
        self._load_fonts()
        self._apply_window_opacity()
        self._redraw()

    def _to_logical(self, x, y):
        return (
            x * self.base_width / max(1, self.width),
            y * self.base_height / max(1, self.height),
        )

    def _load_fonts(self):
        # Draw only the reactive orb at 3x, then downsample with LANCZOS.
        # Keep the legacy pill at 2x to avoid extra work there.
        s = 3 if self._is_orb_theme() else 2
        self.render_scale = s
        if self._is_orb_theme():
            # Scale orb geometry and fonts together at every preset size.
            s *= self.width / self.ORB_BASE_SIZE
        try:
            self.font_title = ImageFont.truetype("segoeuib.ttf", round(9 * s))
            self.font_body = ImageFont.truetype("segoeui.ttf", round(8 * s))
            self.font_small = ImageFont.truetype("segoeui.ttf", round(7 * s))
            self.font_symbol = ImageFont.truetype("seguiemj.ttf", round(10 * s))
            self.font_orb_state = ImageFont.truetype("consolab.ttf", round(12 * s))
            self.font_orb_micro = ImageFont.truetype("consolab.ttf", round(7 * s))
        except Exception:
            self.font_title = ImageFont.load_default()
            self.font_body = self.font_title
            self.font_small = self.font_title
            self.font_symbol = self.font_title
            self.font_orb_state = self.font_title
            self.font_orb_micro = self.font_title

    def _apply_no_activate(self):
        """Applies WS_EX_NOACTIVATE to prevent focus theft from user's active window."""
        try:
            self.root.update_idletasks()
            hwnd = user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
        except Exception as e:
            print(f"[UI] Could not apply WS_EX_NOACTIVATE: {e}")

    # ----------------------------------------------------
    # ANTI-ALIASED RENDERING ENGINE (Orb: 3x Pillow Supersampling)
    # ----------------------------------------------------
    def _render_orb_image(self) -> Image.Image:
        from orb_visual import render_orb
        return render_orb(
            self.width, self.height, self.state_mode, self._display_level,
            self.wave_phase, self.hover_element,
            getattr(self, "language_hint", "AUTO"), self.render_scale,
        )

    def _render_pill_image(self) -> Image.Image:
        """Render the selected floating controller theme."""
        if self._is_orb_theme():
            return self._render_orb_image()

        s = self.render_scale
        w, h = self.base_width, self.base_height
        img = Image.new("RGBA", (w * s, h * s), (0, 0, 1, 0))
        draw = ImageDraw.Draw(img)

        # Palette: graphite glass by default; high-contrast is an
        # accessibility-oriented alternate presentation.
        theme = self.config_manager.get("ui_theme", "minimal")
        if theme == "contrast":
            palette = {
                "idle": ((255, 255, 255, 255), "READY", "Win+H  /  hold Insert"),
                "listening": ((0, 238, 255, 255), "LISTENING", getattr(self, "language_hint", "Auto language")),
                "transcribing": ((255, 201, 71, 255), "PROCESSING", "Merging transcript"),
                "done": ((96, 255, 153, 255), "READY", "Transcript ready"),
            }
            card_fill = (0, 0, 0, 255)
            card_outline = (210, 230, 240, 255)
        else:
            palette = {
                "idle": ((91, 105, 125, 255), "READY", "Win+H  /  hold Insert"),
                "listening": ((61, 218, 255, 255), "LISTENING", getattr(self, "language_hint", "Auto language")),
                "transcribing": ((151, 112, 255, 255), "PROCESSING", "Merging transcript"),
                "done": ((62, 224, 156, 255), "READY", "Transcript ready"),
            }
            card_fill = (12, 17, 24, 248)
            card_outline = (37, 49, 64, 255)
        accent, state_label, state_hint = palette.get(self.state_mode, palette["idle"])

        # Soft shadow and single glass-like surface.
        card = [5 * s, 5 * s, (w - 5) * s, (h - 5) * s]
        draw.rounded_rectangle(
            [7 * s, 8 * s, (w - 3) * s, (h - 2) * s],
            radius=17 * s, fill=(0, 0, 0, 92)
        )
        draw.rounded_rectangle(
            card, radius=16 * s,
            fill=card_fill,
            outline=card_outline,
            width=max(1, s)
        )

        # Thin futuristic highlight rail.
        rail_y = 6 * s
        draw.rounded_rectangle(
            [22 * s, rail_y, (w - 22) * s, 8 * s],
            radius=1 * s, fill=(28, 42, 55, 255)
        )
        rail_width = 88 if self.state_mode == "listening" else 54
        rail_x = 22 + int((math.sin(self.wave_phase * 0.45) + 1) * 18)
        draw.rounded_rectangle(
            [rail_x * s, rail_y, min(w - 22, rail_x + rail_width) * s, 8 * s],
            radius=1 * s, fill=accent
        )

        # State beacon + text block.
        dot_x, dot_y = 22 * s, 31 * s
        glow = 8 * s if self.state_mode in ("listening", "transcribing") else 6 * s
        draw.ellipse(
            [dot_x - glow, dot_y - glow, dot_x + glow, dot_y + glow],
            fill=(accent[0], accent[1], accent[2], 34)
        )
        draw.ellipse(
            [dot_x - 4 * s, dot_y - 4 * s, dot_x + 4 * s, dot_y + 4 * s],
            fill=accent
        )
        draw.text((36 * s, 23 * s), state_label, fill=(233, 241, 248, 255), font=self.font_title)
        draw.text((36 * s, 37 * s), state_hint, fill=(119, 137, 156, 255), font=self.font_small)

        # Compact 7-bar waveform.
        wave_x = 151 * s
        wave_y = 32 * s
        for i in range(7):
            offset = i - 3
            if self.state_mode == "listening":
                level = max(0.12, min(1.0, self.audio_level))
                motion = abs(math.sin(self.wave_phase + i * 0.72))
                bar_h = (5 + 15 * level * (0.45 + 0.55 * motion)) * s
            elif self.state_mode == "transcribing":
                bar_h = (5 + 10 * abs(math.sin(self.wave_phase * 1.1 + i * 0.8))) * s
            else:
                bar_h = (5 + (3 if i in (2, 3, 4) else 1)) * s
            x = wave_x + offset * 7 * s
            draw.rounded_rectangle(
                [x - 1.5 * s, wave_y - bar_h / 2, x + 1.5 * s, wave_y + bar_h / 2],
                radius=1.5 * s,
                fill=(accent[0], accent[1], accent[2], 225 if self.state_mode != "idle" else 120)
            )

        # Primary microphone action.
        mic_x, mic_y = 221 * s, 32 * s
        mic_r = 19 * s
        hover_mic = self.hover_element == "mic"
        mic_fill = (
            (accent[0], accent[1], accent[2], 245)
            if self.state_mode in ("listening", "transcribing")
            else ((30, 42, 54, 255) if not hover_mic else (38, 54, 68, 255))
        )
        draw.ellipse(
            [mic_x - mic_r, mic_y - mic_r, mic_x + mic_r, mic_y + mic_r],
            fill=mic_fill,
            outline=(accent[0], accent[1], accent[2], 210),
            width=max(1, s)
        )
        icon_col = (7, 17, 24, 255) if self.state_mode in ("listening", "transcribing") else (205, 220, 232, 255)
        draw.rounded_rectangle(
            [mic_x - 4 * s, mic_y - 8 * s, mic_x + 4 * s, mic_y + 4 * s],
            radius=4 * s, outline=icon_col, width=max(1, 1 * s)
        )
        draw.arc(
            [mic_x - 7 * s, mic_y - 4 * s, mic_x + 7 * s, mic_y + 9 * s],
            start=0, end=180, fill=icon_col, width=max(1, 1 * s)
        )
        draw.line([(mic_x, mic_y + 7 * s), (mic_x, mic_y + 11 * s)], fill=icon_col, width=max(1, s))
        draw.line([(mic_x - 5 * s, mic_y + 11 * s), (mic_x + 5 * s, mic_y + 11 * s)], fill=icon_col, width=max(1, s))

        # Minimal utility actions.
        gear_x, close_x, action_y = 268 * s, 298 * s, 32 * s
        if self.hover_element == "gear":
            draw.ellipse([gear_x - 12*s, action_y - 12*s, gear_x + 12*s, action_y + 12*s], fill=(27, 38, 50, 255))
        util_col = (149, 168, 187, 255)
        # Draw a small sliders/settings glyph instead of relying on emoji fonts.
        for oy, knob in ((-6, -3), (0, 4), (6, -1)):
            y = action_y + oy * s
            draw.line([(gear_x - 7*s, y), (gear_x + 7*s, y)], fill=util_col, width=max(1, s))
            kx = gear_x + knob * s
            draw.ellipse([kx - 2*s, y - 2*s, kx + 2*s, y + 2*s], fill=util_col)

        if self.hover_element == "close":
            draw.ellipse([close_x - 11*s, action_y - 11*s, close_x + 11*s, action_y + 11*s], fill=(44, 30, 38, 255))
        close_col = (236, 142, 162, 255) if self.hover_element == "close" else (128, 145, 162, 255)
        d = 4 * s
        draw.line([(close_x-d, action_y-d), (close_x+d, action_y+d)], fill=close_col, width=max(1, s))
        draw.line([(close_x-d, action_y+d), (close_x+d, action_y-d)], fill=close_col, width=max(1, s))

        downsampled = img.resize(
            (self.width, self.height), Image.Resampling.LANCZOS
        )
        bg = Image.new("RGB", (self.width, self.height), (0, 0, 1))
        bg.paste(downsampled, (0, 0), downsampled)
        return bg

    def _redraw(self):
        try:
            if self._is_orb_theme() and not getattr(self, "_native_alpha_failed", False):
                try:
                    from layered_surface import LayeredSurface
                    from orb_visual import render_orb
                    surface = getattr(self, "_layered_surface", None)
                    if surface is None:
                        self.root.update_idletasks()
                        user32.GetParent.argtypes = [ctypes.c_void_p]
                        user32.GetParent.restype = ctypes.c_void_p
                        hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
                        surface = self._layered_surface = LayeredSurface(hwnd)
                    pixel_w, pixel_h = surface.pixel_size()
                    surface.draw(render_orb(
                        pixel_w, pixel_h, self.state_mode, self._display_level,
                        self.wave_phase, self.hover_element,
                        getattr(self, "language_hint", "AUTO"), self.render_scale,
                    ))
                    return
                except Exception as error:
                    print(f"[UI] Native alpha fallback: {error}")
                    self._native_alpha_failed = True
                    if getattr(self, "_layered_surface", None):
                        self._layered_surface.restore_color_key()
                        self._layered_surface = None
            rendered_bg = self._render_pill_image()
            if rendered_bg.mode == "RGBA":
                alpha = rendered_bg.getchannel("A").point(lambda value: 255 if value > 96 else 0)
                fallback = Image.new("RGB", rendered_bg.size, (0, 0, 1))
                fallback.paste(rendered_bg, (0, 0), alpha)
                rendered_bg = fallback
            self._photo_ref = ImageTk.PhotoImage(rendered_bg)
            item = getattr(self, "_canvas_image_item", None)
            if item is None:
                self._canvas_image_item = self.canvas.create_image(
                    0, 0, image=self._photo_ref, anchor="nw"
                )
            else:
                self.canvas.itemconfigure(item, image=self._photo_ref)
        except Exception as e:
            print(f"[UI] Redraw note: {e}")

    # ----------------------------------------------------
    # MOUSE HIT TESTING & CURSORS
    # ----------------------------------------------------
    def _get_element_at(self, x: int, y: int):
        # Convert physical mouse coordinates back to the theme's logical layout.
        x, y = self._to_logical(x, y)
        if self._is_orb_theme():
            if (x - 127) ** 2 + (y - 44) ** 2 <= 12 ** 2:
                return "close"
            if (x - 127) ** 2 + (y - 108) ** 2 <= 12 ** 2:
                return "gear"
            cx = cy = self.ORB_BASE_SIZE / 2
            dist_sq = (x - cx) ** 2 + (y - cy) ** 2
            if dist_sq <= 34 ** 2:
                return "mic"
            if dist_sq <= 62 ** 2:
                return "card"
            return None
        if 286 <= x <= 312 and 18 <= y <= 46:
            return "close"
        if 255 <= x <= 282 and 18 <= y <= 46:
            return "gear"
        dist_sq = (x - 221) ** 2 + (y - 32) ** 2
        if dist_sq <= 21 ** 2:
            return "mic"
        if 5 <= x <= 313 and 5 <= y <= 59:
            return "card"
        return None

    def _on_mouse_hover(self, event):
        elem = self._get_element_at(event.x, event.y)
        if elem in ("close", "gear", "mic"):
            self.canvas.config(cursor="hand2")
        elif elem == "card":
            self.canvas.config(cursor="fleur")
        else:
            self.canvas.config(cursor="")

        if elem != self.hover_element:
            self.hover_element = elem
            self._redraw()

    def _on_mouse_leave(self, event):
        if self.hover_element is not None:
            self.hover_element = None
            self.canvas.config(cursor="")
            self._redraw()

    def _on_mouse_down(self, event):
        elem = self._get_element_at(event.x, event.y)
        if elem == "close":
            self._on_close_click()
        elif elem == "gear":
            self.open_settings()
        elif elem == "mic":
            if self.on_mic_toggle:
                self.on_mic_toggle()
        elif elem == "help":
            self.open_help()
        else:
            self._drag_data["x"] = event.x
            self._drag_data["y"] = event.y
            self._drag_data["dragging"] = True
            self._redraw()

    def _on_mouse_up(self, event):
        if self._drag_data["dragging"]:
            self._drag_data["dragging"] = False
            self.pos_x = self.root.winfo_x()
            self.pos_y = self.root.winfo_y()
            self._redraw()

    def _on_drag(self, event):
        if self._drag_data["dragging"]:
            dx = event.x - self._drag_data["x"]
            dy = event.y - self._drag_data["y"]
            nx = self.root.winfo_x() + dx
            ny = self.root.winfo_y() + dy
            self.pos_x, self.pos_y = nx, ny
            self.root.geometry(f"+{nx}+{ny}")

    def _on_close_click(self):
        if self.on_cancel:
            self.on_cancel()
        else:
            self.hide()

    # ----------------------------------------------------
    # LIFECYCLE & VISIBILITY
    # ----------------------------------------------------
    def is_visible(self) -> bool:
        return self._visible

    @staticmethod
    def _voice_visual_target(level):
        # The recorder supplies normalized RMS, with most speech well below
        # 1. Compress its dynamic range and reject faint room noise.
        amount = min(1.0, max(0.0, (float(level) - .012) * 2.5))
        return amount ** .70

    def _update_voice_visual_level(self):
        target = (self._voice_visual_target(self.audio_level)
                  if self.state_mode == "listening" else 0.0)
        previous = self._display_level
        factor = .58 if target > previous else .27
        self._display_level += (target - previous) * factor
        if self._display_level < .008:
            self._display_level = 0.0
        return abs(previous - self._display_level) > .01

    def set_state(self, mode: str, audio_level: float = 0.0):
        self.state_mode = mode
        self.audio_level = audio_level
        self._display_level = (self._voice_visual_target(audio_level)
                               if mode == "listening" else 0.0)
        self._redraw()

    def set_audio_level(self, level: float):
        self.audio_level = level

    def show(self):
        if self._settings_offscreen:
            self.root.geometry(
                f"{self.width}x{self.height}+{self.pos_x}+{self.pos_y}"
            )
            self._settings_offscreen = False
        self._visible = True
        self._apply_no_activate()
        try:
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE = 4
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010 | 0x0040)  # SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE|SWP_SHOWWINDOW
        except Exception:
            self.root.deiconify()
        self._redraw()
        try:
            self.root.update_idletasks()
        except Exception:
            pass

    def hide(self):
        self._visible = False
        self.state_mode = "idle"
        settings = getattr(self, "settings_window", None)
        settings_toplevel = getattr(settings, "window", None)
        try:
            settings_open = bool(
                settings_toplevel and settings_toplevel.winfo_exists()
            )
        except tk.TclError:
            settings_open = False
        if settings_open:
            if not self._settings_offscreen:
                self._settings_original_geometry = self.root.geometry()
            self._settings_offscreen = True
            self.root.geometry(
                f"{self.width}x{self.height}+20000+20000"
            )
            self.root.deiconify()
        else:
            self.root.withdraw()

    def _animate_loop(self):
        level_changed = self._update_voice_visual_level()
        motion = self.config_manager.get("ui_motion", "reduced")
        active = self.state_mode in ("listening", "transcribing")
        orb_motion = self._visible and self._is_orb_theme()
        if self._visible and motion != "off" and (active or orb_motion):
            if self._is_orb_theme():
                speed = (0.20 if motion == "full" else 0.11) * (1 + .45*self._display_level)
                if not active:
                    speed *= 0.55
                self.wave_phase += speed
            else:
                self.wave_phase += 0.35 if motion == "full" else 0.22
            self._redraw()
        elif level_changed and orb_motion and self.state_mode == "listening":
            # Reduced-motion users still receive voice-level feedback.
            self._redraw()
        # Redraw only while visible; microphone updates arrive every 20 ms.
        delay = (55 if active else 100) if motion == "full" else (
            (70 if active else 140) if motion == "reduced" else (100 if active else 160)
        )
        self.root.after(delay if self._visible else 250, self._animate_loop)

    # ----------------------------------------------------
    # MODAL DIALOGS
    # ----------------------------------------------------
    def open_settings(self):
        """Open Settings while keeping Tk's parent mapped outside the desktop."""
        controller_geometry = (
            self._settings_original_geometry or self.root.geometry()
        )
        controller_size = (self.width, self.height)
        controller_revision = getattr(self, "_interface_revision", 0)

        if not self._visible and not self._settings_offscreen:
            self._settings_original_geometry = controller_geometry
            self._settings_offscreen = True
            self.root.geometry(
                f"{self.width}x{self.height}+20000+20000"
            )
            self.root.deiconify()
            self.root.update_idletasks()

        if self.settings_window is None or not (
            hasattr(self.settings_window, "window")
            and self.settings_window.window
            and self.settings_window.window.winfo_exists()
        ):
            self.settings_window = ModernSettingsWindow(
                self.config_manager,
                on_settings_saved=self._on_settings_saved_callback,
                parent=self.root
            )
        self.settings_window.show()
        settings_toplevel = self.settings_window.window

        if getattr(self.settings_window, "_visibility_bound_window", None) is not settings_toplevel:
            def restore_controller_after_settings_destroy(event, window=settings_toplevel):
                if event.widget is not window or self._closing:
                    return
                if self._visible:
                    self._settings_offscreen = False
                    self._settings_original_geometry = None
                    return
                try:
                    self.root.withdraw()
                    if getattr(self, "_interface_revision", 0) == controller_revision:
                        geometry = self._settings_original_geometry or controller_geometry
                    else:
                        geometry = (
                            f"{self.width}x{self.height}+{self.pos_x}+{self.pos_y}"
                        )
                    self.root.geometry(geometry)
                except tk.TclError:
                    pass
                self._settings_offscreen = False
                self._settings_original_geometry = None

            settings_toplevel.bind(
                "<Destroy>", restore_controller_after_settings_destroy, add="+"
            )
            self.settings_window._visibility_bound_window = settings_toplevel

        def restore_controller_geometry():
            # Do not undo a saved size or position with a delayed open callback.
            if getattr(self, "_interface_revision", 0) != controller_revision:
                return
            self.width, self.height = controller_size
            self.canvas.configure(width=self.width, height=self.height)
            self.root.minsize(self.width, self.height)
            self.root.maxsize(self.width, self.height)
            if self._settings_offscreen and not self._visible:
                geometry = (
                    f"{self.width}x{self.height}+20000+20000"
                )
            else:
                geometry = controller_geometry
            self.root.geometry(geometry)
            self._redraw()

        # Let Tk finish DPI bookkeeping without moving the idle controller
        # back onto the desktop. Restore its stored geometry only when Settings
        # closes, or leave it visible if dictation starts during that time.
        self.root.after_idle(restore_controller_geometry)
        self.root.after(80, restore_controller_geometry)
        self.root.after(300, restore_controller_geometry)

    def _on_settings_saved_callback(self):
        self._apply_interface_preferences(reposition=True)
        if self.on_settings_change:
            self.on_settings_change()

    def open_help(self):
        """Displays VoxType shortcuts and behavior."""
        import customtkinter as ctk
        help_win = ctk.CTkToplevel()
        help_win.title("Sadh — Help & Shortcuts | صَدْح")
        help_win.geometry("440x360")
        help_win.configure(fg_color="#090e14")
        help_win.attributes("-topmost", True)
        help_win.resizable(False, False)

        try:
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            dx = max(0, (sw - 440) // 2)
            dy = max(0, (sh - 360) // 2)
            help_win.geometry(f"440x360+{dx}+{dy}")
        except Exception:
            pass

        frame = ctk.CTkFrame(help_win, fg_color="transparent", padx=20, pady=20)
        frame.pack(fill="both", expand=True)

        ctk.CTkLabel(
            frame, text="🎙️ Sadh Shortcuts & Guide | صَدْح",
            font=ctk.CTkFont(family="Segoe UI Variable Display", size=18, weight="bold"),
            text_color="#ffffff"
        ).pack(anchor="w", pady=(0, 14))

        shortcuts = [
            ("Win + H", "Start or stop reliable background-chunked dictation"),
            ("Insert", "Hold to talk; release to merge and insert the transcript"),
            ("Esc", "Cancel without inserting text"),
            ("Ctrl + Shift + Space", "Alternative global shortcut"),
            ("Match Windows", "Follows your active Windows input language"),
            ("Obvious fix", "Repairs known-name typos; keeps your wording"),
        ]

        for key, desc in shortcuts:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(
                row, text=key, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                text_color="#60cdff", width=140, anchor="w"
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=desc, font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color="#d0d0d0", anchor="w"
            ).pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            frame, text="Got it", fg_color="#0078d4", hover_color="#1084d8",
            width=100, height=34, font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=help_win.destroy
        ).pack(anchor="e", pady=(16, 0))

        help_win.lift()
        help_win.focus_force()

if __name__ == "__main__":
    from config_manager import ConfigManager
    import desktop_utils
    desktop_utils.attach_to_default_desktop()
    cfg = ConfigManager()
    ui = VoicePillWindow(cfg)
    ui.show()
    ui.set_state("listening", 0.7)
    ui.root.mainloop()