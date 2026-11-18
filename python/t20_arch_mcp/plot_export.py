"""plot_export.py - 用 COM 把当前布局打印成 PDF。

为什么单独一个模块：项目早期结论是「机器上没有 .pc3，所以 PDF 不可能」。
实测 2026-11-18 推翻了它 —— `DWG To PDF.pc3` 就在
`%APPDATA%\\Autodesk\\AutoCAD 2022\\R24.1\\chs\\Plotters\\`，而且
`Layout.ConfigName` 可以直接设置、`Plot.PlotToFile(path, config)` 能出图。

两个必须知道的实测细节：
  1. `IAcadPlot` 上**没有** `PlotConfigurations` 集合（这是老的错误写法），
     绘图仪只能通过 `ActiveLayout.ConfigName` 设置；
  2. `PlotToFile` 是**异步**的 —— 调用返回时文件还不存在，要轮询等它落盘。
     早先就是因为查得太早，误判成「写不出文件」。
"""
from __future__ import annotations

import os
import time
from typing import Any

from .acad_conn import AcadError, _active_document, _com, call_with_retry, doc_name

DEFAULT_CONFIG = "DWG To PDF.pc3"

# AutoCAD 的 PlotType 枚举
# 0 Display · 1 Extents · 2 Limits · 3 View · 4 Window
PLOT_TYPES = {"display": 0, "extents": 1, "limits": 2, "view": 3, "window": 4}


def choose_config(names: list[str], preferred: str | None = None) -> str | None:
    """从可用绘图仪里挑一个能出 PDF 的配置。

    优先级：调用方指定（精确 → 包含）> DWG To PDF.pc3 > 含 PDF 且含 DWG > 任意含 PDF。
    """
    if not names:
        return None
    if preferred:
        for name in names:
            if name == preferred:
                return name
        for name in names:
            if preferred.lower() in name.lower():
                return name
    for name in names:
        if name == DEFAULT_CONFIG:
            return name
    for name in names:
        if "PDF" in name.upper() and "DWG" in name.upper():
            return name
    for name in names:
        if "PDF" in name.upper():
            return name
    return None


def wait_for_file(path: str, timeout: float = 60.0, poll: float = 0.5,
                  settle: int = 2) -> dict[str, Any]:
    """等异步打印真的把文件写完（连续 settle 次大小不变且 > 0）。"""
    started = time.time()
    last_size = -1
    stable = 0
    while time.time() - started < timeout:
        try:
            size = os.path.getsize(path) if os.path.exists(path) else 0
        except OSError:
            size = 0
        if size > 0 and size == last_size:
            stable += 1
            if stable >= settle:
                break
        else:
            stable = 0
        last_size = size
        time.sleep(poll)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return {
        "exists": bool(size),
        "size": size,
        "waited_ms": int((time.time() - started) * 1000),
    }


def _devices_impl(spec: dict[str, Any]) -> dict[str, Any]:
    doc = _active_document()
    lay = doc.ActiveLayout
    names: list[str] = []
    try:
        names = [str(n) for n in call_with_retry(lay.GetPlotDeviceNames,
                                                 label="GetPlotDeviceNames")]
    except Exception:  # noqa: BLE001
        names = []
    if not names:
        current = str(getattr(lay, "ConfigName", "") or "")
        names = [current] if current else []
    return {
        "ok": True,
        "document": doc_name(doc, None),
        "layout": str(getattr(lay, "Name", "")),
        "configs": names,
        "current": str(getattr(lay, "ConfigName", "") or ""),
        "pdf_capable": choose_config(names),
        "media": str(getattr(lay, "CanonicalMediaName", "") or ""),
    }


def list_plot_devices() -> dict[str, Any]:
    return _com(_devices_impl, {}, timeout=90)


