"""注入端到端测试：自建输入框当靶子，验证"文字落在光标处 + 剪贴板被还原"。

这是全项目最容易翻车的一环，必须真机验证而不是靠推理。
运行：.venv/Scripts/python.exe tests/inject_test.py
"""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import win32clipboard  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QTextEdit, QVBoxLayout, QWidget  # noqa: E402

from voice_input.focus import capture_focus_target, set_clipboard_text  # noqa: E402
from voice_input.injector import Injector  # noqa: E402

ORIGINAL_CLIPBOARD = "原始剪贴板内容-请勿丢失"
TEXT_TO_INJECT = "语音注入测试"


def read_clipboard() -> str:
    try:
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""


def main() -> int:
    app = QApplication(sys.argv)

    # 1. 准备剪贴板原始内容
    set_clipboard_text(ORIGINAL_CLIPBOARD)
    assert read_clipboard() == ORIGINAL_CLIPBOARD, "前置条件失败：剪贴板未写入"

    # 2. 自建靶子窗口并激活
    win = QWidget()
    win.setWindowTitle("注入测试靶子")
    layout = QVBoxLayout(win)
    edit = QTextEdit()
    edit.setPlainText("AB")
    layout.addWidget(edit)
    win.resize(400, 200)
    win.show()
    win.raise_()
    win.activateWindow()
    app.processEvents()
    time.sleep(0.5)

    # 把光标放到 A 和 B 之间：粘贴后应为 A<注入文本>B
    cursor = edit.textCursor()
    cursor.setPosition(1)
    edit.setTextCursor(cursor)
    edit.setFocus()
    app.processEvents()
    time.sleep(0.2)

    target = capture_focus_target()
    assert target is not None, "未抓到焦点目标"
    print(f"焦点目标：hwnd={target.hwnd} 进程={target.process_name.split(chr(92))[-1]}")

    # 3. 注入（必须跑真正的事件循环，否则 Ctrl+V 消息不会被处理）
    result_holder = {}

    def do_inject() -> None:
        injector = Injector()
        injector.set_scheduler(lambda ms, fn: QTimer.singleShot(ms, fn))
        result_holder["r"] = injector.inject(TEXT_TO_INJECT, target)

    QTimer.singleShot(200, do_inject)
    QTimer.singleShot(2000, app.quit)
    app.exec()

    result = result_holder.get("r")
    assert result is not None, "注入未执行"
    print(f"注入结果：ok={result.ok} strategy={result.strategy} msg={result.message}")

    # 4. 校验文字落在光标处
    content = edit.toPlainText()
    expected = f"A{TEXT_TO_INJECT}B"
    print(f"输入框内容：{content!r}")
    assert content == expected, f"注入位置不对：期望 {expected!r}，实际 {content!r}"

    # 5. 校验剪贴板已还原（规矩 R7）
    restored = read_clipboard()
    print(f"剪贴板内容：{restored!r}")
    assert restored == ORIGINAL_CLIPBOARD, f"剪贴板未还原：{restored!r}"

    print("\n注入端到端测试通过：文字落在光标处，剪贴板完好还原")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
