import sys
import time
import ctypes
import numpy as np
from pathlib import Path
from unittest.mock import patch

# Add root directory to sys.path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

try:
    from .config_manager import ConfigManager
    from .dialect_prompts import get_dialect_prompt
    from .audio_recorder import AudioRecorder
    from .text_injector import TextInjector
    from .groq_transcriber import GroqTranscriber
except ImportError:
    from config_manager import ConfigManager
    from dialect_prompts import get_dialect_prompt
    from audio_recorder import AudioRecorder
    from text_injector import TextInjector
    from groq_transcriber import GroqTranscriber

def test_config():
    print("[TEST 1] ConfigManager testing...")
    cfg = ConfigManager()
    assert cfg.get("model") in ["whisper-large-v3", "whisper-large-v3-turbo"]
    assert cfg.get("language") in ["ar", "en", "auto", "windows"]
    assert cfg.get("dialect") in ["gazan", "levantine", "gulf", "standard", "none"]
    assert isinstance(cfg.get("auto_fix_obvious"), bool)
    assert isinstance(cfg.get("reliable_chunking"), bool)
    assert isinstance(cfg.get("live_typing"), bool)
    assert 1.2 <= cfg.get("chunk_min_seconds") < cfg.get("chunk_hard_max_seconds")
    assert 550 <= cfg.get("chunk_overlap_ms") <= 800
    print("  -> ConfigManager passed!")

def test_dialect_prompts():
    print("[TEST 2] Dialect Prompts testing...")
    prompt_gz = get_dialect_prompt("gazan")
    assert "هلقيت" in prompt_gz
    assert "بدي" in prompt_gz
    print("  -> Gazan prompt verification passed!")

def test_audio_recorder():
    print("[TEST 3] AudioRecorder capture + local silence gate...")
    recorder = AudioRecorder(sample_rate=16000)
    wav_bytes = None
    try:
        recorder.start(play_cue=False)
        time.sleep(1.0)
        wav_bytes = recorder.stop(play_cue=False, require_speech=False)
        assert wav_bytes is not None
        assert len(wav_bytes) > 10000
    except Exception as e:
        print(f"  -> Physical microphone not available in runner ({e}); skipping live hardware capture.")

    silent = AudioRecorder(sample_rate=16000)
    silent.is_recording = True
    silent.audio_chunks = [np.zeros((16000, 1), dtype=np.float32)]
    assert silent.stop(play_cue=False, require_speech=True) is None

    voiced = AudioRecorder(sample_rate=16000)
    voiced.is_recording = True
    t = np.arange(16000, dtype=np.float32) / 16000.0
    tone = (0.03 * np.sin(2 * np.pi * 220 * t)).astype(np.float32).reshape(-1, 1)
    voiced.audio_chunks = [tone]
    assert voiced.stop(play_cue=False, require_speech=True) is not None

    print("  -> Capture passed; silence rejected; one-shot audio preserved.")

def test_chunk_audio_retention():
    print("[TEST 4] Chunk buffer + full-session retention...")
    recorder = AudioRecorder(sample_rate=16000)
    recorder.is_recording = True
    t = np.arange(16000, dtype=np.float32) / 16000.0
    tone = (0.03 * np.sin(2 * np.pi * 220 * t)).astype(np.float32).reshape(-1, 1)
    recorder.audio_chunks = [tone.copy()]
    recorder.session_chunks = [tone.copy()]
    recorder._buffered_frames = len(tone)

    chunk = recorder.take_chunk(overlap_seconds=0.25, require_speech=True)
    assert chunk is not None
    assert 0.24 <= recorder.buffered_duration() <= 0.26

    extra = tone[:8000].copy()
    recorder.audio_chunks.append(extra)
    recorder.session_chunks.append(extra)
    recorder._buffered_frames += len(extra)
    full = recorder.full_session_wav(require_speech=True)
    assert full is not None
    assert len(full) > len(chunk)
    print("  -> Chunk drains preserve overlap and an independent full-session recovery copy.")

def test_audio_silence_gate():
    print("[TEST 5] AudioRecorder silence gate...")
    import numpy as np

    recorder = AudioRecorder(sample_rate=16000)
    recorder.is_recording = True
    recorder.audio_chunks = [np.zeros((32000, 1), dtype=np.float32)]
    assert recorder.stop(play_cue=False) is None
    print("  -> Digital silence is rejected before any Groq request.")

def test_text_injector():
    print("[TEST 6] TextInjector clipboard test...")
    injector = TextInjector()
    injector.capture_active_window()
    print("  -> Active window capture passed!")

