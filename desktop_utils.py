import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
DESKTOP_ALL_ACCESS = 0x01FF
user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
user32.GetThreadDesktop.restype = wintypes.HANDLE
user32.OpenDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenDesktopW.restype = wintypes.HANDLE
user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]

def attach_to_default_desktop() -> bool:
    """Attaches the calling thread to the interactive 'Default' desktop so GUI & hooks are visible to the user."""
    try:
        h_current = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        buf = ctypes.create_unicode_buffer(512)
        user32.GetUserObjectInformationW(h_current, 2, buf, ctypes.sizeof(buf), None)
        if buf.value == "Default":
            return True

        h_default = user32.OpenDesktopW("Default", 0, False, DESKTOP_ALL_ACCESS)
        if h_default:
            res = user32.SetThreadDesktop(h_default)
            if res:
                print("[Desktop] Thread successfully attached to 'Default' desktop.")
                return True
    except Exception as e:
        print(f"[Desktop] Could not attach to Default desktop: {e}")
    return False

if __name__ == "__main__":
    attached = attach_to_default_desktop()
    print(f"Attach result: {attached}")
