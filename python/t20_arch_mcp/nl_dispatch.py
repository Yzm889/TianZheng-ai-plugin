"""nl_dispatch.py - turn a parsed plan into a real backend call + honest feedback.

Backends are imported lazily inside the executor so the parser and the whole
test suite work without pywin32 / mcp installed.  Tests can inject their own
executor and verify the exact command, inputs and verify_type that would be sent
to AutoCAD.
"""
from __future__ import annotations

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
        for k in ("error", "diagnosis", "com_error", "detail")
    ).lower()
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
    if plan.backend == "tianzheng":
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

    if backend == "tianzheng":
        from . import acad_conn, env_check, tianzheng_runner

        def send() -> dict[str, Any]:
            return tianzheng_runner.run_command(
                command=plan.command or "",
                inputs=plan.inputs,
                verify_type=plan.verify_type,
                mode="async",
                auto_dismiss_ui=True,
            )

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
