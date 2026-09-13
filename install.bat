@echo off
echo Installing sdl2stt-win dependencies...
python -m pip install -r "%~dp0requirements.txt"
if %errorlevel% equ 0 (
    echo.
    echo Setup complete! You can now run:
    echo   talk daemon     - to start the background Ctrl+Space push-to-talk listener
    echo   talk toggle     - to test starting/stopping recording
    echo.
) else (
    echo.
    echo Error installing dependencies. Please check Python / pip installation.
)
pause
