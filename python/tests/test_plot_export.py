"""PDF 导出的纯逻辑测试（不需要 CAD）。

重点守两件事：
  1. 选绘图仪的顺序（指定 > DWG To PDF.pc3 > 任意 PDF）；
  2. PlotToFile 是异步的 —— 必须等到文件真的写完，不能"调用返回就报成功"。
"""
import os
import tempfile
import threading
import time
import unittest

from t20_arch_mcp import plot_export


class TestChooseConfig(unittest.TestCase):
    def test_prefers_exact_then_substring(self):
        names = ["Default Windows System Printer.pc3", "DWG To PDF.pc3"]
        self.assertEqual(plot_export.choose_config(names, "DWG To PDF.pc3"),
                         "DWG To PDF.pc3")
        self.assertEqual(plot_export.choose_config(names, "dwg to pdf"),
                         "DWG To PDF.pc3")

    def test_falls_back_to_default_pdf_plotter(self):
        names = ["DWF6 ePlot.pc3", "DWG To PDF.pc3", "PublishToWeb JPG.pc3"]
        self.assertEqual(plot_export.choose_config(names), "DWG To PDF.pc3")

    def test_falls_back_to_any_pdf(self):
        names = ["DWF6 ePlot.pc3", "AutoCAD PDF (High Quality Print).pc3"]
        self.assertEqual(plot_export.choose_config(names),
                         "AutoCAD PDF (High Quality Print).pc3")

    def test_returns_none_when_no_pdf_plotter(self):
        self.assertIsNone(plot_export.choose_config(["Default Windows System Printer.pc3"]))
        self.assertIsNone(plot_export.choose_config([]))


class TestWaitForFile(unittest.TestCase):
    def _delayed_writer(self, path: str, delay: float, size: int = 120) -> None:
        def run():
            time.sleep(delay)
            with open(path, "wb") as fh:
                fh.write(b"%PDF-" + b"x" * size)

        threading.Thread(target=run, daemon=True).start()

    def test_waits_for_async_write(self):
        path = os.path.join(tempfile.mkdtemp(), "async.pdf")
        self._delayed_writer(path, 0.6)
        result = plot_export.wait_for_file(path, timeout=8.0, poll=0.2)
        self.assertTrue(result["exists"])
        self.assertGreater(result["size"], 0)
        self.assertGreater(result["waited_ms"], 300)

    def test_reports_missing_file(self):
        path = os.path.join(tempfile.mkdtemp(), "never.pdf")
        result = plot_export.wait_for_file(path, timeout=1.0, poll=0.2)
        self.assertFalse(result["exists"])
        self.assertEqual(result["size"], 0)

    def test_zero_byte_file_is_not_success(self):
        path = os.path.join(tempfile.mkdtemp(), "empty.pdf")
        open(path, "wb").close()
        result = plot_export.wait_for_file(path, timeout=1.0, poll=0.2)
        self.assertFalse(result["exists"])


class TestPlotTypeValidation(unittest.TestCase):
    def test_known_plot_types(self):
        self.assertEqual(plot_export.PLOT_TYPES["extents"], 1)
        self.assertEqual(plot_export.PLOT_TYPES["window"], 4)


if __name__ == "__main__":
    unittest.main()
