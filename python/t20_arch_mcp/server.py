"""server.py - MCP entry point for the T20-Arch V10.0 bridge.

Runs as a stdio MCP server. Tools are intentionally few and shallow: the
generic `run_tianzheng_command` covers any Tianzheng command, and the remaining
tools only read state or draw native AutoCAD entities.
"""
from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import acad_conn, env_check, native_tools, nl_dispatch, tianzheng_query, tianzheng_runner
from .tianzheng_query import KNOWN_TCH_TYPES

SERVER_NAME = "t20_arch"

INSTRUCTIONS = """
Bridge to a running AutoCAD instance with T20-Arch (Tianzheng Architecture) loaded.

PREFERRED ENTRY POINT for Chinese natural-language requests:
  * Call natural_language(text) first. It deterministically maps the sentence to
    a catalogued command, normalises coordinates/units, validates required
    parameters and returns structured Chinese errors instead of guessing.
  * Use preview_natural_language(text) to dry-run the mapping when unsure.
  * Use list_capabilities() to see all supported intents and example sentences.
  * Only fall back to the low-level run_tianzheng_command when a command is not
    in the catalogue, and pass verify_type explicitly whenever possible.

Key facts for callers:
  * Tianzheng objects (TCH_WALL, TCH_OPENING, TCH_CORNER_WINDOW, ...) are ARX custom
    objects. They cannot be created through COM or entmake. You must issue a real
    Tianzheng command with run_tianzheng_command.
  * Command aliases come from SYS\\acad.pgp. Verified on this install:
        HZQT  -> TGWall          (draw wall)
        HZZW  -> TRectAxis       (draw rectangular axis grid)
        ZWBZ  -> TMultAxisDim    (axis annotation)
        TCGL  -> TLayerManager   (layer manager)
    The underlying English command names (TGWall, TRectAxis, ...) also work.
  * SendCommand returns nothing. Success is judged ONLY by the entity count
    increasing. run_tianzheng_command does that comparison for you.
  * Call create_test_drawing before issuing commands so you never modify the
    user's open drawings.
""".strip()

mcp = MCPServer(name=SERVER_NAME, instructions=INSTRUCTIONS)


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


# --------------------------------------------------------------------------- #
# Connection / document
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="get_active_document",
    description=(
        "Report the AutoCAD application version and the active document path/name over COM. "
        "Use this first to confirm the bridge is attached to a running AutoCAD with Tianzheng."
    ),
)
def get_active_document() -> str:
    try:
        return _dump({"ok": True, **acad_conn.get_active_document_info()})
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc), "error_type": type(exc).__name__})


@mcp.tool(
    name="create_test_drawing",
    description=(
        "Create a NEW drawing that this server owns, and optionally save it to a path, so "
        "Tianzheng commands never touch the user's real drawings. "
        "Both arguments are optional: with no template_path the Tianzheng template matching "
        "the running AutoCAD version is found automatically, and with no save_path the "
        "drawing stays in memory."
    ),
)
def create_test_drawing(template_path: str | None = None, save_path: str | None = None) -> str:
    try:
        auto_template = None
        if not template_path:
            try:
                version = acad_conn.get_active_document_info()["application"].get("Version")
            except Exception:
                version = None
            auto_template = env_check.template_for_autocad(version)
        info = acad_conn.create_test_drawing(template_path or auto_template, save_path)
        return _dump(
            {
                "ok": True,
                "created": info,
                "template_used": template_path or auto_template,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc), "error_type": type(exc).__name__})


@mcp.tool(
    name="diagnose_environment",
    description=(
        "Report the Tianzheng installation: install roots, per-AutoCAD-version SYS folders, "
        "and whether the critical data files (DataStore.dbf, tchdata.dbf) are writable. "
        "A read-only DataStore.dbf makes Tianzheng abort commands such as HZQT with a modal "
        "dialog, which looks like silent failure - run this first when commands do nothing. "
        "This tool only reports; it never modifies your Tianzheng installation."
    ),
)
def diagnose_environment(tianzheng_root: str | None = None) -> str:
    try:
        report = env_check.diagnose(tianzheng_root)
        try:
            version = acad_conn.get_active_document_info()["application"].get("Version")
        except Exception:
            version = None
        report["autocad_com_version"] = version
        report["auto_template"] = env_check.template_for_autocad(version)
        return _dump({"ok": True, **report})
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(
    name="close_test_drawing",
    description="Close the drawing this server created. Refuses to close the user's own drawings.",
)
def close_test_drawing(save: bool = False) -> str:
    try:
        return _dump({"ok": True, **acad_conn.close_owned_document(save)})
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


# --------------------------------------------------------------------------- #
# Tianzheng load state
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="check_tianzheng_loaded",
    description=(
        "Confirm the Tianzheng ARX modules (especially tch_kernal.arx) are loaded in the "
        "running AutoCAD. Without them every TCH_* command answers 'unknown command'. "
        "Do not skip this: a bare acad.exe has no Tianzheng commands."
    ),
)
def check_tianzheng_loaded() -> str:
    try:
        return _dump({"ok": True, **acad_conn.check_tianzheng_loaded()})
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


# --------------------------------------------------------------------------- #
# Natural language entry point (preferred for Chinese instructions)
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="natural_language",
    description=(
        "Execute a Chinese natural-language instruction end to end: parse intent, "
        "normalise points/units, validate parameters, dispatch to the CAD backend, "
        "and report success or an actionable Chinese error. "
        "Examples: '从 0,0 到 7000,0 画一堵墙', '统计墙的数量', "
        "'在 100,100 画半径 500 的圆', '保存图纸为 C:\\temp\\a.dwg'. "
        "Set dry_run=true to only preview the mapping. The response always contains "
        "a 'parse' block (what was understood) and, when executed, an 'execution' block."
    ),
)
def natural_language(text: str, dry_run: bool = False, auto_create_drawing: bool = True) -> str:
    try:
        return _dump(nl_dispatch.execute(text, dry_run=dry_run, auto_create_drawing=auto_create_drawing))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc), "error_type": type(exc).__name__})


