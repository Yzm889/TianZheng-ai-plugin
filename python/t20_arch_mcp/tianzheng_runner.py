"""tianzheng_runner.py - issue Tianzheng commands and verify via entity counts.

Design notes that drove this implementation (learned the hard way on T20-Arch V10.0):

1. `SendCommand` is *synchronous* and *blocks* while a Tianzheng UI is up.
   `HZQT` raises the Tianzheng wall panel, so a synchronous SendCommand call never
   returns and the whole COM worker stalls. Therefore the default path uses
   `SendStringToExecute(..., activate=True)` which returns immediately, and we
   then POLL the entity count until it stabilises.

2. Tianzheng command success is never reported by COM. The only objective
   evidence is that ModelSpace gained a TCH_* entity. Every call compares counts.

3. Tianzheng asks for its parameters interactively on the command line, but on
   many T20 builds it *also* opens a panel. Both paths are supported:
     - `inputs` are typed at the command line;
     - `auto_dismiss_ui` posts ESC/close to a blocking dialog if one appears.

4. AutoCAD rejects COM while not quiescent (RPC_E_CALL_REJECTED). Polling code
   must retry rather than treat that as failure.
"""
from __future__ import annotations

import os
import time
from typing import Any, Sequence

from . import dialog, sendinput, winmsg
from .acad_conn import (
    AcadError,
    _active_document,
    _attach_app,
    _com,
    allow_foreign_document,
    call_with_retry,
    doc_name,
    is_owned_document,
    wait_until_quiescent as _wait_quiescent,
)
from .nl_catalog import suggest_verify_type
from .tianzheng_query import _matches, _object_name, normalize_type

DEFAULT_TIMEOUT = 20.0


