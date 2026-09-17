"""
Text injector for Windows.
Pastes transcribed text into the currently focused application via clipboard,
with automatic save/restore of the user's clipboard contents.
"""
import ctypes
from ctypes import wintypes
import time
import logging

logger = logging.getLogger('sdl2stt')

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56

# Clipboard constants
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


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
kernel32 = ctypes.windll.kernel32

# Explicit ctypes signatures for clipboard memory operations
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

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


def _open_clipboard_with_retry(max_attempts=10, delay=0.05):
    """Try to open the clipboard, retrying on failure.

    RDP's rdpclip.exe frequently holds the clipboard locked during sync,
    so a single attempt often fails.  Retrying with a short backoff is the
    standard workaround.
    """
    for attempt in range(max_attempts):
        if user32.OpenClipboard(0):
            return True
        time.sleep(delay)
    logger.warning("Could not open clipboard after %d attempts", max_attempts)
    return False


def _get_clipboard_text():
    """Read the current clipboard text, or None if empty/unavailable."""
    text = None
    if not _open_clipboard_with_retry():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if handle:
            ptr = kernel32.GlobalLock(handle)
            if ptr:
                text = ctypes.wstring_at(ptr)
                kernel32.GlobalUnlock(handle)
    except Exception:
        pass
    finally:
        user32.CloseClipboard()
    return text


def _set_clipboard_text(text):
    """Write text to the clipboard. Returns True on success."""
    encoded = text.encode("utf-16-le") + b"\x00\x00"
    if not _open_clipboard_with_retry():
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return False
        ctypes.memmove(ptr, encoded, len(encoded))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        return True
    except Exception:
        return False
    finally:
        user32.CloseClipboard()


def _send_paste():
    """Simulate Ctrl+V keystroke."""
    inputs = [
        # Ctrl down
        INPUT(type=INPUT_KEYBOARD),
        # V down
        INPUT(type=INPUT_KEYBOARD),
        # V up
        INPUT(type=INPUT_KEYBOARD),
        # Ctrl up
        INPUT(type=INPUT_KEYBOARD),
    ]
    inputs[0].union.ki = KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)
    inputs[1].union.ki = KEYBDINPUT(wVk=VK_V, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)
    inputs[2].union.ki = KEYBDINPUT(wVk=VK_V, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)
    inputs[3].union.ki = KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)

    arr = (INPUT * 4)(*inputs)
    ret = user32.SendInput(4, arr, ctypes.sizeof(INPUT))
    if ret == 0:
        err = ctypes.GetLastError()
        logger.warning(f"SendInput (paste) failed with error code {err}. The target window may be elevated (UIPI).")


def inject_text(text: str):
    """
    Injects text into the focused window via clipboard paste (Ctrl+V).
    Saves and restores the user's clipboard contents automatically.
    """
    if not text:
        return

    text = text.rstrip('\r\n')
    if not text:
        return

    # Force-release all modifier keys before injecting.  In push-to-talk
    # mode Ctrl (and possibly others) is still "down" in the OS input queue
    # when this runs.  This is especially important over RDP.
    _release_modifiers()
    time.sleep(0.05)

    # Save current clipboard, paste, then restore
    old_clip = _get_clipboard_text()

    if not _set_clipboard_text(text):
        logger.error("Failed to write text to clipboard for injection")
        return

    _send_paste()

    # Wait for the paste to be consumed by the target app before restoring
    time.sleep(0.1)

    # Restore whatever was on the clipboard before
    if old_clip is not None:
        _set_clipboard_text(old_clip)
    else:
        # Clear clipboard back to empty
        if _open_clipboard_with_retry():
            user32.EmptyClipboard()
            user32.CloseClipboard()
