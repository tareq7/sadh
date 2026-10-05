from pathlib import Path
import win32com.client

root_dir = Path(__file__).resolve().parent
exe_path = root_dir / "Sadh.exe"
target_vbs = root_dir / "start_hidden.vbs"

shell = win32com.client.Dispatch("WScript.Shell")
desktop = shell.SpecialFolders("Desktop")
shortcut_path = Path(desktop) / "Sadh Voice Typing.lnk"
shortcut = shell.CreateShortCut(str(shortcut_path))

if exe_path.exists():
    shortcut.TargetPath = str(exe_path)
    shortcut.Arguments = ""
else:
    shortcut.TargetPath = "wscript.exe"
    shortcut.Arguments = f'"{target_vbs}"'

shortcut.WorkingDirectory = str(root_dir)
shortcut.Description = "Sadh (صَدْح) Voice Typing"
icon_path = root_dir / "assets" / "voxtype.ico"
if icon_path.exists():
    shortcut.IconLocation = f"{icon_path},0"
shortcut.Save()
print(f"[OK] Desktop shortcut created at: {shortcut_path}")