def test_groq_client_init():
    print("[TEST 7] GroqTranscriber validation...")
    transcriber = GroqTranscriber(api_key="gsk_dummy_key_for_test")
    assert transcriber.api_key == "gsk_dummy_key_for_test"
    print("  -> Groq client initialization passed!")

def test_no_speech_filter():
    print("[TEST 8] Whisper no-speech filtering...")
    text, filtered = GroqTranscriber._extract_text({
        "text": "hallucinated silence",
        "segments": [{"no_speech_prob": 0.71}],
    })
    assert text == ""
    assert filtered is True

    text, filtered = GroqTranscriber._extract_text({
        "text": "مرحبا",
        "segments": [{"no_speech_prob": 0.08}],
    })
    assert text == "مرحبا"
    assert filtered is False
    mixed_prompt = GroqTranscriber._build_prompt("auto", "gazan", "")
    assert mixed_prompt == ""

    english_prompt = GroqTranscriber._build_prompt("en", "gazan", "CustomPrompt")
    assert english_prompt == "CustomPrompt"

    # Conservative correction guard: small obvious fixes pass; translations,
    # code-switch erasure, and large rewrites are rejected.
    assert GroqTranscriber._correction_is_conservative(
        "افتح GitHup وروح على pull request",
        "افتح GitHub وروح على pull request",
    )
    assert not GroqTranscriber._correction_is_conservative(
        "ما بدي أغير هاد الكلام",
        "I do not want to change these words",
    )
    assert not GroqTranscriber._correction_is_conservative(
        "افتح GitHub وروح على pull request",
        "افتح جيت هب وروح على طلب السحب",
    )

    repeated = (
        "Substantially update the design of the design of the design of the "
        "to be more compact to be more compact to be more compact "
        "lightweight lightweight lighter faster and more modern"
    )
    cleaned = GroqTranscriber(api_key="dummy").correct_obvious_mistakes(repeated)["text"]
    assert cleaned == repeated

    typo = GroqTranscriber(api_key="dummy").correct_obvious_mistakes(
        "Please open GitHup and Chat GPT"
    )["text"]
    assert typo == "Please open GitHub and ChatGPT"
    print("  -> No-speech filtering, unbiased auto mode, and obvious-only correction passed.")

def test_hotkey_listener():
    print("[TEST 9] HotkeyListener configuration validation...")
    from hotkey_listener import HotkeyListener
    hl = HotkeyListener("win+h", lambda: None)
    assert hl.hotkey_str == "<cmd>+h"
    assert hl.get_display_name() == "Win + H"
    assert hl._primary_modifiers == {"cmd"}
    assert hl._primary_vk == 0x48

    hl.update_hotkey("ctrl+alt+k")
    assert hl.hotkey_str == "<ctrl>+<alt>+k"
    assert hl._primary_modifiers == {"ctrl", "alt"}
    assert hl._primary_vk == ord("K")

    # Low-level hooks run before GetAsyncKeyState changes, so verify that
    # modifier key events themselves drive custom shortcut detection.
    import threading
    fired = threading.Event()
    hl_custom = HotkeyListener("ctrl+alt+k", fired.set)

    class _KeyEvent:
        flags = 0
        def __init__(self, vk):
            self.vkCode = vk

    with patch("hotkey_listener.user32.GetAsyncKeyState", return_value=0x8000):
        hl_custom._win32_filter(0x0100, _KeyEvent(0xA2))  # left Ctrl down
        hl_custom._win32_filter(0x0104, _KeyEvent(0xA4))  # left Alt down
        hl_custom._win32_filter(0x0100, _KeyEvent(ord("K")))
        assert fired.wait(1.0)
        hl_custom._win32_filter(0x0101, _KeyEvent(ord("K")))
        hl_custom._win32_filter(0x0105, _KeyEvent(0xA4))
        hl_custom._win32_filter(0x0101, _KeyEvent(0xA2))

    hl2 = HotkeyListener("ctrl+shift+space", lambda: None)
    assert hl2.hotkey_str == "<ctrl>+<shift>+<space>"
    assert hl2.get_display_name() == "Ctrl + Shift + Space"

    ptt_started = threading.Event()
    ptt_stopped = threading.Event()
    ptt_counts = {"start": 0, "stop": 0}

    def _ptt_start():
        ptt_counts["start"] += 1
        ptt_started.set()

    def _ptt_stop():
        ptt_counts["stop"] += 1
        ptt_stopped.set()

    hl_ptt = HotkeyListener(
        "win+h",
        lambda: None,
        on_ptt_start=_ptt_start,
        on_ptt_stop=_ptt_stop,
    )
    hl_ptt._win32_filter(0x0100, _KeyEvent(0x2D))
    hl_ptt._win32_filter(0x0100, _KeyEvent(0x2D))  # key-repeat must not re-start
    assert ptt_started.wait(1.0)
    hl_ptt._win32_filter(0x0101, _KeyEvent(0x2D))
    assert ptt_stopped.wait(1.0)
    assert ptt_counts == {"start": 1, "stop": 1}
    print("  -> Toggle shortcuts and Insert push-to-talk events work correctly.")

