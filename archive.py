#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""archive.py —— 报告快照归档 + 原始响应留证。

解决三个"过去就找不回来"的问题：
    1. report.html 每次生成都原地覆盖 → 昨天/上周的结果再也看不到
       → 每次运行额外存一份 reports/YYYY-MM-DD_HHMMSS.html，可回看
    2. 只留渲染后的中文摘要，没有原始返回 → 要跟人核对积分时拿不出凭证
       → 同时存 evidence/YYYY-MM-DD_HHMMSS.json（signin 的原始 payload + stdout）
    3. 归档多了会无限膨胀
       → 报告保留最近 80 份、凭证保留最近 200 份，自动清理

索引 reports/index.json 记录每份归档的元信息（时间/结果/积分/是否成功），
供面板列表直接渲染，不必去解析 HTML。
"""
import datetime
import os
import re
import time

import store

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(HERE, "reports")
EVIDENCE_DIR = os.path.join(HERE, "evidence")
INDEX = os.path.join(REPORT_DIR, "index.json")

KEEP_REPORTS = 80
KEEP_EVIDENCE = 200
KEEP_INDEX = 120

_SAFE = re.compile(r"^[0-9A-Za-z_.\-]+$")


def _safe_path(dir_path, name):
    """只允许纯文件名，禁止 ../ 与绝对路径。"""
    if not name or not _SAFE.match(name):
        return None
    p = os.path.abspath(os.path.join(dir_path, name))
    if os.path.dirname(p) != os.path.abspath(dir_path):
        return None
    return p


def load_index():
    data, _ = store.read_json_ex(INDEX, {"_schema": 1, "items": []})
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    return items if isinstance(items, list) else []


def save_index(items):
    return store.atomic_write_json(
        INDEX, {"_schema": 1, "items": items[:KEEP_INDEX]}, indent=0)


def _evidence_of(payload, now, mode):
    data = (payload or {}).get("data") or {}
    return {
        "schema": 1,
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "ok": bool((payload or {}).get("ok")),
        "exit_code": (payload or {}).get("exit_code"),
        "attempts_used": (payload or {}).get("attempts_used"),
        "needs_alert": (payload or {}).get("needs_alert"),
        "skipped": (payload or {}).get("skipped"),
        "result": data.get("result"),
        "today_credit": data.get("today_credit"),
        "streak_days": data.get("streak_days"),
        "total_credits": data.get("total_credits"),
        "report": data.get("report"),
        "data": data,
        "attempts": (payload or {}).get("history"),
        "stdout": ((payload or {}).get("raw") or "")[:20000],
        "stderr": ((payload or {}).get("stderr") or "")[:8000],
    }


_ENV_CACHE = {"t": 0.0, "v": None}
_ENV_TTL = 120          # 同一轮运行里 save() 与 save_evidence() 会连着调用，缓存一下


def _env_of():
    """记录当时的"环境指纹"：推送通道、计划任务、历史天数、凭据剩余。

    为什么要存：只对比积分/连续天数，看不出"昨天能推送、今天不能"这类问题。
    把环境一起冻结进快照，两份快照一对比就能定位"是什么变了"。

    只有轻量读取（不碰 token、不发网络请求），失败一律忽略。
    """
    now = time.time()
    if _ENV_CACHE["v"] is not None and now - _ENV_CACHE["t"] < _ENV_TTL:
        return dict(_ENV_CACHE["v"])

    env = {}
    try:
        import notify
        cfg = notify.load_config() or {}
        env["channel"] = cfg.get("channel") or ""
        env["notify_on"] = bool(cfg.get("enabled"))
        env["notify_ready"] = bool(
            cfg.get("webhook_url") or cfg.get("telegram_chat_id")
            or (cfg.get("email") or {}).get("to"))
    except Exception:
        pass
    try:
        import scheduler
        tasks = scheduler.list_tasks()
        env["tasks"] = [
            {"name": t.get("name"), "times": t.get("times"), "enabled": t.get("enabled")}
            for t in tasks if isinstance(t, dict)
        ]
    except Exception:
        pass
    try:
        import history
        env["history_days"] = history.status().get("days")
    except Exception:
        pass
    try:
        import health
        h = health.cred_health()
        env["cred_days"] = h.get("days_left")
        env["cred_state"] = h.get("state")
    except Exception:
        pass

    _ENV_CACHE["t"] = now
    _ENV_CACHE["v"] = dict(env)
    return env


def _entry_of(ev_name, now, mode, payload, has_report, size=0):
    data = (payload or {}).get("data") or {}
    return {
        "file": "",
        "evidence": ev_name,
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "mode": mode,
        "ok": bool((payload or {}).get("ok")),
        "result": data.get("result"),
        "credit": data.get("today_credit"),
        "total": data.get("total_credits"),
        "streak": data.get("streak_days"),
        "attempts": (payload or {}).get("attempts_used"),
        "skipped": (payload or {}).get("skipped"),
        "has_report": bool(has_report),
        "has_evidence": True,
        "size": size,
        "env": _env_of(),
    }


def _learn_baseline(payload):
    """成功运行顺手更新"接口契约基线"，供 monitor.py 比对（失败不影响归档）。"""
    try:
        import monitor
        monitor.update_baseline(payload)
    except Exception:
        pass


def save_evidence(payload, mode="silent", keep=KEEP_EVIDENCE):
    """只留凭证（定时任务/托盘等不生成报告的运行）。

    这是最关键的一条审计路径：定时任务在后台静默跑，失败时没人看得到，
    只有凭证能证明"当时到底返回了什么"。
    """
    now = datetime.datetime.now()
    ev_name = now.strftime("%Y-%m-%d_%H%M%S") + ".json"
    ev_path = _safe_path(EVIDENCE_DIR, ev_name)
    if not ev_path:
        return None
    if not store.atomic_write_json(ev_path, _evidence_of(payload, now, mode), indent=1):
        return None
    entry = _entry_of(ev_name, now, mode, payload, has_report=False)
    items = [e for e in load_index() if e.get("evidence") != ev_name]
    items.insert(0, entry)
    save_index(items)
    store.prune_dir(EVIDENCE_DIR, keep, suffix=".json")
    _learn_baseline(payload)
    return entry


def save(html, payload, mode="auto", keep_report=KEEP_REPORTS):
    """写入一份报告快照与对应凭证。返回索引条目（失败返回 None）。"""
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H%M%S")
    rep_name = stamp + ".html"
    ev_name = stamp + ".json"

    rep_path = _safe_path(REPORT_DIR, rep_name)
    ev_path = _safe_path(EVIDENCE_DIR, ev_name)
    if not rep_path or not ev_path:
        return None

    ok_html = store.atomic_write_text(rep_path, html)
    ok_ev = store.atomic_write_json(ev_path, _evidence_of(payload, now, mode), indent=1)

    entry = _entry_of(ev_name, now, mode, payload, has_report=ok_html,
                      size=os.path.getsize(rep_path) if ok_html else 0)
    entry["file"] = rep_name
    entry["has_evidence"] = bool(ok_ev)
    items = [e for e in load_index() if e.get("file") != rep_name]
    items.insert(0, entry)
    save_index(items)

    # 自动清理（保留最近 N 份）
    store.prune_dir(REPORT_DIR, keep_report, suffix=".html")
    store.prune_dir(EVIDENCE_DIR, KEEP_EVIDENCE, suffix=".json")
    _learn_baseline(payload)
    return entry


def list_reports(limit=60):
    """返回仍然存在的归档（按时间倒序），含"只有凭证、没有报告"的静默运行。"""
    out = []
    for e in load_index():
        if not isinstance(e, dict):
            continue
        rep = _safe_path(REPORT_DIR, e.get("file") or "") if e.get("file") else None
        ev = _safe_path(EVIDENCE_DIR, e.get("evidence") or "") if e.get("evidence") else None
        has_rep = bool(rep and os.path.exists(rep))
        has_ev = bool(ev and os.path.exists(ev))
        if not has_rep and not has_ev:
            continue
        item = dict(e)
        item["has_report"] = has_rep
        item["has_evidence"] = has_ev
        if has_rep:
            try:
                item["size"] = os.path.getsize(rep)
            except Exception:
                pass
        out.append(item)
        if len(out) >= limit:
            break
    # 兼容：索引丢失但磁盘上有报告时，用文件名兜底列出
    if not out:
        for f in store.list_dir_files(REPORT_DIR, suffix=".html")[:limit]:
            out.append({
                "file": f["name"], "ts": f["mtime"], "size": f["size"],
                "ok": None, "result": None, "credit": None,
                "has_report": True, "has_evidence": False, "from_disk": True,
            })
    return out


def open_report(name):
    """只允许归档 HTML（防止把 index.json 等非报告文件当报告返回）。"""
    if not name or not name.lower().endswith(".html"):
        return None
    p = _safe_path(REPORT_DIR, name)
    if p and os.path.exists(p):
        return p
    return None


def open_evidence(name):
    """只允许凭证 JSON。"""
    if not name or not name.lower().endswith(".json"):
        return None
    p = _safe_path(EVIDENCE_DIR, name)
    if p and os.path.exists(p):
        return p
    return None


def remove(key):
    """删除一条归档（按报告文件名或凭证文件名匹配），文件与索引一起清。

    典型用途：清掉"测试/误跑"留下的记录，保持审计链干净。
    返回 {'removed': bool, 'files': [...]}
    """
    items = load_index()
    kept, files = [], []
    hit = False
    for e in items:
        if not isinstance(e, dict):
            continue
        if e.get("file") == key or e.get("evidence") == key:
            hit = True
            for p in (open_report(e.get("file") or "") if e.get("file") else None,
                      open_evidence(e.get("evidence") or "") if e.get("evidence") else None):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                        files.append(os.path.basename(p))
                    except Exception:
                        pass
        else:
            kept.append(e)
    if hit:
        save_index(kept)
    return {"removed": hit, "files": files}


def status():
    """给体检用。"""
    items = list_reports(10)
    reps = store.list_dir_files(REPORT_DIR, suffix=".html")
    evs = store.list_dir_files(EVIDENCE_DIR, suffix=".json")
    corrupts = 0
    try:
        corrupts = len([n for n in os.listdir(REPORT_DIR) if ".corrupt-" in n])
    except Exception:
        pass
    latest = items[0] if items else None
    return {
        "reports": len(reps),
        "evidence": len(evs),
        "latest": (latest.get("ts") if latest else (reps[0]["mtime"] if reps else None)),
        # 最近一次"有结果"的运行（跳过 status 这类无 result 的探针）
        "latest_result": next((i.get("result") for i in items if i.get("result")), None),
        "corrupt_backups": corrupts,
        "dir": REPORT_DIR,
    }
