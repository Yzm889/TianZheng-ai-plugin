"""native_tools.py - plain AutoCAD COM drawing helpers.

These exist to (a) prove the COM link works, and (b) provide auxiliary geometry.
They deliberately use only the native AcDb entities, which AutoCAD's COM layer
can create directly. Tianzheng custom objects cannot be created this way - that
is what tianzheng_runner is for.
"""
from __future__ import annotations

from typing import Any

import pythoncom
import win32com.client

from .acad_conn import (
    AcadError,
    _active_document,
    _attach_app,
    _com,
    allow_foreign_document,
    doc_name,
    is_owned_document,
)


def _guard(doc: Any) -> None:
    if not is_owned_document(doc) and not allow_foreign_document():
        raise AcadError(
            "Refusing to modify "
            f"'{doc_name(doc, '?')}' because this server did not create it. "
            "Call create_test_drawing first, or set T20_ALLOW_FOREIGN_DOC=true."
        )


def _xyz(point: Any) -> tuple[float, float, float]:
    if isinstance(point, str):
        parts = [p.strip() for p in point.replace(";", ",").split(",") if p.strip() != ""]
        coords = [float(p) for p in parts]
    else:
        coords = [float(v) for v in point]
    while len(coords) < 3:
        coords.append(0.0)
    return coords[0], coords[1], coords[2]


def _variant_point(point: Any) -> Any:
    """Wrap coordinates in a VARIANT array of doubles.

    AutoCAD's type library declares these parameters as VARIANT (VT_ARRAY of
    VT_R8). A plain Python tuple is marshalled as VT_DISPATCH/VT_VARIANT and the
    call fails with -2147024809 (invalid argument). This is the fix.
    """
    x, y, z = _xyz(point)
    if pythoncom is None:
        return (x, y, z)
    try:
        return win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R8, (x, y, z)
        )
    except Exception:
        return (x, y, z)


def draw_line(start: Any, end: Any, layer: str | None = None) -> dict[str, Any]:
    return _com(_draw_line_impl, start, end, layer, timeout=90)


def _draw_line_impl(start: Any, end: Any, layer: str | None) -> dict[str, Any]:
    app = _attach_app()
    doc = _active_document()
    _guard(doc)
    try:
        line = doc.ModelSpace.AddLine(_variant_point(start), _variant_point(end))
    except Exception as exc:  # noqa: BLE001
        raise AcadError(f"AddLine failed: {exc}") from exc
    if layer:
        try:
            line.Layer = layer
        except Exception:
            pass
    return {"ok": True, "handle": str(getattr(line, "Handle", "")), "object_name": getattr(line, "ObjectName", "AcDbLine")}


def draw_circle(center: Any, radius: float, layer: str | None = None) -> dict[str, Any]:
    return _com(_draw_circle_impl, center, float(radius), layer, timeout=90)


def _draw_circle_impl(center: Any, radius: float, layer: str | None) -> dict[str, Any]:
    doc = _active_document()
    _guard(doc)
    try:
        circle = doc.ModelSpace.AddCircle(_variant_point(center), radius)
    except Exception as exc:  # noqa: BLE001
        raise AcadError(f"AddCircle failed: {exc}") from exc
    if layer:
        try:
            circle.Layer = layer
        except Exception:
            pass
    return {"ok": True, "handle": str(getattr(circle, "Handle", "")), "object_name": getattr(circle, "ObjectName", "AcDbCircle")}


def add_text(insertion: Any, text: str, height: float = 250.0, layer: str | None = None) -> dict[str, Any]:
    return _com(_add_text_impl, insertion, str(text), float(height), layer, timeout=90)


def _add_text_impl(insertion: Any, text: str, height: float, layer: str | None) -> dict[str, Any]:
    doc = _active_document()
    _guard(doc)
    try:
        entity = doc.ModelSpace.AddText(text, _variant_point(insertion), height)
    except Exception as exc:  # noqa: BLE001
        raise AcadError(f"AddText failed: {exc}") from exc
    if layer:
        try:
            entity.Layer = layer
        except Exception:
            pass
    return {"ok": True, "handle": str(getattr(entity, "Handle", "")), "object_name": getattr(entity, "ObjectName", "AcDbText")}


def save_drawing(path: str | None = None) -> dict[str, Any]:
    return _com(_save_impl, path, timeout=180)


def _save_impl(path: str | None) -> dict[str, Any]:
    doc = _active_document()
    if not is_owned_document(doc) and not allow_foreign_document():
        raise AcadError(
            "Refusing to save "
            f"'{doc_name(doc, '?')}' because this server did not create it. "
            "That is the user's own drawing; saving it is not this server's call."
        )
    try:
        if path:
            doc.SaveAs(path)
        else:
            doc.Save()
    except Exception as exc:  # noqa: BLE001
        raise AcadError(f"Save failed: {exc}") from exc
    return {
        "ok": True,
        "document": doc_name(doc),
        "full_name": getattr(doc, "FullName", None),
        "saved": getattr(doc, "Saved", None),
    }


def zoom_extents() -> dict[str, Any]:
    return _com(_zoom_impl, timeout=90)


def _zoom_impl() -> dict[str, Any]:
    app = _attach_app()
    doc = _active_document()
    try:
        app.ZoomExtents()
        return {"ok": True, "document": doc_name(doc)}
    except Exception:
        try:
            doc.SendCommand("_.ZOOM\n_E\n")
            return {"ok": True, "document": doc_name(doc), "method": "ZOOM command"}
        except Exception as exc:  # noqa: BLE001
            raise AcadError(f"Zoom failed: {exc}") from exc
