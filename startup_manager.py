import sys
import os
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Sadh"
LEGACY_APP_NAMES = ("VoxType", "WinVoiceGroq")

def get_launcher_path() -> Path:
    """Returns absolute path to launcher executable or script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve().parent / "start_hidden.vbs"

def is_start_with_windows_enabled() -> bool:
    """Checks VoxType and its previous value name for an existing startup entry."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE)
        try:
            for value_name in (APP_NAME, *LEGACY_APP_NAMES):
                try:
                    val, _ = winreg.QueryValueEx(key, value_name)
                    if val:
                        return True
                except FileNotFoundError:
                    continue
            return False
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False
    except Exception as e:
        print(f"[StartupManager] Error querying startup status: {e}")
        return False

def set_start_with_windows(enable: bool) -> bool:
    """Enables or disables VoxType startup and removes the legacy Run value."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
        if enable:
            launcher_path = get_launcher_path()
            if getattr(sys, "frozen", False):
                cmd = f'"{launcher_path}"'
            else:
                cmd = f'wscript.exe "{launcher_path}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            for legacy_name in LEGACY_APP_NAMES:
                try:
                    winreg.DeleteValue(key, legacy_name)
                except FileNotFoundError:
                    pass
            print(f"[StartupManager] Enabled {APP_NAME} startup -> {cmd}")
        else:
            removed = False
            for value_name in (APP_NAME, *LEGACY_APP_NAMES):
                try:
                    winreg.DeleteValue(key, value_name)
                    removed = True
                except FileNotFoundError:
                    pass
            if removed:
                print("[StartupManager] Disabled Windows startup.")
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[StartupManager] Failed to update startup registry: {e}")
        return False

if __name__ == "__main__":
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    if action == "enable":
        set_start_with_windows(True)
    elif action == "disable":
        set_start_with_windows(False)
    else:
        status = "ENABLED" if is_start_with_windows_enabled() else "DISABLED"
        print(f"{APP_NAME} Windows Startup: {status}")