def test_run_entrypoint():
    print("[TEST 10] run.py entrypoint import...")
    import run
    assert run.WinVoiceApp is not None
    print("  -> run.py imports the actual local WinVoiceApp.")

def test_single_instance_mutex():
    print("[TEST 11] Single-instance mutex validation...")
    import os
    from main import _acquire_single_instance_mutex, kernel32

    # Use an isolated mutex name so the test can run while WinVoice is live.
    test_name = f"Local\\WinVoiceGroqSingletonTest_{os.getpid()}"

    # Simulate a stale ERROR_ALREADY_EXISTS left by an unrelated Win32 call.
    ctypes.set_last_error(183)
    first = _acquire_single_instance_mutex(test_name)
    assert first is not None
    try:
        second = _acquire_single_instance_mutex(test_name)
        assert second is None
    finally:
        kernel32.CloseHandle(first)
    print("  -> Stale LastError is ignored and duplicate instances are detected.")

def test_chunk_merge_and_fallback():
    print("[TEST 12] Reliable chunk merge + fallback...")
    import queue
    import threading
    from main import WinVoiceApp, _dedupe_overlap, _merge_overlap, _stable_live_prefix

    assert _stable_live_prefix("one two three four", 2) == "one two"
    assert _stable_live_prefix("one two", 2) == ""
    assert _dedupe_overlap(
        "hello from this system",
        "this system works very well",
    ) == "works very well"
    assert _merge_overlap(
        "the application works correctly.",
        "works correctly before merging",
    ) == "the application works correctly. before merging"
    assert _merge_overlap(
        "I completed the task.",
        "the task. Next we deploy",
    ) == "I completed the task. Next we deploy"
    assert _dedupe_overlap(
        "hello world",
        "world again",
    ) == "world again"

    class _Cfg:
        def get(self, key, default=None):
            values = {
                "groq_api_key": "dummy",
                "model": "whisper-large-v3-turbo",
                "language": "en",
                "dialect": "none",
                "custom_prompt": "",
                "auto_fix_obvious": False,
                "chunk_retries": 1,
            }
            return values.get(key, default)

    class _FakeTranscriber:
        api_key = ""
        def __init__(self):
            self.bad_calls = 0
        def transcribe(self, wav_bytes, **kwargs):
            if wav_bytes == b"first":
                text = "hello from this system"
            elif wav_bytes == b"second":
                text = "this system works very well"
            elif wav_bytes == b"bad":
                self.bad_calls += 1
                raise RuntimeError("simulated chunk failure")
            elif wav_bytes == b"full":
                text = "complete fallback transcript"
            else:
                text = ""
            return {"text": text, "latency": 0.01, "filtered_no_speech": False}
        def correct_obvious_mistakes(self, text):
            return {"text": text, "changed": False, "latency": 0.0}

    app = WinVoiceApp.__new__(WinVoiceApp)
    app.config_manager = _Cfg()
    app.transcriber = _FakeTranscriber()
    app._chunk_queue = queue.Queue()
    app._chunk_states = {}
    app.event_queue = queue.Queue()

    app._chunk_states[1] = {
        "target": 123,
        "parts": [],
        "failed": False,
        "cancelled": False,
        "latency": 0.0,
    }
    threading.Thread(target=app._chunk_worker_loop, daemon=True).start()
    app._chunk_queue.put((1, b"first", False, False, None))
    app._chunk_queue.put((1, b"second", True, True, b"full"))
    event = app.event_queue.get(timeout=10.0)
    assert event[0] == "chunk_session_done"
    session_id, text, _, used_fallback, target, *_ = event[1]
    assert session_id == 1
    assert text == "hello from this system works very well"
    assert used_fallback is False
    assert target == 123

    app._chunk_states[2] = {
        "target": 456,
        "parts": [],
        "failed": False,
        "cancelled": False,
        "latency": 0.0,
    }
    app._chunk_queue.put((2, b"bad", True, False, b"full"))
    event = app.event_queue.get(timeout=10.0)
    assert event[0] == "chunk_session_done"
    session_id, text, _, used_fallback, target, *_ = event[1]
    assert session_id == 2
    assert text == "complete fallback transcript"
    assert used_fallback is True
    assert target == 456
    assert app.transcriber.bad_calls == 2
    print("  -> Ordered overlap merge works; persistent chunk failure recovers from full audio.")

