"""authheal.py 的单测：判定条件、指纹比较、以及"客户端状态"各分支。

全部用假 launcher / 假 sleeper，不会真的启动客户端，也不访问网络。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import authheal


def write_session(path, token):
    path.write_text(json.dumps({"auth": {"accessToken": token},
                                "account": {"uid": "u"}}), encoding="utf-8")


class AuthHealTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = Path(self.temp.name) / "session.json"
        write_session(self.auth, "old-token")
        env = patch.dict(os.environ, {"WORKBUDDY_AUTH_HEAL": "1"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("WORKBUDDY_HEAL_WAIT", None)
        self.running = patch.object(authheal, "is_running", return_value=False)
        self.running.start()
        self.addCleanup(self.running.stop)

    def test_should_heal_matrix(self):
        self.assertTrue(authheal.should_heal("AUTH_ERROR"))
        self.assertTrue(authheal.should_heal("AUTH_REJECTED"))
        for other in ("NO_AUTH", "NETWORK", "TIMEOUT", "FORBIDDEN", "", None):
            self.assertFalse(authheal.should_heal(other), other)

    def test_disabled_by_env(self):
        os.environ["WORKBUDDY_AUTH_HEAL"] = "0"
        self.assertFalse(authheal.should_heal("AUTH_ERROR"))
        out = authheal.heal(auth_file=str(self.auth), exe="C:\\x\\WorkBuddy.exe")
        self.assertFalse(out["attempted"])
        self.assertEqual(out["reason"], "disabled")
        self.assertFalse(out["launched"])

    def test_wait_seconds_clamped(self):
        self.assertEqual(authheal.wait_seconds(), authheal.DEFAULT_WAIT)
        os.environ["WORKBUDDY_HEAL_WAIT"] = "1"
        self.assertEqual(authheal.wait_seconds(), 5)
        os.environ["WORKBUDDY_HEAL_WAIT"] = "9999"
        self.assertEqual(authheal.wait_seconds(), 300)
        os.environ["WORKBUDDY_HEAL_WAIT"] = "abc"
        self.assertEqual(authheal.wait_seconds(), authheal.DEFAULT_WAIT)

    def test_fingerprint(self):
        fp = authheal.fingerprint(str(self.auth))
        self.assertEqual(len(fp), 12)
        self.assertEqual(fp, authheal.fingerprint(str(self.auth)))   # 同 token 稳定
        write_session(self.auth, "new-token")
        self.assertNotEqual(fp, authheal.fingerprint(str(self.auth)))  # 换 token 就变
        self.assertIsNone(authheal.fingerprint(str(Path(self.temp.name) / "nope")))
        bad = Path(self.temp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertIsNone(authheal.fingerprint(str(bad)))
        empty = Path(self.temp.name) / "empty.json"
        empty.write_text(json.dumps({"auth": {}}), encoding="utf-8")
        self.assertIsNone(authheal.fingerprint(str(empty)))

    def test_no_auth_file(self):
        out = authheal.heal(auth_file=str(Path(self.temp.name) / "missing.json"),
                            exe="C:\\x\\WorkBuddy.exe")
        self.assertEqual(out["reason"], "no-auth-file")
        self.assertFalse(out["launched"])

    def test_unreadable_credential(self):
        bad = Path(self.temp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        out = authheal.heal(auth_file=str(bad), exe="C:\\x\\WorkBuddy.exe")
        self.assertEqual(out["reason"], "unreadable-credential")

    def test_client_already_running(self):
        with patch.object(authheal, "is_running", return_value=True):
            out = authheal.heal(auth_file=str(self.auth), exe="C:\\x\\WorkBuddy.exe",
                                launcher=lambda exe: None, sleeper=lambda s: None)
        self.assertEqual(out["reason"], "client-already-running")
        self.assertFalse(out["launched"])

    def test_runtime_not_found(self):
        with patch.object(authheal, "_exe", side_effect=RuntimeError("nope")):
            out = authheal.heal(auth_file=str(self.auth), launcher=lambda exe: None,
                                sleeper=lambda s: None)
        self.assertEqual(out["reason"], "runtime-not-found")
        self.assertFalse(out["launched"])

    def test_launch_failed(self):
        def boom(_exe):
            raise OSError("denied")
        out = authheal.heal(auth_file=str(self.auth), exe="C:\\x\\WorkBuddy.exe",
                            launcher=boom, sleeper=lambda s: None)
        self.assertEqual(out["reason"], "launch-failed")
        self.assertFalse(out["launched"])

    def test_refreshed_when_token_changes(self):
        calls = {"n": 0}

        def sleeper(_sec):
            calls["n"] += 1
            if calls["n"] == 1:
                write_session(self.auth, "fresh-token")

        out = authheal.heal(auth_file=str(self.auth), exe="C:\\x\\WorkBuddy.exe",
                            wait=9, launcher=lambda exe: None,
                            sleeper=sleeper, poll=3)
        self.assertTrue(out["launched"])
        self.assertTrue(out["refreshed"])
        self.assertEqual(out["reason"], "credential-refreshed")
        self.assertAlmostEqual(out["waited"], 3.0, places=1)

    def test_no_refresh_times_out(self):
        out = authheal.heal(auth_file=str(self.auth), exe="C:\\x\\WorkBuddy.exe",
                            wait=6, launcher=lambda exe: None,
                            sleeper=lambda s: None, poll=3)
        self.assertTrue(out["launched"])
        self.assertFalse(out["refreshed"])
        self.assertEqual(out["reason"], "no-refresh")
        self.assertAlmostEqual(out["waited"], 6.0, places=1)

    def test_zero_wait_does_not_loop(self):
        out = authheal.heal(auth_file=str(self.auth), exe="C:\\x\\WorkBuddy.exe",
                            wait=0, launcher=lambda exe: None,
                            sleeper=lambda s: None, poll=3)
        self.assertEqual(out["reason"], "no-refresh")
        self.assertEqual(out["waited"], 0.0)


if __name__ == "__main__":
    unittest.main()
