#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make-report.py —— 把一次 signin.py 运行结果渲染成直观的 HTML 报告（静态，无需服务器）

用法：
    python make-report.py            # 默认完整运行 auto
    python make-report.py status     # 只读状态
生成：report.html（同目录下，可直接双击打开）

说明：本脚本在前台运行，继承当前用户权限读取桌面端凭据；适合在本机普通终端使用。
"""
import datetime
import json
import os
import sys
import webbrowser

import archive
import health
import history
import milestone
import store
from signin_runner import run_with_retry, PY, SIGNIN

HERE = os.path.dirname(os.path.abspath(__file__))
# 签到日历的持久化记录：date -> {credit, streak, result}（供月历热力图）
HIST = os.path.join(HERE, "signin_history.json")
_SIGNED_RESULTS = ("GROWTH", "ALREADY", "CLAIMED")

# --open：生成后自动用默认浏览器打开（配合 pythonw 无窗口启动）
# --no-archive：只生成报告、不写归档（调试/预览时用，避免污染审计链）
_ARGS = sys.argv[1:]
OPEN_AFTER = "--open" in _ARGS
NO_ARCHIVE = "--no-archive" in _ARGS
_ARGS = [a for a in _ARGS if not a.startswith("--")]
MODE = _ARGS[0] if _ARGS else "auto"


def run():
    """运行 signin.py（带外层重试与智能判定），返回结构化结果。

    重试与成败分类已抽到 signin_runner，沿用其默认：最多 3 次、退避 [10,30]s。
    """
    return run_with_retry(MODE)


def _record_history(payload):
    """成功签到后把当天记入历史（委托 history.py，供热力图 / 收益趋势共用）。"""
    history.record(payload)


def _load_calendar():
    """返回 (days, today)：委托 history.py。"""
    return history.load_days()


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WorkBuddy 自动签到 · 测试报告</title>
<style>
:root{--bg:#f5f7fa;--card:rgba(255,255,255,.88);--ink:#1f2933;--muted:#5b6672;
--line:#e5e7eb;--brand:#1F6FEB;--green:#16a34a;--red:#dc2626;--amber:#d97706;--soft:#eef2ff}
*{box-sizing:border-box}
/* 壁纸：虚化填充(cover) → 完整图片(contain，保证全貌不被裁) → 薄纱 */
.bg{position:fixed;inset:0;z-index:0;overflow:hidden;pointer-events:none;background:#dde5ef}
.bg-blur{position:absolute;inset:-70px;background-image:var(--wp-img,url("wallpaper.jpg"));
background-position:center;background-size:cover;background-repeat:no-repeat;
filter:blur(40px) saturate(1.2) brightness(1.05);transform:scale(1.15);opacity:.8}
.bg-full{position:absolute;inset:0;background-image:var(--wp-img,url("wallpaper.jpg"));
background-position:center center;background-size:contain;background-repeat:no-repeat;
filter:saturate(1.06) contrast(1.02) drop-shadow(0 18px 40px rgba(20,30,50,.18))}
.bg-veil{position:absolute;inset:0;opacity:var(--veil,.5);background:linear-gradient(180deg,
rgba(255,255,255,.30) 0%,rgba(255,255,255,.45) 45%,rgba(255,255,255,.60) 100%)}
body.wp-cover .bg-full{background-size:cover}
body.wp-stretch .bg-full{background-size:100% 100%}
body.wp-off .bg{display:none}
body.wp-off{background:#eef2f7}
/* 暗色模式 */
body.dark{--bg:#0f1620;--card:rgba(30,41,59,.82);--glass:rgba(30,41,59,.82);
--ink:#e6edf5;--muted:#9fb0c3;--line:#2c3e54;--brand:#4f9bff;--soft:#172339}
body.dark h1{color:#e6edf5;text-shadow:none}
body.dark .sub{color:#a9b8c9;text-shadow:none}
body.dark .metric .k,body.dark .card h2{color:#cbd5e1}
body.dark .report{background:rgba(12,18,28,.72);color:#cbd5e1}
body.dark .empty{background:rgba(30,41,59,.7);border-color:rgba(120,140,170,.3);color:#a9b8c9}
body.dark .bg-veil{background:linear-gradient(180deg,rgba(8,12,18,.5),rgba(8,12,18,.66),rgba(8,12,18,.82))}
body.dark .gear,body.dark .settings{background:rgba(30,41,59,.94);color:#e6edf5;
border-color:rgba(255,255,255,.14);box-shadow:0 12px 34px rgba(0,0,0,.5)}
body.dark .seg3 button,body.dark .filebtn{background:#1e293b;color:#a9b8c9}
body.dark .seg3 button.on{background:var(--brand);color:#fff}
body.dark .chip{background:rgba(255,255,255,.08)}
body.dark .restore-hint{background:rgba(8,14,24,.72)}
body.dark.wp-off{background:#0f1620}
.row.off{opacity:.45;pointer-events:none;transition:opacity .2s}
body{margin:0;background:transparent;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
"Microsoft YaHei",sans-serif;color:var(--ink);line-height:1.6;min-height:100vh;
padding:28px 16px 60px}
.wrap{max-width:760px;margin:0 auto;position:relative;z-index:1}
h1{font-size:22px;margin:0 0 4px;color:#14263c;text-shadow:0 1px 2px rgba(255,255,255,.85)}
.sub{color:#3f4d5e;font-size:13px;margin-bottom:8px;text-shadow:0 1px 2px rgba(255,255,255,.8)}
.time{color:var(--muted);font-size:12px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.metric{background:var(--card);border:1px solid rgba(255,255,255,.75);border-radius:14px;padding:16px;
text-align:center;backdrop-filter:blur(14px) saturate(1.2);-webkit-backdrop-filter:blur(14px) saturate(1.2);
box-shadow:0 6px 22px rgba(25,40,70,.10)}
.metric .v{font-size:28px;font-weight:700;color:var(--brand)}
.metric .k{font-size:12px;color:var(--muted);margin-top:2px}
.card{background:var(--card);border:1px solid rgba(255,255,255,.75);border-radius:14px;padding:18px;
margin-bottom:16px;backdrop-filter:blur(14px) saturate(1.2);-webkit-backdrop-filter:blur(14px) saturate(1.2);
box-shadow:0 8px 26px rgba(25,40,70,.11)}
.card h2{font-size:15px;margin:0 0 12px;display:flex;align-items:center;gap:8px}
.badge{font-size:12px;font-weight:700;padding:3px 10px;border-radius:999px}
.b-ok{background:#dcfce7;color:var(--green)}.b-warn{background:#fef3c7;color:var(--amber)}
.b-bad{background:#fee2e2;color:var(--red)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{font-size:13px;padding:6px 11px;border-radius:9px;border:1px solid var(--line);background:#fafafa}
.chip.ok{background:#f0fdf4;border-color:#bbf7d0;color:#166534}
.chip.fail{background:#fef2f2;border-color:#fecaca;color:#991b1b}
.chip.skip{background:#fffbeb;border-color:#fde68a;color:#92400e}
.report{white-space:pre-wrap;font-size:13px;color:#374151;background:rgba(250,250,250,.94);
border:1px solid var(--line);border-radius:10px;padding:12px;max-height:260px;overflow:auto}
details{margin-top:6px}summary{cursor:pointer;color:var(--muted);font-size:13px}
pre{white-space:pre-wrap;font-size:12px;background:#0f172a;color:#e2e8f0;padding:12px;border-radius:10px;
overflow:auto;max-height:300px}
.empty{color:#3f4d5e;font-size:14px;text-align:center;padding:26px 0;background:rgba(255,255,255,.88);
border:1px dashed rgba(120,140,170,.45);border-radius:14px}
.wrap{transition:opacity .28s ease,transform .28s ease}
body.hide-ui .wrap{opacity:0;visibility:hidden;pointer-events:none;transform:translateY(10px) scale(.99)}
.restore-hint{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:4;display:none;
color:#fff;background:rgba(18,28,48,.55);padding:10px 20px;border-radius:999px;font-size:14px;letter-spacing:.5px;
backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);box-shadow:0 6px 20px rgba(0,0,0,.18);pointer-events:none}
body.hide-ui .restore-hint{display:block}
/* 导出与分享工具条（右下角悬浮胶囊，和分享卡片的工具条一致，不压标题） */
.exbar{position:fixed;right:16px;bottom:16px;z-index:6;display:flex;gap:8px;
  background:rgba(255,255,255,.92);border:1px solid rgba(255,255,255,.85);border-radius:999px;
  padding:7px 8px;box-shadow:0 10px 30px rgba(25,40,70,.18);
  backdrop-filter:blur(12px) saturate(1.2);-webkit-backdrop-filter:blur(12px) saturate(1.2)}
.exbar button{font:inherit;font-size:12.5px;font-weight:600;color:#33415c;background:#fff;border:0;
  border-radius:9px;padding:6px 12px;cursor:pointer;white-space:nowrap;transition:background .15s}
.exbar button:hover{background:#f1f5fb}
.exbar button:disabled{opacity:.6;cursor:default}
body.dark .exbar{background:rgba(30,41,59,.94);color:#e6edf5;border-color:rgba(255,255,255,.14);
  box-shadow:0 12px 34px rgba(0,0,0,.5)}
body.dark .exbar button{background:#1e293b;color:#a9b8c9}
body.dark .exbar button:hover{filter:brightness(1.12)}
/* 导出（无头截图）模式下隐藏一切交互 UI，保证画面干净 */
body.export .exbar,body.export .gear,body.export .settings,body.export .restore-hint{display:none}
/* 右上角设置按钮 + 面板 */
.gear{position:fixed;top:16px;right:16px;z-index:6;width:42px;height:42px;border-radius:50%;
display:flex;align-items:center;justify-content:center;font-size:12px;letter-spacing:1px;line-height:1;cursor:pointer;
background:rgba(255,255,255,.92);border:1px solid rgba(255,255,255,.85);color:#33415c;
box-shadow:0 6px 20px rgba(25,40,70,.16);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);transition:.2s}
.gear:hover{filter:brightness(1.04)}
.settings{position:fixed;top:66px;right:16px;z-index:7;width:300px;max-width:calc(100vw - 32px);
background:rgba(255,255,255,.95);
border:1px solid rgba(255,255,255,.85);border-radius:16px;padding:14px 16px;
backdrop-filter:blur(16px) saturate(1.2);-webkit-backdrop-filter:blur(16px) saturate(1.2);
box-shadow:0 12px 34px rgba(25,40,70,.2);font-size:13px;color:var(--ink);display:none;
max-height:calc(100vh - 84px);overflow-y:auto;scrollbar-gutter:stable;overscroll-behavior:contain}
#langSeg button{padding:6px 9px;font-size:11.5px}
.settings.open{display:block;animation:pop .18s ease}
@keyframes pop{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.settings-h{font-weight:700;font-size:14px;margin-bottom:6px;color:#14263c}
.row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 0;
border-top:1px solid rgba(120,140,170,.18)}
.row:first-of-type{border-top:0}
.row>span{color:var(--muted);white-space:nowrap}
.seg3{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.seg3 button{padding:6px 11px;font-size:12px;font-weight:600;border:0;background:#fff;color:var(--muted);cursor:pointer;white-space:nowrap}
.seg3 button.on{background:var(--brand);color:#fff}
.seg3 button+button{border-left:1px solid var(--line)}
.filebtn{display:inline-flex;align-items:center;padding:6px 11px;font-size:12px;font-weight:600;
background:#fff;color:var(--muted);cursor:pointer;border:0;white-space:nowrap}
.filebtn:hover{background:#f3f6fb}
.filebtn input{display:none}
/* 默认壁纸：缩略图 + 状态 */
.settings-sub{font-weight:700;font-size:12.5px;color:#14263c;
margin-top:12px;padding-top:11px;border-top:1px solid rgba(120,140,170,.18);letter-spacing:.3px}
.wpdef{display:flex;align-items:center;gap:10px;padding:9px 0 3px}
.wpdef img{width:64px;height:38px;object-fit:cover;border-radius:7px;flex:none;cursor:zoom-in;
border:1px solid rgba(120,140,170,.3);background:#eef3f8;transition:filter .15s}
.wpdef img:hover{filter:brightness(1.06)}
.wpdef .m{margin-left:auto;text-align:right;font-size:11.5px;line-height:1.45;color:var(--muted)}
.wpdef .m b{display:block;font-size:12px;color:var(--ink)}
input[type=range]{width:128px;accent-color:var(--brand)}
input[type=number].rnum{width:50px;padding:4px 5px;font-size:12px;border:1px solid var(--line);border-radius:7px;background:#fff;color:#14263c;text-align:center;font-variant-numeric:tabular-nums}
.rnum-grp{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap;justify-content:flex-end}
.rnum-grp i{font-style:normal;font-size:11px;color:var(--muted)}
.hidden{display:none!important}
body.dark input[type=number].rnum{background:#0b1220;color:#e6edf5;border-color:#2a3a52}
#rulesNote{display:none}
body.dark .rnum-grp i{color:#94a3b8}
.sw{position:relative;display:inline-block;width:42px;height:24px;flex:none}
.sw input{display:none}
.sw .track{position:absolute;inset:0;border-radius:999px;background:#cbd5e1;transition:.2s;cursor:pointer}
.sw .track:before{content:"";position:absolute;left:3px;top:3px;width:18px;height:18px;border-radius:50%;
background:#fff;transition:.2s;box-shadow:0 1px 3px rgba(0,0,0,.2)}
.sw input:checked+.track{background:var(--brand)}
.sw input:checked+.track:before{transform:translateX(18px)}
.hlist{display:flex;flex-direction:column;gap:8px}
.hrow{font-size:13px;color:var(--muted);line-height:1.5;border-left:3px solid var(--line);
padding:2px 0 2px 10px;background:rgba(245,247,250,.5)}
.hrow b{color:var(--ink)}
.hrep{font-size:12px;color:#6b7785;margin-top:2px;word-break:break-all}
.hclear{margin-left:auto;font-size:12px;font-weight:600;cursor:pointer;color:var(--muted);
background:transparent;border:0;padding:2px 8px}
.hclear:hover{color:var(--red)}
.arclink{font-size:12px;color:var(--brand);text-decoration:none;border:1px solid rgba(31,111,235,.35);
border-radius:6px;padding:1px 7px;margin-left:4px;white-space:nowrap}
.arclink:hover{background:rgba(31,111,235,.1)}
@media(max-width:620px){.grid{grid-template-columns:1fr}body{padding:20px 12px 60px}}
/* 顶部告警横幅（成功自动淡出；失败常驻，直到刷新） */
#alert{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:60;
display:none;padding:10px 18px;border-radius:12px;font-size:14px;font-weight:600;
box-shadow:0 6px 20px rgba(0,0,0,.22);max-width:90%;text-align:center;
pointer-events:none}
#alert.show{display:block;animation:alertIn .25s ease}
@keyframes alertIn{from{opacity:0;transform:translate(-50%,-8px)}to{opacity:1;transform:translate(-50%,0)}}
#alert.fail{background:rgba(220,38,38,.95);color:#fff}
#alert.ok{background:rgba(22,163,74,.95);color:#fff}
/* 连续签到 + 月历热力图 */
.streak{font-size:13px;color:var(--green);font-weight:700;margin-left:6px}
.cal-head{display:flex;align-items:center;justify-content:space-between;margin:4px 0 10px}
.cal-nav{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;
width:30px;height:30px;font-size:17px;line-height:1;cursor:pointer;padding:0}
.cal-nav:hover{border-color:var(--brand);color:var(--brand)}
.cal-week{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;text-align:center;
font-size:12px;color:var(--muted);margin-bottom:6px}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.cal-cell{aspect-ratio:1/1;border-radius:8px;display:flex;align-items:center;justify-content:center;
font-size:12px;background:rgba(120,140,170,.14);color:var(--muted);position:relative}
body.dark .cal-cell{background:rgba(120,140,170,.16);color:#8fa3ba}
.cal-cell.blank{background:transparent}
.cal-cell.signed{background:rgba(22,163,74,.30);color:#0b5a2e;font-weight:600}
body.dark .cal-cell.signed{background:rgba(22,163,74,.55);color:#eafff2}
.cal-cell.hot{background:rgba(22,163,74,.64);color:#fff}
body.dark .cal-cell.hot{background:rgba(34,197,94,.82);color:#06210f}
.cal-cell.today{outline:2px solid var(--brand);outline-offset:1px}
.cal-stats{font-size:12px;color:var(--muted);margin-top:10px}
/* 连签里程碑进度 */
.ms-list{display:flex;flex-direction:column;gap:8px;margin-top:14px}
.ms-item{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:rgba(250,250,250,.55)}
body.dark .ms-item{background:rgba(30,41,59,.5)}
.ms-top{display:flex;align-items:baseline;gap:8px;font-size:13px;font-weight:600;margin-bottom:6px}
.ms-top .ms-gap{margin-left:auto;font-weight:500;font-size:12px;color:var(--muted)}
.ms-bar{height:8px;border-radius:999px;background:rgba(120,140,170,.22);overflow:hidden}
.ms-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,rgba(22,163,74,.6),var(--green))}
.ms-item.done .ms-fill{background:linear-gradient(90deg,#16a34a,#22c55e)}
.ms-item.done .ms-gap{color:var(--green);font-weight:600}
.ms-eta{font-size:11.5px;color:var(--muted);margin-top:5px}
/* ---- 凭据健康 metric ---- */
.metric .v.sm{font-size:20px;letter-spacing:0}
.metric .bar{height:5px;border-radius:999px;background:rgba(120,140,170,.2);overflow:hidden;margin-top:9px}
.metric .bar i{display:block;height:100%;border-radius:999px;background:var(--green);transition:width .5s}
.metric .k2{font-size:11px;color:var(--muted);margin-top:4px;opacity:.9}
.metric.warn .v{color:var(--amber)}.metric.warn .bar i{background:var(--amber)}
.metric.bad .v{color:var(--red)}.metric.bad .bar i{background:var(--red)}
/* ---- 积分趋势 ---- */
.trend-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
.trend-svg{display:block;width:100%;height:auto;overflow:visible}
.trend-svg .gl{stroke:rgba(120,140,170,.22);stroke-width:1;stroke-dasharray:3 4}
.trend-svg .ax{fill:var(--muted);font-size:10px}
.trend-svg .ln{fill:none;stroke:var(--brand);stroke-width:2.4;stroke-linejoin:round;stroke-linecap:round}
.trend-svg .ar{fill:var(--brand);opacity:.15}
.trend-svg .bar2{fill:rgba(22,163,74,.32)}
.trend-svg .pln{fill:none;stroke:var(--amber);stroke-width:2;stroke-dasharray:5 5;opacity:.9}
.trend-svg .pt{fill:var(--brand);stroke:#fff;stroke-width:1.6}
.trend-svg .pt2{fill:var(--amber);stroke:#fff;stroke-width:1.4;opacity:.95}
.trend-legend{display:flex;flex-wrap:wrap;gap:14px;font-size:11.5px;color:var(--muted);margin-top:6px}
.trend-legend i{display:inline-block;width:11px;height:3px;border-radius:2px;margin-right:5px;vertical-align:middle}
.trend-legend .b2{height:8px;width:8px;border-radius:2px;background:rgba(22,163,74,.42)}
.trend-stats{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;font-size:12.5px}
.trend-stats span{background:rgba(120,140,170,.13);padding:4px 10px;border-radius:8px}
.trend-stats b{color:var(--brand)}
.trend-empty{font-size:13px;color:var(--muted);text-align:center;padding:24px 0}
/* ---- 归档对比 ---- */
.diff{margin-top:10px;border-top:1px dashed var(--line);padding-top:10px;font-size:12.5px}
.diff-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px;color:var(--muted)}
.diff-h select{font-size:12px;padding:4px 6px;border-radius:7px;border:1px solid var(--line);
background:rgba(255,255,255,.9);color:var(--ink);max-width:190px}
.diff table{width:100%;border-collapse:collapse}
.diff td{padding:4px 6px;border-bottom:1px solid rgba(120,140,170,.12);vertical-align:top}
.diff tr.chgrow td{background:rgba(217,119,6,.05)}
.diff td:first-child{color:var(--muted);white-space:nowrap;width:88px}
.diff td.n{text-align:right;font-variant-numeric:tabular-nums;width:88px;white-space:nowrap}
.diff .up{color:var(--green);font-weight:600}
.diff .down{color:var(--red);font-weight:600}
.diff .same{color:var(--muted)}
.diff .chg{color:var(--amber);font-weight:600}
.diff .none{color:var(--muted);padding:6px 0}
.diff-close{float:right;cursor:pointer;color:var(--muted);font-size:16px;line-height:1;padding:0 4px}
/* 失败诊断卡 */
.diag{border-left:4px solid var(--line);margin-bottom:16px}
.diag.is-bad{border-left-color:var(--red)}
.diag.is-warn{border-left-color:var(--amber)}
.diag .dcause{font-size:13.5px;color:var(--ink);line-height:1.7;margin:2px 0 12px}
body.dark .diag .dcause{color:#dfe8f2}
.diag .dact{display:flex;flex-wrap:wrap;gap:10px}
.dbtn{font:inherit;font-size:13px;font-weight:600;padding:9px 16px;border-radius:10px;cursor:pointer;
  border:1px solid var(--line);background:var(--card);color:var(--ink);transition:.16s}
.dbtn:hover{filter:brightness(.98);border-color:var(--brand)}
.dbtn:disabled{opacity:.6;cursor:default}
.dbtn-primary{background:var(--brand);color:#fff;border-color:transparent}
.dbtn-primary:hover{filter:brightness(1.06)}
.diag .dhint{margin-top:10px;font-size:12.5px;color:var(--muted);min-height:1em}
.diag details{margin-top:10px}
body.dark .dbtn{background:rgba(30,41,59,.9)}
body.dark .diff-h select{background:#1e293b;color:#cbd5e1;border-color:#334155}
body.dark .trend-stats span{background:rgba(255,255,255,.07)}
/* 归档行在暗色下原本还是浅底，白字直接看不见——一并补上 */
body.dark .hrow{background:rgba(255,255,255,.05);border-left-color:rgba(255,255,255,.16)}
body.dark .arclink{color:#8bb8ff;border-color:rgba(139,184,255,.38)}
body.dark .arclink:hover{background:rgba(139,184,255,.14)}
body.dark .diff td{border-bottom-color:rgba(255,255,255,.08)}
body.dark .diff .chg{color:#f0b45f}
body.dark .diff tr.chgrow td{background:rgba(217,119,6,.09)}
body.dark .diff .up{color:#4ade80}
body.dark .diff .down{color:#f87171}
/* 打印 / 导出 PDF：整页壁纸逐页重复会让文件极大，换成干净白底 */
@media print{
  .bg{display:none!important}
  .exbar,.gear,.settings,.restore-hint{display:none!important}
  body{background:#fff!important;padding:0 0 6px}
  .wrap{max-width:none}
  .metric,.card{box-shadow:none!important;border:1px solid #e3eaf3!important;
    break-inside:avoid;page-break-inside:avoid}
  .report,pre{max-height:none!important;overflow:visible!important}
  a{color:#1450a8}
}
</style></head><body>
<div id="alert"></div>
<div class="bg" aria-hidden="true">
<div class="bg-blur"></div><div class="bg-full"></div><div class="bg-veil"></div>
</div>
<div class="wrap">
<h1>WorkBuddy 自动签到 · 测试报告</h1>
<div class="sub">脚本读取本机登录态，仅作用于你自己的账号。</div>
<div class="time" id="t"></div>
<div id="out"><div class="empty">生成中…</div></div>
<div class="card" id="trend"><h2>积分增长 <span class="streak" id="trendBadge"></span></h2>
<div class="trend-head">
<div class="seg3" id="trendSeg"><button data-days="30" class="on">30 天</button>
<button data-days="90">90 天</button><button data-days="all">全部</button></div>
</div>
<div id="trendBox"><div class="trend-empty">统计中…</div></div>
<div class="trend-legend" id="trendLegend"></div>
<div class="trend-stats" id="trendStats"></div>
</div>
<div class="card" id="milestones"><h2>连签里程碑 <span class="streak" id="msBadge"></span></h2>
<div class="ms-list" id="msBox"><div class="empty">计算中…</div></div>
</div>
<div class="card" id="calendar"><h2>连续签到 <span class="streak" id="streakBadge"></span></h2>
<div class="cal-head"><button class="cal-nav" id="calPrev" aria-label="上个月">‹</button>
<span id="calTitle"></span>
<button class="cal-nav" id="calNext" aria-label="下个月">›</button></div>
<div class="cal-week"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div>
<div class="cal-grid" id="calGrid"></div>
<div class="cal-stats" id="calStats"></div>
</div>
<div class="card" id="history"><h2>运行历史 <button class="hclear" id="hclear">清空</button></h2>
<div class="hlist" id="historyList"></div></div>
<div class="card" id="archive"><h2>历史报告 <span class="streak" id="arcBadge"></span></h2>
<div class="hlist" id="archiveList"></div><div id="diffBox"></div></div>
</div>
<div class="restore-hint">点击任意处恢复</div>
<div class="exbar" id="exbar" title="导出与分享">
  <button data-x="png">导出图片</button>
  <button data-x="pdf">导出 PDF</button>
  <button data-x="card">连签卡片</button>
</div>
<button class="gear" id="gear" title="设置" aria-label="设置">设置</button>
<div class="settings" id="settings">
<div class="settings-h">设置</div>
<div class="row"><span>语言 / Language</span>
<div class="seg3" id="langSeg"><button data-lang="zh" class="on">中文</button>
<button data-lang="en">English</button><button data-lang="ja">日本語</button></div></div>
<div class="row"><span>壁纸模式</span>
<div class="seg3" id="wpSeg"><button data-wp="contain" class="on">完整</button>
<button data-wp="cover">铺满</button><button data-wp="stretch">拉伸</button>
<button data-wp="off">关闭</button></div></div>
<div class="row"><span>遮罩浓度</span><input type="range" id="veil" min="0" max="100" value="50"></div>
<div class="row"><span>点击背景隐藏数据</span>
<label class="sw"><input type="checkbox" id="clickHide" checked><span class="track"></span></label></div>
<div class="row"><span>默认展开原始 JSON</span>
<label class="sw"><input type="checkbox" id="jsonOpen"><span class="track"></span></label></div>
<div class="row"><span>暗色模式</span>
<label class="sw"><input type="checkbox" id="darkToggle"><span class="track"></span></label></div>
<div class="row"><span>壁纸轮播</span>
<label class="sw"><input type="checkbox" id="carouselOn"><span class="track"></span></label></div>
<div class="row off" id="carouselRow"><span>轮播间隔</span>
<input type="range" id="carouselInt" min="3" max="60" value="10"><span class="ival" id="carouselIntVal">10秒</span></div>
<div class="row off" id="carouselRow2"><span>轮播图片</span>
<div class="seg3"><label class="filebtn"><input type="file" id="carouselAdd" accept="image/*" multiple>＋添加</label>
<button id="carouselClear">清空</button></div></div>
<div class="row off" id="carouselRow3"><span></span><span class="ival" id="carouselCount">0 张</span></div>
<div class="row"><span>自定义壁纸</span>
<div class="seg3"><label class="filebtn"><input type="file" id="wpFile" accept="image/*">选择图片</label>
<button id="wpReset">恢复默认</button></div></div>
<div class="settings-sub">默认壁纸</div>
<div class="wpdef">
<img id="wpDefThumb" src="wallpaper.jpg" alt="默认壁纸" title="点击查看原图">
<div class="m"><b id="wpDefState">内置</b><span id="wpDefMeta">—</span></div>
</div>
<div class="row" id="wpDefRow"><span>换成我的图片</span>
<div class="seg3"><label class="filebtn"><input type="file" id="wpDefPick" accept="image/*">选择图片</label>
<button id="wpDefReset">还原出厂</button></div></div>
<div class="settings-sub">提醒规则 <span class="ival" id="rulesSaved"></span></div>
<div class="row rule-row"><span>凭据预警（天）</span>
<span class="rnum-grp"><i>提醒</i><input type="number" id="ruleWarn" min="1" max="60" class="rnum"><i>危险</i><input type="number" id="ruleDanger" min="1" max="60" class="rnum"></span></div>
<div class="row rule-row"><span>连签目标（天）</span>
<span class="rnum-grp"><label class="sw" style="margin-right:6px"><input type="checkbox" id="ruleStreakOn"><span class="track"></span></label><input type="number" id="ruleStreakDays" min="1" max="365" class="rnum"></span></div>
<div class="row rule-row"><span>积分下滑</span>
<label class="sw"><input type="checkbox" id="ruleDropOn"><span class="track"></span></label></div>
<div class="row rule-row"><span>较均值降</span>
<span class="rnum-grp"><input type="number" id="ruleDropPct" min="0" max="100" class="rnum"><i>% 且少</i><input type="number" id="ruleDropAbs" min="0" max="500" class="rnum"><i>分</i></span></div>
<div class="row off" id="rulesNote"><span></span><span class="ival">仅主控台可改</span></div>
</div>
<script>
const PAYLOAD = /*DATA*/;
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function badge(t,c){return '<span class="badge '+c+'">'+t+'</span>';}
var expandJson=false;

/* ---- 多语言（中 / English / 日本語）---- */
const I18N = {
"WorkBuddy 自动签到 · 测试报告":["WorkBuddy Auto Check-in · Report","WorkBuddy 自動チェックイン · レポート"],
"脚本读取本机登录态，仅作用于你自己的账号。":["Reads your local login state; only affects your own account.","ローカルのログイン状態を読み取り、あなた自身のアカウントにのみ作用します。"],
"设置":["Settings","設定"],
"壁纸模式":["Wallpaper mode","壁紙モード"],
"遮罩浓度":["Overlay opacity","オーバーレイ濃度"],
"点击背景隐藏数据":["Click background to hide","背景クリックで非表示"],
"默认展开原始 JSON":["Expand raw JSON by default","生 JSON を初期展開"],
"暗色模式":["Dark mode","ダークモード"],
"壁纸轮播":["Wallpaper slideshow","壁紙スライドショー"],
"轮播间隔":["Slideshow interval","スライド間隔"],
"轮播图片":["Slideshow images","スライド画像"],
"自定义壁纸":["Custom wallpaper","カスタム壁紙"],
"默认壁纸":["Default wallpaper","デフォルト壁紙"],
"换成我的图片":["Use my image","自分の画像を使う"],
"还原出厂":["Factory reset","初期値に戻す"],
"内置":["Built-in","内蔵"],
"仅主控台可改":["Console only","コンソールのみ変更可"],
"上传中…":["Uploading…","アップロード中…"],
"失败":["Failed","失敗"],
"完整":["Fit","全体"],
"铺满":["Cover","全体に敷く"],
"拉伸":["Stretch","伸縮"],
"关闭":["Off","オフ"],
"＋添加":["+ Add","＋追加"],
"清空":["Clear","クリア"],
"选择图片":["Choose image","画像を選択"],
"恢复默认":["Reset","デフォルトに戻す"],
"0 张":["0 images","0 枚"],
"10秒":["10s","10秒"],
"点击任意处恢复":["Click anywhere to restore","任意の場所をクリックで復元"],
"连续签到(天)":["Streak (days)","連続 (日)"],
"累计积分":["Total credits","累計ポイント"],
"今日积分":["Today's credits","本日ポイント"],
"签到 ":["Check-in ","チェックイン "],
"成长中心 ":["Growth center ","成長センター "],
"成长中心":["Growth center","成長センター"],
"整体":["Overall","全体"],
" 整体 ":[" Overall "," 全体 "],
"运行历史":["Run history","実行履歴"],
"连续签到":["Streak","連続"],
"查看原始 JSON":["View raw JSON","生 JSON を表示"],
"生成中…":["Generating…","生成中…"],
"运行失败":["Run failed","実行失敗"],
"今日已签 ✓":["Checked in today ✓","本日チェックイン済み ✓"],
"需注意":["Attention","注意"],
"待签到":["Not checked in","未チェックイン"],
"需关注":["Attention","要確認"],
"正常":["Normal","正常"],
"成长中心已跑":["Growth center done","成長センター完了"],
"已跳过":["Skipped","スキップ"],
"（无明细）":["(no details)","（明細なし）"],
"连签 %s 天":["%s-day streak","%s 日連続"],
"还差 %s 天":["%s days to go","あと %s 日"],
"还差 1 天":["1 day to go","あと 1 日"],
"预计 %s 达成":["ETA %s","%s 達成見込み"],
"已达成":["Reached","達成済み"],
"连签里程碑":["Streak milestones","連続マイルストーン"],
"全部达成":["All reached","すべて達成"],
"下一目标 %s 天":["Next goal: %s days","次の目標：%s 日"],
"历史报告":["Past reports","過去のレポート"],
"还没有归档，下次运行后自动保存":["No archive yet — saved automatically on the next run","まだありません（次回実行時に保存）"],
"最近 ":["last ","最新 "],
" 份":[" files"," 件"],
"打开":["Open","開く"],
"积分增长":["Credit growth","ポイント推移"],
"积分增长趋势":["Credit growth trend","ポイント推移グラフ"],
"累计积分":["Total credits","累計ポイント"],
"当日增量":["Daily gain","当日増分"],
"未来 %s 天预测":["%s-day forecast","%s 日予測"],
"至少要两次签到记录才能画趋势":["At least two recorded check-ins are needed to draw a trend","トレンド表示には 2 回以上の記録が必要です"],
"近 %s 天":["Last %s days","直近 %s 日"],
"日均":["Daily avg","1日平均"],
"最近 7 天":["Last 7 days","直近 7 日"],
"%s 天后约":["In %s days ≈","%s 日後 ≈"],
"走势持稳（无新增）":["Flat (no gain)","横ばい（増加なし）"],
"30 天":["30 days","30 日"],
"90 天":["90 days","90 日"],
"全部":["All","すべて"],
"凭据健康":["Credential health","認証の健全性"],
"凭据剩余（天）":["Credential left (days)","認証残り（日）"],
"健康":["Health","健全"],
"到期":["Expires","期限"],
"已过期，需重新登录一次":["Expired — sign in again","期限切れ：再ログインが必要"],
"未找到凭据":["No credential","認証情報なし"],
"需打开桌面端登录一次":["Open the desktop app to sign in","デスクトップアプリでログインしてください"],
"暂不可用（稍后自动刷新）":["Unavailable (auto-refresh soon)","取得できません（後で自動更新）"],
"对比":["Compare","比較"],
"对比基准":["Compare with","比較基準"],
"关闭":["Close","閉じる"],
"开":["On","オン"],
"关":["Off","オフ"],
"是":["Yes","はい"],
"否":["No","いいえ"],
"结果":["Result","結果"],
"成功":["OK","成功"],
"本次积分":["Credits","今回のポイント"],
"连续天数":["Streak days","連続日数"],
"尝试次数":["Attempts","試行回数"],
"模式":["Mode","モード"],
"推送通道":["Notify channel","通知チャネル"],
"推送开关":["Notify enabled","通知オン/オフ"],
"推送就绪":["Notify ready","通知設定済み"],
"计划任务":["Scheduled tasks","スケジュールタスク"],
"历史天数":["History days","履歴日数"],
"环境":["Environment","環境"],
"两份快照都没记录环境信息（旧版本生成）":["Neither snapshot recorded environment info (older version)","両方とも環境情報なし（旧バージョン）"],
"没有可比对的更早记录":["No earlier snapshot to compare","比較できる古い記録がありません"],
"（无输出）":["(no output)","（出力なし）"],
"暂无记录，运行后会自动保存最近 ":["No records yet; the last ","まだ記録がありません。最新 "],
" 次。":[" runs will be saved."," 回分を保存します。"],
"一":["Mon","月"],"二":["Tue","火"],"三":["Wed","水"],"四":["Thu","木"],"五":["Fri","金"],"六":["Sat","土"],"日":["Sun","日"],
"当前连续":["Current streak","現在連続"],
"本月签到":["Days this month","今月のチェックイン"],
"记录内最长":["Longest","最長"],
"运行模式：":["Mode: ","モード："],
"签到成功":["Check-in succeeded","チェックイン成功"],
"签到失败":["Check-in failed","チェックイン失敗"],
"提醒规则":["Reminder rules","リマインダー設定"],
"凭据预警（天）":["Credential alert (days)","認証アラート（日）"],
"连签目标（天）":["Streak goal (days)","連続目標（日）"],
"积分下滑":["Credit drop","ポイント低下"],
"较均值降":["vs avg drop","平均比で低下"],
"提醒":["Warn","警告"],
"危险":["Danger","危険"],
"% 且少":["% & below","% かつ未満"],
"分":["pts","pt"],
"已保存":["Saved","保存済み"]
};
const PREFIX_KEYS = ["暂无记录，运行后会自动保存最近 ","运行模式：","签到成功","签到失败"];
let LANG='zh';
try{ LANG=localStorage.getItem('wb-lang')||'zh'; }catch(e){}
if(LANG!=='zh'&&LANG!=='en'&&LANG!=='ja') LANG='zh';
const _origText=new WeakMap();
function t(zh){ if(LANG==='zh') return zh; const v=I18N[zh]; return v? v[LANG==='en'?0:1] : zh; }
function trText(raw){
  if(LANG==='zh') return raw;
  const rawKey=String(raw);
  const key=rawKey.trim();
  if(!key) return raw;
  let out=null;
  // 词条键是带空格的（"签到 "、" 整体 "），而这里会把首尾空白 trim 掉，
  // 直接用 trim 后的 key 查不到 → 界面上残留中文。原样再查一次即可命中。
  if(I18N[rawKey]) out=t(rawKey);
  else if(I18N[key]) out=t(key);
  else {
    let best=null;
    for(const k of PREFIX_KEYS){ if(key.startsWith(k)&&(!best||k.length>best.length)) best=k; }
    if(best){
      const rest=key.slice(best.length);
      const rt=I18N[rest.trim()];
      let pre, sep='';
      if(I18N[best]) pre=t(best);
      else { pre=t(best.trim()); if(/\s$/.test(best)&&pre&&!pre.endsWith(' ')) sep=' '; }
      out=pre+sep+(rt?t(rest.trim()):rest);
    }
  }
  if(out==null) out=key;
  if(LANG!=='zh'){
    out=out.replace(/尚未运行/g, LANG==='en'?'never run':'未実行');
    out=out.replace(/当前连续 (\d+) 天/g, LANG==='en'?'Current streak $1 days':'現在 $1 日連続');
    out=out.replace(/本月签到 (\d+) 天/g, LANG==='en'?'$1 days this month':'今月 $1 日');
    out=out.replace(/记录内最长 (\d+) 天/g, LANG==='en'?'Longest $1 days':'最長 $1 日');
    out=out.replace(/连续 (\d+) 天/g, LANG==='en'?'$1-day streak':'$1 日連続');
    out=out.replace(/ · 退出码：/g, LANG==='en'?' · exit code ':' · 終了コード ');
    out=out.replace(/ · 尝试 /g, LANG==='en'?' · attempts ':' · 試行 ');
    out=out.replace(/ 次 · 生成时间：/g, LANG==='en'?' times · generated ':' 回 · 生成時刻 ');
    out=out.replace(/（共尝试 (\d+) 次，重试后成功）/g, LANG==='en'?' (attempts: $1, succeeded on retry)':'（試行 $1 回、再試行で成功）');
    out=out.replace(/（共尝试 (\d+) 次，仍失败）/g, LANG==='en'?' (attempts: $1, still failing)':'（試行 $1 回、なお失敗）');
    out=out.replace(/(\d+) 次。/g, LANG==='en'?'$1 runs will be saved.':'$1 回分を保存します。');
    if(LANG==='en'){ out=out.replace(/(\d+)\s*小时/g,'$1h ').replace(/(\d+)\s*分(?!钟)/g,'$1m ').replace(/(\d+)\s*秒/g,'$1s'); }
    else { out=out.replace(/(\d+)\s*小时/g,'$1時間').replace(/(\d+)\s*分(?!钟)/g,'$1分').replace(/(\d+)\s*秒/g,'$1秒'); }
  }
  const lead=(raw.match(/^\s*/)||[''])[0], trail=(raw.match(/\s*$/)||[''])[0];
  return lead+out+trail;
}
function fmtMonth(y,m){
  if(LANG==='en'){ const MN=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']; return MN[m]+' '+y; }
  return y+'年'+(m+1)+'月';
}
function translateNode(n){
  const raw=n.nodeValue;
  if(!raw||!raw.trim()) return;
  if(!_origText.has(n)) _origText.set(n, raw);
  const orig=_origText.get(n);
  const out = LANG==='zh' ? orig : trText(orig);
  if(n.nodeValue!==out) n.nodeValue=out;
}
function localizeDom(root){
  if(!root) return;
  const w=document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(n){
      if(!n.nodeValue||!n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const p=n.parentElement;
      if(p&&p.closest('script,style')) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  const arr=[]; while(w.nextNode()) arr.push(w.currentNode);
  arr.forEach(translateNode);
  if(root.querySelectorAll){
    root.querySelectorAll('[title]').forEach(function(el){
      if(!el.dataset.origTitle) el.dataset.origTitle=el.title;
      el.title = LANG==='zh' ? el.dataset.origTitle : trText(el.dataset.origTitle);
    });
  }
}
function applyLang(){
  document.documentElement.lang=LANG;
  localizeDom(document.body);
  const seg=document.getElementById('langSeg');
  if(seg) seg.querySelectorAll('button').forEach(function(b){ b.classList.toggle('on', b.dataset.lang===LANG); });
  try{ localStorage.setItem('wb-lang', LANG); }catch(e){}
}
function render(d){
 const out=document.getElementById('out');
 if(!d.ok){out.innerHTML='<div class="grid">'+credMetricHTML(d)+'</div>'+diagFailure(d);return;}
 const x=d.data||{}, rep=x.report||'';
 const items=rep.split('；').map(s=>s.trim()).filter(Boolean).map(t=>{
   let c='ok';
   if(/失败|prerequisite not met|no active buddy|未解锁|INACTIVE/.test(t))c='fail';
   else if(/已签过|今日名额已用完|还在路上|无需|跳过/.test(t))c='skip';
   return '<span class="chip '+c+'">'+esc(t)+'</span>';}).join('');
 const signed=/ALREADY|CLAIMED|今日已签|成功领取/.test(x.result||'')||/已签过|成功领取/.test(rep);
 const skipped=(x.result==='SKIPPED');
 const sb=skipped?badge('已跳过','b-warn'):(signed?badge('今日已签 ✓','b-ok'):(x.needs_attention?badge('需注意','b-warn'):badge('待签到','b-warn')));
 const ab=x.needs_attention?badge('需关注','b-warn'):badge('正常','b-ok');
 const gb=(x.growth_result==='GROWTH')?badge('成长中心已跑','b-ok'):(x.growth_result?badge(String(x.growth_result),'b-skip'):'');
 out.innerHTML=`
  <div class="grid">
   <div class="metric"><div class="v">${x.streak_days??'—'}</div><div class="k">连续签到(天)</div></div>
   <div class="metric"><div class="v">${x.total_credits??'—'}</div><div class="k">累计积分</div></div>
   <div class="metric"><div class="v">${x.today_credit??'—'}</div><div class="k">今日积分</div></div>
   ${credMetricHTML(d)}
  </div>
  <div class="card"><h2>签到 ${sb}</h2><div class="chips">${items||'<span class="chip">（无明细）</span>'}</div></div>
  <div class="card"><h2><span>成长中心</span> ${gb} <span>整体</span> ${ab}</h2>
   <div class="report">${esc(rep||'（无输出）')}</div>
   <details${expandJson?' open':''}><summary>查看原始 JSON</summary><pre>${esc(JSON.stringify(x,null,2))}</pre></details>
  </div>`;
}
/* 智能告警：成功/失败后更新标签页标题 + 顶部横幅 +（尽力）鸣叫。
   关键：瞬时抖动在重试中自愈后不会触发告警——只有最终仍失败才到这里。 */
function beep(){
  try{
    var C=window.AudioContext||window.webkitAudioContext; if(!C)return;
    var ctx=new C();
    var blip=function(f,t0,dur){
      var o=ctx.createOscillator(),g=ctx.createGain();
      o.type='square'; o.connect(g); g.connect(ctx.destination);
      g.gain.setValueAtTime(0.0001,t0);
      g.gain.exponentialRampToValueAtTime(0.25,t0+0.02);
      g.gain.exponentialRampToValueAtTime(0.0001,t0+dur);
      o.frequency.value=f; o.start(t0); o.stop(t0+dur);
    };
    var n=ctx.currentTime;
    blip(880,n,0.16); blip(620,n+0.2,0.22);
    setTimeout(function(){try{ctx.close();}catch(e){}},700);
  }catch(e){}
}
function applyAlert(d){
  var el=document.getElementById('alert');
  var retryNote=d.retried?('（共尝试 '+d.attempts_used+' 次'+(d.ok?'，重试后成功':'，仍失败')+'）'):'';
  if(d.ok){
    document.title=t('签到成功')+' · WorkBuddy';
    el.className='ok'; el.textContent='签到成功 '+retryNote;
    el.classList.add('show');
    setTimeout(function(){el.classList.remove('show');},6000);
  }else{
    document.title=t('签到失败')+' · WorkBuddy';
    var reason='';
    if(d.data&&d.data.report)reason='：'+(d.data.report||'').slice(0,40);
    else if(d.stderr)reason='：'+(d.stderr||'').slice(0,40);
    el.className='fail'; el.textContent='签到失败 '+retryNote+reason;
    el.classList.add('show');
    beep(); // 无用户手势时浏览器可能拦截音频，不影响红色横幅/标题告警
  }
}
/* ---- 连续签到天数 + 月历热力图 ---- */
function renderCalendar(d){
  var days=(d&&d.calendar_days)||[];
  var todayStr=(d&&d.calendar_today)||new Date().toISOString().slice(0,10);
  var map={};
  days.forEach(function(it){if(it&&it.date)map[it.date]={credit:it.credit,streak:it.streak};});
  var sd=(d&&d.data&&d.data.streak_days)||0;
  function iso(x){var p=function(n){return String(n).padStart(2,'0');};
    return x.getFullYear()+'-'+p(x.getMonth()+1)+'-'+p(x.getDate());}
  // 用 streak_days 回填当前连续窗口，让热力图开箱即准（历史未覆盖的区间也能显示）
  var t=new Date(todayStr+'T00:00:00');
  for(var i=0;i<sd;i++){
    var k=iso(new Date(t.getTime()-i*86400000));
    if(!map[k])map[k]={credit:(i===0?((d.data&&d.data.today_credit)||null):null),backfill:true};
  }
  var cur=new Date(t.getFullYear(),t.getMonth(),1);
  function longestStreak(){
    var ks=Object.keys(map).filter(function(k){return !!map[k];}).sort();
    if(!ks.length)return 0;
    var best=1,run=1;
    for(var i=1;i<ks.length;i++){
      var a=new Date(ks[i-1]+'T00:00:00'),b=new Date(ks[i]+'T00:00:00');
      if((b-a)/86400000===1){run++;if(run>best)best=run;}else{run=1;}
    }
    return best;
  }
  function draw(){
    var y=cur.getFullYear(),m=cur.getMonth();
    document.getElementById('calTitle').textContent=fmtMonth(y,m);
    var grid=document.getElementById('calGrid');grid.innerHTML='';
    var offset=(new Date(y,m,1).getDay()+6)%7;  // 周一起始
    var dim=new Date(y,m+1,0).getDate();
    for(var b=0;b<offset;b++){var c0=document.createElement('div');c0.className='cal-cell blank';grid.appendChild(c0);}
    var monthSigned=0;
    for(var dd=1;dd<=dim;dd++){
      var ds=iso(new Date(y,m,dd));
      var cell=document.createElement('div');cell.className='cal-cell';cell.textContent=dd;
      var info=map[ds];
      if(info){
        cell.classList.add('signed');monthSigned++;
        if((info.credit||0)>=100)cell.classList.add('hot');
        cell.title=ds+' · 已签'+(info.credit!=null?(' · +'+info.credit+'积分'):'')+(info.backfill?'（连续期内）':'');
      }else{cell.title=ds+' · 未签';}
      if(ds===todayStr)cell.classList.add('today');
      grid.appendChild(cell);
    }
    var stats=[];
    if(sd)stats.push('当前连续 '+sd+' 天');
    stats.push('本月签到 '+monthSigned+' 天');
    var longest=longestStreak();
    if(longest)stats.push('记录内最长 '+longest+' 天');
    document.getElementById('calStats').textContent=stats.join(' · ');
    document.getElementById('streakBadge').textContent=sd?('连续 '+sd+' 天'):'';
  }
  document.getElementById('calPrev').onclick=function(){cur=new Date(cur.getFullYear(),cur.getMonth()-1,1);draw();};
  document.getElementById('calNext').onclick=function(){cur=new Date(cur.getFullYear(),cur.getMonth()+1,1);draw();};
  draw();
}
/* ---- 积分增长：累计曲线 + 每日增量柱 + 未来 7 天预测 ---- */
var TREND_DAYS=30;
function fmtNum(v){ if(v==null||isNaN(v))return '—';
  return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g,','); }
function dayDiff(a,b){
  var A=new Date(a+'T00:00:00'),B=new Date(b+'T00:00:00');
  return Math.round((B-A)/86400000);
}
function mmdd(s){ return (s||'').slice(5); }
function renderTrend(d){
  var box=document.getElementById('trendBox'); if(!box)return;
  var badge=document.getElementById('trendBadge');
  var legend=document.getElementById('trendLegend');
  var stats=document.getElementById('trendStats');
  var raw=(d&&d.credit_series)||[];
  var valid=raw.filter(function(it){ return it&&it.date&&it.total!=null; });
  if(valid.length<2){
    box.innerHTML='<div class="trend-empty">'+t('至少要两次签到记录才能画趋势')+'</div>';
    if(badge)badge.textContent='';
    legend.innerHTML=''; stats.innerHTML='';
    return;
  }
  var n=(TREND_DAYS==='all')?valid.length:Math.min(TREND_DAYS,valid.length);
  var rows=valid.slice(-n);
  var pts=[];
  for(var i=0;i<rows.length;i++){
    var delta=null;
    if(i>0) delta=rows[i].total-rows[i-1].total;
    else if(rows[i].credit!=null) delta=rows[i].credit;
    pts.push({date:rows[i].date,total:rows[i].total,delta:delta});
  }
  // 预测：取最近 7 个日增量的均值（负增长不外推，交给"走势持稳"文案）
  var tail=[];
  for(var j=Math.max(1,pts.length-7);j<pts.length;j++){
    if(pts[j].delta!=null) tail.push(pts[j].delta);
  }
  var avg7=tail.length?(tail.reduce(function(a,b){return a+b;},0)/tail.length):0;
  var FORECAST=7;
  var last=pts[pts.length-1].total;
  var predVals=[];
  if(avg7>0){ for(var k=1;k<=FORECAST;k++) predVals.push(last+avg7*k); }

  var W=720,H=212,PL=42,PR=8,PT=14,PB=30;
  var iw=W-PL-PR, ih=H-PT-PB;
  var histW=iw*(predVals.length?0.78:1), predW=iw-histW;

  var all=pts.map(function(p){return p.total;}).concat(predVals);
  var vmin=Math.min.apply(null,all), vmax=Math.max.apply(null,all);
  if(vmax-vmin<10){ vmax=vmin+10; }
  var pad=(vmax-vmin)*0.14; vmin-=pad; vmax+=pad;

  function X(i){ return PL+(pts.length<=1?0:histW*i/(pts.length-1)); }
  function XP(k){ return PL+histW+predW*k/FORECAST; }
  function Y(v){ return PT+ih*(1-(v-vmin)/(vmax-vmin)); }

  var maxD=0;
  pts.forEach(function(p){ if(p.delta!=null&&p.delta>maxD)maxD=p.delta; });

  var s=[];
  s.push('<svg class="trend-svg" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+esc(t('积分增长趋势'))+'">');
  for(var g=0;g<=3;g++){
    var gy=PT+ih*g/3, gv=vmax-(vmax-vmin)*g/3;
    s.push('<line class="gl" x1="'+PL+'" y1="'+gy.toFixed(1)+'" x2="'+(PL+iw)+'" y2="'+gy.toFixed(1)+'"/>');
    s.push('<text class="ax" x="'+(PL-6)+'" y="'+(gy+3.5).toFixed(1)+'" text-anchor="end">'+fmtNum(gv)+'</text>');
  }
  var line=[];
  pts.forEach(function(p,i){ line.push(X(i).toFixed(1)+','+Y(p.total).toFixed(1)); });
  s.push('<polygon class="ar" points="'+line.join(' ')+' '+
    X(pts.length-1).toFixed(1)+','+(PT+ih)+' '+X(0).toFixed(1)+','+(PT+ih)+'"/>');
  var bw=Math.min(26,Math.max(2,histW/pts.length*0.5));
  pts.forEach(function(p,i){
    if(p.delta==null||p.delta<=0||!maxD) return;
    var bh=(p.delta/maxD)*(ih*0.40);
    s.push('<rect class="bar2" x="'+(X(i)-bw/2).toFixed(1)+'" y="'+(PT+ih-bh).toFixed(1)+
      '" width="'+bw.toFixed(1)+'" height="'+bh.toFixed(1)+'" rx="1.5"><title>'+
      p.date+' +'+p.delta+'</title></rect>');
  });
  s.push('<polyline class="ln" points="'+line.join(' ')+'"/>');
  if(predVals.length){
    var pl=[X(pts.length-1).toFixed(1)+','+Y(last).toFixed(1)];
    predVals.forEach(function(v,k){ pl.push(XP(k+1).toFixed(1)+','+Y(v).toFixed(1)); });
    s.push('<polyline class="pln" points="'+pl.join(' ')+'"/>');
    s.push('<circle class="pt2" cx="'+XP(FORECAST).toFixed(1)+'" cy="'+Y(predVals[predVals.length-1]).toFixed(1)+'" r="3.2"/>');
  }
  s.push('<circle class="pt" cx="'+X(pts.length-1).toFixed(1)+'" cy="'+Y(last).toFixed(1)+'" r="3.6"/>');
  var xlabs=[0,Math.floor((pts.length-1)/2),pts.length-1];
  var seen={};
  xlabs.forEach(function(i){
    if(seen[i])return; seen[i]=1;
    s.push('<text class="ax" x="'+X(i).toFixed(1)+'" y="'+(H-10)+'" text-anchor="'+
      (i===0?'start':(i===pts.length-1?'end':'middle'))+'">'+mmdd(pts[i].date)+'</text>');
  });
  if(predVals.length){
    s.push('<text class="ax" x="'+XP(FORECAST).toFixed(1)+'" y="'+(H-10)+
      '" text-anchor="end">+'+FORECAST+'d</text>');
  }
  s.push('</svg>');
  box.innerHTML=s.join('');

  var leg=[];
  leg.push('<span><i style="background:var(--brand)"></i>'+t('累计积分')+'</span>');
  if(maxD>0) leg.push('<span><i class="b2"></i>'+t('当日增量')+'</span>');
  if(predVals.length) leg.push('<span><i style="background:var(--amber)"></i>'+
    tf('未来 %s 天预测',FORECAST)+'</span>');
  legend.innerHTML=leg.join('');

  // 跨度取"记录数"与"首尾日期差+1"中较大的一个：连续记录时约等于天数，
  // 中间有缺口时按真实跨度算，避免日均被高估。
  var span=Math.max(pts.length,dayDiff(pts[0].date,pts[pts.length-1].date)+1);
  var gained=last-pts[0].total;
  var avgAll=span>0?(gained/span):0;
  var st=[];
  st.push('<span>'+tf('近 %s 天',span)+' <b>'+(gained>=0?'+':'')+fmtNum(gained)+'</b></span>');
  st.push('<span>'+t('日均')+' <b>'+(avgAll>=0?'+':'')+avgAll.toFixed(1)+'</b></span>');
  if(tail.length>=3&&tail.length<pts.length-1)
    st.push('<span>'+t('最近 7 天')+' <b>'+(avg7>=0?'+':'')+avg7.toFixed(1)+'</b></span>');
  if(avg7>0){
    st.push('<span>'+tf('%s 天后约',FORECAST)+' <b>'+fmtNum(last+avg7*FORECAST)+'</b></span>');
  }else if(avg7<=0&&tail.length){
    st.push('<span>'+t('走势持稳（无新增）')+'</span>');
  }
  stats.innerHTML=st.join('');
  if(badge)badge.textContent=span>0?(tf('近 %s 天',span)+' '+(gained>=0?'+':'')+fmtNum(gained)):'';
}
/* ---- 凭据健康度（refreshToken 剩余）---- */
function credMetricHTML(d){
  var h=(d&&d.cred_health)||{};
  var st=h.state||'unknown', days=h.days_left;
  var cls='',v='—',k=t('凭据健康'),k2='';
  if(st==='ok'||st==='warn'||st==='danger'){
    v=(days!=null?String(Math.max(0,Math.round(days))):'—');
    k=t('凭据剩余（天）');
    k2=t('健康')+' '+Math.round(h.pct||0)+'% · '+(h.refresh_expires_at||'').slice(5,16);
    if(st==='warn')cls=' warn'; if(st==='danger')cls=' bad';
  }else if(st==='expired'){
    v='0'; k=t('凭据剩余（天）'); k2=t('已过期，需重新登录一次'); cls=' bad';
  }else if(st==='missing'){
    v='—'; k=t('未找到凭据'); k2=t('需打开桌面端登录一次'); cls=' bad';
  }else{
    k2=t('暂不可用（稍后自动刷新）');
  }
  var pct=Math.max(0,Math.min(100,h.pct||0));
  var tip=(h.refresh_expires_at||'')?(t('到期')+' '+h.refresh_expires_at):'';
  var bar=(st==='ok'||st==='warn'||st==='danger')
    ? '<div class="bar"><i style="width:'+pct.toFixed(1)+'%"></i></div>' : '';
  return '<div class="metric'+cls+'" title="'+esc(tip)+'">'+
    '<div class="v sm">'+v+'</div><div class="k">'+esc(k)+'</div>'+bar+
    '<div class="k2">'+esc(k2)+'</div></div>';
}
/* ---- 失败自动诊断 + 一键修复 ---- */
/* 失败类型词典：[中文, English, 日本語]；cause 为对应原因文字。 */
var DIAG={
 auth_expired:{label:['凭据已过期','Credential expired','認証の期限切れ'],
   cause:['refreshToken 已到期，客户端无法再自动续期，之后每次签到都会失败。必须重新登录一次。',
          'The refresh token expired; the client can no longer renew it. Re-login is required.',
          '更新トークンの期限切れ、自動更新不可。再ログインが必要です。']},
 auth:{label:['登录失效','Sign-in failed','ログイン失敗'],
   cause:['登录态已失效，客户端无法用现有凭据签到，需要重新登录一次。',
          'The session is invalid; re-login is required.',
          'セッション無効。再ログインが必要です。']},
 noauth:{label:['未登录','Not signed in','未ログイン'],
   cause:['找不到登录凭据文件，说明还没登录过或凭据被清除。请打开桌面端登录一次。',
          'No credential file found. Please sign in via the desktop app.',
          '認証ファイルがありません。デスクトップ版でログインしてください。']},
 inactive:{label:['档位未解锁','Tier locked','ティア未解放'],
   cause:['当前解锁档位未激活（多为连续登录天数不足），属于常态而非故障，等条件满足后重试即可。',
          'The unlocked tier is inactive (often insufficient streak days). Normal; retry later.',
          '解放済ティア未活性（連続日数不足等）。正常なので後で再試行を。']},
 forbidden:{label:['权限不足','Forbidden','権限不足'],
   cause:['账号权限或活动条件不满足（HTTP 403），不是登录过期，请检查账号/活动要求。',
          'Account permission or activity condition not met (HTTP 403); not a login expiry.',
          'アカウント権限/条件不满足（HTTP 403）。ログイン期限切れではありません。']},
 network:{label:['网络异常','Network error','ネットワーク異常'],
   cause:['网络不通或请求超时，签到接口没有收到响应。请确认网络后重试。',
          'Network unreachable or request timed out. Check your connection and retry.',
          'ネットワーク不通/タイムアウト。接続確認して再試行。']},
 timeout:{label:['请求超时','Timed out','タイムアウト'],
   cause:['请求超过时间预算被放弃，可能是接口慢或网络抖动。请稍后重试。',
          'The request exceeded its time budget. Retry later.',
          '時間予算を超過。後で再試行を。']},
 busy:{label:['凭据刷新中','Refreshing','認証更新中'],
   cause:['客户端正在刷新凭据（文件被临时占用），这是瞬时状态，无需重新登录，稍等再试即可。',
          'The client is refreshing the credential (file temporarily locked). Transient; retry shortly.',
          'クライアントが認証更新中（一時ロック）。再ログイン不要、少し待って再試行を。']},
 unknown:{label:['未知错误','Unknown error','不明なエラー'],
   cause:['签到脚本异常结束，详情见下方原始日志。',
          'The sign-in script ended abnormally. See raw log below.',
          'スクリプト異常終了。下の生ログ参照。']}
};
var DACT={heal:['重新登录（拉起客户端）','Re-login (open app)','再ログイン（アプリ起動）'],
 retry:['重试','Retry','再試行'],retry_later:['稍后重试','Retry later','後で再試行']};
function dt(o){var i=LANG==='en'?1:(LANG==='ja'?2:0);return (o&&o[i])||(o&&o[0])||'';}
/* 根据签到结果码 + 文案 + 凭据健康度推断失败类型；凭据过期/缺失优先（失败时最想确认的就是它）。 */
function _diagType(d){
  var x=(d&&d.data)||{}, res=String(x.result||'').toUpperCase(),
      rep=(x.report||''), raw=(d&&d.raw)||'', err=(d&&d.stderr)||'',
      cred=(d&&d.cred_health)||{},
      txt=(res+' '+rep+' '+raw+' '+err).toLowerCase();
  if(cred.state==='expired') return 'auth_expired';
  if(cred.state==='missing') return 'noauth';
  if(res==='NO_AUTH') return 'noauth';
  if(res==='INACTIVE') return 'inactive';
  if(res==='FORBIDDEN') return 'forbidden';
  if(res==='NETWORK') return 'network';
  if(res==='TIMEOUT') return 'timeout';
  if(res==='AUTH_BUSY') return 'busy';
  if(res==='AUTH_ERROR'||res==='AUTH_REJECTED'||res==='NO_SESSION') return 'auth';
  if(/auth|令牌|token|登录失效|未授权|401|重新登录|登录过期|expired/.test(txt)) return 'auth';
  if(/未解锁|no buddy|inactive|连登.*不足|档位|锁定/.test(txt)) return 'inactive';
  if(/403|权限|活动条件/.test(txt)) return 'forbidden';
  if(/network|超时|timeout|连不上|无法连接|连接失败|dns|econn|502|503|504|unreachable/.test(txt)) return 'network';
  if(/busy|正在刷新|文件被占用|独占|locked/.test(txt)) return 'busy';
  return 'unknown';
}
function diagFailure(d){
  var type=_diagType(d), info=DIAG[type]||DIAG.unknown,
      cred=(d&&d.cred_health)||{}, li=(LANG==='en'?1:(LANG==='ja'?2:0)),
      isBad=(type==='auth_expired'||type==='auth'||type==='noauth'||type==='forbidden');
  var acts=[];
  if(type==='auth_expired'||type==='auth'||type==='noauth'){
    acts.push({k:'heal',t:DACT.heal});acts.push({k:'retry',t:DACT.retry});
  }else if(type==='inactive'||type==='busy'){
    acts.push({k:'retry',t:DACT.retry_later});
  }else{
    acts.push({k:'retry',t:DACT.retry});
  }
  var cause=dt(info.cause);
  if(cred.state==='danger'&&type!=='auth_expired'){
    cause+='（同时检测到凭据仅剩 '+Math.max(0,Math.round(cred.days_left||0))+' 天，建议尽快重新登录。）';
  }
  var pills='<span class="badge '+(isBad?'b-bad':'b-warn')+'">'+esc(dt(info.label))+'</span>';
  var btns=acts.map(function(a){
    return '<button class="dbtn'+(a.k==='heal'?' dbtn-primary':'')+'" data-dact="'+a.k+'">'+esc(a.t[li])+'</button>';
  }).join('');
  var log=((d&&(d.raw||d.stderr))||'（无输出）');
  return '<div class="card diag '+(isBad?'is-bad':'is-warn')+'">'+
    '<h2>失败诊断 '+pills+'</h2>'+
    '<div class="dcause">'+esc(cause)+'</div>'+
    '<div class="dact">'+btns+'</div>'+
    '<div class="dhint" id="diagHint"></div>'+
    '<details><summary>原始日志</summary><div class="report">'+esc(log)+'</div></details>'+
    '</div>';
}

/* ---- 归档快照对比 ---- */
function archFields(){
  return [
    {k:'result',zh:'结果'},
    {k:'ok',zh:'成功',bool:'yes'},
    {k:'credit',zh:'本次积分',num:1,good:1},
    {k:'total',zh:'累计积分',num:1,good:1},
    {k:'streak',zh:'连续天数',num:1,good:1},
    {k:'attempts',zh:'尝试次数',num:1},
    {k:'mode',zh:'模式'}
  ];
}

function tf(zh){var s=t(zh);for(var i=1;i<arguments.length;i++){s=s.replace('%s',arguments[i]);}return s;}
function renderMilestones(d){
  var box=document.getElementById('msBox');if(!box)return;
  var ms=(d&&d.milestones)||[];if(!ms.length){box.innerHTML='';return;}
  var badge=document.getElementById('msBadge');
  if(badge)badge.textContent=(d&&d.next)?tf('下一目标 %s 天',d.next.days):t('全部达成');
  var h='';
  for(var i=0;i<ms.length;i++){
    var m=ms[i],pct=Math.round((m.progress||0)*100);
    var gap=m.reached?t('已达成'):(m.remaining===1?t('还差 1 天'):tf('还差 %s 天',m.remaining));
    var eta=(!m.reached&&m.eta)?('<div class="ms-eta">'+tf('预计 %s 达成',m.eta)+'</div>'):'';
    h+='<div class="ms-item'+(m.reached?' done':'')+'">'+
       '<div class="ms-top"><span>'+tf('连签 %s 天',m.days)+'</span>'+
       '<span class="ms-gap">'+gap+'</span></div>'+
       '<div class="ms-bar"><div class="ms-fill" style="width:'+pct+'%"></div></div>'+eta+'</div>';
  }
  box.innerHTML=h;
}
/* 历史报告归档：直接链到 reports/ 下的快照，不用开面板 */
var DIFF={i:-1,o:-1};
function renderArchive(d){
  var box=document.getElementById('archiveList');if(!box)return;
  var items=(d&&d.archive_recent)||[];
  var badge=document.getElementById('arcBadge');
  if(badge)badge.textContent=items.length?(t('最近 ')+items.length+t(' 份')):'';
  if(!items.length){box.innerHTML='<div class="hint">'+t('还没有归档，下次运行后自动保存')+'</div>';return;}
  box.innerHTML=items.map(function(it,i){
    var bits=[(it.ts||''),(it.result||'')];
    if(it.credit!=null)bits.push('+'+it.credit);
    if(it.total!=null)bits.push('Σ'+it.total);
    var href='reports/'+encodeURIComponent(it.file||it.evidence||'');
    var open=it.file?(' <a class="arclink" href="'+href+'" target="_blank" rel="noopener">'+t('打开')+'</a>'):'';
    var cmp=(i<items.length-1)?(' <a class="arclink arccmp" data-i="'+i+'" href="javascript:void(0)">'+t('对比')+'</a>'):'';
    return '<div class="hrow"><b>'+esc(bits.filter(Boolean).join(' · '))+'</b>'+open+cmp+'</div>';
  }).join('');
  box.querySelectorAll('.arccmp').forEach(function(el){
    el.addEventListener('click',function(){ showDiff(+el.dataset.i); });
  });
  if(DIFF.i>=0) showDiff(DIFF.i,DIFF.o);
}
/* 两份快照逐项对照：数值给差值，文本给"变了/没变" */
function diffCell(oldV,newV,isNum,good){
  if(oldV==null&&newV==null) return ['same','—'];
  if(isNum&&typeof oldV==='number'&&typeof newV==='number'){
    var d=newV-oldV;
    if(d===0) return ['same','—'];
    return [good?(d>0?'up':'down'):(d>0?'up':'down'),(d>0?'+':'')+fmtNum(d)];
  }
  if(String(oldV)===String(newV)) return ['same','—'];
  return ['chg','→'];
}
function diffVal(v,kind){
  if(v==null||v==='') return '—';
  if(kind==='yes') return v?t('是'):t('否');
  if(kind==='sw') return v?t('开'):t('关');
  return String(v);
}
function showDiff(newIdx,oldIdx){
  var items=PAYLOAD.archive_recent||[];
  var nw=items[newIdx];
  var box=document.getElementById('diffBox'); if(!box)return;
  if(!nw){ box.innerHTML=''; DIFF.i=-1; return; }
  if(oldIdx==null||oldIdx<0||oldIdx>=items.length||oldIdx===newIdx) oldIdx=newIdx+1;
  var od=items[oldIdx];
  DIFF.i=newIdx; DIFF.o=oldIdx;
  if(!od){ box.innerHTML='<div class="diff"><div class="none">'+t('没有可比对的更早记录')+'</div></div>'; return; }

  var opts=items.map(function(it,j){
    if(j===newIdx) return '';
    return '<option value="'+j+'"'+(j===oldIdx?' selected':'')+'>'+esc(it.ts||('#'+j))+'</option>';
  }).join('');

  var h=['<div class="diff"><div class="diff-h">',
    '<span>'+t('对比基准')+'</span>',
    '<select id="diffOld">'+opts+'</select>',
    '<span>→ '+esc((nw.ts||'').slice(0,16))+'</span>',
    '<span class="diff-close" title="'+t('关闭')+'">×</span>',
    '</div><table>'];

  var F=archFields();
  F.forEach(function(f){
    var ov=od[f.k], nv=nw[f.k];
    var c=diffCell(ov,nv,!!f.num,!!f.good);
    h.push('<tr'+(c[0]==='same'?'':' class="chgrow"')+'><td>'+t(f.zh)+'</td><td class="n">'+
      diffVal(ov,f.bool)+'</td><td class="n">'+diffVal(nv,f.bool)+
      '</td><td class="n '+c[0]+'">'+c[1]+'</td></tr>');
  });

  var eo=od.env||{}, en=nw.env||{};
  var hasEnv=Object.keys(en).length||Object.keys(eo).length;
  if(hasEnv){
    function envRow(label,ov,nv,isNum,good,kind){
      var c=diffCell(ov,nv,!!isNum,!!good);
      h.push('<tr'+(c[0]==='same'?'':' class="chgrow"')+'><td>'+esc(label)+
        '</td><td class="n">'+diffVal(ov,kind)+'</td><td class="n">'+diffVal(nv,kind)+
        '</td><td class="n '+c[0]+'">'+c[1]+'</td></tr>');
    }
    envRow(t('推送通道'),eo.channel,en.channel);
    envRow(t('推送开关'),eo.notify_on,en.notify_on,0,0,'sw');
    envRow(t('推送就绪'),eo.notify_ready,en.notify_ready,0,0,'sw');
    var to=Array.isArray(eo.tasks)?eo.tasks.length:null;
    var tn=Array.isArray(en.tasks)?en.tasks.length:null;
    envRow(t('计划任务'),to,tn,1,1);
    envRow(t('历史天数'),eo.history_days,en.history_days,1,1);
    envRow(t('凭据剩余（天）'),eo.cred_days,en.cred_days,1,1);
  }else{
    h.push('<tr><td>'+t('环境')+'</td><td colspan="3" class="same">'+
      t('两份快照都没记录环境信息（旧版本生成）')+'</td></tr>');
  }
  h.push('</table></div>');
  box.innerHTML=h.join('');

  var sel=box.querySelector('#diffOld');
  if(sel) sel.onchange=function(){ showDiff(newIdx,parseInt(this.value,10)); };
  var cls=box.querySelector('.diff-close');
  if(cls) cls.onclick=function(){ DIFF.i=-1; box.innerHTML=''; };
}

document.getElementById('t').textContent='运行模式：'+PAYLOAD.mode+' · 退出码：'+PAYLOAD.exit_code+' · 尝试 '+PAYLOAD.attempts_used+' 次 · 生成时间：'+new Date().toLocaleString();
/* ---- 设置：壁纸模式 / 遮罩浓度 / 点击隐藏 ---- */
var gear=document.getElementById('gear'),panel=document.getElementById('settings');
var wpSeg=document.getElementById('wpSeg'),veilInput=document.getElementById('veil'),clickHide=document.getElementById('clickHide');
function applyWp(m){document.body.classList.remove('wp-contain','wp-cover','wp-off');
document.body.classList.add('wp-'+m);
wpSeg.querySelectorAll('button').forEach(function(b){b.classList.toggle('on',b.dataset.wp===m);});
try{localStorage.setItem('wb-wallpaper',m);}catch(e){}}
wpSeg.addEventListener('click',function(e){var b=e.target.closest('button[data-wp]');if(!b)return;applyWp(b.dataset.wp);});
var savedWp='contain';try{savedWp=localStorage.getItem('wb-wallpaper')||'contain';}catch(e){}
if(['contain','cover','stretch','off'].indexOf(savedWp)<0)savedWp='contain';applyWp(savedWp);
function applyVeil(v){document.documentElement.style.setProperty('--veil',(v/100).toFixed(3));
try{localStorage.setItem('wb-veil',v);}catch(e){}}
veilInput.addEventListener('input',function(){applyVeil(+veilInput.value);});
var savedVeil=50;try{savedVeil=parseInt(localStorage.getItem('wb-veil')||'50',10)||50;}catch(e){}
veilInput.value=savedVeil;applyVeil(savedVeil);
function applyClickHide(on){document.body.classList.toggle('no-click-hide',!on);
try{localStorage.setItem('wb-clickhide',on?'1':'0');}catch(e){}}
clickHide.addEventListener('change',function(){applyClickHide(clickHide.checked);});
var savedHide=true;try{savedHide=localStorage.getItem('wb-clickhide')!=='0';}catch(e){}
clickHide.checked=savedHide;applyClickHide(savedHide);
/* 自定义壁纸：选图替换 / 恢复默认 */
var wpFile=document.getElementById('wpFile'),wpReset=document.getElementById('wpReset');
function setWpImg(u){document.documentElement.style.setProperty('--wp-img',u);}
try{var s=localStorage.getItem('wb-wallpaper-img');if(s)setWpImg(s);}catch(e){}
wpFile.addEventListener('change',function(){
  var f=wpFile.files&&wpFile.files[0];if(!f)return;
  var r=new FileReader();
  r.onload=function(){
    try{localStorage.setItem('wb-wallpaper-img',r.result);}catch(err){alert('图片体积过大，无法长期记忆，仅本次会话生效。');}
    setWpImg('url("'+r.result+'")');
  };
  r.readAsDataURL(f);
});
wpReset.addEventListener('click',function(){
  setWpImg('url("wallpaper.jpg")');
  try{localStorage.removeItem('wb-wallpaper-img');}catch(e){}
  wpFile.value='';
});
/* ---- 默认壁纸：写回本机 wallpaper.jpg，报告页 / 开场页一起变 ---- */
var wpDefThumb=document.getElementById('wpDefThumb'),wpDefState=document.getElementById('wpDefState'),
wpDefMeta=document.getElementById('wpDefMeta'),wpDefPick=document.getElementById('wpDefPick'),
wpDefReset=document.getElementById('wpDefReset'),wpDefRow=document.getElementById('wpDefRow');
var WP_CAN_SET=(location.protocol==='http:'||location.protocol==='https:');
function wpDefPaint(m){
  if(!m||!m.exists){wpDefState.textContent=t('失败');wpDefMeta.textContent='—';return;}
  wpDefState.textContent=m.custom?t('自定义'):t('内置');
  wpDefMeta.textContent=(m.w?m.w+'×'+m.h+' · ':'')+m.kb+' KB · '+m.mtime;
}
function wpDefApply(ver){   /* 换了默认，本浏览器的自定义图 / 轮播要让位，否则看不出变化 */
  try{localStorage.removeItem('wb-wallpaper-img');}catch(e){}
  if(carouselOn.checked)setCarousel(false);
  setWpImg('url("wallpaper.jpg?t='+ver+'")');
}
if(WP_CAN_SET){
  fetch('/wallpaper/meta').then(function(r){return r.json();}).then(function(m){
    wpDefPaint(m);if(m.ver)wpDefThumb.src='wallpaper.jpg?t='+m.ver;
  }).catch(function(){});
}else{
  wpDefRow.classList.add('off');
  wpDefState.textContent=t('仅主控台可改');
  wpDefMeta.textContent='';
}
wpDefThumb.addEventListener('click',function(){
  var w=window.open('','_blank');if(w)w.location='wallpaper.jpg?t='+Date.now();
});
wpDefPick.addEventListener('change',function(){
  var f=wpDefPick.files&&wpDefPick.files[0];if(!f)return;
  wpDefState.textContent=t('上传中…');
  fetch('/wallpaper/set',{method:'POST',body:f}).then(function(r){return r.json();}).then(function(j){
    wpDefPick.value='';
    if(!j.ok){wpDefState.textContent=t('失败');wpDefMeta.textContent=j.msg||'';return;}
    wpDefPaint(j.meta);wpDefThumb.src='wallpaper.jpg?t='+j.meta.ver;
    wpDefApply(j.meta.ver);
  }).catch(function(){wpDefPick.value='';wpDefState.textContent=t('失败');});
});
wpDefReset.addEventListener('click',function(){
  if(!window.confirm('把默认壁纸还原成出厂那张？'))return;
  fetch('/wallpaper/reset',{method:'POST'}).then(function(r){return r.json();}).then(function(j){
    if(!j.ok){wpDefState.textContent=t('失败');wpDefMeta.textContent=j.msg||'';return;}
    wpDefPaint(j.meta);wpDefThumb.src='wallpaper.jpg?t='+j.meta.ver;
    wpDefApply(j.meta.ver);
  }).catch(function(){wpDefState.textContent=t('失败');});
});
gear.addEventListener('click',function(e){e.stopPropagation();panel.classList.toggle('open');});
document.addEventListener('click',function(e){
  if(!panel.contains(e.target)&&e.target!==gear)panel.classList.remove('open');});
/* 点背景隐藏数据 / 再点恢复（避开卡片、按钮、设置面板） */
document.addEventListener('click',function(e){
  if(e.target.closest('.gear,#settings,.card,button,summary,a'))return;
  if(document.body.classList.contains('no-click-hide'))return;
  document.body.classList.toggle('hide-ui');
});
/* 默认展开原始 JSON */
var jsonOpen=document.getElementById('jsonOpen');
try{expandJson=localStorage.getItem('wb-jsonopen')==='1';}catch(e){}
jsonOpen.checked=expandJson;
jsonOpen.addEventListener('change',function(){
  expandJson=jsonOpen.checked;
  try{localStorage.setItem('wb-jsonopen',expandJson?'1':'0');}catch(e){}
  var dt=document.querySelector('details');
  if(dt)dt.open=expandJson;
});

/* 暗色模式 */
var darkToggle=document.getElementById('darkToggle');
var darkOn=false;
try{darkOn=localStorage.getItem('wb-dark')==='1';}catch(e){}
darkToggle.checked=darkOn;
document.body.classList.toggle('dark',darkOn);
darkToggle.addEventListener('change',function(){
  darkOn=darkToggle.checked;
  document.body.classList.toggle('dark',darkOn);
  try{localStorage.setItem('wb-dark',darkOn?'1':'0');}catch(e){}
});

/* 壁纸轮播 */
var carouselOn=document.getElementById('carouselOn'),carouselInt=document.getElementById('carouselInt'),
carouselIntVal=document.getElementById('carouselIntVal'),carouselAdd=document.getElementById('carouselAdd'),
carouselClear=document.getElementById('carouselClear'),carouselCount=document.getElementById('carouselCount'),
carouselRow=document.getElementById('carouselRow'),carouselRow2=document.getElementById('carouselRow2'),
carouselRow3=document.getElementById('carouselRow3');
var wpList=[],carouselTimer=null;
try{wpList=JSON.parse(localStorage.getItem('wb-wp-list')||'[]');}catch(e){}
function saveList(){try{localStorage.setItem('wb-wp-list',JSON.stringify(wpList));}catch(err){alert('图片过多，无法长期记忆，仅本次会话生效。');}}
function updateCount(){carouselCount.textContent=wpList.length+' 张';}
function stopCarousel(){if(carouselTimer){clearInterval(carouselTimer);carouselTimer=null;}}
function startCarousel(){
  stopCarousel();
  if(!wpList.length)return;
  var i=0;
  var step=function(){setWpImg('url("'+wpList[i%wpList.length]+'")');i++;};
  step();
  carouselTimer=setInterval(step,Math.max(3,+carouselInt.value)*1000);
}
function setCarousel(on){
  carouselOn.checked=on;
  [carouselRow,carouselRow2,carouselRow3].forEach(function(r){r.classList.toggle('off',!on);});
  try{localStorage.setItem('wb-carousel',on?'1':'0');}catch(e){}
  if(on)startCarousel();else stopCarousel();
}
carouselAdd.addEventListener('change',function(){
  var files=carouselAdd.files;if(!files||!files.length)return;
  var pending=files.length,done=0;
  Array.prototype.forEach.call(files,function(f){
    var r=new FileReader();
    r.onload=function(){wpList.push(r.result);if(++done===pending){saveList();updateCount();if(carouselOn.checked)startCarousel();}};
    r.readAsDataURL(f);
  });
  carouselAdd.value='';
});
carouselClear.addEventListener('click',function(){
  wpList=[];saveList();updateCount();stopCarousel();setWpImg('url("wallpaper.jpg")');});
carouselInt.addEventListener('input',function(){
  carouselIntVal.textContent=carouselInt.value+'秒';
  try{localStorage.setItem('wb-carousel-int',carouselInt.value);}catch(e){}
  if(carouselOn.checked)startCarousel();
});
carouselOn.addEventListener('change',function(){setCarousel(carouselOn.checked);});
try{carouselInt.value=localStorage.getItem('wb-carousel-int')||'10';}catch(e){}
carouselIntVal.textContent=carouselInt.value+'秒';
updateCount();
var savedCarousel=false;
try{savedCarousel=localStorage.getItem('wb-carousel')==='1';}catch(e){}
setCarousel(savedCarousel);

/* 运行历史（最近 10 次）*/
var HKEY='wb-history',HMAX=10;
function loadHistory(){try{return JSON.parse(localStorage.getItem(HKEY)||'[]');}catch(e){return [];}}
function saveHistory(h){try{localStorage.setItem(HKEY,JSON.stringify(h.slice(0,HMAX)));}catch(e){}}
function fmtTime(t){var d=new Date(t),p=function(n){return String(n).padStart(2,'0');};
return p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());}
function renderHistory(){
  var h=loadHistory(),box=document.getElementById('historyList');
  if(!h.length){box.innerHTML='<div class="hint">暂无记录，运行后会自动保存最近 '+HMAX+' 次。</div>';return;}
  box.innerHTML=h.map(function(it){
    var ic='';
    var s=it.streak!=null?'连'+it.streak+'天':'';
    var c=it.total!=null?'累'+it.total+'分':'';
    var head=[ic,fmtTime(it.t),it.mode,s,c].filter(Boolean).join(' · ');
    var rep=it.report?'<div class="hrep">'+esc(it.report)+'</div>':'';
    return '<div class="hrow"><b>'+head+'</b>'+rep+'</div>';
  }).join('');
}
function pushHistory(d){
  var x=(d&&d.data)||{};
  var h=loadHistory();
  h.unshift({t:Date.now(),mode:PAYLOAD.mode,ok:!!(d&&d.ok),exit:d&&d.exit_code,
    streak:x.streak_days,total:x.total_credits,today:x.today_credit,
    report:(x.report||'').slice(0,60)});
  saveHistory(h);renderHistory();
}
document.getElementById('hclear').addEventListener('click',function(){saveHistory([]);renderHistory();});
renderHistory();
pushHistory(PAYLOAD);
render(PAYLOAD);
applyAlert(PAYLOAD);
renderCalendar(PAYLOAD);
renderMilestones(PAYLOAD);
renderArchive(PAYLOAD);
renderTrend(PAYLOAD);
document.getElementById('trendSeg').addEventListener('click',function(e){
  var b=e.target.closest('button[data-days]'); if(!b)return;
  TREND_DAYS=(b.dataset.days==='all')?'all':parseInt(b.dataset.days,10);
  this.querySelectorAll('button').forEach(function(x){x.classList.toggle('on',x===b);});
  renderTrend(PAYLOAD);
});

/* ---- 语言切换 ---- */
function refreshReport(){
  try{ render(PAYLOAD); }catch(e){}
  try{ renderCalendar(PAYLOAD); }catch(e){}
  try{ renderMilestones(PAYLOAD); }catch(e){}
  try{ renderArchive(PAYLOAD); }catch(e){}
  try{ renderTrend(PAYLOAD); }catch(e){}
  try{ renderHistory(); }catch(e){}
  // 上面几块是用 t() 重绘的，但 render() 生成的卡片里仍有硬编码中文，
  // 重建后必须再扫一遍，否则切语言时会残留中文。
  try{ localizeDom(document.body); }catch(e){}
}
document.getElementById('langSeg').addEventListener('click', function(e){
  const b=e.target.closest('button[data-lang]'); if(!b) return;
  LANG=b.dataset.lang;
  applyLang();
  refreshReport();
});
let _localizing=false,_locTimer=null;
const _locObs=new MutationObserver(function(){
  if(_localizing) return;
  clearTimeout(_locTimer);
  _locTimer=setTimeout(function(){
    _localizing=true;
    try{ localizeDom(document.body); }finally{ _localizing=false; }
  },60);
});
_locObs.observe(document.body,{childList:true,subtree:true,characterData:true});
/* 失败诊断：一键修复按钮（重试走 /start，重新登录走 /heal） */
function diagHint(m){var el=document.getElementById('diagHint');if(el)el.textContent=m||'';}
function dActRetry(btn){
  if(btn){btn.disabled=true;btn.textContent='重试中…';}
  diagHint('已触发重试，正在重新签到…');
  fetch('/start',{method:'POST'}).catch(function(){
    diagHint('本页无法直接重试，请回到主控台点「立即签到」。');
  });
  var iv=setInterval(function(){
    fetch('/status').then(function(r){return r.json();}).then(function(j){
      if(j&&j.done){clearInterval(iv);location.reload();}
    }).catch(function(){
      clearInterval(iv);diagHint('重试状态不可达，请手动刷新页面或回主控台。');
    });
  },700);
}
function dActHeal(btn){
  diagHint('正在拉起 WorkBuddy 桌面端…');
  fetch('/heal',{method:'POST'}).then(function(r){return r.json();}).then(function(j){
    diagHint((j&&j.msg)||'已尝试拉起客户端，请在客户端内完成登录后点「重试」。');
  }).catch(function(){
    diagHint('无法自动拉起，请手动打开 WorkBuddy 桌面端登录。');
  });
}
document.addEventListener('click',function(e){
  var b=e.target.closest('[data-dact]');if(!b)return;
  var act=b.getAttribute('data-dact');
  if(act==='retry')dActRetry(b);
  else if(act==='heal')dActHeal(b);
});

/* ---- 导出与分享：PNG / PDF / 连签卡片（需在主控台 http 环境下使用）---- */
var exbar=document.getElementById('exbar');
function exDownload(blob,name){
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;
  document.body.appendChild(a);a.click();
  setTimeout(function(){try{URL.revokeObjectURL(a.href);a.remove();}catch(e){}},2000);
}
function exBusy(btn,on){
  if(!btn)return;
  if(on){btn.dataset.lab=btn.textContent;btn.disabled=true;btn.textContent='生成中…';}
  else{btn.disabled=false;btn.textContent=btn.dataset.lab||btn.textContent;}
}
function exNet(){return (location.protocol==='http:'||location.protocol==='https:');}
exbar.addEventListener('click',function(e){
  var b=e.target.closest('button[data-x]');if(!b)return;
  var x=b.getAttribute('data-x');
  if(x==='card'){                       // 复用已有的连签分享卡片
    if(!exNet()){alert('连签卡片需在主控台内下载，请回到主控台「报告」页操作。');return;}
    exBusy(b,true);
    fetch('/export/card.png').then(function(r){if(!r.ok)throw 0;return r.blob();}).then(function(bl){
      exDownload(bl,'workbuddy-streak-card.png');
    }).catch(function(){alert('连签卡片生成失败，请先在主控台「分享卡片」页点一次「重新生成」。');})
    .finally(function(){exBusy(b,false);});
    return;
  }
  if(!exNet()){alert('导出功能需在主控台内使用：请回到主控台点「报告」页，再点导出。');return;}
  exBusy(b,true);
  fetch('/export/report.'+x).then(function(r){if(!r.ok)throw 0;return r.blob();}).then(function(bl){
    exDownload(bl,'workbuddy-report.'+(x==='pdf'?'pdf':'png'));
  }).catch(function(){alert('导出失败，请重试，或回主控台操作。');})
  .finally(function(){exBusy(b,false);});
});
/* 导出（无头截图）模式：隐藏交互 UI，确保画面干净 */
(function(){
  try{
    if(new URLSearchParams(location.search).get('export')==='1'){
      document.body.classList.add('export');
      document.body.classList.remove('hide-ui');
      if(typeof setCarousel==='function'){try{setCarousel(false);}catch(e){}}
      /* 把整页高度写进 title，供无头截图按需设定窗口高度（否则只能截到可视区） */
      var _h=function(){try{document.title='SH:'+Math.max(document.documentElement.scrollHeight,document.body.scrollHeight);}catch(e){}};
      if(document.readyState==='complete'){setTimeout(_h,250);}
      else{window.addEventListener('load',function(){setTimeout(_h,250);});}
    }
  }catch(e){}
})();

applyLang();

/* ---- 提醒规则：服务端持久化，仅在 http(s) 主控台下可改（与壁纸同逻辑） ---- */
var RULES_CAN_SET=(location.protocol==='http:'||location.protocol==='https:');
var ruleWarn=document.getElementById('ruleWarn'),ruleDanger=document.getElementById('ruleDanger'),
    ruleStreakOn=document.getElementById('ruleStreakOn'),ruleStreakDays=document.getElementById('ruleStreakDays'),
    ruleDropOn=document.getElementById('ruleDropOn'),ruleDropPct=document.getElementById('ruleDropPct'),
    ruleDropAbs=document.getElementById('ruleDropAbs'),rulesNote=document.getElementById('rulesNote'),
    rulesSaved=document.getElementById('rulesSaved');
function clampInt(v,lo,hi,def){var n=parseInt(v,10);if(isNaN(n))return def;return Math.max(lo,Math.min(hi,n));}
function rulesCollect(){
  return {
    credential:{warn_days:clampInt(ruleWarn.value,1,60,7),danger_days:clampInt(ruleDanger.value,1,60,3)},
    streak:{enabled:ruleStreakOn.checked,target_days:clampInt(ruleStreakDays.value,1,365,30)},
    credit_drop:{enabled:ruleDropOn.checked,pct:clampInt(ruleDropPct.value,0,100,30),abs:clampInt(ruleDropAbs.value,0,500,30)}
  };
}
function rulesPaint(r){
  if(!r)return;
  try{
    var c=r.credential||{}; ruleWarn.value=c.warn_days!=null?c.warn_days:7; ruleDanger.value=c.danger_days!=null?c.danger_days:3;
    var s=r.streak||{}; ruleStreakOn.checked=!!s.enabled; ruleStreakDays.value=s.target_days!=null?s.target_days:30;
    var d=r.credit_drop||{}; ruleDropOn.checked=!!d.enabled; ruleDropPct.value=d.pct!=null?d.pct:30; ruleDropAbs.value=d.abs!=null?d.abs:30;
  }catch(e){}
}
function rulesSave(){
  if(!RULES_CAN_SET)return;
  fetch('/rules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(rulesCollect())})
    .then(function(r){return r.json();}).then(function(j){
      if(j&&j.ok){ rulesSaved.textContent=t('已保存'); setTimeout(function(){ if(rulesSaved)rulesSaved.textContent=''; },1500); }
    }).catch(function(){});
}
if(RULES_CAN_SET){
  fetch('/rules').then(function(r){return r.json();}).then(rulesPaint).catch(function(){});
  [ruleWarn,ruleDanger,ruleStreakDays,ruleDropPct,ruleDropAbs].forEach(function(el){
    el.addEventListener('change',rulesSave);});
  [ruleStreakOn,ruleDropOn].forEach(function(el){el.addEventListener('change',rulesSave);});
}else{
  document.querySelectorAll('.rule-row').forEach(function(el){el.classList.add('off','hidden');});
  if(rulesNote){ rulesNote.classList.remove('off'); rulesNote.style.display='flex'; }
}
</script></body></html>"""


