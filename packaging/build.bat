@echo off
cd /d "%~dp0.."

rem ============================================================
rem  语音输入工具 - 一键打包
rem  说明：.venv 不进 git，克隆到新位置后本脚本会自动建环境、装依赖再打包。
rem  注意：本文件为 GBK 编码 + CRLF 换行，请勿用编辑器另存为 UTF-8/LF。
rem ============================================================

rem 打包期间清掉外部注入的 PYTHONPATH：其他工具挂的 sitecustomize 会干扰 pip 与 PyInstaller
set "PYTHONPATH="
set "PYTHONDONTWRITEBYTECODE=1"
rem 让 pip 按 UTF-8 解析 requirements.txt：否则它按系统本地编码(cp936)解码，遇到中文就 UnicodeDecodeError
set "PYTHONUTF8=1"
set "VENV_PY=.venv\Scripts\python.exe"
set "DIST_EXE=dist\VoiceInput.exe"

echo ============================================================
echo   语音输入工具 - 打包
echo   项目目录：%CD%
echo ============================================================
echo.

if exist "%VENV_PY%" goto deps

echo [1/3] 未发现 .venv，正在自动创建虚拟环境...
call :find_python
if "%PYTHON%"=="" goto no_python
echo       使用解释器：%PYTHON%
"%PYTHON%" -m venv --system-site-packages .venv
if errorlevel 1 goto venv_fail
if not exist "%VENV_PY%" goto venv_fail
echo       虚拟环境已就绪
echo.

:deps
echo [2/3] 检查并安装依赖（首次较慢，之后会跳过已装的）...
"%VENV_PY%" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo       默认源失败，改用清华镜像重试一次...
  "%VENV_PY%" -m pip install -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
  if errorlevel 1 goto deps_fail
)
"%VENV_PY%" -c "import PySide6, sounddevice, numpy, win32gui, keyring, pydantic, requests, soxr, uiautomation" >nul 2>nul
if errorlevel 1 goto deps_fail
echo       依赖校验通过
echo.

echo [3/3] 正在打包（首次约 2-5 分钟）...
"%VENV_PY%" -m PyInstaller packaging\VoiceInput.spec --noconfirm --distpath dist --workpath build
if errorlevel 1 goto build_fail
if not exist "%DIST_EXE%" goto build_fail

echo.
echo ============================================================
echo   打包完成：%DIST_EXE%
echo   建议先自检：%DIST_EXE% --diag
echo ============================================================
echo.
pause
exit /b 0

rem ---------------- 寻找可用的 Python ----------------
:find_python
set "PYTHON="
where py >nul 2>nul
if errorlevel 1 goto fp_python
for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%i"
if defined PYTHON goto fp_check

:fp_python
where python >nul 2>nul
if errorlevel 1 goto fp_paths
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%i"
if defined PYTHON goto fp_check

:fp_paths
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" "C:\Python312\python.exe" "C:\Python311\python.exe" "C:\Python310\python.exe" "E:\python\python3.12\python.exe" "D:\python\python3.12\python.exe") do if not defined PYTHON if exist %%P set "PYTHON=%%~P"

:fp_check
if not defined PYTHON goto :eof
"%PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo       找到的 Python 版本过低（需要 3.10+）：%PYTHON%
  set "PYTHON="
)
goto :eof

rem ---------------- 错误出口 ----------------
:no_python
echo.
echo 打包失败：没找到 Python 3.10 及以上版本。
echo.
echo 解决办法二选一：
echo   1. 到 python.org 安装 Python 3.12，务必勾选 Add python.exe to PATH，然后重跑
echo   2. 手动建环境后重跑：
echo        python -m venv .venv
echo        .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1

:venv_fail
echo.
echo 打包失败：创建虚拟环境失败（见上方报错）。
echo 若提示权限问题，请以普通用户身份运行，不要用管理员。
echo.
pause
exit /b 1

:deps_fail
echo.
echo 打包失败：依赖没装全。
echo 可手动执行下面这条看真实报错：
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1

:build_fail
echo.
echo 打包失败，请看上面的报错。
echo 常见原因：杀毒软件锁住 dist，或 build 目录残留。
echo 可尝试删除 build、dist 两个文件夹后重跑。
echo.
pause
exit /b 1