def app_process_id(app: Any) -> int | None:
    """Find the PID hosting this COM object, via its window title lookup."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = int(app.HWND)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value) or None
    except Exception:
        return None


def _count_type(doc: Any, entity_type: str, layer: str | None = None) -> int:
    """Count entities of a type, optionally restricted to one layer.

    The layer filter matters for axis grids: Tianzheng draws axes as ordinary
    LINE entities on the DOTE layer, so counting plain LINEs would match unrelated
    geometry. Restricting to DOTE is what makes the axis check meaningful.
    """
    try:
        msp = doc.ModelSpace
        total = int(msp.Count)
    except Exception:
        return 0
    count = 0
    want_layer = str(layer).strip().lower() if layer else None
    for index in range(total):
        try:
            entity = msp.Item(index)
        except Exception:
            continue
        if not _matches(entity_type, _object_name(entity)):
            continue
        if want_layer:
            try:
                if str(entity.Layer).strip().lower() != want_layer:
                    continue
            except Exception:
                continue
        count += 1
    return count


def _histogram(doc: Any) -> dict[str, int]:
    seen: dict[str, int] = {}
    try:
        msp = doc.ModelSpace
        total = int(msp.Count)
    except Exception:
        return seen
    for index in range(total):
        try:
            entity = msp.Item(index)
        except Exception:
            continue
        name = _object_name(entity)
        key = normalize_type(name) if name else "<unknown>"
        seen[key] = seen.get(key, 0) + 1
    return seen


def _count_observed(doc: Any, entity_type: str | None, layer: str | None = None) -> int:
    """Count the proof entity, or all ModelSpace entities when the type is unknown.

    Commands such as HZZW may create a type we have not catalogued yet.  Falling
    back to the ModelSpace total is honest (we say what we measured in
    `verify_mode`) and far better than comparing against the command name,
    which could never match and reported a guaranteed false failure.
    """
    if entity_type:
        return _count_type(doc, entity_type, layer)
    try:
        return int(call_with_retry(lambda: doc.ModelSpace.Count, attempts=3, delay=0.25))
    except Exception:
        return 0


def _last_prompt(doc: Any) -> str | None:
    for var in ("LASTPROMPT",):
        try:
            value = doc.GetVariable(var)
        except Exception:
            continue
        if value:
            return str(value)
    return None


def _safe_doc_name(doc: Any) -> str | None:
    """Read doc.Name defensively: AutoCAD may reject while busy."""
    try:
        return str(call_with_retry(lambda: doc.Name, attempts=3, delay=0.25))
    except Exception:
        return None


def terminate_active_command(app: Any, pid: int, attempts: int = 3) -> dict[str, Any]:
    """Get AutoCAD back to a quiescent state after an interactive command.

    Tianzheng commands (HZQT especially) keep asking for further input after the
    first object is drawn. Until that loop ends, AutoCAD answers every COM call
    with RPC_E_CALL_REJECTED, which looks exactly like a frozen application.

    CRITICAL: PostMessage(WM_KEYDOWN) is ignored by AutoCAD's accelerator tables
    and by Tianzheng's message filters. Only driver-level SendInput actually
    cancels the loop, so that is what we use, after focusing the window.
    """
    report: dict[str, Any] = {"attempts": 0, "quiescent": None, "method": None}
    try:
        hwnd = int(app.HWND)
    except Exception:
        hwnd = None

    for i in range(attempts):
        report["attempts"] = i + 1
        if hwnd:
            try:
                # Prefer window messages: posting ESC needs no foreground and
                # produces no real keystroke. That matters beyond focus -- a
                # computer-use helper (e.g. a desktop-automation plugin) may bind
                # Esc to its own "stop" shortcut, so a SendInput ESC silently
                # aborts the agent's desktop session. Measured: two runs were
                # killed exactly this way. Only fall back to SendInput if posting
                # does not take effect.
                targets = sendinput.post_escape(hwnd, 2)
                report["method"] = f"PostMessage->{targets} windows"
            except Exception:
                pass
        if not report.get("method"):
            try:
                app.ActiveDocument.SendCommand("\x1b\x1b")
                report["method"] = "SendCommand"
            except Exception:
                pass

        time.sleep(0.8)
        try:
            if app.GetAcadState().IsQuiescent:
                report["quiescent"] = True
                return report
        except Exception:
            continue
        # Still busy: now try a real keystroke (only if nothing else worked).
        if hwnd and i == attempts - 1:
            try:
                if sendinput.ensure_focus(hwnd, attempts=1, settle=0.2):
                    sendinput.send_escape(2)
                    report["method"] = "SendInput"
            except Exception:
                pass
            time.sleep(0.8)
            try:
                if app.GetAcadState().IsQuiescent:
                    report["quiescent"] = True
                    return report
            except Exception:
                pass

    # Final attempt: post ESC again before giving up on quiescence.
    if hwnd:
        try:
            sendinput.post_escape(hwnd, 3)
            time.sleep(1.0)
        except Exception:
            pass

    try:
        report["quiescent"] = bool(app.GetAcadState().IsQuiescent)
    except Exception:
        report["quiescent"] = None
    return report


def _build_payload(command: str, inputs: Sequence[str]) -> str:
    """Compose command-line text. '\\n' is Enter; '' is a bare Enter."""
    parts = [command.strip()]
    for item in inputs:
        parts.append(str(item))
    return "\n".join(parts) + "\n"


def run_with_dialog(
    command: str,
    inputs: Sequence[str] | None = None,
    verify_type: str | None = None,
    dialog_title: str | None = None,
    dialog_edits: dict[int, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    settle: float = 1.6,
    verify_layer: str | None = None,
) -> dict[str, Any]:
    """Run a Tianzheng command that opens a native parameter dialog first.

    Measured on T30-Arch V2.0 / AutoCAD 2022: `MC` (doors/windows) raises the
    '门窗参数' dialog *before* asking 点取门窗插入位置. While that dialog is open
    AutoCAD rejects all COM calls, so the dialog is filled with Win32 messages and
    the insertion points are then posted one at a time.

    `dialog_edits` maps visible edit index -> value; for 门窗参数 the order is
    0=编号 1=门高 2=门宽 3=门槛高 4=距离 5=个数.
    """
    return _com(
        _run_with_dialog_impl,
        command,
        list(inputs or []),
        verify_type,
        dialog_title,
        dict(dialog_edits or {}),
        float(timeout),
        float(settle),
        verify_layer,
        timeout=float(timeout) + 120.0,
    )


def _post_text(doc: Any, text: str, attempts: int = 12, delay: float = 0.3) -> None:
    """PostCommand with busy-retry: AutoCAD may briefly reject after a dialog."""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            doc.PostCommand(text)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay)
    raise AcadError(f"PostCommand rejected repeatedly: {last}")


def force_unblock(app: Any | None = None, attempts: int = 3) -> dict[str, Any]:
    """Hard recovery from the states that make AutoCAD answer RPC_E_CALL_REJECTED.

    MEASURED failure modes, all seen in practice:
      1. an interactive Tianzheng loop is still live (needs ESC);
      2. a modal #32770 dialog is open (needs it closed);
      3. a modal dialog was closed *programmatically*: the command line looks idle
         but COM stays rejected until a REAL keypress arrives -- posting ESC is not
         enough, only a genuine SendInput key while AutoCAD is in the foreground.
    """
    app = app or _attach_app()
    hwnd = int(app.HWND)
    pid = app_process_id(app)
    report: dict[str, Any] = {"steps": []}

    def quiescent() -> bool:
        try:
            return bool(app.GetAcadState().IsQuiescent)
        except Exception:
            return False

    if quiescent():
        report["quiescent"] = True
        report["steps"].append("already idle")
        return report

    # 1) posted ESC (no foreground needed)
    try:
        report["steps"].append(f"post_escape -> {sendinput.post_escape(hwnd, 2)}")
    except Exception as exc:  # noqa: BLE001
        report["steps"].append(f"post_escape failed: {exc}")
    time.sleep(0.9)
    if quiescent():
        report["quiescent"] = True
        report["method"] = "post_escape"
        return report

    # 2) close modal dialogs that would swallow every key
    try:
        for win in dialog.top_windows(pid):
            if win["class"] == "#32770" and win["visible"]:
                dialog.click_button_text_real(win["hwnd"], "取消")
                time.sleep(0.4)
                if dialog.dialog_visible(win["hwnd"]):
                    user32_close(win["hwnd"])
                report["steps"].append(f"closed dialog {win['title']!r}")
    except Exception as exc:  # noqa: BLE001
        report["steps"].append(f"dialog close failed: {exc}")

    # 3) foreground + REAL keypress (the only thing that lifts case 3).
    #
    # OFF by default: a desktop-automation helper may bind Esc to its own "stop"
    # shortcut, so a real ESC here looks to the user like the agent aborting
    # itself (measured repeatedly). Opt in with T20_ALLOW_REAL_ESCAPE=1 when a
    # modal dialog has left COM rejected and posting ESC is not enough.
    import os as _os
    if _os.environ.get("T20_ALLOW_REAL_ESCAPE", "0").strip().lower() not in ("1", "true", "yes", "on"):
        report["steps"].append("real ESC skipped (set T20_ALLOW_REAL_ESCAPE=1 to allow)")
        report["quiescent"] = False
        report["method"] = "post_only"
        return report

    for i in range(attempts):
        try:
            sendinput.focus_window(hwnd)
            if sendinput.ensure_focus(hwnd, attempts=2, settle=0.3):
                sendinput.send_escape(2)
                report["steps"].append(f"real ESC #{i + 1}")
        except Exception as exc:  # noqa: BLE001
            report["steps"].append(f"real ESC failed: {exc}")
        time.sleep(0.9)
        if quiescent():
            report["quiescent"] = True
            report["method"] = "real_escape"
            return report

    report["quiescent"] = False
    report["method"] = "exhausted"
    return report


def user32_close(hwnd: int) -> bool:
    """Post WM_CLOSE to a window (last-resort dialog dismissal)."""
    try:
        return bool(winmsg.user32.PostMessageW(winmsg.wintypes.HWND(hwnd), 0x0010, 0, 0))
    except Exception:
        return False


def clear_pending(app: Any) -> dict[str, Any]:
    """Cancel a command left half-finished by a previous run.

    A previous call can leave AutoCAD inside an interactive loop; every later COM
    call is then rejected and the next command appears to do nothing. Clearing it
    up front is what makes repeated runs reliable.
    """
    report: dict[str, Any] = {"was_busy": None, "cleared": None}
    try:
        report["was_busy"] = not app.GetAcadState().IsQuiescent
    except Exception:
        return report
    if not report["was_busy"]:
        report["cleared"] = True
        return report

    pid = app_process_id(app)
    try:
        report.update(terminate_active_command(app, pid, attempts=2))
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)

    # A programmatically dismissed modal dialog leaves COM rejected even though the
    # command line looks idle; only force_unblock's real keypress lifts that.
    try:
        if not app.GetAcadState().IsQuiescent:
            report["force_unblock"] = force_unblock(app)
    except Exception as exc:  # noqa: BLE001
        report["force_unblock_error"] = str(exc)
    try:
        report["cleared"] = bool(app.GetAcadState().IsQuiescent)
    except Exception:
        report["cleared"] = None
    return report


def _run_with_dialog_impl(
    command: str,
    inputs: list[str],
    verify_type: str | None,
    dialog_title: str | None,
    dialog_edits: dict[int, Any],
    timeout: float,
    settle: float,
    verify_layer: str | None = None,
) -> dict[str, Any]:
    app = _attach_app()
    # Clear anything a previous run left hanging BEFORE touching the document:
    # a pending command makes every document call fail with RPC_E_CALL_REJECTED.
    clear_pending(app)
    doc = _active_document()

    if not is_owned_document(doc) and not allow_foreign_document():
        raise AcadError(
            "Refusing to send commands to "
            f"'{doc_name(doc, '?')}' because this server did not create it. "
            "Call create_test_drawing first, or set T20_ALLOW_FOREIGN_DOC=true."
        )

    try:
        doc.Activate()
    except Exception:
        pass
    _wait_quiescent(app, timeout=20.0)

    observed = verify_type or command.strip().upper()
    before_hist = _histogram(doc)
    before_typed = _count_type(doc, observed, verify_layer)
    pid = app_process_id(app)

    started = time.time()
    dialog_report: dict[str, Any] | None = None
    com_error: str | None = None
    sent: list[str] = []
    # Baseline size of the command-output text, used to detect when AutoCAD has
    # actually answered (see the prompt-driven wait below).
    output_mark = winmsg.command_output_size(pid) if pid else -1

    # The interactive points cannot be delivered without the keyboard focus.
    #
    # MEASURED: SendInput only reaches the foreground window, and posting
    # WM_CHAR to the drawing views is ignored by AutoCAD (posting ESC works, but
    # characters do not). So if this process cannot take the foreground, typing
    # would silently do nothing and the command would hang. Fail loudly instead,
    # because a silent no-op is far harder to diagnose than a clear message.
    # Move the keyboard into the drawing view BEFORE any command starts.
    #
    # MEASURED: the AutoCAD window can be foreground while the keyboard focus sits
    # on a Tianzheng docked panel; typed coordinates are then swallowed and the
    # command silently does nothing (this is what made 轴网 look unreliable). One
    # click inside the drawing area, while no command is running, fixes it and is
    # harmless. Disable with T20_CLICK_TO_FOCUS=0.
    try:
        sendinput.focus_drawing_view(int(app.HWND))
    except Exception:
        pass

    focus_ready = False
    try:
        # Drawing commands need AutoCAD to own the foreground; bring it forward if
        # it is not already (plain foreground change, no minimise/restore flash).
        if not sendinput.is_foreground(int(app.HWND)):
            sendinput.focus_window(int(app.HWND))
            time.sleep(0.25)
        focus_ready = sendinput.ensure_focus(int(app.HWND), attempts=3, settle=0.3)
    except Exception:
        focus_ready = False

    if inputs and not focus_ready:
        result: dict[str, Any] = {
            "success": False,
            "command": command,
            "inputs": inputs,
            "verify_type": observed,
            "mode": "dialog" if dialog_title else "stepwise",
            "focus_available": False,
            "error": (
                "AutoCAD 窗口无法获得键盘焦点，交互式输入无法送达。"
                "请用鼠标点一下 AutoCAD 绘图区（或把它切到前台）后重试。"
            ),
            "note": "命令未发送，模型未改动。",
        }
        try:
            result["counts"] = {"before": before_typed, "after": before_typed, "delta": 0}
        except Exception:
            pass
        return result

    try:
        _post_text(doc, command.strip() + "\n")
        sent.append(command.strip())
        # Wait for AutoCAD to answer the command instead of guessing a delay.
        if pid:
            winmsg.wait_for_output(pid, output_mark, timeout=max(3.0, settle * 2),
                                   settle=min(0.5, settle * 0.2))
    except Exception as exc:  # noqa: BLE001
        com_error = f"PostCommand failed: {exc}"

    # The dialog (if this command has one) appears right after the command.
    if dialog_title and pid:
        try:
            dialog_report = dialog.fill_and_accept(
                pid, dialog_title, dialog_edits, timeout=min(timeout, 10.0)
            )
        except Exception as exc:  # noqa: BLE001
            dialog_report = {"found": False, "error": str(exc)}

    # Feed the interactive points one at a time.
    #
    # MEASURED: once a command is inside its interactive loop (点取门窗插入位置),
    # AutoCAD rejects PostCommand with RPC_E_CALL_REJECTED, so the points cannot
    # be posted. They must be typed like a user, which is exactly what SendInput
    # does. (A human typing "1000,0" is accepted immediately.)
    #
    # Waiting is prompt-driven, not fixed: after each input we wait until AutoCAD
    # prints its next prompt. A fixed delay was the reason 轴网标注 (ZWBZ) worked
    # when driven by hand but produced nothing in a burst -- each step needs the
    # previous one to be fully processed first.
    for item in inputs:
        try:
            mark = winmsg.command_output_size(pid) if pid else -1
            text = "" if item == "" else str(item)
            # type_into_window avoids the "first character is eaten by window
            # activation" bug that corrupted every coordinate prefix.
            sendinput.type_into_window(int(app.HWND), text, enter=True)
            sent.append(text)
            if pid:
                winmsg.wait_for_output(
                    pid, mark,
                    timeout=max(2.5, settle * 2.5),
                    settle=min(0.45, settle * 0.2),
                )
            else:
                time.sleep(settle)
        except Exception as exc:  # noqa: BLE001
            com_error = f"{com_error or ''} | input {item!r} failed: {exc}".strip(" |")
            break

    # End the command's repeat loop so AutoCAD becomes usable again.
    time.sleep(settle * 0.6)
    terminated = None
    if pid:
        try:
            terminated = terminate_active_command(app, pid)
        except Exception:
            terminated = None

    elapsed = round(time.time() - started, 2)
    try:
        after_hist = _histogram(doc)
        after_typed = _count_type(doc, observed, verify_layer)
    except Exception as exc:  # noqa: BLE001
        after_hist, after_typed = before_hist, before_typed
        com_error = com_error or f"post-check failed: {exc}"

    delta_typed = after_typed - before_typed
    type_deltas = {
        key: count - before_hist.get(key, 0)
        for key, count in after_hist.items()
        if count - before_hist.get(key, 0)
    }
    success = delta_typed > 0

    result: dict[str, Any] = {
        "success": success,
        "command": command,
        "inputs": inputs,
        "verify_type": observed,
        "mode": "dialog",
        "dialog": dialog_report,
        "counts": {
            "before": before_typed,
            "after": after_typed,
            "delta": delta_typed,
            "model_space_before": sum(before_hist.values()),
            "model_space_after": sum(after_hist.values()),
        },
        "type_deltas": type_deltas,
        "elapsed_seconds": elapsed,
        "document": _safe_doc_name(doc),
        "command_terminated": terminated,
        "posted": sent,
    }
    if com_error:
        result["com_error"] = com_error
    if not success:
        hints = []
        if com_error:
            hints.append(com_error)
        if dialog_report and not dialog_report.get("found"):
            hints.append(
                f"未检测到 '{dialog_title}' 对话框：该命令在本版本可能不需要参数框，"
                "或标题不同。"
            )
        if dialog_report and dialog_report.get("found") and not dialog_report.get("closed", True):
            hints.append("参数对话框未能关闭，命令被卡住。")
        if type_deltas:
            hints.append(
                "模型有变化（" + ", ".join(f"{k}+{v}" for k, v in type_deltas.items()) + "），"
                f"但没有新增 '{observed}'。"
            )
        if not hints:
            hints.append("未新增实体：请检查输入点是否落在墙上，或命令是否需要鼠标拾取。")
        result["diagnosis"] = " ".join(hints)
    return result


def _pick_world_point(app: Any, doc: Any, pid: int, screen_point: tuple[int, int]) -> tuple[float, float] | None:
    """Click a screen point inside AutoCAD's ID command and read back the world point."""
    import re as _re

    mark = winmsg.command_output_size(pid)
    try:
        _post_text(doc, "_.ID\n")
    except Exception:
        return None
    time.sleep(0.9)
    sendinput.click(screen_point[0], screen_point[1], settle=0.4)
    winmsg.wait_for_output(pid, mark, timeout=6.0, settle=0.35)
    time.sleep(0.4)
    tail = winmsg.command_tail(pid, 260)
    terminate_active_command(app, pid)
    time.sleep(0.3)
    xs = _re.findall(r"X\s*=\s*(-?\d+(?:\.\d+)?)", tail, _re.IGNORECASE)
    ys = _re.findall(r"Y\s*=\s*(-?\d+(?:\.\d+)?)", tail, _re.IGNORECASE)
    if xs and ys:
        return float(xs[-1]), float(ys[-1])
    return None


