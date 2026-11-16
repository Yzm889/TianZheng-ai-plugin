"""winmsg.py - minimal Win32 window helpers (no external deps).

Used to inspect what AutoCAD is showing (floating Tianzheng panels vs. modal
dialogs) and to answer/cancel a blocking prompt. This matters because Tianzheng
commands are UI-driven: `HZQT` opens its wall panel, and while that panel is up
AutoCAD rejects COM calls with RPC_E_CALL_REJECTED.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
VK_ESCAPE = 0x1B
VK_RETURN = 0x0D

EnumWindowsProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
)


def windows_for_pid(pid: int) -> list[dict]:
    """Every top-level window owned by `pid`, with class and title."""
    out: list[dict] = []

    def _cb(hwnd, _lparam):
        proc_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value != pid:
            return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if title.value:
            out.append(
                {
                    "hwnd": int(hwnd),
                    "class": cls.value,
                    "title": title.value,
                    "visible": bool(user32.IsWindowVisible(hwnd)),
                    "enabled": bool(user32.IsWindowEnabled(hwnd)),
                }
            )
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return out


def find_windows(pid: int, title_contains: str | None = None, window_class: str | None = None) -> list[dict]:
    result = []
    for win in windows_for_pid(pid):
        if title_contains and title_contains.lower() not in win["title"].lower():
            continue
        if window_class and win["class"] != window_class:
            continue
        result.append(win)
    return result


def post_message(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> bool:
    return bool(user32.PostMessageW(wintypes.HWND(hwnd), msg, wparam, lparam))


def post_key(hwnd: int, vk: int) -> None:
    """Post a key press+release to a window."""
    user32.PostMessageW(wintypes.HWND(hwnd), WM_KEYDOWN, vk, 0)
    user32.PostMessageW(wintypes.HWND(hwnd), WM_KEYUP, vk, 0)


def cancel_dialogs(pid: int, titles: tuple[str, ...] = ("天正软件",)) -> list[dict]:
    """ESC/close every visible blocking dialog owned by `pid`.

    Returns what was actioned so the caller can report it honestly.
    """
    actioned: list[dict] = []
    for win in windows_for_pid(pid):
        if win["class"] != "#32770" or not win["visible"]:
            continue
        if titles and not any(t in win["title"] for t in titles):
            continue
        post_key(win["hwnd"], VK_ESCAPE)
        post_message(win["hwnd"], WM_CLOSE)
        actioned.append(win)
    return actioned


def describe_ui(pid: int) -> dict:
    """Summarise visible Tianzheng/AutoCAD UI that could block automation."""
    wins = windows_for_pid(pid)
    dialogs = [w for w in wins if w["class"] == "#32770" and w["visible"]]
    panels = [
        w
        for w in wins
        if w["class"] == "AfxFrameOrView140u" and w["visible"]
    ]
    return {
        "process_id": pid,
        "blocking_dialogs": dialogs,
        "tianzheng_panels": panels,
        "has_blocking_dialog": bool(dialogs),
        "window_count": len(wins),
    }
