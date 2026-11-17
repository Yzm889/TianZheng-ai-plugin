"""acad_conn.py - COM connection + Tianzheng load-state checks.

Design rules enforced here:
  * We attach to an ALREADY RUNNING AutoCAD via GetActiveObject.
    We never silently start a bare instance, because a fresh acad.exe usually has
    no Tianzheng ARX loaded and every TCH_* command would answer "unknown command".
  * All COM work runs on ONE dedicated STA thread, because AutoCAD is a
    single-threaded-apartment server and asyncio/MCP callbacks arrive on other
    threads.
  * Documents the server creates are tracked so we never send commands into a
    user's real drawing.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import traceback
from typing import Any, Callable

try:
    import pythoncom
    import pywintypes
    import win32com.client as _win32com

    _COM_IMPORT_ERROR: Exception | None = None
except Exception as _exc:  # pragma: no cover - environment guard
    pythoncom = None
    pywintypes = None
    _win32com = None
    _COM_IMPORT_ERROR = _exc


# ProgIDs, most specific first.
PROGIDS = (
    "AutoCAD.Application",
    "AutoCAD.Application.25.1",
    "AutoCAD.Application.25.0",
    "AutoCAD.Application.24.1",
    "AutoCAD.Application.24.0",
)

TRUTHY = {"1", "true", "yes", "on"}


class AcadError(RuntimeError):
    """Raised when the AutoCAD COM link is unavailable or unusable."""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in TRUTHY


def allow_new_instance() -> bool:
    """Whether we may launch a bare AutoCAD when no instance is running."""
    return _env_flag("AUTOCAD_ALLOW_NEW_INSTANCE", False)


def allow_foreign_document() -> bool:
    """Whether commands may be sent to a drawing this server did not create."""
    return _env_flag("T20_ALLOW_FOREIGN_DOC", False)


# --------------------------------------------------------------------------- #
# STA worker: every COM call is funnelled onto this one thread.
# --------------------------------------------------------------------------- #
class _ComWorker:
    def __init__(self) -> None:
        self._q: "queue.Queue[Any]" = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="t20-acad-com", daemon=True
        )
        self._thread.start()
        self._ready.wait(15)

    def _loop(self) -> None:
        try:
            if pythoncom is not None:
                pythoncom.CoInitialize()
        except Exception:
            pass
        self._ready.set()
        while True:
            item = self._q.get()
            if item is None:
                break
            fn, args, kwargs, done, box = item
            try:
                box["result"] = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - forwarded to caller
                box["error"] = exc
                box["traceback"] = traceback.format_exc()
            finally:
                done.set()

    def call(self, fn: Callable[..., Any], *args: Any, timeout: float = 120.0, **kwargs: Any) -> Any:
        if self._thread is None or not self._thread.is_alive():
            raise AcadError("COM worker thread is not running.")
        done = threading.Event()
        box: dict[str, Any] = {}
        self._q.put((fn, args, kwargs, done, box))
        if not done.wait(timeout):
            raise AcadError(
                f"AutoCAD COM call timed out after {timeout:.0f}s. "
                "AutoCAD is probably waiting for user input (a modal dialog or a "
                "pending command). Press ESC in AutoCAD and retry."
            )
        if "error" in box:
            raise box["error"]
        return box.get("result")


_worker: _ComWorker | None = None
_worker_lock = threading.Lock()


def _com(fn: Callable[..., Any], *args: Any, timeout: float = 120.0, **kwargs: Any) -> Any:
    global _worker
    if _COM_IMPORT_ERROR is not None:
        raise AcadError(
            "pywin32 is not importable by this interpreter "
            f"({_COM_IMPORT_ERROR!r}). Run the server with a Python that has "
            "pywin32 installed (e.g. Python 3.13 on this machine)."
        )
    with _worker_lock:
        if _worker is None:
            _worker = _ComWorker()
    return _worker.call(fn, *args, timeout=timeout, **kwargs)


# --------------------------------------------------------------------------- #
# Application attach
# --------------------------------------------------------------------------- #
def _typed(value: Any) -> Any:
    """Re-bind a late-bound CDispatch to the early-bound (typed) wrapper.

    GetActiveObject hands back a generic CDispatch with no type information, and
    every property access on it then fails with AttributeError "<unknown>.Name".
    Wrapping the underlying IUnknown in the generated type-library wrapper makes
    Document/ModelSpace/entity properties work.
    """
    try:
        from win32com.client import gencache

        oleobj = getattr(value, "_oleobj_", None)
        if oleobj is None:
            return value
        return gencache.EnsureDispatch(oleobj)
    except Exception:
        return value


def _attach_app() -> Any:
    errors: list[str] = []
    for progid in PROGIDS:
        try:
            return _typed(_win32com.GetActiveObject(progid))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{progid}: {exc}")

    if allow_new_instance():
        for progid in PROGIDS:
            try:
                return _typed(_win32com.Dispatch(progid))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{progid} (create): {exc}")

    tail = " | ".join(errors[:3])
    raise AcadError(
        "No running AutoCAD instance is reachable over COM "
        f"(MK_E_UNAVAILABLE / 0x800401E3 is the usual symptom). Details: {tail}. "
        "Start AutoCAD through the Tianzheng launcher (TGStart.exe) so the TCH ARX "
        "modules are loaded, then retry. AUTOCAD_ALLOW_NEW_INSTANCE is "
        f"{'true' if allow_new_instance() else 'false'}; leaving it false is "
        "intentional because a bare instance has no Tianzheng commands."
    )


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
# Ownership must survive process restarts (and lets the MCP server be restarted
# without losing track of drawings it created), so it is persisted to a small
# JSON state file instead of living only in memory.
_STATE_FILE = os.path.join(
    os.environ.get("T20_STATE_DIR", os.path.expanduser("~")),
    ".t20_arch_mcp_owned.json",
)

_owned_docs: set[str] = set()
_owned_lock = threading.Lock()
_owned_loaded = False


def _load_owned() -> None:
    global _owned_loaded
    if _owned_loaded:
        return
    _owned_loaded = True
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            _owned_docs.update(str(x).strip().lower() for x in data if x)
    except Exception:
        pass


def _save_owned() -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE) or ".", exist_ok=True)
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(sorted(_owned_docs), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _STATE_FILE)
    except Exception:
        pass


RPC_E_CALL_REJECTED = -2147418111
RPC_E_SERVERCALL_RETRYLATER = -2147417846
BUSY_HRESULTS = {RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER}


def is_busy_error(exc: BaseException) -> bool:
    """True when the COM error means 'AutoCAD was busy, try again'."""
    code = getattr(exc, "hresult", None)
    if code is None:
        args = getattr(exc, "args", ())
        code = args[0] if args and isinstance(args[0], int) else None
    return code in BUSY_HRESULTS


def call_with_retry(
    fn: Callable[[], Any],
    attempts: int = 20,
    delay: float = 0.5,
    label: str = "COM call",
) -> Any:
    """Retry a COM call while AutoCAD is busy or has a modal dialog open.

    AutoCAD rejects calls with RPC_E_CALL_REJECTED whenever it is not quiescent
    (a command is running, a dialog is up, or the user is dragging). This is
    normal, not an error, so we retry before giving up.
    """
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not is_busy_error(exc):
                raise
            time.sleep(delay)
    raise AcadError(
        f"{label} kept being rejected by AutoCAD for {attempts * delay:.0f}s "
        "(RPC_E_CALL_REJECTED). AutoCAD is busy: finish or press ESC on the "
        "active command / close the open dialog, then retry."
    ) from last


def wait_until_quiescent(app: Any, timeout: float = 25.0, poll: float = 0.5) -> bool:
    """Best-effort wait for AutoCAD to report idle.

    MEASURED CAVEAT: with T20-Arch loaded, IsQuiescent often stays False forever
    (Tianzheng keeps its docked panels/hooks active), even though COM calls like
    Documents.Add and ModelSpace work fine. So this is only a short courtesy
    pause - correctness must never depend on it. `call_with_retry` is what
    actually protects callers, by retrying on RPC_E_CALL_REJECTED.
    """
    deadline = time.time() + min(timeout, 5.0)
    while time.time() < deadline:
        try:
            if app.GetAcadState().IsQuiescent:
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False


def doc_name(doc: Any, default: str | None = None) -> str | None:
    """Read doc.Name without ever raising.

    Any direct `doc.Name` access raises RPC_E_CALL_REJECTED while AutoCAD is
    busy, and a bare getattr() inside an error message is enough to mask the real
    error. Always go through this helper.
    """
    try:
        return str(call_with_retry(lambda: doc.Name, attempts=3, delay=0.25))
    except Exception:
        return default


def _doc_key(doc: Any) -> str:
    try:
        name = str(call_with_retry(lambda: doc.FullName, attempts=6, delay=0.3) or "")
    except Exception:
        name = ""
    if not name:
        try:
            name = str(call_with_retry(lambda: doc.Name, attempts=6, delay=0.3) or "")
        except Exception:
            name = ""
    return name.strip().lower()


def register_owned_document(doc: Any) -> None:
    key = _doc_key(doc)
    if not key:
        return
    with _owned_lock:
        _load_owned()
        if key in _owned_docs:
            return
        _owned_docs.add(key)
        _save_owned()


def is_owned_document(doc: Any) -> bool:
    key = _doc_key(doc)
    if not key:
        return False
    with _owned_lock:
        _load_owned()
        if key in _owned_docs:
            return True
        # A server-created drawing that was never saved has no FullName yet;
        # match on the bare Name as a fallback so it is still recognised.
        try:
            name = str(call_with_retry(lambda: doc.Name, attempts=6, delay=0.3) or "")
        except Exception:
            return False
        return bool(name) and (name.strip().lower() in _owned_docs)


def _describe_document(doc: Any) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for attr in ("Name", "FullName", "Path", "Saved"):
        try:
            info[attr] = call_with_retry(lambda a=attr: getattr(doc, a), label=f"doc.{attr}")
        except Exception as exc:  # noqa: BLE001
            info[attr] = None
            info.setdefault("errors", {})[attr] = str(exc)
    try:
        info["ModelSpaceCount"] = call_with_retry(
            lambda: doc.ModelSpace.Count, label="doc.ModelSpace.Count"
        )
    except Exception as exc:  # noqa: BLE001
        info["ModelSpaceCount"] = None
        info.setdefault("errors", {})["ModelSpaceCount"] = str(exc)
    try:
        info["is_owned_by_server"] = is_owned_document(doc)
    except Exception:
        info["is_owned_by_server"] = None
    return info


def _active_document() -> Any:
    app = _attach_app()
    try:
        return call_with_retry(lambda: app.ActiveDocument, label="app.ActiveDocument")
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, AcadError):
            raise
        raise AcadError(f"AutoCAD is running but has no active document: {exc}") from exc


def get_active_document_info() -> dict[str, Any]:
    return _com(_describe_active, timeout=120)


def _describe_active() -> dict[str, Any]:
    app = _attach_app()
    result: dict[str, Any] = {}
    app_info: dict[str, Any] = {}
    for attr in ("Name", "Version", "Caption", "Visible"):
        try:
            app_info[attr] = call_with_retry(lambda a=attr: getattr(app, a), label=f"app.{attr}")
        except Exception:
            app_info[attr] = None
    result["application"] = app_info

    try:
        result["is_quiescent"] = call_with_retry(
            lambda: app.GetAcadState().IsQuiescent, label="IsQuiescent"
        )
    except Exception:
        result["is_quiescent"] = None

    try:
        result["document_count"] = call_with_retry(
            lambda: app.Documents.Count, attempts=8, label="Documents.Count"
        )
    except Exception as exc:  # noqa: BLE001
        result["document_count"] = None
        result["document_count_error"] = str(exc)

    doc = _active_document()
    result["active_document"] = _describe_document(doc)
    return result


def create_test_drawing(template: str | None = None, save_path: str | None = None) -> dict[str, Any]:
    """Create a NEW drawing owned by this server, leaving user drawings untouched."""
    return _com(_create_test_drawing_impl, template, save_path, timeout=180)


def _create_test_drawing_impl(template: str | None, save_path: str | None) -> dict[str, Any]:
    app = _attach_app()
    # A Tianzheng-enabled AutoCAD keeps loading ARX for a while after startup and
    # rejects COM meanwhile. Wait for quiescence, and retry the Add itself, which
    # can be rejected even when GetAcadState() already says quiet.
    wait_until_quiescent(app, timeout=60.0)

    def _add() -> Any:
        if template and os.path.exists(template):
            return app.Documents.Add(template)
        return app.Documents.Add()

    try:
        doc = call_with_retry(_add, attempts=40, delay=1.0, label="Documents.Add")
    except Exception as exc:  # noqa: BLE001
        raise AcadError(
            f"Documents.Add failed: {exc}. AutoCAD/Tianzheng did not become ready; "
            "if Tianzheng is still starting, wait for the splash to finish and retry."
        ) from exc

    # Register by the document's own Name too: a brand-new drawing has an empty
    # FullName, and SaveAs later changes it, so ownership must survive both.
    try:
        register_owned_document(doc)
    except Exception:
        pass
    try:
        _register_name(doc)
    except Exception:
        pass

    try:
        doc.Activate()
    except Exception:
        pass

    # Wait for AutoCAD to settle before touching the document again; a freshly
    # added drawing is briefly non-quiescent and rejects COM calls.
    wait_until_quiescent(app, timeout=25.0)

    saved_to = None
    save_warning = None
    if save_path:
        target = os.path.abspath(save_path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # AutoCAD refuses to overwrite a file that is currently open in another
        # document, and SaveAs to an in-use path raises a generic "保存文档时出错".
        for candidate in _save_candidates(target):
            try:
                doc.SaveAs(candidate)
                saved_to = candidate
                break
            except Exception as exc:  # noqa: BLE001
                save_warning = f"SaveAs({candidate}) failed: {exc}"
        if saved_to is None:
            # The drawing WAS created; report it rather than discarding it.
            info = _describe_document(doc)
            info["saved_to"] = None
            info["save_warning"] = save_warning
            info["note"] = (
                "Drawing created and usable, but could not be saved to the "
                "requested path. Commands can still run on it."
            )
            return info
        try:
            register_owned_document(doc)
            _register_name(doc)
        except Exception:
            pass

    info = _describe_document(doc)
    info["saved_to"] = saved_to
    if save_warning:
        info["save_warning"] = save_warning
    return info


def _register_name(doc: Any) -> None:
    try:
        name = str(call_with_retry(lambda: doc.Name, attempts=6, delay=0.3) or "")
    except Exception:
        return
    if not name:
        return
    key = name.strip().lower()
    with _owned_lock:
        _load_owned()
        if key not in _owned_docs:
            _owned_docs.add(key)
            _save_owned()


def _save_candidates(target: str) -> list[str]:
    """Target path first, then timestamped fallbacks if it is locked."""
    candidates = [target]
    stem, ext = os.path.splitext(target)
    stamp = time.strftime("%H%M%S")
    for i in range(1, 4):
        candidates.append(f"{stem}_{stamp}_{i}{ext}")
    return candidates


def close_owned_document(save: bool = False) -> dict[str, Any]:
    return _com(_close_owned_document_impl, save, timeout=120)


def _close_owned_document_impl(save: bool) -> dict[str, Any]:
    doc = _active_document()
    if not is_owned_document(doc):
        raise AcadError(
            "Refusing to close the active document because this server did not "
            "create it (it is probably the user's real drawing)."
        )
    name = _doc_key(doc)
    doc.Close(save)
    with _owned_lock:
        _owned_docs.discard(name)
    return {"closed": name, "saved": bool(save)}


# --------------------------------------------------------------------------- #
# Tianzheng / ARX load state
# --------------------------------------------------------------------------- #
TIANZHENG_CORE_MODULES = (
    "tch_kernal.arx",
    "tch_kerncmd.arx",
    "tch_common.arx",
    "tch_utility.arx",
    "tch_initstart.arx",
)


def _list_arx() -> list[str]:
    app = _attach_app()
    try:
        raw = app.ListArx()
    except Exception as exc:  # noqa: BLE001
        raise AcadError(f"Application.ListArx() failed: {exc}") from exc
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    try:
        return [str(item) for item in raw]
    except TypeError:
        return [str(raw)]


def check_tianzheng_loaded() -> dict[str, Any]:
    return _com(_check_tianzheng_loaded_impl, timeout=90)


def _check_tianzheng_loaded_impl() -> dict[str, Any]:
    arx = _list_arx()
    lowered = [a.lower().replace("/", "\\") for a in arx]
    present = {m: any(m.lower() in a for a in lowered) for m in TIANZHENG_CORE_MODULES}
    tch_all = sorted({a for a in arx if "tch" in a.lower()})
    loaded = present.get("tch_kernal.arx", False)
    return {
        "tianzheng_loaded": loaded,
        "tch_kernal_loaded": loaded,
        "core_modules": present,
        "tch_module_count": len(tch_all),
        "tch_modules": tch_all,
        "total_arx_count": len(arx),
        "note": (
            "tch_kernal.arx is loaded, so TCH_* commands and custom objects are available."
            if loaded
            else "tch_kernal.arx is NOT loaded: Tianzheng commands will answer 'unknown command'."
        ),
    }

def remember_active_document(doc: Any | None = None) -> dict:
    """Register the active/newly saved document as server-owned.

    Call this after SaveAs (or when adopting a drawing the same user just saved
    from this session), otherwise the safety guard keeps refusing it: a SaveAs
    changes the file name, so the old registered name no longer matches.
    """
    info = get_active_document_info() if doc is None else None
    target = doc
    if target is None:
        return {"registered": False, "reason": "no document"}
    try:
        register_owned_document(target)
    except Exception as exc:  # noqa: BLE001
        return {"registered": False, "reason": str(exc)}
    try:
        name = doc_name(target, "?")
    except Exception:
        name = "?"
    return {"registered": True, "document": name}
