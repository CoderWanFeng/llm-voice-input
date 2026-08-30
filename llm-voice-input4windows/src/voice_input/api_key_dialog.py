"""凭证配置对话框模块。

支持多 ASR 提供商选择，动态显示对应凭证输入字段：
- 下拉框选择提供商
- 根据所选提供商显示对应的 app_id / token 输入框
- 界面下方显示该厂商凭证获取步骤（随下拉框切换）
- 保存时按提供商分别存储凭证

必须在 tkinter 主线程调度。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from . import config as config_module
from .diag_log import DiagLog


# 提供商凭证字段定义：每个提供商需要输入的字段列表
# key: 凭证字段名, label: 显示标签, show: 是否以密码形式显示
_PROVIDER_FIELDS: Dict[str, List[Dict[str, str]]] = {
    "volc": [
        {"key": "app_id", "label": "APP ID / APP Key:", "show": ""},
        {"key": "access_token", "label": "Access Token (可选):", "show": "*"},
    ],
    "xfly": [
        {"key": "app_id", "label": "APP ID:", "show": ""},
        {"key": "app_key", "label": "APP Key:", "show": "*"},
    ],
    "tencent": [
        {"key": "app_id", "label": "App ID:", "show": ""},
        {"key": "secret_id", "label": "Secret ID:", "show": ""},
        {"key": "secret_key", "label": "Secret Key:", "show": "*"},
    ],
    "aliyun": [
        {"key": "app_key", "label": "项目 App Key:", "show": ""},
        {"key": "access_key_id", "label": "Access Key ID:", "show": ""},
        {"key": "access_key_secret", "label": "Access Key Secret:", "show": "*"},
    ],
}


class APIKeyDialog:
    """凭证配置对话框，模态弹出。

    用户通过下拉框选择 ASR 提供商，然后填写对应的凭证字段。
    不同提供商的凭证字段名不同，对话框会动态调整显示。
    """

    def __init__(self, parent: tk.Tk) -> None:
        # 父窗口（tkinter root）
        self._parent = parent
        # 对话框引用
        self._top: Optional[tk.Toplevel] = None
        # 是否已保存
        self._saved = False
        # 提供商选择变量
        self._provider_var = tk.StringVar()
        # 凭证输入变量字典（key=字段名）
        self._field_vars: Dict[str, tk.StringVar] = {}
        # 凭证字段容器（用于动态刷新）
        self._fields_frame: Optional[ttk.Frame] = None
        # 获取步骤说明文本框（随提供商切换更新）
        self._guide_text: Optional[tk.Text] = None
        # 提供商变化回调绑定ID
        self._provider_trace_id: Optional[str] = None

    def show(self) -> bool:
        """显示对话框，返回是否保存成功。

        关键：Windows 下 tkinter 根窗口被 withdraw() 后，
        子 Toplevel 对话框可能无法显示。
        因此创建对话框前，先确保根窗口处于可见状态。

        Returns:
            True 表示用户保存了凭证，False 表示取消或关闭。
        """
        # 预填现有配置
        existing = config_module.load()
        self._provider_var.set(
            config_module.PROVIDER_NAMES.get(existing.provider, existing.provider)
        )

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
        self._top.title("设置语音识别凭证")
        # 对话框需容纳下方多行获取步骤说明，因此高度加大
        self._top.geometry("560x600")
        self._top.resizable(False, False)
        # 模态
        self._top.transient(self._parent)
        self._top.grab_set()

        # 绑定提供商变化事件
        self._provider_trace_id = self._provider_var.trace_add(
            "write", self._on_provider_changed
        )

        # 构建 UI
        self._build_ui(existing)

        # 置顶并聚焦
        self._top.lift()
        self._top.focus_force()

        # 阻塞等待对话框关闭
        self._parent.wait_window(self._top)

        # 解除 trace
        if self._provider_trace_id:
            self._provider_var.trace_remove("write", self._provider_trace_id)

        return self._saved

    def _build_ui(self, existing: config_module.AppConfig) -> None:
        """构造对话框 UI 控件。

        Args:
            existing: 当前已有配置，用于预填凭证
        """
        pad = {"padx": 12, "pady": 4}

        # 标题
        title_label = ttk.Label(
            self._top,
            text="语音识别服务设置",
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        title_label.pack(anchor="w", padx=12, pady=(12, 4))

        # 提供商选择
        provider_frame = ttk.Frame(self._top)
        provider_frame.pack(fill="x", **pad)
        ttk.Label(provider_frame, text="ASR 提供商:", width=16).pack(side="left")

        # 下拉框显示中文名，绑定到 provider id
        display_names = list(config_module.PROVIDER_NAMES.values())
        self._provider_combo = ttk.Combobox(
            provider_frame,
            textvariable=self._provider_var,
            values=display_names,
            state="readonly",
            width=30,
        )
        self._provider_combo.pack(side="left", fill="x", expand=True)

        # 分隔线
        ttk.Separator(self._top, orient="horizontal").pack(fill="x", padx=12, pady=8)

        # 凭证字段容器（动态内容）
        self._fields_frame = ttk.Frame(self._top)
        self._fields_frame.pack(fill="x", **pad)

        # 获取凭证步骤说明（随提供商切换更新）
        guide_caption = ttk.Label(
            self._top,
            text="获取凭证步骤:",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        guide_caption.pack(anchor="w", padx=12, pady=(4, 0))

        # 只读文本框展示多行步骤，浅灰底色提示这是说明区域
        self._guide_text = tk.Text(
            self._top,
            height=12,
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            background="#f5f5f5",
            relief="flat",
            borderwidth=0,
            state="disabled",
        )
        self._guide_text.pack(fill="both", expand=True, padx=12, pady=(2, 8))

        # 按钮区
        btn_frame = ttk.Frame(self._top)
        btn_frame.pack(side="bottom", fill="x", pady=12, padx=12)
        ttk.Button(btn_frame, text="取消", command=self._on_cancel).pack(
            side="right", padx=8
        )
        ttk.Button(btn_frame, text="保存", command=self._on_save).pack(side="right", padx=8)

        # 初始化凭证字段
        self._refresh_fields(existing)

    def _get_provider_id(self) -> str:
        """根据下拉框选中的中文名获取提供商ID。

        Returns:
            提供商ID（如 volc, xfly 等）
        """
        display_name = self._provider_var.get()
        # 反查映射
        for pid, pname in config_module.PROVIDER_NAMES.items():
            if pname == display_name:
                return pid
        return "volc"

    def _get_provider_guide(self, provider_id: str) -> str:
        """获取指定厂商凭证的详细获取步骤。

        Args:
            provider_id: 提供商ID

        Returns:
            多行步骤说明文本
        """
        guides: Dict[str, str] = {
            "volc": (
                "1. 登录火山引擎控制台，进入「语音技术-流式语音识别」\n"
                "2. 开通服务（新用户可领取免费试用额度）\n"
                "3. 进入「语音技术-应用管理」，点击「创建应用」\n"
                "4. 勾选已开通的流式语音识别服务，创建后记录 APP ID\n"
                "5. 在应用详情中复制 Access Token（可选）"
            ),
            "xfly": (
                "1. 打开 https://www.xfyun.cn 注册并完成实名认证\n"
                "2. 进入控制台，点击「创建新应用」，记录 APP ID\n"
                "3. 在左侧「语音识别」中选择「实时语音转写」\n"
                "4. 领取免费试用包或购买后，进入该服务管理页\n"
                "5. 复制页面上的 APP Key，与本页 APP ID 一同填写\n"
                "6. 无需配置 IP 白名单"
            ),
            "tencent": (
                "1. 打开 https://console.cloud.tencent.com/asr\n"
                "   开通「实时语音识别」服务\n"
                "2. 访问账号信息页 console.cloud.tencent.com/developer\n"
                "   记录 APP ID\n"
                "3. 打开 API 密钥管理 console.cloud.tencent.com/cam/capi\n"
                "4. 点击「新建密钥」，复制 SecretId 与 SecretKey\n"
                "5. 将三项凭证填入本窗口对应输入框"
            ),
            "aliyun": (
                "1. 打开 https://nls-portal.console.aliyun.com/\n"
                "   开通智能语音交互服务\n"
                "2. 在「全部项目」中创建项目，记录项目 App Key\n"
                "   （注意：是项目 App Key，不是 RAM 子用户名）\n"
                "3. 打开 RAM 控制台 https://ram.console.aliyun.com/users\n"
                "4. 创建子用户并授予 AliyunNLSFullAccess 权限\n"
                "5. 在子用户「认证管理-AccessKey」处创建密钥\n"
                "6. 复制 AccessKey ID 与 AccessKey Secret\n"
                "7. 建议使用 RAM 子用户，不要使用主账号 AccessKey"
            ),
        }
        return guides.get(provider_id, "")

    def _refresh_fields(self, existing: Optional[config_module.AppConfig] = None) -> None:
        """根据当前选中的提供商刷新凭证输入字段。

        Args:
            existing: 当前已有配置，用于预填现有凭证值
        """
        provider_id = self._get_provider_id()
        fields = _PROVIDER_FIELDS.get(provider_id, [])

        # 清空旧字段
        if self._fields_frame is not None:
            for widget in self._fields_frame.winfo_children():
                widget.destroy()

        # 清空变量
        self._field_vars = {}

        # 预填当前提供商的已有凭证
        existing_creds: Dict[str, str] = {}
        if existing is not None:
            existing_creds = existing.providers.get(provider_id, {})

        # 创建新字段
        for i, field_def in enumerate(fields):
            key = field_def["key"]
            label = field_def["label"]
            show = field_def["show"]

            # 字段行
            row_frame = ttk.Frame(self._fields_frame)
            row_frame.pack(fill="x", pady=4)

            ttk.Label(row_frame, text=label, width=20).pack(side="left")

            var = tk.StringVar(value=existing_creds.get(key, ""))
            self._field_vars[key] = var

            entry = ttk.Entry(row_frame, textvariable=var, width=35, show=show or "")
            entry.pack(side="left", fill="x", expand=True)

        # 更新获取步骤说明（Text 控件需临时恢复 normal 状态才能写入）
        if self._guide_text is not None:
            self._guide_text.config(state="normal")
            self._guide_text.delete("1.0", "end")
            self._guide_text.insert("1.0", self._get_provider_guide(provider_id))
            self._guide_text.config(state="disabled")

    def _on_provider_changed(self, *args: str) -> None:
        """提供商下拉框值变化回调，刷新凭证字段。

        切换厂商时需重新加载该厂商已存的凭证并回显到输入框，
        否则切回已配置的厂商会显示空，用户误以为要重新配置。
        load() 返回的 providers 包含所有已配置厂商的凭证，
        _refresh_fields 据此回显目标厂商已存值。
        """
        self._refresh_fields(config_module.load())

    def _on_save(self) -> None:
        """保存按钮回调：验证并保存凭证。

        验证逻辑：第一个字段（通常是 app_id / api_key）为必填项。
        """
        provider_id = self._get_provider_id()
        fields = _PROVIDER_FIELDS.get(provider_id, [])

        # 收集凭证值
        credentials: Dict[str, str] = {}
        for field_def in fields:
            key = field_def["key"]
            value = self._field_vars.get(key).get().strip() if key in self._field_vars else ""
            credentials[key] = value

        # 验证第一个必填字段
        first_key = fields[0]["key"] if fields else ""
        if not credentials.get(first_key):
            DiagLog.shared().write("[Dialog] 保存失败：必填项为空")
            return

        # 保存到配置
        try:
            config_module.update_provider_credentials(provider_id, credentials)
            self._saved = True
            DiagLog.shared().write(f"[Dialog] 凭证已保存（提供商: {provider_id}）")
        except Exception as e:
            DiagLog.shared().write(f"[Dialog] 凭证保存失败: {e}")
            return

        # 关闭对话框
        if self._top is not None:
            self._top.destroy()

    def _on_cancel(self) -> None:
        """取消按钮回调：直接关闭对话框。"""
        if self._top is not None:
            self._top.destroy()
