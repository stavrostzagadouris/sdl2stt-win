"""
Audio recording module for sdl2stt-win.
Captures 16-bit signed LE mono audio at 16000 Hz on a dedicated thread.
Provides live peak levels to the visualizer and finalizes to WAV bytes on stop.
"""
import io
import threading
import wave
import numpy as np
import sounddevice as sd


class AudioRecorder:
    def __init__(self, sample_rate=16000, channels=1, device=None):
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self._lock = threading.Lock()
        self._stream = None
        self._frames = []
        self._peak = 0
        self._is_recording = False

    @property
    def is_recording(self):
        return self._is_recording

    def _audio_callback(self, indata, frames, time_info, status):
        """Audio callback running on high-priority PortAudio / WASAPI thread."""
        if not self._is_recording:
            return

        # indata is numpy int16 array of shape (frames, channels)
        arr = indata.flatten()
        with self._lock:
            self._frames.append(arr.tobytes())
            max_val = int(np.max(np.abs(arr))) if len(arr) > 0 else 0
            if max_val > self._peak:
                self._peak = max_val

    def start(self):
        """Start non-blocking audio capture."""
        if self._is_recording:
            return

        with self._lock:
            self._frames.clear()
            self._peak = 0
            self._is_recording = True

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            device=self.device,
            callback=self._audio_callback,
            blocksize=640,  # 40ms fragments at 16kHz
        )
        self._stream.start()

    def get_peak_level(self):
        """
        Return the peak level normalized between 0.0 and 1.0 since last query,
        and reset the tracked peak.
        """
        with self._lock:
            peak = self._peak
            self._peak = 0
        return min(1.0, peak / 32768.0)

    def stop(self) -> bytes:
        """
        Stop audio capture and return WAV-encoded bytes.
        """
        if not self._is_recording:
            return b""

        self._is_recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        with self._lock:
            pcm_data = b"".join(self._frames)
            self._frames.clear()
            self._peak = 0

        if not pcm_data:
            return b""

        # Build WAV in memory
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm_data)

        return buf.getvalue()
