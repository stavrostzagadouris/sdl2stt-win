"""
Noise gate evaluation for sdl2stt-win.
Directly ports the proven RMS energy gate from the Linux talk orchestrator.
"""
import io
import math
import wave
import numpy as np


def evaluate_audio_gate(wav_bytes: bytes, threshold: float = 0.02, min_loud_windows: int = 3):
    """
    Evaluates whether the recorded WAV audio contains human speech above background noise.

    Returns:
        (peak_rms: float, loud_windows_count: int, passed: bool)
    """
    if not wav_bytes:
        return 0.0, 0, False

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                # If format differs, fail open so we don't accidentally drop valid audio
                return 1.0, 99, True

            num_frames = wf.getnframes()
            if num_frames == 0:
                return 0.0, 0, False
            
            sample_rate = wf.getframerate()
            raw_bytes = wf.readframes(num_frames)

        if not raw_bytes:
            return 0.0, 0, False

        samples = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
        win_size = int(sample_rate * 0.02)
        n_windows = len(samples) // win_size
        
        if n_windows == 0:
            return 0.0, 0, False
            
        windows = samples[:n_windows * win_size].reshape(n_windows, win_size)
        rms_values = np.sqrt(np.mean(windows ** 2, axis=1)) / 32768.0
        peak_rms = float(np.max(rms_values))
        loud_count = int(np.sum(rms_values > threshold))

        passed = loud_count >= min_loud_windows
        return peak_rms, loud_count, passed

    except Exception:
        # Fail OPEN: measurement error must never silently drop a real recording
        return 1.0, 99, True
