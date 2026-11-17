"""nl_dispatch.py - turn a parsed plan into a real backend call + honest feedback.

Backends are imported lazily inside the executor so the parser and the whole
test suite work without pywin32 / mcp installed.  Tests can inject their own
executor and verify the exact command, inputs and verify_type that would be sent
to AutoCAD.
"""
from __future__ import annotations

import os
import time

from typing import Any, Callable

from . import nl_catalog, nl_parser
from .nl_parser import ParseResult

Executor = Callable[[ParseResult], dict[str, Any]]


def list_capabilities() -> dict[str, Any]:
    return {"ok": True, "intents": nl_catalog.supported_intents()}


def _parse_hint(issues: list[dict[str, Any]]) -> str:
    parts = [i.get("hint", "") for i in issues if i.get("hint")]
    parts.append("可调用 list_capabilities 查看所有支持的指令与示例。")
    return " ".join(p for p in parts if p)


def _execution_hint(plan: ParseResult, raw: dict[str, Any]) -> str:
    text = " ".join(
        str(raw.get(k, "") or "")
        for k in ("error", "diagnosis", "com_error", "detail", "note", "last_prompt")
    ).lower()
    if "焦点" in text or "focus" in text:
        return (
            "AutoCAD 窗口没有键盘焦点，交互式坐标送不进去。"
            "请用鼠标点一下 AutoCAD 的绘图区（或把它切到前台），然后重试。"
        )
    if "pc2t20" in text or "转化的图元" in text:
        return (
            "天正轴网进入了「PC2T20 选择需要转化的图元」分支，而不是「请选择插入点」分支，"
            "所以坐标无效。请手动打开天正「绘制轴网」对话框确认轴网数据后再试。"
        )
    if "refusing" in text or "did not create" in text:
        return "当前图纸不是本服务创建的。先说'新建一个测试图纸'，或设置 T20_ALLOW_FOREIGN_DOC=true。"
    if "unknown command" in text or "未知命令" in text:
        return "天正命令未被识别：请通过 TGStart.exe 启动 AutoCAD，并用 check_tianzheng_loaded 确认 ARX 已加载。"
    if "datastore.dbf" in text:
        return "天正数据文件只读导致命令被中止。运行 diagnose_environment 查看具体文件，按提示去掉只读属性后重启 CAD。"
    if "dialog" in text or "对话框" in text:
        return "命令卡在对话框上。请先关闭 AutoCAD 中的对话框，或用 probe_command 重新探测输入序列。"
    if plan.unstable:
        return "该命令的命令行序列尚未在本机验证：请用 probe_command 查看实体类型变化，再决定是否手动重试。"
    return "对照 execution.last_prompt 与 execution.sent_payload 检查输入顺序；必要时用 probe_command 探测。"


