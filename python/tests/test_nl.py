"""Regression tests for the natural-language -> Tianzheng command pipeline.

Run with:  python -m unittest discover -s python/tests -v
No AutoCAD, pywin32 or mcp installation is required: the executor is injected.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from t20_arch_mcp import nl_catalog, nl_dispatch, nl_parser  # noqa: E402


class RecordingExecutor:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {
            "ok": True,
            "success": True,
            "counts": {"before": 0, "after": 1, "delta": 1},
            "handle": "A1",
        }

    def __call__(self, plan):
        self.calls.append(plan)
        return self.result


class TestNaturalLanguageParsing(unittest.TestCase):
    """Representative instructions must map to one unambiguous, verified plan."""

    CASES = [
        # (instruction, intent, command, verify_type, expected inputs)
        ("从 0,0 到 7000,0 画一堵墙", "draw_wall", "HZQT", "TCH_WALL", ["0,0", "7000,0", ""]),
        ("画墙 从（0，0）到（7000，0）", "draw_wall", "HZQT", "TCH_WALL", ["0,0", "7000,0", ""]),
        ("HZQT 0,0 7000,0 0,5000", "draw_wall", "HZQT", "TCH_WALL", ["0,0", "7000,0", "0,5000", ""]),
        ("在 0,0 画轴网，横向间距 6 米，纵向间距 5 米，3 跨 2 跨",
         "draw_axis_grid", "HZZW", None, ["0,0", "6000", "5000", "3", "2", ""]),
        ("在 100,100 画半径 500 的圆", "draw_circle", "CIRCLE", "CIRCLE", []),
        ("从 0,0 到 1000,1000 画一条线，图层 AXIS", "draw_line", "LINE", "LINE", []),
        ("在 500,500 写文字 \"轴线 A\"，字高 300", "add_text", "TEXT", "TEXT", []),
        ("另存为 C:\\temp\\t20.dwg", "save_drawing", None, None, []),
        ("新建测试图纸", "new_test_drawing", None, None, []),
        ("缩放到全图", "zoom_extents", None, None, []),
        ("检查天正是否加载", "check_tianzheng_loaded", None, None, []),
    ]

    def test_typical_instructions(self):
        for text, intent, command, verify, inputs in self.CASES:
            with self.subTest(text=text):
                plan = nl_parser.parse(text)
                self.assertTrue(plan.ok, [i.to_dict() for i in plan.issues])
                self.assertEqual(plan.intent, intent)
                self.assertEqual(plan.command, command)
                self.assertEqual(plan.verify_type, verify)
                self.assertEqual(plan.inputs, inputs)

    def test_query_intent_does_not_collide_with_draw_wall(self):
        plan = nl_parser.parse("统计墙的数量")
        self.assertTrue(plan.ok)
        self.assertEqual(plan.intent, "count_entities")
        self.assertEqual(plan.params["entity_type"], "TCH_WALL")

        plan = nl_parser.parse("有多少扇门？")
        self.assertTrue(plan.ok, [i.to_dict() for i in plan.issues])
        self.assertEqual(plan.params["entity_type"], "TCH_OPENING")

    def test_units_are_normalised_to_millimetres(self):
        plan = nl_parser.parse("在 0,0 画圆，半径 1.5 米")
        self.assertTrue(plan.ok, [i.to_dict() for i in plan.issues])
        self.assertEqual(plan.params["radius"], 1500.0)

    def test_optional_parameters_are_not_reported_as_missing(self):
        plan = nl_parser.parse("在 100,100 画半径 500 的圆")
        self.assertNotIn("layer", plan.params)
        self.assertTrue(plan.ok)

    def test_unstable_command_is_flagged(self):
        plan = nl_parser.parse("在 0,0 画轴网，间距 6000，3 跨 2 跨")
        self.assertTrue(plan.unstable)
        self.assertTrue(plan.warnings)


class TestErrorFeedback(unittest.TestCase):
    def assert_code(self, text, code):
        plan = nl_parser.parse(text)
        self.assertFalse(plan.ok, text)
        codes = [i.code for i in plan.issues]
        self.assertIn(code, codes, (text, codes))
        return plan

    def test_missing_points(self):
        plan = self.assert_code("画一堵墙", "MISSING_PARAMETER")
        self.assertEqual(plan.issues[0].field, "points")

    def test_ambiguous_intent(self):
        plan = self.assert_code("画一个门和窗", "AMBIGUOUS_INTENT")
        self.assertIn("一次只下达一条指令", plan.issues[0].hint)

    def test_unknown_intent(self):
        plan = self.assert_code("随便做点什么", "UNKNOWN_INTENT")
        self.assertTrue(plan.issues[0].hint)

    def test_bad_number(self):
        plan = self.assert_code("在 0,0 画半径 -5 的圆", "INVALID_NUMBER")
        self.assertEqual(plan.issues[0].field, "radius")

    def test_missing_entity_type(self):
        self.assert_code("统计", "MISSING_PARAMETER")

    def test_empty_input(self):
        self.assert_code("   ", "EMPTY_INPUT")

    def test_errors_never_reach_executor(self):
        executor = RecordingExecutor()
        result = nl_dispatch.execute("画一堵墙", executor=executor)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "parse")
        self.assertEqual(executor.calls, [])
        self.assertIn("hint", result)


class TestDispatchAndFeedback(unittest.TestCase):
    def test_end_to_end_wall(self):
        executor = RecordingExecutor()
        result = nl_dispatch.execute("从 0,0 到 7000,0 画一堵墙", executor=executor)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stage"], "execute")
        self.assertEqual(len(executor.calls), 1)
        plan = executor.calls[0]
        self.assertEqual(plan.command, "HZQT")
        self.assertEqual(plan.inputs, ["0,0", "7000,0", ""])
        self.assertEqual(plan.verify_type, "TCH_WALL")
        self.assertIn("TCH_WALL +1", result["summary"])

    def test_dry_run_does_not_execute(self):
        executor = RecordingExecutor()
        result = nl_dispatch.execute("统计墙的数量", executor=executor, dry_run=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["stage"], "preview")
        self.assertEqual(result["would_execute"]["intent"], "count_entities")
        self.assertEqual(executor.calls, [])

    def test_data_only_backends_are_not_misreported_as_failure(self):
        # create_test_drawing / count_entities return plain data with no ok flag.
        executor = RecordingExecutor({"Name": "Drawing3.dwg", "is_owned_by_server": True})
        result = nl_dispatch.execute("新建测试图纸", executor=executor)
        self.assertTrue(result["ok"], result)
        self.assertNotIn("errors", result)

        executor = RecordingExecutor({
            "entity_type": "TCH_WALL", "count": 1, "model_space_total": 1,
        })
        result = nl_dispatch.execute("统计墙的数量", executor=executor)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["summary"], "TCH_WALL 数量：1。")

    def test_explicit_failure_flag_still_wins(self):
        executor = RecordingExecutor({"ok": False, "error": "boom"})
        result = nl_dispatch.execute("新建测试图纸", executor=executor)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["message"], "boom")

    def test_execution_failure_has_actionable_hint(self):
        executor = RecordingExecutor({
            "ok": False,
            "success": False,
            "diagnosis": "Tianzheng answered 'unknown command'",
        })
        result = nl_dispatch.execute("从 0,0 到 7000,0 画一堵墙", executor=executor)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "EXECUTION_FAILED")
        self.assertIn("TGStart.exe", result["hint"])

    def test_readonly_datastore_hint(self):
        executor = RecordingExecutor({"ok": False, "error": "DataStore.dbf is read-only"})
        result = nl_dispatch.execute("从 0,0 到 7000,0 画一堵墙", executor=executor)
        self.assertIn("diagnose_environment", result["hint"])

    def test_unsupported_ui_command_is_reported_honestly(self):
        # No executor injection: the unsupported branch must handle itself and
        # must never be replaced by a backend call.
        result = nl_dispatch.execute("打开图层管理器")
        self.assertFalse(result["ok"])
        self.assertIn("纯界面", result["execution"]["error"])


class TestRealInstallAlignment(unittest.TestCase):
    """Intents added from the install's real createobjcmd.ini must be recognised."""

    def test_extra_object_intents(self):
        cases = [
            ("画柱", "draw_column", "TGColumn"),
            ("画楼梯", "draw_stair", "TLStair"),
            ("画楼板", "draw_slab", "TSlab"),
            ("天正文字", "add_tianzheng_text", "TText"),
        ]
        for text, intent, command in cases:
            with self.subTest(text=text):
                plan = nl_parser.parse(text)
                self.assertEqual(plan.intent, intent)
                self.assertEqual(plan.command, command)
                self.assertEqual(plan.backend, "unsupported")
                self.assertTrue(plan.note)

    def test_env_check_reads_root_and_sys(self):
        import stat
        import tempfile

        from t20_arch_mcp import env_check

        with tempfile.TemporaryDirectory() as root:
            sys_dir = os.path.join(root, "SYS")
            ver_dir = os.path.join(root, "sys22x64")
            os.mkdir(sys_dir)
            os.mkdir(ver_dir)
            root_ds = os.path.join(root, "DataStore.dbf")
            ver_ds = os.path.join(ver_dir, "DataStore.dbf")
            paths = [
                root_ds,
                os.path.join(root, "tchdata.dbf"),
                os.path.join(sys_dir, "DataStore.dbf"),
                os.path.join(sys_dir, "tchdata.dbf"),
                ver_ds,
                os.path.join(ver_dir, "tchdata.dbf"),
            ]
            for path in paths:
                with open(path, "wb") as handle:
                    handle.write(b"x")
            os.chmod(root_ds, stat.S_IREAD)
            os.chmod(ver_ds, stat.S_IREAD)
            try:
                report = env_check.check_writable_files(root)
                self.assertTrue(report["blocking_issue"])
                self.assertIn(root_ds, report["readonly_files"])
                # The version folder is what T20 really loads at runtime.
                self.assertIn(ver_ds, report["readonly_files"])
                for scope in ("install_root", "SYS", "version_dir"):
                    self.assertTrue(any(f["scope"] == scope for f in report["files"]))
            finally:
                os.chmod(root_ds, stat.S_IWRITE | stat.S_IREAD)
                os.chmod(ver_ds, stat.S_IWRITE | stat.S_IREAD)


