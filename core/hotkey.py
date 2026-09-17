"""
Global hotkey listener for Windows.
Supports both hold-to-talk (push-to-talk) and toggle modes.
"""
import logging
import threading
import time
import queue
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
        self._lock = threading.Lock()
        
        self._hotkey_parts = [p.strip() for p in self.hotkey.split('+')]
        self._keys_down = {part: False for part in self._hotkey_parts}
        self._was_all_down = False
        self._modifiers = {"ctrl", "alt", "shift", "win", "windows", "meta", "cmd"}
        
        self._queue = queue.Queue()
        self._worker_thread = None
        self._hook = None

    def is_recording(self):
        with self._lock:
            return self._recording

    def set_recording(self, state: bool):
        with self._lock:
            self._recording = state
            
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

    def _on_key_event(self, e: keyboard.KeyboardEvent):
        if not self._running:
            return

        is_down = (e.event_type == keyboard.KEY_DOWN)

        # Some key events (e.g. RDP virtual keys) have name=None — ignore them
        if e.name is None:
            return

        name = e.name.lower()

        # Update key states based on parts
        for part in self._hotkey_parts:
            if part in self._modifiers:
                if part in name:
                    self._keys_down[part] = is_down
            else:
                if name == part:
                    self._keys_down[part] = is_down

        all_down = all(self._keys_down.values())
        transition_to_down = all_down and not self._was_all_down
        transition_to_up = not all_down and self._was_all_down

        if self.mode == "hold":
            # Push-to-talk hold mode
            if transition_to_down:
                with self._lock:
                    if not self._recording:
                        self._recording = True
                        logger.info(f"Hotkey hold started ({self.hotkey})")
                        self._queue.put('START')

            elif transition_to_up:
                with self._lock:
                    if self._recording:
                        self._recording = False
                        logger.info(f"Hotkey hold released ({self.hotkey})")
                        self._queue.put('STOP')

        elif self.mode == "toggle":
            # Toggle mode: trigger on transition to all-pressed (edge detection)
            if transition_to_down:
                logger.info(f"Hotkey toggle triggered ({self.hotkey})")
                self._queue.put('TOGGLE')
                
        self._was_all_down = all_down

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
