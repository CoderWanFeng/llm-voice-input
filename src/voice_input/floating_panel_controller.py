"""录音悬浮面板模块（美化版）。

设计目标：
- 现代化视觉效果，类似语音助手风格
- Canvas 绘制录音指示灯、音量条、状态图标
- 录音状态：醒目的红色脉冲光圈 + 音量可视化
- 识别状态：蓝色旋转指示
- 完成/错误：绿色/紫色反馈

线程模型：tkinter 必须在主线程，所有 UI 操作通过 schedule_on_main 调度。
"""

from __future__ import annotations

import math
import tkinter as tk
from typing import Callable, List, Optional

from .diag_log import DiagLog


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
_PULSE_INTERVAL_MS = 500            # 脉冲动画间隔
_VOLUME_BAR_COUNT = 12              # 音量条数量
_VOLUME_BAR_MAX_HEIGHT = 28         # 音量条最大高度
_VOLUME_BAR_MIN_HEIGHT = 4          # 音量条最小高度

# ===== 状态类型 =====
STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_TRANSCRIBING = "transcribing"
STATE_FINAL = "final"
STATE_ERROR = "error"


class FloatingPanelController:
    """录音悬浮面板控制器。

    使用 Canvas 绘制自定义视觉元素，实现现代化的录音指示器。
    所有 UI 操作必须在 tkinter 主线程。
    """

    def __init__(self) -> None:
        # tkinter root：初始隐藏
        self._root = tk.Tk()
        self._root.withdraw()
        # 悬浮面板
        self._panel: Optional[tk.Toplevel] = None
        # Canvas 画布（绘制所有视觉元素）
        self._canvas: Optional[tk.Canvas] = None
        # 各绘制元素 ID（用于更新）
        self._rec_circle_id: Optional[int] = None
        self._rec_glow_id: Optional[int] = None
        self._rec_text_id: Optional[int] = None
        self._status_text_id: Optional[int] = None
        self._provider_text_id: Optional[int] = None
        self._volume_bar_ids: List[int] = []
        self._text_content_id: Optional[int] = None
        self._hint_text_id: Optional[int] = None
        self._spinner_ids: List[int] = []
        # 动画状态
        self._visible = False
        self._auto_hide_id: Optional[str] = None
        self._pulse_id: Optional[str] = None
        self._spinner_id: Optional[str] = None
        self._anim_phase = 0.0          # 动画相位（0~1）
        self._current_state = STATE_TRANSCRIBING
        self._current_provider_name = "语音识别"
        # 唤醒词提示（show_idle 时设置；空串表示未启用语音唤醒）
        self._current_wake_word = ""
        self._current_partial = ""
        self._current_amplitude = 0.0
        self._pulse_running = False

    # ===== 根窗口可见性修复 =====

    def ensure_root_visible(self) -> None:
        """确保 tkinter 根窗口处于可见状态。

        Windows 下根窗口被 withdraw() 后，子 Toplevel 可能不显示。
        因此在创建/显示子窗口前先 deiconify() 根窗口。
        """
        try:
            if self._root.state() == "withdrawn":
                self._root.deiconify()
                self._root.geometry("1x1+0+0")
                self._root.overrideredirect(True)
                self._root.attributes("-topmost", True)
        except Exception:
            pass

    # ===== 公共接口 =====

    def root(self) -> tk.Tk:
        """暴露 tkinter root。"""
        return self._root

    def run_mainloop(self) -> None:
        """启动 tkinter 主循环。"""
        DiagLog.shared().write("[Panel] tkinter mainloop 启动")
        self._root.mainloop()

    def quit_mainloop(self) -> None:
        """请求退出主循环。"""
        self.schedule_on_main(self._root.quit)

    def schedule_on_main(self, callback: Callable[[], None], delay_ms: int = 0) -> None:
        """将回调调度到主线程执行。"""
        try:
            if delay_ms > 0:
                self._root.after(delay_ms, callback)
            else:
                self._root.after(0, callback)
        except Exception as e:
            DiagLog.shared().write(f"[Panel] schedule_on_main 异常: {e}")

    # ===== 状态显示接口 =====

    def show_idle(self, provider_name: str = "语音识别", wake_word: str = "") -> None:
        """显示就绪面板（启动时默认打开，不自动隐藏）。

        让用户运行工具后立即看到主界面，而不是只有托盘图标。
        面板持续显示，录音时切换为录音状态，隐藏后可通过
        托盘菜单「显示面板」或按热键再次唤出。

        Args:
            provider_name: 当前 ASR 提供商名称
            wake_word: 唤醒词提示（如「小薇小薇」，空串表示未启用语音唤醒）
        """
        self._stop_pulse()
        self._stop_spinner()
        self._cancel_auto_hide()
        self._current_state = STATE_IDLE
        self._current_provider_name = provider_name
        self._current_wake_word = wake_word
        self._ensure_panel()
        self._render_idle()

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
        self._current_provider_name = provider_name
        self._current_partial = partial_text
        self._current_amplitude = amplitude
        self._current_state = STATE_RECORDING
        self._ensure_panel()
        self._render_recording()
        if not self._pulse_running:
            self._start_pulse()

    def show_transcribing(self, partial_text: str = "") -> None:
        """显示识别中状态。"""
        self._stop_pulse()
        self._stop_spinner()
        self._current_partial = partial_text
        self._current_state = STATE_TRANSCRIBING
        self._ensure_panel()
        self._render_transcribing(partial_text)
        self._start_spinner()

    def show_polishing(self, partial_text: str = "") -> None:
        """显示 AI 润色中状态（复用识别中的旋转样式）。

        启用 LLM 后处理后，识别完成到文本注入之间会调用大模型，
        此状态告知用户正在进行 AI 纠错，避免误以为卡死。

        Args:
            partial_text: 待润色的原始文本（展示给用户参考）
        """
        self._stop_pulse()
        self._stop_spinner()
        self._current_partial = partial_text
        self._current_state = STATE_TRANSCRIBING
        self._ensure_panel()
        self._render_transcribing(partial_text, title="AI 润色中")
        self._start_spinner()

    def show_final(self, text: str) -> None:
        """显示识别完成，2 秒后自动隐藏。"""
        self._stop_pulse()
        self._stop_spinner()
        self._current_state = STATE_FINAL
        self._ensure_panel()
        self._render_final(text)
        self._schedule_auto_hide(2000)

    def show_error(self, message: str) -> None:
        """显示错误消息，3 秒后自动隐藏。"""
        self._stop_pulse()
        self._stop_spinner()
        self._current_state = STATE_ERROR
        self._ensure_panel()
        self._render_error(message)
        self._schedule_auto_hide(3000)

    def show_message_test(self, message: str) -> None:
        """显示测试提示，2 秒后自动隐藏。

        用于快捷键测试等场景，复用完成状态的样式。
        """
        self._stop_pulse()
        self._stop_spinner()
        self._current_state = STATE_FINAL
        self._ensure_panel()
        self._render_final(message)
        self._schedule_auto_hide(2000)

    def show(self, status: str, text: str = "") -> None:
        """通用显示接口（兼容旧调用）。

        Args:
            status: 状态文字
            text: 内容文字
        """
        self._stop_pulse()
        self._stop_spinner()
        self._current_state = STATE_FINAL
        self._ensure_panel()
        c = self._canvas
        if c is None:
            return
        self._draw_panel_background()
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT

        # 简单居中显示
        c.create_text(
            w // 2, h // 2 - 10,
            text=status,
            fill=_COLOR_TEXT,
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        c.create_text(
            w // 2, h // 2 + 14,
            text=text,
            fill=_COLOR_TEXT_SECONDARY,
            font=("Microsoft YaHei UI", 11),
        )

        self._panel.deiconify()
        self._panel.lift()
        self._visible = True

    def hide(self) -> None:
        """隐藏面板。"""
        self._cancel_auto_hide()
        self._stop_pulse()
        self._stop_spinner()
        if self._panel and self._visible:
            self._panel.withdraw()
            self._visible = False

    # ===== 渲染方法（在 Canvas 上绘制精美 UI）=====

    def _ensure_panel(self) -> None:
        """创建/确保面板可见。"""
        if self._panel is not None:
            return

        # 关键：Windows 下必须保证根窗口可见
        self.ensure_root_visible()

        # 计算屏幕底部居中位置
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = (screen_w - _PANEL_WIDTH) // 2
        y = screen_h - _PANEL_HEIGHT - _BOTTOM_MARGIN

        # 创建无边框置顶面板
        self._panel = tk.Toplevel(self._root)
        self._panel.overrideredirect(True)
        self._panel.geometry(f"{_PANEL_WIDTH}x{_PANEL_HEIGHT}+{x}+{y}")
        self._panel.attributes("-topmost", True)
        try:
            self._panel.attributes("-alpha", 0.96)
        except Exception:
            pass
        self._panel.configure(bg=_COLOR_BG)

        # 创建 Canvas 画布
        self._canvas = tk.Canvas(
            self._panel,
            width=_PANEL_WIDTH,
            height=_PANEL_HEIGHT,
            bg=_COLOR_BG,
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack(fill="both", expand=True)

        # 初始隐藏
        self._panel.withdraw()
        self._visible = False
        self._canvas.update_idletasks()

    def _draw_panel_background(self) -> None:
        """绘制面板背景：圆角矩形 + 边框。"""
        if not self._canvas:
            return
        c = self._canvas
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT
        radius = 16

        # 清除画布
        c.delete("all")

        # 背景圆角矩形
        self._bg_id = c.create_rectangle(
            2, 2, w - 2, h - 2,
            fill=_COLOR_BG,
            outline=_COLOR_BORDER,
            width=1,
        )

    def _render_idle(self) -> None:
        """渲染就绪状态：绿色圆点 + 工具名称 + 提供商 + 快捷键提示。"""
        self._ensure_panel()
        c = self._canvas
        if c is None:
            return

        self._draw_panel_background()
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT

        # 左侧：绿色就绪圆点
        cx, cy, r = 44, h // 2, 18
        c.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=_COLOR_SUCCESS,
            outline="",
        )
        c.create_text(
            cx, cy,
            text="●",
            fill=_COLOR_TEXT,
            font=("Segoe UI", 9, "bold"),
        )

        # 状态文字
        c.create_text(
            cx + r + 16, cy - 12,
            text="VoiceInput 就绪",
            fill=_COLOR_TEXT,
            font=("Microsoft YaHei UI", 13, "bold"),
            anchor="w",
        )

        # 当前提供商
        c.create_text(
            cx + r + 16, cy + 14,
            text=f"当前提供商：{self._current_provider_name}",
            fill=_COLOR_TEXT_SECONDARY,
            font=("Microsoft YaHei UI", 11),
            anchor="w",
        )

        # 底部提示：启用语音唤醒时提示唤醒词，否则仅提示快捷键
        if self._current_wake_word:
            hint = f"说「{self._current_wake_word}」或按 Ctrl + Alt + K 开始录音 · 右键托盘图标可配置"
        else:
            hint = "按 Ctrl + Alt + K 开始 / 停止录音 · 右键托盘图标可配置"
        c.create_text(
            w // 2, h - 16,
            text=hint,
            fill=_COLOR_TEXT_DIM,
            font=("Microsoft YaHei UI", 9),
        )

        # 显示面板
        self._panel.deiconify()
        self._panel.lift()
        self._visible = True

    def _render_recording(self) -> None:
        """渲染录音中状态：红色脉冲光圈 + 音量条 + 文本。"""
        self._ensure_panel()
        c = self._canvas
        if c is None:
            return

        self._draw_panel_background()
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT

        # 左侧区域：录音指示灯
        # 光晕圆（大）
        self._rec_glow_id = c.create_oval(
            20, h // 2 - 24, 68, h // 2 + 24,
            fill=_COLOR_RECORDING_GLOW,
            outline="",
        )
        # 主录音圆
        self._rec_circle_id = c.create_oval(
            26, h // 2 - 18, 62, h // 2 + 18,
            fill=_COLOR_RECORDING,
            outline="",
        )
        # REC 文字
        self._rec_text_id = c.create_text(
            44, h // 2,
            text="● REC",
            fill=_COLOR_TEXT,
            font=("Segoe UI", 10, "bold"),
        )

        # 音量条区域（右侧中上）
        vol_x_start = 88
        vol_y = h // 2 - 4
        self._volume_bar_ids = []
        bar_width = 6
        bar_gap = 3
        total_bar_width = _VOLUME_BAR_COUNT * (bar_width + bar_gap) - bar_gap
        bar_x_start = vol_x_start

        # 根据振幅计算每条音量条高度
        amp = self._current_amplitude
        for i in range(_VOLUME_BAR_COUNT):
            # 创建高度随振幅变化的条
            bar_h = self._calc_bar_height(amp, i, _VOLUME_BAR_COUNT)
            x1 = bar_x_start + i * (bar_width + bar_gap)
            x2 = x1 + bar_width
            y1 = vol_y - bar_h
            y2 = vol_y
            bar_id = c.create_rectangle(
                x1, y1, x2, y2,
                fill=_COLOR_RECORDING,
                outline="",
            )
            self._volume_bar_ids.append(bar_id)

        # 右侧文字区域
        text_x = bar_x_start + total_bar_width + 16

        # 第一行：状态 + 提供商
        self._status_text_id = c.create_text(
            text_x, 28,
            text="正在录音",
            fill=_COLOR_TEXT,
            font=("Microsoft YaHei UI", 13, "bold"),
            anchor="w",
        )
        self._provider_text_id = c.create_text(
            w - 16, 28,
            text=self._current_provider_name,
            fill=_COLOR_TEXT_SECONDARY,
            font=("Microsoft YaHei UI", 9),
            anchor="e",
        )

        # 第二行：partial 文本
        partial = self._current_partial or "正在聆听..."
        self._text_content_id = c.create_text(
            text_x, 58,
            text=partial,
            fill=_COLOR_TEXT,
            font=("Microsoft YaHei UI", 11),
            anchor="w",
            width=w - text_x - 32,
        )

        # 第三行：提示
        self._hint_text_id = c.create_text(
            w // 2, h - 16,
            text="按 Ctrl + Alt + K 停止录音",
            fill=_COLOR_TEXT_DIM,
            font=("Microsoft YaHei UI", 9),
        )

        # 显示面板
        self._panel.deiconify()
        self._panel.lift()
        self._visible = True

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
        base_h = _VOLUME_BAR_MIN_HEIGHT + amplitude * (_VOLUME_BAR_MAX_HEIGHT - _VOLUME_BAR_MIN_HEIGHT)
        # 左右两端的条略低，中间的略高（模拟音频波形）
        center = total / 2.0
        distance_from_center = abs(index - center + 0.5) / (total / 2.0)
        wave_factor = 1.0 - distance_from_center * 0.4
        return max(_VOLUME_BAR_MIN_HEIGHT, int(base_h * wave_factor))

    def _update_volume_bars(self, amplitude: float) -> None:
        """动态更新音量条高度（partial 回调时调用）。"""
        if not self._canvas or not self._volume_bar_ids:
            return
        c = self._canvas
        bar_width = 6
        bar_gap = 3
        vol_y = _PANEL_HEIGHT // 2 - 4
        for i, bar_id in enumerate(self._volume_bar_ids):
            bar_h = self._calc_bar_height(amplitude, i, len(self._volume_bar_ids))
            x1 = 88 + i * (bar_width + bar_gap)
            x2 = x1 + bar_width
            c.coords(bar_id, x1, vol_y - bar_h, x2, vol_y)

    def _transcribe_partial(self, text: str, amplitude: float) -> None:
        """实时更新 partial 文本和音量。"""
        self._current_partial = text
        self._current_amplitude = amplitude
        if not self._canvas:
            return
        # 更新 partial 文字
        if self._text_content_id:
            display = text or "正在聆听..."
            self._canvas.itemconfig(self._text_content_id, text=display)
        # 更新音量条
        self._update_volume_bars(amplitude)

    def _render_transcribing(self, partial_text: str, title: str = "正在识别中") -> None:
        """渲染识别中状态：蓝色旋转指示 + partial 文本。

        Args:
            partial_text: 展示文本
            title: 状态标题（识别中/AI 润色中）
        """
        self._ensure_panel()
        c = self._canvas
        if c is None:
            return

        self._draw_panel_background()
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT

        # 左侧：旋转指示器（三个小点）
        dot_radius = 5
        dot_gap = 14
        center_y = h // 2
        start_x = 40
        self._spinner_ids = []
        for i in range(3):
            dot_x = start_x + i * dot_gap
            dot_id = c.create_oval(
                dot_x - dot_radius, center_y - dot_radius,
                dot_x + dot_radius, center_y + dot_radius,
                fill=_COLOR_TRANSCRIBING,
                outline="",
            )
            self._spinner_ids.append(dot_id)

        # 状态文字
        c.create_text(
            start_x + 3 * dot_gap + 16, center_y - 10,
            text=title,
            fill=_COLOR_TEXT,
            font=("Microsoft YaHei UI", 13, "bold"),
            anchor="w",
        )

        # partial 文本
        text_x = start_x + 3 * dot_gap + 16
        display = partial_text or "等待识别结果..."
        c.create_text(
            text_x, center_y + 14,
            text=display,
            fill=_COLOR_TEXT_SECONDARY,
            font=("Microsoft YaHei UI", 11),
            anchor="w",
            width=w - text_x - 32,
        )

        # 底部提示
        c.create_text(
            w // 2, h - 16,
            text="请稍候...",
            fill=_COLOR_TEXT_DIM,
            font=("Microsoft YaHei UI", 9),
        )

        self._panel.deiconify()
        self._panel.lift()
        self._visible = True

    def _render_final(self, text: str) -> None:
        """渲染识别完成：绿色勾 + 结果文本。"""
        self._ensure_panel()
        c = self._canvas
        if c is None:
            return

        self._draw_panel_background()
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT

        # 绿色勾圆圈
        cx = 44
        cy = h // 2
        r = 20
        c.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=_COLOR_SUCCESS,
            outline="",
        )
        # 勾
        c.create_line(
            cx - 8, cy, cx - 2, cy + 6, cx + 10, cy - 8,
            fill=_COLOR_TEXT,
            width=3,
            capstyle="round",
        )

        # 状态文字
        c.create_text(
            cx + r + 16, cy - 12,
            text="识别完成",
            fill=_COLOR_TEXT,
            font=("Microsoft YaHei UI", 13, "bold"),
            anchor="w",
        )

        # 结果文本
        c.create_text(
            cx + r + 16, cy + 14,
            text=text,
            fill=_COLOR_TEXT_SECONDARY,
            font=("Microsoft YaHei UI", 11),
            anchor="w",
            width=w - cx - r - 48,
        )

        self._panel.deiconify()
        self._panel.lift()
        self._visible = True

    def _render_error(self, message: str) -> None:
        """渲染错误状态：紫色三角 + 错误信息。"""
        self._ensure_panel()
        c = self._canvas
        if c is None:
            return

        self._draw_panel_background()
        w = _PANEL_WIDTH
        h = _PANEL_HEIGHT

        # 警告三角
        cx = 44
        cy = h // 2
        r = 20
        # 背景圆
        c.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=_COLOR_ERROR,
            outline="",
        )
        # ! 号
        c.create_text(
            cx, cy - 2,
            text="!",
            fill=_COLOR_TEXT,
            font=("Segoe UI", 14, "bold"),
        )

        # 错误标题
        c.create_text(
            cx + r + 16, cy - 12,
            text="出错了",
            fill=_COLOR_TEXT,
            font=("Microsoft YaHei UI", 13, "bold"),
            anchor="w",
        )

        # 错误详情
        c.create_text(
            cx + r + 16, cy + 14,
            text=message,
            fill=_COLOR_TEXT_SECONDARY,
            font=("Microsoft YaHei UI", 11),
            anchor="w",
            width=w - cx - r - 48,
        )

        self._panel.deiconify()
        self._panel.lift()
        self._visible = True

    # ===== 动画方法 =====

    def _start_pulse(self) -> None:
        """启动录音状态脉冲动画：呼吸效果。"""
        self._stop_pulse()
        self._pulse_running = True
        self._anim_phase = 0.0
        self._pulse_tick()

    def _pulse_tick(self) -> None:
        """脉冲动画一帧：更新光晕大小和亮度。"""
        if self._current_state != STATE_RECORDING or not self._visible:
            self._pulse_running = False
            return

        self._anim_phase += 0.15
        # 使用 sin 函数模拟呼吸
        t = (math.sin(self._anim_phase) + 1.0) / 2.0  # 0.0 ~ 1.0

        c = self._canvas
        if c and self._rec_glow_id and self._rec_circle_id:
            h = _PANEL_HEIGHT
            cx = 44
            cy = h // 2
            # 光晕大小随呼吸变化
            glow_r = 20 + t * 12  # 20 ~ 32
            c.coords(
                self._rec_glow_id,
                cx - glow_r, cy - glow_r,
                cx + glow_r, cy + glow_r,
            )
            # 光晕颜色：最亮时用浅红
            glow_intensity = int(0xB0 + t * 0x4F)  # 176 ~ 223
            glow_color = f"#FF{glow_intensity:02X}{glow_intensity:02X}"
            c.itemconfig(self._rec_glow_id, fill=glow_color)

            # 更新 partial 文本（实时可能已变）
            if self._text_content_id:
                display = self._current_partial or "正在聆听..."
                c.itemconfig(self._text_content_id, text=display)

            # 更新音量条
            self._update_volume_bars(self._current_amplitude)

        self._pulse_id = self._root.after(_PULSE_INTERVAL_MS, self._pulse_tick)

    def _stop_pulse(self) -> None:
        """停止脉冲动画。"""
        self._pulse_running = False
        if self._pulse_id is not None:
            try:
                self._root.after_cancel(self._pulse_id)
            except Exception:
                pass
            self._pulse_id = None

    def _start_spinner(self) -> None:
        """启动识别中的三点跳动动画。"""
        self._stop_spinner()
        self._spinner_phase = 0
        self._spinner_tick()

    def _spinner_tick(self) -> None:
        """旋转指示器动画：三个小点依次跳动。"""
        if self._current_state != STATE_TRANSCRIBING or not self._visible:
            return
        self._spinner_phase = (self._spinner_phase + 1) % 4
        c = self._canvas
        if c and self._spinner_ids:
            dot_radius = 5
            dot_gap = 14
            center_y = _PANEL_HEIGHT // 2
            start_x = 40
            for i, dot_id in enumerate(self._spinner_ids):
                # 当前跳动的点放大
                if i == self._spinner_phase % 3:
                    r = dot_radius + 2
                    y_off = -2
                else:
                    r = dot_radius
                    y_off = 0
                x = start_x + i * dot_gap
                c.coords(
                    dot_id,
                    x - r, center_y - r + y_off,
                    x + r, center_y + r + y_off,
                )
        self._spinner_id = self._root.after(250, self._spinner_tick)

    def _stop_spinner(self) -> None:
        """停止旋转指示器。"""
        if self._spinner_id is not None:
            try:
                self._root.after_cancel(self._spinner_id)
            except Exception:
                pass
            self._spinner_id = None

    # ===== 自动隐藏 =====

    def _schedule_auto_hide(self, delay_ms: int) -> None:
        """安排延迟自动隐藏。"""
        self._cancel_auto_hide()
        self._auto_hide_id = self._root.after(delay_ms, self.hide)

    def _cancel_auto_hide(self) -> None:
        """取消挂起的自动隐藏任务。"""
        if self._auto_hide_id is not None:
            try:
                self._root.after_cancel(self._auto_hide_id)
            except Exception:
                pass
            self._auto_hide_id = None
