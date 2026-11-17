"""scene_build.py —— 分阶段建造 + 批量执行 + 实体反射。

借鉴 CodexAutoCadMcpBridge 的三点：
  1. 分阶段（walls / openings / dimensions）各自独立，失败保留已完成部分，
     并返回 completed_stages / failed_stage / partial_result；
  2. 一次调用批量建多面墙（走 template_clone，不调天正命令）；
  3. 实体反射：按原生类名统计、拿样本句柄、读块内对象。

保留本项目自己的优点：自然语言指令、实体计数验证、安全检查、中文报错。
"""
from __future__ import annotations

import time
from typing import Any

from . import template_clone, tianzheng_runner
from .acad_conn import AcadError, _active_document, _com, allow_foreign_document, is_owned_document


def _ensure_writable(doc: Any) -> None:
    if not is_owned_document(doc) and not allow_foreign_document():
        raise AcadError(
            f"Refusing to modify '{getattr(doc, 'Name', '?')}': this server did not create it."
        )


def summarize_entity_classes(custom_only: bool = False, limit: int = 5000,
                             timeout: float = 180.0) -> dict[str, Any]:
    """按原生类名统计模型空间实体，并给出每类样本句柄（只读）。"""
    return _com(_summarize_impl, bool(custom_only), int(limit), timeout=timeout)


def _summarize_impl(custom_only: bool, limit: int) -> dict[str, Any]:
    doc = _active_document()
    try:
        n = min(doc.ModelSpace.Count, limit)
    except Exception:
        n = 0
    classes: dict[str, dict[str, Any]] = {}
    for i in range(n):
        try:
            e = doc.ModelSpace.Item(i)
            name = str(e.ObjectName)
        except Exception:
            continue
        if custom_only and not any(k in name.upper() for k in ("TDb", "TCH", "TDB")):
            continue
        rec = classes.setdefault(name, {"count": 0, "samples": []})
        rec["count"] += 1
        if len(rec["samples"]) < 5:
            try:
                rec["samples"].append(str(e.Handle))
            except Exception:
                pass
    return {
        "ok": True,
        "scanned": n,
        "class_count": len(classes),
        "classes": classes,
    }


def list_block_entities(block_handle: str, limit: int = 5000,
                        timeout: float = 180.0) -> dict[str, Any]:
    """列出一个块参照内部的对象（含嵌套的天正墙/门窗句柄）。只读。"""
    return _com(_block_impl, str(block_handle), int(limit), timeout=timeout)


def _block_impl(block_handle: str, limit: int) -> dict[str, Any]:
    from win32com.client import CastTo

    doc = _active_document()
    target = None
    try:
        n = doc.ModelSpace.Count
    except Exception:
        n = 0
    for i in range(n):
        try:
            e = doc.ModelSpace.Item(i)
            if str(getattr(e, "Handle", "")).upper() == block_handle.upper():
                target = e
                break
        except Exception:
            continue
    if target is None:
        raise AcadError(f"block reference {block_handle} not found")
    try:
        br = CastTo(target, "IAcadBlockReference")
        name = str(br.Name)
    except Exception as exc:  # noqa: BLE001
        raise AcadError(f"{block_handle} is not a block reference: {exc}")

    blk = None
    for i in range(doc.Blocks.Count):
        b = doc.Blocks.Item(i)
        if str(b.Name) == name:
            blk = b
            break
    if blk is None:
        raise AcadError(f"block definition {name!r} not found")

    out: dict[str, dict[str, Any]] = {}
    total = min(blk.Count, limit)
    for i in range(total):
        try:
            e = blk.Item(i)
            t = str(e.ObjectName)
            h = str(e.Handle)
        except Exception:
            continue
        rec = out.setdefault(t, {"count": 0, "samples": []})
        rec["count"] += 1
        if len(rec["samples"]) < 8:
            rec["samples"].append(h)
    return {"ok": True, "block": name, "entities": total, "classes": out}


