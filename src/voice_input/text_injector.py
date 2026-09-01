"""文本注入模块。

对应 macOS 原项目 TextInjector.swift：
1. 备份当前剪贴板
2. 写入识别文本
3. 等 150ms
4. SendInput 模拟 Ctrl down / V down / V up / Ctrl up
5. 等 1.5s 恢复原剪贴板内容
6. 失败时（高完整性目标进程阻断）退化为提示手动 Ctrl+V

Windows 上无"辅助功能权限"概念，普通权限即可发送按键；
但目标进程若以管理员权限运行（UIPI），普通权限进程模拟按键会被阻断。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Optional

from .diag_log import DiagLog


# ===== Win32 API 类型与常量 =====

# SendInput 输入类型：键盘
INPUT_KEYBOARD = 1
# 键盘事件标志
KEYEVENTF_KEYUP = 0x0002  # 按键释放

# 虚拟键码
VK_CONTROL = 0x11  # Ctrl
VK_V = 0x56        # V

# 剪贴板格式
CF_UNICODETEXT = 13


# ===== 结构体定义 =====

class MOUSEINPUT(ctypes.Structure):
    """鼠标输入结构（SendInput 用，本文不使用但需占位）。"""
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class KEYBDINPUT(ctypes.Structure):
    """键盘输入结构。"""
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class HARDWAREINPUT(ctypes.Structure):
    """硬件输入结构（占位）。"""
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    """输入数据联合体。"""
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    """SendInput 单条输入结构。"""
    _fields_ = [
        ("type", wintypes.DWORD),
        ("ii", _INPUT_UNION),
    ]


# 加载 user32.dll 与 kernel32.dll
_user32 = ctypes.windll.user32
# GlobalLock / GlobalUnlock / GlobalAlloc 属于 kernel32，不在 user32
_kernel32 = ctypes.windll.kernel32

# ---- 声明 Win32 API 签名（64 位系统上句柄/指针是 8 字节，
#      不声明 restype 默认按 c_int 截断，导致句柄失效/随机失败）----
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SendInput.restype = wintypes.UINT
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_kernel32.GlobalAlloc.restype = wintypes.HANDLE
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]


def _make_key_input(vk: int, up: bool) -> INPUT:
    """构造一次按键 INPUT 结构。up=True 表示释放。"""
    flags = KEYEVENTF_KEYUP if up else 0
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ii.ki.wVk = vk
    inp.ii.ki.wScan = 0
    inp.ii.ki.dwFlags = flags
    inp.ii.ki.time = 0
    inp.ii.ki.dwExtraInfo = None
    return inp


def _send_inputs(*inputs: INPUT) -> int:
    """调用 SendInput 发送多条输入，返回成功发送数。"""
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    return int(_user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT)))


def _get_clipboard_text() -> str:
    """读取当前剪贴板 Unicode 文本（失败返回空字符串）。"""
    try:
        if not _user32.OpenClipboard(0):
            return ""
        try:
            handle = _user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = _kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                # 按 wide-char 读取直到 NUL
                raw = ctypes.wstring_at(ptr)
                return raw
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()
    except Exception as e:
        DiagLog.shared().write(f"[Inject] 读取剪贴板异常: {e}")
        return ""


def _set_clipboard_text(text: str) -> bool:
    """写入 Unicode 文本到剪贴板，返回是否成功。"""
    # 写入需要进程拥有剪贴板所有权，EmptyClipboard 后 SetClipboardData
    GMEM_MOVEABLE = 0x0002
    try:
        if not _user32.OpenClipboard(0):
            DiagLog.shared().write("[Inject] OpenClipboard 失败")
            return False
        try:
            _user32.EmptyClipboard()
            # 长度（含末尾 NUL，按字符算）
            text_with_nul = text + "\x00"
            byte_len = len(text_with_nul) * 2  # UTF-16 字节数
            # 分配全局内存
            handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, byte_len)
            if not handle:
                return False
            ptr = _kernel32.GlobalLock(handle)
            if not ptr:
                return False
            try:
                ctypes.memmove(ptr, text_with_nul.encode("utf-16-le"), byte_len)
            finally:
                _kernel32.GlobalUnlock(handle)
            _user32.SetClipboardData(CF_UNICODETEXT, handle)
            # 系统接管内存，无需 GlobalFree
            return True
        finally:
            _user32.CloseClipboard()
    except Exception as e:
        DiagLog.shared().write(f"[Inject] 写剪贴板异常: {e}")
        return False


def inject(text: str) -> None:
    """注入文本：写剪贴板 + 模拟 Ctrl+V 粘贴。"""
    if not text:
        return
    DiagLog.shared().write(f"[Inject] 准备注入文本: {text[:50]}")

    # 1. 备份当前剪贴板内容
    previous = _get_clipboard_text()
    DiagLog.shared().write(f"[Inject] 原剪贴板内容长度: {len(previous)}")

    # 2. 写入新文本
    if not _set_clipboard_text(text):
        DiagLog.shared().write("[Inject] ❌ 写入剪贴板失败")
        return
    DiagLog.shared().write("[Inject] ✅ 剪贴板已写入")

    # 3. 等 150ms 让剪贴板与目标应用就绪
    time.sleep(0.15)

    # 4. 模拟 Ctrl+V
    _simulate_paste()

    # 5. 等 1.5s 后恢复原剪贴板（避免污染用户剪贴板）
    time.sleep(1.5)
    if previous:
        _set_clipboard_text(previous)
        DiagLog.shared().write("[Inject] 已恢复原剪贴板")


def _simulate_paste() -> None:
    """模拟 Ctrl+V 按键序列。"""
    # 顺序：Ctrl down -> V down -> sleep 10ms -> V up -> Ctrl up
    _send_inputs(_make_key_input(VK_CONTROL, up=False))
    _send_inputs(_make_key_input(VK_V, up=False))
    time.sleep(0.01)
    _send_inputs(_make_key_input(VK_V, up=True))
    _send_inputs(_make_key_input(VK_CONTROL, up=True))
    DiagLog.shared().write("[Inject] ✅ 粘贴事件已发送")
