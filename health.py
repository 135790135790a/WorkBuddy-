#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""health.py —— 登录凭据健康度：提前预警，而不是等失败了才自愈。

为什么需要它
------------
`authheal.py` 是"事后救火"：签到返回 AUTH_ERROR 时才去拉桌面端续期。
但凭据文件里真正的死线是 **refreshToken 的到期时间**（`refreshExpiresAt`）——
它一到点，客户端自己也续不上了，之后每次签到都必然 AUTH_ERROR，
半夜跑定时任务时没人点，当天就断签，第二天才发现。

所以这里只算一件事：refreshToken 还剩几天、健康度多少，然后用一句人话
告诉你"大约什么时候得手动登一次"。

安全性
------
只读 `expiresAt` / `refreshExpiresAt` / `lastRefreshTime` / `expiresIn` 这几个
**时间字段**；不读、不打印、不外传任何 token 内容（token 在文件里本身也是加密
信封）。文件被客户端占用读不出来时返回 unknown，不影响签到主流程。
"""
import datetime
import json
import os

DEFAULT_TOTAL_DAYS = 30      # refreshToken 默认有效期（refreshExpiresIn=2592000）
WARN_DAYS = 7                # ≤7 天：该找时间登一次了
DANGER_DAYS = 3              # ≤3 天：尽快，否则大概率断签
MAX_BYTES = 4 * 1024 * 1024


def auth_file():
    """定位凭据文件；复用 signin 的候选路径，避免两处路径漂移。"""
    try:
        import signin
        path, _ = signin.find_auth_file()
        return path
    except Exception:
        return None


def _ms(value):
    """时间戳统一成毫秒；秒/毫秒混用也能吃下，非法值返回 None。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n if n >= 10 ** 12 else n * 1000


def _seconds(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _fmt(ms):
    try:
        return datetime.datetime.fromtimestamp(ms / 1000.0).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def _read_json(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
    except OSError:
        return None
    for _ in range(2):          # 客户端刷新 token 时文件会被短暂占用，读两次
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return None


def cred_health(now=None):
    """返回凭据健康度。

    state: missing | unknown | expired | danger | warn | ok
    以 refreshToken 为主指标（accessToken 会被客户端自动续，通常不是瓶颈）。
    """
    now = now or datetime.datetime.now()
    path = auth_file()
    out = {
        "state": "missing",
        "has_file": bool(path),
        "pct": 0.0,                 # 剩余健康度 0~100
        "days_left": None,          # 距 refreshToken 到期的天数（可负）
        "hours_left": None,
        "total_days": DEFAULT_TOTAL_DAYS,
        "refresh_expires_at": "",
        "access_expires_at": "",
        "last_refresh": "",
    }
    if not path:
        return out

    data = _read_json(path)
    if not isinstance(data, dict):
        out["state"] = "unknown"
        return out

    auth = data.get("auth")
    if not isinstance(auth, dict):
        auth = {}

    refresh_exp = _ms(auth.get("refreshExpiresAt"))
    access_exp = _ms(auth.get("expiresAt"))
    last_refresh = _ms(auth.get("lastRefreshTime"))

    out["access_expires_at"] = _fmt(access_exp) if access_exp else ""
    out["last_refresh"] = _fmt(last_refresh) if last_refresh else ""

    total_sec = _seconds(auth.get("refreshExpiresIn")) or DEFAULT_TOTAL_DAYS * 86400
    out["total_days"] = int(round(total_sec / 86400))

    if not refresh_exp:
        # 老版本客户端可能没写 refreshExpiresAt：退回用 expiresAt，但不给倒计时结论
        out["state"] = "unknown" if not access_exp else "ok"
        if access_exp:
            out["refresh_expires_at"] = ""
            out["days_left"] = None
        return out

    out["refresh_expires_at"] = _fmt(refresh_exp)
    remain = refresh_exp / 1000.0 - now.timestamp()
    out["days_left"] = round(remain / 86400.0, 1)
    out["hours_left"] = int(remain // 3600)
    out["pct"] = round(max(0.0, min(1.0, remain / total_sec)) * 100, 1)

    if remain <= 0:
        out["state"] = "expired"
    elif remain / 86400.0 <= _danger_days():
        out["state"] = "danger"
    elif remain / 86400.0 <= _warn_days():
        out["state"] = "warn"
    else:
        out["state"] = "ok"
    return out


def _warn_days():
    """凭据预警阈值（天），默认 7；规则文件缺省/损坏时回退常量。"""
    try:
        import rules
        return rules.cred_warn_days()
    except Exception:
        return WARN_DAYS


def _danger_days():
    """凭据危险阈值（天），默认 3；必须 ≤ warn。"""
    try:
        import rules
        return rules.cred_danger_days()
    except Exception:
        return DANGER_DAYS


def needs_attention(h=None):
    """是否值得在推送里多提一句。"""
    h = h or cred_health()
    return h.get("state") in ("expired", "danger", "warn", "missing")


def one_line(h=None):
    """推送用的一句话（中文，够短）。不需要提醒时返回空串。"""
    h = h or cred_health()
    st = h.get("state")
    if st == "missing":
        return "登录凭据缺失，签到会失败——请打开桌面端登录一次。"
    if st == "expired":
        return "登录凭据已过期（%s），需重新登录，否则后续签到全部失败。" % (
            h.get("refresh_expires_at") or "时间未知")
    if st in ("danger", "warn"):
        d = h.get("days_left")
        if d is None:
            return ""
        return "登录凭据约 %s 天后到期（%s），建议近期打开桌面端续一次。" % (
            int(d) if d >= 1 else "不足 1", h.get("refresh_expires_at") or "")
    return ""


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(cred_health(), ensure_ascii=False, indent=2))
