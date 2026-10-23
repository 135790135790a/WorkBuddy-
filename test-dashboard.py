#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test-dashboard.py —— WorkBuddy 自动签到 · 可视化测试面板（后端）

- GET  /                          返回 test-dashboard.html
- GET  /run?mode=auto|status      运行 signin.py 并回传结果
- GET  /tasks                     列出 WorkBuddy 计划任务（含预设）
- POST /tasks                     计划任务增删改/运行/启用停用（JSON）
- GET  /logs                      读取 signin.log 尾部
- GET  /health                    一键体检
- GET  /notify  POST /notify  POST /notify/test   推送配置

安全：绑定 127.0.0.1；校验 Host/Origin（防 DNS rebinding / CSRF）；
所有写操作与 /run、/health 需带页面注入的 X-WB-Token。

运行：python test-dashboard.py  然后浏览器打开 http://127.0.0.1:8765
"""
import datetime
import json
import os
import re
import secrets
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import archive
import history
import milestone
import monitor
import notify
import scheduler
from signin_runner import run_with_retry, PY, SIGNIN, classify, decode

# ---- 路径（与本机安装保持一致）----
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765

HTML = os.path.join(HERE, "test-dashboard.html")

# 每次启动生成随机令牌，注入页面；写操作必须带上它（防 CSRF）
SERVER_TOKEN = secrets.token_urlsafe(24)
ALLOWED_HOSTS = {"127.0.0.1:%d" % PORT, "localhost:%d" % PORT, "127.0.0.1", "localhost"}
ALLOWED_ORIGINS = {"http://127.0.0.1:%d" % PORT, "http://localhost:%d" % PORT}

_LOOPBACK = ("127.0.0.1", "localhost", "::1", "[::1]")


def _host_ok(host):
    """只校验主机名是不是本机回环，**不校验端口**。

    之前把端口写死成 :8765，导致经过预览代理 / 端口转发访问时（Host 里是别的
    端口）一律被判 forbidden host —— 页面直接白屏，看起来像"网站打不开"。
    放开端口不会削弱防护：DNS rebinding 的关键是"域名指向 127.0.0.1"，
    域名本身就过不了这一关。
    """
    if not host:
        return True
    h = host.strip().lower()
    if h.startswith("["):                     # [::1]:8765
        h = h.split("]")[0] + "]"
    else:
        h = h.rsplit(":", 1)[0] if ":" in h else h
    return h in _LOOPBACK


def _origin_ok(origin):
    """同源校验：只放行本机回环（端口不限），其余一律拒绝。"""
    if not origin:
        return True
    o = origin.strip().lower()
    if o == "null":                           # file:// 或沙箱 iframe
        return True
    try:
        netloc = o.split("://", 1)[1].split("/")[0]
    except Exception:
        return False
    return _host_ok(netloc)


def run_signin(mode: str) -> dict:
    """调用 signin.py 并返回结构化结果（成败判定统一走 signin_runner）。

    交互面板走单次运行（attempts=1）：signin.py 内部已做退避重试，这里只负责
    用正确的 classify 判定 ok / needs_alert，避免把 ERROR/TIMEOUT 误判成成功。
    无窗口的「静默捷径」路径（make-report.py）才用完整的 3 次外层重试。
    """
    return run_with_retry(mode, attempts=1)


def _empty_tasks():
    try:
        return scheduler.list_tasks()
    except Exception:
        return []


LOG = os.path.join(HERE, "signin.log")
_ERROR_RESULTS = {"ERROR", "TIMEOUT", "NETWORK", "NO_AUTH", "AUTH_ERROR",
                  "AUTH_REJECTED", "FORBIDDEN", "UNKNOWN"}


_HOME = os.path.expanduser("~")


def tidy_text(s):
    """把日志文案整理成"人能读"的样子，只影响显示，不改原文件。

    两处会让中文日志看起来像乱码，都是程序产生的而不是编码问题：
    1. OSError 的 str() 用 repr(filename)，Windows 路径里的 `\\` 被转义成 `\\\\`，
       于是满屏 `C:\\\\Users\\\\86139\\\\AppData\\\\...`；
    2. 绝对路径太长（这里一条 78 字符），日志框只有一行宽，被硬断成好几截。
    所以先压掉多余反斜杠，再把家目录前缀缩成 `~`，最后把过长路径中段省略。
    """
    if not s:
        return ""
    s = re.sub(r"\\{2,}", r"\\", s)
    if _HOME:
        for prefix in (_HOME + "\\", _HOME.replace("\\", "/") + "/"):
            if prefix in s:
                s = s.replace(prefix, "~" + prefix[-1])
    # 仍然超长的路径（>52 字符）只留开头和结尾，中间用省略号
    def _short(m):
        p = m.group(0)
        if len(p) <= 52:
            return p
        sep = "\\" if "\\" in p else "/"
        parts = p.split(sep)
        if len(parts) < 4:
            return p
        return sep.join(parts[:2] + ["…"] + parts[-2:])
    return re.sub(r"(?:[A-Za-z]:\\|~\\|/)[^\s'\"()，。；、]+", _short, s)


def _parse_log_line(line):
    """把 '[time] {json}' 解析成前端好渲染的结构。"""
    time_str, body = "", line
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", line)
    if m:
        time_str, body = m.group(1), m.group(2)
    payload = None
    try:
        if body.strip().startswith("{"):
            payload = json.loads(body)
    except Exception:
        payload = None
    result = (payload or {}).get("result")
    level = "info"
    if isinstance(payload, dict):
        if result in _ERROR_RESULTS:
            level = "err"
        elif payload.get("needs_attention"):
            level = "warn"
        else:
            level = "ok"
    report = (payload or {}).get("report") or ""
    return {
        "time": time_str,
        "date": time_str[:10],
        "result": result,
        "level": level,
        "report": report,
        "msg": tidy_text(report),
        "credit": (payload or {}).get("today_credit"),
        "total": (payload or {}).get("total_credits"),
        "streak": (payload or {}).get("streak_days"),
        "text": line,
    }


def read_log_tail(max_lines=200, keyword=""):
    if not os.path.exists(LOG):
        return {"ok": True, "exists": False, "size": 0, "mtime": None, "lines": []}
    size = os.path.getsize(LOG)
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(LOG)).strftime("%Y-%m-%d %H:%M")
    try:
        with open(LOG, "rb") as f:
            if size > 262144:
                f.seek(-262144, os.SEEK_END)
                data = f.read()
                nl = data.find(b"\n")
                if nl >= 0:
                    data = data[nl + 1:]
            else:
                data = f.read()
    except Exception as e:
        return {"ok": False, "error": str(e), "exists": True, "size": size,
                "mtime": mtime, "lines": []}
    raw_lines = [ln for ln in decode(data).splitlines() if ln.strip()]
    if keyword:
        kw = keyword.lower()
        raw_lines = [ln for ln in raw_lines if kw in ln.lower()]
    try:
        n = max(1, min(2000, int(max_lines or 200)))
    except Exception:
        n = 200
    raw_lines = raw_lines[-n:]
    return {"ok": True, "exists": True, "size": size, "mtime": mtime,
            "lines": [_parse_log_line(ln) for ln in raw_lines]}


def _read_log_records(limit=5000):
    """读日志并解析为记录（供收益/趋势使用）。"""
    if not os.path.exists(LOG):
        return []
    try:
        with open(LOG, "rb") as f:
            size = os.path.getsize(LOG)
            if size > 1048576:
                f.seek(-1048576, os.SEEK_END)
                data = f.read()
                nl = data.find(b"\n")
                if nl >= 0:
                    data = data[nl + 1:]
            else:
                data = f.read()
    except Exception:
        return []
    lines = [ln for ln in decode(data).splitlines() if ln.strip()]
    return [_parse_log_line(ln) for ln in lines[-limit:]]


_BILL_HINT = re.compile(r"旅行|Buddy|盲盒|礼物|签到|已签|补登|兑换|连签|门票|能量|积分|抽奖|补签", re.I)
_GAIN = re.compile(r"[+＋]\s*\d+|\d+\s*(积分|能量|天|次|张|卡|抽奖)")


def _split_bill(report):
    """把 report 拆成可读的收益条目。"""
    if not report:
        return []
    out, seen = [], set()
    for t in re.split(r"[；;，,、\n（(]+", report):
        t = t.strip().strip("）)】【[] “”\"' ")
        if not t:
            continue
        if _GAIN.search(t) or _BILL_HINT.search(t):
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out[:12]


def build_insights():
    """汇总：今日/本周/本月/累计、近 14 天趋势、今日收益账单。"""
    store = history.load_store()
    today = datetime.date.today()
    today_str = today.isoformat()

    def row(d):
        v = store.get(d)
        return v if isinstance(v, dict) else {}

    def credit_of(d):
        return int(row(d).get("credit") or 0)

    today_credit = credit_of(today_str)
    week = sum(credit_of((today - datetime.timedelta(days=i)).isoformat()) for i in range(7))
    month = sum(credit_of((today - datetime.timedelta(days=i)).isoformat()) for i in range(30))

    latest_total = latest_streak = None
    for d in sorted(store.keys(), reverse=True):
        v = row(d)
        if latest_total is None and v.get("total") is not None:
            latest_total = v["total"]
        if latest_streak is None and v.get("streak") is not None:
            latest_streak = v["streak"]
        if latest_total is not None and latest_streak is not None:
            break
    # 历史里缺 total/streak 时，从日志回填
    if latest_total is None or latest_streak is None:
        for r in reversed(_read_log_records()):
            if latest_total is None and r.get("total") is not None:
                latest_total = r["total"]
            if latest_streak is None and r.get("streak") is not None:
                latest_streak = r["streak"]
            if latest_total is not None and latest_streak is not None:
                break

    trend = []
    for i in range(13, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        v = row(d)
        trend.append({
            "date": d,
            "credit": int(v.get("credit") or 0),
            "total": v.get("total"),
            "is_today": d == today_str,
        })

    report = row(today_str).get("report") or ""
    source = None
    if not report:
        for r in reversed(_read_log_records()):
            if r.get("date") == today_str and r.get("level") == "ok" and r.get("report"):
                report = r["report"]
                source = r.get("time")
                break

    return {
        "ok": True,
        "today": today_str,
        "summary": {
            "today": today_credit,
            "week": week,
            "month": month,
            "total": latest_total,
            "streak": latest_streak,
            "days": len(store),
        },
        "trend": trend,
        "bill": _split_bill(report),
        "bill_report": report,
        "bill_source": source,
    }


def _status_probe(timeout=25):
    """跑一次只读 status，用于验证登录态与网络。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONLEGACYWINDOWSSTDIO="")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.run([PY, SIGNIN, "status"], cwd=HERE, capture_output=True,
                          timeout=timeout, env=env, creationflags=flags)
    raw = decode(proc.stdout).strip()
    data = None
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    kind, result = classify(data, proc.returncode)
    return {"ok": kind == "success", "kind": kind, "result": result,
            "data": data, "raw": raw}


