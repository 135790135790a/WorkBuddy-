#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""history.py —— 统一的签到历史存取。

`signin_history.json`：
    {"_schema": 2, "updated": "...", "days": {"2026-10-18": {credit, streak, total, result, report}}}

make-report.py（快捷方式）、notify-run.py（定时任务）、tray.py（托盘立即签到）
都调用 record()，避免"只有手动打开报告才记历史、定时任务不记"的缺口。

写盘走 store.py：原子替换 + 损坏自动隔离，不会因为写一半崩溃而丢掉全部历史。
旧格式（顶层直接是 date -> {...} 的扁平字典）读取时自动升级为 v2。
"""
import datetime
import os

import store

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "signin_history.json")

_SIGNED_RESULTS = ("GROWTH", "ALREADY", "CLAIMED")
_MAX_DAYS = 400

_last_status = "missing"  # 供体检/排查看最近一次读取状态


def _migrate(data):
    """兼容旧格式：扁平 date->obj → {_schema, days}。"""
    if not isinstance(data, dict):
        return {"_schema": store.SCHEMA_HISTORY, "days": {}}
    if isinstance(data.get("days"), dict):
        return data
    # 旧格式：所有 key 都是日期
    days = {k: v for k, v in data.items()
            if isinstance(k, str) and len(k) == 10 and k[4] == "-" and isinstance(v, dict)}
    return {"_schema": store.SCHEMA_HISTORY, "days": days}


def load_store_ex():
    """返回 (days, status)。status: ok/missing/corrupt/error。"""
    global _last_status
    raw, status = store.read_json_ex(STORE, {"_schema": store.SCHEMA_HISTORY, "days": {}},
                                     migrate=_migrate)
    _last_status = status
    days = (raw or {}).get("days")
    if not isinstance(days, dict):
        days = {}
    return days, status


def load_store():
    """返回 date -> {credit, streak, total, result, report}。"""
    return load_store_ex()[0]


def save_store(days):
    """原子写入；附带 schema 与更新时间。"""
    payload = {
        "_schema": store.SCHEMA_HISTORY,
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
    }
    return store.atomic_write_json(STORE, payload, indent=0)


def record(payload):
    """成功签到（GROWTH/ALREADY/CLAIMED）后把当天记入历史。返回是否写入。"""
    data = (payload or {}).get("data") or {}
    if not (payload or {}).get("ok"):
        return False
    if data.get("result") not in _SIGNED_RESULTS:
        return False
    today = datetime.date.today().isoformat()
    days = load_store()
    report = (data.get("report") or "").strip()
    entry = {
        "credit": data.get("today_credit"),
        "streak": data.get("streak_days"),
        "total": data.get("total_credits"),
        "result": data.get("result"),
        "report": report[:600],
        "at": datetime.datetime.now().strftime("%H:%M:%S"),
    }
    # 保留当日首次记录的时间（同一天多次运行时只更新数值，不覆盖首次时间）
    old = days.get(today)
    if isinstance(old, dict) and old.get("at") and data.get("result") == "ALREADY":
        entry["at"] = old.get("at", entry["at"])
        if entry.get("credit") is None:
            entry["credit"] = old.get("credit")
        if entry.get("total") is None:
            entry["total"] = old.get("total")
    days[today] = entry
    # 只保留最近 N 天
    keys = sorted(days.keys())
    if len(keys) > _MAX_DAYS:
        days = {k: days[k] for k in keys[-_MAX_DAYS:]}
    return save_store(days)


def load_days():
    """返回 (days, today)：days 为按日期升序的 [{date, credit, streak}]。"""
    days_map = load_store()
    out = []
    for d in sorted(days_map.keys()):
        v = days_map.get(d)
        v = v if isinstance(v, dict) else {}
        out.append({"date": d, "credit": v.get("credit"), "streak": v.get("streak")})
    return out, datetime.date.today().isoformat()


def series(limit=120):
    """按日期升序返回每日记录，供趋势图使用（比 load_days 多带 total/result）。

    趋势图要画"累计积分"曲线，而 load_days 只暴露 credit/streak；这里把
    total 一起给出，并保留 result，方便前端区分"真签了"和"今天已签"。
    """
    days = load_store()
    keys = sorted(days.keys())
    if limit and len(keys) > limit:
        keys = keys[-limit:]
    out = []
    for d in keys:
        v = days.get(d)
        v = v if isinstance(v, dict) else {}
        out.append({
            "date": d,
            "total": v.get("total"),
            "credit": v.get("credit"),
            "streak": v.get("streak"),
            "result": v.get("result"),
        })
    return out


def status():
    """给体检用：文件状态与健康度。"""
    exists = os.path.exists(STORE)
    size = os.path.getsize(STORE) if exists else 0
    days, st = load_store_ex()
    corrupts = []
    try:
        corrupts = [n for n in os.listdir(HERE) if ".corrupt-" in n and n.startswith("signin_history")]
    except Exception:
        pass
    return {
        "exists": exists,
        "size": size,
        "status": st,
        "days": len(days),
        "latest": max(days.keys()) if days else None,
        "corrupt_backups": len(corrupts),
    }
