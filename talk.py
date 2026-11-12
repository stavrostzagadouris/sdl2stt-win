"""
talk — Push-to-talk voice input for Windows: record -> STT -> type transcript into focused field.

Usage:
  talk daemon     Run the background hotkey daemon (Win+Space push-to-talk)
  talk start      Begin recording
  talk stop       Stop, transcribe, and inject text
  talk toggle     Toggle recording on / off
  talk status     Check daemon status
  talk exit       Stop the background daemon
  talk install-startup    Start the daemon automatically when you log in
  talk uninstall-startup  Remove the automatic start
"""
import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import json
import logging
import logging.handlers
import subprocess
import sys
import threading
import time
import winsound

from core import startup
from core.audio import AudioRecorder, choose_microphone
from core.hotkey import HotkeyListener
from core.injector import inject_text
from core.ipc import IPCServer, send_command
from core.noisegate import evaluate_audio_gate
from core.stt_client import STTClient
from core.visualizer import VisualizerBar

# Paths & logging
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", BASE_DIR), "sdl2stt")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "talk.log")

handlers = [
    logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
]
if sys.stdout is not None:
    handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=handlers,
)
logger = logging.getLogger("sdl2stt")

# Route uncaught exceptions to the log so crashes under pythonw aren't silent
def _handle_exception(exc_type, exc_val, exc_tb):
    logger.critical("Uncaught exception", exc_info=(exc_type, exc_val, exc_tb))

sys.excepthook = _handle_exception
threading.excepthook = lambda args: _handle_exception(
    args.exc_type, args.exc_value, args.exc_traceback
)


def load_env():
    """Loads environment variables from .env file if present."""
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception as e:
            logger.error(f"Error loading .env file: {e}")


