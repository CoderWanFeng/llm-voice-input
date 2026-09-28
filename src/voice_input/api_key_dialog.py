"""凭证配置对话框模块（PySide6 版）。

支持多 ASR 提供商选择，动态显示对应凭证输入字段：
- 下拉框选择提供商
- 根据所选提供商显示对应的 app_id / token 输入框
- 界面下方显示该厂商凭证获取步骤（随下拉框切换）
- 保存时按提供商分别存储凭证

必须在 Qt 主线程调度（通过 schedule_on_main）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

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

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        # 父窗口（可为 None，QDialog 会自动处理）
        self._parent = parent
        # 对话框引用
        self._dlg: Optional[QDialog] = None
        # 是否已保存
        self._saved = False
        # 凭证字段输入框字典（key=字段名 → QLineEdit）
        self._field_edits: Dict[str, QLineEdit] = {}
        # 凭证字段容器（用于动态刷新）
        self._fields_container: Optional[QWidget] = None
        # 凭证字段容器布局（清空/添加字段用）
        self._fields_layout: Optional[QVBoxLayout] = None
        # 获取步骤说明文本框（随提供商切换更新）
        self._guide_text: Optional[QTextEdit] = None
        # 提供商下拉框
        self._provider_combo: Optional[QComboBox] = None

    def show(self) -> bool:
        """显示对话框，返回是否保存成功。

        QDialog.exec() 是模态阻塞的，等价于 tkinter 的 wait_window。

        Returns:
            True 表示用户保存了凭证，False 表示取消或关闭。
        """
        existing = config_module.load()

        # 创建模态对话框
        self._dlg = QDialog(self._parent)
        self._dlg.setWindowTitle("设置语音识别凭证")
        self._dlg.setFixedSize(560, 600)

        self._build_ui(existing)

        # exec() 模态阻塞，返回后 _saved 已被槽函数设置
        self._dlg.exec()
        return self._saved

    def _build_ui(self, existing: config_module.AppConfig) -> None:
        """构造对话框 UI 控件。

        Args:
            existing: 当前已有配置，用于预填凭证
        """
        layout = QVBoxLayout(self._dlg)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        # 标题
        title = QLabel("语音识别服务设置")
        title.setStyleSheet("font-family: 'Microsoft YaHei UI'; font-size: 12pt; font-weight: bold;")
        layout.addWidget(title)

        # 提供商选择行
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("ASR 提供商:"))
        self._provider_combo = QComboBox()
        # readonly 行为：setEditable(False)
        self._provider_combo.setEditable(False)
        # 填入中文名
        for pid, pname in config_module.PROVIDER_NAMES.items():
            self._provider_combo.addItem(pname, userData=pid)
        # 默认选中当前 provider
        idx = self._provider_combo.findData(existing.provider)
        if idx >= 0:
            self._provider_combo.setCurrentIndex(idx)
        # 切换信号
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self._provider_combo, 1)
        layout.addLayout(provider_row)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        # 凭证字段容器
        self._fields_container = QWidget()
        self._fields_layout = QVBoxLayout(self._fields_container)
        self._fields_layout.setContentsMargins(0, 0, 0, 0)
        self._fields_layout.setSpacing(4)
        layout.addWidget(self._fields_container)

        # 获取凭证步骤说明
        guide_caption = QLabel("获取凭证步骤:")
        guide_caption.setStyleSheet("font-family: 'Microsoft YaHei UI'; font-size: 9pt; font-weight: bold;")
        layout.addWidget(guide_caption)

        self._guide_text = QTextEdit()
        self._guide_text.setReadOnly(True)
        self._guide_text.setStyleSheet("background-color: #f5f5f5; border: none; font-family: 'Microsoft YaHei UI'; font-size: 9pt;")
        layout.addWidget(self._guide_text, 1)

        # 按钮区
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self._on_cancel)
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._on_save)
        save_btn.setDefault(True)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        # 初始化凭证字段
        self._refresh_fields(existing)

    def _refresh_fields(self, existing: Optional[config_module.AppConfig] = None) -> None:
        """根据当前选中的提供商刷新凭证输入字段。

        Args:
            existing: 当前已有配置，用于预填现有凭证值
        """
        provider_id = self._get_provider_id()
        fields = _PROVIDER_FIELDS.get(provider_id, [])

        # 清空旧字段
        while self._fields_layout.count():
            item = self._fields_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # 清空引用
        self._field_edits = {}

        # 预填当前提供商的已有凭证
        existing_creds: Dict[str, str] = {}
        if existing is not None:
            existing_creds = existing.providers.get(provider_id, {})

        # 创建新字段
        for field_def in fields:
            key = field_def["key"]
            label = field_def["label"]
            show = field_def["show"]

            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(160)
            edit = QLineEdit()
            edit.setText(existing_creds.get(key, ""))
            if show == "*":
                edit.setEchoMode(QLineEdit.Password)
            row.addWidget(lbl)
            row.addWidget(edit, 1)
            self._field_edits[key] = edit

            # 用 wrapper widget 包裹 QHBoxLayout
            wrapper = QWidget()
            wrapper.setLayout(row)
            self._fields_layout.addWidget(wrapper)

        # 更新获取步骤说明
        if self._guide_text is not None:
            self._guide_text.setPlainText(self._get_provider_guide(provider_id))

    def _on_provider_changed(self, _index: int) -> None:
        """提供商下拉框值变化回调，刷新凭证字段。

        切换厂商时需重新加载该厂商已存的凭证并回显到输入框，
        否则切回已配置的厂商会显示空，用户误以为要重新配置。
        load() 返回的 providers 包含所有已配置厂商的凭证，
        _refresh_fields 据此回显目标厂商已存值。
        """
        self._refresh_fields(config_module.load())

    def _get_provider_id(self) -> str:
        """根据下拉框当前选中的 userData 获取提供商ID。

        Returns:
            提供商ID（如 volc, xfly 等）
        """
        if self._provider_combo is None:
            return "volc"
        data = self._provider_combo.currentData()
        return data if data else "volc"

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
            edit = self._field_edits.get(key)
            value = edit.text().strip() if edit is not None else ""
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
        if self._dlg is not None:
            self._dlg.accept()

    def _on_cancel(self) -> None:
        """取消按钮回调：直接关闭对话框。"""
        if self._dlg is not None:
            self._dlg.reject()
