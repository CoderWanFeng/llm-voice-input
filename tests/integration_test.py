"""集成测试：验证编排链路与浮窗"不抢焦点"（不需要麦克风和真实剪贴板）。

运行：.venv/Scripts/python.exe tests/integration_test.py
"""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from voice_input.app import Orchestrator  # noqa: E402
from voice_input.asr import MockAsrClient  # noqa: E402
from voice_input.config import Config  # noqa: E402
from voice_input.injector import InjectResult  # noqa: E402
from voice_input.models import AppState, AudioClip  # noqa: E402
from voice_input.ui import Overlay  # noqa: E402


class FakeRecorder:
    """假录音器：产出 500ms 的静音片段。"""

    def __init__(self) -> None:
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_open(self) -> bool:
        return True

    def open(self) -> bool:
        return True

    def start(self) -> bool:
        self._recording = True
        return True

    def stop(self) -> AudioClip:
        self._recording = False
        return AudioClip(pcm=b"\x00" * 16000)  # 500ms

    def level(self) -> float:
        return 0.4

    def close(self) -> None:
        pass


class FakeInjector:
    """假注入器：只记录调用，绝不真粘贴。"""

    def __init__(self) -> None:
        self.text = ""
        self.target = None

    def inject(self, text, target) -> InjectResult:
        self.text = text
        self.target = target
        return InjectResult(True, "paste", "")


def wait_until(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.time() + timeout_s
    app = QApplication.instance()
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_orchestration() -> None:
    config = Config(asr_provider="mock", overlay_enabled=False)
    recorder = FakeRecorder()
    injector = FakeInjector()
    overlay = Overlay()

    orch = Orchestrator(config, recorder, MockAsrClient(replies=["集成测试文本"], delay_s=0.05),
                        injector, overlay)

    assert orch.machine.state == AppState.IDLE
    orch.toggle()  # 开始录音
    assert orch.machine.state == AppState.RECORDING, orch.machine.state
    assert orch.session.focus_target is not None, "必须抓到焦点目标"

    orch.toggle()  # 停止 → 识别 → 注入
    assert orch.machine.state == AppState.TRANSCRIBING, orch.machine.state

    ok = wait_until(lambda: orch.machine.state == AppState.IDLE)
    assert ok, f"链路未在超时内完成，停在 {orch.machine.state}"
    assert injector.text == "集成测试文本", injector.text
    print("[OK] 编排链路：IDLE → RECORDING → TRANSCRIBING → INJECTING → IDLE")


def test_overlay_never_activates() -> None:
    """规矩 R10：浮窗显示后不得成为活动窗口。"""
    overlay = Overlay()
    overlay.set_state(AppState.RECORDING, "")
    QApplication.instance().processEvents()

    flags = overlay.windowFlags()
    assert flags & Qt.WindowDoesNotAcceptFocus, "缺少 WindowDoesNotAcceptFocus"
    assert flags & Qt.WindowTransparentForInput, "缺少 WindowTransparentForInput"
    assert flags & Qt.WindowStaysOnTopHint, "缺少 WindowStaysOnTopHint"
    assert not overlay.isActiveWindow(), "浮窗竟然成了活动窗口，会抢走输入焦点"

    overlay.set_state(AppState.IDLE, "")
    print("[OK] 浮窗标志位齐全且未成为活动窗口")


def test_hotkey_registration() -> None:
    """动作 A1：注册失败必须给出可见的原因（R11）。"""
    from voice_input.hotkey import HotkeyManager

    manager = HotkeyManager("ctrl+alt+space", lambda: None, backend="win32")
    ok, msg = manager.register()
    print(f"[{'OK' if ok else 'WARN'}] 热键注册：{msg}")
    if ok:
        manager.unregister()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    test_overlay_never_activates()
    test_orchestration()
    test_hotkey_registration()
    print("\n全部集成测试通过")
