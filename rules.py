#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules.py —— 可自定义的提醒规则。

把原本写死在 health.py / notify.py 里的阈值与判定，抽成一份可配置规则，
让"什么时候该提醒我"由用户自己定，而不是只有一套写死的 7/3 天。

规则分三类：
- credential：凭据到期预警的 warn / danger 天数（默认 7 / 3）。一旦低于 danger
  天，客户端再也续不上 refreshToken，之后每次签到必 AUTH_ERROR。
- streak：连签目标天数。成功签到后，若当前连签低于目标，推一句"再加把劲"，
  把"连续签到"从被动记录变成有人推着走的目标感。
- credit_drop：积分下滑告警。今日积分较近期均值下滑超过阈值时提醒，
  多半是漏签了某个项目（只拿了基础分没拿满奖励）。

配置落盘 rules.json（经 store.py 原子写，损坏不影响主流程）。读取失败一律回退
默认，绝不抛异常影响签到 / 推送。
"""
import os

import store

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "rules.json")

DEFAULT_WARN = 7
DEFAULT_DANGER = 3

DEFAULT_RULES = {
    "_schema": 1,
    "credential": {"warn_days": DEFAULT_WARN, "danger_days": DEFAULT_DANGER},
    "streak": {"enabled": True, "target_days": 30},
    "credit_drop": {"enabled": True, "pct": 30, "abs": 30},
}


def _deep_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_rules():
    """返回合并默认值后的规则 dict；文件缺失/损坏时给默认。"""
    try:
        data, _ = store.read_json_ex(RULES_PATH, None)
    except Exception:
        data = None
    if not isinstance(data, dict) or not data:
        return _deep_merge(DEFAULT_RULES, {})
    return _deep_merge(DEFAULT_RULES, data)


def save_rules(updates):
    """合并保存；数值做规范化（danger 不超过 warn、天数/百分比下限保护）。"""
    cur = load_rules()
    merged = _deep_merge(cur, updates or {})
    try:
        c = merged["credential"]
        warn = max(1, int(c.get("warn_days", DEFAULT_WARN) or DEFAULT_WARN))
        danger = int(c.get("danger_days", DEFAULT_DANGER) or DEFAULT_DANGER)
        danger = max(1, min(danger, warn))          # danger 必须 ≤ warn
        c["warn_days"], c["danger_days"] = warn, danger

        s = merged["streak"]
        s["enabled"] = bool(s.get("enabled", True))
        s["target_days"] = max(1, int(s.get("target_days", 30) or 30))

        d = merged["credit_drop"]
        d["enabled"] = bool(d.get("enabled", True))
        d["pct"] = max(0, int(d.get("pct", 30) or 0))
        d["abs"] = max(0, int(d.get("abs", 30) or 0))
    except Exception:
        pass
    try:
        merged["_schema"] = 1
        store.atomic_write_json(RULES_PATH, merged, indent=2)
    except Exception:
        pass
    return merged


def cred_warn_days():
    try:
        return max(1, int(load_rules()["credential"]["warn_days"]))
    except Exception:
        return DEFAULT_WARN


def cred_danger_days():
    try:
        return max(1, min(cred_warn_days(), int(load_rules()["credential"]["danger_days"])))
    except Exception:
        return DEFAULT_DANGER


def streak_hint(payload):
    """连签目标提醒：成功签到后，若连签低于目标天数，返回一句提醒（否则空串）。"""
    try:
        rules = load_rules()["streak"]
        if not rules.get("enabled", True):
            return ""
        target = int(rules.get("target_days", 30) or 30)
        data = (payload or {}).get("data") or {}
        streak = data.get("streak_days")
        if streak is None:
            return ""
        streak = int(streak)
        if streak < target:
            return "连签 %d 天，距目标 %d 天还差 %d 天，保持住" % (streak, target, target - streak)
    except Exception:
        return ""
    return ""


def _recent_avg_credit(n=8):
    """近期（不含今天）每日积分均值；样本不足返回 None。"""
    try:
        import history
        rows = history.series(limit=n + 1)
        if not rows:
            return None
        prev = [float(x.get("credit") or 0) for x in rows[:-1]
                if x.get("credit") is not None and x.get("credit") != 0]
        if len(prev) < 3:
            return None
        return sum(prev) / len(prev)
    except Exception:
        return None


def credit_drop_hint(payload):
    """积分下滑告警：今日积分较近期均值下滑超阈值时提醒（可能漏签某项）。仅成功时有效。"""
    try:
        rules = load_rules()["credit_drop"]
        if not rules.get("enabled", True):
            return ""
        data = (payload or {}).get("data") or {}
        today = data.get("today_credit")
        if today is None:
            return ""
        today = float(today)
        avg = _recent_avg_credit()
        if avg is None or avg <= 0:
            return ""
        drop_pct = (avg - today) / avg * 100.0
        drop_abs = avg - today
        need_pct = int(rules.get("pct", 30) or 0)
        need_abs = int(rules.get("abs", 30) or 0)
        if drop_pct >= need_pct and drop_abs >= need_abs:
            return "今日积分 +%d，较近期均值（约 %d）下滑 %d%%，可能漏签了某个项目" % (
                int(today), int(avg), int(drop_pct))
    except Exception:
        return ""
    return ""


if __name__ == "__main__":
    import json
    print(json.dumps(load_rules(), ensure_ascii=False, indent=2))
