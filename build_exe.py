import os
import sys
import subprocess
from pathlib import Path

root_dir = Path(__file__).resolve().parent

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconsole",
    "--onefile",
    "--name", "Sadh",
    "--icon", str(root_dir / "assets" / "voxtype.ico"),
    "--add-data", f"{root_dir / 'assets'};assets",
    "--add-data", f"{root_dir / 'config.json'};.",
    "--collect-all", "customtkinter",
    "--collect-all", "sounddevice",
    "--collect-all", "soundfile",
    "--collect-all", "pystray",
    "--clean",
    str(root_dir / "run.py")
]

print("[Build] Compiling Sadh standalone .exe with PyInstaller...")
print("Command:", " ".join(cmd))
res = subprocess.run(cmd)
if res.returncode == 0:
    exe_path = root_dir / "dist" / "Sadh.exe"
    print(f"\n[Build SUCCESS] Executable created at: {exe_path}")
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"[Build INFO] File size: {size_mb:.2f} MB")
else:
    print(f"\n[Build FAILED] Exit code: {res.returncode}")
    sys.exit(res.returncode)
