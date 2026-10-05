## Sadh (صَدْح) v1.0.0 — Fast Bilingual Voice Typing for Windows

Initial public production release of **Sadh (صَدْح)** — a fast, bilingual (Arabic & English) voice typing utility powered by Groq Cloud Whisper.

### Highlights

* **Sub-Second Latency:** Audio streams through Groq's LPUs running `whisper-large-v3-turbo` in under 600 ms.
* **Drop-in `Win + H` Replacement:** Intercepts native Windows Speech hotkeys cleanly without triggering Microsoft speech error popups or breaking the Start menu.
* **Zero Focus Theft (`WS_EX_NOACTIVATE`):** Never lose cursor focus, active IDE text fields, browser inputs, or terminal carets while speaking.
* **Bilingual Synchronization:** Automatically detects or synchronizes with your active Windows keyboard layout (Arabic / English) to prevent cross-language translation hallucinations.
* **Push-to-Talk & Hotkeys:**
  * `Win + H` / `Ctrl + Shift + Space`: Toggle dictation
  * `Insert` (Hold): Push-to-Talk
  * `Alt + Insert`: One-shot English dictation
  * `Alt + Shift + Insert`: One-shot Arabic dictation
  * `Esc`: Cancel active recording

---

### Distribution Packages

| File | Platform | Description |
| :--- | :--- | :--- |
| **`Sadh-windows-x64.exe`** | Windows 10/11 (x64) | Standalone single-file executable (zero Python required) |
| **`Sadh-v1.0.0-windows-x64.zip`** | Windows 10/11 (x64) | Portable bundle with assets, launcher, and icon |
| **`sadh-1.0.0-py3-none-any.whl`** | Windows / Linux / macOS | Universal Python wheel (`pip install sadh-1.0.0-py3-none-any.whl`) |
| **`sadh-1.0.0.tar.gz`** | Cross-platform | Source distribution archive |
| **`SHA256SUMS.txt`** | All | Cryptographic checksums |

---

### Quick Installation (Windows)

Run in PowerShell:
```powershell
irm https://raw.githubusercontent.com/tareq7/sadh/main/install.ps1 | iex
```

Or download `Sadh-windows-x64.exe` directly below, run it, and enter your free [Groq API Key](https://console.groq.com/keys) in Settings.
