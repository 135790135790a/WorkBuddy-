#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monitor.py —— 接口失效监测（逆向接口改版预警）。

背景：
    `signin.py` 是对 WorkBuddy 桌面端接口的逆向实现。对方一改版，脚本通常不会
    报"接口变了"，而是抛出各种难以归因的现象：返回不是 JSON、字段名变了导致
    积分取不到、出现没见过的业务码、HTTP 404 变成 401……

    结果就是：签到悄悄失败几天，你还以为一切正常。

做法（两层判定，尽量不误报）：
    1. 结构信号（硬信号）：输出不是 JSON / 没有 result 字段 / 出现未登记的
       result 值 / 报错文本里带 KeyError・AttributeError・JSONDecodeError 等
       "脚本解析失败"的特征 —— 这些基本只会在接口变形状时出现。
    2. 契约基线：按 **result 类型分别**记住"正常时有哪些字段"
       （ALREADY 有 today_credit，GROWTH 有 credits_gained+energy，混着比会天天误报）。
       同一个 result 再次出现、却少了基线里有的字段 → 契约漂移。

    再加一层去重：同一状态的告警 6 小时内只推一次，避免变成复读机。
"""
import datetime
import json
import os

import store

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "monitor_state.json")
EVIDENCE_DIR = os.path.join(HERE, "evidence")

SCHEMA = 1
WINDOW = 20               # 每次扫描看最近多少条凭证
ALERT_COOLDOWN_H = 6      # 同一状态 6 小时内只提醒一次

# signin.py 会吐出的 result 全集（新增值意味着脚本或接口发生了版本变化）
KNOWN_RESULTS = {
    "ALREADY", "GROWTH", "CLAIMED", "INACTIVE", "AUTH_READY",
    "NETWORK", "TIMEOUT", "UNKNOWN", "ERROR",
    "NO_AUTH", "AUTH_REJECTED", "FORBIDDEN",
    # 凭据文件被客户端占用（环境问题，不是接口改版）
    "AUTH_BUSY",
}
OK_RESULTS = {"ALREADY", "GROWTH", "CLAIMED", "INACTIVE", "AUTH_READY"}

# 这些 result 里的"字段"本身就不稳定，不参与契约比较
_SKIP_CONTRACT = {"AUTH_READY", "INACTIVE"}

# ---------------- 归因规则（按顺序匹配，先命中先返回）----------------
_RULES = [
    # (code, 关键词, 说明, 处置建议)
    ("env", ("PermissionError", "Permission denied", "拒绝访问", "访问被拒",
             "WinError 5", "AUTH_BUSY", "CREDENTIAL_BUSY", "CREDENTIAL_DENIED",
             "凭据文件被"),
     "读取本地凭据或文件被拒绝",
     ["确认脚本以你自己的身份运行（不要放在受限沙箱/服务账户里）",
      "检查杀毒软件是否拦截了对 WorkBuddy 数据目录的读取",
      "面板/托盘的「立即签到」若由受限进程启动，请改用桌面快捷方式"]),

    ("auth", ("NO_AUTH", "AUTH_REJECTED", "登录凭据", "登录已过期", "HTTP 401"),
     "登录态失效或被服务端拒绝",
     ["打开 WorkBuddy 桌面端重新登录",
      "登录后再点一次「运行测试」确认恢复",
      "若反复失效：检查客户端是否切换了服务区域（endpoint 变了）"]),

    ("forbidden", ("FORBIDDEN", "HTTP 403"),
     "服务端拒绝该操作（权限/活动条件）",
     ["确认账号是否被限制参与签到活动",
      "换个时间再试；若持续 403，多半是活动规则变了"]),

    ("network", ("NETWORK", "TIMEOUT", "网络不可达", "timed out", "Connection",
                 "urlopen error", "SSLError", "getaddrinfo"),
     "网络不可达或超时",
     ["检查网络/代理（尤其公司网、VPN）",
      "系统时间是否准确（TLS 握手依赖时间）",
      "这类失败脚本会自动重试，通常无需干预"]),

    ("drift", ("未识别的领取返回", "KeyError", "AttributeError", "TypeError",
               "IndexError", "JSONDecodeError", "Expecting value",
               "'NoneType'", "not subscriptable", "unsupported operand",
               "接口返回异常（HTTP 404", "HTTP 404", "HTTP 410"),
     "接口形状变了：脚本按旧字段解析失败",
     ["打开面板「🔍 接口失效监测」→ 最新一条「原始数据」，看真实返回长什么样",
      "对照脚本里解析的字段名（today_credit / streak_days / total_credits），确认被改名了",
      "去脚本来源仓库看有没有新版本（逆向脚本通常跟版本更新）",
      "WorkBuddy 桌面端若刚升级过，基本可以确定是接口改版"]),

    ("server", ("HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504",
                "Internal Server", "Service Unavailable"),
     "服务端错误（5xx），通常是平台临时故障",
     ["稍等一段时间再试（脚本会自动重试）",
      "连续多小时 5xx 才需要关注，一般无需改脚本"]),
]

ADVICE_BY_CODE = {c: a for c, _, _, a in _RULES}
DESC_BY_CODE = {c: d for c, _, d, _ in _RULES}


# ---------------- 状态文件 ----------------
def _load_state():
    data, _ = store.read_json_ex(STATE, {"_schema": SCHEMA, "baseline": {}, "last_alert": {}},
                                 migrate=_migrate_state)
    if not isinstance(data, dict):
        return {"_schema": SCHEMA, "baseline": {}, "last_alert": {}}
    data.setdefault("baseline", {})
    data.setdefault("last_alert", {})
    return data


def _migrate_state(data):
    if not isinstance(data, dict):
        return {"_schema": SCHEMA, "baseline": {}, "last_alert": {}}
    data.pop("_schema", None)
    return data


def save_state(state):
    payload = dict(state)
    payload["_schema"] = SCHEMA
    payload["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    store.atomic_write_json(STATE, payload, indent=1)


# ---------------- 单次运行归因 ----------------
def _text_of(ev):
    """把可能藏错误的字段拼成一段文本：顶层 report、data.report、stderr、stdout。"""
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    parts = [
        ev.get("report") or "",
        data.get("report") or "",
        data.get("growth") or "",
        ev.get("stderr") or "",
        ev.get("stdout") or "",
    ]
    return "\n".join(p for p in parts if isinstance(p, str) and p)[:8000]


# result 直接映射（比关键词匹配更可靠，作为文本归因的兜底）
_RESULT_CODE = {
    "NETWORK": "network",
    "TIMEOUT": "network",
    "NO_AUTH": "auth",
    "AUTH_REJECTED": "auth",
    "AUTH_BUSY": "env",
    "FORBIDDEN": "forbidden",
    "UNKNOWN": "drift",
}


def _match(text):
    for code, kws, desc, advice in _RULES:
        for kw in kws:
            if kw and kw in text:
                return code, desc, advice
    return None, None, None


def _contract_diff(ev, baseline):
    """同一 result 下，基线里有的字段现在没了 → 漂移。返回缺失字段列表。"""
    data = ev.get("data") or {}
    result = data.get("result")
    if not result or result in _SKIP_CONTRACT:
        return []
    base = (baseline or {}).get(result)
    if not isinstance(base, dict) or not base.get("keys"):
        return []
    current = set(data.keys())
    return [k for k in base["keys"] if k not in current]


def analyze(ev, baseline=None):
    """对一条凭证（或一次运行的 payload）做归因。

    返回 {severity, code, desc, signals[], advice[], result, ts}
      severity: ok / warn / drift
    baseline 传入可避免反复读状态文件；不传则现读。
    """
    ev = ev or {}
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    result = data.get("result") or ev.get("result")
    ok = bool(ev.get("ok"))
    text = _text_of(ev)
    signals, advice = [], []

    # 状态探针（status/doctor）只探测连通性，不产生 signin 契约，不能拿它判改版
    if (ev.get("mode") or "") in ("status", "doctor"):
        return {"severity": "ok", "code": "probe", "desc": "状态探针（不参与契约判定）",
                "signals": [], "advice": [], "result": result,
                "ts": ev.get("ts", "")}

    # ① 硬结构信号
    has_json = data or bool(ev.get("result"))
    if not has_json:
        raw = (ev.get("stdout") or "").strip()
        if ev.get("skipped"):
            return {"severity": "ok", "code": "skipped", "desc": "本次运行被跳过（已有实例在跑）",
                    "signals": [], "advice": [], "result": result,
                    "ts": ev.get("ts", "")}
        signals.append("脚本没有输出可解析的 JSON（原始输出 %d 字节）" % len(raw))
        # 可能是凭证/网络问题，先按文本归因，归不到再判漂移
        code, desc, adv = _match(text)
        if code:
            return {"severity": "warn" if code in ("network", "server") else "drift",
                    "code": code, "desc": desc, "signals": signals, "advice": adv,
                    "result": result, "ts": ev.get("ts", "")}
        return {"severity": "drift", "code": "drift",
                "desc": "输出不是预期的 JSON，疑似脚本或接口结构变化",
                "signals": signals, "advice": ADVICE_BY_CODE["drift"],
                "result": result, "ts": ev.get("ts", "")}

    if not result:
        signals.append("返回里缺少 result 字段（脚本约定必有的字段）")
        return {"severity": "drift", "code": "drift",
                "desc": "约定字段缺失，接口或脚本版本可能已变化",
                "signals": signals, "advice": ADVICE_BY_CODE["drift"],
                "result": None, "ts": ev.get("ts", "")}

    if result not in KNOWN_RESULTS:
        signals.append("出现了未登记的 result 值：%s" % result)
        return {"severity": "drift", "code": "drift",
                "desc": "业务状态码不在已知集合里，接口很可能已改版",
                "signals": signals, "advice": ADVICE_BY_CODE["drift"],
                "result": result, "ts": ev.get("ts", "")}

    # ② 文本归因（错误类结果才需要）
    code, desc, adv = (None, None, None)
    if result not in OK_RESULTS or not ok:
        code, desc, adv = _match(text)

    # ③ 契约字段比对（成功的结果才有意义）
    if baseline is None:
        baseline = _load_state().get("baseline") or {}
    missing = _contract_diff(ev, baseline)
    if missing:
        signals.append("同一结果（%s）缺少基线中存在的字段：%s" % (result, "、".join(missing)))
        return {"severity": "drift", "code": "drift",
                "desc": "返回字段变少：接口改名或脚本解析目标失效",
                "signals": signals, "advice": ADVICE_BY_CODE["drift"],
                "result": result, "ts": ev.get("ts", "")}

    if result == "UNKNOWN":
        signals.append("脚本返回 UNKNOWN（未能识别业务返回）")
        matched = _match(text)
        return {"severity": "drift", "code": "drift",
                "desc": "出现未识别的业务返回，接口行为已超出脚本预期",
                "signals": signals,
                "advice": matched[2] or ADVICE_BY_CODE["drift"],
                "result": result, "ts": ev.get("ts", "")}

    if result in OK_RESULTS and ok:
        return {"severity": "ok", "code": "ok", "desc": "正常", "signals": [],
                "advice": [], "result": result, "ts": ev.get("ts", "")}

    # ④ 失败但能归因：先看文本，再看 result 映射
    if not code:
        code = _RESULT_CODE.get(result)
        if code:
            desc = DESC_BY_CODE.get(code)
            adv = ADVICE_BY_CODE.get(code)
    if code:
        sev = "warn" if code in ("network", "server", "forbidden", "auth", "env") else "drift"
        return {"severity": sev, "code": code, "desc": desc or "", "signals": signals,
                "advice": adv or [], "result": result, "ts": ev.get("ts", "")}

    # ⑤ 兜底：失败且归不了因 —— 不轻易判成"接口失效"，标为需观察
    signals.append("失败但未能匹配已知原因，原始信息需人工查看")
    return {"severity": "warn", "code": "unknown", "desc": "失败原因未识别",
            "signals": signals,
            "advice": ["打开最新一条「原始数据」凭证，看脚本真实返回",
                       "若同时伴随字段缺失或非 JSON，则按接口改版处理"],
            "result": result, "ts": ev.get("ts", "")}


# ---------------- 基线维护 ----------------
def update_baseline(ev):
    """成功运行后记录/更新"这个 result 正常时有哪些字段"。"""
    state = _load_state()
    data = (ev or {}).get("data") or {}
    result = data.get("result")
    if not result or result in _SKIP_CONTRACT:
        return False
    if not ev.get("ok"):
        return False
    keys = sorted(k for k in data.keys() if k != "raw")
    baseline = state.get("baseline") or {}
    old = baseline.get(result) or {}
    # 只增不减：把新出现的字段补进基线，避免一次异常把基线削掉
    merged = sorted(set(old.get("keys") or []) | set(keys))
    if old.get("keys") == merged:
        return False
    baseline[result] = {
        "keys": merged,
        "taken": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    state["baseline"] = baseline
    save_state(state)
    return True


# ---------------- 扫描 ----------------
def _recent_evidence(limit=WINDOW):
    out = []
    try:
        names = sorted((n for n in os.listdir(EVIDENCE_DIR) if n.endswith(".json")), reverse=True)
    except Exception:
        return out
    for n in names[:limit]:
        p = os.path.join(EVIDENCE_DIR, n)
        ev, _ = store.read_json_ex(p, None)
        if isinstance(ev, dict):
            ev.setdefault("ts", n[:19].replace("_", " "))
            ev["_file"] = n
            out.append(ev)
    return out


def scan(limit=WINDOW):
    """扫描最近若干次运行，给出"接口是否可能失效"的结论与处置建议。"""
    evs = _recent_evidence(limit)
    baseline = _load_state().get("baseline") or {}
    runs = []
    for ev in evs:
        a = analyze(ev, baseline)
        runs.append({
            "ts": a.get("ts") or ev.get("ts"),
            "mode": ev.get("mode"),
            "result": a.get("result") or ev.get("result"),
            "severity": a.get("severity"),
            "code": a.get("code"),
            "desc": a.get("desc"),
            "report": ((ev.get("data") or {}).get("report") or ev.get("report") or "")[:160],
            "evidence": ev.get("_file"),
        })

    # 连续异常（从最新往回数，遇到正常就停）；状态探针不参与判定
    judged = [r for r in runs if r["code"] != "probe"]
    consecutive = 0
    for r in judged:
        if r["severity"] == "ok":
            break
        consecutive += 1

    recent3 = judged[:3]
    drift_recent = [r for r in recent3 if r["severity"] == "drift"]
    if drift_recent:
        state = "drift"
    elif any(r["severity"] != "ok" for r in recent3):
        state = "watch"
    else:
        state = "ok"

    # 汇总信号与建议（去重，保持顺序）
    signals, advices = [], []
    for r in runs:
        if r["severity"] == "ok":
            continue
        for s in ([r["desc"]] if r.get("desc") else []):
            if s and s not in signals:
                signals.append(s)
        if r.get("code") and r["code"] in ADVICE_BY_CODE:
            for a in ADVICE_BY_CODE[r["code"]]:
                if a not in advices:
                    advices.append(a)
        elif r.get("code") == "unknown":
            for a in ("打开最新一条「原始数据」凭证，看脚本真实返回",
                      "若同时伴随字段缺失或非 JSON，则按接口改版处理"):
                if a not in advices:
                    advices.append(a)
    if state == "ok":
        signals, advices = [], []

    st = _load_state()
    baseline = st.get("baseline") or {}
    return {
        "ok": True,
        "state": state,
        "state_text": {"ok": "接口正常", "watch": "需观察", "drift": "疑似接口失效"}[state],
        "consecutive_failures": consecutive,
        "counts": {
            "total": len(runs),
            "ok": sum(1 for r in runs if r["severity"] == "ok"),
            "warn": sum(1 for r in runs if r["severity"] == "warn"),
            "drift": sum(1 for r in runs if r["severity"] == "drift"),
        },
        "signals": signals,
        "advice": advices,
        "baseline": {
            "results": sorted(baseline.keys()),
            "count": len(baseline),
            "updated": st.get("updated"),
        },
        "runs": runs[:limit],
        "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------- 告警去重 ----------------
def should_alert(scan_result, cooldown_h=ALERT_COOLDOWN_H):
    """只在"疑似失效"且冷却期外才提醒，避免复读。返回 (bool, 原因)。"""
    if not scan_result or scan_result.get("state") != "drift":
        return False, "not-drift"
    st = _load_state()
    last = (st.get("last_alert") or {}).get("drift") or {}
    when = last.get("at")
    if when:
        try:
            dt = datetime.datetime.strptime(when, "%Y-%m-%d %H:%M:%S")
            hours = (datetime.datetime.now() - dt).total_seconds() / 3600
            if hours < cooldown_h:
                return False, "cooldown-%.1fh" % hours
        except Exception:
            pass
    return True, "ok"


def mark_alerted(scan_result):
    st = _load_state()
    st.setdefault("last_alert", {})["drift"] = {
        "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reasons": (scan_result or {}).get("signals", [])[:3],
    }
    st["last_state"] = (scan_result or {}).get("state")
    save_state(st)


def alert_message(scan_result):
    """生成推送文案：说明现象 + 能做什么。"""
    s = scan_result or {}
    title = "⚠️ WorkBuddy 签到接口疑似失效"
    lines = [
        "最近 %d 次运行中有 %d 次异常（连续 %d 次未成功）。"
        % (s.get("counts", {}).get("total", 0),
           s.get("counts", {}).get("warn", 0) + s.get("counts", {}).get("drift", 0),
           s.get("consecutive_failures", 0)),
    ]
    if s.get("signals"):
        lines.append("现象：")
        for x in s["signals"][:4]:
            lines.append("· " + str(x))
    if s.get("advice"):
        lines.append("建议按顺序排查：")
        for i, x in enumerate(s["advice"][:5], 1):
            lines.append("%d. %s" % (i, x))
    lines.append("详情：面板 →「🔍 接口失效监测」")
    lines.append(s.get("checked_at", ""))
    return title, "\n".join(lines)


# ---------------- 自检 ----------------
if __name__ == "__main__":
    r = scan()
    print(json.dumps({k: r[k] for k in ("state", "state_text", "consecutive_failures", "counts",
                                        "signals", "advice", "baseline")},
                     ensure_ascii=False, indent=2))