def _summary(plan: ParseResult, raw: dict[str, Any], success: bool) -> str:
    if plan.backend == "dialog_command":
        if not success:
            return f"{plan.command} 执行失败：{raw.get('error') or '未生成实体'}。"
        deltas = raw.get("type_deltas") or {}
        detail = "、".join(f"{k}+{v}" for k, v in deltas.items()) or "已生成"
        return f"已执行 {plan.command}（{detail}）。"
    if plan.backend == "inspect_frames":
        if not success:
            return f"识图失败：{raw.get('error') or '未找到图框'}"
        lines = []
        for f in (raw.get("detail") or [])[:8]:
            fields = f.get("fields") or {}
            label = fields.get("图名") or fields.get("名称") or fields.get("图纸名称") or "?"
            lines.append("%s(%s 实体)" % (label, f.get("entities_inside")))
        return "共 %d 个图框，共 %d 个实体：%s" % (
            raw.get("frames", 0), raw.get("entities_total", 0), "；".join(lines))
    if plan.backend == "layout_template":
        if not success:
            return f"套版失败：{raw.get('error') or '未知原因'}"
        c = raw.get("counts") or {}
        return (
            "已套版：平面 %d 件缩放 %.3f 倍排入左侧，门窗表 %d 件、说明 %d 件按原尺寸"
            "排在右侧，平面落在平面区内=%s。"
            % (c.get("plan", 0), raw.get("plan_scale") or 0, c.get("sheets", 0),
               c.get("notes", 0), raw.get("plan_inside_zone"))
        )
    if plan.backend == "layout":
        if not success:
            return f"排版失败：{raw.get('error') or '未知原因'}"
        area = raw.get("usable_area") or []
        return (
            "已排版：%d 个实体缩放 %.3f 倍并移入图框，可用区域 %s。"
            % (raw.get("entities_scaled") or 0, raw.get("scale_factor") or 0, area)
        )
    if plan.backend == "title_info":
        if not success:
            return f"填写图框失败：{raw.get('error') or '未写入任何属性'}。"
        applied = raw.get("applied") or []
        unique = list(dict.fromkeys(a["tag"] for a in applied))
        tags = "、".join(unique)
        note = "（已读回校验）" if raw.get("verified") else "（读回不一致）"
        return f"已填写图框 {len(unique)} 个字段：{tags}{note}。"
    if plan.backend == "probe_position":
        if not success:
            return f"定位自检失败：{raw.get('error') or '未取得落点'}。"
        err = raw.get("error") or {}
        return (
            f"目标 {raw.get('target')} → 实际 {raw.get('actual')}，"
            f"误差 {err.get('distance')} 单位（{err.get('pixels')} 像素）：{raw.get('verdict')}。"
        )
    if plan.backend == "tianzheng_title":
        size = raw.get("size") or plan.params.get("size") or "A3"
        if not success:
            return f"插入 {size} 图框失败：{raw.get('error') or '未生成图框'}。"
        deltas = raw.get("type_deltas") or {}
        detail = "、".join(f"{k}+{v}" for k, v in deltas.items()) or "无实体变化"
        return f"已插入 {size} 图框（{detail}）。"
    if plan.backend in ("tianzheng", "tianzheng_dialog", "tianzheng_custom", "tianzheng_annotate"):
        counts = raw.get("counts") or {}
        delta = counts.get("delta")
        detail = f"，{plan.verify_type or '模型实体总数'} +{delta}" if delta else ""
        verb = "已执行" if success else "执行失败"
        return f"{verb} {plan.command}{detail}。"
    if plan.backend == "query":
        if plan.intent == "count_entities":
            return f"{plan.params.get('entity_type')} 数量：{raw.get('count', '?')}。"
        return f"列出 {plan.params.get('entity_type')}：返回 {raw.get('returned', '?')} 个。"
    if plan.backend == "native":
        return ("已创建" if success else "创建失败") + f" {plan.command or plan.intent}（句柄 {raw.get('handle', '?')}）。"
    if plan.backend == "document":
        return ("已完成" if success else "失败") + f"：{plan.intent}。"
    if plan.backend == "diagnostic":
        return ("已获取" if success else "获取失败") + f"：{plan.intent}。"
    return raw.get("message", "") or plan.intent or ""


def _backend_success(plan: ParseResult, raw: dict[str, Any]) -> bool:
    """Judge backend success without assuming every tool returns `ok`.

    tianzheng_runner returns `success`, native_tools returns `ok`, but
    acad_conn.create_test_drawing and tianzheng_query.* return plain data
    dictionaries with neither flag.  Treating a missing flag as failure made
    real, successful operations (create drawing, count entities) report
    `ok: false`.  Explicit false wins; otherwise an error-looking field or an
    empty payload means failure, and a data payload means success.
    """
    if "ok" in raw:
        return bool(raw["ok"])
    if "success" in raw:
        return bool(raw["success"])
    if any(raw.get(key) for key in ("error", "error_type", "diagnosis", "exception")):
        return False
    if plan.intent == "diagnose_environment" and raw.get("found") is False:
        return False
    return bool(raw)


def _wrap(plan: ParseResult, raw: dict[str, Any]) -> dict[str, Any]:
    success = _backend_success(plan, raw)
    out: dict[str, Any] = {
        "ok": success,
        "stage": "execute",
        "parse": plan.to_dict(),
        "execution": raw,
        "summary": _summary(plan, raw, success),
    }
    if plan.warnings:
        out["warnings"] = list(plan.warnings)
    if not success:
        message = (
            raw.get("error") or raw.get("diagnosis") or raw.get("detail")
            or "后端未报告具体原因，请查看 execution 原始结果。"
        )
        out["errors"] = [{
            "code": "EXECUTION_FAILED",
            "message": str(message),
            "hint": _execution_hint(plan, raw),
        }]
        out["hint"] = out["errors"][0]["hint"]
    return out


