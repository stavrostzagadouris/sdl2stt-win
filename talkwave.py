"""
talkwave.py — Standalone visualizer & audio recorder for Windows.
Direct equivalent of Linux talkwave.c.
When started: creates the bottom-center SDL2 level-meter window immediately and captures mic audio.
When stopped (via stop signal or close): finalizes WAV and exits.
"""
import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import sys
import time
import math
import argparse
import signal
import ctypes
from ctypes import wintypes
import pygame

from core.audio import AudioRecorder

# Colors matched to cleanTTS
C_BG = (23, 26, 33)         # #171a21
C_BORDER = (42, 47, 58)     # #2a2f3a
C_DIM = (42, 47, 58)        # #2a2f3a
C_LIT = (94, 161, 255)      # #5ea1ff

BAR_W = 280
BAR_H = 48
NBARS = 16
BAR_MARG = 24

user32 = ctypes.windll.user32

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

HWND_TOPMOST = ctypes.c_void_p(-1)


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


def get_monitor_coords():
    fg = user32.GetForegroundWindow()
    hmon = user32.MonitorFromWindow(fg, 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    mon_w = mi.rcWork.right - mi.rcWork.left
    x = mi.rcWork.left + (mon_w - BAR_W) // 2
    y = mi.rcWork.bottom - BAR_H - BAR_MARG
    return x, y


def draw_frame(screen, lit_bars: int):
    screen.fill(C_BG)
    pygame.draw.rect(screen, C_BORDER, (0, 0, BAR_W, BAR_H), width=1)
    dot_size = 10
    dot_y = (BAR_H - dot_size) // 2
    pygame.draw.rect(screen, C_LIT, (16, dot_y, dot_size, dot_size))

    bar_x0 = 44
    bar_w = 9
    bar_step = 12
    bar_h = 26
    bar_y = (BAR_H - bar_h) // 2

    for b in range(NBARS):
        color = C_LIT if b < lit_bars else C_DIM
        bx = bar_x0 + b * bar_step
        pygame.draw.rect(screen, color, (bx, bar_y, bar_w, bar_h))


def main():
    parser = argparse.ArgumentParser(description="talkwave audio recorder & visualizer")
    parser.add_argument("--out", default="talk.wav", help="Output WAV path")
    args = parser.parse_args()

    # Audio capture starts immediately
    recorder = AudioRecorder(sample_rate=16000, channels=1)
    recorder.start()

    # Get monitor coordinates
    x, y = get_monitor_coords()

    # Position SDL window before initialization
    os.environ["SDL_VIDEO_WINDOW_POS"] = f"{x},{y}"

    pygame.init()
    screen = pygame.display.set_mode((BAR_W, BAR_H), pygame.NOFRAME)
    pygame.display.set_caption("talkwave")
    hwnd = pygame.display.get_wm_info()["window"]

    # Apply Win32 NOACTIVATE & TOPMOST to keep focus in active app
    cur_ex = _get_long(hwnd, -20)
    _set_long(hwnd, -20, cur_ex | 0x08000000 | 0x00000008 | 0x00000080)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, BAR_W, BAR_H, 0x0010 | 0x0040)

    # Initial draw
    draw_frame(screen, 0)
    pygame.display.flip()

    g_stop = False

    def on_signal(sig, frame):
        nonlocal g_stop
        g_stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    clock = pygame.time.Clock()
    level = 0.0

    try:
        while not g_stop:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    g_stop = True
                    break

            peak = recorder.get_peak_level()
            if peak > 0.001:
                target_lvl = min(1.0, math.sqrt(peak * 2.5))
            else:
                target_lvl = 0.0

            level = target_lvl if target_lvl > level else level * 0.75
            lit = int(min(1.0, max(0.0, level)) * NBARS + 0.5)

            draw_frame(screen, lit)
            pygame.display.flip()
            clock.tick(30)
    finally:
        wav_bytes = recorder.stop()
        if wav_bytes and args.out:
            with open(args.out, "wb") as f:
                f.write(wav_bytes)
        pygame.quit()


if __name__ == "__main__":
    main()
