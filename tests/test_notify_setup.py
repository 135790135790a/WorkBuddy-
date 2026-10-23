"""推送配置：地址→渠道识别，以及一条命令配置后的落盘/回读。

不发真实网络请求（send_test 由人工执行）；只验证配置链路。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import notify


class DetectChannelTests(unittest.TestCase):
    def test_known_hosts(self):
        cases = {
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc": "wecom",
            "https://oapi.dingtalk.com/robot/send?access_token=abc": "dingtalk",
            "https://open.feishu.cn/open-apis/bot/v2/hook/abc": "feishu",
            "https://open.larksuite.com/open-apis/bot/v2/hook/abc": "feishu",
            "https://api.day.app/abcdEFG": "bark",
            "https://api.telegram.org/bot123:abc/sendMessage": "telegram",
        }
        for url, want in cases.items():
            self.assertEqual(notify.detect_channel(url), want, url)

    def test_unknown_and_broken(self):
        self.assertEqual(notify.detect_channel("https://example.com/hook"), "generic")
        self.assertEqual(notify.detect_channel(""), "generic")
        self.assertEqual(notify.detect_channel(None), "generic")
        self.assertEqual(notify.detect_channel("not a url"), "generic")

    def test_lookalike_host_is_generic(self):
        self.assertEqual(notify.detect_channel("https://qyapi.weixin.qq.com.evil.io/x"), "generic")


class ConfigRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "notify_config.json"
        p = patch.object(notify, "CONFIG", str(self.path))
        p.start()
        self.addCleanup(p.stop)

    def test_save_enables_and_round_trips(self):
        url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret-key"
        notify.save_config({"enabled": True, "channel": "wecom", "webhook_url": url})
        cfg = notify.load_config()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["channel"], "wecom")
        self.assertEqual(cfg["webhook_url"], url)

    def test_public_config_masks_url(self):
        url = "https://api.day.app/abcdefghijk"
        notify.save_config({"enabled": True, "channel": "bark", "webhook_url": url})
        shown = notify.public_config()
        self.assertNotEqual(shown["webhook_url"], url)
        self.assertIn("…", shown["webhook_url"])

    def test_disable_keeps_channel(self):
        notify.save_config({"enabled": True, "channel": "feishu",
                            "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/x"})
        notify.save_config({"enabled": False})
        cfg = notify.load_config()
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["channel"], "feishu")

    def test_file_is_not_plaintext_json_secret(self):
        url = "https://oapi.dingtalk.com/robot/send?access_token=tok"
        notify.save_config({"enabled": True, "channel": "dingtalk", "webhook_url": url})
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("access_token=tok", raw)   # DPAPI 加密后不应出现明文


if __name__ == "__main__":
    unittest.main()
