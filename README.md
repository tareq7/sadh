<div align="center">

# Sadh · صَدْح

**Fast Bilingual Voice Typing for Windows**  
*Drop-in replacement for Windows 11 Voice Typing (`Win + H`), powered by Groq Cloud Whisper.*

[![GitHub Release](https://img.shields.io/github/v/release/tareq7/sadh?color=10b981&label=Release)](https://github.com/tareq7/sadh/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078d4.svg)](https://github.com/tareq7/sadh)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-3776ab.svg)](https://python.org)
[![Groq Whisper](https://img.shields.io/badge/Engine-Groq%20Whisper%20Large%20v3%20Turbo-f55036.svg)](https://groq.com)

[**Quick Install**](#quick-installation) • [**Features**](#why-sadh) • [**Shortcuts**](#keyboard-shortcuts) • [**Configuration**](#configuration) • [**Architecture**](#architecture)

---

</div>

## Overview

**Sadh (صَدْح)** — Arabic for a clear, loud, resonant vocal utterance — is a lightweight Windows voice typing companion designed to fix the chronic issues of native speech typing:

- **Responsive dictation**: Uses Groq's hosted Whisper models; responsiveness varies with speech duration, network conditions, and service load.
- **Accurate bilingual support**: Transcribes Modern Standard Arabic, regional dialects (Levantine, Egyptian, Gulf), and English without cross-translation or garbled transliteration.
- **Zero focus theft**: Uses a native Win32 `WS_EX_NOACTIVATE` floating overlay. You never lose cursor focus, active text fields, or command prompt carets while speaking.
- **Native hotkey integration**: Intercepts `Win + H` cleanly without triggering Windows Speech error dialogs.
- **No local Whisper GPU required**: Speech recognition runs remotely, while local microphone capture, UI and text insertion use CPU and memory.

---

## Performance notes

Sadh uses remote speech recognition rather than a local GPU model. End-to-end latency and accuracy depend on microphone quality, internet connection, API model availability, and the speech itself. The project does not publish reproducible comparative benchmarks; claims of fixed latency, memory use, or superiority over other engines should not be inferred.

---

## Quick Installation

### Option 1: One-Line PowerShell Installer (Recommended)

Open PowerShell and paste:

```powershell
irm https://raw.githubusercontent.com/tareq7/sadh/main/install.ps1 | iex
```

The installer downloads a release binary and its SHA-256 checksum manifest, verifies the binary before replacing any existing installation, creates a Desktop shortcut, and launches the app. Review the remote script before running it. Close any existing Sadh process before upgrading.

---

### Option 2: Standalone Executable (.exe)

1. Download **`Sadh-windows-x64.exe`** from the [Latest Release](https://github.com/tareq7/sadh/releases/latest).
2. Move it to your preferred folder (e.g., `C:\Tools\Sadh`).
3. Run `Sadh-windows-x64.exe`.
4. Right-click the microphone icon in your system tray -> **Settings** -> paste your free [Groq API Key](https://console.groq.com/keys).

---

### Option 3: Run from Source (Developers)

```bash
# Clone the repository
git clone https://github.com/tareq7/sadh.git
cd sadh

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy configuration and launch
copy config.json.example config.json
python main.py
```

---

## Keyboard Shortcuts

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| **`Win + H`** | Toggle Dictation | Clean drop-in replacement for Windows Speech |
| **`Ctrl + Shift + Space`** | Toggle Dictation | Safe fallback hotkey for restricted keyboards |
| **`Insert` (Hold)** | Push-to-Talk | Records while pressed; transcribes on release |
| **`Alt + Insert`** | Force English | One-shot English dictation override |
| **`Alt + Shift + Insert`** | Force Arabic | One-shot Arabic dictation override |
| **`Esc`** | Cancel | Aborts active audio capture and discards pending chunks |

---

## Why Sadh?

```
┌────────────────────────────────────────────────────────┐
│                   Active Application                   │
│        (VS Code / Browser / Slack / Word / Terminal)    │
└───────────────────────────▲────────────────────────────┘
                            │ SendInput (Unicode characters)
┌───────────────────────────┴────────────────────────────┐
│                    Sadh Core Runtime                   │
│                                                        │
│  ┌──────────────────┐            ┌──────────────────┐  │
│  │ Low-Level Hook   │            │ Floating Overlay │  │
│  │ (WH_KEYBOARD_LL) │            │ WS_EX_NOACTIVATE │  │
│  └────────┬─────────┘            └──────────────────┘  │
│           │                                            │
│  ┌────────▼─────────┐            ┌──────────────────┐  │
│  │ Audio Stream     │            │ IPC Trigger      │  │
│  │ (PortAudio 16kHz)│            │ (127.0.0.1:49152)│  │
│  └────────┬─────────┘            └──────────────────┘  │
│           │ VAD Chunking (1.6s – 2.8s)                 │
└───────────┼────────────────────────────────────────────┘
            │ HTTPS (TLS 1.3)
┌───────────▼────────────────────────────────────────────┐
│                Groq Cloud LPU Hardware                 │
│              whisper-large-v3-turbo                    │
└────────────────────────────────────────────────────────┘
```

### 1. Reliable Background Chunking
Instead of waiting for you to finish speaking 30 seconds of audio before sending a single monolithic request, Sadh splits audio into natural speech chunks using an adaptive Voice Activity Detector (VAD). Completed chunks are transcribed and typed live while you continue speaking.

### 2. Language Auto-Synchronization
When set to **Match Windows typing language**, Sadh checks the active keyboard layout of the target window when you start speaking:
- If your layout is Arabic, it primes Whisper for Arabic speech.
- If your layout is English, it locks to English.
- If set to **Auto**, a detection-only probe verifies the spoken language early to prevent hallucinated translations.

### 3. Native Windows Hook Without Side Effects
Standard `Win + H` interception in third-party tools often breaks the Start Menu or displays Windows speech errors. Sadh uses a low-level keyboard hook with `VK_CONTROL` chord masking to suppress native speech errors while preserving Windows keyup propagation.

---

## Configuration

Preferences are stored in `config.json` next to the executable:

```json
{
  "groq_api_key": "gsk_...",
  "model": "whisper-large-v3-turbo",
  "language": "auto",
  "dialect": "gazan",
  "hotkey": "<cmd>+h",
  "dictation_mode": "toggle",
  "auto_paste": true,
  "reliable_chunking": true,
  "live_typing": true,
  "ui_theme": "orb",
  "start_with_windows": true
}
```

Key options can be changed through the visual Settings window (accessible via system tray).

---

## Architecture & Codebase Map

| File | Responsibility |
| :--- | :--- |
| [`main.py`](main.py) | Application coordinator, state machine, event loop, and IPC server |
| [`audio_recorder.py`](audio_recorder.py) | 16 kHz mono PortAudio capture, ring buffer, dynamic VAD chunking |
| [`groq_transcriber.py`](groq_transcriber.py) | Groq Whisper client, dialect prompts, retry backoff, response filtering |
| [`hotkey_listener.py`](hotkey_listener.py) | Low-level Win32 keyboard hook (`WH_KEYBOARD_LL`), key masking |
| [`text_injector.py`](text_injector.py) | Target HWND focus verification, Unicode `SendInput`, clipboard safety |
| [`ui_pill.py`](ui_pill.py) | Floating non-activating indicator pill (`WS_EX_NOACTIVATE`) |
| [`startup_manager.py`](startup_manager.py) | Windows Run registry (`HKCU\...\Run`) persistence and migration |
| [`settings_ui.py`](settings_ui.py) | CustomTkinter dark-mode settings panel |
| [`trigger.py`](trigger.py) | CLI trigger utility communicating over local TCP socket |

---

## Testing

The codebase includes an automated regression and component test suite:

```bash
# Run all tests
pytest -v
```

Tests cover hotkey race conditions, cancellation timeouts, chunk deduplication, Arabic character normalization, and destination window validation without requiring live Groq network requests.

---

## Building Standalone Binary

Builds require Windows, Python 3.10–3.12 and PyInstaller:

```powershell
python -m pip install -r requirements.txt pyinstaller
python build_exe.py
```

The build writes `dist\\Sadh.exe`. It packages application assets but **never** bundles a local `config.json`, `.env`, or API key. Releases are built and checked on GitHub Actions.

---

## Privacy & Security

- **Groq cloud transcription:** Microphone audio is sent over HTTPS to Groq for recognition. The service is not offline or entirely local.
- **Optional screen context:** When explicitly enabled in Settings, Sadh may capture a crop of the active window and send it to Groq to improve recognition/correction. This is disabled by default. Avoid enabling it while confidential material is visible.
- **Diagnostics:** Runtime diagnostics are written to a rotating `logs/runtime.log` file. The application does not intentionally log full transcripts, but exception details may contain sensitive information; inspect logs before sharing them.
- **Credentials:** The Groq key is read from the local `config.json`, `.env`, or `GROQ_API_KEY` environment variable. Never commit those files or include them in issue reports. Keep the installation directory private.
- **Local hotkeys and clipboard:** Windows keyboard hooks and clipboard operations are used to insert the transcript at the focused cursor; the clipboard may temporarily contain dictated text.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

---

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Tareq Naji.