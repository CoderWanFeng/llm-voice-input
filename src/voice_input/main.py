"""程序入口：装配零件 → 注册热键 → 预热录音流 → 进入事件循环。"""

from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

from .app import build_orchestrator
from .config import (
    CONFIG_FILE,
    Config,
    Credential,
    install_exception_hooks,
    setup_logging,
)
from .models import AppState
from .settings import LogViewer, SettingsDialog
from .ui import Overlay

# 非模态窗口必须留住引用，否则函数一返回就被回收、窗口闪一下就没了
_WINDOWS: list = []


def main() -> int:
    config = Config.load()
    first_run = not CONFIG_FILE.exists()
    config.save()  # 首次运行生成配置文件，方便手改

    logger = setup_logging(
        getattr(logging, config.log_level.upper(), logging.INFO)
    )
    install_exception_hooks(logger)  # 未捕获异常也进日志，别让崩溃无声无息
    logger.info("语音输入工具启动（日志级别 %s）", config.log_level)

    if "--diag" in sys.argv:  # 打包后自检：只检查环境，不进事件循环
        return run_diagnostics(logger, config)

    app = QApplication(sys.argv)
    app.setApplicationName("语音输入工具")
    app.setQuitOnLastWindowClosed(False)  # 关掉浮窗不等于退出

    overlay = Overlay()

    def open_logs() -> None:
        viewer = LogViewer()
        viewer.show()
        _WINDOWS.append(viewer)

    def open_settings() -> None:
        dlg = SettingsDialog(orchestrator.config, on_saved=on_saved)
        dlg.exec()

    def on_saved(new_config: Config) -> None:
        msg = orchestrator.apply_config(new_config)
        logger.info("设置已生效：%s", msg)
        orchestrator.tray.icon.showMessage(
            "语音输入", f"设置已保存并生效：{msg}",
            QSystemTrayIcon.MessageIcon.Information, 4000,
        )

    orchestrator = build_orchestrator(
        config, overlay, on_quit=app.quit, on_settings=open_settings, on_logs=open_logs
    )
    overlay.set_level_provider(orchestrator.recorder.level)

    # 隐藏窗口：只为接收 WM_HOTKEY，永远不显示
    holder = QWidget()
    holder.setWindowFlags(Qt.Tool)
    holder.setAttribute(Qt.WA_ShowWithoutActivating, True)
    hwnd = int(holder.winId())

    ok, msg = orchestrator.setup_hotkey(app, hwnd)
    logger.info(msg)
    if not ok:
        orchestrator.tray.icon.showMessage(
            "语音输入", msg, QSystemTrayIcon.MessageIcon.Warning, 5000
        )

    # 预热录音流：常驻打开，避免每次按键都要等开流（丢音头）
    if not orchestrator.recorder.open():
        orchestrator.tray.icon.showMessage(
            "语音输入", "麦克风不可用，请检查设备与隐私权限",
            QSystemTrayIcon.MessageIcon.Warning, 5000,
        )

    app.aboutToQuit.connect(orchestrator.shutdown)
    orchestrator.overlay.set_state(AppState.IDLE, "")
    logger.info("就绪，按 %s 开始说话", config.hotkey)

    # 首次运行直接把设置窗口打开，省得用户去猜密钥填在哪
    if first_run:
        QTimer.singleShot(300, open_settings)
    elif not _credentials_ready(config):
        orchestrator.tray.icon.showMessage(
            "语音输入",
            "还没填 ASR 密钥：右键托盘图标 → 设置…",
            QSystemTrayIcon.MessageIcon.Warning, 6000,
        )
        logger.warning("已选服务商 %s 但缺少密钥，识别将失败", config.asr_provider)

    return app.exec()


def run_diagnostics(logger, config: Config) -> int:
    """打包后的自检：把关键环境信息写进日志，返回 0 表示正常、2 表示有异常。

    exe 里看不到控制台，出问题只能靠日志，所以每一步都要留下痕迹。
    """
    state = {"ok": True}

    def note(name: str, value, good: bool = True) -> None:
        state["ok"] = state["ok"] and good
        logger.info("诊断 %-12s = %s%s", name, value, "" if good else "   <-- 异常")

    logger.info("自检模式（--diag）：只检查环境，不常驻运行")

    backend = Credential.backend_name()
    note("凭据库后端", backend, backend not in ("不可用", ""))

    from .recorder import list_input_devices  # noqa: PLC0415

    devs = list_input_devices()
    note("输入设备数", len(devs), len(devs) > 0)

    from .hotkey import parse_combo  # noqa: PLC0415

    try:
        mods, vk = parse_combo(config.hotkey)
        note("热键解析", f"{config.hotkey} → mods={mods} vk={vk}")
    except ValueError as exc:
        note("热键解析", exc, False)

    # 真的开一次流并录 0.3 秒：PortAudio 的 DLL 是打包最容易漏的东西，
    # 漏了的现象是"设备能列出来但一录音就崩"
    from .recorder import Recorder  # noqa: PLC0415

    try:
        rec = Recorder(device=config.device)
        if not rec.open():
            note("麦克风开流", rec.error or "失败", False)
        else:
            note("麦克风开流", f"{rec._native_rate} Hz")
            rec.start()
            time.sleep(0.3)
            clip = rec.stop()
            rec.close()
            note("录音探针", f"{clip.duration_ms} ms，前置缓冲={clip.has_preroll}",
                 clip.duration_ms > 0)
    except Exception as exc:
        note("麦克风开流", exc, False)

    # 能不能构造出界面 = Qt 平台插件与控件是否齐全（打包最容易缺的就是这个）
    try:
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: PLC0415

        app = QApplication([])
        dlg = SettingsDialog(config)
        dlg.close()
        note("Qt 界面", "QApplication + 设置界面构造成功")
        # 托盘是常驻程序唯一的入口，没有它就既打不开设置也退不出去
        note("系统托盘", QSystemTrayIcon.isSystemTrayAvailable(),
             QSystemTrayIcon.isSystemTrayAvailable())
        del app
    except Exception as exc:
        note("Qt 界面", exc, False)

    logger.info("诊断结束：%s", "全部正常" if state["ok"] else "存在异常，见上")
    return 0 if state["ok"] else 2


def _credentials_ready(config: Config) -> bool:
    """判断当前服务商的密钥是否齐备（mock 不需要密钥）。"""
    if config.asr_provider == "tencent":
        return bool(Credential.get("asr_secret_id") and Credential.get("asr_secret_key"))
    if config.asr_provider == "openai":
        return bool(config.asr_endpoint and Credential.get("asr_api_key"))
    return True


if __name__ == "__main__":
    sys.exit(main())
