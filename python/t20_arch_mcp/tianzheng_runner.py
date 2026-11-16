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

    # The interactive points cannot be delivered without the keyboard focus.
    #
    # MEASURED: SendInput only reaches the foreground window, and posting
    # WM_CHAR to the drawing views is ignored by AutoCAD (posting ESC works, but
    # characters do not). So if this process cannot take the foreground, typing
    # would silently do nothing and the command would hang. Fail loudly instead,
    # because a silent no-op is far harder to diagnose than a clear message.
    focus_ready = False
    try:
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
    for item in inputs:
        time.sleep(settle)
        try:
            sendinput.focus_window(int(app.HWND))
            time.sleep(0.15)
            text = "" if item == "" else str(item)
            if text:
                sendinput.type_unicode(text)
                time.sleep(0.1)
            sendinput.press_key(0x0D)  # Enter
            sent.append(text)
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
