"""录音悬浮面板模块（PySide6 版）。

设计目标：
- 现代化视觉效果，类似语音助手风格
- QPainter 绘制录音指示灯、音量条、状态图标
- 录音状态：醒目的红色脉冲光圈 + 音量可视化
- 识别状态：蓝色旋转指示
- 完成/错误：绿色/紫色反馈

线程模型：Qt 主线程，所有 UI 操作通过 schedule_on_main 调度到主线程。
子线程回调通过 Qt 信号槽 + QueuedConnection 投递到主线程事件循环执行。

注意：QTimer.singleShot(0, callback) 从子线程调用时不会跨线程投递
（QTimer 在调用线程的事件循环里运行，子线程没有事件循环即永远不触发）。
因此子线程必须用 _MainThreadCaller.call() —— 基于信号槽的真正跨线程投递。
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from PySide6.QtCore import (
    QObject,
    Qt,
    QTimer,
    QVariantAnimation,
    QEasingCurve,
    QPointF,
    QRectF,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QWidget

from .diag_log import DiagLog


class _MainThreadCaller(QObject):
    """跨线程调度辅助对象：把回调真正投递到 Qt 主线程事件循环。

    Qt 信号槽天然支持跨线程：当 emit 在子线程触发，
    而 receiver (this) 住在主线程时，连接走 QueuedConnection，
    回调会被 Qt 自动投递到主线程事件循环执行。

    替代 QTimer.singleShot：后者从子线程调用时不会跨线程投递
    （timer 在调用线程的事件循环里运行，子线程无事件循环即不触发）。
    """

    # 立即执行信号（参数为 Python callable）
    _immediate = Signal(object)
    # 延迟执行信号（参数为 callback + 延迟毫秒数）
    _delayed = Signal(object, int)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # 显式 QueuedConnection：确保跨线程时回调在主线程执行
        # （同线程时 DirectConnection 等价，行为不变）
        self._immediate.connect(self._on_immediate, Qt.QueuedConnection)
        self._delayed.connect(self._on_delayed, Qt.QueuedConnection)

    def call(self, callback: Callable[[], None], delay_ms: int = 0) -> None:
        """从任意线程调用：把 callback 投递到主线程 Qt 事件循环执行。

        Args:
            callback: 待执行的回调
            delay_ms: 延迟毫秒数（0 表示立即在主线程下一轮事件循环执行）
        """
        if delay_ms > 0:
            self._delayed.emit(callback, delay_ms)
        else:
            self._immediate.emit(callback)

    def _on_immediate(self, callback: Callable[[], None]) -> None:
        """主线程槽：执行回调。"""
        try:
            callback()
        except Exception as e:
            DiagLog.shared().write(f"[Panel] main thread callback failed: {e}")

    def _on_delayed(self, callback: Callable[[], None], delay_ms: int) -> None:
        """主线程槽：到达主线程后启动 QTimer（此时 timer 在主线程）。

        Args:
            callback: 待执行的回调
            delay_ms: 延迟毫秒数
        """
        # 此时已在主线程，QTimer.singleShot 在主线程事件循环里运行，正常工作
        QTimer.singleShot(delay_ms, lambda: self._on_immediate(callback))


# ===== 面板尺寸 =====
_PANEL_WIDTH = 520
_PANEL_HEIGHT = 130
_BOTTOM_MARGIN = 60

# ===== 颜色主题 =====
_COLOR_BG = "#1A1A2E"               # 主背景：深蓝黑
_COLOR_BORDER = "#3D3D5C"           # 边框：蓝灰
_COLOR_RECORDING = "#FF3B30"         # 录音红
_COLOR_RECORDING_DARK = "#C62828"    # 录音深红
_COLOR_RECORDING_GLOW = "#FF6B6B"    # 录音光晕
_COLOR_TRANSCRIBING = "#0A84FF"      # 识别蓝
_COLOR_SUCCESS = "#30D158"           # 成功绿
_COLOR_ERROR = "#BF5AF2"            # 错误紫
_COLOR_TEXT = "#FFFFFF"              # 主文字白
_COLOR_TEXT_SECONDARY = "#8E8E93"    # 次文字灰
_COLOR_TEXT_DIM = "#636366"          # 暗文字

# ===== 动画参数 =====
_PULSE_INTERVAL_MS = 500            # 脉冲动画周期（毫秒）
_SPINNER_INTERVAL_MS = 250          # 旋转指示器周期（毫秒）
_VOLUME_BAR_COUNT = 12              # 音量条数量
_VOLUME_BAR_MAX_HEIGHT = 28         # 音量条最大高度
_VOLUME_BAR_MIN_HEIGHT = 4          # 音量条最小高度

# ===== 状态类型 =====
STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_TRANSCRIBING = "transcribing"
STATE_FINAL = "final"
STATE_ERROR = "error"

# ===== 标题文字（识别中/AI 润色中都用 transcribing 状态，但标题不同） =====
_TITLE_TRANSCRIBING = "正在识别中"
_TITLE_POLISHING = "AI 润色中"


class _PanelWidget(QWidget):
    """悬浮面板自定义 widget，用 QPainter 在 paintEvent 中绘制所有视觉元素。

    设计思路：所有 _render_xxx 方法只更新状态字段 + 调用 update() 触发重绘，
    由 paintEvent 统一根据当前状态绘制。性能优于 tkinter Canvas 的 delete+recreate。
    """

    def __init__(self) -> None:
        super().__init__()
        # 无边框 + 不在任务栏 + 置顶
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        # 半透明背景：paintEvent 中绘制圆角矩形作为可见区域
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.96)
        self.setFixedSize(_PANEL_WIDTH, _PANEL_HEIGHT)

        # ===== 运行时绘制状态 =====
        self._current_state = STATE_IDLE
        self._current_provider_name = "语音识别"
        self._current_wake_word = ""
        self._current_partial = ""
        self._current_amplitude = 0.0
        self._current_title = _TITLE_TRANSCRIBING  # 识别中标题（polishing 状态时切换为"AI 润色中"）
        self._current_final_text = ""
        self._current_error_msg = ""
        self._current_status_text = ""  # 通用 show() 用
        self._current_status_subtext = ""

        # 动画相位（0~1，由 QVariantAnimation 驱动）
        self._anim_phase = 0.0
        # 旋转 dots 当前跳动的点索引（0~2）
        self._spinner_phase = 0

        # ===== 动画对象 =====
        # 脉冲动画：500ms 一个周期，循环播放，valueChanged 信号驱动重绘
        self._pulse_anim = QVariantAnimation(self)
        self._pulse_anim.setDuration(_PULSE_INTERVAL_MS)
        self._pulse_anim.setLoopCount(-1)  # 无限循环
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._pulse_anim.valueChanged.connect(self._on_pulse_value)
        # 默认不启动，进入录音状态时才 start()

        # 旋转 dots 定时器
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(_SPINNER_INTERVAL_MS)
        self._spinner_timer.timeout.connect(self._on_spinner_tick)

    # ===== 动画回调 =====

    def _on_pulse_value(self, value: float) -> None:
        """脉冲动画一帧：更新动画相位 + 触发重绘。

        Args:
            value: QVariantAnimation 当前值（0.0~1.0）
        """
        self._anim_phase = value
        # 仅在录音状态时持续重绘，避免其他状态下白白消耗 CPU
        if self._current_state == STATE_RECORDING:
            self.update()

    def _on_spinner_tick(self) -> None:
        """旋转 dots 一帧：推进跳动索引 + 重绘。

        三个点依次放大跳动，phase 取 0→1→2 循环。
        """
        if self._current_state != STATE_TRANSCRIBING:
            return
        self._spinner_phase = (self._spinner_phase + 1) % 3
        self.update()

    # ===== 动画控制 =====

    def start_pulse(self) -> None:
        """启动录音脉冲动画。"""
        if self._pulse_anim.state() != QVariantAnimation.Running:
            self._pulse_anim.start()

    def stop_pulse(self) -> None:
        """停止脉冲动画。"""
        self._pulse_anim.stop()

    def start_spinner(self) -> None:
        """启动旋转 dots 动画。"""
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()

    def stop_spinner(self) -> None:
        """停止旋转 dots 动画。"""
        self._spinner_timer.stop()

    # ===== 绘制入口 =====

    def paintEvent(self, event) -> None:
        """统一绘制入口：根据 _current_state 分发到对应渲染方法。

        Args:
            event: Qt 绘制事件（未使用，由 Qt 传入）
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # 先画圆角背景
        self._draw_background(p)
        # 按状态分发
        if self._current_state == STATE_IDLE:
            self._draw_idle(p)
        elif self._current_state == STATE_RECORDING:
            self._draw_recording(p)
        elif self._current_state == STATE_TRANSCRIBING:
            self._draw_transcribing(p)
        elif self._current_state == STATE_FINAL:
            self._draw_final(p)
        elif self._current_state == STATE_ERROR:
            self._draw_error(p)
        else:
            # 通用 show() 状态：状态文字 + 副文字
            self._draw_generic(p)

    # ===== 背景绘制 =====

    def _draw_background(self, p: QPainter) -> None:
        """绘制圆角矩形背景 + 1px 边框。

        Args:
            p: QPainter 实例
        """
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(1, 1, _PANEL_WIDTH - 2, _PANEL_HEIGHT - 2),
            16, 16,
        )
        p.fillPath(path, QColor(_COLOR_BG))
        p.setPen(QPen(QColor(_COLOR_BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    # ===== 各状态绘制 =====

    def _draw_idle(self, p: QPainter) -> None:
        """绘制就绪状态：绿色圆点 + 工具名称 + 提供商 + 快捷键提示。

        Args:
            p: QPainter 实例
        """
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT
        cx, cy, r = 44, h // 2, 18

        # 左侧：绿色就绪圆点
        p.setBrush(QColor(_COLOR_SUCCESS))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)

        # 状态文字
        p.setPen(QColor(_COLOR_TEXT))
        p.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        p.drawText(QRectF(cx + r + 16, cy - 22, w - cx - r - 32, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, "VoiceInput 就绪")

        # 当前提供商
        p.setPen(QColor(_COLOR_TEXT_SECONDARY))
        p.setFont(QFont("Microsoft YaHei UI", 11))
        p.drawText(QRectF(cx + r + 16, cy + 6, w - cx - r - 32, 24),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   f"当前提供商：{self._current_provider_name}")

        # 底部提示
        if self._current_wake_word:
            hint = f"说「{self._current_wake_word}」或按 Ctrl + Alt + K 开始录音 · 右键托盘图标可配置"
        else:
            hint = "按 Ctrl + Alt + K 开始 / 停止录音 · 右键托盘图标可配置"
        p.setPen(QColor(_COLOR_TEXT_DIM))
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(QRectF(0, h - 28, w, 20),
                   Qt.AlignCenter, hint)

    def _draw_recording(self, p: QPainter) -> None:
        """绘制录音状态：红色脉冲光圈 + 主圆 + 音量条 + 文本。

        光晕半径与亮度由 _anim_phase（0~1）驱动，模拟呼吸效果。

        Args:
            p: QPainter 实例
        """
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT

        # ===== 左侧：录音指示灯（光晕 + 主圆 + REC 文字） =====
        cx_rec = 44
        cy_rec = h // 2
        # 呼吸 t 值：0~1 sin 波（与原 tkinter 实现保持一致）
        t = (math.sin(self._anim_phase * math.pi * 2) + 1.0) / 2.0

        # 光晕圆：半径随呼吸在 20~32 变化
        glow_r = 20 + t * 12
        # 光晕颜色：最亮时偏浅红
        glow_intensity = int(0xB0 + t * 0x4F)  # 176~223
        glow_color = QColor(f"#FF{glow_intensity:02X}{glow_intensity:02X}")
        p.setBrush(glow_color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx_rec, cy_rec), glow_r, glow_r)

        # 主录音圆
        p.setBrush(QColor(_COLOR_RECORDING))
        p.drawEllipse(QPointF(cx_rec, cy_rec), 18, 18)

        # REC 文字
        p.setPen(QColor(_COLOR_TEXT))
        p.setFont(QFont("Segoe UI", 10, QFont.Bold))
        p.drawText(QRectF(cx_rec - 30, cy_rec - 10, 60, 20),
                   Qt.AlignCenter, "● REC")

        # ===== 音量条区域（右侧） =====
        vol_x_start = 88
        vol_y = h // 2 - 4
        bar_width = 6
        bar_gap = 3
        amp = self._current_amplitude
        for i in range(_VOLUME_BAR_COUNT):
            bar_h = self._calc_bar_height(amp, i, _VOLUME_BAR_COUNT)
            x1 = vol_x_start + i * (bar_width + bar_gap)
            y1 = vol_y - bar_h
            p.setBrush(QColor(_COLOR_RECORDING))
            p.setPen(Qt.NoPen)
            p.drawRect(QRectF(x1, y1, bar_width, bar_h))

        # ===== 右侧文字区 =====
        total_bar_width = _VOLUME_BAR_COUNT * (bar_width + bar_gap) - bar_gap
        text_x = vol_x_start + total_bar_width + 16

        # 状态 + 提供商
        p.setPen(QColor(_COLOR_TEXT))
        p.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        p.drawText(QRectF(text_x, 16, w - text_x - 16 - 100, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, "正在录音")

        p.setPen(QColor(_COLOR_TEXT_SECONDARY))
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(QRectF(w - 130, 16, 114, 24),
                   Qt.AlignRight | Qt.AlignVCenter, self._current_provider_name)

        # partial 文本
        partial = self._current_partial or "正在聆听..."
        p.setPen(QColor(_COLOR_TEXT))
        p.setFont(QFont("Microsoft YaHei UI", 11))
        p.drawText(QRectF(text_x, 46, w - text_x - 32, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, partial)

        # 底部提示
        p.setPen(QColor(_COLOR_TEXT_DIM))
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(QRectF(0, h - 28, w, 20),
                   Qt.AlignCenter, "按 Ctrl + Alt + K 停止录音")

    def _draw_transcribing(self, p: QPainter) -> None:
        """绘制识别中状态：蓝色旋转三点 + 标题 + partial 文本。

        Args:
            p: QPainter 实例
        """
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT

        # 左侧：三个小点，当前跳动的点放大 + 上移
        dot_radius = 5
        dot_gap = 14
        center_y = h // 2
        start_x = 40
        for i in range(3):
            x = start_x + i * dot_gap
            if i == self._spinner_phase:
                r = dot_radius + 2
                y_off = -2
            else:
                r = dot_radius
                y_off = 0
            p.setBrush(QColor(_COLOR_TRANSCRIBING))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(x, center_y + y_off), r, r)

        # 状态文字（标题：识别中 / AI 润色中）
        text_x = start_x + 3 * dot_gap + 16
        p.setPen(QColor(_COLOR_TEXT))
        p.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        p.drawText(QRectF(text_x, center_y - 22, w - text_x - 32, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, self._current_title)

        # partial 文本
        display = self._current_partial or "等待识别结果..."
        p.setPen(QColor(_COLOR_TEXT_SECONDARY))
        p.setFont(QFont("Microsoft YaHei UI", 11))
        p.drawText(QRectF(text_x, center_y + 6, w - text_x - 32, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, display)

        # 底部提示
        p.setPen(QColor(_COLOR_TEXT_DIM))
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(QRectF(0, h - 28, w, 20),
                   Qt.AlignCenter, "请稍候...")

    def _draw_final(self, p: QPainter) -> None:
        """绘制识别完成：绿色勾圆 + 结果文本。

        Args:
            p: QPainter 实例
        """
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT
        cx = 44
        cy = h // 2
        r = 20

        # 绿色背景圆
        p.setBrush(QColor(_COLOR_SUCCESS))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)

        # 白色勾（用三条线段构成 ✓ 形）
        pen = QPen(QColor(_COLOR_TEXT), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        # 勾的三个顶点（与原 tkinter 实现一致）
        p.drawLine(QPointF(cx - 8, cy), QPointF(cx - 2, cy + 6))
        p.drawLine(QPointF(cx - 2, cy + 6), QPointF(cx + 10, cy - 8))

        # 状态文字
        p.setPen(QColor(_COLOR_TEXT))
        p.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        p.drawText(QRectF(cx + r + 16, cy - 22, w - cx - r - 48, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, "识别完成")

        # 结果文本
        p.setPen(QColor(_COLOR_TEXT_SECONDARY))
        p.setFont(QFont("Microsoft YaHei UI", 11))
        p.drawText(QRectF(cx + r + 16, cy + 6, w - cx - r - 48, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, self._current_final_text)

    def _draw_error(self, p: QPainter) -> None:
        """绘制错误状态：紫色圆 + ! 号 + 错误信息。

        Args:
            p: QPainter 实例
        """
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT
        cx = 44
        cy = h // 2
        r = 20

        # 紫色背景圆
        p.setBrush(QColor(_COLOR_ERROR))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)

        # ! 号
        p.setPen(QColor(_COLOR_TEXT))
        p.setFont(QFont("Segoe UI", 14, QFont.Bold))
        p.drawText(QRectF(cx - 12, cy - 14, 24, 24),
                   Qt.AlignCenter, "!")

        # 错误标题
        p.setPen(QColor(_COLOR_TEXT))
        p.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        p.drawText(QRectF(cx + r + 16, cy - 22, w - cx - r - 48, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, "出错了")

        # 错误详情
        p.setPen(QColor(_COLOR_TEXT_SECONDARY))
        p.setFont(QFont("Microsoft YaHei UI", 11))
        p.drawText(QRectF(cx + r + 16, cy + 6, w - cx - r - 48, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, self._current_error_msg)

    def _draw_generic(self, p: QPainter) -> None:
        """绘制通用 show() 状态：状态文字 + 副文字，居中显示。

        Args:
            p: QPainter 实例
        """
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT
        p.setPen(QColor(_COLOR_TEXT))
        p.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        p.drawText(QRectF(0, h // 2 - 34, w, 24),
                   Qt.AlignCenter, self._current_status_text)
        p.setPen(QColor(_COLOR_TEXT_SECONDARY))
        p.setFont(QFont("Microsoft YaHei UI", 11))
        p.drawText(QRectF(0, h // 2 - 10, w, 24),
                   Qt.AlignCenter, self._current_status_subtext)

    def _calc_bar_height(self, amplitude: float, index: int, total: int) -> int:
        """计算单个音量条的高度。

        Args:
            amplitude: 振幅 0~1
            index: 条索引
            total: 总条数

        Returns:
            条高度（像素）
        """
        # 基础高度随振幅线性增长
        base_h = _VOLUME_BAR_MIN_HEIGHT + amplitude * (
            _VOLUME_BAR_MAX_HEIGHT - _VOLUME_BAR_MIN_HEIGHT
        )
        # 左右两端的条略低，中间的略高（模拟音频波形）
        center = total / 2.0
        distance_from_center = abs(index - center + 0.5) / (total / 2.0)
        wave_factor = 1.0 - distance_from_center * 0.4
        return max(_VOLUME_BAR_MIN_HEIGHT, int(base_h * wave_factor))


class FloatingPanelController:
    """录音悬浮面板控制器。

    使用 QPainter 在自定义 QWidget 上绘制，实现现代化的录音指示器。
    所有 UI 操作必须在 Qt 主线程。
    """

    def __init__(self) -> None:
        # QApplication 实例（替代 tkinter Tk()）
        # 使用 instance() 避免重复创建（PyInstaller 等环境下可能已有）
        self._app = QApplication.instance() or QApplication([])
        # 隐藏根 QWidget（替代被 withdraw 的 Tk root），对话框可作为 parent
        self._root = QWidget()
        self._root.hide()
        # 跨线程调度辅助：所有子线程回调通过它投递到主线程 Qt 事件循环
        # 必须在主线程构造（默认即主线程），它本身住在主线程，
        # 子线程 emit 信号时 Qt 自动走 QueuedConnection 投递到主线程
        self._caller = _MainThreadCaller(parent=self._root)
        # 悬浮面板 widget（替代 tk.Toplevel）
        self._panel: Optional[_PanelWidget] = None
        # 自动隐藏定时器（QTimer 单次触发）
        self._auto_hide_timer: Optional[QTimer] = None
        # 周期性轮询定时器（app.py 用于音频拉取循环）
        self._polling_timer: Optional[QTimer] = None
        # 可见标志
        self._visible = False

    # ===== 根窗口可见性 =====

    def ensure_root_visible(self) -> None:
        """确保根窗口处于可见状态。

        PySide6 下根窗口默认隐藏即可（对话框 exec 自带模态层），
        但保留此方法以兼容调用方语义。
        """
        # PySide6 中 QDialog.exec() 不依赖父窗口可见，因此无需特殊处理
        pass

    # ===== 公共接口 =====

    def root(self) -> QWidget:
        """暴露根 QWidget，作为对话框的 parent。"""
        return self._root

    def run_mainloop(self) -> None:
        """启动 Qt 主循环。"""
        DiagLog.shared().write("[Panel] Qt mainloop 启动")
        # Qt 退出后由 quit_mainloop 触发 exec() 返回
        self._app.exec()

    def quit_mainloop(self) -> None:
        """请求退出主循环。"""
        self.schedule_on_main(self._app.quit)

    def schedule_on_main(self, callback: Callable[[], None], delay_ms: int = 0) -> None:
        """将回调调度到主线程执行。

        基于 Qt 信号槽 + QueuedConnection 实现真正跨线程投递：
        子线程调用本方法时，信号 emit 触发 Qt 把回调打包成事件，
        投递到 _caller 所在主线程的事件循环，主线程下一轮事件循环时执行回调。

        注意：不能用 QTimer.singleShot 替代——它在调用线程的事件循环里运行，
        子线程没有事件循环时回调永远不会执行。

        Args:
            callback: 待执行的回调
            delay_ms: 延迟毫秒数（0 表示立即在主线程下一轮事件循环执行）
        """
        try:
            self._caller.call(callback, delay_ms)
        except Exception as e:
            DiagLog.shared().write(f"[Panel] schedule_on_main 异常: {e}")

    def start_polling(self, callback: Callable[[], None], interval_ms: int) -> None:
        """启动周期性轮询定时器（替代 tkinter 的 root.after 循环调度）。

        仅维护一个轮询定时器实例：重复调用会先停止上一个再启动新的。

        Args:
            callback: 每个 interval 触发的回调
            interval_ms: 触发间隔（毫秒）
        """
        self.stop_polling()
        t = QTimer(self._panel)
        t.setInterval(interval_ms)
        t.timeout.connect(callback)
        t.start()
        self._polling_timer = t

    def stop_polling(self) -> None:
        """停止周期性轮询定时器（若存在）。"""
        if self._polling_timer is not None:
            try:
                self._polling_timer.stop()
            except Exception:
                pass
            self._polling_timer = None

    # ===== 状态显示接口 =====

    def show_idle(self, provider_name: str = "语音识别", wake_word: str = "") -> None:
        """显示就绪面板（启动时默认打开，不自动隐藏）。

        Args:
            provider_name: 当前 ASR 提供商名称
            wake_word: 唤醒词提示（如「小薇小薇」，空串表示未启用语音唤醒）
        """
        self._stop_animations()
        self._cancel_auto_hide()
        self._ensure_panel()
        self._panel._current_state = STATE_IDLE
        self._panel._current_provider_name = provider_name
        self._panel._current_wake_word = wake_word
        self._panel.update()
        self._show_panel()

    def show_recording(
        self,
        partial_text: str = "",
        amplitude: float = 0.0,
        provider_name: str = "语音识别",
    ) -> None:
        """显示录音中状态。

        Args:
            partial_text: 实时 partial 识别文本
            amplitude: 当前音量振幅（0~1）
            provider_name: 当前 ASR 提供商名称
        """
        self._ensure_panel()
        self._panel._current_provider_name = provider_name
        self._panel._current_partial = partial_text
        self._panel._current_amplitude = amplitude
        self._panel._current_state = STATE_RECORDING
        self._panel.update()
        self._show_panel()
        # 启动脉冲动画
        self._panel.start_pulse()

    def _transcribe_partial(self, text: str, amplitude: float) -> None:
        """实时更新 partial 文本和音量条，不重置动画状态。

        由 app.py 的 _on_asr_partial 在 ASR 实时回调中调用，
        通过 schedule_on_main 投递到主线程后执行。仅刷新 _current_partial
        和 _current_amplitude 字段，触发 paintEvent 重绘：
        - recording 状态：partial 文本会更新到"正在聆听..."位置
        - transcribing 状态：partial 文本会更新到"等待识别结果..."位置
        脉冲/旋转动画继续运行，不会被打断。

        Args:
            text: 实时 partial 文本
            amplitude: 当前音量振幅（0~1）
        """
        if self._panel is None:
            return
        self._panel._current_partial = text
        self._panel._current_amplitude = amplitude
        # 仅触发重绘，不改 state，动画对象继续运行
        self._panel.update()

    def show_transcribing(self, partial_text: str = "") -> None:
        """显示识别中状态。

        Args:
            partial_text: 待识别的 partial 文本
        """
        self._stop_animations()
        self._panel._current_partial = partial_text
        self._panel._current_state = STATE_TRANSCRIBING
        self._panel._current_title = _TITLE_TRANSCRIBING
        self._panel.update()
        self._show_panel()
        self._panel.start_spinner()

    def show_polishing(self, partial_text: str = "") -> None:
        """显示 AI 润色中状态（复用识别中的旋转样式）。

        启用 LLM 后处理后，识别完成到文本注入之间会调用大模型，
        此状态告知用户正在进行 AI 纠错，避免误以为卡死。

        Args:
            partial_text: 待润色的原始文本（展示给用户参考）
        """
        self._stop_animations()
        self._panel._current_partial = partial_text
        self._panel._current_state = STATE_TRANSCRIBING
        self._panel._current_title = _TITLE_POLISHING
        self._panel.update()
        self._show_panel()
        self._panel.start_spinner()

    def show_final(self, text: str) -> None:
        """显示识别完成，2 秒后自动隐藏。

        Args:
            text: 最终识别文本
        """
        self._stop_animations()
        self._panel._current_state = STATE_FINAL
        self._panel._current_final_text = text
        self._panel.update()
        self._show_panel()
        self._schedule_auto_hide(2000)

    def show_error(self, message: str) -> None:
        """显示错误消息，3 秒后自动隐藏。

        Args:
            message: 错误信息
        """
        self._stop_animations()
        self._panel._current_state = STATE_ERROR
        self._panel._current_error_msg = message
        self._panel.update()
        self._show_panel()
        self._schedule_auto_hide(3000)

    def show_message_test(self, message: str) -> None:
        """显示测试提示，2 秒后自动隐藏。

        用于快捷键测试等场景，复用完成状态的样式。

        Args:
            message: 测试提示文本
        """
        self._stop_animations()
        self._panel._current_state = STATE_FINAL
        self._panel._current_final_text = message
        self._panel.update()
        self._show_panel()
        self._schedule_auto_hide(2000)

    def show(self, status: str, text: str = "") -> None:
        """通用显示接口（兼容旧调用）。

        Args:
            status: 状态文字
            text: 内容文字
        """
        self._stop_animations()
        self._panel._current_state = STATE_FINAL  # 用一个未列出的 state 走 generic 分支
        # 但 _PanelWidget 只识别 5 个状态 + else generic，这里用一个特殊标记
        # 简化方案：直接复用 final 状态显示 status 文字
        self._panel._current_state = STATE_FINAL
        self._panel._current_final_text = status
        self._panel._current_status_text = status
        self._panel._current_status_subtext = text
        self._panel.update()
        self._show_panel()

    def hide(self) -> None:
        """隐藏面板。"""
        self._cancel_auto_hide()
        self._stop_animations()
        if self._panel and self._visible:
            self._panel.hide()
            self._visible = False

    # ===== 内部辅助方法 =====

    def _ensure_panel(self) -> None:
        """创建/确保面板 widget 存在并定位到屏幕底部居中。"""
        if self._panel is not None:
            return
        # 计算屏幕底部居中位置
        screen = self._app.primaryScreen().geometry()
        x = (screen.width() - _PANEL_WIDTH) // 2
        y = screen.height() - _PANEL_HEIGHT - _BOTTOM_MARGIN
        self._panel = _PanelWidget()
        self._panel.move(x, y)
        # 初始隐藏
        self._panel.hide()
        self._visible = False

    def _show_panel(self) -> None:
        """显示面板并提升到最前。"""
        self._panel.show()
        self._panel.raise_()
        self._visible = True

    def _stop_animations(self) -> None:
        """停止所有动画（脉冲 + 旋转）。"""
        if self._panel is not None:
            self._panel.stop_pulse()
            self._panel.stop_spinner()

    # ===== 自动隐藏 =====

    def _schedule_auto_hide(self, delay_ms: int) -> None:
        """安排延迟自动隐藏。

        Args:
            delay_ms: 延迟毫秒数
        """
        self._cancel_auto_hide()
        t = QTimer(self._panel)
        t.setSingleShot(True)
        t.timeout.connect(self.hide)
        t.start(delay_ms)
        self._auto_hide_timer = t

    def _cancel_auto_hide(self) -> None:
        """取消挂起的自动隐藏任务。"""
        if self._auto_hide_timer is not None:
            try:
                self._auto_hide_timer.stop()
            except Exception:
                pass
            self._auto_hide_timer = None