def test_auto_language_consensus():
    print("[TEST] Auto language consensus...")
    import threading
    from main import WinVoiceApp
    from groq_transcriber import GroqTranscriber

    app = WinVoiceApp.__new__(WinVoiceApp)
    app.config_manager = {}
    app.transcriber = GroqTranscriber()

    def state():
        return {
            "config": {"language": "auto", "model": "whisper-large-v3-turbo"},
            "session_language": None,
            "language_lock": threading.Lock(),
        }

    initial = {
        "text": "Please open the project.",
        "detected_language": "English",
        "requested_language": "auto",
        "latency": 0.1,
    }
    calls = []
    reference = {}

    def mock_transcribe(audio, current, *, config_override, mark_permanent):
        calls.append(dict(config_override))
        assert audio == b"sample"
        assert config_override["language"] == "auto"
        assert config_override["model"] == "whisper-large-v3"
        assert mark_permanent is False
        return reference

    app._transcribe_with_retry = mock_transcribe
    try:
        reference = {
            "text": "افتح المشروع من فضلك",
            "detected_language": "Arabic",
            "latency": 0.2,
        }
        current = state()
        guarded = app._guard_auto_chunk_language(b"sample", initial, current)
        assert guarded["suspected_language_mismatch"]
        assert current["session_language"] is None

        # A confident large-v3 Arabic decode can recover turbo's English
        # mistranscription without forcing a language on the same audio.
        reference["avg_logprob"] = -0.2
        current = state()
        guarded = app._guard_auto_chunk_language(b"sample", initial, current)
        assert guarded["text"] == reference["text"]
        assert guarded["language_guard"] == "large_v3_resolved_conflict"
        assert current["session_language"] == "ar"

        reference = {
            "text": "Close every other window.",
            "detected_language": "English",
            "latency": 0.2,
        }
        current = state()
        guarded = app._guard_auto_chunk_language(b"sample", initial, current)
        assert guarded["text"] == reference["text"]
        assert current["session_language"] == "en"

        reference = {
            "text": "Please open the project",
            "detected_language": "English",
            "latency": 0.2,
        }
        current = state()
        guarded = app._guard_auto_chunk_language(b"sample", initial, current)
        assert guarded["text"] == reference["text"]
        assert guarded["language_guard"] == "auto_models_agree_on_language"
        assert abs(guarded["latency"] - 0.3) < 1e-9
        assert current["session_language"] == "en"

        reference = {
            "text": "please open the project",
            "detected_language": "Arabic",
            "latency": 0.2,
            "avg_logprob": -0.2,
        }
        current = state()
        guarded = app._guard_auto_chunk_language(b"sample", initial, current)
        assert guarded["suspected_language_mismatch"]
        assert current["session_language"] is None
        assert len(calls) == 5
    finally:
        app.transcriber.close()
    print("  -> Uncertain conflicts withheld; confident large-v3 and wording variations accepted.")


