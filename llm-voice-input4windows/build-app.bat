@echo off
REM ========================================
REM VoiceInput Windows Build Script v2.0
REM ========================================
REM Usage: Double-click build-app.bat
REM Requirements: Python 3.10+ in PATH
REM Output: dist\VoiceInput.exe
REM ========================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ======================================
echo   VoiceInput Windows Build v2.0
echo ======================================
echo.

REM 1. Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo         Please install Python 3.10+ from:
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK] %%v
echo.

REM 2. Install dependencies
echo [1/5] Installing dependencies...
python -m pip install --upgrade pip
if errorlevel 1 goto :deps_error
python -m pip install -r requirements.txt
if errorlevel 1 goto :deps_error

REM 3. Generate icon
echo.
echo [2/5] Checking icon...
if not exist "resources\icon.ico" (
    echo         Generating icon...
    python resources\make_icon.py
    if errorlevel 1 (
        echo [WARN] Icon generation failed, continuing without icon.
    )
) else (
    echo [OK] Icon already exists.
)

REM 4. Clean old build
echo.
echo [3/5] Cleaning old build...
REM Stop any running VoiceInput.exe instance (file lock breaks clean/build)
taskkill /F /IM VoiceInput.exe >nul 2>&1
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist VoiceInput.spec del /q VoiceInput.spec

REM 5. PyInstaller packaging
echo.
echo [4/5] Running PyInstaller...
echo         This may take a few minutes, please wait...
echo.

REM Set icon option
set "ICON_CMD="
if exist "resources\icon.ico" set "ICON_CMD=--icon resources\icon.ico"

python -m PyInstaller ^
    --noconsole ^
    --onefile ^
    --name VoiceInput ^
    --paths src ^
    --hidden-import sounddevice ^
    --hidden-import websockets ^
    --hidden-import pynput ^
    --hidden-import pystray ^
    --collect-all sounddevice ^
    %ICON_CMD% ^
    src\voice_input\__main__.py

if errorlevel 1 goto :build_error

REM 6. Verify output
echo.
echo [5/5] Verifying output...
if exist "dist\VoiceInput.exe" (
    for %%A in ("dist\VoiceInput.exe") do (
        set /a "SIZE_MB=%%~zA / 1048576"
    )
    echo.
    echo ======================================
    echo   BUILD SUCCESSFUL!
    echo ======================================
    echo   Output: dist\VoiceInput.exe
    echo   Size:   ~!SIZE_MB! MB
    echo.
    echo   How to use:
    echo   1. Double-click VoiceInput.exe to run
    echo   2. First run will show credential dialog
    echo   3. Or configure via tray icon right-click menu
    echo.
    echo   Config file:
    echo   %%APPDATA%%\VoiceInput\config.json
    echo   Log file:
    echo   %%APPDATA%%\VoiceInput\diag.log
    echo ======================================
) else (
    echo [ERROR] Output not found: dist\VoiceInput.exe
    pause
    exit /b 1
)

echo.
pause
endlocal
exit /b 0

:deps_error
echo.
echo [ERROR] Failed to install dependencies.
echo         Try running: python -m pip install -r requirements.txt
pause
exit /b 1

:build_error
echo.
echo [ERROR] PyInstaller build failed.
echo         Check the error messages above.
pause
exit /b 1