def build_plan(spec: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
    """分阶段建图：walls -> openings -> dimensions。

    spec = {
      "origin": [x, y],
      "stage": "all" | "walls" | "openings" | "dimensions",
      "walls":         [{"x1","y1","x2","y2"}, ...],
      "wall_template": "句柄",            # 省略则自动扫第一面墙
      "openings":      [{"type","x","y"}, ...],
      "opening_templates": {"door": "句柄", "window": "句柄"},
      "dimensions":    [{"kind": "linear"|"axis", "p1": [...], "p2": [...]}, ...]
    }
    """
    return _com(_build_impl, dict(spec or {}), float(timeout), timeout=timeout + 120.0)


def _build_impl(spec: dict[str, Any], timeout: float) -> dict[str, Any]:
    doc = _active_document()
    _ensure_writable(doc)

    stage = str(spec.get("stage", "all")).lower()
    if stage not in ("all", "walls", "openings", "dimensions"):
        raise AcadError("stage 只能是 all / walls / openings / dimensions")

    origin = spec.get("origin") or [0, 0]
    ox, oy = float(origin[0]), float(origin[1])

    result: dict[str, Any] = {
        "ok": True,
        "stage": stage,
        "completed_stages": [],
        "wall_count": 0,
        "wall_handles": [],
        "opening_count": 0,
        "opening_handles": [],
        "dimension_count": 0,
        "dimension_handles": [],
    }

    def fail(where: str, exc: Exception) -> dict[str, Any]:
        result["ok"] = False
        result["failed_stage"] = where
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["partial_result"] = bool(result["completed_stages"])
        return result

    # ---------------- walls ----------------
    if stage in ("all", "walls"):
        walls = spec.get("walls") or []
        if not walls:
            return fail("walls", AcadError("walls 不能为空"))
        try:
            tpl = spec.get("wall_template")
            if not tpl:
                scan = template_clone._scan_impl()          # in-process, no _com
                found = (scan.get("templates") or {}).get("TDBWALL") or []
                if not found:
                    raise AcadError("图里没有墙模板；请先画一面墙或用 wall_template 指定句柄")
                tpl = found[0]["handle"]
            segs = [
                {"x1": float(w["x1"]) + ox, "y1": float(w["y1"]) + oy,
                 "x2": float(w["x2"]) + ox, "y2": float(w["y2"]) + oy}
                for w in walls
            ]
            # NOTE: never call a _com-wrapped helper from inside _build_impl --
            # that re-enters the single COM worker thread and deadlocks (measured:
            # the first version hung forever on scan_templates()).
            r = template_clone._clone_walls_impl(tpl, segs)
            result["wall_count"] = r.get("created_count", 0)
            result["wall_handles"] = [c["handle"] for c in r.get("created", [])]
            result["wall_errors"] = r.get("errors", [])
            result["completed_stages"].append("walls")
            time.sleep(float(spec.get("wall_settle", 0.6)))
        except Exception as exc:  # noqa: BLE001
            return fail("walls", exc)

    # ---------------- openings ----------------
    if stage in ("all", "openings") and spec.get("openings"):
        try:
            tpls = spec.get("opening_templates") or {}
            if not tpls:
                scan = template_clone._scan_impl()          # in-process, no _com
                op = (scan.get("templates") or {}).get("TDBOPENING") or []
                if not op:
                    raise AcadError("图里没有门窗模板；请先放一樘门/窗或用 opening_templates 指定")
                # a single template serves both when types are unknown
                tpls = {"door": op[0]["handle"], "window": op[0]["handle"]}
            made, errs = [], []
            for idx, item in enumerate(spec["openings"]):
                kind = str(item.get("type", "door")).lower()
                h = tpls.get(kind) or tpls.get("door")
                if not h:
                    errs.append(f"opening {idx}: no template for {kind}")
                    continue
                try:
                    r = template_clone._clone_impl(
                        h, [{"x": float(item["x"]) + ox, "y": float(item["y"]) + oy}],
                        float(item.get("rotate", 0.0)),
                    )
                    made.extend(r.get("created", []))
                    errs.extend(r.get("errors", []))
                except Exception as exc:  # noqa: BLE001
                    errs.append(f"opening {idx}: {exc}")
            result["opening_count"] = len(made)
            result["opening_handles"] = [m["handle"] for m in made]
            result["opening_errors"] = errs[:10]
            result["completed_stages"].append("openings")
        except Exception as exc:  # noqa: BLE001
            return fail("openings", exc)

    # ---------------- dimensions ----------------
    if stage in ("all", "dimensions") and spec.get("dimensions"):
        try:
            made, errs = [], []
            for idx, d in enumerate(spec["dimensions"]):
                try:
                    kind = str(d.get("kind", "linear"))
                    if kind == "axis":
                        # annotate_axis / run_with_dialog are _com-wrapped too, so they
                        # cannot be nested inside build_plan without deadlocking the
                        # single COM worker thread. Report clearly instead.
                        errs.append(
                            "dimension %d: 轴网类尺寸请单独调用 annotate_axis"
                            "（build_plan 内部不能再嵌套 COM 调用）" % idx)
                        continue
                    p1 = d["p1"]; p2 = d["p2"]; pos = d.get("pos", p1)
                    rr = tianzheng_runner._run_with_dialog_impl(
                        "TDimMP",
                        [f"{float(p1[0]) + ox:g},{float(p1[1]) + oy:g}",
                         f"{float(p2[0]) + ox:g},{float(p2[1]) + oy:g}",
                         f"{float(pos[0]) + ox:g},{float(pos[1]) + oy:g}", ""],
                        "TCH_DIMENSION2", None, None, 25.0, 1.6, None)
                    if rr.get("success"):
                        made.append(idx)
                    else:
                        errs.append(f"dimension {idx}: {rr.get('error') or 'failed'}")
                except Exception as exc:  # noqa: BLE001
                    errs.append(f"dimension {idx}: {exc}")
            result["dimension_count"] = len(made)
            result["dimension_handles"] = made
            result["dimension_errors"] = errs[:10]
            result["completed_stages"].append("dimensions")
        except Exception as exc:  # noqa: BLE001
            return fail("dimensions", exc)

    return result
