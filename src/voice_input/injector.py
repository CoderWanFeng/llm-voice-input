"""Injector 注入器：把文本送进目标光标（规矩 R7 / R8 / R12，动作 A8 / A10）。

主策略：剪贴板备份 → 写入文本 → 恢复前台窗口 → SendInput 发 Ctrl+V → 延时 → 还原剪贴板。
不用 pyautogui 的原因：它的按键时序不可控，无法保证"粘贴完成后再还原剪贴板"。
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .constants import PASTE_DELAY_MS, RESTORE_DELAY_MS
from .focus import ClipboardSnapshot, activate, is_window_valid, set_clipboard_text
from .models import FocusTarget

logger = logging.getLogger("voice_input.injector")

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    """必须完整定义：INPUT 的大小由联合体中最大的成员（MOUSEINPUT）决定。"""

    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    """64 位下 sizeof(INPUT) == 40：type(4) + 对齐填充(4) + 联合体(32)。"""

    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


@dataclass(frozen=True)
class InjectResult:
    """注入结果，供上层决定要不要提示用户。"""

    ok: bool
    strategy: str  # paste | uia | clipboard_only | failed
    message: str = ""


class Injector:
    """文本注入器。"""

    def __init__(
        self,
        paste_delay_ms: int = PASTE_DELAY_MS,
        restore_delay_ms: int = RESTORE_DELAY_MS,
        fallback_uia: bool = False,
    ) -> None:
        self.paste_delay_ms = paste_delay_ms
        self.restore_delay_ms = restore_delay_ms
        self.fallback_uia = fallback_uia
        self._schedule = _default_schedule
        # 待还原的剪贴板快照：必须持有强引用，否则定时器回调（绑定方法）会随对象回收而静默失效
        self._pending: list[tuple[ClipboardSnapshot, Callable[[], None]]] = []

    def set_scheduler(self, schedule: Callable[[int, Callable[[], None]], None]) -> None:
        """注入延时调度器（delay_ms, callback）。

        生产环境传 QTimer.singleShot：不阻塞主线程，Ctrl+V 才能被及时处理。
        千万别用 time.sleep 干等——消息积压在队列里，等恢复时剪贴板已被还原。
        """
        self._schedule = schedule

    def inject(self, text: str, target: Optional[FocusTarget] = None) -> InjectResult:
        """执行注入，返回使用的策略。整个过程不阻塞调用线程。"""
        # 规矩 R8：空文本不上屏
        if not text or not text.strip():
            return InjectResult(False, "failed", "文本为空，已跳过注入")

        # 规矩 R12：目标窗口已经没了，只能降级
        if target is None or not is_window_valid(target.hwnd):
            logger.warning("目标窗口无效，降级为仅复制到剪贴板")
            return self._clipboard_only(text)

        snapshot = ClipboardSnapshot().capture()
        try:
            if not set_clipboard_text(text):
                return InjectResult(False, "failed", "无法写入剪贴板")

            activate(target.hwnd)
            _send_ctrl_v()
            logger.info("已发出粘贴指令（%d 字，hwnd=%s）", len(text), target.hwnd)
            return InjectResult(True, "paste", "")
        except Exception as exc:
            logger.error("粘贴注入失败：%s", exc)
            if self.fallback_uia:
                res = self._inject_via_uia(text, target)
                if res.ok:
                    return res
            return InjectResult(False, "failed", str(exc))
        finally:
            # 规矩 R7：无论成败都要还原，但要等目标窗口读完剪贴板再还原
            self._defer_restore(self.paste_delay_ms + self.restore_delay_ms, snapshot)

    def _defer_restore(self, delay_ms: int, snapshot: ClipboardSnapshot) -> None:
        """延时还原剪贴板，期间保持快照强引用。"""

        def _run() -> None:
            try:
                snapshot.restore()
            finally:
                if holder in self._pending:
                    self._pending.remove(holder)

        holder = (snapshot, _run)
        self._pending.append(holder)
        self._schedule(delay_ms, _run)

    # ---------------- 降级策略（动作 A10） ----------------

    def _clipboard_only(self, text: str) -> InjectResult:
        """无有效目标时的最后手段：文本留在剪贴板，提示用户手动粘贴。

        有意不还原剪贴板——此时用户需要这段文本，还原反而让他拿不到。
        """
        if set_clipboard_text(text):
            return InjectResult(False, "clipboard_only", "已复制到剪贴板，请手动 Ctrl+V")
        return InjectResult(False, "failed", "复制到剪贴板失败")

    def _inject_via_uia(self, text: str, target: FocusTarget) -> InjectResult:
        """UIA 兜底：直接写入控件值。

        注意：这是"整段替换"而非"光标处插入"，因此默认关闭，只作为兜底。
        """
        try:
            import uiautomation as auto

            ctrl = auto.ControlFromHandle(target.hwnd)
            if ctrl is None:
                return InjectResult(False, "failed", "UIA 未能定位控件")
            pattern = ctrl.GetValuePattern()
            if pattern is None:
                return InjectResult(False, "failed", "控件不支持 ValuePattern")
            pattern.SetValue(text)
            return InjectResult(True, "uia", "已通过 UIA 写入（整段替换）")
        except Exception as exc:
            return InjectResult(False, "failed", f"UIA 注入失败：{exc}")


def _default_schedule(delay_ms: int, fn: Callable[[], None]) -> None:
    """无调度器时的兜底：后台线程延时执行，绝不占用主线程。"""
    threading.Timer(delay_ms / 1000.0, fn).start()


def _send_ctrl_v() -> None:
    """用 SendInput 发 Ctrl+V，粒度到每个按键事件，时序可控。"""
    _key(VK_CONTROL, up=False)
    _key(VK_V, up=False)
    _key(VK_V, up=True)
    _key(VK_CONTROL, up=True)


def _key(vk: int, up: bool) -> None:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    sent = ctypes.windll.user32.SendInput(
        1, ctypes.byref(inp), ctypes.sizeof(_INPUT)
    )
    if sent != 1:
        raise RuntimeError(f"SendInput 失败（vk={vk}），返回值 {sent}")
    time.sleep(0.01)
