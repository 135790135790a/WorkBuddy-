"""签到总览页：聚合、渲染、以及"数据源全挂也不白屏"的兜底。

不测浏览器渲染，只测数据与 HTML 结构。
"""
import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import board

NOW = datetime.datetime(2026, 10, 18, 21, 0, 0)


def item(ts, result="ALREADY", ok=True, mode="auto", attempts=1,
         credit=100, total=600, file="", evidence=""):
    return {"ts": ts, "date": ts[:10], "mode": mode, "ok": ok, "result": result,
            "attempts": attempts, "credit": credit, "total": total,
            "file": file, "evidence": evidence,
            "has_report": bool(file), "has_evidence": bool(evidence)}


class CollectTests(unittest.TestCase):
    def collect(self, items, days=None, ms=None):
        with patch.object(board.archive, "list_reports", return_value=items), \
                patch.object(board.history, "load_store", return_value=days or {}), \
                patch.object(board.milestone, "compute", return_value=ms or {"streak": 3}), \
                patch.object(board.heartbeat, "status", return_value={}):
            return board.collect(NOW)

    def test_counts_today_runs(self):
        d = self.collect([
            item("2026-10-18 09:00:00"),
            item("2026-10-18 10:00:00", result="ERROR", ok=False),
            item("2026-10-17 09:00:00"),
        ])
        self.assertEqual(d["today_runs"], 2)
        self.assertEqual(d["today_ok"], 1)
        self.assertEqual(d["today_fail"], 1)
        self.assertEqual(d["dist"]["ALREADY"], 2)

    def test_per_day_prefers_success_flag(self):
        d = self.collect([item("2026-10-17 09:00:00", result="ERROR", ok=False)])
        self.assertEqual(d["per_day"]["2026-10-17"]["ok"], 0)
        self.assertEqual(d["per_day"]["2026-10-17"]["fail"], 1)

    def test_history_only_day_counts_as_signed(self):
        d = self.collect([], days={"2026-10-16": {"credit": 100, "total": 500}})
        self.assertEqual(d["per_day"]["2026-10-16"]["ok"], 1)
        self.assertEqual(d["per_day"]["2026-10-16"]["total"], 500)

    def test_sources_down_still_returns(self):
        with patch.object(board.archive, "list_reports", side_effect=RuntimeError("x")), \
                patch.object(board.history, "load_store", side_effect=RuntimeError("x")), \
                patch.object(board.milestone, "compute", side_effect=RuntimeError("x")), \
                patch.object(board.heartbeat, "status", side_effect=RuntimeError("x")):
            d = board.collect(NOW)
        self.assertEqual(d["items"], [])
        self.assertEqual(d["streak"], 0)
        self.assertIsNone(d["total_credits"])


class RenderTests(unittest.TestCase):
    def sample(self, **kw):
        d = {"now": NOW, "today": "2026-10-18", "items": [], "days": {},
             "per_day": {}, "streak": 6, "best": 6, "next": {}, "signed_today": True,
             "total_credits": 600, "today_runs": 3, "today_ok": 3, "today_fail": 0,
             "dist": {"ALREADY": 3}, "heartbeat": {}}
        d.update(kw)
        return d

    def test_kpis_and_sections(self):
        html = board.render(self.sample())
        for text in ("连续天数", "累计积分", "今日运行", "今日状态", "最近 8 周",
                     "累计积分走势", "结果分布", "最近运行", "里程碑"):
            self.assertIn(text, html)

    def test_failure_turns_kpi_red(self):
        html = board.render(self.sample(today_fail=2, today_runs=3, today_ok=1))
        self.assertIn("kpi warn", html)

    def test_rows_link_to_report(self):
        rows = board._rows([item("2026-10-18 20:49:39", file="2026-10-18_204939.html")])
        self.assertIn('href="reports/2026-10-18_204939.html"', rows)
        self.assertIn("今日已签", rows)

    def test_rows_without_report(self):
        rows = board._rows([item("2026-10-18 20:49:39", evidence="x.json")])
        self.assertIn("仅凭证", rows)

    def test_heatmap_marks_failure_and_unknown(self):
        per_day = {"2026-10-18": {"ok": 0, "fail": 2, "credit": None, "total": None}}
        html = board._heatmap(per_day, NOW.date())
        self.assertIn("cell bad", html)
        self.assertIn("cell none", html)
        self.assertIn("无记录", html)

    def test_spark_needs_two_values(self):
        flat = {"2026-10-18": {"total": 600}}
        self.assertIn("积分曲线需要多天数据", board._spark(flat))
        curve = board._spark({"2026-10-17": {"total": 500}, "2026-10-18": {"total": 600}})
        self.assertIn("<svg", curve)

    def test_unknown_result_shown_as_is(self):
        self.assertEqual(board._short("SOME_NEW_CODE"), "SOME_NEW_CODE")
        self.assertEqual(board._short(None), "—")


class WriteTests(unittest.TestCase):
    def test_write_to_temp_path(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        out = Path(temp.name) / "history.html"
        with patch.object(board, "OUT", str(out)), \
                patch.object(board.archive, "list_reports", return_value=[item("2026-10-18 09:00:00")]), \
                patch.object(board.history, "load_store", return_value={}), \
                patch.object(board.milestone, "compute", return_value={"streak": 6}), \
                patch.object(board.heartbeat, "status", return_value={}):
            ok, msg = board.write(NOW)
        self.assertTrue(ok, msg)
        self.assertTrue(out.exists())
        self.assertIn("WorkBuddy 签到总览", out.read_text(encoding="utf-8"))

    def test_write_reports_failure_without_raising(self):
        with patch.object(board, "OUT", "Z:/definitely/missing/dir/history.html"):
            ok, msg = board.write(NOW)
        self.assertFalse(ok)
        self.assertIn("失败", msg)


if __name__ == "__main__":
    unittest.main()
