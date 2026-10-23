#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""streak-card.py —— 生成可分享的「连签卡片」（1080×1440 竖版 PNG）。

为什么做这个：
    面板、体检、日志都是给维护者看的。真正能让使用者有反馈的，只有一张
    "连续 6 天"的卡片——它能发群里、能截图、一年后翻出来还有感觉。

怎么做到零依赖：
    项目坚持只用标准库 + 本机自带运行时，所以不引 Pillow。
    这里用 HTML 排版 → Edge/Chrome 无头模式截图。截图失败就保留 HTML，
    用户自己按 Win+Shift+S 截也一样，不会白跑。

数据来源：
    - 连续天数 / 累计积分 / 里程碑：milestone.compute()
    - 最近 30 天格子：history 里有记录的算已签；再按"连续 N 天"往前补齐
      （连续天数由服务端给出，前 N-1 天必然是签过的，这是推导不是编造）

用法：
    python streak-card.py             # 生成 streak-card.png（1080×1440 竖版）
    python streak-card.py --wide      # 生成 streak-card-wide.png（1600×1000 横版）
    python streak-card.py --both      # 两种都生成（浏览用横版，分享用竖版）
    python streak-card.py --open      # 生成后打开图片
    python streak-card.py --html      # 只写 HTML（不想截图时）