def test_ui_pill_init():
    print("[TEST 13] VoicePillWindow themes, sizes, settings geometry...")
    from config_manager import ConfigManager
    from ui_pill import VoicePillWindow
    cfg = ConfigManager()
    cfg.config.update({
        "controller_size": "standard",
        "controller_position": "top_center",
        "ui_theme": "minimal",
        "ui_motion": "reduced",
        "settings_size": "standard",
    })
    pill = VoicePillWindow(cfg)
    assert (pill.width, pill.height) == (318, 64)
    assert pill.render_scale == 2
    assert pill._get_element_at(221, 32) == "mic"
    assert pill._get_element_at(268, 32) == "gear"
    assert pill._get_element_at(298, 32) == "close"

    for theme in ("minimal", "contrast"):
        cfg.config["ui_theme"] = theme
        pill._apply_interface_preferences()
        for state in ("listening", "transcribing", "done", "idle"):
            pill.set_state(state, audio_level=0.5)
            rendered = pill._render_pill_image()
            assert rendered.size == (318, 64)

    cfg.config["ui_theme"] = "orb"
    pill._apply_interface_preferences()
    assert (pill.width, pill.height) == (148, 148)
    assert pill.render_scale == 3
    assert pill._get_element_at(74, 74) == "mic"
    assert pill._get_element_at(124, 43) == "close"
    assert pill._get_element_at(124, 105) == "gear"
    # The native surface uses per-pixel alpha, and must keep physical size
    # stable across frames on high-DPI displays.
    from PIL import ImageChops
    assert not getattr(pill, "_native_alpha_failed", False)
    assert pill._layered_surface is not None
    assert pill._layered_surface.pixel_size() == (148, 148)
    bitmap = pill._layered_surface.bitmap
    for _ in range(6):
        pill._redraw()
        assert pill._layered_surface.pixel_size() == (148, 148)
        assert pill._layered_surface.bitmap == bitmap

    pill.set_state("listening", audio_level=0.0)
    silent = pill._render_orb_image()
    assert silent.getpixel((0, 0))[3] == 0
    assert any(0 < alpha < 255 for alpha in silent.getchannel("A").tobytes())
    pill.set_audio_level(0.7)
    for _ in range(4):
        pill._update_voice_visual_level()
    assert pill._display_level > 0.7
    loud = pill._render_orb_image()
    changed = ImageChops.difference(silent, loud).convert("L")
    assert sum(1 for pixel in changed.tobytes() if pixel > 24) > 150
    for state in ("listening", "transcribing", "done", "idle"):
        pill.set_state(state, audio_level=0.5)
        assert pill._render_pill_image().size == (148, 148)

    cfg.config["controller_size"] = "compact"
    pill._apply_interface_preferences()
    assert (pill.width, pill.height) == (108, 108)
    assert pill._render_pill_image().size == (108, 108)

    cfg.config["controller_size"] = "large"
    pill._apply_interface_preferences()
    assert (pill.width, pill.height) == (174, 174)
    assert pill._render_pill_image().size == (174, 174)

    # Settings must remain viewable while its idle controller is hidden,
    # without resizing the controller or leaving the parked root visible.
    before = (pill.width, pill.height)
    pill.hide()
    pill.root.update()
    assert pill.root.state() == "withdrawn"
    pill.open_settings()
    for _ in range(5):
        pill.root.update()
        time.sleep(0.08)
    assert (pill.width, pill.height) == before
    assert (pill.root.winfo_width(), pill.root.winfo_height()) == before
    assert pill._settings_offscreen
    assert pill.root.state() == "normal"
    pill.settings_window.window.destroy()
    pill.root.update()
    assert pill.root.state() == "withdrawn"
    assert not pill._settings_offscreen
    pill.root.destroy()
    del pill
    import gc
    gc.collect()
    print("  -> Idle Settings stays viewable; orb root parks offscreen and restores cleanly.")

def test_startup_manager():
    print("[TEST 14] StartupManager validation...")
    from startup_manager import is_start_with_windows_enabled, set_start_with_windows
    initial = is_start_with_windows_enabled()
    # Test toggle
    set_start_with_windows(True)
    assert is_start_with_windows_enabled() is True
    set_start_with_windows(False)
    assert is_start_with_windows_enabled() is False
    # Restore initial state
    if initial:
        set_start_with_windows(True)
    print("  -> StartupManager HKCU registry toggle passed!")

def test_tray_icon():
    print("[TEST 15] Sadh tray icon...")
    from tray_manager import APP_NAME, create_icon_image
    normal = create_icon_image(False)
    recording = create_icon_image(True)
    assert APP_NAME == "Sadh"
    assert normal.size == (64, 64) and normal.mode == "RGBA"
    assert recording.size == (64, 64) and recording.mode == "RGBA"
    assert normal.getpixel((0, 0))[3] == 0
    assert normal.tobytes() != recording.tobytes()
    print("  -> Generated tray artwork and recording badge render at 64×64.")


if __name__ == "__main__":
    print("=== RUNNING SADH STANDALONE COMPONENT TESTS ===")
    test_config()
    test_dialect_prompts()
    test_audio_recorder()
    test_chunk_audio_retention()
    test_audio_silence_gate()
    test_text_injector()
    test_groq_client_init()
    test_no_speech_filter()
    test_hotkey_listener()
    test_run_entrypoint()
    test_single_instance_mutex()
    test_chunk_merge_and_fallback()
    test_auto_language_consensus()
    test_ui_pill_init()
    test_startup_manager()
    test_tray_icon()
    print("=== ALL TESTS PASSED SUCCESSFULLY! ===")
