"""signin_runner 与心跳的衔接：哪些模式该写心跳、payload 是否带断档信息。

不启动真进程：只测 _done() 这个收尾函数，run_with_retry 的循环另有冒烟验证。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import signin_runner


class RunnerHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "heartbeat.json"
        env = patch.dict(os.environ, {"WORKBUDDY_HEARTBEAT_FILE": str(self.file)},
                         clear=False)
        env.start()
        self.addCleanup(env.stop)

    def payload(self):
        return {"ok": True, "data": {"result": "CLAIMED"}, "mode": "auto",
                "attempts_used": 1, "retried": False}

    def test_auto_mode_writes_heartbeat(self):
        out = signin_runner._done(self.payload(), "auto")
        self.assertIn("heartbeat", out)
        self.assertTrue(self.file.exists())
        rec = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(rec["result"], "CLAIMED")
        self.assertEqual(rec["mode"], "auto")

    def test_status_mode_is_probe_only(self):
        p = self.payload()
        out = signin_runner._done(p, "status")
        self.assertIs(out, p)                      # 原样返回，不改 payload
        self.assertFalse(self.file.exists())       # 也不写心跳

    def test_payload_not_mutated_in_place(self):
        p = self.payload()
        out = signin_runner._done(p, "silent")
        self.assertNotIn("heartbeat", p)
        self.assertIn("heartbeat", out)


class SilentLogFallbackTests(unittest.TestCase):
    """silent 模式不打印 stdout，结果只能从日志里捞。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "signin.log"
        self.log.write_text('[2026-10-18 00:05:00] {"result": "CLAIMED"}\n', encoding="utf-8")
        env = patch.dict(os.environ, {"WORKBUDDY_SIGNIN_LOG": str(self.log)}, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def run_once_with(self, appended):
        def fake_run(*_a, **_k):
            if appended:
                with open(self.log, "a", encoding="utf-8") as f:
                    f.write(appended)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        with patch.object(signin_runner.subprocess, "run", side_effect=fake_run):
            return signin_runner.run_once("silent")

    def test_picks_up_line_written_by_this_run(self):
        line = ('[2026-10-18 20:15:28] {"result": "ALREADY", "today_credit": 100,'
                ' "streak_days": 6, "report": "今日已签过"}\n')
        res = self.run_once_with(line)
        self.assertEqual(res["data"]["result"], "ALREADY")
        self.assertEqual(res["data"]["streak_days"], 6)
        self.assertEqual(res["raw"], "")          # stdout 确实是空的

    def test_empty_run_does_not_reuse_old_line(self):
        # 轮询空跑不写日志：不能把上一轮的结果当成本次的
        res = self.run_once_with("")
        self.assertIsNone(res["data"])

    def test_ignores_unrelated_lines(self):
        res = self.run_once_with("some noise without json\n")
        self.assertIsNone(res["data"])


class ConfigTests(unittest.TestCase):
    def test_log_path_env_override(self):
        with patch.dict(os.environ, {"WORKBUDDY_SIGNIN_LOG": "D:/tmp/x.log"}, clear=False):
            self.assertEqual(signin_runner.log_path(), "D:/tmp/x.log")


if __name__ == "__main__":
    unittest.main()