"""
import datetime
import os
import subprocess
import sys
import tempfile

import history
import milestone
from browser_open import find_edge

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_OUT = os.path.join(HERE, "share-card.html")
PNG_OUT = os.path.join(HERE, "streak-card.png")
SIZE = (1080, 1440)

# 横版：给浏览器里的预览用（横屏容器下才显得大），分享仍用竖版
_WIDE_HTML_OUT = os.path.join(HERE, "share-card-wide.html")
_WIDE_PNG_OUT = os.path.join(HERE, "streak-card-wide.png")
WIDE_SIZE = (1600, 800)

SHOWN_DAYS = 14      # 卡片上展示的天数：7 列 × 2 行。太多空格的 30 格反而显脏

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 r"Google\Chrome\Application\chrome.exe"),
]


# ---------------- 数据 ----------------
def collect(now=None):
    """汇总卡片需要的全部数字。任何一项取不到都不影响出图。"""
    now = now or datetime.datetime.now()
    today = now.date()
    data = {}
    try:
        data = milestone.compute() or {}
    except Exception:
        data = {}
    try:
        days, _ = history.load_days()
    except Exception:
        days = []
    # load_days() 只给 date/credit/streak，累计积分要从原始 store 取
    try:
        raw = history.load_store()
    except Exception:
        raw = {}
    by_date = {}
    for item in days:
        if isinstance(item, dict) and item.get("date"):
            key = str(item["date"])
            by_date[key] = dict(item)
            src = raw.get(key) if isinstance(raw.get(key), dict) else {}
            if src.get("total") is not None:
                by_date[key]["total"] = src.get("total")

    streak = int(data.get("streak") or 0)
    signed = set()
    if data.get("today_signed"):
        signed.add(str(today))           # 今天没签就别点亮，别骗自己
    for i in range(1, streak):           # 连续天数往前补齐（推导，不是编造）
        signed.add(str(today - datetime.timedelta(days=i)))
    signed.update(by_date.keys())

    grid = []
    for i in range(SHOWN_DAYS - 1, -1, -1):
        d = today - datetime.timedelta(days=i)
        grid.append({"date": str(d), "signed": str(d) in signed,
                     "today": i == 0})

    total = None
    today_credit = None
    for key in sorted(by_date.keys(), reverse=True):
        v = by_date[key]
        if total is None and v.get("total") is not None:
            total = v.get("total")
        if today_credit is None and key == str(today) and v.get("credit") is not None:
            today_credit = v.get("credit")
    # 不猜"今日 +100"：只显示历史里真的记到的数字，宁可少一行
    nxt = data.get("next") or {}
    return {
        "date": str(today),
        "stamp": now.strftime("%Y-%m-%d %H:%M"),
        "streak": streak,
        "best": int(data.get("best") or streak or 0),
        "signed_today": bool(data.get("today_signed")),
        "total": total,
        "today_credit": today_credit,
        "grid": grid,
        "hit": sum(1 for g in grid if g["signed"]),
        "next_days": nxt.get("days"),
        "next_remaining": nxt.get("remaining"),
        "next_progress": nxt.get("progress") or 0,
        "next_eta": nxt.get("eta") or "",
    }


# ---------------- 排版 ----------------
def _squares(grid):
    """30 天格子：10 列 × 3 行。"""
    cells = []
    for g in grid:
        cls = "cell on" if g["signed"] else "cell"
        if g["today"]:
            cls += " today"
        cells.append('<div class="%s" title="%s"></div>' % (cls, g["date"]))
    return "\n".join(cells)


def _fmt(v):
    if v is None:
        return "—"
    return "{:,}".format(int(v))


def _copy(d):
    """标题文案。竖版和横版共用，避免两处措辞不一致。"""
    streak = d["streak"]
    if streak > 0 and d["signed_today"]:
        return "连续签到", "今天已完成，明天继续保持"
    if streak > 0:
        return "连续签到", "今天还没签，自动任务会补上"
    return "今天开始", "签到一次，故事就开始了"


def _milestone_html(d):
    """里程碑进度块。没有下一个里程碑就整块不出现。"""
    if not d["next_days"]:
        return ""
    return ('<div class="ms"><div class="ms-top"><span>下一个里程碑 · %d 天</span>'
            '<span>还差 %d 天</span></div>'
            '<div class="bar"><i style="width:%.0f%%"></i></div>'
            '<div class="ms-bottom">%s</div></div>'
            % (d["next_days"], d["next_remaining"] or 0,
               max(3, min(100, (d["next_progress"] or 0) * 100)),
               ("预计 %s 达成" % d["next_eta"]) if d["next_eta"] else ""))


def _stats_html(d):
    extra = []
    if d["total"] is not None:
        extra.append(("累计积分", _fmt(d["total"])))
    if d["today_credit"]:
        extra.append(("今日", "+%s" % _fmt(d["today_credit"])))
    return "\n".join(
        '<div class="stat"><div class="k">%s</div><div class="v">%s</div></div>'
        % (k, v) for k, v in extra)


def render_html(d):
    streak = d["streak"]
    headline, tail = _copy(d)
    stats = _stats_html(d)
    bar = _milestone_html(d)
    # 三位数的连续天数会撑破版面，按位数收字号
    num_size = 268 if streak < 10 else (224 if streak < 100 else 176)

    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{width:%(W)dpx;height:%(H)dpx;overflow:hidden}
  body{
    font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;
    background:radial-gradient(120%% 80%% at 20%% 0%%,#1f9e6a 0%%,#0e5c3c 45%%,#06301f 100%%);
    color:#eafff4;position:relative;
  }
  .glow{position:absolute;width:620px;height:620px;border-radius:50%%;
    background:radial-gradient(circle,rgba(120,255,190,.22),transparent 70%%);
    top:-180px;right:-160px}
  .glow2{position:absolute;width:520px;height:520px;border-radius:50%%;
    background:radial-gradient(circle,rgba(46,224,138,.16),transparent 70%%);
    bottom:-200px;left:-140px}
  .wrap{position:relative;padding:96px 90px;height:100%%;display:flex;flex-direction:column}
  .brand{display:flex;align-items:center;gap:16px;font-size:30px;letter-spacing:2px;
    color:#bdf5d8;opacity:.9}
  .dot{width:16px;height:16px;border-radius:50%%;background:#2ee08a;
    box-shadow:0 0 0 6px rgba(46,224,138,.18)}
  .mid{margin-top:96px}
  .big{display:flex;align-items:flex-end;gap:26px;line-height:1}
  .num{font-size:%(num_size)dpx;font-weight:800;letter-spacing:-2px;line-height:.88;
    background:linear-gradient(180deg,#ffffff,#9df7c7);-webkit-background-clip:text;
    background-clip:text;color:transparent}
  .unit{font-size:70px;font-weight:700;color:#d7ffe9;letter-spacing:2px;
    padding-bottom:34px}
  .cap{margin-top:18px;font-size:34px;color:#b6ecd0;letter-spacing:8px}
  .tail{margin-top:20px;font-size:27px;color:#8fd9b0}
  .stats{display:flex;gap:56px;margin-top:54px}
  .stat .k{font-size:24px;color:#93d9b2;letter-spacing:2px}
  .stat .v{font-size:52px;font-weight:700;color:#ffffff;margin-top:6px}
  .ms{margin-top:auto}
  .ms-top{display:flex;justify-content:space-between;font-size:25px;color:#a9e8c5}
  .bar{margin-top:16px;height:16px;border-radius:99px;background:rgba(255,255,255,.12);
    overflow:hidden}
  .bar i{display:block;height:100%%;border-radius:99px;
    background:linear-gradient(90deg,#2ee08a,#a8ffd2)}
  .ms-bottom{margin-top:12px;font-size:22px;color:#7fcda4}
  .grid{display:grid;grid-template-columns:repeat(7,1fr);gap:16px;margin-top:52px}
  .cell{aspect-ratio:1/1;border-radius:20px;background:rgba(255,255,255,.06)}
  .cell.on{background:linear-gradient(180deg,#3ff09a,#1fba74)}
  .cell.today{box-shadow:0 0 0 3px rgba(255,255,255,.55) inset}
  .foot{display:flex;justify-content:space-between;align-items:center;
    margin-top:44px;font-size:22px;color:#6fbe92}
</style></head>
<body>
  <div class="glow"></div><div class="glow2"></div>
  <div class="wrap">
    <div class="brand"><span class="dot"></span>WorkBuddy 自动签到</div>
    <div class="mid">
      <div class="big"><span class="num">%(streak)d</span><span class="unit">天</span></div>
      <div class="cap">%(headline)s</div>
      <div class="tail">%(tail)s</div>
      <div class="stats">%(stats)s</div>
    </div>
    <div class="grid">%(squares)s</div>
    %(bar)s
    <div class="foot"><span>最近 %(shown)d 天 · 已签 %(hit)d 天</span><span>%(stamp)s</span></div>
  </div>
</body></html>""" % {
        "W": SIZE[0], "H": SIZE[1], "num_size": num_size,
        "streak": streak, "headline": headline, "tail": tail,
        "stats": stats, "squares": _squares(d["grid"]), "bar": bar,
        "hit": d["hit"], "stamp": d["date"], "shown": SHOWN_DAYS,
    }
    return html


