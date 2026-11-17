"""template_clone.py —— 模板克隆：用"复制已有天正对象"代替"调天正命令"。

为什么这样做（借鉴 CodexAutoCadMcpBridge 的思路，但用纯 COM 实现，无需编译 DLL）：
  天正的 HZQT / MC 是界面命令，靠 PostCommand + 键盘输入驱动时极易崩溃
  （实测 MC 在 AutoCAD 2022 上三次崩于 885F217Dh）。而天正对象本身是可以正常
  复制、移动、缩放、旋转的普通 AcDbEntity —— 只要图纸里已经有同类对象，就可以
  把它当成"模板"，Copy 一份再 Move/Scale 到目标位置，全程不碰任何对话框。

需要在图里先有一份模板（用户手动插一次，或用 clone_template_scan 找到现成的）。
"""
from __future__ import annotations

import math
from typing import Any

from .acad_conn import AcadError, _active_document, _attach_app, _com, allow_foreign_document, is_owned_document


def _bbox(ent: Any) -> tuple[float, float, float, float] | None:
    try:
        mn, mx = ent.GetBoundingBox()
        return (float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1]))
    except Exception:
        return None


def _vpt(x: float, y: float, z: float = 0.0):
    import pythoncom
    from win32com.client import VARIANT

    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), float(z)))


def scan_templates(timeout: float = 120.0) -> dict[str, Any]:
    """列出图纸里可作为模板的天正对象（按类型分组，给出句柄与包围盒）。"""
    return _com(_scan_impl, timeout=timeout)


def _scan_impl() -> dict[str, Any]:
    doc = _active_document()
    groups: dict[str, list[dict[str, Any]]] = {}
    try:
        n = doc.ModelSpace.Count
    except Exception:
        n = 0
    for i in range(n):
        try:
            e = doc.ModelSpace.Item(i)
            name = str(e.ObjectName).upper()
        except Exception:
            continue
        if not any(k in name for k in ("WALL", "OPENING", "COLUMN", "STAIR", "SPACE", "SLAB")):
            continue
        box = _bbox(e)
        entry = {
            "handle": str(getattr(e, "Handle", "")),
            "type": name,
            "layer": str(getattr(e, "Layer", "")),
            "bbox": [round(v, 1) for v in box] if box else None,
        }
        if box:
            entry["size"] = [round(box[2] - box[0], 1), round(box[3] - box[1], 1)]
        groups.setdefault(name, []).append(entry)

    result: dict[str, Any] = {
        "ok": bool(groups),
        "found": {k: len(v) for k, v in groups.items()},
        "templates": groups,
    }
    if groups:
        result["hint"] = (
            "拿一个句柄当模板：clone_from_template(handle, targets) 或 clone_walls_batch。"
        )
    else:
        # Ask the user for the exact component instead of searching install folders.
        from . import template_request

        result["need_user_action"] = True
        result["requirements"] = template_request.all_requirements()
        lines = ["图纸里还没有可克隆的天正对象。请在天正里插入需要的模板：", ""]
        for k, v in template_request.all_requirements().items():
            lines.append("· %s：命令 %s（%s）" % (v["name"], v["command"], v["spec"]))
        result["message"] = "\n".join(lines)
    return result


def clone_from_template(template_handle: str, targets: list[dict[str, Any]],
                        rotate_deg: float = 0.0, timeout: float = 180.0) -> dict[str, Any]:
    """把一个已有天正对象复制成多个，并移动到目标位置。"""
    return _com(_clone_impl, str(template_handle), list(targets), float(rotate_deg),
                timeout=timeout)


