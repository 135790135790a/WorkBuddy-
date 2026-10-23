#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify-setup.py —— 一条命令把推送配好（并当场发一条测试）。

为什么单独做这个：
    notify.py 本身有 7 个渠道、DPAPI 加密、失败静默等一堆逻辑，但配置全靠手改
    JSON。结果就是"监控写完了、出口一直关着"——心跳告警发不出去也没人发现。
    这里把它压成：粘一个 Webhook 地址进来，自动认渠道、写配置、发测试。

用法：
    python notify-setup.py https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
    python notify-setup.py https://oapi.dingtalk.com/robot/send?access_token=xxx --secret SEC...
    python notify-setup.py https://api.day.app/xxxxxxxx
    python notify-setup.py --telegram-token 123:abc --chat-id 456
    python notify-setup.py --email smtp.qq.com:465 --user me@qq.com --password *** --to me@qq.com
    python notify-setup.py --test      # 只发一条测试
    python notify-setup.py --show      # 看当前配置（脱敏）
    python notify-setup.py --off       # 关掉推送

退出码：配置并发送成功 0，否则 1。
"""
import sys

import notify


def _say(msg):
    try:
        print(msg)
    except Exception:
        pass


def _send_test():
    cfg = notify.load_config()
    if not cfg.get("enabled"):
        _say("推送还没启用，先给一个地址（见 --help）")
        return False
    res = notify.send_test(cfg)
    ok = bool(isinstance(res, dict) and res.get("ok"))
    _say("测试推送：%s" % ("成功 ✓" if ok else "失败 ✗ %r" % (res,)))
    return ok


def _show():
    cfg = notify.public_config()
    _say("enabled   : %s" % cfg.get("enabled"))
    _say("channel   : %s" % cfg.get("channel"))
    _say("webhook   : %s" % (cfg.get("webhook_url") or "（未配置）"))
    _say("secret    : %s" % (cfg.get("secret") or "（未配置）"))
    email = cfg.get("email") or {}
    _say("email     : %s" % ("%s:%s → %s" % (email.get("host"), email.get("port"),
                                             email.get("to")) if email.get("host") else "（未配置）"))
    _say("推送策略  : 成功=%s 失败=%s 每日摘要=%s 失败阈值=%s" % (
        cfg.get("push_on_success"), cfg.get("push_on_failure"),
        cfg.get("daily_summary"), cfg.get("failure_threshold")))
    return True


def _parse(argv):
    if not argv or argv[0] in ("-h", "--help"):
        _say(__doc__)
        return None
    opts = {"url": "", "secret": "", "telegram": "", "chat": "",
            "email": "", "user": "", "password": "", "to": "", "action": ""}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--test", "--show", "--off"):
            opts["action"] = a.strip("-")
            i += 1
            continue
        if a.startswith("--"):
            key = a[2:]
            val = argv[i + 1] if i + 1 < len(argv) else ""
            if key in ("telegram-token",):
                opts["telegram"] = val
            elif key in ("chat-id",):
                opts["chat"] = val
            elif key in ("user", "password", "to", "secret", "email"):
                opts[key] = val
            else:
                _say("未知参数：%s" % a)
                return None
            i += 2
            continue
        opts["url"] = a
        i += 1
    return opts


def main():
    opts = _parse(sys.argv[1:])
    if opts is None:
        return 1
    if opts["action"] == "show":
        return 0 if _show() else 1
    if opts["action"] == "off":
        notify.save_config({"enabled": False})
        _say("已关闭推送。")
        return 0
    if opts["action"] == "test":
        return 0 if _send_test() else 1

    if opts["telegram"]:
        if not (opts["chat"] or notify.load_config().get("telegram_chat_id")):
            _say("Telegram 还需要 --chat-id")
            return 1
        notify.save_config({"enabled": True, "channel": "telegram",
                            "webhook_url": "https://api.telegram.org",
                            "secret": opts["telegram"],
                            "telegram_chat_id": opts["chat"]})
    elif opts["email"]:
        host, _, port = opts["email"].partition(":")
        cur = notify.load_config().get("email") or {}
        notify.save_config({"enabled": True, "channel": "email", "email": {
            "host": host, "port": int(port or 465), "ssl": True,
            "user": opts["user"] or cur.get("user"),
            "password": opts["password"],
            "to": opts["to"] or cur.get("to"),
        }})
    elif opts["url"]:
        channel = notify.detect_channel(opts["url"])
        updates = {"enabled": True, "channel": channel, "webhook_url": opts["url"]}
        if opts["secret"]:
            updates["secret"] = opts["secret"]
        if channel == "telegram":
            _say("Telegram 请用 --telegram-token 与 --chat-id")
            return 1
        notify.save_config(updates)
        _say("渠道识别为：%s" % channel)
    else:
        _say("没给地址。用法见 --help")
        return 1

    _say("配置已写入 notify_config.json（密钥已加密）。")
    return 0 if _send_test() else 1


if __name__ == "__main__":
    sys.exit(main())
