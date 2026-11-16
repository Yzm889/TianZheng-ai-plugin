"""winmsg.py - minimal Win32 window helpers (no external deps).

Used to inspect what AutoCAD is showing (floating Tianzheng panels vs. modal
dialogs) and to answer/cancel a blocking prompt. This matters because Tianzheng
commands are UI-driven: `HZQT` opens its wall panel, and while that panel is up
AutoCAD rejects COM calls with RPC_E_CALL_REJECTED.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)


def enable_dpi_awareness() -> str:
    """Declare this process DPI-aware so window rects match SendInput coordinates.

    MEASURED: on a 125% display an unaware process receives virtualised window
    rectangles. GetWindowRect reported (353,214,991,478) for a drawing view whose
    real position was (441,267,1239,597) -- exactly 1.25x off -- so clicks computed
    from those numbers landed ~79 px away from the intended target.
    """
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        if shcore.SetProcessDpiAwareness(2) == 0:  # PER_MONITOR_DPI_AWARE
            return "per-monitor"
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "unaware"


DPI_MODE = enable_dpi_awareness()

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


# --------------------------------------------------------------------------- #
# Command-line progress probe
# --------------------------------------------------------------------------- #
_TEXT_HWND_CACHE: dict[int, int] = {}
WM_GETTEXTLENGTH = 0x000E


def _text_window_hwnd(pid: int) -> int | None:
    """Locate (and cache) the edit control inside AutoCAD's F2 text window."""
    cached = _TEXT_HWND_CACHE.get(pid)
    if cached:
        try:
            if user32.IsWindow(wintypes.HWND(cached)):
                return cached
        except Exception:
            pass
        _TEXT_HWND_CACHE.pop(pid, None)

    target = None
    for win in windows_for_pid(pid):
        if "文本窗口" not in win["title"]:
            continue
        best_area, best_hwnd = -1, None
        for hwnd in [win["hwnd"]] + descendants_of(win["hwnd"]):
            try:
                n = int(user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXTLENGTH, 0, 0))
            except Exception:
                continue
            if n > best_area:
                best_area, best_hwnd = n, hwnd
        if best_hwnd:
            target = best_hwnd
            break
    if target:
        _TEXT_HWND_CACHE[pid] = target
    return target


def descendants_of(hwnd: int) -> list[int]:
    out: list[int] = []
    _P = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        out.append(int(h))
        return True

    try:
        user32.EnumChildWindows(wintypes.HWND(hwnd), _P(cb), 0)
    except Exception:
        pass
    return out


def command_output_size(pid: int) -> int:
    """Size of AutoCAD's command-line history text.

    Growth of this number means AutoCAD printed something new (an echo, a prompt,
    an error). Polling a single integer is far cheaper than re-reading the text,
    so it can be used to wait for each interactive step instead of sleeping for a
    fixed time -- which is what makes timing-sensitive Tianzheng commands reliable.
    """
    hwnd = _text_window_hwnd(pid)
    if not hwnd:
        return -1
    try:
        return int(user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXTLENGTH, 0, 0))
    except Exception:
        return -1


def command_tail(pid: int, chars: int = 200) -> str:
    """Return the last `chars` characters of AutoCAD's command-line history.

    Reading the text (not just its length) lets a driver branch on the *actual*
    prompt. Measured: ZWBZ only asks "是否为该对象?[是(Y)/否(N)]" when the picked
    point is ambiguous, so a fixed input sequence is wrong by construction.
    """
    hwnd = _text_window_hwnd(pid)
    if not hwnd:
        return ""
    try:
        n = int(user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXTLENGTH, 0, 0))
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.SendMessageW(wintypes.HWND(hwnd), 0x000D, n + 1, ctypes.byref(buf))
        text = buf.value
        return text[-chars:] if chars > 0 else text
    except Exception:
        return ""


def wait_for_text(
    pid: int,
    needles: tuple[str, ...],
    timeout: float = 8.0,
    poll: float = 0.15,
) -> str | None:
    """Wait until one of `needles` appears in the command tail; return it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        tail = command_tail(pid, 400)
        for needle in needles:
            if needle in tail:
                return needle
        time.sleep(poll)
    return None


def wait_for_output(
    pid: int,
    previous_size: int,
    timeout: float = 8.0,
    poll: float = 0.12,
    settle: float = 0.25,
) -> dict:
    """Block until AutoCAD's command output grows past `previous_size`."""
    import time as _time

    started = _time.time()
    if previous_size < 0:
        _time.sleep(settle)
        return {"changed": True, "reason": "no-probe", "elapsed": 0.0}
    while _time.time() - started < timeout:
        size = command_output_size(pid)
        if size > previous_size:
            _time.sleep(settle)
            return {
                "changed": True,
                "grew_by": size - previous_size,
                "elapsed": round(_time.time() - started, 2),
            }
        _time.sleep(poll)
    return {
        "changed": False,
        "elapsed": round(_time.time() - started, 2),
        "reason": "timeout",
    }
