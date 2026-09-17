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

    # Small pause to allow key release state to settle
    time.sleep(0.02)

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
