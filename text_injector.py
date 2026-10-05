import time
import ctypes
import threading
import pyperclip

# Win32 Virtual Key Codes
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
user32.GetKeyboardLayout.argtypes = [ctypes.c_ulong]
user32.GetKeyboardLayout.restype = ctypes.c_void_p
kernel32 = ctypes.windll.kernel32
kernel32.GetLocaleInfoW.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_int]
kernel32.GetLocaleInfoW.restype = ctypes.c_int
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.GetClipboardSequenceNumber.restype = ctypes.c_ulong

class TextInjector:
    def __init__(self):
        self.last_foreground_hwnd = None
        self._inject_lock = threading.RLock()
        self._clipboard_generation = 0
        self._clipboard_restore_pending = False
        self._clipboard_original = None

    def capture_active_window(self):
        """Capture and return the active window before the floating UI appears."""
        try:
            self.last_foreground_hwnd = user32.GetForegroundWindow()
        except Exception:
            self.last_foreground_hwnd = None
        return self.last_foreground_hwnd

    def typing_language_for_window(self, target_hwnd):
        """Read the target window's active Windows input language."""
        try:
            if not target_hwnd or not user32.IsWindow(target_hwnd):
                return None
            thread_id = user32.GetWindowThreadProcessId(target_hwnd, None)
            if not thread_id:
                return None
            layout = user32.GetKeyboardLayout(thread_id)
            if not layout:
                return None
            # The low word of HKL is the Windows LANGID, including its region.
            # Ask Windows for its ISO language instead of maintaining a small
            # list of keyboard layouts ourselves.
            langid = int(layout) & 0xFFFF
            code_buffer = ctypes.create_unicode_buffer(16)
            if not kernel32.GetLocaleInfoW(langid, 0x59, code_buffer, len(code_buffer)):
                return None
            code = code_buffer.value.casefold().strip()
            code = {"fil": "tl", "nb": "no", "nn": "no", "iw": "he", "in": "id",
                    "jv": "jw"}.get(code, code)
            return code if code.isascii() and code.isalpha() and len(code) == 2 else None
        except (OSError, TypeError, ValueError, OverflowError):
            return None

    def restore_focus(self, target_hwnd=None):
        """Restore focus to a specific dictation target, if available."""
        hwnd = target_hwnd or self.last_foreground_hwnd
        if hwnd:
            try:
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.05)
            except Exception:
                pass

    def inject_text(
        self,
        text: str,
        preserve_clipboard: bool = True,
        target_hwnd=None,
        copy_on_failure: bool = True,
    ):
        """
        Insert text at the current cursor using clipboard paste (Ctrl+V).
        Clipboard copy/paste/restore is serialized so overlapping live chunks
        cannot treat a previous dictation fragment as the user's original clipboard.
        """
        if not text:
            return

        with self._inject_lock:
            target = target_hwnd or self.last_foreground_hwnd
            target_ok = bool(
                target
                and user32.IsWindow(target)
                and user32.GetForegroundWindow() == target
            )
            modifiers_down = any(
                user32.GetAsyncKeyState(vk) & 0x8000
                for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C)
            )
            if not target_ok or modifiers_down:
                if copy_on_failure:
                    self._clipboard_generation += 1
                    self._clipboard_restore_pending = False
                    self._clipboard_original = None
                    pyperclip.copy(text)
                return False

            if preserve_clipboard and not self._clipboard_restore_pending:
                try:
                    self._clipboard_original = pyperclip.paste()
                except Exception:
                    self._clipboard_original = None
                self._clipboard_restore_pending = True
            elif not preserve_clipboard:
                self._clipboard_restore_pending = False
                self._clipboard_original = None

            self._clipboard_generation += 1
            generation = self._clipboard_generation

            pyperclip.copy(text)
            sequence = user32.GetClipboardSequenceNumber()
            # Clipboard writes are synchronous on Windows; a tiny settle is
            # enough and avoids ~30 ms of artificial latency per live chunk.
            time.sleep(0.008)

            if user32.GetForegroundWindow() != target:
                if copy_on_failure:
                    # The caller explicitly wants the transcript left on the
                    # clipboard when focus becomes unsafe. Cancel any older
                    # restore so it cannot overwrite this fallback copy.
                    self._clipboard_generation += 1
                    self._clipboard_restore_pending = False
                    self._clipboard_original = None
                    pyperclip.copy(text)
                elif preserve_clipboard and self._clipboard_restore_pending:
                    original = self._clipboard_original
                    self._clipboard_restore_pending = False
                    self._clipboard_original = None
                    self._clipboard_generation += 1
                    if original is not None:
                        try:
                            pyperclip.copy(original)
                        except Exception:
                            pass
                return False

            user32.keybd_event(0x5B, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(0x5C, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.004)

            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(VK_V, 0, 0, 0)
            time.sleep(0.008)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

            if preserve_clipboard:
                def _restore():
                    time.sleep(0.25)
                    with self._inject_lock:
                        if generation != self._clipboard_generation:
                            return
                        original = self._clipboard_original
                        try:
                            still_owned = (
                                user32.GetClipboardSequenceNumber() == sequence
                                and pyperclip.paste() == text
                            )
                        except Exception:
                            still_owned = False
                        self._clipboard_restore_pending = False
                        self._clipboard_original = None
                        if still_owned and original is not None:
                            try:
                                pyperclip.copy(original)
                            except Exception:
                                pass

                threading.Thread(target=_restore, daemon=True).start()
            return True
