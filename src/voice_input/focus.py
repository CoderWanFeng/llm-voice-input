"""FocusTarget 与 ClipboardSnapshot（规矩 R2 / R7 的载体）。

焦点：必须在任何 UI 变化之前抓取，否则浮窗会抢走焦点，导致注入到空气里。
剪贴板：必须完整备份并还原，pyperclip 只存文本会毁掉用户复制的图片。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import win32api
import win32clipboard
import win32con
import win32gui
import win32process

from .models import FocusTarget

logger = logging.getLogger("voice_input.focus")

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008


# --------------------------------------------------------------------------
# 焦点
# --------------------------------------------------------------------------

def capture_focus_target() -> Optional[FocusTarget]:
    """抓取当前前台窗口作为"上屏目的地"快照。"""
    try:
        hwnd = win32gui.GetForegroundWindow()
    except Exception as exc:
        logger.error("获取前台窗口失败：%s", exc)
        return None

    if not hwnd:
        logger.warning("当前没有前台窗口，FocusTarget 为空")
        return None

    thread_id = pid = 0
    try:
        thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        pass

    return FocusTarget(
        hwnd=hwnd,
        thread_id=thread_id,
        pid=pid,
        process_name=_process_name(pid),
        integrity_level=_integrity_level(pid),
    )


def is_window_valid(hwnd: int) -> bool:
    """注入前复核窗口仍然存在（规矩 R12）。"""
    try:
        return bool(win32gui.IsWindow(hwnd))
    except Exception:
        return False


def activate(hwnd: int) -> bool:
    """把焦点还给目标窗口（粘贴前调用）。"""
    if not hwnd:
        return False
    try:
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception as exc:
        logger.warning("恢复前台窗口失败（可能受系统焦点策略限制）：%s", exc)
        return False


def _process_name(pid: int) -> str:
    if not pid:
        return ""
    try:
        handle = win32api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            return win32process.GetModuleFileNameEx(handle, 0)
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return ""


def _integrity_level(pid: int) -> Optional[str]:
    """进程完整性级别：管理员进程之间可能无法注入输入，需要提前知道。"""
    if not pid:
        return None
    try:
        import win32security

        handle = win32api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            token = win32security.OpenProcessToken(handle, _TOKEN_QUERY)
            sid = win32security.GetTokenInformation(
                token, win32security.TokenIntegrityLevel
            )
            return str(sid[0])
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return None


# --------------------------------------------------------------------------
# 剪贴板
# --------------------------------------------------------------------------

class ClipboardSnapshot:
    """剪贴板全格式快照（规矩 R7：成功失败都必须还原）。"""

    def __init__(self) -> None:
        self._items: dict[int, Any] = {}
        self._skipped: list[int] = []

    @property
    def empty(self) -> bool:
        return not self._items

    def capture(self) -> "ClipboardSnapshot":
        try:
            win32clipboard.OpenClipboard()
        except Exception as exc:
            logger.warning("打开剪贴板失败，本次不备份：%s", exc)
            return self

        try:
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break
                try:
                    data = win32clipboard.GetClipboardData(fmt)
                except Exception:
                    self._skipped.append(fmt)
                    continue
                # 句柄型数据（位图等）拿不到可持久化的内容，只能放弃
                if isinstance(data, (str, bytes)):
                    self._items[fmt] = data
                else:
                    self._skipped.append(fmt)
        except Exception as exc:
            logger.warning("枚举剪贴板格式失败：%s", exc)
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

        if self._skipped:
            logger.debug("以下剪贴板格式未能备份：%s", self._skipped)
        return self

    def restore(self) -> bool:
        """还原剪贴板。没有任何可还原内容时返回 False。"""
        if not self._items:
            return False
        try:
            win32clipboard.OpenClipboard()
        except Exception as exc:
            logger.warning("还原剪贴板时打开失败：%s", exc)
            return False

        try:
            win32clipboard.EmptyClipboard()
            for fmt, data in self._items.items():
                try:
                    win32clipboard.SetClipboardData(fmt, data)
                except Exception:
                    logger.debug("剪贴板格式 %s 还原失败，跳过", fmt)
            return True
        except Exception as exc:
            logger.warning("还原剪贴板失败：%s", exc)
            return False
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass


def set_clipboard_text(text: str) -> bool:
    """把待注入文本写入剪贴板（CF_UNICODETEXT）。"""
    for attempt in range(3):  # 剪贴板偶发被占用，重试几次
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.05)
    logger.error("写入剪贴板失败（已重试 3 次）")
    return False