def run_health(deep=True):
    """一键体检：返回结构化检查项。单项失败不影响其余。"""
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    if os.path.exists(scheduler.PYTHONW):
        add("Python 解释器", "ok", scheduler.PYTHONW)
    else:
        add("Python 解释器", "fail", "未找到 pythonw.exe：%s" % scheduler.PYTHONW)

    if os.path.exists(SIGNIN):
        add("签到脚本", "ok", os.path.basename(SIGNIN) + " 就位")
    else:
        add("签到脚本", "fail", "缺少 %s" % SIGNIN)

    if deep:
        try:
            p = _status_probe()
            res = p.get("result")
            if p.get("ok") or res in ("ALREADY", "AUTH_READY", "GROWTH", "CLAIMED", "INACTIVE"):
                add("凭据 / 网络", "ok", "登录态有效%s" % (("（%s）" % res) if res else ""))
            elif res in ("NETWORK", "TIMEOUT"):
                add("凭据 / 网络", "warn", "网络不通或超时（%s）" % res)
            elif res == "AUTH_BUSY":
                add("凭据 / 网络", "fail",
                    "%s（%s）" % (p.get("report") or "凭据文件读不到",
                                  p.get("reason") or "AUTH_BUSY"))
            elif res in ("NO_AUTH", "AUTH_ERROR", "AUTH_REJECTED", "FORBIDDEN"):
                add("凭据 / 网络", "fail", "登录态无效，请在桌面端重新登录（%s）" % res)
            else:
                add("凭据 / 网络", "warn", "状态未知（%s）" % (res or "无输出"))
        except subprocess.TimeoutExpired:
            add("凭据 / 网络", "warn", "检测超时（>25s）")
        except Exception as e:
            add("凭据 / 网络", "warn", "检测失败：%s" % e)
    else:
        add("凭据 / 网络", "info", "已跳过（快速模式）")

    try:
        tasks = scheduler.list_tasks()
        enabled = [t for t in tasks if t.get("enabled")]
        if tasks:
            add("计划任务", "ok" if enabled else "warn",
                "共 %d 个，启用 %d 个：%s" % (len(tasks), len(enabled),
                                              "、".join(t["name"] for t in tasks)))
        else:
            add("计划任务", "warn", "还没有 WorkBuddy 定时任务")
    except Exception as e:
        add("计划任务", "fail", "读取失败：%s" % e)

    try:
        cfg = notify.load_config()
        if cfg.get("enabled"):
            add("推送通知", "ok", "已启用（%s）" % cfg.get("channel"))
        elif cfg.get("webhook_url") or (cfg.get("email") or {}).get("host"):
            add("推送通知", "warn", "已配置但未启用")
        else:
            add("推送通知", "warn", "未配置（不影响签到）")
    except Exception as e:
        add("推送通知", "warn", "读取失败：%s" % e)

    try:
        if os.path.exists(LOG):
            mt = datetime.datetime.fromtimestamp(os.path.getmtime(LOG))
            age = (datetime.datetime.now() - mt).total_seconds() / 3600
            if age <= 26:
                add("签到日志", "ok", "最近记录：%s" % mt.strftime("%Y-%m-%d %H:%M"))
            else:
                add("签到日志", "warn", "最近记录在 %.1f 小时前，可能没在跑" % age)
        else:
            add("签到日志", "warn", "还没有 signin.log")
    except Exception as e:
        add("签到日志", "warn", str(e))

    # 心跳比日志更可信：日志空跑不写、会被清理，心跳每次运行必写。
    # 分两项：任意运行（展示用）与定时运行（断档判定用）。
    try:
        import heartbeat
        st = heartbeat.status()
        if not st["exists"]:
            add("运行心跳", "warn", "还没有心跳记录（下一次运行后生成）")
        else:
            add("运行心跳", "ok",
                "%.1f 小时前 · %s · 模式 %s"
                % (st["age_hours"], st["result"] or "—", st["mode"] or "—"))
        sch = st.get("scheduled") or {}
        if not sch.get("exists"):
            add("定时心跳", "warn", "还没有定时任务的心跳（下一次定时运行后生成）")
        elif sch.get("stale"):
            add("定时心跳", "fail",
                "定时任务已 %.1f 小时没跑（最后一次 %s，结果 %s）；"
                "检查计划任务是否被禁用、解释器路径是否有效"
                % (sch["age_hours"], sch["at"], sch["result"] or "未知"))
        else:
            add("定时心跳", "ok",
                "%.1f 小时前 · %s" % (sch["age_hours"], sch["result"] or "—"))
    except Exception as e:
        add("运行心跳", "warn", "读取失败：%s" % e)

    try:
        hs = history.status()
        if hs["corrupt_backups"]:
            add("签到历史", "warn", "检测到 %d 个损坏备份（已自动隔离，当前 %d 天记录）"
                % (hs["corrupt_backups"], hs["days"]))
        elif hs["days"]:
            add("签到历史", "ok", "%d 天记录 · 最近 %s" % (hs["days"], hs["latest"]))
        else:
            add("签到历史", "warn", "还没有历史记录（下一次签到后生成）")
    except Exception as e:
        add("签到历史", "warn", "读取失败：%s" % e)

    try:
        ar = archive.status()
        if ar["reports"]:
            add("报告归档", "ok", "%d 份报告 · %d 份原始凭证 · 最近 %s"
                % (ar["reports"], ar["evidence"], ar["latest"] or "—"))
        else:
            add("报告归档", "warn", "还没有归档（下次生成报告后自动保存）")
    except Exception as e:
        add("报告归档", "warn", "读取失败：%s" % e)

    try:
        ms = monitor.scan(limit=10)
        st = ms.get("state")
        if st == "drift":
            add("接口契约", "fail", "疑似接口失效：%s（连续 %d 次异常）"
                % ("；".join(ms.get("signals", [])[:2]) or "结构异常",
                   ms.get("consecutive_failures", 0)))
        elif st == "watch":
            add("接口契约", "warn", "最近有异常但可归因，持续观察（%s）"
                % ("；".join(ms.get("signals", [])[:2]) or "见监测卡片"))
        else:
            add("接口契约", "ok", "最近 %d 次运行契约一致"
                % ms.get("counts", {}).get("total", 0))
    except Exception as e:
        add("接口契约", "warn", "检测失败：%s" % e)

    fails = sum(1 for c in checks if c["status"] == "fail")
    warns = sum(1 for c in checks if c["status"] == "warn")
    passed = len(checks) - fails - warns
    overall = "fail" if fails else ("warn" if warns else "ok")
    return {
        "ok": overall != "fail",
        "status": overall,
        "checks": checks,
        "summary": "%d 项通过 · %d 项提醒 · %d 项异常" % (passed, warns, fails),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _guard(self):
        """校验 Host / Origin，防 DNS rebinding 与跨站请求。

        只认"主机是不是本机回环"，端口不限（预览代理/端口转发会改 Host 端口）。
        Origin 为 null 仅在只读 GET 时放行；写操作仍要求带 X-WB-Token。
        """
        host = (self.headers.get("Host") or "").lower()
        if not _host_ok(host):
            self._deny("forbidden host")
            return False
        origin = self.headers.get("Origin")
        if origin:
            o = origin.strip().lower()
            is_get = (self.command or "GET").upper() == "GET"
            if not _origin_ok(o) or (o == "null" and not is_get):
                self._deny("forbidden origin")
                return False
        return True

    def _deny(self, reason):
        """拒绝时给一个看得懂的页面，而不是让浏览器显示空白/JSON。"""
        path = urlparse(self.path).path
        if (self.command or "GET").upper() == "GET" and path in ("/", "/index.html"):
            html = (
                "<!DOCTYPE html><meta charset='utf-8'>"
                "<title>WorkBuddy 面板 · 访问被拒绝</title>"
                "<body style=\"font:15px/1.7 system-ui,'Microsoft YaHei';padding:40px;"
                "background:#f5f7fa;color:#1f2933\">"
                "<h2 style='margin:0 0 12px'>⚠ 访问被拒绝（%s）</h2>"
                "<p>这个面板只允许在本机通过回环地址访问。请改用下面的地址打开：</p>"
                "<p style='font-size:16px'><a href='http://127.0.0.1:%d/'>"
                "http://127.0.0.1:%d/</a></p>"
                "<p style='color:#5b6672'>如果点上面的链接仍打不开，说明面板服务没在运行。"
                "双击桌面上的「WorkBuddy面板」快捷方式即可自动启动。</p>"
                "</body>" % (reason, PORT, PORT)
            ).encode("utf-8")
            self._send(403, html, "text/html; charset=utf-8")
        else:
            self._json({"ok": False, "error": reason})

    def _token_ok(self):
        return (self.headers.get("X-WB-Token") or "") == SERVER_TOKEN

    def _require_token(self):
        if self._token_ok():
            return True
        self._json({"ok": False, "error": "invalid or missing token"})
        return False

    def do_GET(self):
        if not self._guard():
            return
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            try:
                with open(HTML, "r", encoding="utf-8") as f:
                    body = f.read().replace("__WB_TOKEN__", SERVER_TOKEN).encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, "test-dashboard.html not found".encode("utf-8"))
            return
        if parsed.path == "/run":
            if not self._require_token():
                return
            mode = parse_qs(parsed.query).get("mode", ["auto"])[0]
            self._json(run_signin(mode))
            return
        if parsed.path == "/tasks":
            try:
                self._json({"ok": True, "tasks": scheduler.list_tasks(),
                            "presets": scheduler.PRESETS})
            except Exception as e:
                self._json({"ok": False, "error": str(e), "tasks": []})
            return
        if parsed.path == "/notify":
            try:
                self._json({"ok": True, "config": notify.public_config()})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if parsed.path == "/logs":
            qs = parse_qs(parsed.query)
            try:
                lines = int(qs.get("lines", ["200"])[0])
            except Exception:
                lines = 200
            keyword = qs.get("q", [""])[0]
            try:
                self._json(read_log_tail(lines, keyword))
            except Exception as e:
                self._json({"ok": False, "error": str(e), "lines": []})
            return
        if parsed.path == "/health":
            if not self._require_token():
                return
            deep = parse_qs(parsed.query).get("deep", ["1"])[0].lower() not in ("0", "false", "no")
            try:
                self._json(run_health(deep))
            except Exception as e:
                self._json({"ok": False, "error": str(e), "checks": []})
            return
        if parsed.path == "/insights":
            try:
                self._json(build_insights())
            except Exception as e:
                self._json({"ok": False, "error": str(e), "summary": {}, "trend": [], "bill": []})
            return
        if parsed.path == "/reports":
            try:
                qs = parse_qs(parsed.query)
                try:
                    limit = max(1, min(200, int(qs.get("limit", ["60"])[0])))
                except Exception:
                    limit = 60
                self._json({"ok": True, "reports": archive.list_reports(limit),
                            "status": archive.status()})
            except Exception as e:
                self._json({"ok": False, "error": str(e), "reports": []})
            return
        if parsed.path == "/milestones":
            try:
                self._json(milestone.compute())
            except Exception as e:
                self._json({"ok": False, "error": str(e), "milestones": [], "next": None})
            return
        if parsed.path == "/monitor":
            try:
                self._json(monitor.scan())
            except Exception as e:
                self._json({"ok": False, "error": str(e), "state": "unknown",
                            "state_text": "检测失败", "signals": [], "advice": [], "runs": []})
            return
        if parsed.path in ("/report", "/evidence"):
            qs = parse_qs(parsed.query)
            name = qs.get("name", [""])[0]
            if parsed.path == "/report":
                p = archive.open_report(name)
                ctype = "text/html; charset=utf-8"
            else:
                p = archive.open_evidence(name)
                ctype = "application/json; charset=utf-8"
            if not p:
                self._send(404, "not found".encode("utf-8"), "text/plain; charset=utf-8")
                return
            try:
                with open(p, "rb") as f:
                    self._send(200, f.read(), ctype)
            except Exception as e:
                self._send(500, str(e).encode("utf-8"), "text/plain; charset=utf-8")
            return
        self._send(404, b"not found")

    def do_POST(self):
        if not self._guard():
            return
        if not self._require_token():
            return
        path = urlparse(self.path).path
        if path not in ("/tasks", "/notify", "/notify/test"):
            self._send(404, b"not found")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError("请求体应为 JSON 对象")
        except Exception as e:
            self._json({"ok": False, "error": "请求体解析失败：%s" % e, "tasks": _empty_tasks()})
            return
        if path == "/tasks":
            self._handle_task_action(body)
            return
        if path == "/notify":
            try:
                cfg = notify.save_config(body)
                self._json({"ok": True, "config": notify.public_config(cfg)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        # /notify/test：先合并保存（脱敏值会被忽略），再用最终配置发一条测试
        try:
            cfg = notify.save_config(body) if body else notify.load_config()
            res = notify.send_test(cfg)
            self._json({"ok": bool(res.get("ok")), "result": res,
                        "config": notify.public_config(cfg)})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})
            return

    def _handle_task_action(self, body):
        action = (body.get("action") or "").lower()
        try:
            if action in ("create", "update", "upsert"):
                preset = body.get("preset")
                if preset and preset in scheduler.PRESETS:
                    p = scheduler.PRESETS[preset]
                    name, times, mode, desc = p["name"], p["times"], p["mode"], p["description"]
                    runner = p.get("runner", "signin")
                else:
                    name = (body.get("name") or "WorkBuddyAutoSignin").strip()
                    times = body.get("times")
                    if not times:
                        times = [body.get("time") or "00:05"]
                    if isinstance(times, str):
                        times = [times]
                    mode = body.get("mode") or "silent"
                    desc = body.get("description") or ""
                    runner = body.get("runner") or "signin"
                result = scheduler.create_or_update(name, times, mode, desc, runner)
            elif action == "notify_all":
                updated, failed = [], []
                for t in scheduler.list_tasks():
                    times = [x for x in (t.get("times") or []) if isinstance(x, str) and ":" in x]
                    if not times:
                        failed.append("%s(无有效时间点)" % t["name"])
                        continue
                    try:
                        scheduler.create_or_update(t["name"], times, t.get("mode") or "silent",
                                                   "WorkBuddy（跑完推送）", "notify")
                        updated.append(t["name"])
                    except Exception as e2:
                        failed.append("%s(%s)" % (t["name"], e2))
                result = {"updated": updated, "failed": failed}
            elif action == "delete":
                result = scheduler.delete_task(body.get("name"))
            elif action == "run":
                result = scheduler.run_task(body.get("name"))
            elif action == "enable":
                result = scheduler.set_enabled(body.get("name"), True)
            elif action == "disable":
                result = scheduler.set_enabled(body.get("name"), False)
            else:
                raise ValueError("未知操作：%r" % action)
            self._json({"ok": True, "result": result, "tasks": _empty_tasks()})
        except Exception as e:
            self._json({"ok": False, "error": str(e), "tasks": _empty_tasks()})

    def log_message(self, *args):
        pass  # 静默


if __name__ == "__main__":
    if not os.path.exists(PY):
        print("未找到 pythonw/python：", PY, file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(SIGNIN):
        print("未找到 signin.py：", SIGNIN, file=sys.stderr)
        sys.exit(1)
    print(f"测试面板已启动：http://127.0.0.1:{PORT}  (Ctrl+C 退出)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
