"""视频号带货闸门测试（离线、确定性、不读真实业务数据）。

覆盖：
  - 脚本内不得出现写死的本机绝对路径
  - 词库能动态定位，合计 198 词（111 广告法 + 87 视频号扩展）
  - 词库为空时必须 FAIL（fail-closed），不得静默放行
  - 违禁词在对外文案中能被检出
  - 发布计划器对未上架 / 未绑定小店一律不给挂车

运行：
  python -m unittest discover -s tests
"""
import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


class TestNoHardcodedPaths(unittest.TestCase):
    def test_scripts_and_refs_have_no_absolute_user_path(self):
        bad = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+|/c/Users/", re.I)
        offenders = []
        for p in list(SCRIPTS.rglob("*.py")) + list((ROOT / "references").rglob("*.md")):
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if bad.search(line):
                    offenders.append("%s:%d %s" % (p.relative_to(ROOT), i, line.strip()[:90]))
        self.assertEqual(offenders, [], "发现硬编码本机路径：\n" + "\n".join(offenders))


class TestWordlist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cg = load("cg", SCRIPTS / "channel_gates.py")

    def test_base_wordlist_resolves(self):
        self.assertTrue(self.cg.BASE_WORDLIST.is_file(), str(self.cg.BASE_WORDLIST))

    def test_vc_wordlist_ships_with_package(self):
        self.assertTrue(self.cg.VC_WORDLIST.is_file())
        self.assertEqual(self.cg.VC_WORDLIST.parent.parent, ROOT)

    def test_package_wordlist_ships_and_is_usable(self):
        """包内自带的基础词库必须在、且真的能解析出词。

        这是 P2-2 的回归点：不能因为没装第三方技能就让闸门整体瘫痪。
        """
        p = ROOT / "references" / "banned-words.md"
        self.assertTrue(p.is_file(), "包内基础词库缺失：%s" % p)
        cats = self.cg.parse_wordlist(p)
        self.assertGreater(sum(len(v) for v in cats.values()), 50)
        self.assertTrue(all(cats.keys()), "存在空类别名")

    def test_vc_wordlist_is_87_and_machine_independent(self):
        """视频号扩展层是包内资产，词数确定；基础层可能来自第三方，不能写死 111。"""
        n_vc = sum(len(v) for v in self.cg.parse_wordlist(self.cg.VC_WORDLIST).values())
        self.assertEqual(n_vc, 87, "视频号扩展层词数变了")

        n_base = sum(len(v) for v in self.cg.parse_wordlist(self.cg.BASE_WORDLIST).values())
        self.assertGreaterEqual(n_base, 50, "基础层词数过少，检出力不足")
        if self.cg.WORDLIST_SOURCE == "ad-redline-guardian":
            self.assertEqual(n_base, 111, "第三方基础词库词数与基线记录不符")
        else:
            self.assertEqual(self.cg.WORDLIST_SOURCE, "builtin-statutory")

    def test_parse_wordlist_missing_returns_empty(self):
        self.assertEqual(self.cg.parse_wordlist(Path("__nope__/x.md")), {})


