"""tianzheng_query.py - enumerate Tianzheng custom objects and native entities.

Tianzheng objects (TCH_WALL, TCH_OPENING, TCH_CORNER_WINDOW, ...) are ARX custom
objects. They are NOT reachable through COM object creation, but they ARE
readable once they exist in a document: COM exposes them as generic
AcadEntity-ish proxies whose ObjectName reports the DXF type name.
"""
from __future__ import annotations

from typing import Any

from .acad_conn import AcadError, _com, _active_document, call_with_retry

# Tianzheng custom object type names.
#
# IMPORTANT (measured on T20-Arch V10.0 + AutoCAD 2022):
#   The DXF *class* name is TCH_WALL, but the name AutoCAD reports through COM
#   ObjectName is "TDbWall", and the histogram key becomes "TDBWALL".
#   Both spellings are therefore accepted for every type.
KNOWN_TCH_TYPES = (
    "TCH_WALL",
    "TDbWall",
    "TCH_OPENING",
    "TDbOpening",
    "TCH_CORNER_WINDOW",
    "TCH_COLUMN",
    "TCH_AXIS_LABEL",
    "TCH_SPACE",
    "TCH_STAIR",
    "TCH_BALCONY",
    "TCH_SLAB",
    "TCH_ROOF",
)

# Explicit alias table: DXF/class name  ->  accepted ObjectName spellings.
TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "TCH_WALL": ("TCH_WALL", "TDBWALL", "TDbWall"),
    "TDBWALL": ("TCH_WALL", "TDBWALL", "TDbWall"),
    "TCH_OPENING": ("TCH_OPENING", "TDBOPENING", "TDbOpening", "TCH_DOOR", "TCH_WINDOW"),
    "TCH_CORNER_WINDOW": ("TCH_CORNER_WINDOW", "TDBOPENING", "TDbOpening"),
    # ZWBZ (轴网标注) reports its axis-number object as TDbAxisLabelSet, while the
    # documented class is TCH_AXIS_LABEL.
    "TCH_AXIS_LABEL": ("TCH_AXIS_LABEL", "TDBAXISLABELSET", "TDbAxisLabelSet"),
    # Dimensions created by ZWBZ come back as TDbDimension2.
    "TCH_DIMENSION2": ("TCH_DIMENSION2", "TDBDIMENSION2", "TDbDimension2"),
}

# Family prefixes: a request for TCH_WALL should also match anything that is
# clearly a wall variant, but never a different object family.
_FAMILY_HINTS = {
    "TCH_WALL": ("WALL",),
    "TDBWALL": ("WALL",),
}


def _object_name(entity: Any) -> str | None:
    for attr in ("ObjectName", "EntityName", "ObjectID"):
        try:
            value = getattr(entity, attr)
        except Exception:
            continue
        if attr == "ObjectID":
            continue
        if value:
            return str(value)
    # Last resort: DXF-ish name through the entity's own property bag.
    try:
        return str(entity.EntityName)
    except Exception:
        return None


def normalize_type(name: str) -> str:
    """Normalize a type name so 'AcDbLine', 'line', 'TCH_WALL' compare sanely."""
    if not name:
        return ""
    out = name.strip().upper()
    if out.startswith("ACDB"):
        out = out[4:]
    return out


def _canon(name: str) -> str:
    """Reduce a Tianzheng/native class name to a comparable token.

    MEASURED: the same object is named differently at different layers.
      DXF/class name : TCH_COLUMN   (what documentation and createobjcmd.ini say)
      COM ObjectName : TDbColumn    (what Entity.ObjectName actually returns)
    Stripping the variant prefixes from both sides makes every type match without
    needing a hand-maintained alias for each one (walls worked only because an
    alias had been added by hand; columns did not, and real columns were reported
    as "0 created" while they were visibly there).
    """
    token = (name or "").strip().upper().replace("_", "").replace("-", "")
    if token.startswith("ACDB"):
        token = token[4:]
    for prefix in ("TCH", "TDB"):
        if token.startswith(prefix):
            token = token[len(prefix):]
            break
    return token


def _matches(requested: str, actual: str | None) -> bool:
    if not actual:
        return False
    want = normalize_type(requested)
    got = normalize_type(actual)
    if want == got:
        return True
    # 'TCH_WALL' should also match a reported 'ACDBTCH_WALL'.
    if got.endswith(want) or want.endswith(got):
        return True
    # Canonical comparison handles TCH_COLUMN vs TDbColumn and every other pair.
    if _canon(requested) and _canon(requested) == _canon(actual):
        return True
    # Explicit alias table (TCH_WALL <-> TDbWall).
    aliases = TYPE_ALIASES.get(want)
    if aliases and got in {normalize_type(a) for a in aliases}:
        return True
    # Family fallback: accept wall-family objects for a wall request.
    for family, hints in _FAMILY_HINTS.items():
        if want == family:
            if any(h in got for h in hints):
                return True
    return False


