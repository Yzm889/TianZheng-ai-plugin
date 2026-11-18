"""门窗模板宽度登记表的单元测试（不需要 CAD）。

为什么要有登记表：天正不暴露门窗宽度（GetXData 为空、无 Width 属性、包围盒
混了高度），所以宽度只能由人登记一次，再按登记值匹配模板。
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from t20_arch_mcp import opening_library


class TestOpeningLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t20lib_")
        self.state = os.path.join(self.tmp, ".t20_opening_library.json")
        patcher = mock.patch.dict(os.environ, {"T20_OPENING_LIBRARY": self.state})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_register_then_pick_by_width(self):
        r = opening_library.register("4C7", 1500, "door", document="a.dwg")
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 1)

        hit = opening_library.pick("a.dwg", "door", 1500)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["handle"], "4C7")

    def test_pick_respects_tolerance(self):
        opening_library.register("4C7", 1500, "door", document="a.dwg")
        self.assertIsNotNone(opening_library.pick("a.dwg", "door", 1520))
        self.assertIsNone(opening_library.pick("a.dwg", "door", 1600))

    def test_kind_is_not_mixed_up(self):
        opening_library.register("D1", 1500, "door", document="a.dwg")
        opening_library.register("W1", 1500, "window", document="a.dwg")
        self.assertEqual(opening_library.pick("a.dwg", "window", 1500)["handle"], "W1")
        self.assertEqual(opening_library.pick("a.dwg", "door", 1500)["handle"], "D1")

    def test_documents_are_separated(self):
        opening_library.register("X1", 1000, "door", document="a.dwg")
        self.assertIsNone(opening_library.pick("b.dwg", "door", 1000))
        self.assertEqual(opening_library.pick("a.dwg", "door", 1000)["handle"], "X1")

    def test_register_rejects_bad_width(self):
        for bad in (0, -500, "abc", None):
            r = opening_library.register("X", bad, "door", document="a.dwg")
            self.assertFalse(r["ok"], bad)

    def test_re_register_same_handle_overwrites(self):
        opening_library.register("X1", 1000, "door", document="a.dwg")
        opening_library.register("x1", 1200, "door", document="a.dwg")
        entries = opening_library.entries("a.dwg")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["width"], 1200)

    def test_persists_to_json(self):
        opening_library.register("X1", 1000, "door", document="a.dwg")
        with open(self.state, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["entries"][0]["handle"], "X1")

    def test_forget_removes_entry(self):
        opening_library.register("X1", 1000, "door", document="a.dwg")
        r = opening_library.forget("x1", "a.dwg")
        self.assertEqual(r["removed"], 1)
        self.assertEqual(opening_library.entries("a.dwg"), [])

    def test_as_candidates_accepts_handle_and_width_forms(self):
        opening_library.register("X1", 1000, "door", document="a.dwg")
        self.assertEqual(opening_library.as_candidates("X1", "a.dwg"),
                         [{"handle": "X1", "width": 1000.0}])
        self.assertEqual(opening_library.as_candidates({"handle": "X2", "width": 1500}),
                         [{"handle": "X2", "width": 1500.0}])
        self.assertEqual(
            len(opening_library.as_candidates(["X1", "X2"], "a.dwg")), 2)
        self.assertEqual(opening_library.as_candidates(None), [])

    def test_select_reasons(self):
        cands = [{"handle": "D1", "width": 1000}, {"handle": "D2", "width": 1500}]
        self.assertEqual(opening_library.select(cands, "door", 1500),
                         ({"handle": "D2", "width": 1500}, "matched"))
        self.assertEqual(opening_library.select(cands, "door", 1200)[1], "no_match")
        self.assertEqual(opening_library.select(cands, "door", None)[1], "no_width")
        self.assertEqual(opening_library.select([], "door", 1200)[1], "empty")


if __name__ == "__main__":
    unittest.main()