def _set_layout_for_plot(lay: Any, config: str, media: str | None,
                         plot_type: str, plot_style: str | None = None) -> None:
    lay.ConfigName = config
    try:
        lay.RefreshPlotDeviceInfo()
    except Exception:  # noqa: BLE001
        pass
    if media:
        lay.CanonicalMediaName = media
    if plot_style:
        # 实测：图里墙/线是白色，直接打成 PDF 会白底白线，看不出内容。
        # 用 monochrome.ctb 强制黑色，是出图可读的最低要求。
        lay.StyleSheet = plot_style
        try:
            lay.PlotWithPlotStyles = True
        except Exception:  # noqa: BLE001
            pass
    if plot_type:
        key = plot_type.lower()
        if key not in PLOT_TYPES:
            raise AcadError(
                f"plot_type 只支持 {sorted(PLOT_TYPES)}，收到 {plot_type!r}")
        lay.PlotType = PLOT_TYPES[key]
    try:
        lay.CenterPlot = True
        lay.PlotWithLineweights = True
    except Exception:  # noqa: BLE001
        pass


def _export_impl(spec: dict[str, Any]) -> dict[str, Any]:
    path = os.path.abspath(os.path.expanduser(spec["path"]))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path) and not spec.get("overwrite", True):
        raise AcadError(f"目标文件已存在：{path}（传 overwrite=true 覆盖）")

    doc = _active_document()
    lay = doc.ActiveLayout

    names: list[str] = []
    try:
        names = [str(n) for n in call_with_retry(lay.GetPlotDeviceNames,
                                                 label="GetPlotDeviceNames")]
    except Exception:  # noqa: BLE001
        current = str(getattr(lay, "ConfigName", "") or "")
        names = [current] if current else []

    config = choose_config(names, spec.get("config"))
    if config is None:
        raise AcadError(
            "这台机器没有可用的 PDF 绘图仪（GetPlotDeviceNames 里找不到含 PDF 的配置）。"
            f"可用配置：{names[:6]}")

    try:
        layout_name = str(call_with_retry(lambda: lay.Name, label="layout Name"))
    except Exception:  # noqa: BLE001
        layout_name = ""
    call_with_retry(
        lambda: _set_layout_for_plot(lay, config, spec.get("media"),
                                     spec.get("plot_type", "extents"),
                                     spec.get("plot_style")),
        attempts=8, delay=1.0, label="set layout for plot")
    try:
        doc.Plot.QuietErrorMode = True
    except Exception:  # noqa: BLE001
        pass

    call_with_retry(lambda: doc.Plot.PlotToFile(path, config), attempts=8, delay=1.0,
                    label="PlotToFile")
    waited = wait_for_file(path, timeout=float(spec.get("timeout", 60.0)))
    if not waited["exists"]:
        raise AcadError(
            f"PlotToFile 已返回，但 {int(waited['waited_ms'])} ms 内没有生成文件：{path}"
            "（检查绘图仪配置、权限，或该布局是否为空）")

    result: dict[str, Any] = {
        "ok": True,
        "path": path,
        "size": waited["size"],
        "waited_ms": waited["waited_ms"],
        "document": doc_name(doc, None),
        "layout": layout_name,
        "config": config,
        "plot_type": str(spec.get("plot_type", "extents")),
        "media": "",
        "plot_style": str(spec.get("plot_style") or ""),
    }
    try:
        result["media"] = str(call_with_retry(lambda: lay.CanonicalMediaName,
                                              label="media"))
    except Exception:  # noqa: BLE001
        pass
    with open(path, "rb") as fh:
        result["magic"] = fh.read(5).decode("latin-1")
    return result


def export_pdf(path: str, config: str | None = None, media: str | None = None,
               plot_type: str = "extents", overwrite: bool = True,
               timeout: float = 60.0,
               plot_style: str = "monochrome.ctb") -> dict[str, Any]:
    """把**当前布局**输出成 PDF。PlotToFile 是异步的，这里会等到文件真的写完。

    plot_style 默认 monochrome.ctb：白线在白底 PDF 上是隐形的，必须换成单色样式表。
    """
    if not path:
        raise AcadError("path 不能为空")
    spec = {"path": path, "config": config, "media": media, "plot_type": plot_type,
            "overwrite": overwrite, "timeout": timeout,
            "plot_style": plot_style or None}
    return _com(_export_impl, spec, timeout=max(120.0, timeout + 60.0))
