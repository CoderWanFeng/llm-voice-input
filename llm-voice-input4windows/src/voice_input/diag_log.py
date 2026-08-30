"""诊断日志模块。

对应 macOS 原项目 DiagLog.swift，提供线程安全的单例日志器：
- 写入到 %APPDATA%\\VoiceInput\\diag.log
- 同时输出到 stdout（方便调试）
- 带时间戳前缀
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from typing import Optional


class DiagLog:
    """单例诊断日志器，所有模块共用，线程安全。"""

    _instance: Optional["DiagLog"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        # 配置目录：%APPDATA%\VoiceInput
        app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
        self._dir = os.path.join(app_data, "VoiceInput")
        os.makedirs(self._dir, exist_ok=True)
        # 日志文件路径
        self._log_path = os.path.join(self._dir, "diag.log")
        # 文件写入互斥锁，避免多线程交错
        self._file_lock = threading.Lock()

    @classmethod
    def shared(cls) -> "DiagLog":
        """获取单例实例，首次调用时构造。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def write(self, message: str) -> None:
        """写入一行日志，自动追加时间戳与换行。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {message}\n"
        # 控制台输出（便于调试）
        try:
            sys.stdout.write(line)
            sys.stdout.flush()
        except Exception:
            pass
        # 文件落盘
        with self._file_lock:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass
