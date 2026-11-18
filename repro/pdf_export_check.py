"""PDF 导出实测：在临时副本上跑一遍，产出真 PDF 并核对文件头。

用法：python pdf_export_check.py <dwg副本> <输出pdf路径>
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "python"))
os.environ.setdefault("T20_ALLOW_FOREIGN_DOC", "true")

import pythoncom  # noqa: E402
import win32com.client as win32com  # noqa: E402

from t20_arch_mcp import acad_conn, plot_export  # noqa: E402

PROGIDS = ("AutoCAD.Application", "AutoCAD.Application.25.1",
           "AutoCAD.Application.25.0", "AutoCAD.Application.24.1",
           "AutoCAD.Application.24.0")


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("用法：python pdf_export_check.py <dwg> <out.pdf>")
    src, out = sys.argv[1], os.path.abspath(sys.argv[2])
    if os.path.exists(out):
        os.remove(out)

    app = None
    for progid in PROGIDS:
        try:
            app = acad_conn._typed(win32com.GetActiveObject(progid))
            break
        except Exception:  # noqa: BLE001
            continue
    if app is None:
        raise SystemExit("连不上 AutoCAD")

    acad_conn.wait_until_quiescent(app, timeout=45.0)
    doc = acad_conn.call_with_retry(lambda: app.Documents.Open(src), attempts=30,
                                    delay=1.0, label="open")
    acad_conn.wait_until_quiescent(app, timeout=30.0)
    try:
        print("doc:", acad_conn.doc_name(doc, "?"))
        try:
            print("devices:", json.dumps(plot_export.list_plot_devices(),
                                         ensure_ascii=False, indent=2, default=str))
        except Exception as exc:  # noqa: BLE001
            print("list_plot_devices failed:", exc)

        result = plot_export.export_pdf(out, plot_type="extents", timeout=90.0)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

        ok = result.get("ok") and result.get("size", 0) > 0 \
            and result.get("magic", "").startswith("%PDF")
        print("\n结论：", "通过" if ok else "未通过")
        if not ok:
            sys.exit(1)
    finally:
        try:
            acad_conn.call_with_retry(lambda: doc.Close(False), attempts=30,
                                      delay=1.0, label="close")
            print("closed without saving")
        except Exception as exc:  # noqa: BLE001
            print("close failed:", exc)


if __name__ == "__main__":
    pythoncom.CoInitialize()
    main()
