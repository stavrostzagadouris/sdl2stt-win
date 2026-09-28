"""
Global hotkey listener for Windows.
Supports both hold-to-talk (push-to-talk) and toggle modes.
"""
import ctypes
import logging
import threading
import queue
import keyboard

logger = logging.getLogger("sdl2stt")

# The `keyboard` library reports modifiers as "left ctrl", "windows", etc.
# Map the names people type in config.json onto those canonical forms.
MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "windows",
    "windows": "windows",
    "super": "windows",
    "meta": "windows",
    "cmd": "windows",
}

user32 = ctypes.windll.user32
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.MapVirtualKeyW.restype = ctypes.c_uint
user32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]

MAPVK_VSC_TO_VK_EX = 3

# Left/right virtual-key codes for each canonical modifier.
MODIFIER_VKS = {
    "ctrl": (0xA2, 0xA3),      # VK_LCONTROL, VK_RCONTROL
    "shift": (0xA0, 0xA1),     # VK_LSHIFT,   VK_RSHIFT
    "alt": (0xA4, 0xA5),       # VK_LMENU,    VK_RMENU
    "windows": (0x5B, 0x5C),   # VK_LWIN,     VK_RWIN
}


def _canonical(part: str) -> str:
    part = part.strip().lower()
    return MODIFIER_ALIASES.get(part, part)


def _vk_is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class HotkeyListener:
    """
    Edge-detects a hotkey combo from a global keyboard hook.

    Key state is read back from Windows via GetAsyncKeyState rather than
    accumulated from hook events.  A WH_KEYBOARD_LL hook can silently miss
    events — Windows skips hooks that overrun LowLevelHooksTimeout, and this
    one gets flooded by the bulk SendInput text injection in core.injector.
    A missed key-up used to latch that key "down" forever, after which the
    trigger key alone (e.g. plain Space for a "super+space" hotkey) fired the
    hotkey on every press.
    """

    def __init__(self, hotkey: str = "win+space", mode: str = "hold", on_start=None, on_stop=None, on_toggle=None):
        self.hotkey = hotkey.lower()
        self.mode = mode.lower()  # 'hold' or 'toggle'
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_toggle = on_toggle
        self._running = False

        self._hotkey_parts = [_canonical(p) for p in self.hotkey.split("+")]
        self._modifiers = set(MODIFIER_ALIASES.values())

        # Fallback latch, used only for non-modifier keys whose scan code we
        # have not been able to resolve to a virtual-key code yet.
        self._keys_down = {part: False for part in self._hotkey_parts}
        self._trigger_vks = {}
        self._was_all_down = False

        self._queue = queue.Queue()
        self._worker_thread = None
        self._hook = None

    def _process_queue(self):
        while True:
            cmd = self._queue.get()
            if cmd is None:
                break
            try:
                if cmd == 'START' and self.on_start:
                    self.on_start()
                elif cmd == 'STOP' and self.on_stop:
                    self.on_stop()
                elif cmd == 'TOGGLE' and self.on_toggle:
                    self.on_toggle()
            except Exception as e:
                logger.error(f"Error in hotkey callback: {e}")

    def _matches(self, part: str, name: str) -> bool:
        """Does this event name refer to `part`?  Modifiers are sided."""
        if part in self._modifiers:
            return part in name
        return name == part

    def _part_is_down(self, part: str, name: str, is_down: bool) -> bool:
        """
        Resolve whether `part` is currently held.

        The event being handled wins for its own key: a low-level hook runs
        before Windows updates the async key state, so GetAsyncKeyState does
        not yet reflect the press we are handling right now.  Every other part
        was pressed earlier, so the OS state for it is accurate — and, unlike a
        latch, cannot be left stale by a dropped event.
        """
        if self._matches(part, name):
            return is_down
        if part in self._modifiers:
            return any(_vk_is_down(vk) for vk in MODIFIER_VKS[part])
        vk = self._trigger_vks.get(part)
        if vk:
            return _vk_is_down(vk)
        return self._keys_down.get(part, False)

    def _on_key_event(self, e: keyboard.KeyboardEvent):
        if not self._running:
            return

        # Some key events (e.g. RDP virtual keys) have name=None — ignore them
        if e.name is None:
            return

        name = e.name.lower()
        is_down = (e.event_type == keyboard.KEY_DOWN)

        # Only hotkey keys can change the combo state.  Returning early keeps
        # the hook callback cheap for ordinary typing, which is itself part of
        # not getting skipped by Windows.
        touched = [p for p in self._hotkey_parts if self._matches(p, name)]
        if not touched:
            return

        for part in touched:
            self._keys_down[part] = is_down
            if part not in self._modifiers and is_down and e.scan_code:
                vk = user32.MapVirtualKeyW(e.scan_code, MAPVK_VSC_TO_VK_EX)
                if vk:
                    self._trigger_vks[part] = vk

        all_down = all(
            self._part_is_down(part, name, is_down) for part in self._hotkey_parts
        )
        transition_to_down = all_down and not self._was_all_down
        transition_to_up = not all_down and self._was_all_down
        self._was_all_down = all_down

        if self.mode == "hold":
            # Push-to-talk hold mode.  Whether a recording is actually running
            # is the recorder's business, not ours — guarding on a local copy
            # of that state used to swallow presses and emit unpaired stops.
            if transition_to_down:
                logger.info(f"Hotkey hold started ({self.hotkey})")
                self._queue.put('START')
            elif transition_to_up:
                logger.info(f"Hotkey hold released ({self.hotkey})")
                self._queue.put('STOP')

        elif self.mode == "toggle":
            # Toggle mode: trigger on transition to all-pressed (edge detection)
            if transition_to_down:
                logger.info(f"Hotkey toggle triggered ({self.hotkey})")
                self._queue.put('TOGGLE')

    def start(self):
        """Starts the global keyboard hook."""
        if self._running:
            return
        self._running = True

        self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()

        self._hook = keyboard.hook(self._on_key_event, suppress=False)
        logger.info(f"Global hotkey hook installed for {self.hotkey} (mode: {self.mode})")

    def stop(self):
        """Removes the keyboard hook."""
        self._running = False

        self._queue.put(None)

        if self._hook is not None:
            try:
                keyboard.unhook(self._hook)
                self._hook = None
            except Exception:
                pass
