"""打包产物端到端验证：启动 dist/VoiceInput.exe，模拟按热键，看文字是否落进光标处。

这是最贴近真实使用的一次验证——双击 exe、按快捷键、文字进输入框。
（此刻 ASR 还是 Mock，所以期望的是固定的模拟文本。）
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EXE = ROOT / "dist" / "VoiceInput.exe"
LOG = Path.home() / "AppData/Roaming/voice_input/app.log"

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QTextEdit, QVBoxLayout, QWidget  # noqa: E402

import win32clipboard  # noqa: E402
from voice_input.injector import _key  # noqa: E402

VK_CONTROL, VK_MENU, VK_SPACE = 0x11, 0x12, 0x20
ORIGINAL_CLIPBOARD = "原始剪贴板-勿丢失"

proc: subprocess.Popen | None = None
result = {"stage": "未开始"}


def send_hotkey() -> None:
    """模拟按下 ctrl+alt+space（SendInput 注入系统输入流，全局热键能收到）。"""
    for vk in (VK_CONTROL, VK_MENU, VK_SPACE):
        _key(vk, up=False)
    for vk in (VK_SPACE, VK_MENU, VK_CONTROL):
        _key(vk, up=True)


def read_clipboard() -> str:
    try:
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:
        return f"<读取失败 {exc}>"


def main() -> int:
    if not EXE.exists():
        print(f"❌ 找不到 {EXE}，请先打包")
        return 1

    app = QApplication([])

    # 靶子窗口：光标放在 'AB' 中间，注入后应变成 'A...B'
    win = QWidget()
    win.setWindowTitle("exe 端到端测试靶子")
    layout = QVBoxLayout(win)
    edit = QTextEdit()
    edit.setPlainText("AB")
    layout.addWidget(edit)
    win.resize(460, 220)

    print(f"启动 {EXE.name} ...")
    global proc
    proc = subprocess.Popen([str(EXE)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def stage_show() -> None:
        result["stage"] = "显示靶子窗口"
        win.show()
        win.raise_()
        win.activateWindow()
        app.processEvents()
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(ORIGINAL_CLIPBOARD)
        win32clipboard.CloseClipboard()
        cursor = edit.textCursor()
        cursor.setPosition(1)
        edit.setTextCursor(cursor)
        edit.setFocus()
        app.processEvents()
        print(f"  剪贴板预设：{read_clipboard()!r}")

    def stage_press_start() -> None:
        result["stage"] = "按第一次（开始录音）"
        print("  按下 Ctrl+Alt+Space → 开始录音")
        send_hotkey()

    def stage_press_stop() -> None:
        result["stage"] = "按第二次（停止并上屏）"
        print("  再按一次 → 停止、识别、上屏")
        send_hotkey()

    def stage_check() -> None:
        result["stage"] = "校验结果"
        for _ in range(15):
            app.processEvents()
            time.sleep(0.1)
        content = edit.toPlainText()
        clip = read_clipboard()
        print(f"\n  输入框内容：{content!r}")
        print(f"  剪贴板内容：{clip!r}")

        ok_text = content.startswith("A") and content.endswith("B") and len(content) > 2
        ok_clip = clip == ORIGINAL_CLIPBOARD
        print(f"  {'✅' if ok_text else '❌'} 文字落在光标处（'AB' 中间被插入内容）")
        print(f"  {'✅' if ok_clip else '❌'} 剪贴板已还原")

        log_tail = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-14:]
        print("\n  --- exe 运行日志 ---")
        for line in log_tail:
            print("   " + line)
        ok_log = any("注入成功" in l for l in log_tail)
        print(f"\n  {'✅' if ok_log else '❌'} 日志中出现「注入成功」")

        result["ok"] = ok_text and ok_clip and ok_log
        finish()

    def finish() -> None:
        global proc
        if proc and proc.poll() is None:
            proc.terminate()
        app.quit()

    QTimer.singleShot(8000, stage_show)        # 等 onefile 解压启动
    QTimer.singleShot(8500, stage_press_start)
    QTimer.singleShot(10200, stage_press_stop)
    QTimer.singleShot(13200, stage_check)
    QTimer.singleShot(20000, finish)           # 兜底，别把测试挂死

    app.exec()

    ok = result.get("ok", False)
    print("\n结果：" + ("exe 端到端通过 ✅" if ok else f"exe 端到端失败 ❌（停在：{result['stage']}）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
