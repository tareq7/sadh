"""Cached Win32 per-pixel-alpha surface for a Tk-owned borderless window."""
import ctypes as c
from ctypes import wintypes as w

u = c.WinDLL("user32", use_last_error=True)
g = c.WinDLL("gdi32", use_last_error=True)
LAYERED = 0x80000

class SIZE(c.Structure):
    _fields_ = [("cx", w.LONG), ("cy", w.LONG)]

class BLEND(c.Structure):
    _fields_ = [("operation", w.BYTE), ("flags", w.BYTE),
                ("opacity", w.BYTE), ("alpha", w.BYTE)]

class BITMAPINFOHEADER(c.Structure):
    _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                ("planes", w.WORD), ("bits", w.WORD), ("compression", w.DWORD),
                ("image_size", w.DWORD), ("xppm", w.LONG), ("yppm", w.LONG),
                ("used", w.DWORD), ("important", w.DWORD)]

class BITMAPINFO(c.Structure):
    _fields_ = [("header", BITMAPINFOHEADER), ("colors", w.DWORD * 1)]

u.GetWindowRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
u.GetWindowRect.restype = w.BOOL
u.GetWindowLongW.argtypes = [w.HWND, c.c_int]
u.GetWindowLongW.restype = w.LONG
u.SetWindowLongW.argtypes = [w.HWND, c.c_int, w.LONG]
u.SetWindowLongW.restype = w.LONG
u.SetThreadDpiAwarenessContext.argtypes = [c.c_void_p]
u.SetThreadDpiAwarenessContext.restype = c.c_void_p
u.SetLayeredWindowAttributes.argtypes = [w.HWND, w.DWORD, w.BYTE, w.DWORD]
u.SetLayeredWindowAttributes.restype = w.BOOL
u.UpdateLayeredWindow.argtypes = [
    w.HWND, w.HDC, c.POINTER(w.POINT), c.POINTER(SIZE), w.HDC,
    c.POINTER(w.POINT), w.DWORD, c.POINTER(BLEND), w.DWORD,
]
u.UpdateLayeredWindow.restype = w.BOOL
g.CreateCompatibleDC.argtypes = [w.HDC]
g.CreateCompatibleDC.restype = w.HDC
g.CreateDIBSection.argtypes = [w.HDC, c.POINTER(BITMAPINFO), w.UINT,
                              c.POINTER(c.c_void_p), w.HANDLE, w.DWORD]
g.CreateDIBSection.restype = w.HBITMAP
g.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
g.SelectObject.restype = w.HGDIOBJ
g.DeleteObject.argtypes = [w.HGDIOBJ]
g.DeleteDC.argtypes = [w.HDC]

class LayeredSurface:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.dc = self.bitmap = self.previous = None
        self.bits = c.c_void_p()
        self.dimensions = None
        # Tk's color-key API must be reset before UpdateLayeredWindow is legal.
        style = u.GetWindowLongW(hwnd, -20)
        u.SetWindowLongW(hwnd, -20, style & ~LAYERED)
        u.SetWindowLongW(hwnd, -20, style | LAYERED)

    def pixel_size(self):
        context = u.SetThreadDpiAwarenessContext(c.c_void_p(-4))
        try:
            rect = w.RECT()
            if not u.GetWindowRect(self.hwnd, c.byref(rect)):
                raise c.WinError(c.get_last_error())
            return max(1, rect.right-rect.left), max(1, rect.bottom-rect.top)
        finally:
            if context:
                u.SetThreadDpiAwarenessContext(context)

    def _allocate(self, size):
        if self.dimensions == size:
            return
        self.close()
        width, height = size
        self.dc = g.CreateCompatibleDC(None)
        info = BITMAPINFO()
        info.header = BITMAPINFOHEADER(c.sizeof(BITMAPINFOHEADER), width, -height,
                                      1, 32, 0, width*height*4, 0, 0, 0, 0)
        self.bitmap = g.CreateDIBSection(self.dc, c.byref(info), 0,
                                       c.byref(self.bits), None, 0)
        if not self.dc or not self.bitmap or not self.bits.value:
            self.close()
            raise c.WinError(c.get_last_error())
        self.previous = g.SelectObject(self.dc, self.bitmap)
        self.dimensions = size

    def draw(self, image):
        context = u.SetThreadDpiAwarenessContext(c.c_void_p(-4))
        try:
            # Positions remain managed by Tk. Never resize the native window
            # from bitmap dimensions: that caused DPI drift in earlier designs.
            size = self.pixel_size()
            if image.size != size:
                image = image.resize(size)
            self._allocate(size)
            data = image.convert("RGBa").tobytes("raw", "BGRa")
            c.memmove(self.bits, data, len(data))
            source = w.POINT(0, 0)
            native_size = SIZE(*size)
            blend = BLEND(0, 0, 208, 1)
            if not u.UpdateLayeredWindow(self.hwnd, None, None, c.byref(native_size),
                                         self.dc, c.byref(source), 0, c.byref(blend), 2):
                raise c.WinError(c.get_last_error())
        finally:
            if context:
                u.SetThreadDpiAwarenessContext(context)

    def restore_color_key(self):
        u.SetLayeredWindowAttributes(self.hwnd, 0x00010000, 255, 3)
        self.close()

    def close(self):
        if self.dc and self.previous:
            g.SelectObject(self.dc, self.previous)
        if self.bitmap:
            g.DeleteObject(self.bitmap)
        if self.dc:
            g.DeleteDC(self.dc)
        self.dc = self.bitmap = self.previous = None
        self.dimensions = None
