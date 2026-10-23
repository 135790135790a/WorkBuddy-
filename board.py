#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board.py —— 「签到总览」静态页的生成逻辑（入口是同名的 history-page.py）。

为什么是静态页而不是再加一个服务：
    面板（8765）是给维护者用的，要起进程、要看日志、要动计划任务。
    使用者真正想要的是"打开就能看我这些天跑得怎么样"，那就应该是一个
    双击即可打开的 HTML：不需要端口、不需要服务在跑、可以发给别人看。

页面内容（全部来自已有数据，不新增采集）：
    - 四个 KPI：连续天数 / 累计积分 / 今日状态 / 今日成功率
    - 最近 8 周热力图：有成功=绿、只有失败=红、无运行记录=灰（如实标注）
    - 累计积分曲线：点少于两个时明确显示"数据不足"，不画假线
    - 结果分布与最近运行明细（可按成功/失败筛选）
    - 里程碑进度

调用方式：
    board.write()            -> (ok, 说明)
    入口脚本 history-page.py   给人和托盘用（可 --open）
    notify-run.py 每次跑完会自动重写一次，页面始终是最新的
"""
import datetime
import html
import os

import archive
import heartbeat
import history
import milestone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "history.html")
HEAT_WEEKS = 8
RECENT_ROWS = 20

OK_RESULTS = {"ALREADY", "CLAIMED", "GROWTH", "AUTH_READY", "INACTIVE"}


def _esc(v):
    return html.escape(str(v if v is not None else ""), quote=True)


def _short(result):
    """结果码 → 人话（未知码原样显示，不要藏起来）。"""
    return {
        "ALREADY": "今日已签", "CLAIMED": "签到成功", "GROWTH": "成长中心",
        "INACTIVE": "非签到季", "AUTH_READY": "凭据就绪", "OK": "签到成功",
        "ERROR": "运行出错", "AUTH_ERROR": "登录态失效", "AUTH_REJECTED": "认证被拒",
        "AUTH_BUSY": "凭据被占用", "NO_AUTH": "未找到凭据", "FORBIDDEN": "权限不足",
        "NETWORK": "网络异常", "TIMEOUT": "请求超时", "UNKNOWN": "未知返回",
        "SKIPPED": "已跳过",
    }.get(result or "", result or "—")


def _is_ok(item):
    if item.get("ok") is not None:
        return bool(item.get("ok"))
    return (item.get("result") or "") in OK_RESULTS


def collect(now=None):
    now = now or datetime.datetime.now()
    today = now.date()
    try:
        items = archive.list_reports(400) or []
    except Exception:
        items = []
    try:
        days = history.load_store() or {}
    except Exception:
        days = {}
    try:
        ms = milestone.compute() or {}
    except Exception:
        ms = {}
    try:
        hb = heartbeat.status()
    except Exception:
        hb = {}

    today_items = [i for i in items if str(i.get("date") or (i.get("ts") or "")[:10]) == str(today)]
    ok_today = sum(1 for i in today_items if _is_ok(i))
    fail_today = len(today_items) - ok_today

    dist = {}
    for i in items:
        key = i.get("result") or ("OK" if _is_ok(i) else "UNKNOWN")
        dist[key] = dist.get(key, 0) + 1

    per_day = {}
    for i in items:
        d = str(i.get("date") or (i.get("ts") or "")[:10])
        if not d:
            continue
        slot = per_day.setdefault(d, {"ok": 0, "fail": 0, "credit": None, "total": None})
        if _is_ok(i):
            slot["ok"] += 1
        else:
            slot["fail"] += 1
        if slot["total"] is None and i.get("total") is not None:
            slot["total"] = i.get("total")
        if slot["credit"] is None and i.get("credit") is not None:
            slot["credit"] = i.get("credit")
    for d, v in days.items():
        slot = per_day.setdefault(str(d), {"ok": 0, "fail": 0, "credit": None, "total": None})
        if not slot["ok"] and not slot["fail"]:
            slot["ok"] = 1          # 历史里有记录但归档已清理：算已签
        if slot["total"] is None and isinstance(v, dict):
            slot["total"] = v.get("total")
        if slot["credit"] is None and isinstance(v, dict):
            slot["credit"] = v.get("credit")

    return {
        "now": now,
        "today": str(today),
        "items": items,
        "days": days,
        "per_day": per_day,
        "streak": int(ms.get("streak") or 0),
        "best": int(ms.get("best") or 0),
        "next": ms.get("next") or {},
        "signed_today": bool(ms.get("today_signed")),
        "total_credits": next((i.get("total") for i in items if i.get("total") is not None), None),
        "today_runs": len(today_items),
        "today_ok": ok_today,
        "today_fail": fail_today,
        "dist": dist,
        "heartbeat": hb,
    }


# ---------------- 组件 ----------------
def _kpi(label, value, sub="", tone=""):
    return ('<div class="kpi %s"><div class="k-label">%s</div>'
            '<div class="k-value">%s</div><div class="k-sub">%s</div></div>'
            % (tone, _esc(label), _esc(value), _esc(sub)))


def _heatmap(per_day, today):
    """最近 N 周，按周排列的日历热力图。没有运行记录的日子标灰并说明。"""
    start = today - datetime.timedelta(days=today.weekday())
    start -= datetime.timedelta(weeks=HEAT_WEEKS - 1)
    cells = []
    for w in range(HEAT_WEEKS):
        for d in range(7):
            day = start + datetime.timedelta(weeks=w, days=d)
            key = str(day)
            info = per_day.get(key)
            if info is None:
                cls, title = "none", "%s · 无运行记录" % key
            elif info["ok"]:
                cls = "ok2" if info["ok"] >= 3 else "ok"
                title = "%s · 成功 %d 次%s" % (
                    key, info["ok"], ("，失败 %d 次" % info["fail"]) if info["fail"] else "")
            else:
                cls, title = "bad", "%s · 失败 %d 次" % (key, info["fail"])
            if day > today:
                cls = "future"
                title = "%s · 未到" % key
            cells.append('<div class="cell %s" title="%s"></div>' % (cls, _esc(title)))
    legend = ('<div class="legend"><span><i class="cell ok"></i>有成功</span>'
              '<span><i class="cell ok2"></i>多次</span>'
              '<span><i class="cell bad"></i>只有失败</span>'
              '<span><i class="cell none"></i>无记录</span>'
              '<span class="range">%s ~ %s</span></div>'
              % (start.isoformat(), today.isoformat()))
    labels = "".join("<span>%s</span>" % x for x in ("一", "", "三", "", "五", "", "日"))
    return ('<div class="heat-wrap"><div class="heat-labels">%s</div>'
            '<div class="heat">%s</div></div>%s' % (labels, "".join(cells), legend))


def _spark(per_day):
    """累计积分曲线。少于两个不同取值就不画，避免一条假平线。"""
    pts = [(d, per_day[d]["total"]) for d in sorted(per_day)
           if isinstance(per_day[d].get("total"), (int, float))]
    if len({v for _, v in pts}) < 2:
        return ('<div class="empty">积分曲线需要多天数据。目前累计值没有变化，'
                '等积累几天后这里会显示趋势。</div>')
    width, height, pad = 960, 220, 28
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    span = max(1, hi - lo)
    step = (width - pad * 2) / max(1, len(pts) - 1)
    coords = []
    for i, (_, v) in enumerate(pts):
        x = pad + i * step
        y = height - pad - (v - lo) / span * (height - pad * 2)
        coords.append((x, y))
    line = " ".join("%.1f,%.1f" % c for c in coords)
    area = "%s %.1f,%d %.1f,%d" % (line, coords[-1][0], height - pad, coords[0][0], height - pad)
    dots = "".join('<circle cx="%.1f" cy="%.1f" r="4" fill="#12996a"/>' % c for c in coords)
    return ('<svg class="chart" viewBox="0 0 %d %d" preserveAspectRatio="none">'
            '<polygon points="%s" fill="rgba(18,153,106,.12)"/>'
            '<polyline points="%s" fill="none" stroke="#12996a" stroke-width="2.5" '
            'stroke-linejoin="round"/>%s</svg>'
            '<div class="axis"><span>%s · %s</span><span>%s · %s</span></div>'
            % (width, height, area, line, dots,
               _esc(pts[0][0]), _esc(pts[0][1]), _esc(pts[-1][0]), _esc(pts[-1][1])))


def _dist_bars(dist, total):
    if not dist:
        return '<div class="empty">还没有运行记录。</div>'
    rows = []
    for key, n in sorted(dist.items(), key=lambda kv: -kv[1]):
        pct = (n / total * 100) if total else 0
        cls = "ok" if key in OK_RESULTS else "bad"
        rows.append('<div class="bar-row"><span class="bar-name">%s</span>'
                    '<span class="bar-track"><i class="%s" style="width:%.1f%%"></i></span>'
                    '<span class="bar-num">%d</span></div>'
                    % (_esc(_short(key)), cls, max(2, pct), n))
    return "".join(rows)


def _rows(items):
    out = []
    for i in items:
        ok = _is_ok(i)
        cls = "ok" if ok else "bad"
        mode = i.get("mode") or "—"
        who = {"silent": "定时", "silent-poll": "轮询", "auto": "手动", "growth": "成长"}.get(mode, mode)
        retry = "" if (i.get("attempts") or 1) <= 1 else "重试 %d 次" % (i.get("attempts") - 1)
        credit = i.get("credit")
        if i.get("has_report") and i.get("file"):
            link = '<a class="link" href="reports/%s" target="_blank">看报告</a>' % _esc(i["file"])
        elif i.get("has_evidence"):
            link = '<span class="muted">仅凭证</span>'
        else:
            link = "—"
        out.append(
            '<tr class="row %s" data-ok="%d"><td class="mono">%s</td><td>%s</td>'
            '<td><span class="pill %s">%s</span></td><td class="muted">%s</td>'
            '<td class="mono">%s</td><td>%s</td></tr>'
            % (cls, 1 if ok else 0, _esc((i.get("ts") or "")[5:16]), _esc(who), cls,
               _esc(_short(i.get("result"))), _esc(retry),
               _esc(("+%s" % credit) if credit else "—"), link))
    return "".join(out)


def _milestone(next_ms, streak):
    if not next_ms:
        return '<div class="empty">暂无里程碑数据。</div>'
    pct = max(2, min(100, (next_ms.get("progress") or 0) * 100))
    return ('<div class="ms"><div class="ms-head"><b>%s</b>'
            '<span>还差 %s 天 · 预计 %s</span></div>'
            '<div class="bar-track"><i class="ok" style="width:%.1f%%"></i></div>'
            '<div class="ms-foot">当前连续 %d 天</div></div>'
            % (_esc(next_ms.get("label") or "里程碑"), _esc(next_ms.get("remaining")),
               _esc(next_ms.get("eta") or "—"), pct, streak))


def _insight(d):
    """一句人话总结。使用者的第一眼应该看这里，而不是从图里自己找。"""
    parts = []
    if d["today_runs"]:
        if d["today_fail"] == 0:
            parts.append("今天运行 %d 次，全部正常" % d["today_runs"])
        else:
            parts.append("今天运行 %d 次，其中 %d 次失败" % (d["today_runs"], d["today_fail"]))
    else:
        parts.append("今天还没有运行记录")
    if d["signed_today"]:
        parts.append("签到已完成")
    else:
        parts.append("今天尚未签到")
    if d["streak"]:
        parts.append("连续 %d 天" % d["streak"])
        if d["best"] and d["best"] > d["streak"]:
            parts.append("距历史最佳还差 %d 天" % (d["best"] - d["streak"]))
    hb = d.get("heartbeat") or {}
    sch = hb.get("scheduled") or {}
    if sch.get("exists"):
        parts.append("上次定时运行 %s" % (sch.get("at") or "")[5:16])
        if sch.get("stale"):
            parts.append("定时任务已停跑 %.0f 小时" % (sch.get("age_hours") or 0))
    return " · ".join(parts)


CSS = """
:root{--bg:#f4f8f6;--card:#fff;--ink:#12332a;--muted:#6b8f80;--line:#dcebe3;
--ok:#12996a;--bad:#d9534f;--soft:#eaf7f0}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;
padding:40px 20px 64px}
.wrap{max-width:1040px;margin:0 auto}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin-bottom:24px}
h1{font-size:26px;font-weight:700;letter-spacing:.5px}
.sub{color:var(--muted);font-size:13px;margin-top:6px}
.badge{background:var(--soft);color:var(--ok);border:1px solid #bfe6d4;border-radius:999px;
padding:7px 14px;font-size:13px;font-weight:600}
.badge.off{color:var(--bad);background:#fdeeee;border-color:#f3c9c7}
.insight{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:14px 18px;font-size:14px;color:#245043;margin-bottom:20px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.k-label{font-size:12px;color:var(--muted);letter-spacing:1px}
.k-value{font-size:32px;font-weight:700;margin-top:8px;line-height:1}
.k-sub{font-size:12px;color:var(--muted);margin-top:8px}
.kpi.warn .k-value{color:var(--bad)}
.kpi.good .k-value{color:var(--ok)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:20px;margin-bottom:18px}
h2{font-size:15px;font-weight:600;margin-bottom:16px;display:flex;justify-content:space-between}
h2 small{font-weight:400;color:var(--muted);font-size:12px}
.heat-wrap{display:flex;gap:10px;align-items:flex-start}
.heat-labels{display:grid;grid-template-rows:repeat(7,20px);gap:5px;font-size:11px;
color:var(--muted);line-height:20px;width:14px;text-align:right}
.heat{display:grid;grid-template-rows:repeat(7,20px);grid-auto-flow:column;
grid-auto-columns:20px;gap:5px}
.cell{width:20px;height:20px;border-radius:4px;background:#eef4f0}
.legend .range{margin-left:auto}
.cell.ok{background:#8fd9b6}.cell.ok2{background:var(--ok)}
.cell.bad{background:var(--bad)}.cell.none{background:#eef4f0}
.cell.future{background:repeating-linear-gradient(45deg,#f6faf8,#f6faf8 3px,#edf3ef 3px,#edf3ef 6px)}
.legend{display:flex;gap:18px;margin-top:14px;font-size:12px;color:var(--muted);flex-wrap:wrap}
.legend span{display:flex;align-items:center;gap:7px}
.legend i{width:13px;height:13px;display:inline-block;border-radius:4px}
.chart{width:100%;height:200px;margin-top:6px}
.axis{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-top:8px}
.bar-row{display:flex;align-items:center;gap:12px;margin-bottom:10px;font-size:13px}
.bar-name{width:96px;color:#2c5c4b}
.bar-track{flex:1;height:10px;background:#eef4f0;border-radius:99px;overflow:hidden}
.bar-track i{display:block;height:100%;border-radius:99px}
.bar-track i.ok{background:var(--ok)}.bar-track i.bad{background:var(--bad)}
.bar-num{width:44px;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:500;font-size:12px;padding:8px 10px;
border-bottom:1px solid var(--line)}
td{padding:9px 10px;border-bottom:1px solid #f0f6f3;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr.hide{display:none}
.mono{font-variant-numeric:tabular-nums;color:#3b6a58}
.link{color:var(--ok);text-decoration:none;border-bottom:1px solid #bfe6d4}
.link:hover{color:#0b7a54}
.muted{color:var(--muted)}
.pill{border-radius:6px;padding:3px 8px;font-size:12px;white-space:nowrap}
.pill.ok{background:var(--soft);color:var(--ok)}
.pill.bad{background:#fdeeee;color:var(--bad)}
.chips{display:flex;gap:8px}
.chip{border:1px solid var(--line);background:#fff;color:var(--muted);border-radius:999px;
padding:5px 12px;font-size:12px;cursor:pointer}
.chip.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.ms-head{display:flex;justify-content:space-between;font-size:13px;margin-bottom:10px}
.ms-foot{font-size:12px;color:var(--muted);margin-top:8px}
.empty{color:var(--muted);font-size:13px;padding:18px 0;text-align:center}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:26px}
@media (max-width:760px){.kpis{grid-template-columns:repeat(2,1fr)}}
"""

JS = """
document.querySelectorAll('.chip').forEach(function(c){
  c.addEventListener('click', function(){
    document.querySelectorAll('.chip').forEach(function(x){x.classList.remove('on')});
    c.classList.add('on');
    var want = c.dataset.f;
    document.querySelectorAll('tr.row').forEach(function(r){
      var ok = r.dataset.ok === '1';
      r.classList.toggle('hide', want !== 'all' && ((want === 'ok') !== ok));
    });
  });
});
"""


def render(d):
    items = d["items"][:RECENT_ROWS]
    total_runs = len(d["items"])
    badge = ('<div class="badge">今日已签</div>' if d["signed_today"]
             else '<div class="badge off">今日未签</div>')
    kpis = "".join([
        _kpi("连续天数", "%d 天" % d["streak"],
             ("历史最佳 %d 天" % d["best"]) if d["best"] else "", "good" if d["streak"] else ""),
        _kpi("累计积分", "{:,}".format(d["total_credits"]) if d["total_credits"] is not None else "—",
             "来自最近一次运行报告"),
        _kpi("今日运行", "%d 次" % d["today_runs"],
             "成功 %d · 失败 %d" % (d["today_ok"], d["today_fail"]),
             "warn" if d["today_fail"] else "good"),
        _kpi("今日状态", "已签" if d["signed_today"] else "未签",
             "自动任务 00:05 起轮询", "good" if d["signed_today"] else "warn"),
    ])
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WorkBuddy 签到总览</title><style>%s</style></head>
<body><div class="wrap">
<header>
  <div><h1>WorkBuddy 签到总览</h1>
  <div class="sub">生成于 %s · 数据来自本地运行记录与归档</div></div>
  %s
</header>
<div class="insight">%s</div>
<div class="kpis">%s</div>

<div class="card"><h2>最近 %d 周 <small>悬停看每天的运行情况</small></h2>%s</div>

<div class="card"><h2>累计积分走势 <small>按天记录</small></h2>%s</div>

<div class="card"><h2>结果分布 <small>共 %d 次运行</small></h2>%s</div>

<div class="card"><h2>最近运行 <small>显示 %d / %d 条</small></h2>
  <div class="chips" style="margin-bottom:12px">
    <button class="chip on" data-f="all">全部</button>
    <button class="chip" data-f="ok">成功</button>
    <button class="chip" data-f="bad">失败</button>
  </div>
  <table><thead><tr><th>时间</th><th>触发</th><th>结果</th><th>重试</th><th>积分</th><th>记录</th></tr></thead>
  <tbody>%s</tbody></table>
</div>

<div class="card"><h2>里程碑</h2>%s</div>

<footer>本页由 history-page.py 生成，数据全部在本地，不会上传。</footer>
</div><script>%s</script></body></html>""" % (
        CSS, _esc(d["now"].strftime("%Y-%m-%d %H:%M")), badge, _esc(_insight(d)), kpis,
        HEAT_WEEKS, _heatmap(d["per_day"], d["now"].date()),
        _spark(d["per_day"]),
        total_runs, _dist_bars(d["dist"], total_runs),
        len(items), total_runs, _rows(items),
        _milestone(d["next"], d["streak"]), JS)


def write(now=None):
    """生成 history.html。返回 (ok, 说明)。绝不抛异常——它常被塞在签到主流程里调。"""
    try:
        data = collect(now)
        text = render(data)
    except Exception as e:
        return False, "生成失败：%s" % e
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        return False, "写入失败：%s" % e
    return True, "已生成 %s（%d 次运行记录）" % (OUT, len(data["items"]))

