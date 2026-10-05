import sys
import os
import time
import re
import queue
import threading
import ctypes
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from screen_context import ScreenContext, apply_terms

ERROR_ALREADY_EXISTS = 183
_INSTANCE_MUTEX_NAME = "Local\\WinVoiceGroqSingleton"
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_int

def _acquire_single_instance_mutex(name: str = _INSTANCE_MUTEX_NAME):
    # ctypes keeps a thread-local copy of Win32 last-error only when the DLL
    # is opened with use_last_error=True. Clear/read that saved value around
    # CreateMutexW so unrelated Win32 calls cannot create a false duplicate.
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    last_error = ctypes.get_last_error()
    if not handle:
        raise OSError("Could not create WinVoice single-instance mutex")
    if last_error == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def _normalized_token(token: str) -> str:
    return "".join(ch for ch in token.casefold() if ch.isalnum())


def _overlap_count(previous_text: str, current_text: str) -> int:
    previous_words = (previous_text or "").split()
    current_words = (current_text or "").split()
    max_overlap = min(18, len(previous_words), len(current_words))

    for count in range(max_overlap, 1, -1):
        left = [_normalized_token(x) for x in previous_words[-count:]]
        right = [_normalized_token(x) for x in current_words[:count]]
        if all(left) and left == right:
            return count

    return 0


def _dedupe_overlap(previous_text: str, current_text: str) -> str:
    """Return only the non-overlapping suffix of current_text."""
    current_text = (current_text or "").strip()
    count = _overlap_count(previous_text, current_text)
    if not count:
        return current_text
    return " ".join(current_text.split()[count:]).strip()


def _merge_overlap(previous_text: str, current_text: str) -> str:
    """Merge an exact overlap without rewriting already committed text."""
    previous_text = (previous_text or "").strip()
    current_text = (current_text or "").strip()
    if not previous_text:
        return current_text
    if not current_text:
        return previous_text

    count = _overlap_count(previous_text, current_text)
    if not count:
        return (previous_text + " " + current_text).strip()

    remainder = " ".join(current_text.split()[count:]).strip()
    if not remainder:
        return previous_text

    # Monotonic output is deliberate: once a completed chunk has been typed
    # into another app, later overlap handling must never mutate that prefix.
    return (previous_text + " " + remainder).strip()


def _append_only_suffix(committed_text: str, full_text: str):
    """Return the suffix that can be appended without changing committed text."""
    committed = (committed_text or "").strip()
    full = (full_text or "").strip()
    if not committed:
        return full
    if full == committed:
        return ""
    prefix = committed + " "
    if full.startswith(prefix):
        return full[len(prefix):]
    return None


def _stable_live_prefix(text: str, holdback_words: int = 2) -> str:
    """Keep a short unstable tail untyped so the next chunk can resolve boundaries."""
    words = (text or "").strip().split()
    holdback = max(0, int(holdback_words))
    if not words:
        return ""
    if holdback == 0:
        return " ".join(words).strip()
    if len(words) <= holdback:
        return ""
    return " ".join(words[:-holdback]).strip()


_SENTENCE_START_RE = re.compile(
    r'(^|[.!?؟]\s+)(["“”\'‘’(\[]*)([A-Z][A-Za-z\'’\-]*)',
    re.MULTILINE,
)
_SENTENCE_PERIOD_RE = re.compile(r'\.+(?=(?:["”’\)\]]*)?(?:\s|$))')


def _apply_dictation_style(text: str, config) -> str:
    """Apply the user's deterministic casing/punctuation preferences."""
    value = (text or "").strip()
    if not value:
        return ""

    if config.get("lowercase_sentence_starts", True):
        def lower_start(match):
            word = match.group(3)
            # Preserve acronyms and intentional mixed-case names (API, GitHub,
            # OpenAI). Ordinary sentence-initial words are lowercased.
            if len(word) > 1 and any(ch.isupper() for ch in word[1:]):
                replacement = word
            else:
                replacement = word[:1].lower() + word[1:]
            return match.group(1) + match.group(2) + replacement

        value = _SENTENCE_START_RE.sub(lower_start, value)

    if config.get("strip_sentence_periods", True):
        # Remove sentence punctuation periods only. Dots inside decimals,
        # domains, filenames and abbreviations followed immediately by text
        # are untouched.
        value = _SENTENCE_PERIOD_RE.sub("", value)

    return value.strip()


def _script_profile(text: str) -> tuple[bool, bool, int]:
    """Return Arabic/Latin presence plus a rough lexical token count."""
    value = text or ""
    has_ar = any("\u0600" <= ch <= "\u06ff" for ch in value)
    has_lat = any(("a" <= ch.casefold() <= "z") for ch in value)
    words = [
        token for token in value.split()
        if any(ch.isalpha() for ch in token)
    ]
    return has_ar, has_lat, len(words)


def _strong_script_language(text: str) -> str | None:
    """Return ar/en only for 3+ word, single-script transcript evidence."""
    has_ar, has_lat, words = _script_profile(text)
    if words < 3 or has_ar == has_lat:
        return None
    return "ar" if has_ar else "en"


def _apply_live_corrections(text: str, corrections) -> str:
    """Reapply accepted prefix corrections to a later raw transcript view."""
    value = (text or "").strip()
    for before, after in corrections or ():
        before = (before or "").strip()
        after = (after or "").strip()
        if not before or before == after:
            continue
        if value == before:
            value = after
        elif value.startswith(before + " "):
            value = after + value[len(before):]
    return value


# Persist runtime diagnostics. pythonw.exe has no stdout/stderr console, so
# previous failures vanished completely. Keep one small rotated log and tee to
# an attached console when one exists.
root_dir = Path(__file__).resolve().parent
_log_dir = root_dir / "logs"
_log_dir.mkdir(exist_ok=True)
_log_path = _log_dir / "runtime.log"
try:
    if _log_path.exists() and _log_path.stat().st_size > 2_000_000:
        backup = _log_dir / "runtime.log.1"
        try:
            backup.unlink(missing_ok=True)
        except Exception:
            pass
        _log_path.replace(backup)
except Exception:
    pass

class _RuntimeTee:
    def __init__(self, original, path):
        self.original = original
        self.file = open(path, "a", encoding="utf-8", buffering=1)
        self.lock = threading.Lock()
        self.encoding = "utf-8"

    def write(self, value):
        if not value:
            return 0
        with self.lock:
            try:
                self.file.write(value)
            except Exception:
                pass
            if self.original is not None:
                try:
                    self.original.write(value)
                except Exception:
                    pass
        return len(value)

    def flush(self):
        with self.lock:
            try:
                self.file.flush()
            except Exception:
                pass
            if self.original is not None:
                try:
                    self.original.flush()
                except Exception:
                    pass

