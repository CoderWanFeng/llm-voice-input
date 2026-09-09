"""AI 润色设置对话框模块。

提供大模型后处理（AI 润色）的可视化配置界面（与凭证对话框同风格）：
- 启用/禁用 AI 润色（勾选框）
- 接口地址（OpenAI 兼容服务，默认阿里云百炼 DashScope）
- 模型名：可编辑下拉框，既可从预设选择，也可手动输入任意模型名
  （例如 qwen3.7-plus）
- API Key：密码形式输入
- 保存时校验：启用状态下 API Key 与模型名必填

保存后写入配置文件，下次识别完成时生效，无需重启。

必须在 tkinter 主线程调度。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Optional

from . import config as config_module
from .diag_log import DiagLog


# 预设模型名（可编辑下拉框选项；也支持手动输入任意模型名）
_MODEL_PRESETS: List[str] = [
    "qwen3.7-plus",
    "qwen-plus",
    "qwen-max",
    "qwen-turbo",
    "deepseek-chat",
    "glm-4-plus",
    "gpt-4o",
    "gpt-4o-mini",
]


class LLMSettingsDialog:
    """AI 润色设置对话框，模态弹出。

    用户配置 OpenAI 兼容的大模型服务（接口地址 / API Key / 模型名），
    启用后识别结果会先经大模型纠错与按要点换行，再注入光标处。
    """

    def __init__(self, parent: tk.Tk) -> None:
        # 父窗口（tkinter root）
        self._parent = parent
        # 对话框引用
        self._top: Optional[tk.Toplevel] = None
        # 是否已保存
        self._saved = False
        # 启用开关变量
        self._enabled_var = tk.BooleanVar(value=False)
        # 接口地址输入变量
        self._base_url_var = tk.StringVar()
        # 模型名输入变量（可编辑下拉框）
        self._model_var = tk.StringVar()
        # API Key 输入变量
        self._api_key_var = tk.StringVar()

    def show(self) -> bool:
        """显示对话框，返回是否保存成功。

        关键：Windows 下 tkinter 根窗口被 withdraw() 后，
        子 Toplevel 对话框可能无法显示。因此创建对话框前，
        先确保根窗口处于可见状态。

        Returns:
            True 表示用户保存了设置，False 表示取消或关闭。
        """
        # 预填现有设置（空值时给默认地址，方便首次配置）
        existing = config_module.load()
        self._enabled_var.set(existing.llm_enabled)
        self._base_url_var.set(existing.llm_base_url or config_module.LLM_DEFAULT_BASE_URL)
        self._model_var.set(existing.llm_model or config_module.LLM_DEFAULT_MODEL)
        self._api_key_var.set(existing.llm_api_key)

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
        self._top.title("AI 润色设置")
        self._top.geometry("520x560")
        self._top.resizable(False, False)
        # 模态
        self._top.transient(self._parent)
        self._top.grab_set()

        # 构建 UI
        self._build_ui()

        # 置顶并聚焦
        self._top.lift()
        self._top.focus_force()

        # 阻塞等待对话框关闭
        self._parent.wait_window(self._top)

        return self._saved

    def _build_ui(self) -> None:
        """构造对话框 UI 控件。"""
        pad = {"padx": 14, "pady": 4}

        # 标题
        ttk.Label(
            self._top,
            text="AI 润色设置",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", padx=14, pady=(14, 4))

        # 功能说明
        ttk.Label(
            self._top,
            text="识别完成后调用大模型纠错（专有名词/口误），并按要点自动换行",
            font=("Microsoft YaHei UI", 9),
            foreground="#666666",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        # 启用开关
        ttk.Checkbutton(
            self._top,
            text="启用 AI 润色",
            variable=self._enabled_var,
        ).pack(anchor="w", **pad)

        # 接口地址行
        url_frame = ttk.Frame(self._top)
        url_frame.pack(fill="x", **pad)
        ttk.Label(url_frame, text="接口地址:", width=10).pack(side="left")
        url_entry = ttk.Entry(url_frame, textvariable=self._base_url_var, width=42)
        url_entry.pack(side="left", fill="x", expand=True)

        # 模型名行：可编辑下拉框——下拉选择预设，也可直接输入任意模型名
        model_frame = ttk.Frame(self._top)
        model_frame.pack(fill="x", **pad)
        ttk.Label(model_frame, text="模型名:", width=10).pack(side="left")
        model_presets = list(_MODEL_PRESETS)
        current_model = self._model_var.get()
        # 当前已存的模型名不在预设里时插入到最前，保证回显可见
        if current_model and current_model not in model_presets:
            model_presets.insert(0, current_model)
        model_combo = ttk.Combobox(
            model_frame,
            textvariable=self._model_var,
            values=model_presets,
            width=32,
        )
        model_combo.pack(side="left", fill="x", expand=True)

        # API Key 行：密码形式显示
        key_frame = ttk.Frame(self._top)
        key_frame.pack(fill="x", **pad)
        ttk.Label(key_frame, text="API Key:", width=10).pack(side="left")
        key_entry = ttk.Entry(key_frame, textvariable=self._api_key_var, width=42, show="*")
        key_entry.pack(side="left", fill="x", expand=True)

        # 配置说明
        ttk.Label(
            self._top,
            text="获取 API Key 与接口地址:",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w", padx=14, pady=(8, 0))

        guide = (
            "· 任何兼容 OpenAI 接口的大模型服务均可使用\n"
            "· 阿里云百炼：bailian.console.aliyun.com 开通后创建 API Key\n"
            "  （sk- 开头），接口地址填默认值即可\n"
            "· DeepSeek：platform.deepseek.com 创建 Key，接口地址改为\n"
            "  https://api.deepseek.com/v1\n"
            "· 模型名可下拉选择，也可手动输入任意名称（如 qwen3.7-plus）\n"
            "· 启用后每次识别完成会调用一次大模型，出字稍慢 1~3 秒\n"
            "· 调用失败或超时会自动回退显示原始识别文本，不影响使用"
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
        """保存按钮回调：校验必填项后写入配置。

        校验逻辑：启用状态下 API Key 必填（Key 是调用大模型的必要凭证）；
        模型名与接口地址为空时自动回退默认值（见 set_llm_settings）。
        """
        enabled = self._enabled_var.get()
        base_url = self._base_url_var.get().strip()
        api_key = self._api_key_var.get().strip()
        model = self._model_var.get().strip()

        # 启用时校验 API Key 与模型名
        if enabled:
            if not api_key:
                messagebox.showwarning(
                    "API Key 为空",
                    "已启用 AI 润色，请填写 API Key",
                    parent=self._top,
                )
                return
            if not model:
                messagebox.showwarning(
                    "模型名为空",
                    "已启用 AI 润色，请填写模型名（可下拉选择或手动输入）",
                    parent=self._top,
                )
                return
        else:
            # 未勾选启用但已填了 API Key：弹确认避免用户以为已生效
            if api_key:
                proceed = messagebox.askyesno(
                    "AI 润色未启用",
                    "你已填写 API Key，但未勾选「启用 AI 润色」。\n"
                    "保存后识别结果将不经大模型纠错，直接注入原文。\n\n"
                    "要启用 AI 润色吗？",
                    parent=self._top,
                )
                if proceed:
                    # 用户确认启用：自动勾选
                    self._enabled_var.set(True)
                    enabled = True
                else:
                    # 用户确认不启用：继续保存为关闭状态
                    pass

        # 写入配置文件
        try:
            cfg = config_module.set_llm_settings(enabled, base_url, api_key, model)
            self._saved = True
            DiagLog.shared().write(
                f"[Dialog] AI 润色设置已保存（启用={enabled}, 模型={cfg.llm_model}）"
            )
        except Exception as e:
            DiagLog.shared().write(f"[Dialog] AI 润色设置保存失败: {e}")
            return

        # 关闭对话框
        if self._top is not None:
            self._top.destroy()

    def _on_cancel(self) -> None:
        """取消按钮回调：直接关闭对话框。"""
        if self._top is not None:
            self._top.destroy()
