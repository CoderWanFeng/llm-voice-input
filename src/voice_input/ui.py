"""Overlay 浮窗与 TrayIcon 托盘（规矩 R10：浮窗永不激活）。

浮窗的四个标志位缺一不可，漏掉 WindowDoesNotAcceptFocus 或 WA_ShowWithoutActivating，
浮窗一弹出就会把焦点从输入框抢走，需求 3 直接失效。
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Callable, Optional

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .models import AppState

logger = logging.getLogger("voice_input.ui")

STATE_LABEL = {
    AppState.IDLE: "待机",
    AppState.RECORDING: "录音中",
    AppState.TRANSCRIBING: "识别中",
    AppState.INJECTING: "上屏中",
    AppState.DONE: "已完成",
    AppState.ABORTED: "已取消",
    AppState.ERROR: "出错了",
}

STATE_COLOR = {
    AppState.IDLE: "#9CA3AF",
    AppState.RECORDING: "#E24B4A",
    AppState.TRANSCRIBING: "#378ADD",
    AppState.INJECTING: "#378ADD",
    AppState.DONE: "#639922",
    AppState.ABORTED: "#888780",
    AppState.ERROR: "#BA7517",
}


class Overlay(QWidget):
    """屏幕底部的状态浮窗：置顶、鼠标穿透、绝不抢焦点。"""

    WIDTH = 240
    HEIGHT = 64
    BARS = 20

    def __init__(self, level_provider: Optional[Callable[[], float]] = None) -> None:
        super().__init__()
        self._level_provider = level_provider or (lambda: 0.0)
        self._state = AppState.IDLE
        self._text = ""
        self._levels: deque[float] = deque([0.0] * self.BARS, maxlen=self.BARS)
        self._phase = 0.0

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setWindowTitle("语音输入")

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    # ---------------- 对外接口 ----------------

    def set_state(self, state: AppState, text: str = "") -> None:
        """更新状态并在必要时显示/隐藏浮窗。"""
        self._state = state
        self._text = text or ""

        if state == AppState.IDLE:
            self._hide_timer.start(600)  # 收尾后短暂停留再消失
            self.update()
            return

        self._hide_timer.stop()
        self.reposition()
        if not self.isVisible():
            self.show()  # 带 WA_ShowWithoutActivating，不会抢焦点
        self.update()

        if state in (AppState.DONE, AppState.ABORTED):
            self._hide_timer.start(1200)

    def set_level_provider(self, fn: Callable[[], float]) -> None:
        """设置音量来源（录音器在编排器里创建，只能后置注入）。"""
        self._level_provider = fn

    def reposition(self) -> None:
        """屏幕底部居中。"""
        screen = self.screen()
        geo = screen.availableGeometry() if screen else None
        if geo is None:
            return
        x = geo.x() + (geo.width() - self.WIDTH) // 2
        y = geo.y() + geo.height() - self.HEIGHT - 48
        self.move(x, y)

    # ---------------- 绘制 ----------------

    def _tick(self) -> None:
        if self._state == AppState.RECORDING:
            self._levels.append(min(1.0, self._level_provider() * 3.0))
        else:
            self._levels.append(0.0)
        self._phase += 0.25
        if self.isVisible():
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # 背景
        p.setPen(QPen(QColor("#D9D9D9"), 1))
        p.setBrush(QBrush(QColor(255, 255, 255, 242)))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.WIDTH - 1, self.HEIGHT - 1), 12, 12)

        color = QColor(STATE_COLOR.get(self._state, "#9CA3AF"))

        # 状态点（录音中呼吸闪烁）
        alpha = 255
        if self._state == AppState.RECORDING:
            import math

            alpha = int(140 + 115 * abs(math.sin(self._phase)))
        dot_color = QColor(color)
        dot_color.setAlpha(alpha)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(dot_color))
        p.drawEllipse(QRectF(14, 16, 10, 10))

        # 状态文字
        font = QFont("Microsoft YaHei", 10)
        font.setWeight(QFont.Weight.Medium)
        p.setFont(font)
        p.setPen(QColor("#1F2937"))
        p.drawText(QRectF(32, 12, self.WIDTH - 46, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   STATE_LABEL.get(self._state, ""))

        # 波形或提示文字
        if self._state == AppState.RECORDING:
            self._draw_bars(p, color)
        elif self._text:
            p.setPen(QColor("#6B7280"))
            small = QFont("Microsoft YaHei", 9)
            p.setFont(small)
            shown = self._text if len(self._text) <= 18 else self._text[:18] + "…"
            p.drawText(QRectF(14, 34, self.WIDTH - 28, 20), Qt.AlignLeft | Qt.AlignVCenter, shown)

    def _draw_bars(self, p: QPainter, color: QColor) -> None:
        bar_w = 4.0
        gap = 2.0
        total = self.BARS * (bar_w + gap) - gap
        x0 = (self.WIDTH - total) / 2
        base_y = 50
        for i, v in enumerate(self._levels):
            h = 3.0 + v * 16.0
            c = QColor(color)
            c.setAlpha(int(120 + 135 * v))
            p.setBrush(QBrush(c))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(x0 + i * (bar_w + gap), base_y - h, bar_w, h), 2, 2)


class TrayIcon:
    """系统托盘入口：常驻、可手动启停、可打开设置与日志、可退出。"""

    def __init__(self, on_toggle, on_quit, on_settings=None, on_logs=None) -> None:
        self._on_toggle = on_toggle
        self.icon = QSystemTrayIcon(_make_icon("#378ADD"))
        self.icon.setToolTip("语音输入工具")

        menu = QMenu()
        act_toggle = menu.addAction("开始 / 停止录音")
        menu.addSeparator()
        act_settings = menu.addAction("设置…")
        act_logs = menu.addAction("查看日志…")
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_toggle.triggered.connect(self._on_toggle)
        act_settings.triggered.connect(on_settings or (lambda: None))
        act_logs.triggered.connect(on_logs or (lambda: None))
        act_quit.triggered.connect(on_quit)
        self.icon.setContextMenu(menu)
        self.icon.show()

    def set_state(self, state: AppState) -> None:
        color = STATE_COLOR.get(state, "#9CA3AF")
        self.icon.setIcon(_make_icon(color))
        self.icon.setToolTip(f"语音输入 · {STATE_LABEL.get(state, '')}")


def _make_icon(color: str) -> QIcon:
    """用 QPainter 画一个圆形图标，避免依赖外部图片文件。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(color)))
    p.drawEllipse(QRectF(8, 8, 48, 48))
    p.end()
    return QIcon(pm)