try:
    sys.stdout = _RuntimeTee(sys.stdout, _log_path)
    sys.stderr = _RuntimeTee(sys.stderr, _log_path)
    print(
        f"\n--- WinVoice runtime start {datetime.now().isoformat(timespec='seconds')} "
        f"pid={os.getpid()} ---",
        flush=True,
    )
except Exception:
    pass

# Ensure root directory is in sys.path
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# CRITICAL: Attach calling thread to interactive 'Default' desktop BEFORE importing Tkinter/GUI
try:
    from .desktop_utils import attach_to_default_desktop
except ImportError:
    from desktop_utils import attach_to_default_desktop
attach_to_default_desktop()

try:
    from .config_manager import ConfigManager
    from .audio_recorder import AudioRecorder
    from .groq_transcriber import GroqTranscriber, TranscriptLanguageError
    from .text_injector import TextInjector
    from .ui_pill import VoicePillWindow
    from .hotkey_listener import HotkeyListener
    from .tray_manager import TrayManager
    from .startup_manager import is_start_with_windows_enabled, set_start_with_windows
except ImportError:
    from config_manager import ConfigManager
    from audio_recorder import AudioRecorder
    from groq_transcriber import GroqTranscriber, TranscriptLanguageError
    from text_injector import TextInjector
    from ui_pill import VoicePillWindow
    from hotkey_listener import HotkeyListener
    from tray_manager import TrayManager
    from startup_manager import is_start_with_windows_enabled, set_start_with_windows

