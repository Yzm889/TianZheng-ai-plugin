"""handoff（交接清单）的纯逻辑测试：不连 CAD、不点鼠标。

守四件事：
  1. kind 的中文/英文写法都能解析到同一条天正命令；
  2. 目标句柄按类型自动挑（圆给半径、弧给直径、房间给面积统计…）；
  3. 建议点位必须"点得中"（圆取 45° 圆周、横墙基线点沿中心线）；
  4. 交接文案要带路径、句柄、点位和"别动其它实体"的要求。
"""
import unittest

from t20_arch_mcp import handoff

ENTITIES = [
    {"handle": "1A", "type": "TDbWall", "layer": "WALL", "bbox": [0, 0, 5800, 200]},
    {"handle": "1B", "type": "TDbWall", "layer": "WALL", "bbox": [5600, 0, 5800, 4200]},
    {"handle": "1C", "type": "TDbSpace", "layer": "SPACE", "bbox": [200, 200, 5600, 4000]},
    {"handle": "1D", "type": "AcDbCircle", "layer": "0", "bbox": [8600, 4700, 10400, 6500]},
    {"handle": "1E", "type": "AcDbArc", "layer": "0", "bbox": [11800, 5600, 13200, 6300]},
    {"handle": "1F", "type": "AcDbHatch", "layer": "0", "bbox": [7200, 1600, 10200, 3400]},
    {"handle": "20", "type": "AcDbText", "layer": "0", "bbox": [0, 7900, 4300, 8400]},
    {"handle": "21", "type": "AcDbLine", "layer": "DOTE", "bbox": [-1000, 0, 5800, 0]},
    {"handle": "22", "type": "AcDbLine", "layer": "0", "bbox": [0, -900, 400, -400]},
]

DOC = {"name": "样图.dwg", "path": r"C:\tmp\样图.dwg"}


class TestKindResolution(unittest.TestCase):
    def test_english_and_chinese(self):
        for raw, kind in (("radius_dim", "radius_dim"), ("半径标注", "radius_dim"),
                          ("面积统计", "area_stat"), ("填充面积", "hatch_area"),
                          ("填 充 面积", "area_stat"),   # 拆开写只能靠"面积"兜底
                          ("分段", "wall_split"), ("单轴标注", "axis_label"),
                          ("dia", "diameter_dim"), ("车位编号", "parking_number")):
            self.assertEqual(handoff._resolve_kind(raw), kind, raw)

    def test_unknown_kind_is_reported_not_guessed(self):
        out = handoff.build_handoff(DOC, ENTITIES, [{"kind": "帮我画个楼梯"}])
        self.assertFalse(out["pending"][0]["supported"])
        self.assertIn("不认识", out["pending"][0]["note"])


class TestTargetSelection(unittest.TestCase):
    def test_radius_takes_circle_and_arc(self):
        out = handoff.build_handoff(DOC, ENTITIES, [{"kind": "radius_dim"}])
        self.assertEqual(out["pending"][0]["handles"], ["1D", "1E"])

    def test_diameter_only_takes_arc(self):
        out = handoff.build_handoff(DOC, ENTITIES, [{"kind": "diameter_dim"}])
        self.assertEqual(out["pending"][0]["handles"], ["1E"])

    def test_area_takes_room_not_circle(self):
        out = handoff.build_handoff(DOC, ENTITIES, [{"kind": "area_stat"}])
        self.assertEqual(out["pending"][0]["handles"], ["1C"])

    def test_axis_label_filters_by_layer(self):
        out = handoff.build_handoff(DOC, ENTITIES, [{"kind": "axis_label"}])
        self.assertEqual(out["pending"][0]["handles"], ["21"])

    def test_explicit_handles_win(self):
        out = handoff.build_handoff(
            DOC, ENTITIES, [{"kind": "area_stat", "handles": ["1A"]}])
        self.assertEqual(out["pending"][0]["handles"], ["1A"])

    def test_missing_target_is_flagged(self):
        out = handoff.build_handoff(DOC, [], [{"kind": "area_stat"}])
        self.assertEqual(out["pending"][0]["handles"], [])
        self.assertIn("找不到", out["pending"][0]["note"])


