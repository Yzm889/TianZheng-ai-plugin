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

from . import (acad_conn, env_check, native_tools, nl_dispatch, opening_library,
               plot_export, scene_build, template_clone, tianzheng_query,
               tianzheng_runner)
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


@mcp.tool(
    name="inspect_frames",
    description=(
        "READ-ONLY: list every title frame in the active drawing with its title-block "
        "fields (图名/比例/图号), its rectangle and the number of entities inside it. "
        "A real sheet usually holds several frames (one per floor plan); use this to "
        "understand the drawing before modifying anything."
    ),
)
def inspect_frames() -> str:
    try:
        return _dump(tianzheng_runner.inspect_frames())
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(
    name="export_dxf",
    description=(
        "Save the active drawing as DXF (any CAD can open it). Uses file SaveAs, so it "
        "works even where PDF plotting is unavailable. Optional path; defaults to the "
        "user's home folder."
    ),
)
def export_dxf(path: str | None = None) -> str:
    try:
        return _dump(native_tools.export_dxf(path))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(
    name="run_instructions",
    description=(
        "Run several natural-language instructions in order and report each result. "
        "Each step goes through the same parser and entity-count verification as a "
        "single call. Example: [\"画墙 0,0 8400,0 8400,6300 0,6300 0,0\", "
        "\"搜索房间，标注位置 4200,3000\"]."
    ),
)
def run_instructions(instructions: list[str], stop_on_error: bool = False) -> str:
    try:
        return _dump(nl_dispatch.execute_batch(list(instructions), stop_on_error=stop_on_error))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(
    name="scan_templates",
    description=(
        "READ-ONLY: list Tianzheng objects usable as clone templates (walls, openings, "
        "columns, stairs, rooms) with their handles, layers, bounding boxes and sizes. "
        "Template cloning lets walls/openings be created without driving the fragile "
        "Tianzheng UI dialogs."
    ),
)
def scan_templates() -> str:
    try:
        return _dump(template_clone.scan_templates())
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(
    name="register_opening_template",
    description=(
        "Record the REAL width (mm) of a door/window template that already exists in "
        "the active drawing. MEASURED: Tianzheng exposes no width over COM -- GetXData "
        "returns [null,null], Width/Height attributes are absent, and the bounding box "
        "mixes width with height (a 1000-wide window measures 1000x975), so the width "
        "must be told once by the caller. build_plan then only clones templates whose "
        "recorded width matches the requested width; when none matches it asks the user "
        "to create one at that width instead of silently cloning a wrong-width opening."
    ),
)
def register_opening_template(handle: str, width: float, kind: str = "door",
                              height: float = 0.0) -> str:
    try:
        document = acad_conn.active_document_name()
        result = opening_library.register(
            handle, width, kind,
            height=(height or None),
            document=document,
        )
        result["document"] = document
        return _dump(result)
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(
    name="list_opening_templates",
    description=(
        "READ-ONLY: list door/window template widths recorded for the active drawing "
        "(or a given document name). Use it to see which widths build_plan can honour."
    ),
)
def list_opening_templates(document: str = "") -> str:
    try:
        scope = document or acad_conn.active_document_name()
        return _dump({"ok": True, "document": scope,
                      "entries": opening_library.entries(scope),
                      "state_file": opening_library._state_file()})
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(
    name="export_pdf",
    description=(
        "Plot the ACTIVE LAYOUT of the active drawing to a PDF file. Uses the "
        "installed 'DWG To PDF.pc3' plotter through Layout.ConfigName + "
        "Plot.PlotToFile -- no plotting UI is involved. MEASURED: PlotToFile returns "
        "BEFORE the file exists (asynchronous background plotting), so this tool "
        "polls until the PDF is really written and reports size + magic bytes; a "
        "0-byte or missing file is reported as an error, never as success. This "
        "changes the layout's plot settings (config/media), which is why it is "
        "safe to run on a scratch drawing and harmless on a real one."
    ),
)
def export_pdf(path: str, config: str = "", media: str = "",
               plot_type: str = "extents", overwrite: bool = True,
               timeout: float = 60.0, plot_style: str = "monochrome.ctb") -> str:
    try:
        return _dump(plot_export.export_pdf(
            path, config=config or None, media=media or None,
            plot_type=plot_type or "extents", overwrite=overwrite, timeout=timeout,
            plot_style=plot_style or None))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(
    name="list_plot_devices",
    description=(
        "READ-ONLY: list the plot devices (.pc3) this AutoCAD install can see, the "
        "current layout's config/media, and which of them can produce a PDF."
    ),
)
def list_plot_devices() -> str:
    try:
        return _dump(plot_export.list_plot_devices())
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(
    name="summarize_entity_classes",
    description=(
        "READ-ONLY: count model-space entities by native class name (TDbWall, "
        "TDbOpening, ...) and return sample handles per class."
    ),
)
def summarize_entity_classes() -> str:
    try:
        return _dump(scene_build.summarize_entity_classes(custom_only=True))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@mcp.tool(
    name="build_plan",
    description=(
        "Build a drawing in stages from a structured spec: walls (cloned from a "
        "template, batched), then openings, then dimensions. Each stage is "
        "independent; on failure the completed stages are kept and reported via "
        "completed_stages / failed_stage / partial_result. "
        "spec = {origin:[x,y], stage:'all|walls|openings|dimensions', "
        "walls:[{x1,y1,x2,y2}], wall_template?:<handle>, "
        "openings:[{type,x,y,width?,template?}], "
        "opening_templates?:{door:[<handle>|{handle,width}],window:[...]}, "
        "dimensions:[{kind,p1,p2,pos}]}. "
        "Openings honour an explicit width: Tianzheng exposes no opening width, so "
        "the call only clones a template whose width was recorded (within 50 mm) and "
        "otherwise returns stage='missing_opening_width' naming the width you must "
        "create first -- it never places a wrong-width opening. Without width it falls "
        "back to the first template and flags width_unverified. "
        "dimension kind='linear' is a point-to-point dimension; kind='axis' runs "
        "the Tianzheng axis-grid annotation and needs p1/p2 to sit exactly on real "
        "axis lines (LINE entities on layer DOTE) -- if the drawing has no axis "
        "grid yet, draw one first with 绘制轴网 HZZW; the call reports that instead "
        "of sending a doomed command. Axis dimensions are annotated inside this "
        "tool (via the bare in-thread implementation, never a nested COM hop)."
    ),
)
def build_plan(spec: str) -> str:
    try:
        return _dump(scene_build.build_plan(json.loads(spec)))
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
