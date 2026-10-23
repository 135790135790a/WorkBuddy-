#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""heartbeat.py —— 运行心跳：回答「任务到底有没有跑」。

为什么不能靠日志判断：
    signin.log 只在有输出时才写；轮询任务一天跑 6 次、空跑默认不写日志
    （除非 WORKBUDDY_GROWTH_LOG_EMPTY=1）。于是"日志很旧"和"根本没跑"
    分不出来，而且日志还会被轮转或手工清掉。

做法：
    每次整轮运行结束（成功或失败）都写一次 heartbeat.json，只记三个事实：
    最后一次跑在什么时候、结果是什么、距今多久。

谁来判"太久了"：
    - 托盘每小时静默检查一次（电脑开着、任务却被禁用 → 只有它能发现）
    - 面板体检（/health 里单独一项）
    - 下一次运行开始时（关机多日 → 开机后第一次跑就能看见断档）

分两种心跳，别混用：
    - 顶层 at/stale  —— 最近一次运行，谁触发都算（面板、托盘、计划任务）
    - scheduled_*    —— 只有 silent / silent-poll / silent-growth 才更新
    断档告警只看 scheduled_*。否则你随手点一次"运行测试"，断档信号就被洗掉了。

文件：heartbeat.json
    {"_schema":1, "at":"2026-10-18 20:00:05", "date":"2026-10-18",
     "mode":"silent", "ok":true, "result":"CLAIMED", "gap_hours":7.2,
     "attempts":1, "retried":false, "pid":1234, "host":"PC",
     "prev_at":"2026-10-18 12:47:14", "alerted_at":""}

环境变量：
    WORKBUDDY_HEARTBEAT_FILE  覆盖路径（测试用）
    WORKBUDDY_STALE_HOURS     断档阈值小时数，默认 26
