"""
Noise gate evaluation for sdl2stt-win.
Directly ports the proven RMS energy gate from the Linux talk orchestrator.
"""
import array
import io
import math
import wave


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

            raw_bytes = wf.readframes(num_frames)
            samples = array.array("h")
            samples.frombytes(raw_bytes)

        if not samples:
            return 0.0, 0, False

        win_size = 320  # 20ms windows at 16kHz
        loud_count = 0
        peak_rms = 0.0

        for i in range(0, len(samples) - win_size + 1, win_size):
            seg = samples[i : i + win_size]
            rms = math.sqrt(sum(float(x) * x for x in seg) / win_size) / 32768.0
            if rms > peak_rms:
                peak_rms = rms
            if rms > threshold:
                loud_count += 1

        passed = loud_count >= min_loud_windows
        return peak_rms, loud_count, passed

    except Exception:
        # Fail OPEN: measurement error must never silently drop a real recording
        return 1.0, 99, True