def calibrate_view(app: Any, doc: Any, pid: int | None = None,
                   probe_px: int = 220) -> dict[str, Any]:
    """Measure the world<->screen transform empirically with three ID picks.

    MEASURED: every piece of view metadata lied. `ActiveViewport.Center` reported
    (4200,3150) while a click at the window centre returned world (4206,12429), and
    the effective view height was ~26000 units although `ActiveViewport.Height`
    reported 7500 -- so `ZoomExtents`, `Height` and VIEWCTR were all unreliable.
    Clicking three known screen points and reading the world result back with the ID
    command produces a transform that is correct by construction.
    """
    from . import screen_map

    pid = pid or app_process_id(app)
    if not pid:
        raise AcadError("Cannot resolve the AutoCAD process id.")

    pane = screen_map.doc_view_window(doc, int(app.HWND))
    rect = screen_map.drawing_view_rect(pane)
    if not rect:
        raise AcadError("无法取得绘图窗格矩形。")
    left, top, width, height = rect
    cx, cy = left + width // 2, top + height // 2

    sendinput.focus_drawing_view(int(app.HWND))
    sendinput.ensure_focus(int(app.HWND), attempts=2)

    p0 = _pick_world_point(app, doc, pid, (cx, cy))
    p1 = _pick_world_point(app, doc, pid, (cx + probe_px, cy))
    p2 = _pick_world_point(app, doc, pid, (cx, cy + probe_px))
    if not (p0 and p1 and p2):
        return {"calibrated": False, "rect": rect,
                "error": "标定失败：ID 未回显坐标。", "picks": [p0, p1, p2]}

    # px per world unit (y is inverted on screen)
    scale_x = (p1[0] - p0[0]) / float(probe_px)
    scale_y = (p0[1] - p2[1]) / float(probe_px)
    if scale_x == 0 or scale_y == 0:
        # The probe clicks did not register (usually because AutoCAD was not in the
        # foreground). Re-focus and try once more before giving up.
        sendinput.focus_window(int(app.HWND))
        sendinput.ensure_focus(int(app.HWND), attempts=3)
        time.sleep(0.4)
        p0 = _pick_world_point(app, doc, pid, (cx, cy))
        p1 = _pick_world_point(app, doc, pid, (cx + probe_px, cy))
        p2 = _pick_world_point(app, doc, pid, (cx, cy + probe_px))
        if p0 and p1 and p2:
            scale_x = (p1[0] - p0[0]) / float(probe_px)
            scale_y = (p0[1] - p2[1]) / float(probe_px)
        if not (p0 and p1 and p2) or scale_x == 0 or scale_y == 0:
            return {"calibrated": False, "rect": rect,
                    "error": "标定失败：三次取点没有区分度（点击未生效）。",
                    "picks": [p0, p1, p2]}

    state = {
        "left": left, "top": top, "width": width, "height": height,
        "centre": p0,                      # world point at the pane centre
        "scale": 1.0 / ((abs(scale_x) + abs(scale_y)) / 2.0),  # px per unit
        "viewsize": height * ((abs(scale_x) + abs(scale_y)) / 2.0),
        "source": "calibrated",
        "scale_x": scale_x, "scale_y": scale_y,
        "panes": rect,
        "picks": [p0, p1, p2],
        "probe_px": probe_px,
    }
    return state


