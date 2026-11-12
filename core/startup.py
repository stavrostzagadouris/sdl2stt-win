"""
Start-with-Windows support: creates or removes a shortcut in the user's Startup folder
that launches the daemon with pythonw (no console window).
"""
import os
import subprocess
import sys

SHORTCUT_NAME = "sdl2stt-win.lnk"

_CREATE_SCRIPT = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SDL2STT_LNK);"
    "$s.TargetPath = $env:SDL2STT_TARGET;"
    "$s.Arguments = $env:SDL2STT_ARGS;"
    "$s.WorkingDirectory = $env:SDL2STT_CWD;"
    "$s.Description = 'sdl2stt-win push-to-talk daemon';"
    "$s.Save()"
)


def startup_folder() -> str:
    return os.path.join(
        os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )


def windowless_python() -> str:
    """pythonw.exe next to the running interpreter, or the interpreter itself if absent."""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pythonw if os.path.exists(pythonw) else sys.executable


def install(script_path: str, folder: str = None) -> str:
    """Creates the shortcut and returns its path. Raises CalledProcessError on failure."""
    folder = folder or startup_folder()
    lnk = os.path.join(folder, SHORTCUT_NAME)
    env = dict(os.environ)
    env.update(
        SDL2STT_LNK=lnk,
        SDL2STT_TARGET=windowless_python(),
        SDL2STT_ARGS=f'"{script_path}" daemon',
        SDL2STT_CWD=os.path.dirname(script_path),
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _CREATE_SCRIPT],
        env=env,
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return lnk


def uninstall(folder: str = None) -> bool:
    """Removes the shortcut. Returns False if there was none."""
    lnk = os.path.join(folder or startup_folder(), SHORTCUT_NAME)
    if not os.path.exists(lnk):
        return False
    os.remove(lnk)
    return True
