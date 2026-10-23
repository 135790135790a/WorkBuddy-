#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify.py —— 签到结果推送（Webhook / 邮件）。

支持渠道：企业微信机器人 / 钉钉机器人 / 飞书机器人 / Bark(iOS) / Telegram / 通用 Webhook / 邮件(SMTP)。

设计要点：
- 所有网络/配置异常都被吞掉，绝不影响签到主流程。
- push() 做「智能决策」：成功且有实质收益才发摘要；"今日已签"不刷屏；
  失败按连续失败次数达到阈值才告警（与 signin_runner 的 needs_alert 呼应）。

配置文件：notify_config.json     状态文件：notify_state.json
"""
import base64
import copy
import datetime
import hashlib
import hmac
import json
import os
import smtplib
import ssl
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage

import store

try:
    import win32crypt  # pywin32: Windows DPAPI
except Exception:  # 非 Windows 或未安装 pywin32 时降级为明文
    win32crypt = None

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "notify_config.json")
STATE = os.path.join(HERE, "notify_state.json")

DEFAULT_CONFIG = {
    "enabled": False,
    "channel": "generic",          # generic|wecom|dingtalk|feishu|bark|telegram|email
    "webhook_url": "",
    "secret": "",                  # 钉钉加签密钥 / Telegram bot token 等
    "telegram_chat_id": "",
    "email": {"host": "", "port": 465, "ssl": True, "user": "", "password": "", "to": ""},
    "push_on_success": True,       # 有实质收益时发摘要
    "push_on_failure": True,       # 失败告警
    "daily_summary": False,        # 每天最多一条（即便"今日已签"）
    "failure_threshold": 1,        # 连续失败达到 N 次才推送
    "timeout": 10,
}

_SECRET_KEYS = ("webhook_url", "secret")

# Webhook 域名 → 渠道。用户只需要粘一个地址，渠道自动认。
CHANNEL_HOSTS = (
    ("qyapi.weixin.qq.com", "wecom"),
    ("oapi.dingtalk.com", "dingtalk"),
    ("open.feishu.cn", "feishu"),
    ("open.larksuite.com", "feishu"),
    ("api.day.app", "bark"),
    ("api.telegram.org", "telegram"),
)


def detect_channel(url):
    """从 Webhook 地址猜渠道；认不出就当通用 Webhook。"""
    try:
        host = (urllib.parse.urlsplit(str(url or "").strip()).hostname or "").lower()
    except Exception:
        return "generic"
    if not host:
        return "generic"
    for suffix, channel in CHANNEL_HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return channel
    return "generic"


# ---------------- 密钥加密（Windows DPAPI）----------------
def _enc_secret(val):
    if not val or not isinstance(val, str):
        return val
    if val.startswith("dpapi:") or win32crypt is None:
        return val
    try:
        blob = win32crypt.CryptProtectData(val.encode("utf-8"), "WorkBuddy notify",
                                           None, None, None, 0)
        return "dpapi:" + base64.b64encode(blob).decode("ascii")
    except Exception:
        return val


def _dec_secret(val):
    if not val or not isinstance(val, str):
        return val
    if not val.startswith("dpapi:"):
        return val  # 旧明文，兼容
    if win32crypt is None:
        return ""
    try:
        blob = base64.b64decode(val[6:])
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode("utf-8")
    except Exception:
        return ""  # 换了用户/机器解密失败：视为未配置


def _decrypt_config(data):
    if not isinstance(data, dict):
        return data
    for k in _SECRET_KEYS:
        if k in data:
            data[k] = _dec_secret(data[k])
    em = data.get("email")
    if isinstance(em, dict) and "password" in em:
        em["password"] = _dec_secret(em["password"])
    return data


def _encrypt_config(data):
    out = copy.deepcopy(data)
    for k in _SECRET_KEYS:
        if k in out:
            out[k] = _enc_secret(out[k])
    em = out.get("email")
    if isinstance(em, dict) and "password" in em:
        em["password"] = _enc_secret(em["password"])
    return out


# ---------------- 配置 / 状态 ----------------
def _deep_merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


_LEGACY_KEYS = {           # 旧字段名 → 新字段名（schema v3 迁移）
    "webhook": "webhook_url",
    "provider": "channel",
    "token": "secret",
    "chat_id": "telegram_chat_id",
}


def _migrate_config(data):
    """兼容历史结构：旧键名重命名、去掉 _schema；保证 email 是 dict。"""
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if k == "_schema":
            continue
        out[_LEGACY_KEYS.get(k, k)] = v
    if not isinstance(out.get("email"), dict):
        out["email"] = {}
    return out


def load_config():
    data, _ = store.read_json_ex(CONFIG, {}, migrate=_migrate_config)
    if not isinstance(data, dict) or not data:
        return copy.deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, _decrypt_config(data))


def save_config(updates):
    """合并保存。密钥类字段传空串表示「保持不变」。"""
    cur = load_config()
    up = dict(updates or {})

    def _is_mask(v):
        return isinstance(v, str) and (v.startswith("…") or "•" in v)

    for k in _SECRET_KEYS:
        if k in up and (up[k] is None or up[k] == "" or _is_mask(up[k])):
            up.pop(k, None)
    em = up.get("email")
    if isinstance(em, dict):
        em = dict(em)
        if em.get("password") is None or em.get("password") == "" or _is_mask(em.get("password")):
            em.pop("password", None)
        up["email"] = em
    merged = _deep_merge(cur, up)
    try:
        # 落盘的是加密副本；返回内存中的明文（供本次发送使用）
        disk = _encrypt_config(merged)
        disk["_schema"] = store.SCHEMA_NOTIFY
        store.atomic_write_json(CONFIG, disk, indent=2)
    except Exception:
        pass
    return merged


def _mask(s, keep=4):
    s = str(s or "")
    if not s:
        return ""
    if len(s) <= keep:
        return "•" * len(s)
    return "…" + s[-keep:]


def public_config(cfg=None):
    """给前端用的脱敏配置。"""
    cfg = cfg or load_config()
    out = copy.deepcopy(cfg)
    out["webhook_url"] = _mask(cfg.get("webhook_url"))
    out["secret"] = _mask(cfg.get("secret"))
    out["email"] = dict(cfg.get("email") or {})
    out["email"]["password"] = _mask((cfg.get("email") or {}).get("password"))
    out["_has"] = {
        "webhook_url": bool(cfg.get("webhook_url")),
        "secret": bool(cfg.get("secret")),
        "password": bool((cfg.get("email") or {}).get("password")),
    }
    return out


def load_state():
    data, _ = store.read_json_ex(STATE, {})
    return data if isinstance(data, dict) else {}


def save_state(state):
    return store.atomic_write_json(STATE, state, indent=0)


# ---------------- 发送 ----------------
def _post_json(url, obj, timeout=10, headers=None):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(2000).decode("utf-8", "replace")


def _get(url, timeout=10):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(2000).decode("utf-8", "replace")


def _dingtalk_sign(url, secret):
    ts = str(round(time.time() * 1000))
    string_to_sign = "%s\n%s" % (ts, secret)
    code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                    hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(code))
    sep = "&" if ("?" in url) else "?"
    return "%s%stimestamp=%s&sign=%s" % (url, sep, ts, sign)


def _send_email(cfg, subject, body):
    em = cfg.get("email") or {}
    host = em.get("host")
    if not host or not em.get("to"):
        raise ValueError("邮件配置不完整（host/to 为空）")
    port = int(em.get("port") or 465)
    timeout = int(cfg.get("timeout") or 10)
    msg = EmailMessage()
    msg["From"] = em.get("user") or em.get("to")
    msg["To"] = em.get("to")
    msg["Subject"] = subject
    msg.set_content(body)
    if em.get("ssl", True):
        with smtplib.SMTP_SSL(host, port, timeout=timeout,
                              context=ssl.create_default_context()) as s:
            if em.get("user"):
                s.login(em["user"], em.get("password") or "")
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as s:
            s.ehlo()
            try:
                s.starttls(context=ssl.create_default_context())
            except Exception:
                pass
            if em.get("user"):
                s.login(em["user"], em.get("password") or "")
            s.send_message(msg)


def _dispatch(cfg, title, body):
    """按渠道发送一条消息，返回 {channel, ok, error?}。绝不抛异常。"""
    channel = (cfg.get("channel") or "generic").lower()
    text = "%s\n%s" % (title, body)
    url = (cfg.get("webhook_url") or "").strip()
    secret = (cfg.get("secret") or "").strip()
    timeout = int(cfg.get("timeout") or 10)
    try:
        if channel == "email":
            _send_email(cfg, title, body)
        elif channel == "wecom":
            if not url:
                raise ValueError("缺少企业微信机器人 Webhook")
            _post_json(url, {"msgtype": "text", "text": {"content": text}}, timeout)
        elif channel == "dingtalk":
            if not url:
                raise ValueError("缺少钉钉机器人 Webhook")
            if secret:
                url = _dingtalk_sign(url, secret)
            _post_json(url, {"msgtype": "text", "text": {"content": text}}, timeout)
        elif channel == "feishu":
            if not url:
                raise ValueError("缺少飞书机器人 Webhook")
            _post_json(url, {"msg_type": "text", "content": {"text": text}}, timeout)
        elif channel == "bark":
            if not url:
                raise ValueError("缺少 Bark 地址")
            base = url.rstrip("/")
            _get("%s/%s/%s" % (base, urllib.parse.quote(title), urllib.parse.quote(body)),
                 timeout)
        elif channel == "telegram":
            bot = secret or url  # 允许把 token 填在 secret 或 url
            chat = (cfg.get("telegram_chat_id") or "").strip()
            if not bot or not chat:
                raise ValueError("Telegram 需要 bot token 与 chat_id")
            _post_json("https://api.telegram.org/bot%s/sendMessage" % bot,
                       {"chat_id": chat, "text": text, "disable_web_page_preview": True},
                       timeout)
        else:  # generic
            if not url:
                raise ValueError("缺少 Webhook 地址")
            _post_json(url, {"title": title, "text": text, "content": text, "msg": text},
                       timeout)
        return {"channel": channel, "ok": True}
    except Exception as e:
        return {"channel": channel, "ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# ---------------- 消息内容 ----------------
def format_message(payload, mode="auto"):
    data = (payload or {}).get("data") or {}
    ok = bool((payload or {}).get("ok"))
    result = data.get("result")
    rep = (data.get("report") or "").strip()
    streak = data.get("streak_days")
    total = data.get("total_credits")
    today = data.get("today_credit")
    extra = []
    if streak is not None:
        extra.append("连续 %s 天" % streak)
    if today is not None:
        extra.append("今日 +%s" % today)
    if total is not None:
        extra.append("累计 %s" % total)
    meta = " · ".join(extra)

    if ok:
        title = "✅ WorkBuddy 签到成功"
        body = rep or "签到完成"
        if meta:
            body = body + "\n" + meta
    else:
        title = "❌ WorkBuddy 签到失败"
        raw = (payload or {}).get("stderr") or (payload or {}).get("raw") or ""
        body = rep or (raw[-300:] if raw else "未知错误")
        if result:
            body = "[%s] %s" % (result, body)
    body = ("%s\n模式：%s · %s" % (body, mode,
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    # 凭据快到期/已过期时顺手带一句提醒：这类"过几天才会爆"的问题，
    # 光看本次成功与否是发现不了的。算不出来（老客户端/文件被占用）就跳过。
    try:
        import health
        hint = health.one_line()
        if hint:
            body = body + "\n" + hint
    except Exception:
        pass
    # 自定义提醒规则（连签目标 / 积分下滑）：仅在成功签到时才有意义，
    # 失败当天没有新的积分/连签数据可比对。规则未启用时各评估器返回空串。
    if ok:
        try:
            import rules
            for fn in (rules.streak_hint, rules.credit_drop_hint):
                rh = fn(payload)
                if rh:
                    body = body + "\n" + rh
        except Exception:
            pass
    return title, body, ok, result


# ---------------- 对外入口 ----------------
def send_test(cfg=None):
    cfg = cfg or load_config()
    title = "🔔 WorkBuddy 推送测试"
    body = "这是一条测试消息。\n时间：%s\n渠道：%s" % (
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), cfg.get("channel"))
    return _dispatch(cfg, title, body)


def push_custom(title, body, force=False):
    """发送任意标题/正文（用于"接口失效"这类与单次结果无关的告警）。

    沿用同一份推送配置；未启用时静默跳过，任何异常都不外抛。
    """
    try:
        cfg = load_config()
        if not cfg.get("enabled") and not force:
            return {"skipped": "disabled"}
        if force:
            cfg = dict(cfg, enabled=True)
        return _dispatch(cfg, title, body)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def push(payload, mode="auto", force=False):
    """根据结果决定是否推送。返回决策信息；任何异常都不外抛。"""
    try:
        cfg = load_config()
        if not cfg.get("enabled") and not force:
            return {"skipped": "disabled"}
        if force:
            cfg = dict(cfg, enabled=True)
        data = (payload or {}).get("data") or {}
        ok = bool((payload or {}).get("ok"))
        result = data.get("result")
        title, body, _, _ = format_message(payload, mode)
        state = load_state()
        today = datetime.date.today().isoformat()

        if not ok:
            if not cfg.get("push_on_failure", True) and not force:
                return {"skipped": "failure-off"}
            cf = int(state.get("consecutive_failures", 0) or 0) + 1
            state["consecutive_failures"] = cf
            save_state(state)
            th = max(1, int(cfg.get("failure_threshold") or 1))
            if cf < th and not force:
                return {"skipped": "below-threshold", "consecutive_failures": cf}
            return {"ok": True, "consecutive_failures": cf, "sent": [_dispatch(cfg, title, body)]}

        # 成功
        cf_was = int(state.get("consecutive_failures", 0) or 0)
        state["consecutive_failures"] = 0
        save_state(state)

        noteworthy = result not in ("ALREADY", "INACTIVE", "SKIPPED")
        daily_due = bool(cfg.get("daily_summary")) and state.get("last_summary_date") != today
        if force:
            should = True
        elif noteworthy and cfg.get("push_on_success", True):
            should = True
        elif daily_due:
            should = True
        else:
            return {"skipped": "nothing-to-report", "result": result,
                    "recovered_from_failures": cf_was}
        res = _dispatch(cfg, title, body)
        if daily_due:
            state["last_summary_date"] = today
            save_state(state)
        out = {"ok": True, "sent": [res], "recovered_from_failures": cf_was}
        return out
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}


if __name__ == "__main__":
    print(json.dumps(public_config(), ensure_ascii=False, indent=2))
