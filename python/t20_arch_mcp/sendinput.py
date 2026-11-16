"""sendinput.py - real keyboard/mouse delivery via SendInput.

Why this exists: PostMessage(WM_KEYDOWN)/(WM_CHAR) is silently dropped by AutoCAD's
accelerator tables and by Tianzheng's message filters, so it cannot drive HZQT's
interactive prompts. SendInput injects at the driver level, which is what an actual
user does, and is the only reliable way to answer a Tianzheng command.

Focus policy (measured the hard way): SendInput reaches only the foreground window.
This module therefore NEVER pulls AutoCAD in front on its own, because a background
agent doing that interrupts the user (it once kept jumping in front of a game).
The caller checks `ensure_focus()` first; when the focus is unavailable the run is
refused with an explanation instead of silently typing into whatever has focus.
`post_escape()` is the exception that works without focus: ESC delivered as window
messages DOES cancel a pending command, so cleanup never disturbs the user.
"""
from __future__ import annotations

import ctypes
import os
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
    """Try to bring a window to the foreground WITHOUT stealing it aggressively.

    Windows refuses SetForegroundWindow from a background process. This used to
    fall back to AttachThreadInput plus a minimise/restore cycle, which made
    AutoCAD visibly pop up and interrupt whatever the user was doing (measured
    complaint: it kept jumping in front of a game). That behaviour is gone:
    unless the caller explicitly allows focus stealing, this never flashes or
    restores the window -- it only succeeds when Windows lets the plain call
    through, and otherwise reports False so the caller can ask the user instead.
    """
    try:
        if user32.SetForegroundWindow(wintypes.HWND(hwnd)):
            return True
    except Exception:
        pass

    if not allow_focus_steal():
        # Respect the user's current foreground window.
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        my_thread = kernel32.GetCurrentThreadId()
        attached = False
        if fg_thread and fg_thread != my_thread:
            attached = bool(user32.AttachThreadInput(fg_thread, my_thread, True))
        try:
            user32.BringWindowToTop(wintypes.HWND(hwnd))
            ok = bool(user32.SetForegroundWindow(wintypes.HWND(hwnd)))
        finally:
            if attached:
                user32.AttachThreadInput(fg_thread, my_thread, False)
        return ok
    except Exception:
        return False


