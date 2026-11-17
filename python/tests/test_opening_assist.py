"""插入门窗助手的单元测试（不需要 CAD）。

锁定本次会话得出的三条关键结论：
  1. 标记只是位置参考，不参与捕捉（用户明确要求）
  2. 窗画在墙里的方框，门画在墙外的箭头（两者包围盒差异极大）
  3. "插好了没有"要看门窗对象高度是否 > 0，不能看墙有没有被切分
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
    def test_counts_and_labels(self):
        p = opening_assist.make_plan(ITEMS)
        self.assertEqual((p["count"], p["doors"], p["windows"]), (4, 2, 2))
        labels = [s["label"] for s in p["steps"]]
        self.assertEqual(labels[0], "M1")
        self.assertEqual(labels[1], "M0主入口")
        self.assertEqual(labels[2], "C1")
        self.assertEqual(labels[3], "C2")

    def test_coordinates_preserved(self):
        p = opening_assist.make_plan(ITEMS)
        self.assertEqual((p["steps"][0]["x"], p["steps"][0]["y"]), (16200.0, 7800.0))

    def test_tips_differ_by_kind(self):
        p = opening_assist.make_plan(ITEMS)
        door_tip = p["steps"][0]["tip"]
        win_tip = p["steps"][2]["tip"]
        self.assertNotEqual(door_tip, win_tip)
        self.assertIn("框", win_tip)      # windows are shown as a box in the wall
        self.assertIn("箭头", door_tip)   # doors use an outside arrow

    def test_markers_are_not_promised_as_snappable(self):
        p = opening_assist.make_plan(ITEMS)
        joined = " ".join(p["how_to"]) + " " + p["note"]
        # must NOT tell users to snap to the marker
        self.assertNotIn("捕捉中点", joined)
        self.assertIn("你自己判断", joined)


class TestVerifyCriteria(unittest.TestCase):
    def test_degenerate_threshold_exported(self):
        # a healthy TDbOpening has a real bbox height; degenerate ones are ~0
        self.assertEqual(opening_assist.DEGENERATE_HEIGHT, 100.0)

    def test_verify_signature(self):
        self.assertTrue(callable(opening_assist.verify))
        self.assertTrue(callable(opening_assist.clear_marks))
        self.assertTrue(callable(opening_assist.mark_on_drawing))


class TestLayer(unittest.TestCase):
    def test_markers_live_on_dedicated_layer(self):
        self.assertEqual(opening_assist.LAYER, "AUX-OPENING")


if __name__ == "__main__":
    unittest.main()
