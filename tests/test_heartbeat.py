"""heartbeat.py 的单测：间隔计算、断档判定、告警静默期、坏文件不崩。

时间全部由调用方注入，不依赖真实时钟。
"""
import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import heartbeat

T0 = datetime.datetime(2026, 10, 18, 12, 0, 0)


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "heartbeat.json"
        env = patch.dict(os.environ, {"WORKBUDDY_HEARTBEAT_FILE": str(self.file)},
                         clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("WORKBUDDY_STALE_HOURS", None)

    def beat(self, at, payload=None, mode="silent"):
        """默认用定时任务的 mode：断档判定只看定时心跳。"""
        return heartbeat.beat(payload or {"ok": True, "data": {"result": "CLAIMED"}},
                              mode, now=at)

    def test_first_beat_has_no_gap(self):
        info = self.beat(T0)
        self.assertEqual(info["gap_hours"], None)
        self.assertFalse(info["stale_gap"])
        self.assertTrue(self.file.exists())

    def test_gap_and_stale_flag(self):
        self.beat(T0)
        info = self.beat(T0 + datetime.timedelta(hours=30))
        self.assertEqual(info["prev_at"], "2026-10-18 12:00:00")
        self.assertAlmostEqual(info["gap_hours"], 30.0, places=1)
        self.assertEqual(info["gap_days"], 1)
        self.assertTrue(info["stale_gap"])

    def test_short_gap_is_not_stale(self):
        self.beat(T0)
        info = self.beat(T0 + datetime.timedelta(hours=10))
        self.assertFalse(info["stale_gap"])

    def test_record_fields(self):
        self.beat(T0, {"ok": False, "data": {"result": "AUTH_ERROR"},
                       "attempts_used": 2, "retried": True}, mode="silent")
        rec = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(rec["result"], "AUTH_ERROR")
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["mode"], "silent")
        self.assertEqual(rec["attempts"], 2)
        self.assertTrue(rec["retried"])
        self.assertEqual(rec["date"], "2026-10-18")

    def test_mark_alerted_keeps_other_fields(self):
        self.beat(T0)
        heartbeat.mark_alerted(now=T0 + datetime.timedelta(hours=1))
        rec = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(rec["result"], "CLAIMED")
        self.assertEqual(rec["alerted_at"], "2026-10-18 13:00:00")

    def test_status_missing_file(self):
        st = heartbeat.status(now=T0)
        self.assertFalse(st["exists"])
        self.assertFalse(st["stale"])
        self.assertIsNone(st["age_hours"])

    def test_status_fresh_and_stale(self):
        self.beat(T0)
        fresh = heartbeat.status(now=T0 + datetime.timedelta(hours=3))
        self.assertTrue(fresh["exists"])
        self.assertFalse(fresh["stale"])
        self.assertAlmostEqual(fresh["age_hours"], 3.0, places=1)

        old = heartbeat.status(now=T0 + datetime.timedelta(hours=40))
        self.assertTrue(old["stale"])
        self.assertEqual(old["days_since"], 2)   # 18 日 12:00 → 20 日 04:00

    def test_should_alert_rules(self):
        self.assertFalse(heartbeat.should_alert()[0])          # 没心跳不告警
        self.beat(T0)
        self.assertFalse(heartbeat.should_alert(
            heartbeat.status(now=T0 + datetime.timedelta(hours=2)))[0])
        st = heartbeat.status(now=T0 + datetime.timedelta(hours=48))
        ok, why = heartbeat.should_alert(st)
        self.assertTrue(ok)
        self.assertIn("stale", why)

    def test_alert_throttled_within_window(self):
        self.beat(T0)
        self.assertTrue(heartbeat.should_alert(
            heartbeat.status(now=T0 + datetime.timedelta(hours=48)))[0])
        heartbeat.mark_alerted()
        st = heartbeat.status(now=T0 + datetime.timedelta(hours=48))
        ok, why = heartbeat.should_alert(st)
        self.assertFalse(ok)
        self.assertEqual(why, "throttled")

    def test_gap_alert_only_when_stale(self):
        self.beat(T0)
        short = self.beat(T0 + datetime.timedelta(hours=5))
        self.assertIsNone(heartbeat.gap_alert(short))
        long = self.beat(T0 + datetime.timedelta(hours=50))
        alert = heartbeat.gap_alert(long)
        self.assertIsNotNone(alert)
        title, body = alert
        # 中间那次 5 小时后的心跳已经刷新了基准，所以这里量到的是 45 小时
        self.assertIn("45", title)
        self.assertIn("2026-10-18 17:00:00", body)

    def test_corrupt_file_does_not_raise(self):
        self.file.write_text("{ broken json", encoding="utf-8")
        st = heartbeat.status(now=T0)
        self.assertFalse(st["exists"])
        self.beat(T0)                    # 覆盖坏文件后恢复正常
        self.assertTrue(heartbeat.status(now=T0)["exists"])

    def test_write_failure_is_swallowed(self):
        # 路径指向目录：原子写必然失败，但 beat 不能抛异常
        os.environ["WORKBUDDY_HEARTBEAT_FILE"] = self.temp.name
        info = heartbeat.beat({"ok": True, "data": {"result": "OK"}}, "auto", now=T0)
        self.assertIsNone(info["gap_hours"])

    def test_stale_hours_env_override(self):
        os.environ["WORKBUDDY_STALE_HOURS"] = "2"
        self.beat(T0)
        info = self.beat(T0 + datetime.timedelta(hours=3))
        self.assertTrue(info["stale_gap"])
        self.assertEqual(heartbeat.status(now=T0)["stale_hours"], 2.0)

    def test_alert_text_mentions_panel(self):
        self.beat(T0)
        title, body = heartbeat.alert_text(
            heartbeat.status(now=T0 + datetime.timedelta(hours=48)))
        self.assertIn("停跑", title)
        self.assertIn("8765", body)

    def test_manual_run_does_not_refresh_scheduled_beat(self):
        # 这是分模式的核心：手动点一次"运行测试"不能洗掉断档信号
        self.beat(T0)                                        # 定时任务 12:00 跑过
        self.beat(T0 + datetime.timedelta(hours=30), mode="auto")   # 手动跑了一次
        st = heartbeat.status(now=T0 + datetime.timedelta(hours=30))
        self.assertEqual(st["at"], "2026-10-19 18:00:00")     # 顶层是手动那次
        self.assertFalse(st["stale"])                         # 顶层不算断档
        sch = st["scheduled"]
        self.assertEqual(sch["at"], "2026-10-18 12:00:00")    # 定时记录没被动
        self.assertTrue(sch["stale"])
        ok, why = heartbeat.should_alert(st)
        self.assertTrue(ok)          # 手动运行救不了它
        self.assertIn("stale", why)

    def test_manual_only_has_no_stale_alert(self):
        self.beat(T0, mode="auto")
        st = heartbeat.status(now=T0 + datetime.timedelta(hours=100))
        ok, why = heartbeat.should_alert(st)
        self.assertFalse(ok)
        self.assertEqual(why, "no-scheduled-beat")

    def test_scheduled_fields_survive_manual_runs(self):
        self.beat(T0, {"ok": False, "data": {"result": "ERROR"}})
        self.beat(T0 + datetime.timedelta(hours=1), mode="auto")
        rec = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(rec["scheduled_result"], "ERROR")
        self.assertEqual(rec["scheduled_at"], "2026-10-18 12:00:00")
        self.assertEqual(rec["result"], "CLAIMED")   # 手动那次是成功的

    def test_failing_alert_when_notify_disabled(self):
        # 跑了但一直失败、推送又没开 —— 这是最容易"静默烂掉"的组合
        self.beat(T0, {"ok": False, "data": {"result": "ERROR"}})
        st = heartbeat.status(now=T0 + datetime.timedelta(minutes=5))
        with patch.object(heartbeat, "_notify_enabled", return_value=False):
            ok, why = heartbeat.should_alert(st)
            self.assertTrue(ok)
            self.assertIn("failing", why)
            title, body = heartbeat.alert_text(st)
        self.assertIn("失败", title)
        self.assertIn("ERROR", body)

    def test_failing_is_quiet_when_notify_enabled(self):
        # 推送开着时失败由 notify 发，心跳不重复
        self.beat(T0, {"ok": False, "data": {"result": "ERROR"}})
        st = heartbeat.status(now=T0 + datetime.timedelta(minutes=5))
        with patch.object(heartbeat, "_notify_enabled", return_value=True):
            ok, why = heartbeat.should_alert(st)
        self.assertFalse(ok)
        self.assertEqual(why, "notify-handles-it")


if __name__ == "__main__":
    unittest.main()
