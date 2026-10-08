# Contributing to Sadh (صَدْح)

We welcome contributions, bug fixes, dialect prompts, and improvements!

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/tareq7/sadh.git
   cd sadh
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   python -m pip install -r requirements.txt pytest
   ```

4. Configure your environment:
   ```bash
   copy config.json.example config.json
   # Edit config.json to insert your Groq API key
   ```

5. Run the test suite:
   ```bash
   python -m pytest -v
   ```

6. Start in development mode:
   ```bash
   python main.py
   ```

## Contribution Guidelines

- Keep pull requests focused on a single feature or bug fix.
- Ensure all tests pass (`pytest -v`) before opening a PR.
- Preserve zero focus theft (`WS_EX_NOACTIVATE`) when modifying UI code.
- Declare runtime dependencies in `pyproject.toml` only; `requirements.txt` installs the project so the two lists cannot drift.
- Avoid introducing external runtime dependencies unless necessary.
- Do not package user configuration files, API keys, recordings, or runtime logs.
- Update release-tool tests when changing `build_exe.py` or console entry points.
