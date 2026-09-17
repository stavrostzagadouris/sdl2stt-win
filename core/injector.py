"""
Text injector for Windows using Win32 SendInput with KEYEVENTF_UNICODE.
Types transcribed text into the currently focused application/window.
"""
import ctypes
from ctypes import wintypes
import time
import logging

logger = logging.getLogger('sdl2stt')

INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_RETURN = 0x0D


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


user32 = ctypes.windll.user32

# Virtual key codes for modifier keys
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LMENU = 0xA4   # Left Alt
VK_RMENU = 0xA5   # Right Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C

_MODIFIER_VKS = [
    VK_LCONTROL, VK_RCONTROL,
    VK_LSHIFT, VK_RSHIFT,
    VK_LMENU, VK_RMENU,
    VK_LWIN, VK_RWIN,
]


def _release_modifiers():
    """Send KEY_UP for every modifier key to clear stuck-down state."""
    releases = []
    for vk in _MODIFIER_VKS:
        up = INPUT(type=INPUT_KEYBOARD)
        up.union.ki = KEYBDINPUT(
            wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None
        )
        releases.append(up)
    if releases:
        arr = (INPUT * len(releases))(*releases)
        user32.SendInput(len(releases), arr, ctypes.sizeof(INPUT))


def inject_text(text: str, char_delay: float = 0.001):
    """
    Injects Unicode text into whatever window currently has focus.
    Uses KEYEVENTF_UNICODE so characters are entered directly without
    touching the system clipboard.
    """
    if not text:
        return

    text = text.rstrip('\r\n')
    if not text:
        return

    # Force-release all modifier keys before injecting.  In push-to-talk
    # mode Ctrl (and possibly others) is still "down" in the OS input queue
    # when this runs, so every injected char would be interpreted as a
    # shortcut (Ctrl+H, Ctrl+G …) instead of text.  This is especially bad
    # over RDP where there's extra latency between physical key-up and the
    # remote OS registering it.
    _release_modifiers()
    time.sleep(0.05)  # let the OS / RDP input pipeline settle

    inputs = []
    # Encode to UTF-16 LE to correctly split into 16-bit code units (handling surrogate pairs)
    utf16_bytes = text.encode("utf-16le")
    words = [
        utf16_bytes[i] | (utf16_bytes[i + 1] << 8)
        for i in range(0, len(utf16_bytes), 2)
    ]

    for code_unit in words:
        if code_unit == 0x0A:  # '\n' -> enter key
            down = INPUT(type=INPUT_KEYBOARD)
            down.union.ki = KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)
            up = INPUT(type=INPUT_KEYBOARD)
            up.union.ki = KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)
        elif code_unit == 0x0D:  # '\r' -> skip carriage return as '\n' sends enter
            continue
        else:
            down = INPUT(type=INPUT_KEYBOARD)
            down.union.ki = KEYBDINPUT(
                wVk=0,
                wScan=code_unit,
                dwFlags=KEYEVENTF_UNICODE,
                time=0,
                dwExtraInfo=None,
            )
            up = INPUT(type=INPUT_KEYBOARD)
            up.union.ki = KEYBDINPUT(
                wVk=0,
                wScan=code_unit,
                dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                time=0,
                dwExtraInfo=None,
            )
        inputs.extend([down, up])

    if inputs:
        # Send inputs in chunks to avoid buffer limitations on large blocks of text
        chunk_size = 64
        for i in range(0, len(inputs), chunk_size):
            chunk = inputs[i : i + chunk_size]
            arr = (INPUT * len(chunk))(*chunk)
            ret = user32.SendInput(len(chunk), arr, ctypes.sizeof(INPUT))
            if ret == 0:
                err = ctypes.GetLastError()
                logger.warning(f"SendInput failed with error code {err}. The target window may be elevated (UIPI).")
            if char_delay > 0:
                time.sleep(char_delay)