def main():
    if not os.path.exists(PY):
        print("未找到 python：", PY, file=sys.stderr); sys.exit(1)
    if not os.path.exists(SIGNIN):
        print("未找到 signin.py：", SIGNIN, file=sys.stderr); sys.exit(1)
    payload = run()
    # 推送结果（未启用时内部自动跳过；任何异常都不影响报告生成）
    try:
        import notify
        notify.push(payload, MODE)
    except Exception:
        pass
    # 记录签到历史（仅成功时）并注入日历数据，供热力图渲染
    try:
        _record_history(payload)
    except Exception:
        pass
    try:
        payload["calendar_days"], payload["calendar_today"] = _load_calendar()
    except Exception:
        payload["calendar_days"] = []
        payload["calendar_today"] = datetime.date.today().isoformat()
    # 连签里程碑：用本次运行的最新 streak（历史里没有今天时也能算准）
    try:
        payload["milestones"] = milestone.compute((payload.get("data") or {}).get("streak_days"))
    except Exception:
        payload["milestones"] = {"milestones": [], "next": None, "streak": 0}
    # 积分趋势：历史里每天的 total/credit，供折线图与预测外推
    try:
        payload["credit_series"] = history.series(120)
    except Exception:
        payload["credit_series"] = []
    # 凭据健康度：提前预警 refreshToken 到期（不读 token 内容）
    try:
        payload["cred_health"] = health.cred_health()
    except Exception:
        payload["cred_health"] = {"state": "unknown"}
    # 历史报告快照列表（报告页直接用相对链接打开，不需要面板）
    try:
        payload["archive_recent"] = [x for x in archive.list_reports(10) if x.get("has_report")]
    except Exception:
        payload["archive_recent"] = []
    html = TEMPLATE.replace("/*DATA*/", json.dumps(payload, ensure_ascii=False))
    out_path = os.path.join(HERE, "report.html")
    # 原子写入：写一半崩溃也不会把 report.html 弄成半截文件
    store.atomic_write_text(out_path, html)
    print("已生成报告：", out_path)
    # 归档快照 + 原始响应留证（失败不影响主流程）
    if not NO_ARCHIVE:
        try:
            entry = archive.save(html, payload, MODE)
            if entry:
                print("已归档：", entry["file"], "| 凭证：", entry["evidence"])
        except Exception as e:
            print("归档失败（不影响报告）：", e)
    # 顺手刷新「签到总览」，这样点开永远是新的
    try:
        import board
        ok, msg = board.write()
        if ok:
            print("已更新总览：", board.OUT)
    except Exception as e:
        print("总览更新失败（不影响报告）：", e)
    print("ok=", payload["ok"], "exit_code=", payload["exit_code"])
    if OPEN_AFTER:
        try:
            from browser_open import open_url
            open_url("file:///" + out_path.replace("\\", "/"))
        except Exception:
            pass


if __name__ == "__main__":
    main()
