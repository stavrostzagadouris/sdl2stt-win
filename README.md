# sdl2stt-win

This one is coded by Gemini 3.8 Flash medium. Works on windows and is pretty slick.

NOTE that there is no security build into cleanTTS or these companions, they run unencrypted to an http endpoint. The security is at the network layer.. for example, I have mine go through my tailnet, which itself is secure and encrypted, therefore bundling the data safely.




Push-to-talk voice input for **Windows**, designed to pair with [cleanTTS](https://github.com/stavrostzagadouris/cleanTTS).

Hold **Ctrl+Space**, speak, release — the transcribed text is typed directly into whatever window/field currently has focus.

Windows port of `sdl2tts` with exact visual styling, audio capture parameters, and noise gate behavior.

---

## Features

- **Push-to-Talk (Hold-to-Talk) & Toggle**:
  - Hold **`Ctrl+Space`**, speak, and release to instantly transcribe and type.
  - Or switch to toggle mode in `config.json` (press `Ctrl+Space` once to start, press again to stop).
- **cleanTTS Companion**:
  - Matches the exact dark pill visualizer styling (`#171a21` background, `#2a2f3a` border, `#5ea1ff` accent, 16 level-meter bars with smooth peak decay trail).
  - Pinned bottom-center of your focused monitor (above the taskbar).
  - Uses Windows `WS_EX_NOACTIVATE` & `WS_EX_TOPMOST`: **never steals focus** from your active window (Notepad, browser, Discord, IDE, etc.).
- **High-Performance Audio**:
  - Real-time 16 kHz, 16-bit signed LE mono capture running on a dedicated thread.
  - Frame rendering and audio capture are completely decoupled to prevent audio buffer starvation.
- **Built-in Noise Gate**:
  - RMS sliding window analysis (default `0.02` threshold, minimum 3 loud windows) rejects room noise and quiet TV audio to prevent model hallucinations.
- **Unicode Text Injection**:
  - Injects characters via Win32 `SendInput` with `KEYEVENTF_UNICODE` directly into the focused field without overwriting your clipboard.
- **CLI & Scriptable IPC**:
  - Full CLI support (`talk start`, `talk stop`, `talk toggle`, `talk status`, `talk exit`).
  - Integrates with AutoHotkey, Stream Deck, or custom Windows shortcuts.

---

## Requirements

- Windows 10 or Windows 11
- Python 3.10+
- A working microphone (default Windows recording device)
- cleanTTS running on your server (e.g. `http://127.0.0.1:5000/v1/audio/transcriptions` or your LAN/Tailscale address)

---

## Quick Start

1. **Install dependencies**:
   ```cmd
   install.bat
   ```
   Or manually:
   ```cmd
   pip install -r requirements.txt
   ```

2. **Configure your endpoint**:
   Copy `.env.example` to `.env` and set your cleanTTS server address:
   ```cmd
   copy .env.example .env
   ```
   Edit `.env`:
   ```env
   TALK_STT_URL=http://your-server-ip:5000/v1/audio/transcriptions
   TALK_STT_PEER=your-peer-name
   TALK_GATE=0.02
   ```

3. **Start the background push-to-talk daemon**:
   ```cmd
   talk.bat daemon
   ```
   *Tip: To run completely silently in the background without keeping a console window open, double-click `run_background.vbs`.*

4. **Use it**:
   - Focus any text field (browser address bar, chat, editor, etc.).
   - Hold **`Ctrl+Space`**.
   - Speak your sentence.
   - Release **`Ctrl+Space`**.
   - The text appears in your field!

---

## CLI Usage

You can control `sdl2stt-win` from the command line, PowerShell, or external tools:

| Command | Action |
|---|---|
| `talk daemon` | Start the resident background listener for `Ctrl+Space` |
| `talk start` | Begin recording audio and show the visualizer |
| `talk stop` | Stop recording, transcribe, and inject text |
| `talk toggle` | Start if idle, stop if recording |
| `talk status` | Check if currently `RECORDING` or `IDLE` |
| `talk exit` | Terminate the background daemon |

Add the folder containing `talk.bat` to your Windows `PATH` to run `talk` from any prompt or launcher.

---

## Configuration

Settings can be specified in `.env` (recommended, gitignored) or `config.json`:

```json
{
  "stt_url": "http://127.0.0.1:5000/v1/audio/transcriptions",
  "stt_peer": "",
  "hotkey": "ctrl+space",
  "mode": "hold",
  "gate": 0.02,
  "min_loud_windows": 3,
  "sample_rate": 16000,
  "channels": 1,
  "bar_width": 280,
  "bar_height": 48,
  "bar_margin_bottom": 24,
  "play_sound_cues": true
}
```

### Environment Variables (.env)

| Variable | Default | Purpose |
|---|---|---|
| `TALK_STT_URL` | `http://127.0.0.1:5000/v1/audio/transcriptions` | URL of the STT transcription endpoint |
| `TALK_STT_PEER` | `""` | Optional peer name for connection diagnosis |
| `TALK_GATE` | `0.02` | Noise-gate RMS floor |

---

## Auto-Start with Windows

To have `sdl2stt-win` start automatically when you log into Windows:

1. Press `Win + R`, type `shell:startup`, and press Enter.
2. Create a shortcut to `run_background.vbs` in that folder.
3. `sdl2stt-win` will run silently in the background on startup.

---

## Logs & Diagnostics

Logs are written to:
```
%LOCALAPPDATA%\sdl2stt\talk.log
```
Each recording logs:
- Peak RMS and loud-window count (to verify the noise gate).
- STT request duration and response text.
- Connection errors or diagnostic notices if the server cannot be reached.
