"""Build the Windows executable without embedding machine-local credentials.

Requires: python -m pip install pyinstaller
"""
import os
import subprocess
import sys
from pathlib import Path


def build_command(root_dir: Path, python_executable: str = sys.executable) -> list[str]:
    """Construct a deterministic PyInstaller command for the Windows desktop app."""
    root_dir = Path(root_dir).resolve()
    return [
        python_executable, "-m", "PyInstaller",
        "--noconsole",
        "--onefile",
        "--name", "Sadh",
        "--icon", str(root_dir / "assets" / "voxtype.ico"),
        "--add-data", f"{root_dir / 'assets'}{os.pathsep}assets",
        "--collect-all", "customtkinter",
        "--collect-all", "sounddevice",
        "--collect-all", "soundfile",
        "--collect-all", "pystray",
        "--clean",
        str(root_dir / "run.py"),
    ]


def main() -> int:
    if sys.platform != "win32":
        print("Sadh builds must run on Windows.", file=sys.stderr)
        return 1
    root_dir = Path(__file__).resolve().parent
    command = build_command(root_dir)
    print("[Build] Compiling Sadh standalone Windows executable...")
    result = subprocess.run(command, cwd=root_dir, check=False)
    if result.returncode:
        print(f"[Build FAILED] Exit code: {result.returncode}", file=sys.stderr)
        return result.returncode

    exe_path = root_dir / "dist" / "Sadh.exe"
    if not exe_path.is_file():
        print("[Build FAILED] PyInstaller exited successfully but produced no executable.", file=sys.stderr)
        return 1
    print(f"[Build SUCCESS] {exe_path} ({exe_path.stat().st_size / 1048576:.2f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