def verify_position(world_x: float, world_y: float, timeout: float = 20.0) -> dict[str, Any]:
    """定位自检: click a computed screen point and read back where AutoCAD says it landed.

    Uses AutoCAD's own ID command, which prints the picked point to the command
    line, so the *real* location can be compared with the intended one without any
    OCR. This separates the two possible reasons a click "misses":
      * the coordinate mapping is wrong (large error), or
      * the mapping is fine but the clicked point is not on the geometry that
        needed to be selected (small error).
    """
    return _com(_verify_position_impl, float(world_x), float(world_y), float(timeout),
                timeout=float(timeout) + 60.0)


def _verify_position_impl(world_x: float, world_y: float, timeout: float) -> dict[str, Any]:
    import re as _re

    from . import screen_map

    app = _attach_app()
    clear_pending(app)
    doc = _active_document()
    pid = app_process_id(app)
    if not pid:
        raise AcadError("Cannot resolve the AutoCAD process id.")

    sendinput.focus_drawing_view(int(app.HWND))
    sendinput.ensure_focus(int(app.HWND), attempts=2)

    # Calibrate empirically: every piece of view metadata proved unreliable
    # (ActiveViewport.Center claimed 3150 while a click at the window centre
    # returned world y = 12473), so the transform is measured with ID picks.
    state = calibrate_view(app, doc)
    if not state or not state.get("centre"):
        return {"success": False, "error": state.get("error") or "视图标定失败。"}

    point = screen_map.world_to_screen(state, world_x, world_y)
    if not point:
        return {"success": False, "error": "坐标换算失败。"}
    if not screen_map.inside_view(state, point):
        return {
            "success": False,
            "error": "目标点在当前视口之外，请先缩放/平移使其可见。",
            "screen_point": list(point),
            "view": {k: state[k] for k in ("left", "top", "width", "height", "viewsize", "scale")},
        }

    mark = winmsg.command_output_size(pid)
    _post_text(doc, "_.ID\n")
    time.sleep(1.2)
    sendinput.click(point[0], point[1], settle=0.4)
    winmsg.wait_for_output(pid, mark, timeout=8.0, settle=0.4)
    time.sleep(0.6)
    tail = winmsg.command_tail(pid, 400)
    terminate_active_command(app, pid)

    # AutoCAD prints: X = 1234.5678   Y = 2345.6789   Z = 0.0000
    xs = _re.findall(r"X\s*=\s*(-?\d+(?:\.\d+)?)", tail, _re.IGNORECASE)
    ys = _re.findall(r"Y\s*=\s*(-?\d+(?:\.\d+)?)", tail, _re.IGNORECASE)
    actual = None
    if xs and ys:
        actual = (float(xs[-1]), float(ys[-1]))

    report: dict[str, Any] = {
        "success": actual is not None,
        "target": [world_x, world_y],
        "screen_point": list(point),
        "view": {
            "left": state["left"], "top": state["top"],
            "width": state["width"], "height": state["height"],
            "centre": list(state["centre"]), "viewsize": state["viewsize"],
            "scale": round(state["scale"], 6), "source": state.get("source"),
        },
        "raw_tail": tail[-200:],
    }
    if actual:
        err_x = actual[0] - world_x
        err_y = actual[1] - world_y
        dist = (err_x ** 2 + err_y ** 2) ** 0.5
        report["actual"] = [round(actual[0], 2), round(actual[1], 2)]
        report["error"] = {
            "dx": round(err_x, 2), "dy": round(err_y, 2),
            "distance": round(dist, 2),
            "pixels": round(dist * state["scale"], 2),
        }
        report["verdict"] = (
            "定位准确" if dist * state["scale"] <= 2.0
            else ("有偏差" if dist * state["scale"] <= 10.0 else "定位错误")
        )
    else:
        report["error"] = {"message": "ID 命令没有回显坐标，未能校验。"}
    return report


