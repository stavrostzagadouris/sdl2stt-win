"""
Global hotkey listener for Windows.
Supports both hold-to-talk (push-to-talk) and toggle modes for Ctrl+Space.
"""
import logging
import threading
import time
import keyboard

logger = logging.getLogger("sdl2stt")


class HotkeyListener:
    def __init__(self, hotkey: str = "ctrl+space", mode: str = "hold", on_start=None, on_stop=None, on_toggle=None):
        self.hotkey = hotkey.lower()
        self.mode = mode.lower()  # 'hold' or 'toggle'
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_toggle = on_toggle
        self._running = False
        self._recording = False
        self._ctrl_down = False
        self._space_down = False
        self._lock = threading.Lock()

    def is_recording(self):
        with self._lock:
            return self._recording

    def set_recording(self, state: bool):
        with self._lock:
            self._recording = state

    def _on_key_event(self, e: keyboard.KeyboardEvent):
        if not self._running:
            return

        is_down = (e.event_type == keyboard.KEY_DOWN)

        # Track ctrl and space keys
        if "ctrl" in e.name.lower():
            self._ctrl_down = is_down
        elif e.name.lower() == "space":
            self._space_down = is_down

        hotkey_active = self._ctrl_down and self._space_down

        if self.mode == "hold":
            # Push-to-talk hold mode
            with self._lock:
                rec = self._recording

            if hotkey_active and not rec:
                with self._lock:
                    self._recording = True
                logger.info("Hotkey hold started (Ctrl+Space)")
                if self.on_start:
                    threading.Thread(target=self.on_start, daemon=True).start()

            elif not hotkey_active and rec:
                with self._lock:
                    self._recording = False
                logger.info("Hotkey hold released (Ctrl+Space)")
                if self.on_stop:
                    threading.Thread(target=self.on_stop, daemon=True).start()

        elif self.mode == "toggle":
            # Toggle mode: trigger on key down of space while ctrl is pressed
            if is_down and e.name.lower() == "space" and self._ctrl_down:
                logger.info("Hotkey toggle triggered (Ctrl+Space)")
                if self.on_toggle:
                    threading.Thread(target=self.on_toggle, daemon=True).start()

    def start(self):
        """Starts the global keyboard hook."""
        if self._running:
            return
        self._running = True
        keyboard.hook(self._on_key_event, suppress=False)
        logger.info(f"Global hotkey hook installed for {self.hotkey} (mode: {self.mode})")

    def stop(self):
        """Removes the keyboard hook."""
        self._running = False
        try:
            keyboard.unhook_all()
        except Exception:
            pass
