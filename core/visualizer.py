"""
Visualizer window for sdl2stt-win using SDL2 (via pygame-ce).
Displays a bottom-center level-meter bar matching the cleanTTS palette.
Uses 64-bit Win32 prototypes and WS_EX_NOACTIVATE & WS_EX_TOPMOST so keyboard focus is never stolen.
"""
import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import ctypes
from ctypes import wintypes
import math
import threading
import time
import pygame

# cleanTTS Color Palette
C_BG = (23, 26, 33)         # #171a21
C_BORDER = (42, 47, 58)     # #2a2f3a
C_DIM = (42, 47, 58)        # #2a2f3a
C_LIT = (94, 161, 255)      # #5ea1ff

BAR_W = 280
BAR_H = 48
NBARS = 16
BAR_MARG = 24

# Win32 Constants
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = ctypes.c_void_p(-1)
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
MONITOR_DEFAULTTONEAREST = 2

user32 = ctypes.windll.user32

# Explicit 64-bit Win32 function signatures
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

if hasattr(user32, "GetWindowLongPtrW"):
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _get_long = user32.GetWindowLongPtrW
    _set_long = user32.SetWindowLongPtrW
else:
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user32.SetWindowLongW.restype = wintypes.LONG
    _get_long = user32.GetWindowLongW
    _set_long = user32.SetWindowLongW


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR


def get_current_monitor_work_area():
    """Returns the working area (left, top, right, bottom) of the focused/nearest monitor."""
    fg = user32.GetForegroundWindow()
    hmon = user32.MonitorFromWindow(fg, MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom


class VisualizerBar:
    def __init__(self, get_level_fn):
        """
        get_level_fn: callback function returning normalized mic peak level [0.0, 1.0]
        """
        self.get_level_fn = get_level_fn
        self._visible = False
        self._running = False
        self._thread = None
        self._screen = None
        self._hwnd = None
        self._level = 0.0

    def start(self):
        """Initializes the background window and render loop thread."""
        if self._running:
            return
        self._running = True
        self._init_event = threading.Event()
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()
        self._init_event.wait(timeout=2.0)

    def show(self):
        """Unhides the visualizer bar on the active monitor without stealing focus."""
        if not self._running or not self._hwnd:
            return

        self._level = 0.0
        self._visible = True

        left, top, right, bottom = get_current_monitor_work_area()
        mon_w = right - left
        x = left + (mon_w - BAR_W) // 2
        y = bottom - BAR_H - BAR_MARG

        user32.SetWindowPos(
            self._hwnd,
            HWND_TOPMOST,
            x,
            y,
            BAR_W,
            BAR_H,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)

    def hide(self):
        """Hides the visualizer bar."""
        if not self._running or not self._hwnd:
            return
        self._visible = False
        user32.ShowWindow(self._hwnd, SW_HIDE)

    def stop(self):
        """Terminates the render loop and destroys the visualizer window."""
        self._running = False
        self._visible = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _draw_frame(self, lit_bars: int):
        self._screen.fill(C_BG)

        # Border
        pygame.draw.rect(self._screen, C_BORDER, (0, 0, BAR_W, BAR_H), width=1)

        # Left active dot (10x10)
        dot_size = 10
        dot_y = (BAR_H - dot_size) // 2
        pygame.draw.rect(self._screen, C_LIT, (16, dot_y, dot_size, dot_size))

        # 16 level bars
        bar_x0 = 44
        bar_w = 9
        bar_step = 12
        bar_h = 26
        bar_y = (BAR_H - bar_h) // 2

        for b in range(NBARS):
            color = C_LIT if b < lit_bars else C_DIM
            x = bar_x0 + b * bar_step
            pygame.draw.rect(self._screen, color, (x, bar_y, bar_w, bar_h))

    def _render_loop(self):
        # Calculate initial position before creating window
        left, top, right, bottom = get_current_monitor_work_area()
        mon_w = right - left
        init_x = left + (mon_w - BAR_W) // 2
        init_y = bottom - BAR_H - BAR_MARG
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{init_x},{init_y}"

        pygame.init()
        self._screen = pygame.display.set_mode((BAR_W, BAR_H), pygame.NOFRAME)
        pygame.display.set_caption("talkwave")
        self._hwnd = pygame.display.get_wm_info()["window"]

        # Configure window styles: NOACTIVATE + TOPMOST + TOOLWINDOW
        cur_ex = _get_long(self._hwnd, GWL_EXSTYLE)
        _set_long(
            self._hwnd,
            GWL_EXSTYLE,
            cur_ex | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
        )

        # Position precisely at bottom center
        user32.SetWindowPos(
            self._hwnd,
            HWND_TOPMOST,
            init_x,
            init_y,
            BAR_W,
            BAR_H,
            SWP_NOACTIVATE,
        )

        # Draw initial blank frame
        self._draw_frame(0)
        pygame.display.flip()

        # Hide initially
        user32.ShowWindow(self._hwnd, SW_HIDE)
        self._init_event.set()

        clock = pygame.time.Clock()
        while self._running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._running = False
                    break

            if self._visible:
                current_peak = self.get_level_fn()
                if current_peak > 0.001:
                    target_lvl = min(1.0, math.sqrt(current_peak * 2.5))
                else:
                    target_lvl = 0.0

                self._level = (
                    target_lvl
                    if target_lvl > self._level
                    else self._level * 0.75
                )
                self._level = min(1.0, max(0.0, self._level))
                lit_count = int(self._level * NBARS + 0.5)

                self._draw_frame(lit_count)
                pygame.display.flip()

            clock.tick(30)

        try:
            pygame.quit()
        except Exception:
            pass
