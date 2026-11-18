"""F5：build_plan 内部的轴网标注（不需要 CAD）。

守护三条原则：
  1. 内部只调裸实现 `_annotate_axis_impl`，绝不嵌套 `_com`
     （嵌套会永久死锁 —— 内层在等外层占用的那个 COM 线程）；
  2. 图中没有 DOTE 图层轴线时先提示「先画轴网」，不盲发注定失败的 ZWBZ；
  3. origin 偏移要算进每个点，线性标注仍走原来的 TDimMP 路径。
"""
import unittest
from unittest import mock

from t20_arch_mcp import scene_build, tianzheng_runner


class _FakeDoc:
    Name = "fake.dwg"


def _no_nested_com(*_args, **_kwargs):
    raise AssertionError("build_plan 内部不允许调用 _com（会永久死锁）")


OK_AXIS = {"success": True, "counts": {"before": 0, "after": 1, "delta": 1}}


class TestBuildPlanAxis(unittest.TestCase):
    def _build(self, spec, axis_result=OK_AXIS, axis_lines=4):
        """跑 _build_impl，把 COM 与两个天正命令实现都换成假的。"""
        doc = _FakeDoc()
        calls = {"axis": [], "linear": []}

        def fake_axis(start, end, timeout):
            calls["axis"].append((start, end, timeout))
            return axis_result

        def fake_linear(command, inputs, *_a, **_k):
            calls["linear"].append((command, inputs))
            return {"success": True}

        with mock.patch.object(scene_build, "_active_document", return_value=doc), \
                mock.patch.object(scene_build, "_ensure_writable", return_value=None), \
                mock.patch.object(scene_build, "_com", side_effect=_no_nested_com), \
                mock.patch.object(tianzheng_runner, "_count_type", return_value=axis_lines), \
                mock.patch.object(tianzheng_runner, "_annotate_axis_impl",
                                  side_effect=fake_axis), \
                mock.patch.object(tianzheng_runner, "_run_with_dialog_impl",
                                  side_effect=fake_linear):
            result = scene_build._build_impl(spec, 600.0)
        return result, calls

    def test_axis_dimension_runs_in_thread_with_origin_offset(self):
        spec = {"stage": "dimensions", "origin": [100, 200],
                "dimensions": [{"kind": "axis", "p1": [0, 0], "p2": [6000, 0]}]}
        result, calls = self._build(spec)

        self.assertEqual(calls["axis"], [("100,200", "6100,200", 30.0)])
        self.assertEqual(result["dimension_count"], 1)
        self.assertIn("dimensions", result["completed_stages"])
        self.assertEqual(result["axis_dote_lines"], 4)
        self.assertEqual(result["dimension_errors"], [])
        self.assertTrue(result["ok"])

    def test_axis_needs_real_axis_grid_before_acting(self):
        spec = {"stage": "dimensions",
                "dimensions": [{"kind": "axis", "p1": [0, 0], "p2": [6000, 0]}]}
        result, calls = self._build(spec, axis_lines=0)

        self.assertEqual(calls["axis"], [])              # 没有盲发命令
        self.assertEqual(result["axis_dote_lines"], 0)   # 留下实测证据
        self.assertEqual(result["dimension_count"], 0)
        joined = " ".join(result["dimension_errors"])
        self.assertIn("绘制轴网 HZZW", joined)
        self.assertIn("DOTE", joined)

    def test_axis_failure_surfaces_tianzheng_diagnosis(self):
        spec = {"stage": "dimensions",
                "dimensions": [{"kind": "axis", "p1": [0, 0], "p2": [6000, 0]}]}
        result, _ = self._build(
            spec,
            axis_result={"success": False,
                         "diagnosis": "天正判定点位无效：两个点必须落在真实轴线上"})

        self.assertEqual(result["dimension_count"], 0)
        self.assertIn("天正判定点位无效", " ".join(result["dimension_errors"]))

    def test_axis_without_points_is_rejected_early(self):
        spec = {"stage": "dimensions", "dimensions": [{"kind": "axis"}]}
        result, calls = self._build(spec)

        self.assertEqual(calls["axis"], [])
        self.assertIn("需要 p1/p2", " ".join(result["dimension_errors"]))

    def test_linear_dimension_still_uses_tdimp(self):
        spec = {"stage": "dimensions", "origin": [1, 2],
                "dimensions": [{"kind": "linear", "p1": [0, 0], "p2": [6000, 0],
                                "pos": [3000, -500]}]}
        result, calls = self._build(spec)

        self.assertEqual(len(calls["linear"]), 1)
        command, inputs = calls["linear"][0]
        self.assertEqual(command, "TDimMP")
        self.assertEqual(inputs[0], "1,2")
        self.assertEqual(inputs[1], "6001,2")
        self.assertEqual(inputs[2], "3001,-498")
        self.assertEqual(result["dimension_count"], 1)

    def test_axis_and_linear_in_one_plan(self):
        spec = {"stage": "dimensions",
                "dimensions": [
                    {"kind": "axis", "p1": [0, 0], "p2": [0, 6000]},
                    {"kind": "linear", "p1": [0, 0], "p2": [6000, 0],
                     "pos": [3000, -500]},
                ]}
        result, calls = self._build(spec)

        self.assertEqual(len(calls["axis"]), 1)
        self.assertEqual(len(calls["linear"]), 1)
        self.assertEqual(result["dimension_count"], 2)
        self.assertEqual(result["dimension_errors"], [])


if __name__ == "__main__":
    unittest.main()
