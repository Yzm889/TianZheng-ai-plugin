"""dialog.py - drive Tianzheng's native Win32 dialogs from outside AutoCAD.

Why this is unavoidable
-----------------------
Several Tianzheng commands (MC for doors/windows, HZZW for axis grids, ...) open a
native Win32 dialog *before* asking for points. While that dialog is open AutoCAD
answers every COM call with RPC_E_CALL_REJECTED, so the dialog cannot be handled
through COM at all. It has to be driven with Win32 messages, exactly like a user.

Measured flow for MC (T30-Arch V2.0, AutoCAD 2022):

    MC -> dialog '门窗参数' (edits: 编号 / 门高 / 门宽 / 门槛高 / 距离 / 个数)
       -> 确定
       -> 点取门窗插入位置(Shift-左右开)<退出>
       -> <x,y>          (snaps onto the nearest Tianzheng wall)
       -> loops for more openings
       -> ESC

This module only knows how to find such a dialog, set edit values, and press a
button. Command semantics stay in the runner/catalog.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
BM_CLICK = 0x00F5
WM_GETTEXTLENGTH = 0x000E

DLG_CLASS = "#32770"

_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def top_windows(pid: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def cb(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
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
                    }
                )
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return out


def child_controls(hwnd: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def cb(h, _lparam):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls, 256)
        text = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, text, 512)
        out.append(
            {
                "hwnd": int(h),
                "class": cls.value,
                "text": text.value,
                "id": int(user32.GetDlgCtrlID(h)),
                "visible": bool(user32.IsWindowVisible(h)),
                "enabled": bool(user32.IsWindowEnabled(h)),
            }
        )
        return True

    user32.EnumChildWindows(wintypes.HWND(hwnd), _EnumProc(cb), 0)
    return out


def find_dialog(pid: int, title_contains: str) -> dict[str, Any] | None:
    """Find a visible dialog of this process whose title contains the text."""
    for win in top_windows(pid):
        if win["class"] == DLG_CLASS and win["visible"] and title_contains in win["title"]:
            return win
    return None


def wait_for_dialog(pid: int, title_contains: str, timeout: float = 8.0, poll: float = 0.25) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        dlg = find_dialog(pid, title_contains)
        if dlg:
            return dlg
        time.sleep(poll)
    return None


def read_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXT, 512, ctypes.byref(buf))
    return buf.value


def set_text(hwnd: int, value: Any) -> bool:
    buf = ctypes.create_unicode_buffer(str(value))
    return bool(user32.SendMessageW(wintypes.HWND(hwnd), WM_SETTEXT, 0, ctypes.byref(buf)))


def click_button(hwnd: int) -> bool:
    return bool(user32.SendMessageW(wintypes.HWND(hwnd), BM_CLICK, 0, 0))


def find_button(dlg_hwnd: int, text: str) -> dict[str, Any] | None:
    """Find a visible button whose caption contains `text` (case-insensitive)."""
    wanted = (text or "").strip().lower()
    for ctrl in child_controls(dlg_hwnd):
        if ctrl["class"] != "Button" or not ctrl["visible"]:
            continue
        if wanted and wanted in (ctrl["text"] or "").strip().lower():
            return ctrl
    return None


def click_button_text(dlg_hwnd: int, text: str) -> bool:
    """Click a button by its caption, e.g. 插入 / A3 / 确定."""
    ctrl = find_button(dlg_hwnd, text)
    if not ctrl:
        return False
    return click_button(ctrl["hwnd"])


def window_center(hwnd: int) -> tuple[int, int] | None:
    """Screen coordinates of a control's centre."""
    try:
        rect = wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    except Exception:
        return None


def click_button_text_real(
    dlg_hwnd: int,
    text: str,
    verify_dialog_closes: bool = False,
    wait: float = 1.5,
) -> dict[str, Any]:
    """Click a dialog button with a REAL mouse click.

    MEASURED: the 插入 button of Tianzheng's 插入图框 dialog ignored both
    WM_COMMAND and BM_CLICK -- the dialog just sat there until a human clicked it.
    Tianzheng's custom controls do not expose a usable automation interface, so the
    reliable way is a genuine SendInput click on the button's screen position
    (which also requires the dialog to be in the foreground).
    """
    ctrl = find_button(dlg_hwnd, text)
    if not ctrl:
        return {"clicked": False, "reason": f"找不到按钮 {text!r}"}

    center = window_center(ctrl["hwnd"])
    if not center:
        return {"clicked": False, "reason": "按钮没有可见矩形"}

    from . import sendinput  # local import: avoids a cycle at module load

    sendinput.click(center[0], center[1], settle=0.35)
    closed = not bool(user32.IsWindowVisible(wintypes.HWND(dlg_hwnd)))
    if verify_dialog_closes and not closed:
        time.sleep(wait)
        closed = not bool(user32.IsWindowVisible(wintypes.HWND(dlg_hwnd)))
    return {
        "clicked": True,
        "button": ctrl["text"] or text,
        "at": center,
        "dialog_closed": closed,
    }


