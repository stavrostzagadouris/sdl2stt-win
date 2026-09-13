"""
HTTP client for cleanTTS / OpenAI-compatible STT endpoint.
Handles audio upload, transcription extraction, and connection diagnosis.
"""
import json
import logging
import requests

logger = logging.getLogger("sdl2stt")


class STTClient:
    def __init__(self, endpoint_url: str, peer_name: str = ""):
        self.endpoint_url = endpoint_url
        self.peer_name = peer_name

    def transcribe(self, wav_bytes: bytes, timeout: float = 20.0) -> str:
        """
        Sends WAV audio to the STT endpoint and returns the transcribed text.

        Raises:
            ConnectionError: If server cannot be reached.
            RuntimeError: If server returns non-200 status code.
        """
        if not wav_bytes:
            return ""

        files = {
            "file": ("audio.wav", wav_bytes, "audio/wav")
        }

        try:
            response = requests.post(
                self.endpoint_url,
                files=files,
                timeout=(3.0, timeout),  # 3s connect timeout, 20s read timeout
            )
        except requests.exceptions.ConnectTimeout:
            raise ConnectionError(f"Connection timed out reaching {self.endpoint_url}")
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(f"Could not connect to {self.endpoint_url}: {e}")
        except Exception as e:
            raise ConnectionError(f"Network error during transcription: {e}")

        if response.status_code != 200:
            raise RuntimeError(
                f"STT server returned HTTP {response.status_code}: {response.text}"
            )

        try:
            data = response.json()
            if isinstance(data, dict):
                return data.get("text", "").strip()
            return str(data).strip()
        except Exception:
            return response.text.strip()
