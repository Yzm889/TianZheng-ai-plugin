"""插入门窗助手的单元测试（不需要 CAD）。

锁定三条原则：
  1. 点击清单的序号、类型、坐标必须完整且可执行
  2. 操作步骤必须包含"开捕捉"和"点中点"这两个关键点
  3. 必须明确告知这一��为什么无法自动化（MC 只收鼠标）
"""
import unittest

from t20_arch_mcp import opening_assist


ITEMS = [
    {"kind": "door", "x": 16200, "y": 7800, "width": 1500},
    {"kind": "door", "x": 6000, "y": 0, "width": 2400, "label": "M0主入口"},
    {"kind": "window", "x": 13800, "y": 0, "width": 1800},
    {"kind": "window", "x": 16200, "y": 0, "width": 1800},
]


class TestMakePlan(unittest.TestCase):
    def test_counts_and_numbering(self):
        p = opening_assist.make_plan(ITEMS)
        self.assertEqual(p["count"], 4)
        self.assertEqual(p["doors"], 2)
        self.assertEqual(p["windows"], 2)
        self.assertEqual([s["step"] for s in p["steps"]], [1, 2, 3, 4])

    def test_labels_default_per_type(self):
        p = opening_assist.make_plan(ITEMS)
        labels = [s["label"] for s in p["steps"]]
        self.assertEqual(labels[0], "M1")          # first door
        self.assertEqual(labels[1], "M0主入口")     # explicit label wins
        self.assertEqual(labels[2], "C1")          # first window
        self.assertEqual(labels[3], "C2")

    def test_coordinates_preserved(self):
        p = opening_assist.make_plan(ITEMS)
        self.assertEqual((p["steps"][0]["x"], p["steps"][0]["y"]), (16200.0, 7800.0))

    def test_how_to_is_actionable(self):
        p = opening_assist.make_plan(ITEMS)
        steps = p["how_to"]
        self.assertIsInstance(steps, list)
        joined = " ".join(steps)
        self.assertIn("OSNAP", joined)      # must tell the user to enable snaps
        self.assertIn("中点", joined)        # midpoint snap is how you hit the axis

    def test_explains_why_it_is_manual(self):
        p = opening_assist.make_plan(ITEMS)
        self.assertIn("鼠标", p["why_manual"])
        self.assertIn("轴心线", p["note"])


class TestModuleSurface(unittest.TestCase):
    def test_expected_functions_exist(self):
        for fn in ("make_plan", "mark_on_drawing", "clear_marks", "verify"):
            self.assertTrue(callable(getattr(opening_assist, fn, None)), fn)

    def test_layer_name_is_dedicated(self):
        # markers must live on their own layer so clear_marks can remove exactly them
        self.assertEqual(opening_assist.LAYER, "AUX-OPENING")


if __name__ == "__main__":
    unittest.main()