def dialog_visible(hwnd: int) -> bool:
    try:
        return bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))
    except Exception:
        return False


def send_command(hwnd: int, ctrl_id: int) -> bool:
    return bool(user32.SendMessageW(wintypes.HWND(hwnd), WM_COMMAND, ctrl_id, 0))


def visible_edits(dlg_hwnd: int) -> list[dict[str, Any]]:
    return [c for c in child_controls(dlg_hwnd) if c["class"] == "Edit" and c["visible"]]


def describe(dlg_hwnd: int) -> dict[str, Any]:
    """Report a dialog's editable fields and buttons, for diagnostics."""
    kids = child_controls(dlg_hwnd)
    return {
        "edits": [
            {"index": i, "text": read_text(e["hwnd"])}
            for i, e in enumerate([k for k in kids if k["class"] == "Edit" and k["visible"]])
        ],
        "buttons": [
            {"id": k["id"], "text": k["text"], "visible": k["visible"]}
            for k in kids
            if k["class"] == "Button"
        ],
    }


def fill_and_accept(
    pid: int,
    title_contains: str,
    edit_values: dict[int, Any] | None = None,
    timeout: float = 8.0,
    accept: bool = True,
) -> dict[str, Any]:
    """Wait for a Tianzheng dialog, set its edit fields, then press 确定.

    edit_values maps the *visible* edit index to the value to type:
    for 门窗参数 the measured order is
        0=编号 1=门高 2=门宽 3=门槛高 4=距离 5=个数
    so setting the width is {2: 900}.
    """
    dlg = wait_for_dialog(pid, title_contains, timeout=timeout)
    if not dlg:
        return {
            "found": False,
            "title": title_contains,
            "note": "对话框未出现；该命令可能无需参数框，或版本不同。",
        }

    report: dict[str, Any] = {"found": True, "hwnd": dlg["hwnd"], "title": dlg["title"], "applied": []}
    edits = visible_edits(dlg["hwnd"])
    report["edit_count"] = len(edits)

    for index, value in (edit_values or {}).items():
        if 0 <= index < len(edits):
            ok = set_text(edits[index]["hwnd"], value)
            report["applied"].append(
                {"index": index, "value": value, "ok": ok, "now": read_text(edits[index]["hwnd"])}
            )
        else:
            report["applied"].append({"index": index, "value": value, "ok": False, "error": "index out of range"})

    if accept:
        buttons = {c["id"]: c for c in child_controls(dlg["hwnd"]) if c["class"] == "Button"}
        pressed = None
        ok_btn = buttons.get(1) or next(
            (b for b in buttons.values() if "确" in b["text"]), None
        )
        if ok_btn:
            pressed = click_button(ok_btn["hwnd"])
            if not pressed:
                # Skinned dialogs may not answer BM_CLICK; fall back to IDOK.
                pressed = send_command(dlg["hwnd"], 1)
        report["accepted"] = bool(pressed)
        # Confirm the dialog actually went away.
        time.sleep(0.4)
        report["closed"] = find_dialog(pid, title_contains) is None
        if not report["closed"]:
            report["fallback"] = send_command(dlg["hwnd"], 1)
            time.sleep(0.4)
            report["closed"] = find_dialog(pid, title_contains) is None
    return report


def dismiss(pid: int, title_contains: str, cancel_id: int = 2) -> bool:
    dlg = find_dialog(pid, title_contains)
    if not dlg:
        return True
    send_command(dlg["hwnd"], cancel_id)
    time.sleep(0.3)
    if find_dialog(pid, title_contains) is None:
        return True
    user32.PostMessageW(wintypes.HWND(dlg["hwnd"]), WM_CLOSE, 0, 0)
    time.sleep(0.3)
    return find_dialog(pid, title_contains) is None
