"""build_plan 开门窗时的宽度路由（不需要 CAD）。

守护三条原则：
  1. 给了宽度就必须命中同宽度模板，命不中要**先说清楚要建什么**，
     不能默默克隆一个宽度不对的门窗；
  2. 没给宽度时可以退回第一个模板，但必须在结果里标 width_unverified；
  3. 登记表（register_opening_template）是这个能力的记忆。
"""
import os
import tempfile
import unittest
from unittest import mock

from t20_arch_mcp import opening_library, scene_build, template_clone


class _FakeDoc:
    Name = "fake.dwg"


def _no_nested_com(*_args, **_kwargs):
    raise AssertionError("build_plan 内部不允许调用 _com（会永久死锁）")


class TestBuildPlanOpeningWidths(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="t20open_")
        self.state = os.path.join(tmp, ".t20_opening_library.json")
        patcher = mock.patch.dict(os.environ, {"T20_OPENING_LIBRARY": self.state})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _build(self, spec):
        doc = _FakeDoc()
        calls = []

        def fake_clone(handle, targets, rotate_deg=0.0):
            calls.append({"handle": handle, "targets": list(targets),
                          "rotate": rotate_deg})
            return {
                "ok": True,
                "created_count": len(targets),
                "created": [{"handle": "NEW%d" % len(calls),
                             "at": [targets[0]["x"], targets[0]["y"]],
                             "size": [1000.0, 975.0]}],
                "errors": [],
            }

        with mock.patch.object(scene_build, "_active_document", return_value=doc), \
                mock.patch.object(scene_build, "_ensure_writable", return_value=None), \
                mock.patch.object(scene_build, "_com", side_effect=_no_nested_com), \
                mock.patch.object(template_clone, "_scan_impl",
                                  return_value={"templates": {}}), \
                mock.patch.object(template_clone, "_clone_impl", side_effect=fake_clone):
            result = scene_build._build_impl(spec, 600.0)
        return result, calls

    def test_width_picks_matching_template(self):
        spec = {
            "stage": "openings",
            "opening_templates": {
                "door": [{"handle": "D1000", "width": 1000},
                         {"handle": "D1500", "width": 1500}],
            },
            "openings": [{"type": "door", "x": 0, "y": 0, "width": 1500}],
        }
        result, calls = self._build(spec)

        self.assertEqual([c["handle"] for c in calls], ["D1500"])
        self.assertEqual(result["opening_count"], 1)
        self.assertEqual(result["opening_width_plan"][0]["status"], "matched")
        self.assertEqual(result["opening_width_plan"][0]["matched_width"], 1500.0)
        self.assertNotIn("width_warning", result)

    def test_no_matching_width_asks_before_firing(self):
        spec = {
            "stage": "openings",
            "opening_templates": {"door": [{"handle": "D1000", "width": 1000}]},
            "openings": [{"type": "door", "x": 0, "y": 0, "width": 1500}],
        }
        result, calls = self._build(spec)

        self.assertEqual(calls, [])                       # 一个都没克隆
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "missing_opening_width")
        self.assertEqual(result["missing_widths"],
                         [{"index": 0, "kind": "door", "width": 1500.0}])
        self.assertIn("1500", result["message"])
        self.assertIn("MC", result["message"])
        for bad in ("安装目录", "图库", "搜索", "自己找"):
            self.assertNotIn(bad, result["message"])

    def test_without_width_marks_unverified(self):
        spec = {
            "stage": "openings",
            "opening_templates": {"door": ["D1000"]},
            "openings": [{"type": "door", "x": 10, "y": 20}],
        }
        result, calls = self._build(spec)

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["opening_width_plan"][0]["status"], "width_unverified")
        self.assertIn("width_warning", result)

    def test_registry_is_used_when_no_templates_given(self):
        opening_library.register("4C5", 1200, "window", document="fake.dwg")
        spec = {
            "stage": "openings",
            "openings": [{"type": "window", "x": 0, "y": 0, "width": 1200}],
        }
        result, calls = self._build(spec)

        self.assertEqual([c["handle"] for c in calls], ["4C5"])
        self.assertEqual(result["opening_width_plan"][0]["status"], "matched")

    def test_window_does_not_borrow_a_door_template(self):
        opening_library.register("D1", 1000, "door", document="fake.dwg")
        spec = {
            "stage": "openings",
            "openings": [{"type": "window", "x": 0, "y": 0, "width": 1000}],
        }
        result, calls = self._build(spec)

        self.assertEqual(calls, [])
        self.assertEqual(result["stage"], "missing_opening_width")
        self.assertIn("窗", result["message"])

    def test_no_opening_template_at_all_asks_user(self):
        spec = {"stage": "openings", "openings": [{"type": "door", "x": 0, "y": 0}]}
        result, calls = self._build(spec)

        self.assertEqual(calls, [])
        self.assertFalse(result["ok"])
        self.assertEqual(result["template_required"], "TDBOPENING")
        self.assertIn("请在天正里插入", result["message"])


if __name__ == "__main__":
    unittest.main()
