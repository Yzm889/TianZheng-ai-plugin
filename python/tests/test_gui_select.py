"""gui_select 的纯逻辑测试（不点鼠标、不需要 CAD）。

守三件事：
  1. 能力门控：没开 T20_HOST_GUI 就不许往下走；
  2. 点选点必须落在对象上（线段取中点、圆/弧取圆周，别用包围盒中心）；
  3. 视图判定：目标不在视图内要提前拒绝，而不是乱点。
"""
import os
import unittest
from unittest import mock

from t20_arch_mcp import gui_select


class TestGating(unittest.TestCase):
    def test_default_is_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("T20_HOST_GUI", None)
            self.assertFalse(gui_select.gui_enabled())

    def test_truthy_values(self):
        for value in ("1", "true", "YES", " on "):
            with mock.patch.dict(os.environ, {"T20_HOST_GUI": value}):
                self.assertTrue(gui_select.gui_enabled(), value)

    def test_falsy_values(self):
        for value in ("0", "false", "", "off"):
            with mock.patch.dict(os.environ, {"T20_HOST_GUI": value}):
                self.assertFalse(gui_select.gui_enabled(), value)

    def test_gui_paths_refuse_without_capability(self):
        with mock.patch.dict(os.environ, {"T20_HOST_GUI": "0"}):
            with self.assertRaises(Exception) as ctx:
                gui_select.click_targets([])
            self.assertIn("桌面控制", str(ctx.exception))
            with self.assertRaises(Exception) as ctx:
                gui_select.run_select_command("X", [])
            self.assertIn("桌面控制", str(ctx.exception))


class TestPickPoint(unittest.TestCase):
    def test_line_uses_midpoint(self):
        point = gui_select.pick_point_from_geometry(
            "AcDbLine", {"start": [0, 0], "end": [100, 50],
                         "bbox": [[0, 0], [100, 50]]})
        self.assertEqual(point, (50.0, 25.0))

    def test_circle_uses_circumference_not_centre(self):
        point = gui_select.pick_point_from_geometry(
            "AcDbCircle", {"centre": [10, 20], "radius": 5.0,
                           "bbox": [[5, 15], [15, 25]]})
        self.assertNotEqual(point, (10.0, 20.0))
        self.assertAlmostEqual(point[0], 10.0 + 5.0 * 0.7071, places=3)

    def test_wall_like_object_falls_back_to_bbox_centre(self):
        point = gui_select.pick_point_from_geometry(
            "TDbWall", {"bbox": [[0, 0], [10, 4]]})
        self.assertEqual(point, (5.0, 2.0))

    def test_no_geometry_returns_none(self):
        self.assertIsNone(gui_select.pick_point_from_geometry("TDbWall", {"bbox": None}))


class TestInsideAll(unittest.TestCase):
    def test_all_inside_and_one_outside(self):
        state = {"left": 0, "top": 0, "width": 800, "height": 600,
                 "centre": (0.0, 0.0), "viewsize": 600.0, "scale": 1.0}
        self.assertTrue(gui_select.inside_all([(400, 300), (100, 100)], state))
        self.assertFalse(gui_select.inside_all([(400, 300), (801, 100)], state))


if __name__ == "__main__":
    unittest.main()
