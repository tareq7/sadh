# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in Sadh, please do **not** open a public issue.

Instead, please send an advisory or report directly to the repository maintainer through GitHub Security Advisories or by emailing `najetareqz@gmail.com`.

### Security Architecture & Privacy

- **Local-First**: Sadh runs entirely on your local machine. No keyloggers, no telemetry, and no third-party analytics are embedded.
- **API Keys**: Your Groq API key is stored locally in `config.json` inside your user directory. It is never transmitted anywhere other than direct, encrypted HTTPS requests to Groq's official API (`https://api.groq.com/openai/v1/audio/transcriptions`).
- **Audio Privacy**: Audio recordings are held in temporary memory buffers and purged immediately once processed.