def insert_title_frame(size: str = "A3", insertion: str | None = None,
                       timeout: float = 40.0) -> dict[str, Any]:
    """插入图框 (TTitleFrame) by driving its native 插入图框 dialog.

    MEASURED: TTitleFrame opens a real Win32 dialog titled 插入图框 with three
    edits (width / height / ratio, e.g. 1189 / 841 / 100), a set of sheet-size
    buttons (A0, A1, A2, A3, A4 ...) and 插入 / 取消. Because it is a plain #32770
    it can be driven with window messages: click the size button, click 插入, then
    give the insertion point.
    """
    return _com(_insert_title_frame_impl, str(size or "A3"),
                None if insertion is None else str(insertion),
                float(timeout), timeout=float(timeout) + 60.0)


def _insert_title_frame_impl(size: str, insertion: str | None, timeout: float) -> dict[str, Any]:
    app = _attach_app()
    clear_pending(app)
    doc = _active_document()

    if not is_owned_document(doc) and not allow_foreign_document():
        raise AcadError(
            "Refusing to send commands to "
            f"'{doc_name(doc, '?')}' because this server did not create it."
        )

    pid = app_process_id(app)
    if not pid:
        raise AcadError("Cannot resolve the AutoCAD process id.")

    before_hist = _histogram(doc)
    before_total = sum(before_hist.values())
    steps: list[str] = []
    started = time.time()

    try:
        sendinput.focus_drawing_view(int(app.HWND))
        sendinput.ensure_focus(int(app.HWND), attempts=2)
        mark = winmsg.command_output_size(pid)
        _post_text(doc, "TTitleFrame\n")
        winmsg.wait_for_output(pid, mark, timeout=6.0, settle=0.4)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "command": "TTitleFrame",
                "error": f"cannot start TTitleFrame: {exc}", "steps": steps}

    dlg = dialog.wait_for_dialog(pid, "插入图框", timeout=min(timeout, 15.0))
    if not dlg:
        return {
            "success": False,
            "command": "TTitleFrame",
            "error": "未出现「插入图框」对话框。",
            "steps": steps,
        }
    steps.append(f"dialog {dlg['title']!r} hwnd={dlg['hwnd']}")

    # Tianzheng's controls ignore WM_COMMAND/BM_CLICK, so every click here is a
    # real SendInput click on the control's screen position. The dialog must be in
    # the foreground, which the focus call above arranges.
    if size:
        picked = dialog.click_button_text_real(dlg["hwnd"], size)
        steps.append(f"real-click size {size!r} -> {picked}")
        time.sleep(0.6)

    inserted = dialog.click_button_text_real(dlg["hwnd"], "插入", verify_dialog_closes=True)
    steps.append(f"real-click 插入 -> {inserted}")
    if not inserted.get("clicked"):
        # fall back to the message-based click, which works for plain Win32 dialogs
        fallback = dialog.click_button_text(dlg["hwnd"], "插入")
        steps.append(f"BM_CLICK fallback 插入 -> {fallback}")
    time.sleep(1.2)

    if dialog.dialog_visible(dlg["hwnd"]):
        return {
            "success": False,
            "command": "TTitleFrame",
            "size": size,
            "error": "「插入图框」对话框没有关闭，图框未插入（按钮点击未生效）。",
            "steps": steps,
        }

    # insertion point (default: below the origin)
    point = insertion if insertion else "0,-9000"
    try:
        sendinput.type_into_window(int(app.HWND), point, enter=True)
        steps.append(f"insertion {point!r}")
        time.sleep(1.5)
    except Exception as exc:  # noqa: BLE001
        steps.append(f"insertion failed: {exc}")

    terminate_active_command(app, pid)

    # The frame arrives as a block reference plus its polylines, and AutoCAD keeps
    # building it for a moment after the dialog closes -- measuring immediately
    # reported "0 created" for a frame that did appear. Poll until it grows.
    after_hist = before_hist
    after_total = before_total
    for _ in range(10):
        time.sleep(0.6)
        try:
            after_hist = _histogram(doc)
        except Exception:
            continue
        after_total = sum(after_hist.values())
        if after_total > before_total:
            break
    delta = after_total - before_total
    result: dict[str, Any] = {
        "success": delta > 0,
        "command": "TTitleFrame",
        "size": size,
        "insertion": point,
        "counts": {"before": before_total, "after": after_total, "delta": delta},
        "type_deltas": {
            k: after_hist.get(k, 0) - before_hist.get(k, 0)
            for k in set(before_hist) | set(after_hist)
            if after_hist.get(k, 0) - before_hist.get(k, 0)
        },
        "steps": steps,
        "elapsed_seconds": round(time.time() - started, 2),
        "document": _safe_doc_name(doc),
    }
    if not result["success"]:
        result["diagnosis"] = "图框未生成；请查看 steps 中每一步的执行结果与对话框按钮文本。"
    return result


