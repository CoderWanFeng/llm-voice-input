"""VoiceInput Windows 版入口模块。

支持两种启动方式：
1. 开发模式: `python -m voice_input`（通过包方式运行）
2. 打包模式: PyInstaller 打包后的 VoiceInput.exe

导入策略：
- 使用绝对导入 `from voice_input.app` 而非相对导入 `from .app`
- 原因：PyInstaller 将本文件作为独立脚本运行，没有父包，相对导入会失败
"""

from __future__ import annotations

import os
import sys


def _bootstrap_path() -> None:
    """开发模式下将 src 目录加入 sys.path。

    PyInstaller 打包时（sys.frozen=True），PyInstaller 已通过 --paths src
    正确设置模块搜索路径，此函数跳过。
    """
    if getattr(sys, "frozen", False):
        return  # 打包模式：PyInstaller 已处理路径
    here = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(here)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def main() -> None:
    """程序入口。"""
    _bootstrap_path()
    # 绝对导入：在开发模式和 PyInstaller 打包模式下都能正常工作
    from voice_input.app import VoiceInputApp
    VoiceInputApp().run()


if __name__ == "__main__":
    main()
