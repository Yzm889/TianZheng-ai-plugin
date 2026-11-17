"""需求预检测试（不需要 CAD）。

对应工作流要求：
  收到指令 -> 先解析需要哪些组件 -> 区分"我能造"和"需要用户给"
            -> 一次性问用户，而不是先动手再半路卡住。
"""
import unittest

from t20_arch_mcp import prereq_check


class TestPrereqCheck(unittest.TestCase):
    def test_classroom_asks_for_openings_up_front(self):
        # "建一个教室" implies doors/windows; must be requested BEFORE drawing
        r = prereq_check.analyze("建一个教室", {})
        self.assertGreaterEqual(r["need_user_count"], 1)
        kinds = [n.get("template_required") for n in r["need_from_user"]]
        self.assertIn("TDBOPENING", kinds)
        # walls and rooms are self-buildable
        comps = [c["component"] for c in r["i_can_build"]]
        self.assertIn("墙", comps)
        self.assertIn("房间", comps)

    def test_plain_wall_command_needs_nothing(self):
        r = prereq_check.analyze("画墙", {})
        self.assertEqual(r["need_user_count"], 0)
        self.assertTrue(r["ok"])

    def test_existing_template_suppresses_the_request(self):
        # if the drawing already has an opening, do not ask again
        r = prereq_check.analyze("建一个教室", {"TDBOPENING": 3, "TDBWALL": 4})
        self.assertEqual(r["need_user_count"], 0)
        self.assertTrue(r["ok"])

    def test_message_lists_steps_and_spec(self):
        r = prereq_check.analyze("建一个教室", {})
        msg = r["message"]
        self.assertIn("我自己能造", msg)
        self.assertIn("需要你帮我建", msg)
        self.assertIn("MC", msg)
        self.assertIn("鼠标", msg)          # tells the user the pick must be manual

    def test_never_suggests_searching(self):
        r = prereq_check.analyze("建一个教室", {})
        for bad in ("安装目录", "图库", "自己找"):
            self.assertNotIn(bad, r["message"])

    def test_unknown_instruction_returns_guidance(self):
        r = prereq_check.analyze("随便画点什么", {})
        self.assertFalse(r["need_from_user"])
        self.assertIn("没有识别出", r["message"])


if __name__ == "__main__":
    unittest.main()
