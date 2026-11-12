import io
import os
import tempfile
import threading
import time
import unittest
import wave

import numpy as np

from core import startup
from core.ipc import IPCServer, send_command
from core.noisegate import evaluate_audio_gate

TEST_PORT = 49377


def make_wav(samples: np.ndarray, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(samples.astype("<i2").tobytes())
    return buf.getvalue()


class NoiseGateTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(evaluate_audio_gate(b""), (0.0, 0, False))

    def test_silence_rejected(self):
        peak, loud, passed = evaluate_audio_gate(make_wav(np.zeros(16000)))
        self.assertEqual((peak, loud, passed), (0.0, 0, False))

    def test_loud_tone_passes(self):
        t = np.arange(16000) / 16000
        tone = np.sin(2 * np.pi * 440 * t) * 16384  # half scale -> RMS about 0.35
        peak, loud, passed = evaluate_audio_gate(make_wav(tone))
        self.assertAlmostEqual(peak, 0.3536, places=2)
        self.assertEqual(loud, 50)
        self.assertTrue(passed)

    def test_short_burst_below_min_windows(self):
        samples = np.zeros(16000)
        samples[:640] = 20000  # exactly two loud 20ms windows
        _, loud, passed = evaluate_audio_gate(make_wav(samples), min_loud_windows=3)
        self.assertEqual(loud, 2)
        self.assertFalse(passed)

    def test_shorter_than_one_window(self):
        self.assertEqual(evaluate_audio_gate(make_wav(np.full(100, 20000))), (0.0, 0, False))

    def test_unexpected_format_fails_open(self):
        self.assertTrue(evaluate_audio_gate(make_wav(np.zeros(3200), channels=2))[2])
        self.assertTrue(evaluate_audio_gate(b"not a wav file")[2])


class IPCTests(unittest.TestCase):
    def test_round_trip_and_single_instance(self):
        server = IPCServer(lambda cmd: f"GOT:{cmd}", port=TEST_PORT)
        thread = threading.Thread(target=server.start, daemon=True)
        thread.start()
        try:
            deadline = time.time() + 5
            resp = ""
            while time.time() < deadline:
                resp = send_command("status", port=TEST_PORT)
                if resp == "GOT:status":
                    break
                time.sleep(0.05)
            self.assertEqual(resp, "GOT:status")
            self.assertEqual(send_command("toggle", port=TEST_PORT), "GOT:toggle")
            # A second daemon must not be able to claim the same port.
            with self.assertRaises(OSError):
                IPCServer(lambda cmd: "", port=TEST_PORT).start()
        finally:
            server.stop()
            thread.join(timeout=3)


class StartupTests(unittest.TestCase):
    def test_install_and_uninstall_shortcut(self):
        with tempfile.TemporaryDirectory() as folder:
            lnk = startup.install(os.path.abspath("talk.py"), folder=folder)
            self.assertTrue(os.path.isfile(lnk))
            self.assertTrue(startup.uninstall(folder=folder))
            self.assertFalse(startup.uninstall(folder=folder))


if __name__ == "__main__":
    unittest.main()
