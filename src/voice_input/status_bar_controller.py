"""系统托盘控制器模块。

对应 macOS 原项目 StatusBarController.swift：
- pystray 创建系统托盘图标
- 菜单项：设置语音识别凭证 / 测试快捷键 / 5秒自动录音测试 / 关于 / 退出
- 在后台线程跑 icon.run()，避免阻塞 tkinter 主线程
- 菜单回调通过 panel.schedule_on_main 调度到主线程（tkinter 要求）
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from .diag_log import DiagLog

try:
    import pystray
    from PIL import Image, ImageDraw
    _PYSTRAY_AVAILABLE = True
except Exception as e:  # pystray 或 Pillow 未安装
    _PYSTRAY_AVAILABLE = False
    _PYSTRAY_IMPORT_ERROR = str(e)


class StatusBarController:
    """系统托盘控制器，后台线程运行。"""

    def __init__(self) -> None:
        # 菜单回调（由 app 注入）
        self.on_show_panel: Optional[Callable[[], None]] = None
        self.on_setup_credentials: Optional[Callable[[], None]] = None
        self.on_test_hotkey: Optional[Callable[[], None]] = None
        self.on_test_5s_recording: Optional[Callable[[], None]] = None
        self.on_quit: Optional[Callable[[], None]] = None
        # 主线程调度器（FloatingPanelController.schedule_on_main）
        self._scheduler: Optional[Callable[[Callable[[], None]], None]] = None
        # pystray Icon
        self._icon: Optional["pystray.Icon"] = None
        # 后台线程
        self._thread: Optional[threading.Thread] = None

    def set_scheduler(self, scheduler: Callable[[Callable[[], None]], None]) -> None:
        """注入主线程调度器（必需，菜单回调通过它调度到 tkinter 主线程）。"""
        self._scheduler = scheduler

    def start(self) -> bool:
        """启动托盘图标（后台线程），返回是否成功。

        首次启动时会显示一个气球提示，告知用户如何配置语音识别服务。
        """
        if not _PYSTRAY_AVAILABLE:
            DiagLog.shared().write(f"[Tray] pystray 不可用: {_PYSTRAY_IMPORT_ERROR}")
            return False
        try:
            image = self._build_icon_image()
            menu = pystray.Menu(
                pystray.MenuItem("设置语音识别凭证…", self._on_setup_credentials, default=True),
                pystray.MenuItem("显示面板", self._on_show_panel),
                pystray.MenuItem("测试快捷键", self._on_test_hotkey),
                pystray.MenuItem("5秒自动录音测试", self._on_test_5s),
                pystray.MenuItem("关于", self._on_about),
                pystray.MenuItem("退出", self._on_quit),
            )
            # Tooltip 提示用户右键配置
            self._icon = pystray.Icon(
                "VoiceInput", image, "VoiceInput - 右键配置语音识别", menu
            )
            # 在后台线程运行（不阻塞 tkinter 主线程）
            self._thread = threading.Thread(target=self._icon.run, daemon=True)
            self._thread.start()

            # 显示气球提示（首次启动告知用户如何使用）
            try:
                self._icon.notify(
                    "VoiceInput 已启动",
                    "右键托盘图标 → 设置语音识别凭证\n"
                    "按 Ctrl+Alt+K 开始录音",
                )
            except Exception:
                pass  # 气球通知失败不影响主流程

            DiagLog.shared().write("[Tray] 托盘图标已启动")
            return True
        except Exception as e:
            DiagLog.shared().write(f"[Tray] 启动失败: {e}")
            return False

    def stop(self) -> None:
        """停止托盘图标。"""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
        if self._thread is not None:
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass
            self._thread = None

    def _build_icon_image(self) -> "Image.Image":
        """渲染托盘图标：麦克风风格圆角方形。"""
        # 64x64 透明图标
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 圆角深色背景
        draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(60, 120, 200, 255))
        # 简化麦克风轮廓（矩形 + 圆头）
        draw.rounded_rectangle((26, 16, 38, 38), radius=6, fill=(255, 255, 255, 255))
        draw.rectangle((30, 38, 34, 46), fill=(255, 255, 255, 255))
        # 弧形支架（用椭圆近似）
        draw.arc((22, 30, 42, 50), start=0, end=180, fill=(255, 255, 255, 255), width=2)
        draw.line((22, 40, 22, 46), fill=(255, 255, 255, 255), width=2)
        draw.line((42, 40, 42, 46), fill=(255, 255, 255, 255), width=2)
        draw.line((26, 46, 38, 46), fill=(255, 255, 255, 255), width=2)
        return img

    # ===== 菜单回调（pystray 后台线程触发，需调度到主线程） =====

    def _on_show_panel(self, icon, item) -> None:
        """菜单：显示工具面板。"""
        self._dispatch_to_main(lambda: self.on_show_panel() if self.on_show_panel else None)

    def _on_setup_credentials(self, icon, item) -> None:
        """菜单：设置语音识别凭证。"""
        self._dispatch_to_main(lambda: self.on_setup_credentials() if self.on_setup_credentials else None)

    def _on_test_hotkey(self, icon, item) -> None:
        """菜单：测试快捷键。"""
        self._dispatch_to_main(lambda: self.on_test_hotkey() if self.on_test_hotkey else None)

    def _on_test_5s(self, icon, item) -> None:
        """菜单：5秒自动录音测试。"""
        self._dispatch_to_main(lambda: self.on_test_5s_recording() if self.on_test_5s_recording else None)

    def _on_about(self, icon, item) -> None:
        """菜单：关于。"""
        self._dispatch_to_main(lambda: self._show_about())

    def _on_quit(self, icon, item) -> None:
        """菜单：退出。"""
        self._dispatch_to_main(lambda: self.on_quit() if self.on_quit else None)

    def _show_about(self) -> None:
        """显示关于提示（通过悬浮面板）。"""
        # 由 app 注入更具体的 UI 行为，这里仅打日志
        DiagLog.shared().write("[Tray] 关于：VoiceInput Windows 版 v1.0")

    def _dispatch_to_main(self, callback: Callable[[], None]) -> None:
        """调度回调到 tkinter 主线程。"""
        if self._scheduler is None:
            # 无调度器则直接执行（可能在错误上下文）
            try:
                callback()
            except Exception as e:
                DiagLog.shared().write(f"[Tray] 回调异常: {e}")
            return
        try:
            self._scheduler(callback)
        except Exception as e:
            DiagLog.shared().write(f"[Tray] 调度失败: {e}")
