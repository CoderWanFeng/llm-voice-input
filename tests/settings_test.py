"""设置界面与日志功能的验证（不弹窗、不联网、不写真实凭据）。"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from voice_input.config import (  # noqa: E402
    ERROR_LOG_FILE,
    LOG_FILE,
    Config,
    clear_logs,
    install_exception_hooks,
    setup_logging,
)
from voice_input.settings import LogViewer, SettingsDialog  # noqa: E402

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    app = QApplication([])  # 构造控件必须有 QApplication

    # ---------- 设置界面 ----------
    print("\n[设置界面]")
    cfg = Config()
    dlg = SettingsDialog(cfg, on_saved=None)

    dlg.provider.setCurrentIndex(0)  # 腾讯云
    check("服务商下拉含三个选项", dlg.provider.count() == 3,
          str([dlg.provider.itemData(i) for i in range(dlg.provider.count())]))
    check("选腾讯云时显示腾讯云面板", dlg.tencent_page.isVisibleTo(dlg)
          and not dlg.openai_page.isVisibleTo(dlg))

    dlg.provider.setCurrentIndex(1)  # OpenAI
    app.processEvents()
    check("切换服务商后面板随之切换", dlg.openai_page.isVisibleTo(dlg)
          and not dlg.tencent_page.isVisibleTo(dlg))

    dlg.provider.setCurrentIndex(0)
    dlg.secret_id.setText("AKIDFAKEFAKEFAKEFAKE")
    dlg.secret_key.setText("FAKEKEYFAKEKEYFAKEKEY")
    dlg.region.setCurrentIndex(0)
    dlg.engine.setCurrentIndex(0)
    dlg.hotkey.setCurrentText("ctrl+alt+v")
    dlg.max_record.setValue(20000)
    dlg.device.setCurrentIndex(0)
    dlg.polish.setChecked(True)
    dlg.log_level.setCurrentText("DEBUG")

    collected = dlg._collect()
    check("界面内容能组装成合法 Config", isinstance(collected, Config))
    check("热键被采集", collected.hotkey == "ctrl+alt+v", collected.hotkey)
    check("数值项被采集", collected.max_record_ms == 20000)
    check("开关项被采集", collected.polish_enabled is True)
    check("日志级别被采集", collected.log_level == "DEBUG")
    check("密钥不进 Config（不在落盘 json 里）",
          not any("FAKE" in str(v) for v in collected.model_dump().values()),
          str(list(collected.model_dump().keys())))

    # ---------- 日志 ----------
    print("\n[日志文件]")
    logger = setup_logging(logging.DEBUG)
    install_exception_hooks(logger)
    clear_logs()

    logger.info("普通信息一行")
    logger.warning("警告信息一行")
    logger.error("错误信息一行")
    logger.info("疑似密钥：secret=ABCDEFGHIJKLMNOP1234")
    for h in logger.handlers:
        h.flush()

    app_log = LOG_FILE.read_text(encoding="utf-8")
    err_log = ERROR_LOG_FILE.read_text(encoding="utf-8")

    check("app.log 记录 INFO", "普通信息一行" in app_log)
    check("error.log 只收报错", "警告信息一行" in err_log and "错误信息一行" in err_log)
    check("error.log 不收普通信息", "普通信息一行" not in err_log)
    check("error.log 带文件名行号", ":" in err_log and "settings_test.py" in err_log)
    check("日志脱敏生效（密钥明文不落盘）",
          "ABCDEFGHIJKLMNOP1234" not in app_log and "ABCDEFGHIJKLMNOP1234" not in err_log)
    check("脱敏后仍保留线索", "***" in app_log)

    # 未捕获异常也要进日志
    try:
        raise RuntimeError("故意的未捕获异常")
    except RuntimeError as exc:
        sys.excepthook(type(exc), exc, exc.__traceback__)
    for h in logger.handlers:
        h.flush()
    check("未捕获异常写入 error.log",
          "故意的未捕获异常" in ERROR_LOG_FILE.read_text(encoding="utf-8"))

    clear_logs()
    for h in logger.handlers:
        h.flush()
    logger.info("清空后继续写入")
    for h in logger.handlers:
        h.flush()
    after = LOG_FILE.read_text(encoding="utf-8")
    check("清空日志后仍能正常写入", "清空后继续写入" in after)
    check("清空后无残留空洞（NUL 字节）", "\x00" not in after)

    # ---------- 日志查看器 ----------
    print("\n[日志查看器]")
    viewer = LogViewer()
    viewer._refresh()
    check("日志查看器两个页签", viewer.tabs.count() == 2)
    check("查看器能读到内容", "清空后继续写入" in viewer.views[LOG_FILE].toPlainText())

    print(f"\n结果：{len(PASS)} 项通过，{len(FAIL)} 项失败")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
