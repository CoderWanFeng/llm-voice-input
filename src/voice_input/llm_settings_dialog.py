"""AI 润色设置对话框模块（PySide6 版）。

提供大模型后处理（AI 润色）的可视化配置界面（与凭证对话框同风格）：
- 启用/禁用 AI 润色（勾选框）
- 接口地址（OpenAI 兼容服务，默认阿里云百炼 DashScope）
- 模型名：可编辑下拉框，既可从预设选择，也可手动输入任意模型名
  （例如 qwen3.7-plus）
- API Key：密码形式输入
- 保存时校验：启用状态下 API Key 与模型名必填

保存后写入配置文件，下次识别完成时生效，无需重启。

必须在 Qt 主线程调度。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

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

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        # 父窗口
        self._parent = parent
        # 对话框引用
        self._dlg: Optional[QDialog] = None
        # 是否已保存
        self._saved = False
        # 启用开关
        self._enabled_chk: Optional[QCheckBox] = None
        # 接口地址输入框
        self._base_url_edit: Optional[QLineEdit] = None
        # 模型名可编辑下拉框
        self._model_combo: Optional[QComboBox] = None
        # API Key 输入框（密码模式）
        self._api_key_edit: Optional[QLineEdit] = None

    def show(self) -> bool:
        """显示对话框，返回是否保存成功。

        QDialog.exec() 是模态阻塞的，等价于 tkinter 的 wait_window。

        Returns:
            True 表示用户保存了设置，False 表示取消或关闭。
        """
        existing = config_module.load()

        # 创建模态对话框
        self._dlg = QDialog(self._parent)
        self._dlg.setWindowTitle("AI 润色设置")
        self._dlg.setFixedSize(520, 560)

        self._build_ui(existing)

        # exec() 模态阻塞
        self._dlg.exec()
        return self._saved

    def _build_ui(self, existing: config_module.AppConfig) -> None:
        """构造对话框 UI 控件。

        Args:
            existing: 当前已有配置，用于预填值
        """
        layout = QVBoxLayout(self._dlg)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)

        # 标题
        title = QLabel("AI 润色设置")
        title.setStyleSheet("font-family: 'Microsoft YaHei UI'; font-size: 12pt; font-weight: bold;")
        layout.addWidget(title)

        # 功能说明
        desc = QLabel("识别完成后调用大模型纠错（专有名词/口误），并按要点自动换行")
        desc.setStyleSheet("font-family: 'Microsoft YaHei UI'; font-size: 9pt; color: #666666;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 启用开关
        self._enabled_chk = QCheckBox("启用 AI 润色")
        self._enabled_chk.setChecked(existing.llm_enabled)
        layout.addWidget(self._enabled_chk)

        # 接口地址行
        url_row = QHBoxLayout()
        url_lbl = QLabel("接口地址:")
        url_lbl.setFixedWidth(80)
        url_row.addWidget(url_lbl)
        self._base_url_edit = QLineEdit()
        self._base_url_edit.setText(existing.llm_base_url or config_module.LLM_DEFAULT_BASE_URL)
        url_row.addWidget(self._base_url_edit, 1)
        layout.addLayout(url_row)

        # 模型名行（可编辑下拉框）
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("模型名:"))
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)  # 可输入任意模型名
        presets = list(_MODEL_PRESETS)
        current_model = existing.llm_model or config_module.LLM_DEFAULT_MODEL
        if current_model and current_model not in presets:
            presets.insert(0, current_model)
        for p in presets:
            self._model_combo.addItem(p)
        # 默认显示当前模型
        idx = self._model_combo.findText(current_model)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        else:
            self._model_combo.setEditText(current_model)
        model_row.addWidget(self._model_combo, 1)
        layout.addLayout(model_row)

        # API Key 行（密码形式显示）
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API Key:"))
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.Password)
        self._api_key_edit.setText(existing.llm_api_key)
        key_row.addWidget(self._api_key_edit, 1)
        layout.addLayout(key_row)

        # 配置说明标题
        guide_caption = QLabel("获取 API Key 与接口地址:")
        guide_caption.setStyleSheet("font-family: 'Microsoft YaHei UI'; font-size: 9pt; font-weight: bold;")
        layout.addWidget(guide_caption)

        # 配置说明文本（只读）
        guide_text = QTextEdit()
        guide_text.setReadOnly(True)
        guide_text.setStyleSheet("background-color: #f5f5f5; border: none; font-family: 'Microsoft YaHei UI'; font-size: 9pt;")
        guide_text.setPlainText(
            "· 任何兼容 OpenAI 接口的大模型服务均可使用\n"
            "· 阿里云百炼：bailian.console.aliyun.com 开通后创建 API Key\n"
            "  （sk- 开头），接口地址填默认值即可\n"
            "· DeepSeek：platform.deepseek.com 创建 Key，接口地址改为\n"
            "  https://api.deepseek.com/v1\n"
            "· 模型名可下拉选择，也可手动输入任意名称（如 qwen3.7-plus）\n"
            "· 启用后每次识别完成会调用一次大模型，出字稍慢 1~3 秒\n"
            "· 调用失败或超时会自动回退显示原始识别文本，不影响使用"
        )
        layout.addWidget(guide_text, 1)

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

    def _on_save(self) -> None:
        """保存按钮回调：校验必填项后写入配置。

        校验逻辑：启用状态下 API Key 必填（Key 是调用大模型的必要凭证）；
        模型名与接口地址为空时自动回退默认值（见 set_llm_settings）。
        """
        enabled = self._enabled_chk.isChecked() if self._enabled_chk else False
        base_url = self._base_url_edit.text().strip() if self._base_url_edit else ""
        api_key = self._api_key_edit.text().strip() if self._api_key_edit else ""
        model = self._model_combo.currentText().strip() if self._model_combo else ""

        # 启用时校验 API Key 与模型名
        if enabled:
            if not api_key:
                QMessageBox.warning(self._dlg, "API Key 为空", "已启用 AI 润色，请填写 API Key")
                return
            if not model:
                QMessageBox.warning(
                    self._dlg,
                    "模型名为空",
                    "已启用 AI 润色，请填写模型名（可下拉选择或手动输入）",
                )
                return
        else:
            # 未勾选启用但已填了 API Key：弹确认避免用户以为已生效
            if api_key:
                proceed = QMessageBox.question(
                    self._dlg,
                    "AI 润色未启用",
                    "你已填写 API Key，但未勾选「启用 AI 润色」。\n"
                    "保存后识别结果将不经大模型纠错，直接注入原文。\n\n"
                    "要启用 AI 润色吗？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if proceed == QMessageBox.Yes:
                    # 用户确认启用：自动勾选
                    self._enabled_chk.setChecked(True)
                    enabled = True
                # 否则保持不启用，继续保存

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
        if self._dlg is not None:
            self._dlg.accept()

    def _on_cancel(self) -> None:
        """取消按钮回调：直接关闭对话框。"""
        if self._dlg is not None:
            self._dlg.reject()
