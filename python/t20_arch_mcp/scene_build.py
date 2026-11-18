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

from . import opening_library, template_clone, template_request, tianzheng_runner
from .acad_conn import (AcadError, _active_document, _com, allow_foreign_document,
                        doc_name, is_owned_document)


def _opening_kind(item: dict[str, Any]) -> str:
    return opening_library.normalize_kind(item.get("type"))


def _opening_width(item: dict[str, Any]) -> float | None:
    raw = item.get("width")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        width = float(raw)
    except (TypeError, ValueError):
        return None
    return width if width > 0 else None


def _opening_pools(spec: dict[str, Any], doc_key: str | None):
    """整理可用门窗模板，返回 (pools, ask)。

    ask 非空表示模板不够用，要把"请你建什么"的说明直接返回给用户。

    模板来源优先级：
      1) spec 显式给的 opening_templates（可带 width）
      2) 本图已经登记过宽度的模板（register_opening_template）
      3) 扫图得到的 TDbOpening，只接受几何上能确定宽度的
    """
    tpls = spec.get("opening_templates") or {}
    if tpls:
        return ({k: opening_library.as_candidates(tpls.get(k), doc_key)
                 for k in ("door", "window")}, None)

    registered = opening_library.entries(doc_key)
    pools = {k: [{"handle": e["handle"], "width": e.get("width")}
                 for e in registered
                 if opening_library.normalize_kind(e.get("kind")) == k]
             for k in ("door", "window")}
    if pools["door"] or pools["window"]:
        return pools, None

    scan = template_clone._scan_impl()          # in-process, no _com
    found = (scan.get("templates") or {}).get("TDBOPENING") or []
    if not found:
        return {}, template_request.missing_template_request(
            "TDBOPENING", "插门窗需要一樘门或窗作为模板")

    cands = []
    for entry in found:
        # 登记过就用登记值；否则只接受包围盒能判定出来的宽度（短边是墙厚的情况）
        width = entry.get("declared_width")
        if width is None:
            width = entry.get("width_hint")
        cands.append({"handle": entry["handle"], "width": width})
    return {"door": cands, "window": cands}, None


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
      "openings":      [{"type","x","y","width"?, "rotate"?}, ...],
      "opening_templates": {"door": <句柄 | {handle,width} | [{handle,width}]>, "window": ...},
      "dimensions":    [{"kind": "linear"|"axis", "p1": [...], "p2": [...], "pos": [...]}, ...]
    }

    门窗宽度：天正的门窗宽度是内部参数，克隆只能原样复制，所以"不同宽度需要
    不同模板"。build_plan 的处理是：
      · 给了 width -> 必须找到登记过相同宽度的模板，找不到就一次性把"要建
        哪一樘、多宽、怎么登记"告诉你，不会默默用一个宽度不对的模板；
      · 没给 width -> 用第一个模板，并在结果里标 width_unverified + 警告文案。
    模板宽度的来源：spec 里写的、或 register_opening_template 登记过的。

    dimensions 的两种 kind：
      linear —— 逐点标注 TDimMP，p1/p2/pos 三个点。
      axis   —— 轴网标注 ZWBZ，p1/p2 必须落在真实轴线上（天正画在 DOTE 图层的
                LINE）；这一点由 build_plan 预检，没轴线就直接报「先画轴网」，
                不会盲发命令。

    F5 说明：轴网标注同样支持在本函数内部执行。关键在于只调用裸实现
    `_annotate_axis_impl`（它不经过 _com），而不是 `annotate_axis`（包了 _com）。
    后者会在同一个 COM 工作线程里等自己，永久死锁。
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
                    # Do NOT go hunting through install folders; tell the user
                    # exactly what to create and then stop.
                    return template_request.missing_template_request(
                        "TDBWALL", "建墙需要一面墙体模板")
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
            doc_key = doc_name(doc, None)
            tol = float(spec.get("opening_width_tolerance",
                                 opening_library.DEFAULT_TOLERANCE))
            pools, ask = _opening_pools(spec, doc_key)
            if ask:
                result["ok"] = False
                result["failed_stage"] = "openings"
                result["partial_result"] = bool(result["completed_stages"])
                result.update(ask)
                return result

            # 开工前预检：先确认每种宽度都有对应模板，缺什么一次性说清楚，
            # 不要先克隆一半再半路卡住（用户明确要求过这一点）。
            resolved: list[dict[str, Any]] = []
            missing: list[dict[str, Any]] = []
            prescan_errors: list[str] = []
            for idx, item in enumerate(spec["openings"]):
                kind = _opening_kind(item)
                width = _opening_width(item)
                cand, reason = opening_library.select(pools.get(kind) or [], kind,
                                                      width, tol)
                if cand is None and width is None and not pools.get(kind):
                    # 没给宽度时保留旧行为：只有一个模板也能用（结果里会标未核实）。
                    # 给了宽度就不跨类型借用模板，否则会做出"用门模板生成窗"的错件。
                    cand, reason = opening_library.select(pools.get("door") or [],
                                                          kind, width, tol)
                if cand is None:
                    if width is not None:
                        # 要了宽度却没有任何能对上号的模板 -> 一律转成"请你建"，
                        # 包括该类型模板一个都没有的情况。
                        missing.append({"index": idx, "kind": kind, "width": width})
                    else:
                        prescan_errors.append(f"opening {idx}: 没有可用的{kind}模板")
                    continue
                resolved.append({"index": idx, "item": item, "kind": kind,
                                 "width": width, "candidate": cand, "reason": reason})

            if missing:
                first = missing[0]
                ask = template_request.missing_opening_width(first["kind"],
                                                             first["width"], doc_key)
                ask["missing_widths"] = missing
                result["failed_stage"] = "openings"
                result["ok"] = False
                result["partial_result"] = bool(result["completed_stages"])
                result.update(ask)
                return result

            made, errs, width_plan = [], [], []
            for plan in resolved:
                item = plan["item"]
                try:
                    r = template_clone._clone_impl(
                        plan["candidate"]["handle"],
                        [{"x": float(item["x"]) + ox, "y": float(item["y"]) + oy}],
                        float(item.get("rotate", 0.0)),
                    )
                    made.extend(r.get("created", []))
                    errs.extend(r.get("errors", []))
                    width_plan.append({
                        "index": plan["index"],
                        "kind": plan["kind"],
                        "requested_width": plan["width"],
                        "template": plan["candidate"]["handle"],
                        "matched_width": plan["candidate"].get("width"),
                        "status": ("matched" if plan["reason"] == "matched"
                                   else "width_unverified"),
                    })
                except Exception as exc:  # noqa: BLE001
                    errs.append(f"opening {plan['index']}: {exc}")

            result["opening_count"] = len(made)
            result["opening_handles"] = [m["handle"] for m in made]
            result["opening_sizes"] = [m.get("size") for m in made]
            result["opening_errors"] = (prescan_errors + errs)[:10]
            result["opening_width_plan"] = width_plan
            unverified = [w for w in width_plan if w["status"] == "width_unverified"]
            if unverified:
                result["width_warning"] = (
                    "有 %d 个门窗没有给宽度、模板宽度也未登记，克隆出来的宽度等于模板宽度。"
                    "要用指定宽度，请先 register_opening_template 登记模板宽度。" % len(unverified))
            result["completed_stages"].append("openings")
        except Exception as exc:  # noqa: BLE001
            return fail("openings", exc)


    # ---------------- dimensions ----------------
    if stage in ("all", "dimensions") and spec.get("dimensions"):
        try:
            made, errs = [], []
            dote_lines: int | None = None  # 只为轴网预检算一次，用来留实测证据
            for idx, d in enumerate(spec["dimensions"]):
                try:
                    kind = str(d.get("kind", "linear"))
                    if kind == "axis":
                        # F5: annotate_axis() is _com-wrapped and would deadlock the
                        # single COM worker thread we are already running on. The
                        # bare _annotate_axis_impl() does no _com hop, so it is safe
                        # to call from here.
                        p1, p2 = d.get("p1"), d.get("p2")
                        if not p1 or not p2:
                            errs.append(
                                "dimension %d: kind=axis 需要 p1/p2"
                                "（两个精确落在轴线上的点）" % idx)
                            continue
                        if dote_lines is None:
                            dote_lines = int(
                                tianzheng_runner._count_type(doc, "LINE", layer="DOTE"))
                            result["axis_dote_lines"] = dote_lines
                        if dote_lines == 0:
                            # ZWBZ 只认天正自己生成的轴线。先要组件，别盲发命令。
                            errs.append(
                                "dimension %d: 图中没有天正轴线（DOTE 图层的 LINE），"
                                "轴网标注只能标真实轴线。请先用「绘制轴网 HZZW」建轴网，"
                                "或改用 kind=linear 做逐点标注。" % idx)
                            continue
                        rr = tianzheng_runner._annotate_axis_impl(
                            f"{float(p1[0]) + ox:g},{float(p1[1]) + oy:g}",
                            f"{float(p2[0]) + ox:g},{float(p2[1]) + oy:g}",
                            float(d.get("timeout", 30.0)),
                        )
                        if rr.get("success"):
                            made.append(idx)
                            result.setdefault("axis_labels", []).append(
                                {"index": idx, "counts": rr.get("counts")})
                        else:
                            errs.append("dimension %d: %s" % (
                                idx,
                                rr.get("diagnosis") or rr.get("error") or "failed"))
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