def annotate_axis(start_point: str, end_point: str, timeout: float = 30.0) -> dict[str, Any]:
    """轴网标注 (ZWBZ) driven by the *actual prompts* rather than a fixed script.

    MEASURED: the sequence is not fixed. After a picked axis, ZWBZ asks
    "是否为该对象?[是(Y)/否(N)]" only when the point is ambiguous, and answers
    "选择无效, 重新选择!" when the coordinates did not land on a real axis. A fixed
    input list is therefore wrong by construction -- it worked when driven by hand
    (reacting to each prompt) and failed in bursts. This driver reads the command
    line after every step and branches on it.
    """
    return _com(_annotate_axis_impl, str(start_point), str(end_point), float(timeout),
                timeout=float(timeout) + 60.0)


def _annotate_axis_impl(start_point: str, end_point: str, timeout: float) -> dict[str, Any]:
    app = _attach_app()
    clear_pending(app)
    doc = _active_document()

    if not is_owned_document(doc) and not allow_foreign_document():
        raise AcadError(
            "Refusing to send commands to "
            f"'{doc_name(doc, '?')}' because this server did not create it."
        )

    pid = app_process_id(app)
    if not pid:
        raise AcadError("Cannot resolve the AutoCAD process id (window handle unavailable).")

    verify = "TCH_AXIS_LABEL"
    before = _count_type(doc, verify)
    prompts: list[str] = []
    started = time.time()

    try:
        sendinput.focus_drawing_view(int(app.HWND))
        sendinput.ensure_focus(int(app.HWND), attempts=2)
        # Capture the output baseline BEFORE posting, otherwise the wait below can
        # never observe growth and burns its whole timeout.
        mark = winmsg.command_output_size(pid)
        _post_text(doc, "ZWBZ\n")
        winmsg.wait_for_output(pid, mark, timeout=6.0, settle=0.35)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "command": "ZWBZ", "error": f"cannot start ZWBZ: {exc}",
                "prompts": prompts}

    def type_value(value: str, settle: float = 0.3) -> None:
        sendinput.type_into_window(int(app.HWND), value, enter=True)
        time.sleep(settle)

    def tail() -> str:
        return winmsg.command_tail(pid, 200)

    def confirm_if_asked() -> None:
        """Answer the optional 是否为该对象?[Y] prompt when it actually appeared."""
        for _ in range(2):
            if "是否为该对象" in tail():
                type_value("", settle=1.2)
            else:
                break

    # MEASURED: ZWBZ does not render its prompt until some input arrives, so
    # waiting for "请选择起始轴线" deadlocks -- the prompt only showed up after an
    # ESC. AutoCAD buffers command-line input, so typing with a sufficient pause
    # works, and that is what reliably produced TDbAxisLabelSet + 4 × TDbDimension2.
    try:
        type_value(start_point, settle=1.8)
        prompts.append(f"start {start_point!r} -> {tail()[-60:]}")
        confirm_if_asked()

        type_value(end_point, settle=1.8)
        prompts.append(f"end {end_point!r} -> {tail()[-60:]}")
        confirm_if_asked()

        if "不需要标注" in tail():
            type_value("", settle=1.4)  # skip excluded axes
        if "请选择起始轴线" in tail():
            type_value("", settle=1.2)  # looped back -> Enter exits
    except Exception as exc:  # noqa: BLE001
        prompts.append(f"exception: {exc}")

    terminated = terminate_active_command(app, pid)
    try:
        after = _count_type(doc, verify)
    except Exception:
        after = before

    delta = after - before
    result: dict[str, Any] = {
        "success": delta > 0,
        "command": "ZWBZ",
        "verify_type": verify,
        "start_point": start_point,
        "end_point": end_point,
        "counts": {"before": before, "after": after, "delta": delta},
        "prompts": prompts,
        "command_terminated": terminated,
        "elapsed_seconds": round(time.time() - started, 2),
        "document": _safe_doc_name(doc),
    }
    if delta <= 0:
        joined = " ".join(prompts)
        if "选择无效" in joined:
            result["diagnosis"] = (
                "天正判定点位无效：给出的两个点必须精确落在真实轴线上"
                "（可用 list_tianzheng_entities 读取 DOTE 图层轴线的包围盒来取点）。"
            )
        else:
            result["diagnosis"] = "未生成轴号集，请查看 prompts 中每一步的实际提示。"
    return result


