"""Hotkey 全局热键（动作 A1 / A2）。

主方案用 pywin32 的 RegisterHotKey：OS 级注册，**冲突时会明确报错**，
能满足规矩 R11（注册失败必须可见）。pynput 走低级钩子，冲突时静默失效，只作备胎。
"""

from __future__ import annotations

import ctypes
import logging
from typing import Callable, Optional

import win32con
import win32gui

logger = logging.getLogger("voice_input.hotkey")

WM_HOTKEY = 0x0312
HOTKEY_ID = 1
MOD_NOREPEAT = 0x4000  # 按住时不自动重复（Windows 7+）

_MOD_MAP = {
    "ctrl": win32con.MOD_CONTROL,
    "control": win32con.MOD_CONTROL,
    "alt": win32con.MOD_ALT,
    "shift": win32con.MOD_SHIFT,
    "win": win32con.MOD_WIN,
    "super": win32con.MOD_WIN,
}


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_ulong),
        ("pt", _POINT),
    ]


def parse_combo(combo: str) -> tuple[int, int]:
    """把 "ctrl+alt+space" 解析成 (modifiers, vk)。无法识别时抛 ValueError。"""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("热键组合为空")

    mods = 0
    key = parts[-1]
    for p in parts[:-1]:
        if p not in _MOD_MAP:
            raise ValueError(f"未知修饰键：{p}")
        mods |= _MOD_MAP[p]

    vk = _key_to_vk(key)
    return mods, vk


def _key_to_vk(key: str) -> int:
    if len(key) == 1:
        code = ord(key.upper())
        if 0x30 <= code <= 0x5A:  # 数字与字母直接对应虚拟键码
            return code
        raise ValueError(f"不支持的按键：{key}")
    named = getattr(win32con, f"VK_{key.upper()}", None)
    if named is None:
        raise ValueError(f"不支持的按键：{key}")
    return named


class HotkeyManager:
    """注册 / 注销全局热键，并把触发回调出来。"""

    def __init__(self, combo: str, on_trigger: Callable[[], None], backend: str = "win32") -> None:
        self.combo = combo
        self.on_trigger = on_trigger
        self.backend = backend
        self._hwnd: Optional[int] = None
        self._mods = 0
        self._vk = 0
        self._registered = False
        self._filter = None
        self._listener = None

    def attach_window(self, hwnd: int) -> None:
        """提供一个隐藏窗口句柄用于接收 WM_HOTKEY（比 NULL 更可靠）。"""
        self._hwnd = int(hwnd)

    def register(self) -> tuple[bool, str]:
        """注册热键。返回 (是否成功, 说明)。失败原因必须对用户可见（R11）。"""
        try:
            mods, vk = parse_combo(self.combo)
        except ValueError as exc:
            return False, f"热键格式无效：{exc}"
        self._mods, self._vk = mods, vk

        if self.backend == "pynput":
            return self._register_pynput()
        return self._register_win32(mods, vk)

    def _register_win32(self, mods: int, vk: int) -> tuple[bool, str]:
        try:
            # MOD_NOREPEAT：按住时不要自动重复发 WM_HOTKEY（Win7+ 支持）
            win32gui.RegisterHotKey(self._hwnd, HOTKEY_ID, mods | MOD_NOREPEAT, vk)
        except Exception as exc:
            # 1409 = HOTKEY_ALREADY_REGISTERED
            return False, f"热键 {self.combo} 注册失败（可能被其他程序占用）：{exc}"

        self._registered = True
        logger.info("热键已注册：%s", self.combo)
        return True, f"热键 {self.combo} 已生效"

    def _register_pynput(self) -> tuple[bool, str]:
        try:
            from pynput import keyboard
        except Exception as exc:
            return False, f"pynput 不可用：{exc}"

        combo = "<" + "+<".join(p.strip() for p in self.combo.split("+") if p.strip()) + ">"
        try:
            self._listener = keyboard.GlobalHotKeys({combo: self.on_trigger})
            self._listener.start()
        except Exception as exc:
            return False, f"热键注册失败：{exc}"
        self._registered = True
        return True, f"热键 {self.combo} 已生效（pynput）"

    def install_event_filter(self, app) -> None:
        """把 WM_HOTKEY 接到 Qt 的消息循环上（仅 win32 后端需要）。"""
        if self.backend != "win32" or not self._registered:
            return
        try:
            from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
        except Exception:
            return

        class _Filter(QAbstractNativeEventFilter):
            """把 WM_HOTKEY 变成"一次按键 = 一次触发"。

            坑：Windows 在**按下和松开时都会发** WM_HOTKEY，且不按 MOD_NOREPEAT 时
            按住还会自动重复。实测三条消息会导致"按一下 = 立即开始又立即结束"。
            判据不能用 GetAsyncKeyState（松开那条读到的仍是按下状态），
            所以改成"边沿触发 + 等按键真正松开才重新武装"。
            """

            POLL_MS = 40
            MAX_POLLS = 75  # 最多等 3 秒，防止轮询永久挂着

            def __init__(self, cb: Callable[[], None], vk: int) -> None:
                super().__init__()
                self._cb = cb
                self._vk = vk
                self._armed = True
                self._polls = 0
                self._poll = QTimer()
                self._poll.setInterval(self.POLL_MS)
                self._poll.timeout.connect(self._check_released)

            def nativeEventFilter(self, eventType, message):  # noqa: N802
                try:
                    if eventType in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                        msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
                        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                            if not self._armed:
                                logger.debug("忽略重复 WM_HOTKEY（等待松开中）")
                                return False
                            self._armed = False
                            self._polls = 0
                            self._poll.start()
                            logger.debug("WM_HOTKEY 已触发，等待按键松开")
                            self._cb()
                except Exception:
                    pass
                return False

            def _check_released(self) -> None:
                """按键松开（或超时）后重新武装，允许下一次触发。"""
                self._polls += 1
                down = bool(ctypes.windll.user32.GetAsyncKeyState(self._vk) & 0x8000)
                if not down or self._polls >= self.MAX_POLLS:
                    self._poll.stop()
                    self._armed = True
                    logger.debug("已重新武装（松开=%s，轮询 %d 次）", not down, self._polls)

        self._filter = _Filter(self.on_trigger, self._vk)
        app.installNativeEventFilter(self._filter)

    def unregister(self) -> None:
        if not self._registered:
            return
        try:
            if self.backend == "win32":
                win32gui.UnregisterHotKey(self._hwnd, HOTKEY_ID)
            elif self._listener is not None:
                self._listener.stop()
        except Exception as exc:
            logger.debug("注销热键时出错：%s", exc)
        self._registered = False
