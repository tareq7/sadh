import ctypes
import threading
from pynput import keyboard

# Ensure pynput's low-level hook thread attaches to interactive desktop
try:
    import pynput.keyboard._win32 as _pynput_w32
    _orig_listener_run = _pynput_w32.Listener._run

    def _patched_listener_run(self):
        try:
            try:
                from .desktop_utils import attach_to_default_desktop
            except ImportError:
                from desktop_utils import attach_to_default_desktop
            attach_to_default_desktop()
        except Exception:
            pass
        return _orig_listener_run(self)

    _pynput_w32.Listener._run = _patched_listener_run
except Exception as _e:
    pass

user32 = ctypes.windll.user32
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

class HotkeyListener:
    def __init__(
        self,
        hotkey_str: str,
        on_triggered,
        on_cancel=None,
        is_active_predicate=None,
        on_ptt_start=None,
        on_ptt_stop=None,
        on_ptt_start_language=None,
    ):
        self.hotkey_str = self._normalize_hotkey(hotkey_str)
        self.on_triggered = on_triggered
        self.on_cancel = on_cancel
        self.is_active_predicate = is_active_predicate
        self.on_ptt_start = on_ptt_start
        self.on_ptt_stop = on_ptt_stop
        self.on_ptt_start_language = on_ptt_start_language
        self.listener = None
        self._lock = threading.RLock()
        self._win_pressed = False
        self._pressed_modifier_vks = set()
        self._win_combo_active = False
        self._primary_key_down = False
        self._alt_combo_down = False
        self._escape_down = False
        self._insert_down = False
        self._configure_primary_hotkey(self.hotkey_str)

    def _normalize_hotkey(self, raw: str) -> str:
        s = raw.strip().lower()
        alias_map = {
            "win": "cmd",
            "windows": "cmd",
            "super": "cmd",
            "control": "ctrl",
            "escape": "esc",
            "return": "enter",
            "spacebar": "space",
        }
        parts = s.split("+")
        normalized_parts = []
        for p in parts:
            p = p.strip().strip("<>").lower()
            p = alias_map.get(p, p)
            if len(p) > 1 or p in ("cmd", "alt", "ctrl", "shift", "esc", "tab"):
                normalized_parts.append(f"<{p}>")
            else:
                normalized_parts.append(p)
        return "+".join(normalized_parts)

    def _configure_primary_hotkey(self, normalized: str):
        parts = [p.strip().strip("<>").lower() for p in normalized.split("+") if p.strip()]
        modifiers = {p for p in parts if p in {"cmd", "ctrl", "shift", "alt"}}
        keys = [p for p in parts if p not in modifiers]

        special_vk = {
            "space": 0x20,
            "tab": 0x09,
            "enter": 0x0D,
            "esc": 0x1B,
            "backspace": 0x08,
            "delete": 0x2E,
            "home": 0x24,
            "end": 0x23,
            "left": 0x25,
            "up": 0x26,
            "right": 0x27,
            "down": 0x28,
        }

        if len(keys) != 1:
            print(f"[Hotkey] Invalid primary shortcut '{normalized}', falling back to Win + H")
            self.hotkey_str = "<cmd>+h"
            modifiers = {"cmd"}
            keys = ["h"]

        key = keys[0]
        if len(key) == 1 and key.isalnum():
            vk = ord(key.upper())
        else:
            vk = special_vk.get(key)

        if vk is None:
            print(f"[Hotkey] Unsupported primary shortcut key '{key}', falling back to Win + H")
            self.hotkey_str = "<cmd>+h"
            modifiers = {"cmd"}
            vk = 0x48

        self._primary_modifiers = modifiers
        self._primary_vk = vk

    def _modifiers_match(self, win_down: bool, ctrl_down: bool, shift_down: bool, alt_down: bool) -> bool:
        required = self._primary_modifiers
        return (
            win_down == ("cmd" in required)
            and ctrl_down == ("ctrl" in required)
            and shift_down == ("shift" in required)
            and alt_down == ("alt" in required)
        )

    def get_display_name(self, hotkey_str: str = None) -> str:
        target = hotkey_str or self.hotkey_str
        parts = target.split("+")
        names = []
        for p in parts:
            p = p.strip().strip("<>").lower()
            if p == "cmd":
                names.append("Win")
            elif p == "ctrl":
                names.append("Ctrl")
            elif p == "alt":
                names.append("Alt")
            elif p == "shift":
                names.append("Shift")
            elif p == "space":
                names.append("Space")
            elif p == "esc":
                names.append("Esc")
            else:
                names.append(p.upper())
        return " + ".join(names)

    def _send_win_mask_key(self):
        """Sends Ctrl keystroke (down+up) to notify Windows Shell that Win was not pressed alone, preventing Start Menu."""
        try:
            VK_CONTROL = 0x11
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            pass

    def _win32_filter(self, msg, data):
        # Ignore synthetic/injected events to prevent feedback loops
        LLKHF_INJECTED = 0x00000010
        if getattr(data, "flags", 0) & LLKHF_INJECTED:
            return True

        is_down = (msg == WM_KEYDOWN or msg == WM_SYSKEYDOWN)
        is_up = (msg == 0x0101 or msg == 0x0105)  # WM_KEYUP or WM_SYSKEYUP
        vk = data.vkCode

        WIN_VKS = {0x5B, 0x5C}
        CTRL_VKS = {0x11, 0xA2, 0xA3}
        SHIFT_VKS = {0x10, 0xA0, 0xA1}
        ALT_VKS = {0x12, 0xA4, 0xA5}
        MODIFIER_VKS = WIN_VKS | CTRL_VKS | SHIFT_VKS | ALT_VKS

        # LowLevelKeyboardProc runs before GetAsyncKeyState is updated.
        # Track physical modifier events ourselves instead of querying async state.
        if vk in MODIFIER_VKS:
            if is_down:
                self._pressed_modifier_vks.add(vk)
            elif is_up:
                self._pressed_modifier_vks.discard(vk)

        # Low-level hooks can miss a release while the desktop/session changes.
        # Reconcile every *other* tracked modifier with the actual OS state.
        # Exclude vk because GetAsyncKeyState has not yet incorporated this event.
        for tracked_vk in tuple(self._pressed_modifier_vks):
            if tracked_vk == vk:
                continue
            try:
                if not (user32.GetAsyncKeyState(tracked_vk) & 0x8000):
                    self._pressed_modifier_vks.discard(tracked_vk)
            except Exception:
                pass

        self._win_pressed = any(v in self._pressed_modifier_vks for v in WIN_VKS)

        if vk in WIN_VKS and is_up:
            if not self._win_pressed and self._win_combo_active:
                self._win_combo_active = False
                self._send_win_mask_key()
            # NEVER suppress Win keyup; Windows must clear its modifier state.
            return True

        win_down = any(v in self._pressed_modifier_vks for v in WIN_VKS)
        ctrl_down = any(v in self._pressed_modifier_vks for v in CTRL_VKS)
        shift_down = any(v in self._pressed_modifier_vks for v in SHIFT_VKS)
        alt_down = any(v in self._pressed_modifier_vks for v in ALT_VKS)

        # 1. Match configured primary shortcut. Key repeat must never toggle twice.
        if vk == self._primary_vk:
            if is_down and self._primary_key_down:
                if self.listener:
                    self.listener.suppress_event()
                return False

            if is_up and self._primary_key_down:
                self._primary_key_down = False
                if "cmd" in self._primary_modifiers:
                    self._send_win_mask_key()
                if self.listener:
                    self.listener.suppress_event()
                return False

            if is_down and self._modifiers_match(win_down, ctrl_down, shift_down, alt_down):
                if not self._primary_key_down:
                    self._primary_key_down = True
                    if "cmd" in self._primary_modifiers:
                        self._win_combo_active = True
                        self._send_win_mask_key()
                    self.on_triggered()
                if self.listener:
                    self.listener.suppress_event()
                return False

        # 2. Always keep Ctrl + Shift + Space as the fallback shortcut.
        if vk == 0x20:
            if is_down and self._alt_combo_down:
                if self.listener:
                    self.listener.suppress_event()
                return False

            if is_up and self._alt_combo_down:
                self._alt_combo_down = False
                if self.listener:
                    self.listener.suppress_event()
                return False
            if is_down and ctrl_down and shift_down and not win_down and not alt_down:
                if not self._alt_combo_down:
                    self._alt_combo_down = True
                    self.on_triggered()
                if self.listener:
                    self.listener.suppress_event()
                return False

        # 3. Insert is push-to-talk: press starts, release stops.
        if vk == 0x2D and (self.on_ptt_start or self.on_ptt_stop):
            if not self._insert_down and (win_down or ctrl_down or (shift_down and not alt_down)):
                return True
            if not self._insert_down and alt_down and not self.on_ptt_start_language:
                return True
            if is_down and not self._insert_down:
                self._insert_down = True
                if self.on_ptt_start:
                    try:
                        if alt_down and self.on_ptt_start_language:
                            self.on_ptt_start_language("ar" if shift_down else "en")
                        else:
                            self.on_ptt_start()
                    except Exception:
                        pass
            elif is_up and self._insert_down:
                self._insert_down = False
                if self.on_ptt_stop:
                    try:
                        self.on_ptt_stop()
                    except Exception:
                        pass
            if self.listener:
                self.listener.suppress_event()
            return False

        # 4. Cancel only in the original dictation window. If we consume the
        # key-down, also consume its key-up even if cancellation completes first.
        if vk == 0x1B:
            if is_up:
                consumed = self._escape_down
                self._escape_down = False
                if consumed:
                    if self.listener:
                        self.listener.suppress_event()
                    return False
                return True
            is_active = self.is_active_predicate() if self.is_active_predicate else False
            if is_down and is_active:
                if not self._escape_down and self.on_cancel:
                    self._escape_down = True
                    self.on_cancel()
                if self.listener:
                    self.listener.suppress_event()
                return False

        return True

    def start(self):
        with self._lock:
            self.stop()
            self._pressed_modifier_vks.clear()
            self._win_pressed = False
            self._primary_key_down = False
            self._alt_combo_down = False
            self._escape_down = False
            self._insert_down = False
            try:
                try:
                    from .desktop_utils import attach_to_default_desktop
                except ImportError:
                    from desktop_utils import attach_to_default_desktop
                attach_to_default_desktop()

                self.listener = keyboard.Listener(win32_event_filter=self._win32_filter)
                self.listener.start()
                print(f"[Hotkey] Listening for: {self.get_display_name()} & Ctrl + Shift + Space; hold Insert for push-to-talk")
            except Exception as e:
                print(f"[Hotkey] Error starting keyboard hook: {e}")

    def update_hotkey(self, new_hotkey_str: str):
        self.hotkey_str = self._normalize_hotkey(new_hotkey_str)
        self._primary_key_down = False
        self._configure_primary_hotkey(self.hotkey_str)

    def stop(self):
        with self._lock:
            if self.listener:
                try:
                    self.listener.stop()
                except Exception:
                    pass
                self.listener = None
            self._pressed_modifier_vks.clear()
            self._win_pressed = False
            self._insert_down = False