class WinVoiceApp:
    def __init__(self):
        self._instance_mutex = _acquire_single_instance_mutex()
        if self._instance_mutex is None:
            print("[WinVoice] Another instance is already running.", flush=True)
            raise SystemExit(0)

        self.config_manager = ConfigManager()
        self.recorder = AudioRecorder(sample_rate=16000)
        self.transcriber = GroqTranscriber(api_key=self.config_manager.get("groq_api_key", ""))
        self.transcriber.prewarm_async(force=True)
        self.text_injector = TextInjector()

        self.is_dictating = False
        self._action_lock = threading.Lock()
        self._last_toggle_at = 0.0
        self.event_queue = queue.Queue()

        self._ptt_active = False
        self._chunking_active = False

        # Reliable background chunking. Chunks are transcribed while the user
        # speaks, but only the final merged transcript is inserted.
        self._cancelled_through = 0
        self._pending_sessions = set()
        self.screen_context = ScreenContext()
        self._chunk_session = 0
        self._chunk_queue = queue.Queue()
        self._chunk_states = {}
        # Only audio with an established language is prefetched. The ordered
        # worker still validates and inserts each result in recording order.
        self._asr_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="winvoice-asr")
        self._chunk_last_voice_at = 0.0
        self._chunk_speech_seen = False
        self._chunk_soft_ready_at = 0.0
        self._chunk_buffer_has_overlap = False
        self._chunk_first_cut = True
        self._chunk_worker_thread = threading.Thread(
            target=self._chunk_worker_loop,
            daemon=True,
        )
        self._chunk_worker_thread.start()

        # Build UI pill widget (Windows 11 Voice Typing style)
        self.ui_pill = VoicePillWindow(
            self.config_manager,
            on_mic_toggle=self.toggle_dictation,
            on_settings_change=self.on_settings_updated,
            on_cancel=self.cancel_dictation
        )

        # Tray icon
        self.tray = TrayManager(
            on_toggle_dictation=lambda: self.event_queue.put(("toggle", "tray")),
            on_open_settings=lambda: self.event_queue.put(("settings", "tray")),
            on_quit=lambda: self.event_queue.put(("quit", "tray"))
        )

        # Global hotkey listener (Win + H default + Esc cancellation + Win11 suppression)
        self.hotkey = HotkeyListener(
            self.config_manager.get("hotkey", "<cmd>+h"),
            on_triggered=lambda: self.event_queue.put(("toggle", "hotkey")),
            on_cancel=lambda: self.event_queue.put(("cancel", "hotkey")),
            is_active_predicate=self._should_intercept_escape,
            on_ptt_start=lambda: self.event_queue.put("ptt_start"),
            on_ptt_stop=lambda: self.event_queue.put("ptt_stop"),
            on_ptt_start_language=lambda lang: self.event_queue.put(("ptt_start", lang)),
        )

    def start(self):
        print("==================================================", flush=True)
        print("  Sadh (صَدْح) — Voice Typing  ", flush=True)
        print("==================================================", flush=True)
        print(f"• Hotkey:     {self.hotkey.get_display_name()} ({self.config_manager.get('hotkey')})", flush=True)
        print(f"• Model:      {self.config_manager.get('model')}", flush=True)
        print(f"• Language:   {self.config_manager.get('language')}", flush=True)
        print(f"• Dialect:    {self.config_manager.get('dialect')}", flush=True)
        print(f"• API Key:    {'Configured' if self.config_manager.get('groq_api_key') else 'NOT SET'}", flush=True)
        print(f"• Autostart:  {'ENABLED' if self.config_manager.get('start_with_windows', False) else 'DISABLED'}", flush=True)
        print("==================================================", flush=True)

        # Sync startup name and value with the saved preference.
        set_start_with_windows(bool(self.config_manager.get("start_with_windows", False)))

        # Start local IPC trigger socket
        self._start_trigger_server()

        self.tray.start()
        self.hotkey.start()

        # Start periodic Tkinter event loop watchers
        self._schedule_level_update()
        self._poll_event_queue()

        # If API key is not set on first launch, open settings directly
        if not self.config_manager.get("groq_api_key"):
            print("[WinVoice] No Groq API Key found. Opening settings modal...", flush=True)
            self.ui_pill.root.after(300, self.ui_pill.open_settings)

        # Start Tkinter event loop
        try:
            self.ui_pill.root.mainloop()
        except KeyboardInterrupt:
            self.quit()

    def _poll_event_queue(self):
        while not self.event_queue.empty():
            try:
                item = self.event_queue.get_nowait()
            except queue.Empty:
                break

            try:
                if isinstance(item, tuple):
                    cmd, payload = item
                else:
                    cmd, payload = item, None

                if cmd == "toggle":
                    self.toggle_dictation(source=payload or "event")
                elif cmd == "ptt_start":
                    self.start_push_to_talk(payload)
                elif cmd == "ptt_stop":
                    self.stop_push_to_talk()
                elif cmd == "cancel":
                    self.cancel_dictation()
                elif cmd == "settings":
                    self.ui_pill.open_settings()
                elif cmd == "chunk_session_done":
                    session_id, text, latency, used_fallback, target, *extra = payload
                    meta = extra[0] if extra and isinstance(extra[0], dict) else {}
                    live_committed = meta.get("live_committed", "")
                    self._pending_sessions.discard(session_id)
                    if session_id <= self._cancelled_through:
                        continue
                    if text and self.config_manager.get("auto_paste", True):
                        if live_committed:
                            suffix = _append_only_suffix(live_committed, text)
                            if suffix is None:
                                import pyperclip
                                pyperclip.copy(text)
                                self.tray.notify(
                                    "Recovery transcript copied. Live text was kept unchanged."
                                )
                            elif suffix:
                                pasted = self.text_injector.inject_text(
                                    suffix,
                                    preserve_clipboard=True,
                                    target_hwnd=target,
                                    copy_on_failure=False,
                                )
                                if not pasted:
                                    import pyperclip
                                    pyperclip.copy(text)
                                    self.tray.notify(
                                        "Full transcript copied. Focus the destination and replace the live text if needed."
                                    )
                        else:
                            pasted = self.text_injector.inject_text(
                                text,
                                preserve_clipboard=True,
                                target_hwnd=target,
                            )
                            if not pasted:
                                self.tray.notify(
                                    "Transcript copied. Focus the destination and paste it."
                                )
                    elif text:
                        import pyperclip
                        pyperclip.copy(text)
                    stopped_at = meta.get("stopped_at")
                    post_stop = (
                        max(0.0, time.monotonic() - stopped_at)
                        if stopped_at is not None else None
                    )
                    print(
                        f"[WinVoice] Chunk session {session_id} finalized "
                        f"api_total={latency:.2f}s"
                        + (f" post_stop={post_stop:.2f}s" if post_stop is not None else "")
                        + (" (full-audio fallback)." if used_fallback else "."),
                        flush=True,
                    )
                    if session_id == self._chunk_session and not self.is_dictating:
                        self.ui_pill.set_state("idle")
                        self.ui_pill.hide()
                elif cmd == "chunk_session_error":
                    session_id, message = payload
                    self._pending_sessions.discard(session_id)
                    if session_id <= self._cancelled_through:
                        continue
                    self.tray.notify("Dictation failed. Please try again.")
                    print(f"[WinVoice] Chunk session {session_id} failed: {message}", flush=True)
                    if session_id == self._chunk_session and not self.is_dictating:
                        self.ui_pill.set_state("idle")
                        self.ui_pill.hide()
                elif cmd == "quit":
                    self.quit()
                    return
            except Exception as e:
                print(f"[WinVoice] Event handler error ({cmd}): {e}", flush=True)

        self.ui_pill.root.after(10, self._poll_event_queue)

    def _start_trigger_server(self):
        def _server():
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind(("127.0.0.1", 45454))
                while True:
                    data, _ = sock.recvfrom(1024)
                    cmd = data.decode("utf-8", errors="ignore").strip().lower()
                    if cmd in ("toggle", "cancel", "settings"):
                        self.event_queue.put((cmd, "ipc"))
            except Exception as e:
                print(f"[WinVoice] Trigger server note: {e}", flush=True)

        threading.Thread(target=_server, daemon=True).start()

    def _should_intercept_escape(self):
        """Cancel only while the original dictation window is still focused."""
        if not self.is_dictating and not self._pending_sessions:
            return False
        try:
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            foreground = user32.GetForegroundWindow()
            return bool(foreground and any(
                self._chunk_states.get(sid, {}).get("target") == foreground
                for sid in self._pending_sessions
                if sid > self._cancelled_through
            ))
        except Exception:
            return False

    def _schedule_level_update(self):
        if self.is_dictating:
            level = self.recorder.current_level
            self.ui_pill.set_audio_level(level)
            state = self._chunk_states.get(self._chunk_session, {})
            language = state.get("session_language") or "auto"
            self.ui_pill.language_hint = {
                "en": "English", "ar": "Arabic", "auto": "Auto language",
            }.get(language, language.upper())
            if time.monotonic() - self._recording_started >= 300:
                self._stop_listening_and_transcribe()
                return self.ui_pill.root.after(20, self._schedule_level_update)
            if self._chunking_active:
                self._maybe_cut_chunk(level)
        self.ui_pill.root.after(20, self._schedule_level_update)

    def toggle_dictation(self, source="ui"):
        with self._action_lock:
            now = time.monotonic()
            # Low-level keyboard hooks, tray callbacks and IPC can occasionally
            # deliver a duplicate edge. Ignore only near-simultaneous repeats;
            # normal start/stop toggles remain unaffected.
            if now - self._last_toggle_at < 0.22:
                print(
                    f"[WinVoice] Ignored duplicate toggle source={source}",
                    flush=True,
                )
                return
            self._last_toggle_at = now
            action = "stop" if self.is_dictating else "start"
            print(
                f"[WinVoice] Toggle source={source} action={action}",
                flush=True,
            )
            if not self.is_dictating:
                self._start_listening()
            else:
                self._stop_listening_and_transcribe()

    def start_push_to_talk(self, language=None):
        with self._action_lock:
            if not self.is_dictating:
                self._ptt_active = True
                self._start_listening(language_override=language)

    def stop_push_to_talk(self):
        with self._action_lock:
            if self._ptt_active:
                self._ptt_active = False
                if self.is_dictating:
                    self._stop_listening_and_transcribe()

    def _start_chunk_session(self, target_hwnd):
        self._chunk_session += 1
        session_id = self._chunk_session
        cfg = dict(self.config_manager.config)
        self._chunk_states[session_id] = {
            "target": target_hwnd,
            "parts": [],
            "failed": False,
            "cancelled": False,
            "latency": 0.0,
            "config": cfg,
            "live_committed": "",
            "live_corrections": [],
            # Auto mode is allowed to choose a language only once. After the
            # first confident chunk, every later chunk in this recording is
            # explicitly pinned to that same language.
            "session_language": (
                cfg.get("language")
                if cfg.get("language") in ("ar", "en")
                else None
            ),
            # Auto-language detection is launched early while the user is still
            # speaking. The probe's transcript is never typed; it only supplies
            # a language lock so the real chunk can be sent explicitly.
            "language_lock": threading.Lock(),
            "language_probe_started": False,
            "language_probe_attempts": 0,
            "language_probe_event": threading.Event(),
            "live_enabled": bool(
                cfg.get("live_typing", True)
                and cfg.get("reliable_chunking", True)
                and cfg.get("auto_paste", True)
            ),
        }
        self._pending_sessions.add(session_id)
        self._chunk_last_voice_at = 0.0
        self._chunk_speech_seen = False
        self._chunk_soft_ready_at = time.monotonic() + 0.30
        self._chunk_buffer_has_overlap = False
        self._chunk_first_cut = True
        return session_id

    def _queue_chunk(
        self,
        wav_bytes,
        *,
        final=False,
        begins_with_overlap=False,
        full_session_wav=None,
    ):
        if not final and self._chunk_queue.qsize() >= 8:
            self._chunk_states[self._chunk_session]["failed"] = True
            return
        state = self._chunk_states.get(self._chunk_session)
        executor = getattr(self, "_asr_executor", None)
        prefetch = None
        # In one-shot Auto mode, verify the spoken language with a different
        # Whisper model. Start both decodes together to avoid extra wait time.
        if (
            wav_bytes and state and not state.get("failed") and executor
            and final and not state["config"].get("reliable_chunking", True)
            and state["config"].get("language", "auto") == "auto"
            and state.get("session_language") not in ("ar", "en")
        ):
            reference_model = (
                "whisper-large-v3"
                if state["config"].get("model") != "whisper-large-v3"
                else "whisper-large-v3-turbo"
            )
            reference_cfg = dict(state["config"])
            reference_cfg.update({
                "model": reference_model, "language": "auto",
                "custom_prompt": "",
            })
            try:
                state["auto_reference_future"] = executor.submit(
                    self._transcribe_with_retry, wav_bytes, state,
                    config_override=reference_cfg, mark_permanent=False,
                )
            except RuntimeError:
                pass
        if (
            wav_bytes and state and not state.get("failed") and executor
            and state.get("session_language")
            and (
                state["config"].get("language") == state.get("session_language")
                or state.get("language_probe_verified")
                or state.get("first_chunk_verified")
            )
        ):
            try:
                prefetch = executor.submit(self._transcribe_with_retry, wav_bytes, state)
            except RuntimeError:
                pass  # Shutting down; the ordered worker can still transcribe.
        self._chunk_queue.put((
            self._chunk_session,
            (wav_bytes, prefetch) if prefetch else wav_bytes,
            bool(final),
            bool(begins_with_overlap),
            full_session_wav,
        ))

    def _cut_chunk(self, overlap_seconds=0.0):
        begins_with_overlap = self._chunk_buffer_has_overlap
        try:
            wav_bytes = self.recorder.take_chunk(
                overlap_seconds=overlap_seconds,
                require_speech=True,
                ignore_leading_seconds=0.20 if self._chunk_first_cut else 0.0,
            )
        except Exception as e:
            print(f"[WinVoice] Chunk capture note: {e}", flush=True)
            wav_bytes = None
            self._chunk_states[self._chunk_session]["failed"] = True

        self._chunk_first_cut = False
        if wav_bytes:
            self._queue_chunk(
                wav_bytes,
                final=False,
                begins_with_overlap=begins_with_overlap,
            )

        self._chunk_buffer_has_overlap = overlap_seconds > 0.0
        self._chunk_speech_seen = False
        self._chunk_last_voice_at = 0.0

    def _maybe_start_language_probe(self, state, buffered: float):
        """Detect Arabic/English early without ever using probe text as output."""
        cfg = state.get("config", {})
        attempts = int(state.get("language_probe_attempts", 0))
        threshold = 1.05 if attempts == 0 else 2.20
        if (
            cfg.get("language", "auto") != "auto"
            or state.get("session_language") in ("ar", "en")
            or state.get("language_probe_started")
            or attempts >= 2
            or buffered < threshold
        ):
            return

        wav_bytes = self.recorder.peek_chunk(
            require_speech=True,
            ignore_leading_seconds=0.12,
        )
        if not wav_bytes:
            return

        state["language_probe_started"] = True
        state["language_probe_attempts"] = attempts + 1
        event = threading.Event()
        state["language_probe_event"] = event
        probe_cfg = dict(cfg)

        def work():
            try:
                self.transcriber.api_key = probe_cfg.get("groq_api_key", "")
                result = self.transcriber.transcribe(
                    wav_bytes=wav_bytes,
                    model="whisper-large-v3",
                    language="auto",
                    dialect="none",
                    custom_prompt="",
                )
                if state.get("cancelled") or result.get("filtered_no_speech"):
                    return

                detected = GroqTranscriber._normalized_language(
                    result.get("detected_language")
                )
                text = result.get("text", "").strip()
                _, _, word_count = GroqTranscriber._script_profile(text)
                score = result.get("avg_logprob")
                no_speech = result.get("max_no_speech_prob")
                state["language_probe_score"] = score

                # Probe text is detection-only and is always discarded. Lock
                # only when Whisper's language metadata, writing system and
                # acoustic confidence all agree. Ambiguous probes simply cause
                # another probe with more audio or the normal fail-closed path.
                confident = (
                    detected in ("ar", "en")
                    and word_count >= 2
                    and score is not None
                    and float(score) >= -0.35
                    and (no_speech is None or float(no_speech) < 0.40)
                    and not result.get("suspected_language_mismatch")
                    and self.transcriber.transcript_matches_language(
                        detected, text
                    )
                )
                if confident:
                    with state["language_lock"]:
                        current = state.get("session_language")
                        if current not in ("ar", "en"):
                            state["session_language"] = detected
                            state["language_probe_verified"] = True
            except Exception:
                # Probe failure must never break dictation. The main chunk path
                # can still detect/recover the language from more audio.
                pass
            finally:
                state["language_probe_started"] = False
                event.set()

        threading.Thread(
            target=work,
            daemon=True,
            name=f"language-probe-{self._chunk_session}",
        ).start()

    def _maybe_cut_chunk(self, level: float):
        now = time.monotonic()
        if now < self._chunk_soft_ready_at:
            return

        buffered = self.recorder.buffered_duration()
        state = self._chunk_states.get(self._chunk_session) or {}
        language_locked = bool(state.get("session_language") and state.get("session_language") != "auto")
        min_seconds = max(
            1.2,
            float(self.config_manager.get("chunk_min_seconds", 1.6)),
        )
        # Give Auto a little more audio only until its one-time language lock;
        # once locked, shorter phrases can be shipped immediately.
        if not language_locked:
            # Give the parallel Arabic-vs-English probe enough acoustic context.
            # This happens while speech continues, so it improves reliability
            # without adding the same delay after key-up.
            min_seconds = max(2.05, min_seconds)

        voice_on = float(self.config_manager.get("chunk_voice_on", 0.035))
        voice_off = float(self.config_manager.get("chunk_voice_off", 0.018))
        pause_seconds = max(
            0.20,
            float(self.config_manager.get("chunk_pause_ms", 220)) / 1000.0,
        )
        configured_soft = float(
            self.config_manager.get("chunk_soft_max_seconds", 2.8)
        )
        configured_hard = float(
            self.config_manager.get("chunk_hard_max_seconds", 4.0)
        )
        soft_max = max(
            min_seconds + 0.65,
            configured_soft + (0.35 if not language_locked else 0.0),
        )
        hard_max = max(
            soft_max + 0.75,
            configured_hard + (0.35 if not language_locked else 0.0),
        )
        overlap_seconds = max(
            0.55,
            min(0.80, float(self.config_manager.get("chunk_overlap_ms", 650)) / 1000.0),
        )

        if level >= voice_on:
            self._chunk_speech_seen = True
            self._chunk_last_voice_at = now

        if self._chunk_speech_seen and not language_locked:
            self._maybe_start_language_probe(state, buffered)
            language_locked = bool(state.get("session_language") and state.get("session_language") != "auto")

        if buffered >= hard_max:
            self._cut_chunk(overlap_seconds=overlap_seconds if level > voice_off else 0.0)
            return
        if buffered < min_seconds or level >= voice_on:
            return

        if not self._chunk_speech_seen or not self._chunk_last_voice_at:
            return

        quiet_for = now - self._chunk_last_voice_at

        # Natural pause: safest boundary, no overlap required.
        if level <= voice_off and quiet_for >= pause_seconds:
            self._cut_chunk(overlap_seconds=0.0)
            return

        # After the soft maximum, accept a short micro-pause but preserve a
        # small overlap to protect the word/phrase boundary.
        if buffered >= soft_max and level < voice_on and quiet_for >= 0.18:
            self._cut_chunk(overlap_seconds=min(0.65, overlap_seconds))

    def _transcribe_with_retry(
        self,
        wav_bytes,
        state=None,
        *,
        config_override=None,
        mark_permanent=True,
    ):
        state = state or {}
        cfg = config_override or state.get("config", self.config_manager)
        # One retry is enough for transient 429/5xx/network failures.
        # A second retry can add seconds of dead time; retained full-session audio
        # already provides the reliability fallback.
        retries = min(1, max(0, int(cfg.get("chunk_retries", 1))))
        for attempt in range(retries + 1):
            if state.get("cancelled"):
                raise RuntimeError("Cancelled")
            try:
                self.transcriber.api_key = cfg.get("groq_api_key", "")
                requested_language = cfg.get("language", "auto")
                # Give the early probe a very small grace period. In the common
                # case it finishes while the user is still speaking, removing
                # the detect-then-retranscribe delay from the live chunk path.
                if (
                    config_override is None
                    and requested_language == "auto"
                    and state.get("session_language") not in ("ar", "en")
                    and state.get("language_probe_started")
                ):
                    probe_event = state.get("language_probe_event")
                    if probe_event and not probe_event.is_set():
                        # Most warmed Groq calls finish near the time the first
                        # chunk becomes ready. A short grace period is cheaper
                        # than launching an extra Auto + explicit round trip.
                        probe_event.wait(0.55)

                # A config_override is an intentional diagnostic/recovery call
                # and must keep its own language setting. Normal Auto chunks,
                # including full-session fallback, inherit the per-recording lock.
                if (
                    config_override is None
                    and requested_language == "auto"
                    and state.get("session_language") in ("ar", "en")
                ):
                    requested_language = state["session_language"]
                result = self.transcriber.transcribe(
                    wav_bytes=wav_bytes, model=cfg.get("model", "whisper-large-v3-turbo"),
                    language=requested_language, dialect=cfg.get("dialect", "gazan"),
                    custom_prompt=cfg.get("custom_prompt", ""),
                )
                if isinstance(result, dict):
                    result = dict(result)
                    result.setdefault("requested_language", requested_language)
                return result
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if isinstance(exc, TranscriptLanguageError):
                    # Bad ASR language output is recoverable from the retained
                    # full-session audio. It is never a permanent API/config error.
                    raise
                permanent = isinstance(exc, ValueError) or (
                    status and status not in (408, 429) and status < 500
                )
                if permanent:
                    if mark_permanent:
                        state["permanent_error"] = True
                    raise
                if attempt == retries or state.get("cancelled"):
                    raise
                deadline = time.monotonic() + max(
                    0.2 * (attempt + 1), getattr(exc, "retry_after", 0)
                )
                while time.monotonic() < deadline:
                    if state.get("cancelled"):
                        raise RuntimeError("Cancelled")
                    time.sleep(0.05)

    def _guard_auto_chunk_language(self, wav_bytes, result, state):
        """Prefer a validated large-v3 decode; never output an unsafe language."""
        cfg = state.get("config", self.config_manager)
        if cfg.get("language", "auto") != "auto":
            return result
        if result.get("filtered_no_speech") and not state.get("auto_reference_future"):
            return result

        locked = state.get("session_language")
        detected = GroqTranscriber._normalized_language(
            result.get("detected_language")
        )
        text = result.get("text", "").strip()

        if locked in ("ar", "en"):
            reference_future = state.pop("auto_reference_future", None)
            if reference_future:
                reference_future.cancel()
            # A probe may finish while an already-started Auto request is in
            # flight. Never let that Auto transcript leak through merely because
            # its detected language happens to match the new lock: re-run the
            # exact audio explicitly in the verified language first.
            requested = result.get(
                "requested_language", result.get("language", "auto")
            )
            if requested == "auto":
                retry_cfg = dict(cfg)
                retry_cfg.update({
                    "language": locked,
                    "custom_prompt": "",
                })
                try:
                    explicit = self._transcribe_with_retry(
                        wav_bytes,
                        state,
                        config_override=retry_cfg,
                        mark_permanent=False,
                    )
                except Exception:
                    flagged = dict(result)
                    flagged["suspected_language_mismatch"] = True
                    flagged["language_guard"] = "locked_auto_race_failed"
                    return flagged
                explicit = dict(explicit)
                explicit["latency"] = (
                    float(result.get("latency", 0.0))
                    + float(explicit.get("latency", 0.0))
                )
                explicit["language_guard"] = "probe_lock_then_explicit"
                return self._guard_auto_chunk_language(wav_bytes, explicit, state)

            # Defense in depth for all later explicit chunks.
            if (
                requested != locked
                or (detected and detected != locked)
                or result.get("suspected_language_mismatch")
                or not self.transcriber.transcript_matches_language(locked, text)
            ):
                flagged = dict(result)
                flagged["suspected_language_mismatch"] = True
                flagged["language_guard"] = "locked_language_changed"
                return flagged

            # Low confidence alone is not a failure. Verify the same audio once
            # in Auto mode; accept the explicit transcript only when Auto agrees
            # on the locked language. This preserves the no-translation rail
            # without rejecting noisy-but-correct speech.
            if result.get("low_confidence"):
                verify_cfg = dict(cfg)
                verify_cfg.update({
                    "language": "auto",
                    "model": (
                        "whisper-large-v3"
                        if cfg.get("model") != "whisper-large-v3"
                        else "whisper-large-v3-turbo"
                    ),
                    "custom_prompt": "",
                })
                try:
                    verification = self._transcribe_with_retry(
                        wav_bytes,
                        state,
                        config_override=verify_cfg,
                        mark_permanent=False,
                    )
                except Exception:
                    flagged = dict(result)
                    flagged["suspected_language_mismatch"] = True
                    flagged["language_guard"] = "low_confidence_verify_failed"
                    return flagged

                verification = dict(verification)
                verify_detected = GroqTranscriber._normalized_language(
                    verification.get("detected_language")
                )
                verify_text = verification.get("text", "").strip()
                if (
                    verify_detected != locked
                    or verification.get("suspected_language_mismatch")
                    or not self.transcriber.transcript_matches_language(
                        locked, verify_text
                    )
                ):
                    flagged = dict(result)
                    flagged["suspected_language_mismatch"] = True
                    flagged["language_guard"] = "low_confidence_language_disagreed"
                    return flagged

                accepted = dict(result)
                accepted["low_confidence"] = False
                accepted["language_guard"] = "low_confidence_auto_verified"
                accepted["latency"] = (
                    float(result.get("latency", 0.0))
                    + float(verification.get("latency", 0.0))
                )
                return accepted

            return result

        reference_future = state.pop("auto_reference_future", None)
        if detected not in ("ar", "en") and not reference_future:
            _, _, words = GroqTranscriber._script_profile(text)
            if words < 2:
                flagged = dict(result)
                flagged["suspected_language_mismatch"] = True
                flagged["language_guard"] = "unsupported_short_initial_language"
                return flagged
        # A forced-language pass echoes its own language hint and can translate
        # when that hint is wrong. Compare unbiased Auto decodes instead.
        reference_model = (
            "whisper-large-v3"
            if cfg.get("model") != "whisper-large-v3"
            else "whisper-large-v3-turbo"
        )
        reference_cfg = dict(cfg)
        reference_cfg.update({
            "model": reference_model, "language": "auto",
            "custom_prompt": "",
        })
        try:
            reference = (
                reference_future.result()
                if reference_future else self._transcribe_with_retry(
                    wav_bytes, state, config_override=reference_cfg,
                    mark_permanent=False,
                )
            )
        except Exception:
            flagged = dict(result)
            flagged["suspected_language_mismatch"] = True
            flagged["language_guard"] = "auto_reference_failed"
            return flagged

        reference = dict(reference)
        large_v3 = reference if reference_model == "whisper-large-v3" else result
        large_v3 = dict(large_v3)
        large_v3_language = GroqTranscriber._normalized_language(
            large_v3.get("detected_language")
        )
        large_v3_text = large_v3.get("text", "").strip()
        if result.get("filtered_no_speech") and large_v3.get("filtered_no_speech"):
            return large_v3

        large_v3_safe = (
            bool(large_v3_text)
            and large_v3_language in ("ar", "en")
            and not large_v3.get("filtered_no_speech")
            and not large_v3.get("suspected_language_mismatch")
            and self.transcriber.transcript_matches_language(
                large_v3_language, large_v3_text
            )
        )
        if not large_v3_safe:
            flagged = dict(large_v3)
            flagged["suspected_language_mismatch"] = True
            flagged["language_guard"] = "large_v3_language_unsafe"
            return flagged

        primary_safe = (
            bool(text) and detected in ("ar", "en")
            and not result.get("filtered_no_speech")
            and not result.get("suspected_language_mismatch")
            and self.transcriber.transcript_matches_language(detected, text)
        )
        models_agree = primary_safe and detected == large_v3_language
        # A normal transcription can choose different words or spellings;
        # comparing whole strings caused legitimate Arabic speech to fail.
        # On a language conflict, only a confident unbiased large-v3 result
        # may override turbo. Never force the conflicting language as a hint.
        score = large_v3.get("avg_logprob")
        if not models_agree and (score is None or float(score) < -0.75):
            flagged = dict(large_v3)
            flagged["suspected_language_mismatch"] = True
            flagged["language_guard"] = "large_v3_conflict_uncertain"
            return flagged

        if not models_agree:
            print(
                "[WinVoice] Auto language resolved by large-v3 "
                f"initial={detected or 'unknown'} "
                f"large_v3={large_v3_language} avg_logprob={score}",
                flush=True,
            )
        accepted = dict(large_v3)
        accepted["latency"] = (
            float(result.get("latency", 0.0))
            + float(reference.get("latency", 0.0))
        )
        accepted["language_guard"] = (
            "large_v3_resolved_conflict" if not models_agree
            else "auto_models_agree_on_language"
        )
        accepted["suspected_language_mismatch"] = False
        with state["language_lock"]:
            current = state.get("session_language")
            if current in ("ar", "en") and current != large_v3_language:
                flagged = dict(accepted)
                flagged["suspected_language_mismatch"] = True
                flagged["language_guard"] = "concurrent_language_disagreement"
                return flagged
            state["session_language"] = large_v3_language
        return accepted

    def _chunk_worker_loop(self):
        while True:
            session_id, wav_bytes, final, begins_with_overlap, full_wav = self._chunk_queue.get()
            prefetched = None
            if isinstance(wav_bytes, tuple) and len(wav_bytes) == 2 and isinstance(wav_bytes[1], Future):
                wav_bytes, prefetched = wav_bytes
            state = self._chunk_states.get(session_id)
            if not state:
                if prefetched:
                    prefetched.cancel()
                continue

            if state.get("cancelled"):
                if prefetched:
                    prefetched.cancel()
                reference_future = state.pop("auto_reference_future", None)
                if reference_future:
                    reference_future.cancel()
                if final:
                    self._chunk_states.pop(session_id, None)
                continue

            try:
                if state["failed"] and prefetched:
                    prefetched.cancel()
                if wav_bytes and not state["failed"]:
                    try:
                        result = prefetched.result() if prefetched else self._transcribe_with_retry(wav_bytes, state)
                        if state.get("cancelled"):
                            continue
                        result = self._guard_auto_chunk_language(wav_bytes, result, state)
                        state["latency"] += float(result.get("latency", 0.0))
                        if (
                            not result.get("suspected_language_mismatch")
                            and not result.get("filtered_no_speech")
                            and result.get("text", "").strip()
                        ):
                            state["first_chunk_verified"] = True
                        if result.get("suspected_language_mismatch"):
                            state["failed"] = True
                            state["failure_reason"] = (
                                "Automatic language detection disagreed with the transcript; "
                                "using full-session recovery."
                            )
                            print(
                                "[WinVoice] Language guard recovery "
                                f"session={session_id} "
                                f"reason={result.get('language_guard', 'unspecified')} "
                                f"locked={state.get('session_language')} "
                                f"detected={result.get('detected_language')} "
                                f"avg_logprob={result.get('avg_logprob')}",
                                flush=True,
                            )
                        elif not result.get("filtered_no_speech"):
                            cfg = state.get("config", self.config_manager)
                            text = _apply_dictation_style(
                                result.get("text", ""), cfg
                            )
                            if text:
                                if begins_with_overlap and state["parts"]:
                                    previous = " ".join(state["parts"])
                                    if not _overlap_count(previous, text):
                                        state["failed"] = True
                                        state["failure_reason"] = (
                                            "Chunk overlap could not be reconciled; "
                                            "using full-session recovery."
                                        )
                                        print(
                                            f"[WinVoice] Overlap recovery session={session_id}",
                                            flush=True,
                                        )
                                    else:
                                        state["parts"] = [_merge_overlap(previous, text)]
                                else:
                                    state["parts"].append(text)

                                # Completed chunks are safe to append because overlap
                                # merging operates on consistently styled ASR text.
                                # Glossary/name corrections still run only after the
                                # boundary is settled so they cannot break the next
                                # chunk's exact-overlap check.
                                if (
                                    not final
                                    and not state["failed"]
                                    and not state.get("cancelled")
                                    and state.get("live_enabled")
                                ):
                                    current = " ".join(state["parts"]).strip()
                                    current = _apply_live_corrections(
                                        current, state.get("live_corrections")
                                    )
                                    cfg = state.get("config", self.config_manager)
                                    if cfg.get("auto_fix_obvious", True):
                                        mapping_source = current
                                        correction = self.transcriber.correct_obvious_mistakes(current)
                                        current = correction.get("text", current).strip()
                                        state["latency"] += float(correction.get("latency", 0.0))
                                        context = state.get("context")
                                        if context and context.done():
                                            visible = context.result()
                                            if not isinstance(visible, dict):
                                                # Names-only repair is local and effectively free.
                                                # Generative WhatsApp correction is deliberately
                                                # kept off the live path so typing never waits on
                                                # a second network model call.
                                                current = apply_terms(current, visible)

                                        current = _apply_dictation_style(current, cfg)
                                        if current != mapping_source:
                                            mappings = state.setdefault("live_corrections", [])
                                            mappings.append((mapping_source, current))
                                            del mappings[:-8]

                                    stable = _stable_live_prefix(
                                        current,
                                        cfg.get("live_holdback_words", 1),
                                    )
                                    delta = _append_only_suffix(
                                        state.get("live_committed", ""), stable
                                    )
                                    if delta is None:
                                        state["live_enabled"] = False
                                    elif delta:
                                        pasted = self.text_injector.inject_text(
                                            delta + " ",
                                            preserve_clipboard=True,
                                            target_hwnd=state.get("target"),
                                            copy_on_failure=False,
                                        )
                                        if pasted:
                                            state["live_committed"] = stable
                                        else:
                                            state["live_enabled"] = False
                            else:
                                state["failed"] = True
                                state["failure_reason"] = (
                                    "Voiced chunk produced no usable text."
                                )
                                print(
                                    f"[WinVoice] Empty voiced chunk session={session_id}",
                                    flush=True,
                                )
                        else:
                            # A no-speech chunk is not an error. Ignore it and
                            # allow the rest of the recording to continue.
                            pass
                    except Exception as e:
                        state["failed"] = True
                        state["failure_reason"] = str(e)
                        print(
                            f"[WinVoice] Chunk exception session={session_id} "
                            f"type={type(e).__name__} error={e}",
                            flush=True,
                        )

                if not final:
                    continue

                if state.get("cancelled"):
                    continue
                if state.get("permanent_error"):
                    raise RuntimeError(state.get("failure_reason", "Groq request rejected"))
                if state.get("capture_error"):
                    raise RuntimeError("Microphone dropped audio; please repeat the dictation.")
                used_fallback = False
                if state["failed"]:
                    reference_future = state.pop("auto_reference_future", None)
                    if reference_future:
                        reference_future.cancel()
                    if not full_wav:
                        raise RuntimeError(
                            "A chunk failed and full-session recovery audio was unavailable: "
                            + state.get("failure_reason", "unknown error")
                        )
                    # Reliable chunking hands raw retained blocks to the worker.
                    # Avoid concatenating/encoding the entire recording on key-up
                    # unless a fallback is actually necessary.
                    if not isinstance(full_wav, (bytes, bytearray)):
                        full_wav = self.recorder.encode_session_chunks(
                            full_wav, require_speech=True
                        )
                    if not full_wav:
                        raise RuntimeError(
                            "Full-session recovery audio contained no usable speech."
                        )
                    used_fallback = True
                    fallback = self._transcribe_with_retry(full_wav, state)
                    fallback = self._guard_auto_chunk_language(full_wav, fallback, state)
                    state["latency"] += float(fallback.get("latency", 0.0))
                    if fallback.get("suspected_language_mismatch"):
                        locked = state.get("session_language")
                        if locked in ("ar", "en"):
                            label = "Arabic" if locked == "ar" else "English"
                            raise RuntimeError(
                                f"{label} was locked for this recording, but the "
                                "recovery transcript changed language. Result withheld."
                            )
                        raise RuntimeError(
                            "A single language could not be established safely in Auto mode. "
                            "Use Alt+Insert for English or Alt+Shift+Insert for Arabic."
                        )
                    cfg = state.get("config", self.config_manager)
                    text = (
                        ""
                        if fallback.get("filtered_no_speech")
                        else _apply_dictation_style(
                            fallback.get("text", ""), cfg
                        )
                    )
                else:
                    cfg = state.get("config", self.config_manager)
                    text = " ".join(state["parts"]).strip()

                text = _apply_live_corrections(
                    text, state.get("live_corrections")
                )
                if text and cfg.get("auto_fix_obvious", True):
                    correction = self.transcriber.correct_obvious_mistakes(text)
                    text = correction.get("text", text).strip()
                    state["latency"] += float(correction.get("latency", 0.0))

                    context = state.get("context")
                    if context and context.done() and not state.get("cancelled"):
                        visible_context = context.result()
                        if not isinstance(visible_context, dict):
                            # Only deterministic names/terms repair is allowed.
                            # No generative model may rewrite transcript text,
                            # eliminating post-ASR translation as a failure mode.
                            text = apply_terms(text, visible_context)

                # Re-apply the deterministic output style after optional local
                # corrections so no later step can reintroduce sentence periods
                # or automatic sentence-start capitalization.
                text = _apply_dictation_style(text, cfg)

                if not state.get("cancelled"):
                    self.event_queue.put((
                        "chunk_session_done",
                        (
                            session_id,
                            text,
                            state["latency"],
                            used_fallback,
                            state.get("target"),
                            {"live_committed": state.get("live_committed", ""),
                             "stopped_at": state.get("stopped_at")},
                        ),
                    ))
            except Exception as e:
                if not state.get("cancelled"):
                    self.event_queue.put(("chunk_session_error", (session_id, str(e))))
            finally:
                if final:
                    state["cancelled"] = True
                    self._chunk_states.pop(session_id, None)
                wav_bytes = full_wav = None

    def cancel_dictation(self):
        with self._action_lock:
            self._cancelled_through = self._chunk_session
            for state in list(self._chunk_states.values()):
                state["cancelled"] = True
            self._pending_sessions.clear()
            if self.is_dictating:
                print("[WinVoice] Dictation cancelled via Esc/Close.", flush=True)
                self.is_dictating = False
                self._ptt_active = False
                if self._chunking_active:
                    state = self._chunk_states.get(self._chunk_session)
                    if state:
                        state["cancelled"] = True
                try:
                    self.recorder.stop(play_cue=False)
                except Exception as e:
                    print(f"[WinVoice] Recorder cancel note: {e}", flush=True)
                self._queue_chunk(None, final=True)
                self._chunking_active = False
                self.tray.set_recording(False)
                self.ui_pill.set_state("idle")
                self.ui_pill.hide()
            with self.recorder._lock:
                self.recorder.session_chunks = []
                self.recorder.audio_chunks = []
                self.recorder._buffered_frames = 0
            if self.ui_pill.is_visible():
                self._ptt_active = False
                self.ui_pill.hide()

    def _start_listening(self, language_override=None):
        if len(self._pending_sessions) >= 4:
            self._ptt_active = False
            self.tray.notify("Finish or cancel pending dictation before starting another.")
            return
        self.is_dictating = True
        self._chunking_active = bool(self.config_manager.get("reliable_chunking", True))

        # Capture the target before the no-activate floating controller appears.
        target_hwnd = self.text_injector.capture_active_window()
        self._start_chunk_session(target_hwnd)
        state = self._chunk_states[self._chunk_session]
        if language_override in ("en", "ar"):
            # Explicit push-to-talk shortcuts take priority for this recording.
            state["config"]["language"] = language_override
            state["config"]["custom_prompt"] = ""
            with state["language_lock"]:
                state["session_language"] = language_override
        elif state["config"].get("language") == "windows":
            selected = self.text_injector.typing_language_for_window(target_hwnd)
            # Resolve the active Windows layout for this session only; the
            # saved preference still follows later keyboard switches.
            state["config"]["language"] = selected or "auto"
            if selected:
                with state["language_lock"]:
                    state["session_language"] = selected
            print(
                f"[WinVoice] Windows typing language: {selected or 'unavailable'}"
                + ("" if selected else "; using Auto for this recording"),
                flush=True,
            )
        session_language = state["config"].get("language", "auto")
        self.ui_pill.language_hint = {"en": "English", "ar": "Arabic", "auto": "Auto language"}.get(session_language, session_language.upper())
        self._recording_started = time.monotonic()

        # Hide connection setup under the first seconds of speech instead of
        # paying DNS/TLS latency after the first chunk is ready.
        self.transcriber.prewarm_async()

        cue = self.config_manager.get("sound_cues", True)
        try:
            self.recorder.start(play_cue=cue)
        except Exception as e:
            self.is_dictating = False
            self._ptt_active = False
            self._chunk_states[self._chunk_session]["cancelled"] = True
            self._pending_sessions.discard(self._chunk_session)
            self._queue_chunk(None, final=True)
            self._chunking_active = False
            print(f"[WinVoice] Could not start microphone: {e}", flush=True)
            self.ui_pill.set_state("idle")
            self.ui_pill.hide()
            return

        state = self._chunk_states[self._chunk_session]
        if self.config_manager.get("screen_context", False) and self.config_manager.get("auto_fix_obvious", True):
            state["context"] = self.screen_context.start(
                target_hwnd, self.config_manager.get("groq_api_key", ""),
                lambda: state.get("cancelled", False),
            )
        self.ui_pill.set_state("listening")
        self.ui_pill.show()
        self.tray.set_recording(True)
        mode = "reliable background chunking" if self._chunking_active else "one-shot"
        print(f"[WinVoice] Dictation started ({mode}).", flush=True)

    def _stop_listening_and_transcribe(self):
        self.is_dictating = False
        self._ptt_active = False
        state = self._chunk_states[self._chunk_session]
        state["stopped_at"] = time.monotonic()
        try:
            wav_bytes = self.recorder.stop(play_cue=self.config_manager.get("sound_cues", True))
            if self._chunking_active:
                # O(1) handoff; full WAV encoding is deferred to the worker and
                # skipped entirely on the normal successful-chunk path.
                full_wav = self.recorder.detach_session_chunks()
            else:
                full_wav = self.recorder.full_session_wav(require_speech=True)
            state["capture_error"] = self.recorder.capture_error
        except Exception:
            wav_bytes = full_wav = None
            state["cancelled"] = True
            self.tray.notify("Microphone error. Please try again.")
        finally:
            with self.recorder._lock:
                self.recorder.session_chunks = []
                self.recorder.audio_chunks = []
                self.recorder._buffered_frames = 0
        self.tray.set_recording(False)
        if full_wav:
            self.ui_pill.set_state("transcribing")
            self._queue_chunk(
                wav_bytes if self._chunking_active else full_wav, final=True,
                begins_with_overlap=self._chunk_buffer_has_overlap if self._chunking_active else False,
                full_session_wav=full_wav,
            )
        else:
            state["cancelled"] = True
            self._pending_sessions.discard(self._chunk_session)
            self._queue_chunk(None, final=True)
            self.ui_pill.set_state("idle")
            self.ui_pill.hide()
        self._chunking_active = False

    def on_settings_updated(self):
        new_hotkey = self.config_manager.get("hotkey", "<cmd>+h")
        self.hotkey.update_hotkey(new_hotkey)
        self.transcriber.api_key = self.config_manager.get("groq_api_key", "")
        self.transcriber.prewarm_async(force=True)
        print(f"[WinVoice] Settings reloaded. Shortcut: {self.hotkey.get_display_name()} ({new_hotkey})", flush=True)

    def quit(self):
        print("[WinVoice] Exiting...", flush=True)
        self.cancel_dictation()
        self.hotkey.stop()
        self.tray.stop()
        executor = getattr(self, "_asr_executor", None)
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)
        try:
            self.transcriber.close()
            self.screen_context.close()
        except Exception:
            pass
        try:
            self.ui_pill.root.quit()
        except Exception:
            pass
        if getattr(self, "_instance_mutex", None):
            try:
                kernel32.CloseHandle(self._instance_mutex)
            except Exception:
                pass
            self._instance_mutex = None

if __name__ == "__main__":
    app = WinVoiceApp()
    app.start()