def run_command(
    command: str,
    inputs: Sequence[str] | None = None,
    verify_type: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    mode: str = "async",
    auto_dismiss_ui: bool = True,
    verify_layer: str | None = None,
) -> dict[str, Any]:
    """Run a Tianzheng command and verify the model actually changed.

    mode: "async" -> PostCommand + polling (default; survives Tianzheng panels)
          "sync"  -> SendCommand (blocks; only safe for commands with no UI)
    verify_layer restricts the success check to one layer (e.g. DOTE for axes).
    """
    return _com(
        _run_impl,
        command,
        list(inputs or []),
        verify_type,
        float(timeout),
        str(mode),
        bool(auto_dismiss_ui),
        verify_layer,
        timeout=float(timeout) + 90.0,
    )


def _run_impl(
    command: str,
    inputs: list[str],
    verify_type: str | None,
    timeout: float,
    mode: str,
    auto_dismiss_ui: bool,
    verify_layer: str | None = None,
) -> dict[str, Any]:
    app = _attach_app()
    # Clear anything a previous run left hanging BEFORE touching the document:
    # a pending command makes every document call fail with RPC_E_CALL_REJECTED.
    clear_pending(app)
    doc = _active_document()

    if not is_owned_document(doc) and not allow_foreign_document():
        raise AcadError(
            "Refusing to send commands to "
            f"'{doc_name(doc, '?')}' because this server did not create it. "
            "That is very likely the user's real drawing. Call create_test_drawing "
            "first, or set T20_ALLOW_FOREIGN_DOC=true to override deliberately."
        )

    try:
        doc.Activate()
    except Exception:
        pass

    # Never issue a command while AutoCAD is still finishing something else.
    call_with_retry(lambda: app.GetAcadState().IsQuiescent, attempts=1, delay=0.1)
    _wait_quiescent(app, timeout=20.0)

    observed = verify_type or suggest_verify_type(command)
    verify_mode = "entity_type" if observed else "model_space_total"
    before_hist = _histogram(doc)
    before_typed = _count_observed(doc, observed, verify_layer)
    payload = _build_payload(command, inputs)

    pid = app_process_id(app)
    ui_before = winmsg.describe_ui(pid) if pid else None

    started = time.time()
    com_error: str | None = None

    if mode == "sync":
        try:
            doc.SendCommand(payload)
        except Exception as exc:  # noqa: BLE001
            com_error = f"{type(exc).__name__}: {exc}"
    else:
        # PostCommand queues the text and returns immediately - the only
        # asynchronous dispatch AutoCAD 2022 exposes over COM. (There is no
        # SendStringToExecute on the type library.) This matters because
        # Tianzheng commands open UI and a synchronous SendCommand never returns.
        try:
            doc.PostCommand(payload)
        except Exception as exc:  # noqa: BLE001
            # Fall back to the blocking call, but do not let it hang forever:
            # the COM worker timeout still applies.
            com_error = f"PostCommand failed ({type(exc).__name__}: {exc}); tried SendCommand"
            try:
                doc.SendCommand(payload)
            except Exception as exc2:  # noqa: BLE001
                com_error += f" | SendCommand failed: {type(exc2).__name__}: {exc2}"

    # Poll for the change. Tianzheng needs time to process the command.
    dismissed: list[dict] = []
    deadline = started + timeout
    last_typed = before_typed
    stable_rounds = 0
    while time.time() < deadline:
        time.sleep(0.4)
        if auto_dismiss_ui and pid:
            acted = winmsg.cancel_dialogs(pid)
            if acted:
                dismissed.extend(acted)
        try:
            now_typed = call_with_retry(
                lambda: _count_observed(doc, observed, verify_layer), attempts=3, delay=0.25
            )
        except Exception:
            continue
        if now_typed > before_typed:
            if now_typed == last_typed:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_typed = now_typed
            if stable_rounds >= 2:
                break
        elif time.time() - started > timeout * 0.75:
            break

    elapsed = round(time.time() - started, 2)

    # Tianzheng wall/opening commands keep looping for the next input after the
    # first object is drawn, which leaves AutoCAD non-quiescent and blocks every
    # later COM call. Terminate the command loop once we have what we came for.
    finished_clean = None
    if auto_dismiss_ui and pid:
        try:
            finished_clean = terminate_active_command(app, pid)
        except Exception:
            finished_clean = None

    try:
        after_hist = _histogram(doc)
        after_typed = _count_observed(doc, observed, verify_layer)
    except Exception as exc:  # noqa: BLE001
        after_hist = before_hist
        after_typed = before_typed
        com_error = com_error or f"post-check failed: {exc}"

    delta_typed = after_typed - before_typed
    success = delta_typed > 0

    type_deltas: dict[str, int] = {}
    for key, count in after_hist.items():
        diff = count - before_hist.get(key, 0)
        if diff:
            type_deltas[key] = diff

    prompt = None
    try:
        prompt = call_with_retry(lambda: _last_prompt(doc), attempts=3, delay=0.25)
    except Exception:
        pass

    ui_after = winmsg.describe_ui(pid) if pid else None

    result: dict[str, Any] = {
        "success": success,
        "command": command,
        "inputs": inputs,
        "sent_payload": payload.replace("\n", "\\n"),
        "verify_type": observed,
        "verify_mode": verify_mode,
        "mode": mode,
        "counts": {
            "before": before_typed,
            "after": after_typed,
            "delta": delta_typed,
            "model_space_before": sum(before_hist.values()),
            "model_space_after": sum(after_hist.values()),
        },
        "type_deltas": type_deltas,
        "elapsed_seconds": elapsed,
        "last_prompt": prompt,
        "document": _safe_doc_name(doc),
        "ui_dismissed": dismissed,
        "command_terminated": finished_clean,
    }
    if com_error:
        result["com_error"] = com_error
    if ui_before is not None:
        result["ui_before"] = {
            "blocking_dialogs": [w["title"] for w in ui_before["blocking_dialogs"]],
            "panels": [w["title"] for w in ui_before["tianzheng_panels"]],
        }
    if ui_after is not None:
        result["ui_after"] = {
            "blocking_dialogs": [w["title"] for w in ui_after["blocking_dialogs"]],
            "panels": [w["title"] for w in ui_after["tianzheng_panels"]],
        }

    if not success:
        result["diagnosis"] = _diagnose(
            prompt, delta_typed, type_deltas, com_error, ui_after, observed, verify_mode
        )
    return result


