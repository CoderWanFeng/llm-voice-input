@echo off
chcp 65001 >nul
cd /d "%~dp0.."

echo 正在打包，首次约 2-5 分钟，请稍候...
.venv\Scripts\python.exe -m PyInstaller packaging\VoiceInput.spec --noconfirm --distpath dist --workpath build

if errorlevel 1 (
  echo.
  echo 打包失败，请看上面的报错。
  pause
  exit /b 1
)

echo.
echo 打包完成：dist\VoiceInput.exe
echo 建议先自检一次：dist\VoiceInput.exe --diag
pause