def _clone_impl(template_handle: str, targets: list[dict[str, Any]],
                rotate_deg: float) -> dict[str, Any]:
    doc = _active_document()
    if not is_owned_document(doc) and not allow_foreign_document():
        raise AcadError(
            f"Refusing to modify '{getattr(doc, 'Name', '?')}': this server did not create it."
        )

    # locate the template by handle
    tpl = None
    try:
        n = doc.ModelSpace.Count
    except Exception:
        n = 0
    for i in range(n):
        try:
            e = doc.ModelSpace.Item(i)
            if str(getattr(e, "Handle", "")).upper() == template_handle.upper():
                tpl = e
                break
        except Exception:
            continue
    if tpl is None:
        raise AcadError(f"template handle {template_handle} not found in model space")

    tbox = _bbox(tpl)
    if not tbox:
        raise AcadError(f"template {template_handle} has no bounding box")
    tcx, tcy = (tbox[0] + tbox[2]) / 2.0, (tbox[1] + tbox[3]) / 2.0
    tsize = (tbox[2] - tbox[0], tbox[3] - tbox[1])

    created: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, tgt in enumerate(targets):
        try:
            cx = float(tgt["x"])
            cy = float(tgt["y"])
        except Exception:
            errors.append(f"target {idx}: x/y required")
            continue
        try:
            # COM Copy() adds the new entity to its owning collection itself;
            # IAcadModelSpace has no AddItem (that call raised AttributeError and
            # left orphan copies behind, so it must not be attempted).
            copy = tpl.Copy()
            # move so the template centre lands on the target
            base = _vpt(tcx, tcy)
            dest = _vpt(cx, cy)
            copy.Move(base, dest)
            if rotate_deg:
                copy.Rotate(_vpt(cx, cy), math.radians(rotate_deg))
            if tgt.get("layer"):
                copy.Layer = str(tgt["layer"])
            created.append({"handle": str(getattr(copy, "Handle", "")), "at": [cx, cy]})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"target {idx}: {type(exc).__name__}: {exc}")

    return {
        "ok": bool(created),
        "template": template_handle,
        "template_size": [round(v, 1) for v in tsize],
        "created_count": len(created),
        "failed_count": len(errors),
        "created": created,
        "errors": errors[:10],
    }


def clone_walls_batch(template_handle: str, segments: list[dict[str, Any]],
                      timeout: float = 240.0) -> dict[str, Any]:
    """用模板墙批量生成墙段：每条 {x1,y1,x2,y2}。

    模板墙被拉伸/旋转对齐到目标段（按包围盒长度比例缩放 + 旋转）。
    """
    return _com(_clone_walls_impl, str(template_handle), list(segments), timeout=timeout)


def _clone_walls_impl(template_handle: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    doc = _active_document()
    if not is_owned_document(doc) and not allow_foreign_document():
        raise AcadError("Refusing to modify a drawing this server did not create.")

    tpl = None
    try:
        n = doc.ModelSpace.Count
    except Exception:
        n = 0
    for i in range(n):
        try:
            e = doc.ModelSpace.Item(i)
            if str(getattr(e, "Handle", "")).upper() == template_handle.upper():
                tpl = e
                break
        except Exception:
            continue
    if tpl is None:
        raise AcadError(f"wall template {template_handle} not found")

    tbox = _bbox(tpl)
    if not tbox:
        raise AcadError("wall template has no bbox")
    tw = tbox[2] - tbox[0]
    th = tbox[3] - tbox[1]
    tl = max(tw, th)                      # template length
    tcx, tcy = (tbox[0] + tbox[2]) / 2.0, (tbox[1] + tbox[3]) / 2.0
    vertical_tpl = th > tw

    made, failed = [], []
    for idx, seg in enumerate(segments):
        try:
            x1, y1, x2, y2 = (float(seg[k]) for k in ("x1", "y1", "x2", "y2"))
        except Exception:
            failed.append(f"segment {idx}: needs x1,y1,x2,y2")
            continue
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length <= 0 or tl <= 0:
            failed.append(f"segment {idx}: zero length")
            continue
        try:
            cp = tpl.Copy()
            # scale to the target length, then rotate to the segment direction
            if not math.isclose(length, tl, rel_tol=1e-6):
                cp.ScaleEntity(_vpt(tcx, tcy), length / tl)
            angle = math.degrees(math.atan2(dy, dx))
            if vertical_tpl:
                angle -= 90.0
            if abs(angle) > 1e-6:
                cp.Rotate(_vpt(tcx, tcy), math.radians(angle))
            cp.Move(_vpt(tcx, tcy), _vpt((x1 + x2) / 2.0, (y1 + y2) / 2.0))
            if seg.get("layer"):
                cp.Layer = str(seg["layer"])
            made.append({"handle": str(getattr(cp, "Handle", "")), "length": round(length, 1)})
        except Exception as exc:  # noqa: BLE001
            failed.append(f"segment {idx}: {type(exc).__name__}: {exc}")

    return {
        "ok": bool(made),
        "template": template_handle,
        "created_count": len(made),
        "failed_count": len(failed),
        "created": made,
        "errors": failed[:10],
    }