def _diagnose(
    prompt: str | None,
    delta_typed: int,
    type_deltas: dict[str, int],
    com_error: str | None,
    ui_after: dict | None,
    observed: str | None = None,
    verify_mode: str = "entity_type",
) -> str:
    hints: list[str] = []
    text = (prompt or "")
    low = text.lower()

    if com_error:
        hints.append(f"COM error: {com_error}")
    if "未知命令" in text or "unknown command" in low:
        hints.append(
            "Tianzheng answered 'unknown command' - tch_kernal.arx is not loaded in "
            "this AutoCAD. Start CAD through TGStart.exe."
        )
    if "取消" in text or "cancel" in low:
        hints.append(
            "The command was cancelled, so the input sequence does not match what this "
            "Tianzheng build expects. Adjust the number/order of inputs (see run results)."
        )
    if ui_after and ui_after.get("blocking_dialogs"):
        hints.append(
            "A Tianzheng dialog is still open ('"
            + "', '".join(w["title"] for w in ui_after["blocking_dialogs"])
            + "'). The command is waiting on that dialog, not on the command line."
        )
    if type_deltas and delta_typed <= 0:
        hints.append(
            "ModelSpace did change (" + ", ".join(f"{k}+{v}" for k, v in type_deltas.items())
            + f") but no new '{observed}' appeared. Check the type name or the command used."
        )
    if not type_deltas and delta_typed <= 0:
        hints.append(
            "Nothing was added to ModelSpace. Most likely the Tianzheng panel stayed "
            "open (it needs the mouse or a different input sequence), or the input "
            "sequence was rejected. Compare 'sent_payload' with 'last_prompt'."
        )
    if verify_mode == "model_space_total":
        hints.append(
            "This command's output type is not catalogued, so success was judged by "
            "the total ModelSpace count. Run probe_command to discover the exact type."
        )
    return " ".join(hints)


def probe_command(
    command: str, inputs: Sequence[str] | None, timeout: float = DEFAULT_TIMEOUT, mode: str = "async"
) -> dict[str, Any]:
    """Run a command and report which entity types appeared."""
    return run_command(
        command=command, inputs=inputs, verify_type=None, timeout=timeout, mode=mode
    )
