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

from . import sendinput, winmsg
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


def _count_type(doc: Any, entity_type: str) -> int:
    try:
        msp = doc.ModelSpace
        total = int(msp.Count)
    except Exception:
        return 0
    count = 0
    for index in range(total):
        try:
            entity = msp.Item(index)
        except Exception:
            continue
        if _matches(entity_type, _object_name(entity)):
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


def _count_observed(doc: Any, entity_type: str | None) -> int:
    """Count the proof entity, or all ModelSpace entities when the type is unknown.

    Commands such as HZZW may create a type we have not catalogued yet.  Falling
    back to the ModelSpace total is honest (we say what we measured in
    `verify_mode`) and far better than comparing against the command name,
    which could never match and reported a guaranteed false failure.
    """
    if entity_type:
        return _count_type(doc, entity_type)
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
                sendinput.focus_window(hwnd)
                time.sleep(0.15)
                sendinput.send_escape(2)
                report["method"] = "SendInput"
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


def run_command(
    command: str,
    inputs: Sequence[str] | None = None,
    verify_type: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    mode: str = "async",
    auto_dismiss_ui: bool = True,
) -> dict[str, Any]:
    """Run a Tianzheng command and verify the model actually changed.

    mode: "async"  -> SendStringToExecute + polling (default, survives UI panels)
          "sync"   -> SendCommand (blocks; only safe when no UI opens)
    """
    return _com(
        _run_impl,
        command,
        list(inputs or []),
        verify_type,
        float(timeout),
        str(mode),
        bool(auto_dismiss_ui),
        timeout=float(timeout) + 90.0,
    )


def _run_impl(
    command: str,
    inputs: list[str],
    verify_type: str | None,
    timeout: float,
    mode: str,
    auto_dismiss_ui: bool,
) -> dict[str, Any]:
    app = _attach_app()
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
    before_typed = _count_observed(doc, observed)
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
                lambda: _count_observed(doc, observed), attempts=3, delay=0.25
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
        after_typed = _count_observed(doc, observed)
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
