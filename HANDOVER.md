# WinVoice Groq — Current Technical Handover

**Project:** `Sadh (صَدْح) — Fast Windows Voice Typing via Groq Whisper`
**Runtime:** Python 3.12 on Windows 11
**Purpose:** fast global Arabic/English dictation using Groq Whisper.

## Current behavior

- Hold **Insert** to record; release to finish.
- **Win+H** and **Ctrl+Shift+Space** remain toggle shortcuts.
- **Alt+Insert** forces English for one recording.
- **Alt+Shift+Insert** forces Arabic for one recording.
- **Esc** cancels recording/pending work. Text already typed live is not deleted.
- The compact no-activate pill never intentionally steals focus.
- The active destination HWND is captured before the pill appears.
- Unsafe focus changes stop live insertion instead of typing into the wrong app.
- Windows startup uses `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

## Transcription pipeline

Audio is captured as 16 kHz mono PCM WAV with 20 ms input blocks. Reliable chunking uses
a low-latency profile: after language lock, natural pauses may close a chunk at about
1.6 seconds, soft boundaries target about 2.8 seconds, hard boundaries cap near 4 seconds,
and forced non-silence boundaries retain about 650 ms of overlap while natural pauses retain
none. Auto starts a detection-only probe while the user is still speaking. Chunks are
processed in order by one background worker. Full-session audio remains retained for recovery, but WAV concatenation/encoding is
lazy and skipped entirely unless recovery is actually needed.
### Live typing

With **Type stable text while I speak** enabled, each completed chunk is appended immediately
before the recording ends. No extra word is held back; overlap protection is handled at the
audio/chunk boundary so completed text reaches the target as soon as it is available.

Overlap matching is exact and operates on raw Whisper text. Fuzzy overlap deletion is
intentionally disabled. Already committed text is monotonic: later processing never edits
that prefix. If full-session recovery disagrees with live text, the recovery transcript is
copied instead of overwriting what was already typed.

## Translation safeguards

All speech requests use Groq's `/openai/v1/audio/transcriptions` endpoint, never the
translation endpoint. Auto language mode sends **no Whisper prompt**, including no custom
names/terms, because the input language is not known yet and cross-language prompting can
bias Whisper output.

Explicit English/Arabic modes reject opposite-language/script output and extremely
low-confidence forced-language decodes. Auto mode may choose a language only once per
recording. An early Auto probe is detection-only: its transcript is discarded, and it can lock
Arabic or English only when language metadata, writing system, no-speech score, and Whisper
log-probability agree. All real chunks and the retained full-session fallback are then sent
with that exact language explicitly pinned. If the first Auto result claims a third language, it is
not committed: the same audio is re-transcribed with `whisper-large-v3` in the script-consistent
Arabic/English language and then locked. Any later metadata/script change is treated as unsafe.
Recovery never changes the established recording language; if it still conflicts, the result is
withheld instead of pasting a foreign-language chunk.
## Correction and screen context

Local correction is deterministic and narrow: it fixes only explicit high-confidence aliases
such as `GitHup -> GitHub` and `Chat GPT -> ChatGPT`. Intentional repetition is preserved.

Optional screen context captures only the active target window once at dictation start,
compresses it in memory to at most 1600 px / 300 KB, and sends it to Groq Qwen vision.
Images are not written to disk by the feature.

For WhatsApp, visible conversation context is advisory only. Dictation text is never
generatively rewritten from the screenshot. Screen context may supply deterministic
names/terms only; it never changes sentence wording, language, numbers, or meaning. This
removes a second network model call from the transcription critical path and prevents
screenshot/OCR context from translating or paraphrasing dictated text.

## Output style and UI

The current user configuration uses the **Reactive Orb** controller: a borderless floating sphere with layered translucent membranes, counter-rotating energy rings, a network-like core, orbiting particles, audio-reactive ripples, and state-based color changes. The previous graphite controller remains selectable as **Current / Graphite**, with High Contrast as a third option.

Settings disables CustomTkinter's late automatic DPI-awareness change and is explicitly parented to the existing Tk controller. The controller also uses fixed min/max geometry and delayed geometry reassertion, preventing the first Settings open from shrinking the HUD.

Output formatting is deterministic and happens before chunk merging/live insertion: sentence-ending periods are removed and ordinary Latin sentence starts are lowercased. Dots inside decimals/domains remain, and acronyms or intentional mixed-case technical names such as API, GitHub, and OpenAI are preserved. Both formatting rules have Settings toggles.

## Reliability

- Groq HTTP connections are pre-warmed during recording startup and kept alive for five minutes.
- After Auto locks Arabic/English, later requests explicitly send that language; verbose segment metadata remains enabled for no-speech filtering.
- A transient Groq failure retries at most once; a second network retry was removed to reduce worst-case latency.
- Authentication/permanent client errors fail promptly.
- High no-speech probability suppresses Whisper hallucinations over silence.
- Local speech gating rejects near-silent audio before upload.
- Microphone capture status errors fail the session rather than inventing missing audio.
- Recording is capped at five minutes.
- Clipboard restoration is sequence-checked so an older restore cannot overwrite a newer copy.
## Validation

Run:

```powershell
python -m unittest -v test_regressions test_chat_correction
python test_components.py
python -m compileall -q .
git diff --check
```

Current automated baseline: **70 regression/correction tests + 14 component checks passing**.
A live Groq English sample was also verified in both Auto and explicit-English modes with
the same English transcript.

## Core files

- `main.py` — lifecycle, chunk worker, live typing, recovery, cancellation.
- `audio_recorder.py` — microphone capture, chunk draining, full-session retention.
- `groq_transcriber.py` — Groq Whisper client, language/translation safeguards.
- `hotkey_listener.py` — low-level global shortcuts and Insert push-to-talk.
- `text_injector.py` — guarded HWND-targeted clipboard paste.
- `screen_context.py` / `chat_correction.py` — optional visual context and bounded correction.
- `ui_pill.py` / `settings_ui.py` — compact pill and settings UI.
- `test_regressions.py` / `test_chat_correction.py` / `test_components.py` — validation.

## Important limitations

Whisper is batch ASR adapted to low-latency chunking, not native streaming ASR. Very short
or heavily code-switched speech can still confuse automatic language detection. Screenshot
OCR is advisory and can misread letters; correction validators are therefore designed to
fail closed. Live cancellation does not erase text already inserted because safe rollback
cannot be guaranteed after the user moves the caret, edits text, or changes focus.
