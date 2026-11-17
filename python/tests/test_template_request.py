"""模板请求说明的单元测试（不需要 CAD）。

对应一条明确的产品原则：
  缺模板时不要去翻安装目录或图库，也不要硬闯界面命令；
  直接返回"需要用户在 CAD 里创建什么组件、什么规格、什么步骤"。
"""
import unittest

from t20_arch_mcp import template_request


class TestTemplateRequest(unittest.TestCase):
    def test_known_template_returns_actionable_steps(self):
        r = template_request.missing_template_request("TDBOPENING", "插门窗需要模板")
        self.assertFalse(r["ok"])
        self.assertTrue(r["need_user_action"])
        self.assertEqual(r["stage"], "missing_template")
        self.assertEqual(r["command"], "MC")
        self.assertGreaterEqual(len(r["steps"]), 3)
        # the message must tell the user what to create, not what to search
        self.assertIn("请在天正里插入", r["message"])
        self.assertIn("插门窗需要模板", r["message"])

    def test_wall_template_uses_hzqt(self):
        r = template_request.missing_template_request("TDBWALL")
        self.assertEqual(r["command"], "HZQT")
        self.assertIn("墙体", r["message"])

    def test_loose_name_matching(self):
        for name in ("opening", "TDBOPENING", "Opening"):
            r = template_request.missing_template_request(name)
            self.assertEqual(r.get("template_required"), "TDBOPENING", name)

    def test_unknown_template_still_asks_user(self):
        r = template_request.missing_template_request("SOMETHING_ELSE")
        self.assertFalse(r["ok"])
        self.assertTrue(r["need_user_action"])
        self.assertIn("手动插入", r["message"])

    def test_all_requirements_cover_clone_types(self):
        reqs = template_request.all_requirements()
        for key in ("TDBWALL", "TDBOPENING"):
            self.assertIn(key, reqs)
            self.assertIn("command", reqs[key])
            self.assertIn("spec", reqs[key])

    def test_message_never_mentions_searching(self):
        # guard the principle: no "look for it in the install folder" wording
        r = template_request.missing_template_request("TDBOPENING")
        for bad in ("安装目录", "图库", "搜索", "自己找"):
            self.assertNotIn(bad, r["message"])


if __name__ == "__main__":
    unittest.main()