def allow_focus_steal() -> bool:
    """Whether this process may pull AutoCAD in front of the user.

    Off by default: someone running this in the background must not have their
    screen hijacked. Set T20_ALLOW_FOCUS_STEAL=1 to opt in when the plugin is
    driven interactively and taking focus is actually wanted.
    """
    raw = os.environ.get("T20_ALLOW_FOCUS_STEAL", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def ensure_focus(hwnd: int, attempts: int = 3, settle: float = 0.35) -> bool:
    """Focus the window and confirm it really is the foreground window."""
    for _ in range(max(1, attempts)):
        focus_window(hwnd)
        time.sleep(settle)
        try:
            if user32.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass
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


def descendants(hwnd: int) -> list[int]:
    """Every descendant window handle of `hwnd`."""
    out: list[int] = []
    _EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        out.append(int(h))
        return True

    try:
        user32.EnumChildWindows(wintypes.HWND(hwnd), _EnumProc(cb), 0)
    except Exception:
        pass
    return out


def allow_click_to_focus() -> bool:
    """Click-to-focus is DISABLED by default; it was actively harmful.

    MEASURED: clicking inside the drawing area to move the keyboard there makes
    AutoCAD start a window selection ("指定对角点或 [栏选(F)/圈围(WP)/圈交(CP)]").
    The next typed coordinate then became a selection corner instead of a command
    input, and one run even ended up executing `_HELP` (the Help dialog, which is
    modal and blocks every COM call). Focus is now moved with SetFocus on the
    drawing view, which generates no mouse event at all.
    """
    raw = os.environ.get("T20_CLICK_TO_FOCUS", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


_VIEW_CLASS_HINTS = ("FLIP_MODE_VIEW", "ACADDM_CHILD")


def drawing_view_window(hwnd: int) -> int | None:
    """The DirectX drawing-view child window that receives coordinate input."""
    best, best_area = None, 0
    for child in descendants(hwnd):
        try:
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(wintypes.HWND(child), cls, 256)
            if not any(hint in cls.value for hint in _VIEW_CLASS_HINTS):
                continue
            if not user32.IsWindowVisible(wintypes.HWND(child)):
                continue
            rect = wintypes.RECT()
            user32.GetWindowRect(wintypes.HWND(child), ctypes.byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            if area > best_area:
                best, best_area = child, area
        except Exception:
            continue
    return best


def focus_drawing_view(hwnd: int) -> bool:
    """Move the keyboard focus into the drawing view WITHOUT clicking.

    SetFocus only works from the thread owning the window, so this attaches to that
    thread's input queue first. No mouse event is generated, therefore no stray
    selection point and no accidental _HELP.
    """
    try:
        if not is_foreground(hwnd):
            focus_window(hwnd)
            time.sleep(0.2)
    except Exception:
        pass

    view = drawing_view_window(hwnd)
    if not view:
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        target_thread = user32.GetWindowThreadProcessId(wintypes.HWND(view), None)
        my_thread = kernel32.GetCurrentThreadId()
        attached = False
        if target_thread and target_thread != my_thread:
            attached = bool(user32.AttachThreadInput(my_thread, target_thread, True))
        try:
            user32.SetFocus(wintypes.HWND(view))
        finally:
            if attached:
                user32.AttachThreadInput(my_thread, target_thread, False)
        return True
    except Exception:
        return False


def is_foreground(hwnd: int) -> bool:
    try:
        return user32.GetForegroundWindow() == int(hwnd)
    except Exception:
        return False


def type_into_window(hwnd: int, text: str, enter: bool = True) -> bool:
    """Type into a window without losing the first character.

    MEASURED: the FIRST keystroke is frequently swallowed when it is sent right
    after a window activation or a click. That silently corrupted coordinates
    ("0,0" arrived as ",0", "4800,3750" as "800,3750") and made Tianzheng answer
    "需要点或选项关键字" / "选择无效" -- the single biggest source of
    non-deterministic failures in this project.

    The fix is to always pause before typing and to "warm up" the input queue with
    a harmless key (Shift, which no AutoCAD prompt acts on) so the real characters
    are not the first event the window sees.
    """
    need_focus = not is_foreground(hwnd)
    if need_focus:
        focus_window(hwnd)
        time.sleep(0.4)
    time.sleep(0.22)

    # Absorb activation with a no-op keypress before the real text.
    try:
        press_key(0x10, settle=0.05)  # VK_SHIFT
    except Exception:
        pass
    time.sleep(0.05)

    if text:
        type_unicode(text)
        time.sleep(0.1)
    if enter:
        press_key(VK_RETURN)
    return True


def post_escape(hwnd: int, times: int = 2) -> int:
    """Post ESC to a window and all its descendants -- no focus required.

    MEASURED FALLBACK: SendInput only reaches the window that owns the foreground,
    and a background agent often cannot change the foreground at all
    (SetForegroundWindow is refused, AttachThreadInput does not always help). In
    that situation the command loop can still be cancelled by posting keyboard
    messages to the drawing view windows; that is what finally unstuck a hung
    HZQT session here. SendInput is preferred when focus is available because it
    is what a real user produces.
    """
    targets = [int(hwnd)] + descendants(hwnd)
    for target in targets:
        for _ in range(max(1, times)):
            user32.PostMessageW(wintypes.HWND(target), 0x0100, VK_ESCAPE, 0)  # WM_KEYDOWN
            user32.PostMessageW(wintypes.HWND(target), 0x0101, VK_ESCAPE, 0)  # WM_KEYUP
            user32.PostMessageW(wintypes.HWND(target), 0x0102, VK_ESCAPE, 0)  # WM_CHAR
    return len(targets)
