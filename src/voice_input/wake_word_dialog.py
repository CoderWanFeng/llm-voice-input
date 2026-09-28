"""语音唤醒设置对话框模块（PySide6 版）。

提供语音唤醒与结束词的可视化配置界面（与凭证对话框同风格）：
- 启用/禁用语音唤醒（勾选框）+ 唤醒词自定义（可编辑下拉框）
- 启用/禁用语音结束词（录音中说出自动停止）+ 结束词自定义
- 保存时校验唤醒词/结束词每个字是否在 Vosk 离线词库，
  含缺失字时弹窗警告并让用户确认是否仍然保存
- 保存后由主应用热更新检测器，无需重启

唤醒词/结束词均选择「输入文字」而非「录入语音」：
底层检测是文本语法匹配，录音最终也要转成文字，等于绕一圈
还引入转写误差；文字输入可即时校验词库、随时修改、不受环境噪音影响。

必须在 Qt 主线程调度（通过 schedule_on_main）。
"""

from __future__ import annotations

import re
from typing import List, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

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

    def __init__(self, parent: Optional[QWidget], detector: WakeWordDetector) -> None:
        """初始化对话框。

        Args:
            parent: 父窗口（PySide6 QWidget；为 None 时对话框无父窗口）
            detector: 唤醒检测器实例（用于保存前校验词库）
        """
        # 父窗口
        self._parent = parent
        # 唤醒检测器（词库校验用）
        self._detector = detector
        # 对话框引用
        self._dlg: Optional[QDialog] = None
        # 是否已保存
        self._saved = False
        # 启用开关
        self._enabled_chk: Optional[QCheckBox] = None
        # 唤醒词可编辑下拉框
        self._word_combo: Optional[QComboBox] = None
        # 结束词启用开关
        self._stop_enabled_chk: Optional[QCheckBox] = None
        # 结束词可编辑下拉框
        self._stop_word_combo: Optional[QComboBox] = None

    def show(self) -> bool:
        """显示对话框，返回是否保存成功。

        QDialog.exec() 是模态阻塞的，等价于 tkinter 的 wait_window。

        Returns:
            True 表示用户保存了设置，False 表示取消或关闭。
        """
        existing = config_module.load()
        word = existing.wake_word or WAKE_WORD
        stop_word = existing.stop_word or STOP_WORD

        # 创建模态对话框
        self._dlg = QDialog(self._parent)
        self._dlg.setWindowTitle("语音唤醒设置")
        self._dlg.setFixedSize(460, 560)

        self._build_ui(word=word, stop_word=stop_word)

        # exec() 模态阻塞
        self._dlg.exec()
        return self._saved

    def _build_ui(self, word: str, stop_word: str) -> None:
        """构造对话框 UI 控件。

        Args:
            word: 当前唤醒词（确保下拉框预设列表包含它）
            stop_word: 当前结束词（确保下拉框预设列表包含它）
        """
        layout = QVBoxLayout(self._dlg)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)

        # 标题
        title = QLabel("语音唤醒设置")
        title.setStyleSheet("font-family: 'Microsoft YaHei UI'; font-size: 12pt; font-weight: bold;")
        layout.addWidget(title)

        # 启用语音唤醒勾选框
        existing = config_module.load()
        self._enabled_chk = QCheckBox("启用语音唤醒（空闲时说出唤醒词自动开始录音）")
        self._enabled_chk.setChecked(existing.wake_word_enabled)
        layout.addWidget(self._enabled_chk)

        # 唤醒词行
        word_row = QHBoxLayout()
        word_row.addWidget(QLabel("唤醒词:"))
        self._word_combo = QComboBox()
        self._word_combo.setEditable(True)  # 可编辑下拉框：可输入任意词
        presets = list(_WAKE_PRESETS)
        if word and word not in presets:
            presets.insert(0, word)
        for p in presets:
            self._word_combo.addItem(p)
        # 默认显示当前唤醒词
        idx = self._word_combo.findText(word)
        if idx >= 0:
            self._word_combo.setCurrentIndex(idx)
        else:
            self._word_combo.setEditText(word)
        word_row.addWidget(self._word_combo, 1)
        layout.addLayout(word_row)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        # 启用语音结束词勾选框
        self._stop_enabled_chk = QCheckBox("启用语音结束词（录音中说出即自动停止录音）")
        self._stop_enabled_chk.setChecked(existing.stop_word_enabled)
        layout.addWidget(self._stop_enabled_chk)

        # 结束词行
        stop_row = QHBoxLayout()
        stop_row.addWidget(QLabel("结束词:"))
        self._stop_word_combo = QComboBox()
        self._stop_word_combo.setEditable(True)
        stop_presets = list(_STOP_PRESETS)
        if stop_word and stop_word not in stop_presets:
            stop_presets.insert(0, stop_word)
        for p in stop_presets:
            self._stop_word_combo.addItem(p)
        sidx = self._stop_word_combo.findText(stop_word)
        if sidx >= 0:
            self._stop_word_combo.setCurrentIndex(sidx)
        else:
            self._stop_word_combo.setEditText(stop_word)
        stop_row.addWidget(self._stop_word_combo, 1)
        layout.addLayout(stop_row)

        # 使用说明标题
        guide_caption = QLabel("使用说明:")
        guide_caption.setStyleSheet("font-family: 'Microsoft YaHei UI'; font-size: 9pt; font-weight: bold;")
        layout.addWidget(guide_caption)

        # 说明文本（只读）
        guide_text = QTextEdit()
        guide_text.setReadOnly(True)
        guide_text.setStyleSheet("background-color: #f5f5f5; border: none; font-family: 'Microsoft YaHei UI'; font-size: 9pt;")
        guide_text.setPlainText(
            "· 空闲时说出唤醒词即开始录音；停止仍按 Ctrl + Alt + K\n"
            "· 启用结束词后，录音中说出结束词（如「结束录音」）即自动\n"
            "  停止并识别，结束词本身不会出现在识别结果里\n"
            "· 唤醒词与结束词需为 2~6 个常用汉字，推荐叠词或四字短语\n"
            "· 保存时自动校验离线词库，含生僻字的词可能无法生效\n"
            "· 唤醒/结束检测完全离线进行，不会上传任何声音\n"
            "· 保存后立即生效，无需重启工具"
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
        """保存按钮回调：校验唤醒词/结束词格式与词库后写入配置。"""
        word = self._word_combo.currentText().strip() if self._word_combo else ""
        enabled = self._enabled_chk.isChecked() if self._enabled_chk else False
        stop_word = self._stop_word_combo.currentText().strip() if self._stop_word_combo else ""
        stop_enabled = self._stop_enabled_chk.isChecked() if self._stop_enabled_chk else False

        # 开启唤醒时才强制校验唤醒词格式
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
        if self._dlg is not None:
            self._dlg.accept()

    def _check_word(self, word: str, label: str) -> bool:
        """校验唤醒词/结束词的格式与词库，不通过时弹窗提示。

        Args:
            word: 待校验的词（非空）
            label: 显示用名称（「唤醒词」/「结束词」）

        Returns:
            True 表示校验通过（或用户确认仍要保存）
        """
        if not word:
            QMessageBox.warning(
                self._dlg,
                f"{label}为空",
                f"已启用语音{label}，请填写{label}（2~6 个汉字）",
            )
            return False
        if not _WAKE_WORD_RE.match(word):
            QMessageBox.warning(
                self._dlg,
                f"{label}格式不正确",
                f"{label}需为 2~6 个汉字，例如：小爱小爱、你好小智",
            )
            return False
        # 词库校验：缺失字会导致永远无法命中，需用户确认
        missing = self._detector.validate_wake_word(word)
        if missing:
            proceed = QMessageBox.question(
                self._dlg,
                f"{label}含生僻字",
                f"以下字不在离线识别词库中，{label}可能无法生效：\n\n"
                f"{'、'.join(missing)}\n\n"
                "建议换用常用汉字。仍要保存吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if proceed != QMessageBox.Yes:
                return False
            DiagLog.shared().write(
                f"[Dialog] {label}含缺失字 {'、'.join(missing)}，用户确认保存"
            )
        return True

    def _on_cancel(self) -> None:
        """取消按钮回调：直接关闭对话框。"""
        if self._dlg is not None:
            self._dlg.reject()