def _made_progress(result: dict) -> bool:
    """True when the backend reported any created/changed entity."""
    if result.get("success") or result.get("ok"):
        return True
    counts = result.get("counts") or {}
    for key in ("delta", "delta_typed"):
        try:
            if int(counts.get(key) or 0) > 0:
                return True
        except Exception:
            pass
    try:
        if int(counts.get("model_space_after", 0)) > int(counts.get("model_space_before", 0)):
            return True
    except Exception:
        pass
    deltas = result.get("type_deltas") or {}
    return any(isinstance(v, int) and v > 0 for v in deltas.values())


def execute_plan(plan: ParseResult, auto_create_drawing: bool = True) -> dict[str, Any]:
    """Execute a validated plan against the live AutoCAD/Tianzheng bridge."""
    # Each backend is imported only inside its own branch: an unsupported intent
    # (or a pure query) must not fail just because an unrelated optional module
    # such as pywin32 is unavailable.
    backend = plan.backend
    params = plan.params

    if backend == "unsupported":
        return {
            "ok": False,
            "error": f"指令 '{plan.intent}' 属于纯界面/未验证命令，当前版本不做自动执行。",
            "detail": plan.note,
            "hint": "如需手动调用，可使用 run_tianzheng_command 并配合 probe_command 观察结果。",
        }

    if backend == "probe_position":
        from . import tianzheng_runner

        raw_point = params.get("point")
        if not raw_point:
            return {"ok": False, "error": "定位自检需要一个坐标，例如：定位自检 4200,3150"}
        try:
            wx, wy = [float(v) for v in str(raw_point).replace(";", ",").split(",")[:2]]
        except Exception:
            return {"ok": False, "error": f"无法解析坐标：{raw_point!r}"}
        report = tianzheng_runner.verify_position(wx, wy)
        report["ok"] = bool(report.get("success"))
        return report

    if backend == "tianzheng_title":
        from . import tianzheng_runner

        size = str(params.get("size") or "A3")
        insertion_point = params.get("insertion")
        return tianzheng_runner.insert_title_frame(size, insertion_point)

    if backend == "tianzheng_annotate":
        # Prompt-driven commands: the Y/N confirmation appears only sometimes, so
        # a fixed input list is wrong by construction. The runner reads the real
        # prompt after every step and decides there.
        from . import acad_conn, env_check, tianzheng_runner

        start = params.get("start_axis")
        end = params.get("end_axis")
        if not start or not end:
            return {
                "ok": False,
                "error": "轴网标注需要两个点：起始轴线上的点和终止轴线上的点。",
                "hint": "示例：轴网标注 0,1000 4800,1000",
            }

        def send_annotate() -> dict[str, Any]:
            return tianzheng_runner.annotate_axis(start, end)

        try:
            return send_annotate()
        except Exception as exc:  # noqa: BLE001
            if not auto_create_drawing or "Refusing" not in str(exc):
                raise
            acad_conn.create_test_drawing(env_check.template_for_autocad(None))
            return send_annotate()

    if backend == "inspect_frames":
        from . import tianzheng_runner

        report = tianzheng_runner.inspect_frames()
        report["ok"] = bool(report.get("success"))
        return report

    if backend == "layout_template":
        from . import tianzheng_runner

        report = tianzheng_runner.layout_template()
        report["ok"] = bool(report.get("success"))
        return report

    if backend == "layout":
        from . import tianzheng_runner

        report = tianzheng_runner.layout_to_frame()
        report["ok"] = bool(report.get("success"))
        return report

    if backend == "title_info":
        import re as _re

        from . import tianzheng_runner

        text = plan.parse_input if hasattr(plan, "parse_input") else ""
        if not text:
            text = (plan.to_dict().get("normalized") or "") if hasattr(plan, "to_dict") else ""
        pairs = {}
        for match in _re.finditer(r"([^\s=]+)=([^=]+?)(?=\s+[^\s=]+=|\s*$)", text or ""):
            key = match.group(1).strip()
            value = match.group(2).strip()
            if key and value:
                pairs[key] = value
        if not pairs:
            return {"ok": False,
                    "error": "没有识别到 标签=值。示例：填写图框 名称=阳光小区 图号=建施-05"}
        report = tianzheng_runner.set_title_attributes(pairs)
        report["ok"] = bool(report.get("success"))
        return report

    if backend == "dialog_command":
        from . import nl_catalog, tianzheng_runner

        spec = nl_catalog.get_spec(plan.intent or "")
        point = params.get("insertion")
        if not point:
            return {"ok": False, "error": f"{plan.intent} 需要一个插入点。"}
        edits = {}
        if plan.intent == "multiline_text" and params.get("text"):
            edits[4] = params["text"]  # 多行文字正文在第 5 个输入框
        report = tianzheng_runner.run_dialog_command(
            command=plan.command or "",
            dialog_title=(spec.dialog_title if spec else "") or "",
            point=str(point),
            edit_values=edits,
            verify_type=plan.verify_type,
        )
        report["ok"] = bool(report.get("success"))
        return report

    if backend == "tianzheng_custom" and plan.intent == "section_floor":
        from . import tianzheng_runner  # noqa: F401

        if not params.get("thickness"):
            params["thickness"] = 200
            plan.inputs = [str(params.get("p1")), str(params.get("p2")), "200"]

    if backend == "tianzheng_custom" and plan.intent == "annotate_elevation":
        from . import tianzheng_runner  # noqa: F401  (name is used further below)

        # TMElev wants a second "direction" point; derive it above the symbol point
        if not params.get("direction"):
            try:
                px, py = [float(v) for v in str(params.get("point")).split(",")[:2]]
                params["direction"] = f"{px:g},{py + 600:g}"
            except Exception:
                params["direction"] = "0,600"
            # plan.inputs was built during parsing, so it must be rebuilt here or
            # the command still receives the placeholder for the missing point.
            plan.inputs = [str(params["point"]), str(params["direction"])]

    if backend == "tianzheng_custom":
        # Commands whose prompt chain needs literal tokens (selection keywords like
        # L / ALL) interleaved with user values. Measured chains live in the
        # catalog's input_template; a optional pre_step creates geometry first
        # (楼板 needs a closed polyline it can select with `L`).
        from . import acad_conn, env_check, native_tools, nl_catalog, tianzheng_runner

        spec = nl_catalog.get_spec(plan.intent or "")
        verify_layer = spec.verify_layer if spec else None
        boundary: dict[str, Any] | None = None

        def build_inputs() -> list[str]:
            out: list[str] = []
            for token in (spec.input_template if spec else ()):
                if token.startswith("{") and token.endswith("}"):
                    value = params.get(token[1:-1])
                    out.append("" if value in (None, "") else str(value))
                else:
                    out.append(token)
            return out

        def prepare() -> None:
            nonlocal boundary
            if spec and spec.pre_step == "closed_polyline":
                points = params.get("points") or []
                if len(points) < 3:
                    raise ValueError("楼板边界至少需要 3 个点。")
                boundary = native_tools.draw_closed_polyline(points)

        def send_templated() -> dict[str, Any]:
            prepare()
            result = tianzheng_runner.run_with_dialog(
                command=plan.command or "",
                inputs=build_inputs(),
                verify_type=plan.verify_type,
                dialog_title=None,
                timeout=25.0,
                settle=1.8,
                verify_layer=verify_layer,
            )
            if boundary:
                result["boundary"] = boundary
            return result

        try:
            return send_templated()
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not auto_create_drawing or "Refusing" not in str(exc):
                raise
            acad_conn.create_test_drawing(env_check.template_for_autocad(None))
            return send_templated()

    if backend == "tianzheng_dialog":
        from . import acad_conn, env_check, nl_catalog, tianzheng_runner

        spec = nl_catalog.get_spec(plan.intent or "")
        edits: dict[int, Any] = {}
        if spec and spec.dialog_width_edit is not None:
            # 门窗参数 visible-edit order: 0=编号 1=高 2=宽 3=门槛/台高 4=距离 5=个数
            if "width" in params:
                edits[spec.dialog_width_edit] = params["width"]
            if "height" in params:
                edits[1] = params["height"]
            if "sill" in params:
                edits[3] = params["sill"]

        insertion = params.get("insertion")
        inputs = [insertion, ""] if insertion else [""]

        def send_dialog() -> dict[str, Any]:
            attempts = (
                {"settle": 1.6, "timeout": 25.0},
                {"settle": 2.4, "timeout": 30.0},
            )
            last: dict[str, Any] = {}
            for index, cfg in enumerate(attempts):
                last = tianzheng_runner.run_with_dialog(
                    command=plan.command or "",
                    inputs=inputs,
                    verify_type=plan.verify_type,
                    dialog_title=spec.dialog_title if spec else None,
                    dialog_edits=edits,
                    timeout=cfg["timeout"],
                    settle=cfg["settle"],
                )
                if last.get("success"):
                    if index:
                        last["retried"] = index
                    return last
            last["retried"] = len(attempts) - 1
            return last

        try:
            return send_dialog()
        except Exception as exc:  # noqa: BLE001
            if not auto_create_drawing or "Refusing to send commands" not in str(exc):
                raise
            created = acad_conn.create_test_drawing(env_check.template_for_autocad(None))
            retry = send_dialog()
            retry["auto_created_drawing"] = created
            return retry

    if backend == "tianzheng":
        from . import acad_conn, env_check, nl_catalog, tianzheng_runner

        spec = nl_catalog.get_spec(plan.intent or "")
        verify_layer = spec.verify_layer if spec else None

        def send() -> dict[str, Any]:
            # Stepwise execution: the command is posted, then each input is typed
            # like a user. Posting the whole payload at once races with the prompt
            # (measured: HZZW/TGColumn silently did nothing), while stepwise works
            # for every command proven so far, including pick-style ones.
            attempts = (
                {"settle": 1.8, "timeout": 20.0},
                {"settle": 2.6, "timeout": 26.0},
            )
            last: dict[str, Any] = {}
            for index, cfg in enumerate(attempts):
                last = tianzheng_runner.run_with_dialog(
                    command=plan.command or "",
                    inputs=plan.inputs,
                    verify_type=plan.verify_type,
                    dialog_title=None,
                    dialog_edits=None,
                    timeout=cfg["timeout"],
                    settle=cfg["settle"],
                    verify_layer=verify_layer,
                )
                if last.get("success"):
                    if index:
                        last["retried"] = index
                    return last
            # Never report success without evidence: return the real failure.
            last["retried"] = len(attempts) - 1
            return last

        try:
            return send()
        except Exception as exc:  # noqa: BLE001
            if not auto_create_drawing or "Refusing to send commands" not in str(exc):
                raise
            # Safe default: open a server-owned drawing, then retry once.
            created = acad_conn.create_test_drawing(env_check.template_for_autocad(None))
            retry = send()
            retry["auto_created_drawing"] = created
            return retry

    if backend == "native":
        from . import native_tools

        if plan.intent == "draw_line":
            return native_tools.draw_line(params["start"], params["end"], params.get("layer"))
        if plan.intent == "draw_circle":
            return native_tools.draw_circle(params["center"], params["radius"], params.get("layer"))
        if plan.intent == "add_text":
            return native_tools.add_text(
                params["insertion"], params["text"], params.get("height", 250.0), params.get("layer")
            )
        raise ValueError(f"未实现的原生命令: {plan.intent}")

    if backend == "query":
        from . import tianzheng_query

        if plan.intent == "count_entities":
            return tianzheng_query.count_entities(params["entity_type"])
        if plan.intent == "list_entities":
            return tianzheng_query.list_entities(params["entity_type"])
        raise ValueError(f"未实现的查询命令: {plan.intent}")

    if backend == "document":
        from . import acad_conn, env_check, native_tools

        if plan.intent == "new_test_drawing":
            return acad_conn.create_test_drawing(env_check.template_for_autocad(None))
        if plan.intent == "export_dxf":
            return native_tools.export_dxf(params.get("path"))
        if plan.intent == "save_drawing":
            return native_tools.save_drawing(params.get("path"))
        if plan.intent == "zoom_extents":
            return native_tools.zoom_extents()
        raise ValueError(f"未实现的文档命令: {plan.intent}")

    if backend == "diagnostic":
        from . import acad_conn, env_check

        if plan.intent == "check_tianzheng_loaded":
            return acad_conn.check_tianzheng_loaded()
        if plan.intent == "diagnose_environment":
            return env_check.diagnose(None)
        if plan.intent == "get_active_document":
            return acad_conn.get_active_document_info()
        raise ValueError(f"未实现的诊断命令: {plan.intent}")

    raise ValueError(f"未知后端: {backend}")


