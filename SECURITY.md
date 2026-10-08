# Security Policy

## Supported versions

Security fixes are targeted at the latest release on the `main` branch.

## Private vulnerability reporting

Do not publish credentials, transcripts, or security findings in a public issue.
Use [GitHub's private vulnerability reporting](https://github.com/tareq7/sadh/security/advisories/new), if enabled, or email the maintainer at `najetareqz@gmail.com`.

## Data handling and trust boundaries

- Sadh is **Windows desktop software with cloud speech recognition**, not an offline solution.
- Audio is sent over HTTPS to Groq for transcription. An optional, **off-by-default** screen-context feature can send a screenshot crop of the foreground window to a Groq model for vocabulary and WhatsApp message correction.
- Dictated text may be temporarily present in the Windows clipboard. Runtime diagnostics may be written to `logs/runtime.log`; review these before sharing bug reports.
- The Groq API key is read from `config.json`, `.env` or `GROQ_API_KEY`. Do not commit credentials or include a real configuration file in distribution packages.
- Release installation verifies the downloaded executable against the SHA-256 checksum published with the same release. This detects a damaged or mismatched binary but is **not** a substitute for independent software signing or verifying the release publisher.
- Global hotkeys, desktop capture and focus-aware text insertion use Windows APIs. Install only software and updates from a source you trust.

## Reporting checklist

Include the affected version, reproduction steps and impact, but redact API keys, recorded speech, screenshots, clipboard contents and personal file paths.