# ---------------- 横版排版（浏览器预览用） ----------------
# 不用 % 格式化：横版 CSS 里百分号太多，容易漏写 %%；改用 @@key@@ 占位替换。
_WIDE_TMPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{width:@@W@@px;height:@@H@@px;overflow:hidden}
  body{
    font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;
    background:radial-gradient(120% 100% at 14% 0%,#1f9e6a 0%,#0e5c3c 46%,#06301f 100%);
    color:#eafff4;position:relative;
  }
  .glow{position:absolute;width:900px;height:900px;border-radius:50%;
    background:radial-gradient(circle,rgba(120,255,190,.20),transparent 70%);
    top:-320px;right:-200px}
  .glow2{position:absolute;width:720px;height:720px;border-radius:50%;
    background:radial-gradient(circle,rgba(46,224,138,.14),transparent 70%);
    bottom:-340px;left:-200px}
  .wrap{position:relative;padding:52px 76px 48px;height:100%;
    display:flex;flex-direction:column}
  .head{display:flex;justify-content:space-between;align-items:center}
  .brand{display:flex;align-items:center;gap:15px;font-size:26px;letter-spacing:2px;
    color:#bdf5d8;opacity:.9}
  .dot{width:15px;height:15px;border-radius:50%;background:#2ee08a;
    box-shadow:0 0 0 6px rgba(46,224,138,.18)}
  .date{font-size:23px;color:#7fcda4;letter-spacing:1px}
  .main{flex:1;min-height:0;display:flex;align-items:stretch;margin-top:2px}
  .left{flex:1.02;display:flex;flex-direction:column;justify-content:center}
  .divider{width:1px;align-self:stretch;margin:8px 60px;
    background:linear-gradient(180deg,transparent,rgba(255,255,255,.16) 12%,
    rgba(255,255,255,.16) 88%,transparent)}
  .right{flex:1;display:flex;flex-direction:column;justify-content:center;gap:52px}
  .big{display:flex;align-items:flex-end;gap:22px;line-height:1}
  .num{font-size:@@num_size@@px;font-weight:800;letter-spacing:-5px;line-height:.84;
    background:linear-gradient(180deg,#ffffff,#9df7c7);-webkit-background-clip:text;
    background-clip:text;color:transparent}
  .unit{font-size:78px;font-weight:700;color:#d7ffe9;letter-spacing:2px;
    padding-bottom:32px}
  .cap{margin-top:22px;font-size:38px;color:#b6ecd0;letter-spacing:10px}
  .tail{margin-top:14px;font-size:25px;color:#8fd9b0}
  .stats{display:flex;gap:68px;margin-top:40px}
  .stat .k{font-size:22px;color:#93d9b2;letter-spacing:2px}
  .stat .v{font-size:56px;font-weight:700;color:#ffffff;margin-top:3px}
  .sec{font-size:23px;color:#a9e8c5;letter-spacing:2px;
    display:flex;justify-content:space-between}
  .grid{display:grid;grid-template-columns:repeat(7,1fr);gap:16px;margin-top:22px}
  .cell{aspect-ratio:1/1;border-radius:20px;background:rgba(255,255,255,.06)}
  .cell.on{background:linear-gradient(180deg,#3ff09a,#1fba74)}
  .cell.today{box-shadow:0 0 0 3px rgba(255,255,255,.55) inset}
  .ms-top{display:flex;justify-content:space-between;font-size:25px;color:#a9e8c5}
  .bar{margin-top:16px;height:16px;border-radius:99px;background:rgba(255,255,255,.12);
    overflow:hidden}
  .bar i{display:block;height:100%;border-radius:99px;
    background:linear-gradient(90deg,#2ee08a,#a8ffd2)}
  .ms-bottom{margin-top:13px;font-size:22px;color:#7fcda4}
</style></head>
<body>
  <div class="glow"></div><div class="glow2"></div>
  <div class="wrap">
    <div class="head">
      <div class="brand"><span class="dot"></span>WorkBuddy 自动签到</div>
      <div class="date">@@stamp@@</div>
    </div>
    <div class="main">
      <div class="left">
        <div class="big"><span class="num">@@streak@@</span><span class="unit">天</span></div>
        <div class="cap">@@headline@@</div>
        <div class="tail">@@tail@@</div>
        <div class="stats">@@stats@@</div>
      </div>
      <div class="divider"></div>
      <div class="right">
        <div>
          <div class="sec"><span>最近 @@shown@@ 天 · 已签 @@hit@@ 天</span>
            <span>最长 @@best@@ 天</span></div>
          <div class="grid">@@squares@@</div>
        </div>
        @@bar@@
      </div>
    </div>
  </div>
</body></html>
"""


def render_wide_html(d):
    """1600×1000 横版：左栏天数与积分，右栏格子与里程碑。"""
    streak = d["streak"]
    headline, tail = _copy(d)
    num_size = 300 if streak < 10 else (248 if streak < 100 else 192)
    fields = {
        "W": WIDE_SIZE[0], "H": WIDE_SIZE[1], "num_size": num_size,
        "streak": streak, "headline": headline, "tail": tail,
        "stats": _stats_html(d), "squares": _squares(d["grid"]),
        "bar": _milestone_html(d), "hit": d["hit"],
        "stamp": d["date"], "shown": SHOWN_DAYS, "best": d["best"],
    }
    html = _WIDE_TMPL
    for k, v in fields.items():
        html = html.replace("@@%s@@" % k, str(v))
    return html


# ---------------- 截图 ----------------
def _browser():
    edge = find_edge()
    if edge:
        return edge
    for p in CHROME_PATHS:
        if p and os.path.exists(p):
            return p
    return None


def _png_size(path):
    """读 PNG 的 IHDR 拿宽高（只为校验，不引 Pillow）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(33)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return (int.from_bytes(head[16:20], "big"),
                int.from_bytes(head[20:24], "big"))
    except Exception:
        return None


def shoot(html_path, png_path, timeout=40, size=None):
    """用无头浏览器把 HTML 截成 PNG。返回 (ok, 说明)。

    先试 2 倍图（发出去更清晰），尺寸不对再退回 1 倍：
    不同版本的 Edge 对 --window-size 与 scale-factor 的换算不一致，
    与其猜，不如量一下 PNG 头。
    """
    size = size or SIZE
    exe = _browser()
    if not exe:
        return False, "没找到 Edge 或 Chrome，HTML 已保留，可手动截图"
    profile = tempfile.mkdtemp(prefix="wbcard-")
    tail = ["--disable-gpu", "--hide-scrollbars", "--no-first-run",
            "--disable-extensions", "--virtual-time-budget=2500",
            "--user-data-dir=%s" % profile,
            "--screenshot=%s" % png_path,
            "file:///" + html_path.replace("\\", "/")]
    attempts = [
        (["--headless=new", "--force-device-scale-factor=2",
          "--window-size=%d,%d" % size], (size[0] * 2, size[1] * 2)),
        (["--headless=new", "--window-size=%d,%d" % size], size),
        (["--headless", "--window-size=%d,%d" % size], size),
    ]
    last = ""
    for flags, want in attempts:
        if os.path.exists(png_path):
            try:
                os.remove(png_path)
            except Exception:
                pass
        try:
            subprocess.run([exe] + flags + tail, capture_output=True, timeout=timeout)
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            continue
        got = _png_size(png_path)
        if got and os.path.getsize(png_path) > 2048:
            return True, "已生成 %s（%dx%d，%.0f KB）" % (
                os.path.basename(png_path), got[0], got[1],
                os.path.getsize(png_path) / 1024)
        last = "产出尺寸不符（期望 %dx%d，实际 %s）" % (want[0], want[1], got)
    return False, last or "截图失败"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    data = collect()

    jobs = []
    if "--wide" not in argv:                     # 默认仍出竖版，保持原行为
        jobs.append(("竖版", HTML_OUT, render_html(data), PNG_OUT, SIZE))
    if "--wide" in argv or "--both" in argv:     # 浏览用横版
        jobs.append(("横版", _WIDE_HTML_OUT, render_wide_html(data), _WIDE_PNG_OUT, WIDE_SIZE))

    wrote = False
    for label, html_path, html, png_path, size in jobs:
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            print("%s HTML: %s" % (label, html_path))
            wrote = True
        except Exception as e:
            print("%s 写 HTML 失败：%s" % (label, e))
    if not wrote:
        return 1
    if "--html" in argv:
        return 0

    results = []
    for label, html_path, html, png_path, size in jobs:
        if not os.path.exists(html_path):
            results.append(False)
            continue
        ok, msg = shoot(html_path, png_path, size=size)
        print(("%s PNG: %s" % (label, msg)) if ok
              else ("%s 未生成 PNG：%s" % (label, msg)))
        results.append(ok)

    ok = bool(results) and all(results)
    if "--open" in argv:
        for p in ([_WIDE_PNG_OUT] if "--wide" in argv else [PNG_OUT]):
            if os.path.exists(p):
                try:
                    from browser_open import open_url
                    open_url("file:///" + p.replace("\\", "/"))
                except Exception:
                    pass
                break
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