@mcp.tool(
    name="preview_natural_language",
    description=(
        "Dry-run a Chinese instruction: show the recognised intent, CAD command, "
        "exact command-line inputs, verify_type and any warnings without touching AutoCAD."
    ),
)
def preview_natural_language(text: str) -> str:
    try:
        return _dump(nl_dispatch.execute(text, dry_run=True))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc), "error_type": type(exc).__name__})


@mcp.tool(
    name="list_capabilities",
    description="List every supported natural-language intent, its aliases, example sentence and stability note.",
)
def list_capabilities() -> str:
    try:
        return _dump(nl_dispatch.list_capabilities())
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


# --------------------------------------------------------------------------- #
# Core: run a Tianzheng command
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="run_tianzheng_command",
    description=(
        "Send a Tianzheng command (e.g. HZQT, HZZW, TGWall, TRectAxis) followed by an ordered "
        "list of command-line inputs, then verify success by entity-count change. "
        "inputs items are typed at the command line; an empty string means 'press Enter'. "
        "Points are 'x,y' or 'x,y,z' in drawing units. "
        "verify_type is the entity type expected to increase, e.g. TCH_WALL (T20 reports walls "
        "as ObjectName 'TDbWall', and both spellings are accepted). "
        "Success means that type's count actually increased - SendCommand gives no other signal. "
        "mode='async' (default) uses PostCommand and survives Tianzheng UI panels; "
        "mode='sync' uses the blocking SendCommand and is only safe for non-UI commands."
    ),
)
def run_tianzheng_command(
    command: str,
    inputs: list[str] | None = None,
    verify_type: str | None = None,
    timeout_seconds: float = 20.0,
    mode: str = "async",
    auto_dismiss_ui: bool = True,
) -> str:
    try:
        result = tianzheng_runner.run_command(
            command=command,
            inputs=inputs or [],
            verify_type=verify_type,
            timeout=timeout_seconds,
            mode=mode,
            auto_dismiss_ui=auto_dismiss_ui,
        )
        return _dump({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc), "error_type": type(exc).__name__, "command": command})


@mcp.tool(
    name="probe_command",
    description=(
        "Send a command and report which entity types appeared (full before/after histogram). "
        "Use this when you do not know the exact TCH type name a command produces."
    ),
)
def probe_command(
    command: str,
    inputs: list[str] | None = None,
    timeout_seconds: float = 20.0,
    mode: str = "async",
) -> str:
    try:
        return _dump(
            {"ok": True, **tianzheng_runner.probe_command(command, inputs or [], timeout_seconds, mode)}
        )
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc), "command": command})


# --------------------------------------------------------------------------- #
# Query
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="get_entity_count",
    description=(
        "Count entities of a type in the active drawing's ModelSpace. "
        "Use before and after run_tianzheng_command as the objective success test. "
        f"Known Tianzheng types: {', '.join(KNOWN_TCH_TYPES)}"
    ),
)
def get_entity_count(entity_type: str) -> str:
    try:
        return _dump({"ok": True, **tianzheng_query.count_entities(entity_type)})
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc), "entity_type": entity_type})


@mcp.tool(
    name="list_tianzheng_entities",
    description=(
        "List entities of a type (for example TCH_WALL) with handle, layer, endpoints and "
        "bounding box where the object exposes them."
    ),
)
def list_tianzheng_entities(entity_type: str, limit: int = 50) -> str:
    try:
        return _dump({"ok": True, **tianzheng_query.list_entities(entity_type, limit)})
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc), "entity_type": entity_type})


@mcp.tool(
    name="entity_type_histogram",
    description="Count every entity type present in ModelSpace. Useful to discover TCH_* type names.",
)
def entity_type_histogram() -> str:
    try:
        return _dump({"ok": True, **tianzheng_query.entity_type_histogram()})
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


# --------------------------------------------------------------------------- #
# Native AutoCAD helpers
# --------------------------------------------------------------------------- #
@mcp.tool(name="draw_line", description="Draw a native AcDbLine via COM (connection/geometry helper).")
def draw_line(start: str, end: str, layer: str | None = None) -> str:
    try:
        return _dump(native_tools.draw_line(start, end, layer))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(name="draw_circle", description="Draw a native AcDbCircle via COM.")
def draw_circle(center: str, radius: float, layer: str | None = None) -> str:
    try:
        return _dump(native_tools.draw_circle(center, radius, layer))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(name="add_text", description="Add a native AcDbText entity via COM.")
def add_text(insertion: str, text: str, height: float = 250.0, layer: str | None = None) -> str:
    try:
        return _dump(native_tools.add_text(insertion, text, height, layer))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(name="save_drawing", description="Save the server-owned active drawing (Save or SaveAs).")
def save_drawing(path: str | None = None) -> str:
    try:
        return _dump(native_tools.save_drawing(path))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(name="zoom_extents", description="Zoom the active view to drawing extents.")
def zoom_extents() -> str:
    try:
        return _dump(native_tools.zoom_extents())
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


def main() -> None:
    transport = os.environ.get("T20_MCP_TRANSPORT", "stdio")
    if transport != "stdio":
        # Useful while developing; dsh uses stdio.
        mcp.run(transport=transport)  # type: ignore[arg-type]
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
