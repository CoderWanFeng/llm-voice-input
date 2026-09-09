"""语音唤醒设置对话框模块。

提供语音唤醒与结束词的可视化配置界面（与凭证对话框同风格）：
- 启用/禁用语音唤醒（勾选框）+ 唤醒词自定义（可编辑下拉框）
- 启用/禁用语音结束词（录音中说出自动停止）+ 结束词自定义
- 保存时校验唤醒词/结束词每个字是否在 Vosk 离线词库，
  含缺失字时弹窗警告并让用户确认是否仍然保存
- 保存后由主应用热更新检测器，无需重启

唤醒词/结束词均选择「输入文字」而非「录入语音」：
底层检测是文本语法匹配，录音最终也要转成文字，等于绕一圈
还引入转写误差；文字输入可即时校验词库、随时修改、不受环境噪音影响。

必须在 tkinter 主线程调度。
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Optional

from . import config as config_module
from .diag_log import DiagLog
from .wake_word_detector import STOP_WORD, WAKE_WORD, WakeWordDetector


# 预设唤醒词（均已在模型词库中验证可用）
_WAKE_PRESETS: List[str] = ["小爱小爱", "小薇小薇", "你好小智", "小智小智"]

# 预设结束词（均已在模型词库中验证可用）
_STOP_PRESETS: List[str] = ["结束录音", "停止录音", "结束结束"]

# 自定义唤醒词/结束词格式：2~6 个汉字
_WAKE_WORD_RE = re.compile(r"^[\u4e00-\u9fff]{2,6}$")


class WakeWordDialog:
    """语音唤醒设置对话框，模态弹出。

    用户可开关语音唤醒并自定义唤醒词，保存后写入配置文件，
    由主应用负责热更新检测器与界面提示。
    """

    def __init__(self, parent: tk.Tk, detector: WakeWordDetector) -> None:
        """初始化对话框。

        Args:
            parent: 父窗口（tkinter root）
            detector: 唤醒检测器实例（用于保存前校验词库）
        """
        # 父窗口（tkinter root）
        self._parent = parent
        # 唤醒检测器（词库校验用）
        self._detector = detector
        # 对话框引用
        self._top: Optional[tk.Toplevel] = None
        # 是否已保存
        self._saved = False
        # 启用开关变量
        self._enabled_var = tk.BooleanVar(value=True)
        # 唤醒词输入变量（可编辑下拉框）
        self._word_var = tk.StringVar()
        # 结束词启用开关变量
        self._stop_enabled_var = tk.BooleanVar(value=False)
        # 结束词输入变量（可编辑下拉框）
        self._stop_word_var = tk.StringVar()

    def show(self) -> bool:
        """显示对话框，返回是否保存成功。

        关键：Windows 下 tkinter 根窗口被 withdraw() 后，
        子 Toplevel 对话框可能无法显示。因此创建对话框前，
        先确保根窗口处于可见状态。

        Returns:
            True 表示用户保存了设置，False 表示取消或关闭。
        """
        # 预填现有设置
        existing = config_module.load()
        self._enabled_var.set(existing.wake_word_enabled)
        word = existing.wake_word or WAKE_WORD
        self._word_var.set(word)
        self._stop_enabled_var.set(existing.stop_word_enabled)
        self._stop_word_var.set(existing.stop_word or STOP_WORD)

        # 关键：确保根窗口可见，否则对话框在 Windows 上可能不显示
        try:
            if self._parent.state() == "withdrawn":
                self._parent.deiconify()
                self._parent.geometry("1x1+0+0")
                self._parent.overrideredirect(True)
                self._parent.attributes("-topmost", True)
        except Exception:
            pass

        self._top = tk.Toplevel(self._parent)
        self._top.title("语音唤醒设置")
        self._top.geometry("460x560")
        self._top.resizable(False, False)
        # 模态
        self._top.transient(self._parent)
        self._top.grab_set()

        # 构建 UI
        self._build_ui(word)

        # 置顶并聚焦
        self._top.lift()
        self._top.focus_force()

        # 阻塞等待对话框关闭
        self._parent.wait_window(self._top)

        return self._saved

    def _build_ui(self, current_word: str) -> None:
        """构造对话框 UI 控件。

        Args:
            current_word: 当前唤醒词（确保下拉框预设列表包含它）
        """
        pad = {"padx": 14, "pady": 4}

        # 标题
        ttk.Label(
            self._top,
            text="语音唤醒设置",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", padx=14, pady=(14, 4))

        # 启用开关
        ttk.Checkbutton(
            self._top,
            text="启用语音唤醒（空闲时说出唤醒词自动开始录音）",
            variable=self._enabled_var,
        ).pack(anchor="w", **pad)

        # 唤醒词选择/输入行
        word_frame = ttk.Frame(self._top)
        word_frame.pack(fill="x", **pad)
        ttk.Label(word_frame, text="唤醒词:", width=10).pack(side="left")
        # 可编辑下拉框：既可从预设选择，也可直接输入自定义词
        presets = list(_WAKE_PRESETS)
        if current_word and current_word not in presets:
            presets.insert(0, current_word)
        combo = ttk.Combobox(
            word_frame,
            textvariable=self._word_var,
            values=presets,
            width=22,
        )
        combo.pack(side="left", fill="x", expand=True)

        # ===== 语音结束词设置 =====
        ttk.Separator(self._top, orient="horizontal").pack(fill="x", padx=14, pady=6)
        ttk.Checkbutton(
            self._top,
            text="启用语音结束词（录音中说出即自动停止录音）",
            variable=self._stop_enabled_var,
        ).pack(anchor="w", **pad)

        # 结束词选择/输入行
        stop_frame = ttk.Frame(self._top)
        stop_frame.pack(fill="x", **pad)
        ttk.Label(stop_frame, text="结束词:", width=10).pack(side="left")
        stop_current = self._stop_word_var.get()
        stop_presets = list(_STOP_PRESETS)
        if stop_current and stop_current not in stop_presets:
            stop_presets.insert(0, stop_current)
        stop_combo = ttk.Combobox(
            stop_frame,
            textvariable=self._stop_word_var,
            values=stop_presets,
            width=22,
        )
        stop_combo.pack(side="left", fill="x", expand=True)

        # 使用说明
        ttk.Label(
            self._top,
            text="使用说明:",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w", padx=14, pady=(8, 0))

        guide = (
            "· 空闲时说出唤醒词即开始录音；停止仍按 Ctrl + Alt + K\n"
            "· 启用结束词后，录音中说出结束词（如「结束录音」）即自动\n"
            "  停止并识别，结束词本身不会出现在识别结果里\n"
            "· 唤醒词与结束词需为 2~6 个常用汉字，推荐叠词或四字短语\n"
            "· 保存时自动校验离线词库，含生僻字的词可能无法生效\n"
            "· 唤醒/结束检测完全离线进行，不会上传任何声音\n"
            "· 保存后立即生效，无需重启工具"
        )
        guide_text = tk.Text(
            self._top,
            height=7,
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            background="#f5f5f5",
            relief="flat",
            borderwidth=0,
            state="disabled",
        )
        guide_text.pack(fill="both", expand=True, padx=14, pady=(2, 8))
        guide_text.config(state="normal")
        guide_text.insert("1.0", guide)
        guide_text.config(state="disabled")

        # 按钮区
        btn_frame = ttk.Frame(self._top)
        btn_frame.pack(side="bottom", fill="x", pady=12, padx=14)
        ttk.Button(btn_frame, text="取消", command=self._on_cancel).pack(
            side="right", padx=8
        )
        ttk.Button(btn_frame, text="保存", command=self._on_save).pack(side="right", padx=8)

    def _on_save(self) -> None:
        """保存按钮回调：校验唤醒词/结束词格式与词库后写入配置。"""
        word = self._word_var.get().strip()
        enabled = self._enabled_var.get()
        stop_word = self._stop_word_var.get().strip()
        stop_enabled = self._stop_enabled_var.get()

        # 开启唤醒时才强制校验唤醒词格式（关闭状态下允许保留任意旧值）
        if enabled:
            if not self._check_word(word, "唤醒词"):
                return
        # 启用结束词时校验结束词格式与词库
        if stop_enabled:
            if not self._check_word(stop_word, "结束词"):
                return

        # 写入配置文件
        try:
            config_module.set_wake_settings(enabled, word, stop_enabled, stop_word)
            self._saved = True
            DiagLog.shared().write(
                f"[Dialog] 语音唤醒设置已保存（启用={enabled}, 唤醒词={word or '小薇小薇'}"
                f", 结束词启用={stop_enabled}, 结束词={stop_word or '结束录音'}）"
            )
        except Exception as e:
            DiagLog.shared().write(f"[Dialog] 语音唤醒设置保存失败: {e}")
            return

        # 关闭对话框
        if self._top is not None:
            self._top.destroy()

    def _check_word(self, word: str, label: str) -> bool:
        """校验唤醒词/结束词的格式与词库，不通过时弹窗提示。

        Args:
            word: 待校验的词（非空）
            label: 显示用名称（「唤醒词」/「结束词」）

        Returns:
            True 表示校验通过（或用户确认仍要保存）
        """
        if not word:
            messagebox.showwarning(
                f"{label}为空", f"已启用语音{label}，请填写{label}（2~6 个汉字）",
                parent=self._top,
            )
            return False
        if not _WAKE_WORD_RE.match(word):
            messagebox.showwarning(
                f"{label}格式不正确",
                f"{label}需为 2~6 个汉字，例如：小爱小爱、你好小智",
                parent=self._top,
            )
            return False
        # 词库校验：缺失字会导致永远无法命中，需用户确认
        missing = self._detector.validate_wake_word(word)
        if missing:
            proceed = messagebox.askyesno(
                f"{label}含生僻字",
                f"以下字不在离线识别词库中，{label}可能无法生效：\n\n"
                f"{'、'.join(missing)}\n\n"
                "建议换用常用汉字。仍要保存吗？",
                parent=self._top,
            )
            if not proceed:
                return False
            DiagLog.shared().write(
                f"[Dialog] {label}含缺失字 {'、'.join(missing)}，用户确认保存"
            )
        return True

    def _on_cancel(self) -> None:
        """取消按钮回调：直接关闭对话框。"""
        if self._top is not None:
            self._top.destroy()