"""
import datetime
import os
import socket

import store

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "heartbeat.json")
SCHEMA = 1

STALE_HOURS = 26       # 超过这么久没有心跳 → 判定断档（一天一次的任务留 2 小时余量）
ALERT_GAP_HOURS = 12   # 同一条断档告警 12 小时内只发一次，避免复读
FMT = "%Y-%m-%d %H:%M:%S"

# 这些 mode 只由系统计划任务产生（见 scheduler.PRESETS）。
# 停跑判定只看它们：手动点一次"运行测试"不该把断档信号洗掉。
SCHEDULED_MODES = {"silent", "silent-poll", "silent-growth"}


def is_scheduled_mode(mode):
    return (mode or "").strip().lower() in SCHEDULED_MODES


def path():
    return os.environ.get("WORKBUDDY_HEARTBEAT_FILE") or PATH


def stale_hours():
    try:
        v = float(os.environ.get("WORKBUDDY_STALE_HOURS") or STALE_HOURS)
    except Exception:
        v = STALE_HOURS
    return v if v > 0 else STALE_HOURS


def _parse(text):
    try:
        return datetime.datetime.strptime(str(text), FMT)
    except Exception:
        return None


def read():
    data = store.read_json(path(), default=None)
    return data if isinstance(data, dict) else {}


def _gap(prev, now):
    """与上一次心跳的间隔。没有上一次时 gap_hours 为 None（不告警）。"""
    at = _parse(prev.get("at"))
    if not at:
        return {"prev_at": "", "gap_hours": None, "gap_days": None, "stale_gap": False}
    hours = max(0.0, (now - at).total_seconds() / 3600.0)
    return {
        "prev_at": prev.get("at") or "",
        "gap_hours": round(hours, 1),
        "gap_days": max(0, (now.date() - at.date()).days),
        "stale_gap": hours >= stale_hours(),
    }


def beat(payload=None, mode="", now=None):
    """写一次心跳，返回与上一次的间隔信息。

    调用方是签到主流程，所以这里绝不抛异常：写失败也要让签到照常返回。
    """
    now = now or datetime.datetime.now()
    try:
        prev = read()
    except Exception:
        prev = {}
    payload = payload or {}
    data = payload.get("data") or {}
    info = _gap(prev, now)
    rec = {
        "_schema": SCHEMA,
        "at": now.strftime(FMT),
        "date": now.date().isoformat(),
        "mode": mode or payload.get("mode") or "",
        "ok": bool(payload.get("ok")),
        # status 等只读命令不一定带 result 字段，用 OK 兜底，别在面板上显示成 "—"
        "result": (data.get("result") or payload.get("result")
                   or ("OK" if payload.get("ok") else "UNKNOWN")),
        "gap_hours": info["gap_hours"],
        "attempts": int(payload.get("attempts_used") or 0),
        "retried": bool(payload.get("retried")),
        "pid": os.getpid(),
        "host": socket.gethostname()[:64],
        # 上一次是谁、什么时候：事后追断档时不用再翻日志
        "prev_at": prev.get("at") or "",
        "alerted_at": prev.get("alerted_at") or "",
    }
    if is_scheduled_mode(rec["mode"]):
        sched = _gap({"at": prev.get("scheduled_at") or ""}, now)
        rec.update(
            scheduled_at=rec["at"],
            scheduled_date=rec["date"],
            scheduled_ok=rec["ok"],
            scheduled_result=rec["result"],
            scheduled_gap_hours=sched["gap_hours"],
        )
        info.update(scheduled=True,
                    prev_scheduled_at=sched["prev_at"],
                    scheduled_gap_hours=sched["gap_hours"],
                    scheduled_stale_gap=sched["stale_gap"])
    else:
        # 手动/面板运行：只更新"最近一次运行"，不动定时任务的记录
        for key in ("scheduled_at", "scheduled_date", "scheduled_ok",
                    "scheduled_result", "scheduled_gap_hours"):
            if key in prev:
                rec[key] = prev[key]
        info.update(scheduled=False,
                    prev_scheduled_at=rec.get("scheduled_at") or "",
                    scheduled_gap_hours=None,
                    scheduled_stale_gap=False)
    try:
        store.atomic_write_json(path(), rec, indent=1)
    except Exception:
        pass
    info.update(at=rec["at"], ok=rec["ok"], result=rec["result"])
    return info


def _scheduled_of(rec, now):
    """最近一次**定时任务**的运行记录（断档判定只看这一份）。"""
    out = {"exists": False, "at": "", "date": "", "ok": None, "result": "",
           "age_hours": None, "days_since": None, "stale": False,
           "gap_hours": rec.get("scheduled_gap_hours")}
    at = _parse(rec.get("scheduled_at"))
    if not at:
        return out
    age = max(0.0, (now - at).total_seconds() / 3600.0)
    out.update(exists=True, at=rec.get("scheduled_at") or "",
               date=rec.get("scheduled_date") or "",
               ok=rec.get("scheduled_ok"),
               result=rec.get("scheduled_result") or "",
               age_hours=round(age, 1),
               days_since=max(0, (now.date() - at.date()).days),
               stale=age >= stale_hours())
    return out


def status(now=None):
    """当前心跳状态。字段名与其它模块的 status() 风格保持一致。

    两层语义，别混用：
      - 顶层 at/stale：最近一次运行，不管谁触发（面板、托盘、计划任务）
      - scheduled：最近一次**定时任务**运行，断档告警只看它
    否则手动点一次"运行测试"就能把断档信号洗掉。
    """
    now = now or datetime.datetime.now()
    rec = read()
    at = _parse(rec.get("at"))
    base = {
        "exists": bool(rec),
        "at": rec.get("at") or "",
        "date": rec.get("date") or "",
        "ok": rec.get("ok") if rec else None,
        "result": rec.get("result") or "",
        "mode": rec.get("mode") or "",
        "host": rec.get("host") or "",
        "alerted_at": rec.get("alerted_at") or "",
        "stale_hours": stale_hours(),
        "age_hours": None,
        "days_since": None,
        "stale": False,
        "scheduled": _scheduled_of(rec, now),
    }
    if not at:
        return base
    age = max(0.0, (now - at).total_seconds() / 3600.0)
    base["age_hours"] = round(age, 1)
    base["days_since"] = max(0, (now.date() - at.date()).days)
    base["stale"] = age >= stale_hours()
    return base


def _recently_alerted(st):
    at = _parse(st.get("alerted_at"))
    if not at:
        return False
    return (datetime.datetime.now() - at).total_seconds() < ALERT_GAP_HOURS * 3600


def _notify_enabled():
    """推送开着时，失败由 notify 自己报，心跳不再重复通知。"""
    try:
        import notify
        return bool(notify.load_config().get("enabled"))
    except Exception:
        return True


def should_alert(st=None):
    """返回 (bool, 原因)。

    只看**定时任务**的心跳（st["scheduled"]），手动运行不参与判定。
    两种情况才告警，且都受静默期约束：
      1. stale  —— 定时任务太久没跑（被禁用 / 电脑长期关机）
      2. failing —— 定时任务最后一次失败，而推送没开（失败没人知道）
    """
    st = st or status()
    sch = st.get("scheduled") or {}
    if not st.get("exists"):
        # 从没跑过：可能是刚装好，不当作故障
        return False, "no-heartbeat"
    if not sch.get("exists"):
        # 还没有任何定时任务的心跳：可能刚装好，也可能只用手动运行
        return False, "no-scheduled-beat"
    if sch.get("stale"):
        if _recently_alerted(st):
            return False, "throttled"
        return True, "stale %.1fh" % (sch.get("age_hours") or 0)
    if sch.get("ok") is False:
        if _notify_enabled():
            return False, "notify-handles-it"
        if _recently_alerted(st):
            return False, "throttled"
        return True, "failing %s" % (sch.get("result") or "ERROR")
    return False, "fresh"


def mark_alerted(now=None):
    """记录已告警时间（保留其它字段）。"""
    now = now or datetime.datetime.now()
    rec = read()
    if not rec:
        return False
    rec["alerted_at"] = now.strftime(FMT)
    return store.atomic_write_json(path(), rec, indent=1)


def alert_text(st=None):
    """构造告警文案，返回 (title, body)。

    优先讲定时任务那一次（那才是"今天到底跑没跑"的证据）；
    没有定时记录时退回最近一次运行。
    """
    st = st or status()
    sch = st.get("scheduled") or {}
    target = sch if sch.get("exists") else st
    if target.get("ok") is False:
        return ("WorkBuddy 签到一直在失败",
                "最后一次定时运行在 %s（%.1f 小时前），结果是 %s。\n"
                "推送通知没开，这条由托盘代发，避免失败一直没人知道。\n"
                "常见原因：凭据文件被占用（PermissionError）、登录态失效。\n"
                "打开面板看详情：http://127.0.0.1:8765/"
                % (target.get("at") or "—", target.get("age_hours") or 0,
                   target.get("result") or "ERROR"))
    title = "WorkBuddy 签到已停跑"
    if not st.get("exists"):
        body = "本地还没有任何运行心跳。如果这是刚装好，忽略即可；否则请打开面板体检。"
        return title, body
    body = ("最后一次定时运行在 %s（%.0f 小时前，结果：%s）。\n"
            "手动运行不会刷新这个时间，所以它确实代表定时任务的状态。\n"
            "常见原因：计划任务被禁用、电脑长期关机、解释器路径失效。\n"
            "打开面板体检：http://127.0.0.1:8765/"
            % (target.get("at") or "—", target.get("age_hours") or 0,
               target.get("result") or "未知"))
    return title, body


def gap_alert(info):
    """本次运行前检测到断档 → 返回 (title, body)，否则 None。

    定时任务的运行看"定时心跳"的间隔，手动运行看总体间隔。
    和 should_alert 共用同一个静默期：刚为断档发过告警，恢复时不再重复。
    """
    if not info:
        return None
    if info.get("scheduled"):
        stale = info.get("scheduled_stale_gap")
        hours = info.get("scheduled_gap_hours")
        prev = info.get("prev_scheduled_at")
        kind = "定时任务"
    else:
        stale = info.get("stale_gap")
        hours = info.get("gap_hours")
        prev = info.get("prev_at")
        kind = "签到"
    if not stale:
        return None
    if _recently_alerted(read()):
        return None
    return ("WorkBuddy %s曾中断 %.0f 小时" % (kind, hours or 0),
            "上一次运行在 %s，本次已恢复（结果：%s）。\n"
            "如果这个间隔不该出现，检查一下电脑是否长时间关机或任务被停用。"
            % (prev or "—", info.get("result") or "未知"))