class TestCatalogConsistency(unittest.TestCase):
    def test_verify_type_is_inferred_from_command(self):
        cases = {"HZQT": "TCH_WALL", "hzqt": "TCH_WALL", "TGWall": "TCH_WALL",
                 "MC": "TCH_OPENING", "draw_wall": "TCH_WALL"}
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(nl_catalog.suggest_verify_type(command), expected)

    def test_unknown_command_has_no_fake_verify_type(self):
        self.assertIsNone(nl_catalog.suggest_verify_type("HZZW"))
        self.assertIsNone(nl_catalog.suggest_verify_type("SOMETHING"))

    def test_no_tianzheng_spec_uses_command_name_as_entity_type(self):
        # Native commands legitimately share a name with their entity type
        # (LINE -> LINE); the old bug was specific to Tianzheng ARX commands.
        for spec in nl_catalog.SPECS:
            with self.subTest(spec=spec.key):
                if spec.backend == "tianzheng" and spec.verify_type:
                    self.assertNotEqual(spec.verify_type, spec.command)


class TestRunnerDiagnosis(unittest.TestCase):
    def test_blocking_dialog_is_diagnosed(self):
        try:
            from t20_arch_mcp import tianzheng_runner
        except Exception as exc:  # pragma: no cover - non-Windows fallback
            self.skipTest(f"runner unavailable: {exc}")
        text = tianzheng_runner._diagnose(
            None, 0, {}, None, {"blocking_dialogs": [{"title": "DataStore"}]}
        )
        self.assertIn("dialog", text)
        self.assertIn("DataStore", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
