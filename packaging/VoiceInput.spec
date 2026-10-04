# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（单文件 GUI，双击即用）。

几个必须显式处理的点，否则打包后程序能启动但功能残缺：
1. keyring 的后端是靠 entry_points 动态发现的，必须带上它的 dist-info 元数据，
   否则打包后找不到 Windows 凭据库后端 → 密钥保存失败（R18 直接破防）。
2. sounddevice 的 PortAudio DLL 由 hook-sounddevice 收集（pyinstaller-hooks-contrib）。
3. --noconsole 下 sys.stderr 为 None，config.setup_logging 已做判断，不再加控制台句柄。
"""

import os

from PyInstaller.utils.hooks import copy_metadata

# SPECPATH 由 PyInstaller 注入，指向本文件所在目录；用它拼绝对路径最稳，
# 用相对路径在这里不生效（会导致包 voice_input 根本没被收集进去）
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ENTRY = os.path.join(SPECPATH, "entry.py")
ICON = os.path.join(SPECPATH, "icon.ico")

EXCLUDES = [
    # Qt 里我们用不到的大家伙，砍掉能省几百 MB
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2", "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio", "PySide6.QtRemoteObjects",
    "PySide6.QtSensors", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtNetworkAuth",
    # 标准库里用不上的
    "tkinter", "unittest", "pydoc", "doctest", "pytest", "IPython",
]

HIDDEN_IMPORTS = [
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    # keyring 后端（Windows 凭据管理器）
    "keyring.backends", "keyring.backends.Windows", "keyring.backends.fail",
    "keyring.backends.null", "keyring.backends.chainer",
    "win32gui", "win32clipboard", "win32process", "win32con", "win32api", "win32event",
    "sounddevice", "soxr", "numpy", "pydantic", "pydantic.deprecated",
    "requests", "uiautomation", "comtypes", "comtypes.client",
    "tencentcloud.asr.v20190614.asr_client", "tencentcloud.asr.v20190614.models",
]

datas = copy_metadata("keyring")

a = Analysis(
    [ENTRY],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VoiceInput",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # GUI 程序，不要黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)