class TestSuggestedPoints(unittest.TestCase):
    def test_circle_pick_point_sits_on_circumference(self):
        out = handoff.build_handoff(DOC, ENTITIES, [{"kind": "radius_dim"}])
        pick = out["pending"][0]["pick_points"][0]
        cx, cy, r = 9500.0, 5600.0, 900.0
        dist = ((pick[0] - cx) ** 2 + (pick[1] - cy) ** 2) ** 0.5
        self.assertAlmostEqual(dist, r, delta=0.05)   # 点位保留 3 位小数

    def test_radius_label_point_is_outside_circle(self):
        out = handoff.build_handoff(DOC, ENTITIES, [{"kind": "radius_dim"}])
        (x, y), = out["pending"][0]["points"]
        self.assertEqual((x, y), (9500.0 + 1800.0, 5600.0))

    def test_horizontal_wall_baseline_uses_centre_line(self):
        out = handoff.build_handoff(
            DOC, ENTITIES, [{"kind": "wall_split", "handles": ["1A"]}])
        p1, p2 = out["pending"][0]["points"]
        self.assertEqual(p1[1], 100.0)          # 200 高墙的中心线
        self.assertAlmostEqual(p1[0], 5800.0 / 3.0, places=3)
        self.assertAlmostEqual(p2[0], 5800.0 * 2.0 / 3.0, places=3)

    def test_two_tables_get_different_rows(self):
        out = handoff.build_handoff(
            DOC, ENTITIES, [{"kind": "area_stat"}, {"kind": "hatch_area"}])
        y1 = out["pending"][0]["points"][0][1]
        y2 = out["pending"][1]["points"][0][1]
        self.assertLess(y2, y1)                 # 第二张表往下排，别叠在第一张上


class TestFinishedDetection(unittest.TestCase):
    def test_sheet_marks_area_stats_done(self):
        ents = ENTITIES + [{"handle": "30", "type": "TDbSheet", "layer": "PUB_TAB",
                            "bbox": [6000, 0, 13000, 2500]}]
        out = handoff.build_handoff(DOC, ents, [{"kind": "area_stat"}])
        self.assertTrue(out["pending"][0]["already_looks_done"])

    def test_radius_dim_marks_radius_done(self):
        ents = ENTITIES + [{"handle": "31", "type": "TDbRadiusDim", "layer": "PUB_DIM",
                            "bbox": [9000, 5000, 12000, 7000]}]
        out = handoff.build_handoff(DOC, ents, [{"kind": "radius_dim"}])
        self.assertTrue(out["pending"][0]["already_looks_done"])


class TestUnsupported(unittest.TestCase):
    def test_parking_is_reported_without_targets(self):
        out = handoff.build_handoff(DOC, ENTITIES, [{"kind": "parking_number"}])
        entry = out["pending"][0]
        self.assertFalse(entry["supported"])
        self.assertIn("TParkingSpace", entry["note"])


class TestHandoffText(unittest.TestCase):
    def setUp(self):
        self.out = handoff.build_handoff(
            DOC, ENTITIES,
            [{"kind": "radius_dim"}, {"kind": "area_stat"},
             {"kind": "wall_split"}] + [{"kind": "parking_number"}])

    def test_text_carries_path_handles_and_guardrail(self):
        text = self.out["handoff_text"]
        self.assertIn(r"C:\tmp\样图.dwg", text)
        self.assertIn("TDimRad", text)
        self.assertIn("1D", text)
        self.assertIn("别动其他实体", text)
        self.assertIn("save_drawing", text)
        self.assertIn("不要动其它实体", text)

    def test_text_numbers_the_todo_list(self):
        text = self.out["handoff_text"]
        self.assertIn("1. 半径标注", text)
        self.assertIn("2. 面积统计", text)

    def test_parking_appears_as_unsupported_line(self):
        self.assertIn("暂不支持", self.out["handoff_text"])


if __name__ == "__main__":
    unittest.main()