def load_config():
    load_env()
    defaults = {
        "stt_url": "http://127.0.0.1:5000/v1/audio/transcriptions",
        "stt_peer": "",
        "hotkey": "super+space",
        "mode": "hold",
        "gate": 0.02,
        "min_loud_windows": 3,
        "sample_rate": 16000,
        "channels": 1,
        "play_sound_cues": False,
        "max_seconds": 120,        # a recording is stopped automatically after this long
        "log_transcripts": False,  # write dictated text to talk.log
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                defaults.update(user_cfg)
        except Exception as e:
            logger.error(f"Error loading config.json: {e}")

    # Environment variable override (matches Linux behavior)
    if "TALK_STT_URL" in os.environ:
        defaults["stt_url"] = os.environ["TALK_STT_URL"]
    if "TALK_STT_PEER" in os.environ:
        defaults["stt_peer"] = os.environ["TALK_STT_PEER"]
    if "TALK_GATE" in os.environ:
        try:
            defaults["gate"] = float(os.environ["TALK_GATE"])
        except ValueError:
            pass

    return defaults


class TalkDaemon:
    def __init__(self, config):
        self.config = config
        self.recorder = AudioRecorder(
            sample_rate=config.get("sample_rate", 16000),
            channels=config.get("channels", 1),
            device=config.get("device"),
        )
        self.visualizer = VisualizerBar(
            get_level_fn=self.recorder.get_peak_level,
            bar_width=config.get("bar_width", 280),
            bar_height=config.get("bar_height", 48),
            bar_margin_bottom=config.get("bar_margin_bottom", 24),
        )
        self.stt_client = STTClient(
            endpoint_url=config.get("stt_url"),
            peer_name=config.get("stt_peer", ""),
        )
        self.hotkey_listener = HotkeyListener(
            hotkey=config.get("hotkey", "super+space"),
            mode=config.get("mode", "hold"),
            on_start=self.start_recording,
            on_stop=self.stop_recording,
            on_toggle=self.toggle_recording,
        )
        self.ipc_server = IPCServer(handler_callback=self.handle_ipc)
        self._action_lock = threading.Lock()
        self._inject_lock = threading.Lock()  # serialize text injections
        self._sound_cues = config.get("play_sound_cues", True)
        self._max_seconds = float(config.get("max_seconds", 120))
        self._max_timer = None
        self._viz_lock = threading.Lock()
        self._pending = 0  # recordings stopped but not yet fully processed

    def _refresh_visualizer(self, pending_delta: int = 0):
        """Level meter while recording, sweep while transcribing, hidden otherwise."""
        with self._viz_lock:
            self._pending += pending_delta
            if self.recorder.is_recording:
                self.visualizer.show()
            elif self._pending > 0:
                self.visualizer.show_processing()
            else:
                self.visualizer.hide()

    def _on_max_seconds(self, timer):
        if timer is self._max_timer and self.recorder.is_recording:
            logger.warning(f"Recording hit the {self._max_seconds:.0f}s limit, stopping")
            self.stop_recording()

    def play_sound(self, tone: str):
        if not self._sound_cues:
            return
        try:
            if tone == "start":
                winsound.Beep(1200, 35)
            elif tone == "stop":
                winsound.Beep(900, 35)
            elif tone == "quiet":
                winsound.Beep(450, 60)
            elif tone == "error":
                winsound.Beep(300, 100)
        except Exception:
            pass

    def start_recording(self):
        with self._action_lock:
            if self.recorder.is_recording:
                return "ALREADY_RECORDING"
            logger.info("Recording started")
            self.recorder.start()
            self._refresh_visualizer()
            timer = threading.Timer(self._max_seconds, lambda: self._on_max_seconds(timer))
            timer.daemon = True
            self._max_timer = timer
            timer.start()
            self.play_sound("start")
            return "RECORDING_STARTED"

    def stop_recording(self):
        with self._action_lock:
            if not self.recorder.is_recording:
                return "NOT_RECORDING"
            logger.info("Recording stopped, processing...")
            if self._max_timer is not None:
                self._max_timer.cancel()
                self._max_timer = None
            wav_bytes = self.recorder.stop()
            self._refresh_visualizer(pending_delta=1)
            self.play_sound("stop")

        # Process audio asynchronously so we don't hold the lock or block IPC
        threading.Thread(
            target=self._process_and_refresh, args=(wav_bytes,), daemon=True
        ).start()
        return "PROCESSING"

    def toggle_recording(self):
        if self.recorder.is_recording:
            return self.stop_recording()
        else:
            return self.start_recording()

    def _process_and_refresh(self, wav_bytes: bytes):
        try:
            self._process_recording(wav_bytes)
        finally:
            self._refresh_visualizer(pending_delta=-1)

    def _process_recording(self, wav_bytes: bytes):
        if not wav_bytes:
            logger.info("Empty audio buffer, discarding.")
            return

        # 1. Noise gate check
        gate_threshold = self.config.get("gate", 0.02)
        min_windows = self.config.get("min_loud_windows", 3)
        peak_rms, loud_windows, passed = evaluate_audio_gate(
            wav_bytes, threshold=gate_threshold, min_loud_windows=min_windows
        )
        logger.info(
            f"Noise gate: peak_rms={peak_rms:.4f}, loud_windows={loud_windows} -> {'PASS' if passed else 'QUIET'}"
        )

        if not passed:
            logger.info("Speech below noise gate floor, discarding.")
            self.play_sound("quiet")
            return

        # 2. STT Transcription
        t0 = time.time()
        try:
            logger.info(f"POSTing audio to STT: {self.stt_client.endpoint_url}")
            text = self.stt_client.transcribe(wav_bytes)
            elapsed = time.time() - t0
            shown = repr(text) if self.config.get("log_transcripts") else f"{len(text)} characters"
            logger.info(f"STT Response ({elapsed:.2f}s): {shown}")
        except Exception as e:
            logger.error(f"STT error: {e}")
            self.play_sound("error")
            return

        # 3. Text Injection
        if text:
            logger.info(f"Injecting {len(text)} characters into focused window")
            with self._inject_lock:
                inject_text(text)
        else:
            logger.info("Empty transcription received from server")
            self.play_sound("quiet")

    def handle_ipc(self, cmd: str) -> str:
        cmd = cmd.strip().lower()
        if cmd == "start":
            return self.start_recording()
        elif cmd == "stop":
            return self.stop_recording()
        elif cmd == "toggle":
            return self.toggle_recording()
        elif cmd == "status":
            return "RECORDING" if self.recorder.is_recording else "IDLE"
        elif cmd == "exit":
            threading.Thread(target=self.stop, daemon=True).start()
            return "EXITING"
        return "UNKNOWN_COMMAND"

    def run(self):
        logger.info("Initializing sdl2stt-win visualizer and audio system...")
        self.visualizer.start()
        self.hotkey_listener.start()
        logger.info(f"sdl2stt-win daemon is running! Press {self.hotkey_listener.hotkey} to push-to-talk.")
        try:
            self.ipc_server.start()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        logger.info("Stopping daemon...")
        self.hotkey_listener.stop()
        self.visualizer.stop()
        self.ipc_server.stop()
        self.recorder.stop()
        logger.info("Daemon stopped.")


def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "daemon"

    if cmd in ("daemon", "run"):
        cfg = load_config()

        # Interactive mic selection (skipped when stdin is not a terminal,
        # e.g. launched via run_background.vbs or CREATE_NO_WINDOW)
        if sys.stdin is not None and sys.stdin.isatty():
            device = choose_microphone()
            if device is not None:
                cfg["device"] = device
                # Persist selection so background launches use the same mic
                try:
                    disk_cfg = {}
                    if os.path.exists(CONFIG_FILE):
                        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                            disk_cfg = json.load(f)
                    disk_cfg["device"] = device
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(disk_cfg, f, indent=2)
                    logger.info(f"Saved device={device} to config.json")
                except Exception as e:
                    logger.warning(f"Could not save device to config.json: {e}")

        daemon = TalkDaemon(cfg)
        daemon.run()
    elif cmd in ("start", "stop", "toggle", "status", "exit"):
        resp = send_command(cmd)
        if resp == "DAEMON_NOT_RUNNING" and cmd in ("start", "toggle"):
            print("Daemon is not running. Launching background daemon...")
            # Spawn daemon detached
            subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "daemon"],
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0,
            )
            # Poll until daemon is ready (up to 5 seconds)
            for _ in range(25):
                time.sleep(0.2)
                resp = send_command(cmd)
                if resp != "DAEMON_NOT_RUNNING":
                    break
        print(f"{cmd}: {resp}")
    elif cmd == "install-startup":
        try:
            lnk = startup.install(os.path.abspath(__file__))
        except subprocess.CalledProcessError as e:
            print(f"Could not create the startup shortcut: {e.stderr.decode(errors='replace').strip()}")
            sys.exit(1)
        print(f"Created {lnk}")
        print("The daemon will start silently at your next login.")
    elif cmd == "uninstall-startup":
        print("Startup shortcut removed." if startup.uninstall() else "No startup shortcut found.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
