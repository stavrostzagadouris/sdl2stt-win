"""
talk — Push-to-talk voice input for Windows: record -> STT -> type transcript into focused field.

Usage:
  talk daemon     Run the background hotkey daemon (Ctrl+Space push-to-talk)
  talk start      Begin recording
  talk stop       Stop, transcribe, and inject text
  talk toggle     Toggle recording on / off
  talk status     Check daemon status
  talk exit       Stop the background daemon
"""
import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import json
import logging
import subprocess
import sys
import threading
import time
import winsound

from core.audio import AudioRecorder
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

handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
if sys.stdout is not None:
    handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=handlers,
)
logger = logging.getLogger("sdl2stt")


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
        "hotkey": "ctrl+space",
        "mode": "hold",
        "gate": 0.02,
        "min_loud_windows": 3,
        "sample_rate": 16000,
        "channels": 1,
        "play_sound_cues": True,
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
        )
        self.visualizer = VisualizerBar(get_level_fn=self.recorder.get_peak_level)
        self.stt_client = STTClient(
            endpoint_url=config.get("stt_url"),
            peer_name=config.get("stt_peer", ""),
        )
        self.hotkey_listener = HotkeyListener(
            hotkey=config.get("hotkey", "ctrl+space"),
            mode=config.get("mode", "hold"),
            on_start=self.start_recording,
            on_stop=self.stop_recording,
            on_toggle=self.toggle_recording,
        )
        self.ipc_server = IPCServer(handler_callback=self.handle_ipc)
        self._action_lock = threading.Lock()
        self._sound_cues = config.get("play_sound_cues", True)

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
            self.visualizer.show()
            self.hotkey_listener.set_recording(True)
            self.play_sound("start")
            return "RECORDING_STARTED"

    def stop_recording(self):
        with self._action_lock:
            if not self.recorder.is_recording:
                return "NOT_RECORDING"
            logger.info("Recording stopped, processing...")
            self.visualizer.hide()
            wav_bytes = self.recorder.stop()
            self.hotkey_listener.set_recording(False)
            self.play_sound("stop")

        # Process audio asynchronously so we don't hold the lock or block IPC
        threading.Thread(
            target=self._process_recording, args=(wav_bytes,), daemon=True
        ).start()
        return "PROCESSING"

    def toggle_recording(self):
        if self.recorder.is_recording:
            return self.stop_recording()
        else:
            return self.start_recording()

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
            logger.info(f"STT Response ({elapsed:.2f}s): {text!r}")
        except Exception as e:
            logger.error(f"STT error: {e}")
            self.play_sound("error")
            return

        # 3. Text Injection
        if text:
            logger.info(f"Injecting {len(text)} characters into focused window")
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
        logger.info("sdl2stt-win daemon is running! Press Ctrl+Space to push-to-talk.")
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
            time.sleep(1.0)
            resp = send_command(cmd)
        print(f"{cmd}: {resp}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
