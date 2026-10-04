"""设置界面：让用户自己填密钥，密钥不经过聊天、不落明文（规矩 R15）。

设计要点：
- 密钥输入框默认掩码，可临时"显示"核对；
- 保存时密钥写入系统凭据库（Windows 凭据管理器），非敏感项写 config.json；
- 保存后立即通知编排器热重载，不用重启程序；
- 提供"测试连接"，用一段静音音频验证凭据是否真的能用。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import (
    CONFIG_DIR,
    ERROR_LOG_FILE,
    LOG_FILE,
    Credential,
    clear_logs,
    tail_log,
)
from .constants import AUDIO_RATE, AUDIO_WIDTH, MAX_RECORD_MS
from .models import AudioClip
from .recorder import list_input_devices

logger = logging.getLogger("voice_input.settings")

# 服务商下拉的显示名 → 配置里的 provider 值
PROVIDER_ITEMS = [
    ("腾讯云 · 一句话识别", "tencent"),
    ("OpenAI 兼容接口（Whisper / SenseVoice 等）", "openai"),
    ("不联网测试（Mock 假识别）", "mock"),
]

REGION_ITEMS = [
    ("ap-shanghai", "华东-上海"),
    ("ap-beijing", "华北-北京"),
    ("ap-guangzhou", "华南-广州"),
    ("ap-chengdu", "西南-成都"),
    ("ap-hongkong", "中国香港"),
]

ENGINE_ITEMS = [
    ("16k_zh", "中文普通话"),
    ("16k_en", "英语"),
    ("16k_ca", "粤语"),
    ("16k_ja", "日语"),
    ("16k_ko", "韩语"),
]

HOTKEY_PRESETS = [
    "ctrl+alt+space",
    "ctrl+alt+v",
    "ctrl+shift+space",
    "alt+space",
    "ctrl+alt+r",
    "f8",
]


def _provider_index(value: str) -> int:
    for i, (_, v) in enumerate(PROVIDER_ITEMS):
        if v == value:
            return i
    return len(PROVIDER_ITEMS) - 1


def _combo_value(combo: QComboBox) -> str:
    return combo.currentData() or ""


class _SecretField(QWidget):
    """密钥输入行：输入框 + 显示切换。"""

    def __init__(self, label: str, placeholder: str = "") -> None:
        super().__init__()
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(placeholder)

        self.toggle = QCheckBox("显示")
        self.toggle.toggled.connect(self._on_toggle)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label))
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.toggle)

    def _on_toggle(self, checked: bool) -> None:
        self.edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802  对齐 Qt 命名便于调用
        self.edit.setText(value or "")


class _TestWorker(QThread):
    """后台跑一次识别，避免测试连接时界面卡住。"""

    result = Signal(object)

    def __init__(self, client) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:  # noqa: D102
        # 用 1 秒静音当探针：只关心能不能连通、鉴权过不过，不关心识别内容
        clip = AudioClip(pcm=b"\x00" * (AUDIO_RATE * AUDIO_WIDTH))
        try:
            transcript = self._client.transcribe(clip)
        except Exception as exc:  # 后台线程绝不向外抛
            self.result.emit((False, f"{exc}"))
            return
        if transcript.error:
            self.result.emit((False, transcript.error))
        else:
            self.result.emit((True, "连通正常（探针为静音音频，识别结果为空属正常现象）"))


class SettingsDialog(QDialog):
    """设置窗口：识别服务 / 热键与设备 / 高级与日志。"""

    def __init__(self, config, on_saved: Optional[Callable] = None, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self._on_saved = on_saved
        self._test_worker: Optional[_TestWorker] = None

        self.setWindowTitle("语音输入 · 设置")
        self.setMinimumWidth(560)
        # 设置窗口是"要抢焦点"的对话框：它是用户主动打开的，与浮窗相反
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_service_tab(), "识别服务")
        self.tabs.addTab(self._build_input_tab(), "热键与设备")
        self.tabs.addTab(self._build_advanced_tab(), "高级与日志")
        tabs = self.tabs

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并生效")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

        self._load()

    # ---------------- 各页构造 ----------------

    def _build_service_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.provider = QComboBox()
        for label, value in PROVIDER_ITEMS:
            self.provider.addItem(label, value)
        self.provider.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("服务商", self.provider)

        # --- 腾讯云 ---
        self.tencent_page = QWidget()
        tf = QFormLayout(self.tencent_page)
        tf.setContentsMargins(0, 0, 0, 0)
        self.secret_id = _SecretField("SecretId", "AKIDxxxxxxxxxxxxxxxx")
        self.secret_key = _SecretField("SecretKey", "创建密钥时只显示一次")
        tf.addRow(self.secret_id)
        tf.addRow(self.secret_key)

        self.region = QComboBox()
        for value, label in REGION_ITEMS:
            self.region.addItem(f"{value}（{label}）", value)
        tf.addRow("地域", self.region)

        self.engine = QComboBox()
        for value, label in ENGINE_ITEMS:
            self.engine.addItem(f"{value} · {label}", value)
        tf.addRow("识别引擎", self.engine)
        form.addRow(self.tencent_page)

        # --- OpenAI 兼容 ---
        self.openai_page = QWidget()
        of = QFormLayout(self.openai_page)
        of.setContentsMargins(0, 0, 0, 0)
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("https://api.siliconflow.cn/v1")
        of.addRow("接口地址", self.endpoint)
        self.api_key = _SecretField("API Key", "sk-xxxxxxxxxxxxxxxx")
        of.addRow(self.api_key)
        self.model = QLineEdit()
        self.model.setPlaceholderText("whisper-1")
        of.addRow("模型名", self.model)
        form.addRow(self.openai_page)

        # --- Mock（不联网） ---
        self.mock_page = QLabel(
            "当前为测试模式：不联网、不出本机，识别结果固定为假文本，"
            "仅用于验证“热键 → 录音 → 上屏”链路是否通畅。"
        )
        self.mock_page.setWordWrap(True)
        form.addRow(self.mock_page)

        # --- 凭据存放位置说明 ---
        self.cred_hint = QLabel()
        self.cred_hint.setWordWrap(True)
        self.cred_hint.setStyleSheet("color:#6B7280;font-size:11px;")
        form.addRow(self.cred_hint)

        # --- 测试连接 ---
        row = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._on_test)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        row.addWidget(self.test_btn)
        row.addWidget(self.test_result, 1)
        form.addRow(row)

        return page

    def _build_input_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.hotkey = QComboBox()
        self.hotkey.setEditable(True)
        self.hotkey.addItems(HOTKEY_PRESETS)
        form.addRow("录音热键", self.hotkey)

        hint = QLabel("格式：修饰键+按键，如 ctrl+alt+space；支持 ctrl / alt / shift / win")
        hint.setStyleSheet("color:#6B7280;font-size:11px;")
        form.addRow("", hint)

        self.device = QComboBox()
        self.device.addItem("系统默认设备", None)
        for name in list_input_devices():
            self.device.addItem(name, name)
        form.addRow("麦克风", self.device)

        self.max_record = QSpinBox()
        self.max_record.setRange(1000, MAX_RECORD_MS)
        self.max_record.setSuffix(" 毫秒")
        self.max_record.setSingleStep(1000)
        form.addRow("最长录音", self.max_record)

        self.min_record = QSpinBox()
        self.min_record.setRange(0, 5000)
        self.min_record.setSuffix(" 毫秒")
        form.addRow("最短有效录音", self.min_record)

        self.polish = QCheckBox("开启文本润色（去口语词、补标点）")
        form.addRow(self.polish)

        return page

    def _build_advanced_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.overlay_enabled = QCheckBox("显示底部状态浮窗")
        form.addRow(self.overlay_enabled)

        self.fallback_uia = QCheckBox("粘贴失败时用 UI 自动化兜底（实验性）")
        form.addRow(self.fallback_uia)

        self.log_level = QComboBox()
        self.log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        form.addRow("日志级别", self.log_level)

        form.addRow(QLabel("日志文件："))
        self.log_paths = QPlainTextEdit()
        self.log_paths.setReadOnly(True)
        self.log_paths.setFixedHeight(52)
        self.log_paths.setPlainText(f"{LOG_FILE}\n{ERROR_LOG_FILE}")
        form.addRow(self.log_paths)

        row = QHBoxLayout()
        btn_open_log = QPushButton("查看日志")
        btn_open_log.clicked.connect(lambda: LogViewer(self).show())
        btn_open_dir = QPushButton("打开日志目录")
        btn_open_dir.clicked.connect(self._open_log_dir)
        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self._on_clear_logs)
        row.addWidget(btn_open_log)
        row.addWidget(btn_open_dir)
        row.addWidget(btn_clear)
        form.addRow(row)

        return page

    # ---------------- 载入 / 保存 ----------------

    def _load(self) -> None:
        self.provider.setCurrentIndex(_provider_index(self.config.asr_provider))
        self.secret_id.setText(Credential.get("asr_secret_id") or "")
        self.secret_key.setText(Credential.get("asr_secret_key") or "")
        self._select_data(self.region, self.config.asr_region)
        self._select_data(self.engine, self.config.asr_engine)

        self.endpoint.setText(self.config.asr_endpoint)
        self.api_key.setText(Credential.get("asr_api_key") or "")
        self.model.setText(getattr(self.config, "asr_model", "whisper-1"))

        self.hotkey.setCurrentText(self.config.hotkey)
        self._select_data(self.device, self.config.device)
        self.max_record.setValue(self.config.max_record_ms)
        self.min_record.setValue(self.config.min_record_ms)
        self.polish.setChecked(self.config.polish_enabled)

        self.overlay_enabled.setChecked(self.config.overlay_enabled)
        self.fallback_uia.setChecked(self.config.fallback_uia)
        self._select_data(self.log_level, getattr(self.config, "log_level", "INFO"))
        self.log_level.setCurrentText(getattr(self.config, "log_level", "INFO"))

        self.cred_hint.setText(
            f"密钥保存在系统凭据库（当前后端：{Credential.backend_name()}），"
            "不写入 config.json、不写入日志。若凭据库不可用，可用环境变量 "
            "VOICE_INPUT_ASR_SECRET_ID / VOICE_INPUT_ASR_SECRET_KEY 代替。"
        )
        self._on_provider_changed()

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _on_provider_changed(self) -> None:
        provider = _combo_value(self.provider)
        self.tencent_page.setVisible(provider == "tencent")
        self.openai_page.setVisible(provider == "openai")
        self.mock_page.setVisible(provider == "mock")
        self.test_btn.setEnabled(provider != "mock")
        self.adjustSize()

    def _collect(self):
        """把界面内容组装成新 Config（pydantic 负责校验）。"""
        data = self.config.model_dump()
        data.update(
            asr_provider=_combo_value(self.provider),
            asr_region=_combo_value(self.region),
            asr_engine=_combo_value(self.engine),
            asr_endpoint=self.endpoint.text().strip(),
            asr_model=self.model.text().strip() or "whisper-1",
            hotkey=self.hotkey.currentText().strip(),
            device=self.device.currentData(),
            max_record_ms=self.max_record.value(),
            min_record_ms=self.min_record.value(),
            polish_enabled=self.polish.isChecked(),
            overlay_enabled=self.overlay_enabled.isChecked(),
            fallback_uia=self.fallback_uia.isChecked(),
            log_level=self.log_level.currentText(),
        )
        return type(self.config)(**data)

    def _on_save(self) -> None:
        from pydantic import ValidationError

        try:
            new_config = self._collect()
        except ValidationError as exc:
            QMessageBox.warning(self, "配置无效", f"请检查填写内容：\n{exc}")
            return

        # 敏感项：写系统凭据库；清空则表示删除
        provider = new_config.asr_provider
        failed = []
        if provider == "tencent":
            for key, value in (
                ("asr_secret_id", self.secret_id.text()),
                ("asr_secret_key", self.secret_key.text()),
            ):
                if value:
                    if not Credential.set(key, value):
                        failed.append(key)
                else:
                    Credential.delete(key)
            if not (self.secret_id.text() and self.secret_key.text()):
                QMessageBox.information(
                    self, "提示", "密钥未填写完整，识别时会提示“未配置密钥”。"
                )
        elif provider == "openai":
            value = self.api_key.text()
            if value:
                if not Credential.set("asr_api_key", value):
                    failed.append("asr_api_key")
            else:
                Credential.delete("asr_api_key")
            if not new_config.asr_endpoint:
                QMessageBox.information(self, "提示", "未填写接口地址，识别时会失败。")

        if failed:
            QMessageBox.warning(
                self,
                "密钥未能写入凭据库",
                "以下密钥保存失败："
                + ", ".join(failed)
                + "\n可改用环境变量：VOICE_INPUT_ASR_SECRET_ID / "
                "VOICE_INPUT_ASR_SECRET_KEY / VOICE_INPUT_ASR_API_KEY",
            )

        new_config.save()  # 只写非敏感项
        logger.info("设置已保存：provider=%s", provider)

        if self._on_saved is not None:
            try:
                self._on_saved(new_config)
            except Exception as exc:
                logger.error("应用新配置失败：%s", exc)
                QMessageBox.warning(self, "应用失败", str(exc))

        self.accept()

    # ---------------- 测试连接 ----------------

    def _on_test(self) -> None:
        """用界面上**未保存**的凭据测一次，避免先保存再发现问题。"""
        from .asr import OpenAILikeAsrClient, TencentAsrClient

        provider = _combo_value(self.provider)
        if provider == "tencent":
            client = TencentAsrClient(
                secret_id=self.secret_id.text(),
                secret_key=self.secret_key.text(),
                region=_combo_value(self.region),
                engine=_combo_value(self.engine),
            )
        elif provider == "openai":
            client = OpenAILikeAsrClient(
                endpoint=self.endpoint.text().strip(),
                api_key=self.api_key.text(),
                model=self.model.text().strip() or "whisper-1",
            )
        else:
            return

        self.test_btn.setEnabled(False)
        self.test_result.setText("正在测试…")

        self._test_worker = _TestWorker(client)
        self._test_worker.result.connect(self._on_test_result)
        self._test_worker.finished.connect(self._test_worker.deleteLater)
        self._test_worker.start()

    def _on_test_result(self, payload) -> None:
        ok, message = payload
        self.test_btn.setEnabled(True)
        note = "可用" if ok else "失败"
        self.test_result.setText(f"{note}：{message}")
        color = "#639922" if ok else "#BA7517"
        self.test_result.setStyleSheet(f"color:{color};")
        if not ok:
            logger.warning("ASR 连通性测试失败：%s", message)

    # ---------------- 日志相关 ----------------

    @staticmethod
    def _open_log_dir() -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONFIG_DIR)))

    def _on_clear_logs(self) -> None:
        ret = QMessageBox.question(
            self, "清空日志", "确定要清空 app.log 与 error.log 吗？"
        )
        if ret == QMessageBox.StandardButton.Yes:
            clear_logs()
            logger.info("日志已手动清空")


class LogViewer(QDialog):
    """日志查看器：直接读文件尾部，改日志不影响运行中的程序。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("运行日志")
        self.resize(760, 520)

        self.tabs = QTabWidget(self)
        self.views = {}
        for title, path in (("全部日志 (app.log)", LOG_FILE), ("仅报错 (error.log)", ERROR_LOG_FILE)):
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            font = QFont("Consolas", 9)
            view.setFont(font)
            view.setPlainText(tail_log(path) or "（暂无内容）")
            self.tabs.addTab(view, title)
            self.views[path] = view

        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._refresh)
        btn_open = QPushButton("打开目录")
        btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONFIG_DIR))))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.close)
        box.accepted.connect(self.close)
        box.addButton(btn_refresh, QDialogButtonBox.ButtonRole.ActionRole)
        box.addButton(btn_open, QDialogButtonBox.ButtonRole.ActionRole)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(box)

    def _refresh(self) -> None:
        for path, view in self.views.items():
            view.setPlainText(tail_log(path) or "（暂无内容）")
            cursor = view.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            view.setTextCursor(cursor)
