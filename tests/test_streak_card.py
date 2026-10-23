"""连签卡片：取数、排版、以及"没数据也得能出图"的兜底。

不测截图（依赖本机浏览器），只测纯逻辑：milestone/history 缺失、连续天数为 0、
三位数天数的字号收缩。
"""
import datetime
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("streak_card", HERE / "streak-card.py")
card = importlib.util.module_from_spec(spec)
spec.loader.exec_module(card)

TODAY = datetime.datetime(2026, 10, 18, 20, 0, 0)


class CollectTests(unittest.TestCase):
    def test_collect_with_data(self):
        d = card.collect(TODAY)
        self.assertEqual(d["date"], "2026-10-18")
        self.assertEqual(len(d["grid"]), card.SHOWN_DAYS)
        self.assertTrue(d["grid"][-1]["today"])
        self.assertFalse(d["grid"][0]["today"])
        self.assertGreaterEqual(d["streak"], 0)

    def test_grid_respects_streak(self):
        fake = {"streak": 3, "best": 3, "today_signed": True,
                "next": {"days": 7, "remaining": 4, "progress": 0.43, "eta": "2026-10-22"}}
        with patch.object(card.milestone, "compute", return_value=fake), \
                patch.object(card.history, "load_days", return_value=([], "2026-10-18")), \
                patch.object(card.history, "load_store", return_value={}):
            d = card.collect(TODAY)
        self.assertEqual(d["streak"], 3)
        self.assertEqual([g["signed"] for g in d["grid"]][-3:], [True, True, True])
        self.assertEqual(d["hit"], 3)

    def test_survives_missing_modules(self):
        # milestone / history 全挂掉也要能出图，不能白跑
        with patch.object(card.milestone, "compute", side_effect=RuntimeError("x")), \
                patch.object(card.history, "load_days", side_effect=RuntimeError("x")), \
                patch.object(card.history, "load_store", side_effect=RuntimeError("x")):
            d = card.collect(TODAY)
        self.assertEqual(d["streak"], 0)
        self.assertEqual(d["total"], None)
        self.assertEqual(d["hit"], 0)

    def test_total_read_from_store(self):
        days = [{"date": "2026-10-18", "credit": 100, "streak": 6}]
        store = {"2026-10-18": {"credit": 100, "streak": 6, "total": 600}}
        with patch.object(card.history, "load_days", return_value=(days, "2026-10-18")), \
                patch.object(card.history, "load_store", return_value=store), \
                patch.object(card.milestone, "compute", return_value={"streak": 6}):
            d = card.collect(TODAY)
        self.assertEqual(d["total"], 600)
        self.assertEqual(d["today_credit"], 100)


class RenderTests(unittest.TestCase):
    def base(self, **kw):
        d = {"date": "2026-10-18", "stamp": "2026-10-18 20:00", "streak": 6,
             "best": 6, "signed_today": True, "total": 600, "today_credit": 100,
             "grid": [{"date": "2026-10-18", "signed": True, "today": True}],
             "hit": 1, "next_days": 7, "next_remaining": 1,
             "next_progress": 0.857, "next_eta": "2026-10-19"}
        d.update(kw)
        return d

    def test_html_contains_key_facts(self):
        html = card.render_html(self.base())
        self.assertIn("6", html)
        self.assertIn("连续签到", html)
        self.assertIn("600", html)
        self.assertIn("还差 1 天", html)
        self.assertIn("1080", html)

    def test_zero_streak_copy(self):
        html = card.render_html(self.base(streak=0, signed_today=False,
                                          total=None, today_credit=None))
        self.assertIn("今天开始", html)

    def test_today_pending_copy(self):
        html = card.render_html(self.base(streak=5, signed_today=False))
        self.assertIn("今天还没签", html)

    def test_font_shrinks_for_big_numbers(self):
        small = card.render_html(self.base(streak=6))
        big = card.render_html(self.base(streak=365))
        self.assertIn("font-size:268px", small)
        self.assertIn("font-size:176px", big)

    def test_no_milestone_omits_bar(self):
        html = card.render_html(self.base(next_days=None))
        self.assertNotIn("下一个里程碑", html)


class FormatTests(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(card._fmt(None), "—")
        self.assertEqual(card._fmt(600), "600")
        self.assertEqual(card._fmt(12345), "12,345")

    def test_png_size_reader(self):
        self.assertIsNone(card._png_size(str(HERE / "no-such.png")))


if __name__ == "__main__":
    unittest.main()