class TestGate2FailClosed(unittest.TestCase):
    def setUp(self):
        self.cg = load("cg2", SCRIPTS / "channel_gates.py")

    def test_empty_wordlists_fail_not_pass(self):
        with mock.patch.object(self.cg, "BASE_WORDLIST", Path("__nope__/a.md")), \
             mock.patch.object(self.cg, "VC_WORDLIST", Path("__nope__/b.md")), \
             tempfile.TemporaryDirectory() as d:
            chk, issues, _ = self.cg.gate2_compliance(Path(d), {})
        self.assertEqual(chk["gate"], "FAIL")
        self.assertTrue(any("词库" in i for i in issues))

    def test_banned_word_detected_in_ad_copy(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            write_json(run / "02_commerce" / "titles.json",
                       {"sku_id": "T", "titles": ["全网最低价 国家级 第一"]})
            chk, issues, _ = self.cg.gate2_compliance(run, {})
        self.assertEqual(chk["gate"], "FAIL")
        self.assertTrue(any("违禁词" in i for i in issues), issues)

    def test_clean_copy_passes(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            write_json(run / "02_commerce" / "titles.json",
                       {"sku_id": "T", "titles": ["买一把尺子"]})
            chk, issues, _ = self.cg.gate2_compliance(run, {})
        self.assertEqual(chk["gate"], "PASS", issues)


class TestPlannerNoLinkWithoutPrereq(unittest.TestCase):
    def setUp(self):
        self.pl = load("planner", SCRIPTS / "planner.py")

    def _plan(self, facts):
        fn = getattr(self.pl, "plan", None) or getattr(self.pl, "build_plan", None)
        if fn is None:
            self.skipTest("planner.py 未暴露可调用入口，改由 CLI 冒烟覆盖")
        return fn(facts)

    def test_pre_launch_cannot_attach(self):
        res = self._plan({"sku_id": "T", "account_mode": "pre_launch",
                          "product_listing_status": "not_listed"})
        self.assertFalse(res["can_attach_product_link"])
        self.assertTrue(res["blockers"])

    def test_unlisted_product_cannot_attach(self):
        res = self._plan({"sku_id": "T", "account_mode": "self_operated",
                          "product_listing_status": "not_listed"})
        self.assertFalse(res["can_attach_product_link"])


class TestSelfOperatedThreshold(unittest.TestCase):
    """P2-3 回归：自营短视频挂车不是一律 0 粉。

    官方《微信小店商家和带货者账号体系管理规则-20260612版本》原文：
      短视频带货：对外展示账号无关注数限制，其余账号需满足 ≥1000 有效关注。
    所以「是否可对外展示」没说清时，不能默认给「可挂车」。
    """

    def setUp(self):
        self.pl = load("planner2", SCRIPTS / "planner.py")
        self.cg = load("cg3", SCRIPTS / "channel_gates.py")

    def test_unknown_displayable_blocks_attach(self):
        res = self.pl.plan({"sku_id": "T", "account_mode": "self_operated",
                            "product_listing_status": "listed"})
        self.assertFalse(res["can_attach_product_link"])
        self.assertTrue(any("可对外展示" in b for b in res["blockers"]), res["blockers"])

    def test_displayable_true_allows_attach(self):
        res = self.pl.plan({"sku_id": "T", "account_mode": "self_operated",
                            "store_displayable": True,
                            "product_listing_status": "listed",
                            "video_channel_binding_status": "bound"})
        perm = [c for c in res["checks"] if c["check"] == "带货权限"][0]
        self.assertTrue(perm["pass"], perm)

    def test_not_displayable_needs_1000(self):
        res = self.pl.plan({"sku_id": "T", "account_mode": "self_operated",
                            "store_displayable": False, "effective_followers": 300,
                            "product_listing_status": "listed"})
        perm = [c for c in res["checks"] if c["check"] == "带货权限"][0]
        self.assertFalse(perm["pass"])
        self.assertIn("1000", perm["detail"])

    def test_desc_does_not_claim_zero_fans_for_all(self):
        """文案不能再说「自营全部 0 粉可挂车」。"""
        desc = self.pl.THRESHOLD["self_operated"]["desc"]
        self.assertNotIn("全部 0 粉", desc)
        self.assertIn("可对外展示", desc)

    def test_gate1_requires_displayable_flag(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            write_json(run / "01_research" / "BLOCKED_FIELDS.json", {"blocked": [{"field": "price"}]})
            chk, issues, _ = self.cg.gate1_sku_guard(
                run, {"account_mode": "self_operated", "product_listing_status": "listed"}, "T")
        self.assertEqual(chk["gate"], "FAIL")
        self.assertTrue(any("store_displayable" in i for i in issues), issues)


class TestGate3AiDisclosureSemantics(unittest.TestCase):
    """P2-3 回归：别把「建议」当「平台规则」。

    官方公告只要求「发表页 → 添加标注 → 勾选含 AI 生成内容」。
    「画面叠加文字」「前 5 秒」在官方公告里查不到，只能是建议，不能阻断发布。
    """

    def setUp(self):
        self.cg = load("cg4", SCRIPTS / "channel_gates.py")
        self.facts = {"ai_generated_content": True, "account_mode": "self_operated",
                      "ai_content_matches_product": True}

    def _gate(self, plan):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            write_json(run / "05_publish" / "publish_plan.json", plan)
            return self.cg.gate3_ai_disclosure(run, self.facts)

    def test_page_checkbox_alone_is_enough(self):
        chk, issues, detail = self._gate({"ai_label_page_checked": True})
        self.assertEqual(chk["gate"], "PASS", issues)

    def test_missing_page_checkbox_fails(self):
        chk, issues, _ = self._gate({"ai_label_on_screen": True,
                                     "ai_label_within_first_5s": True})
        self.assertEqual(chk["gate"], "FAIL")
        self.assertTrue(any("ai_label_page_checked" in i for i in issues), issues)

    def test_on_screen_label_is_advisory_not_blocking(self):
        chk, issues, detail = self._gate({"ai_label_page_checked": True})
        self.assertEqual(chk["gate"], "PASS")
        self.assertTrue(any("advisory" in d for d in detail if isinstance(d, dict)),
                        "画面标识缺失应只给建议，不应进 issues")


if __name__ == "__main__":
    unittest.main()