def execute_batch(texts: list[str], *, stop_on_error: bool = False,
                  delay: float = 0.35) -> dict[str, Any]:
    """Run several instructions in order and report each result.

    Useful for scripts and for an agent that already knows the plan; it is not a
    preset. Every step goes through the normal parser/dispatcher, so validation and
    the entity-count verification stay identical.
    """
    import time as _time

    results = []
    ok_count = 0
    for index, text in enumerate(texts):
        item = {"index": index, "instruction": text}
        try:
            res = execute(text)
            item["ok"] = bool(res.get("ok"))
            item["summary"] = res.get("summary")
            item["result"] = res
        except Exception as exc:  # noqa: BLE001
            item["ok"] = False
            item["error"] = f"{type(exc).__name__}: {exc}"
        if item["ok"]:
            ok_count += 1
        results.append(item)
        if not item["ok"] and stop_on_error:
            break
        _time.sleep(delay)

    return {
        "ok": ok_count == len(results) and ok_count == len(texts),
        "total": len(texts),
        "succeeded": ok_count,
        "failed": len(results) - ok_count,
        "stopped_early": len(results) < len(texts),
        "steps": results,
    }


def execute(
    text: str,
    *,
    executor: Executor | None = None,
    dry_run: bool = False,
    auto_create_drawing: bool = True,
) -> dict[str, Any]:
    """Parse, validate, execute and report.  Never sends an invalid plan onward."""
    plan = nl_parser.parse(text)
    envelope: dict[str, Any] = {"ok": False, "parse": plan.to_dict()}

    if not plan.ok:
        envelope["stage"] = "parse"
        envelope["errors"] = [issue.to_dict() for issue in plan.issues]
        envelope["hint"] = _parse_hint(envelope["errors"])
        return envelope

    if dry_run:
        envelope.update({
            "ok": True,
            "stage": "preview",
            "would_execute": {
                "intent": plan.intent,
                "backend": plan.backend,
                "command": plan.command,
                "inputs": plan.inputs,
                "params": plan.params,
                "verify_type": plan.verify_type,
                "unstable": plan.unstable,
            },
        })
        if plan.warnings:
            envelope["warnings"] = list(plan.warnings)
        return envelope

    fn = executor or (lambda p: execute_plan(p, auto_create_drawing=auto_create_drawing))
    try:
        raw = fn(plan)
        # One safe retry for a drawing command that produced NOTHING.
        #
        # MEASURED: individual Tianzheng commands occasionally do nothing
        # (HZQT dropped one wall segment out of five in the same run, TUpdSpace
        # failed on a second identical call). Retrying only when the entity count
        # did not change is safe: a command that already created something is never
        # repeated, so nothing is drawn twice.  T20_RETRY_ON_FAILURE=0 disables it.
        if (
            isinstance(raw, dict)
            and not raw.get("success", raw.get("ok", True))
            and not _made_progress(raw)
            and (executor is None)
            and os.environ.get("T20_RETRY_ON_FAILURE", "1").strip().lower()
            not in ("0", "false", "no", "off")
        ):
            first_error = raw.get("error") or raw.get("diagnosis") or "no entities created"
            time.sleep(1.2)
            retried = fn(plan)
            if isinstance(retried, dict):
                retried["retried_after_failure"] = True
                retried["first_attempt_error"] = str(first_error)[:200]
            raw = retried
    except Exception as exc:  # noqa: BLE001
        envelope.update({
            "stage": "execute",
            "execution": {"error": str(exc), "error_type": type(exc).__name__},
            "errors": [{
                "code": "EXECUTION_EXCEPTION",
                "message": str(exc),
                "hint": _execution_hint(plan, {"error": str(exc)}),
            }],
        })
        envelope["hint"] = envelope["errors"][0]["hint"]
        return envelope

    return _wrap(plan, raw)