def _read_attr(entity: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        try:
            value = getattr(entity, name)
        except Exception:
            continue
        if value is None:
            continue
        if isinstance(value, (int, float, str, bool)):
            return value
        try:
            # COM point arrays come back as tuples/list of floats.
            if hasattr(value, "__len__"):
                return [round(float(v), 4) for v in value]
        except Exception:
            return None
    return None


def _bbox(entity: Any) -> dict[str, Any] | None:
    try:
        min_pt, max_pt = entity.GetBoundingBox()
    except Exception:
        return None
    try:
        return {
            "min": [round(float(v), 4) for v in min_pt],
            "max": [round(float(v), 4) for v in max_pt],
        }
    except Exception:
        return None


def _handle(entity: Any) -> str | None:
    try:
        return str(entity.Handle)
    except Exception:
        return None


def count_entities(entity_type: str) -> dict[str, Any]:
    """Count entities of a type in the active document's ModelSpace."""
    return _com(_count_impl, entity_type, timeout=120)


def _model_space(doc: Any) -> Any:
    """Return ModelSpace, retrying while AutoCAD reports itself busy."""
    return call_with_retry(lambda: doc.ModelSpace, label="doc.ModelSpace")


def _count_doctype(msp: Any) -> int:
    return int(call_with_retry(lambda: msp.Count, label="ModelSpace.Count"))


def _count_impl(entity_type: str) -> dict[str, Any]:
    doc = _active_document()
    try:
        msp = _model_space(doc)
        total = _count_doctype(msp)
    except Exception as exc:  # noqa: BLE001
        raise AcadError(f"Cannot read ModelSpace: {exc}") from exc

    matched = 0
    seen: dict[str, int] = {}
    for index in range(total):
        try:
            entity = call_with_retry(lambda i=index: msp.Item(i), attempts=4, delay=0.2)
        except Exception:
            continue
        name = _object_name(entity)
        if name:
            key = normalize_type(name)
            seen[key] = seen.get(key, 0) + 1
        if _matches(entity_type, name):
            matched += 1

    return {
        "entity_type": entity_type,
        "count": matched,
        "model_space_total": total,
        "document": _safe_name(doc),
        "observed_types": dict(
            sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:20]
        ),
    }


def _safe_name(doc: Any) -> str | None:
    try:
        return str(call_with_retry(lambda: doc.Name, attempts=3, delay=0.2))
    except Exception:
        return None


def list_entities(entity_type: str, limit: int = 50) -> dict[str, Any]:
    """List entities of a type with best-effort readable geometry."""
    return _com(_list_impl, entity_type, limit, timeout=120)


def _list_impl(entity_type: str, limit: int) -> dict[str, Any]:
    doc = _active_document()
    msp = _model_space(doc)
    total = _count_doctype(msp)
    items: list[dict[str, Any]] = []

    for index in range(total):
        if len(items) >= limit:
            break
        try:
            entity = call_with_retry(lambda i=index: msp.Item(i), attempts=4, delay=0.2)
        except Exception:
            continue
        name = _object_name(entity)
        if not _matches(entity_type, name):
            continue

        record: dict[str, Any] = {
            "handle": _handle(entity),
            "object_name": name,
            "layer": _read_attr(entity, ("Layer",)),
            "index": index,
        }
        # Generic geometry hints; Tianzheng walls expose some of these.
        for label, attrs in (
            ("start", ("StartPoint",)),
            ("end", ("EndPoint",)),
            ("center", ("Center",)),
            ("insertion", ("InsertionPoint",)),
            ("text", ("TextString", "Contents")),
            ("radius", ("Radius",)),
            ("length", ("Length",)),
        ):
            value = _read_attr(entity, attrs)
            if value is not None:
                record[label] = value
        bbox = _bbox(entity)
        if bbox:
            record["bbox"] = bbox
        items.append(record)

    return {
        "entity_type": entity_type,
        "returned": len(items),
        "model_space_total": total,
        "document": _safe_name(doc),
        "entities": items,
    }


def entity_type_histogram() -> dict[str, Any]:
    """Count every entity type present, so callers can discover TCH type names."""
    return _com(_histogram_impl, timeout=180)


def _histogram_impl() -> dict[str, Any]:
    doc = _active_document()
    msp = _model_space(doc)
    total = _count_doctype(msp)
    seen: dict[str, int] = {}
    for index in range(total):
        try:
            entity = call_with_retry(lambda i=index: msp.Item(i), attempts=4, delay=0.2)
        except Exception:
            continue
        name = _object_name(entity)
        key = normalize_type(name) if name else "<unknown>"
        seen[key] = seen.get(key, 0) + 1
    return {
        "document": _safe_name(doc),
        "model_space_total": total,
        "types": dict(sorted(seen.items(), key=lambda kv: kv[1], reverse=True)),
    }
