"""sendinput.py - real keyboard/mouse delivery via SendInput.

Why this exists: PostMessage(WM_KEYDOWN) gets silently dropped by AutoCAD's
accelerator tables and by Tianzheng's own message filters, so it cannot drive
HZQT's interactive prompts. SendInput injects at the driver level, which is what
an actual user does, and is the only reliable way to answer a Tianzheng command.

Safety: callers must focus the target window first; these helpers do that.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001

VK_ESCAPE = 0x1B
VK_RETURN = 0x0D


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def focus_window(hwnd: int) -> bool:
    """Bring a window to the foreground (needed before SendInput)."""
    try:
        user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
        return bool(user32.SetForegroundWindow(wintypes.HWND(hwnd)))
    except Exception:
        return False


def _send(inputs: list[_INPUT]) -> int:
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    return int(user32.SendInput(n, arr, ctypes.sizeof(_INPUT)))


def _key_input(vk: int, up: bool) -> _INPUT:
    item = _INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
    return item


def press_key(vk: int, settle: float = 0.06) -> None:
    _send([_key_input(vk, False)])
    time.sleep(0.02)
    _send([_key_input(vk, True)])
    time.sleep(settle)


def type_unicode(text: str, settle: float = 0.02) -> None:
    """Type arbitrary text as unicode (works for letters, digits, symbols)."""
    for ch in text:
        item = _INPUT()
        item.type = INPUT_KEYBOARD
        item.ki = _KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, None)
        item2 = _INPUT()
        item2.type = INPUT_KEYBOARD
        item2.ki = _KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
        _send([item, item2])
        time.sleep(settle)


def type_line(text: str, settle: float = 0.06) -> None:
    """Type a line of text and press Enter."""
    type_unicode(text)
    press_key(VK_RETURN, settle)


def send_escape(times: int = 1, settle: float = 0.15) -> None:
    for _ in range(times):
        press_key(VK_ESCAPE, settle)


def screen_size() -> tuple[int, int]:
    return (
        int(user32.GetSystemMetrics(0)),
        int(user32.GetSystemMetrics(1)),
    )


def click(x: int, y: int, settle: float = 0.15) -> None:
    """Absolute click at screen coordinates."""
    def _move():
        item = _INPUT()
        item.type = INPUT_MOUSE
        item.mi = _MOUSEINPUT(0, 0, 0, MOUSEEVENTF_MOVE, 0, None)
        return item

    # Absolute coordinates require normalising to 0..65535.
    w, h = screen_size()
    nx = int(x * 65535 / max(w - 1, 1))
    ny = int(y * 65535 / max(h - 1, 1))
    move = _INPUT()
    move.type = INPUT_MOUSE
    move.mi = _MOUSEINPUT(nx, ny, 0, MOUSEEVENTF_MOVE | 0x8000 | 0x4000, 0, None)
    down = _INPUT()
    down.type = INPUT_MOUSE
    down.mi = _MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
    up = _INPUT()
    up.type = INPUT_MOUSE
    up.mi = _MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
    _send([move])
    time.sleep(0.03)
    _send([down])
    time.sleep(0.03)
    _send([up])
    time.sleep(settle)


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Client area rect in screen coordinates: (left, top, right, bottom)."""
    try:
        rect = wintypes.RECT()
        if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt))
        return pt.x, pt.y, pt.x + rect.right, pt.y + rect.bottom
    except Exception:
        return None
