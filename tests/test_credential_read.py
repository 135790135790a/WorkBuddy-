"""凭据读取的重试策略：共享读、退避、以及"占用 vs 无权限"的区分。

这一组针对真实故障现场：客户端刷新 token 时独占 workbuddy-desktop.info，
旧实现只等 3×2 秒，锁稍长一点就整天断签，而且报错让人误以为是登录失效。
"""
import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import signin

SESSION = {"auth": {"accessToken": "token-abc", "endpoint": "https://copilot.tencent.com",
                    "domain": "www.workbuddy.cn"},
           "account": {"uid": "u", "enterpriseId": "e"}}


def busy(code):
    return signin._FileBusy(r"C:\fake\info", code)


class ReadSharedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_reads_real_file(self):
        p = Path(self.temp.name) / "session.json"
        p.write_text(json.dumps(SESSION), encoding="utf-8")
        self.assertEqual(json.loads(signin._read_shared(str(p)).decode("utf-8")), SESSION)

    def test_missing_file_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            signin._read_shared(str(Path(self.temp.name) / "nope.json"))

    def test_file_busy_keeps_winerror(self):
        e = busy(32)
        self.assertEqual(e.winerror, 32)
        self.assertEqual(e.errno, errno.EACCES)


class LoadSessionRetryTests(unittest.TestCase):
    def setUp(self):
        signin._start_budget()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "session.json")

    def call(self, side_effect, delays=(0.0, 0.0, 0.0)):
        # bytes 会被 mock 当成可迭代对象逐字节返回，这里统一包成序列；
        # 函数/异常照原样交给 mock（前者被调用，后者被抛出）
        if not isinstance(side_effect, (list, BaseException)) and not callable(side_effect):
            side_effect = [side_effect]
        with patch.object(signin, "_read_shared", side_effect=side_effect), \
                patch.object(signin.time, "sleep") as slept:
            try:
                return signin.load_session_retry(self.path, delays=delays), slept
            except Exception as e:
                return e, slept

    def test_ok_first_try(self):
        got, slept = self.call([json.dumps(SESSION).encode()])
        self.assertEqual(got, SESSION)
        self.assertEqual(slept.call_count, 0)

    def test_retries_through_sharing_violation(self):
        got, _ = self.call([busy(32), busy(32), json.dumps(SESSION).encode()])
        self.assertEqual(got, SESSION)

    def test_retries_through_lock_violation(self):
        got, _ = self.call([busy(33), json.dumps(SESSION).encode()])
        self.assertEqual(got, SESSION)

    def test_exhausted_is_auth_busy(self):
        err, _ = self.call([busy(32)] * 5)
        self.assertIsInstance(err, signin.AuthError)
        self.assertEqual(err.result, "AUTH_BUSY")
        self.assertIn("占用", str(err))
        self.assertIn("无需重新登录", str(err))

    def test_access_denied_gives_up_after_a_few_tries(self):
        # Win32 5 可能是"先删后建"的瞬时窗口，给几次机会；永久拒绝才收手
        calls = {"n": 0}

        def deny(_path):
            calls["n"] += 1
            raise busy(5)

        err, _ = self.call(deny, delays=(0.0,) * 8)
        self.assertIsInstance(err, signin.AuthError)
        self.assertEqual(err.result, "AUTH_BUSY")
        self.assertEqual(err.reason, "CREDENTIAL_DENIED")
        self.assertEqual(calls["n"], signin._DENIED_MAX)

    def test_access_denied_recovers_within_window(self):
        # 第 3 次读到就好：说明这确实是瞬时窗口，不该提前放弃
        got, _ = self.call([busy(5), busy(5), json.dumps(SESSION).encode()],
                           delays=(0.0, 0.0, 0.0))
        self.assertEqual(got, SESSION)

    def test_unexpected_oserror_propagates(self):
        err, _ = self.call(FileNotFoundError(errno.ENOENT, "missing", self.path))
        self.assertIsInstance(err, FileNotFoundError)

    def test_corrupt_json_propagates_immediately(self):
        err, slept = self.call(b"{ broken json")
        self.assertIsInstance(err, json.JSONDecodeError)
        self.assertEqual(slept.call_count, 0)

    def test_budget_exhausted_stops_retrying(self):
        with patch.object(signin, "_budget_left", return_value=0):
            err, slept = self.call([busy(32)] * 5)
        self.assertIsInstance(err, signin.AuthError)
        self.assertEqual(err.result, "AUTH_BUSY")
        self.assertEqual(slept.call_count, 0)

    def test_depth_of_retry_defaults(self):
        self.assertGreaterEqual(len(signin.READ_RETRY_DELAYS), 6)
        self.assertGreater(sum(signin.READ_RETRY_DELAYS), 25)


class RunWiringTests(unittest.TestCase):
    """_run 必须把 AUTH_BUSY 原样报出来，不能被兜底成 ERROR + "请重新登录"。"""

    def test_auth_busy_keeps_its_own_result(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "session.json"
        path.write_text("{}", encoding="utf-8")
        captured = {}

        def fake_emit(out, action):
            captured.update(out)

        with patch.object(signin, "find_auth_file", return_value=(str(path), [str(path)])), \
                patch.object(signin, "load_session_retry",
                             side_effect=signin.AuthError("CREDENTIAL_BUSY", "AUTH_BUSY")), \
                patch.object(signin, "emit", side_effect=fake_emit):
            rc = signin._run("silent")
        self.assertEqual(rc, 2)
        self.assertEqual(captured["result"], "AUTH_BUSY")
        self.assertIn("无需重新登录", captured["report"])
        self.assertNotIn("请重新登录", captured["report"])


class ClassifyTests(unittest.TestCase):
    def test_auth_busy_is_retryable(self):
        import signin_runner
        self.assertIn("AUTH_BUSY", signin_runner.RETRYABLE_RESULTS)
        self.assertNotIn("AUTH_BUSY", signin_runner.NO_RETRY_RESULTS)
        kind, result = signin_runner.classify({"result": "AUTH_BUSY"}, 1)
        self.assertEqual((kind, result), ("retry", "AUTH_BUSY"))

    def test_auth_busy_is_known_to_monitor(self):
        import monitor
        self.assertIn("AUTH_BUSY", monitor.KNOWN_RESULTS)
        self.assertNotIn("AUTH_BUSY", monitor.OK_RESULTS)


if __name__ == "__main__":
    unittest.main()
