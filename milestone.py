#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""milestone.py —— 连签里程碑进度。

为什么要单独算：
    面板（127.0.0.1:8765）和报告页（file://）是两套前端，如果各自用
    `streak_days` 现算，很容易出现"面板说还差 1 天、报告说还差 2 天"。
    所以统一在这里算，两个页面只负责画。

输出给前端的内容（全部是可序列化的原始值，不做中文拼接）：
    {
      "streak": 6,              # 当前连续天数
      "best": 12,               # 历史最长连续（从签到历史推算）
      "today_signed": True,     # 今天是否已签
      "milestones": [
         {"days": 7, "label": "7 天", "reached": False,
          "remaining": 1, "progress": 0.857, "eta": "2026-10-19"}
      ],
      "next": {...} 或 None,     # 下一个未达成的里程碑
      "gap_to_best": 6           # 离自己历史最好成绩还差几天（趣味数据）
    }

注意：里程碑只写"天数"，不编造具体奖励内容——脚本里没有奖励细则，
写死一个"7 天送 100 积分"这种文案，一旦规则变了就是错的。
"""
import datetime

import history

# 里程碑档位：只放"有实际意义"的节点，别堆太多导致进度条没有存在感
TIERS = [7, 30, 100]


def _best_streak(days_map):
    """从签到历史里推算最长连续天数（按日期连续性）。"""
    dates = []
    for k in days_map:
        try:
            dates.append(datetime.date.fromisoformat(k))
        except Exception:
            continue
    if not dates:
        return 0
    dates.sort()
    best = cur = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            cur += 1
            best = max(best, cur)
        elif (dates[i] - dates[i - 1]).days == 0:
            continue
        else:
            cur = 1
    return best


def _latest_streak(days_map):
    """取最近一条有 streak 的记录。"""
    for k in sorted(days_map.keys(), reverse=True):
        v = days_map.get(k)
        if isinstance(v, dict) and isinstance(v.get("streak"), int):
            return v["streak"]
    return None


def compute(streak=None, today=None):
    """计算里程碑状态。streak 传入则优先（用本次运行的最新值）。"""
    days_map = {}
    try:
        days_map = history.load_store() or {}
    except Exception:
        days_map = {}

    today_d = today or datetime.date.today()
    if isinstance(today_d, str):
        try:
            today_d = datetime.date.fromisoformat(today_d)
        except Exception:
            today_d = datetime.date.today()

    if streak is None:
        streak = _latest_streak(days_map)
    if streak is None:
        streak = 0
    try:
        streak = int(streak)
    except Exception:
        streak = 0
    streak = max(0, streak)

    today_key = today_d.isoformat()
    today_signed = today_key in days_map or streak > 0

    # 若本次运行确认今天已签，但历史里还没有今天（记录失败），按 streak 兜底
    best = max(_best_streak(days_map), streak)

    items = []
    for tier in TIERS:
        reached = streak >= tier
        remaining = 0 if reached else tier - streak
        progress = 1.0 if reached else round(min(1.0, streak / float(tier)), 4)
        eta = None
        if not reached and streak > 0:
            try:
                eta = (today_d + datetime.timedelta(days=remaining)).isoformat()
            except Exception:
                eta = None
        items.append({
            "days": tier,
            "label": "%d 天" % tier,
            "reached": reached,
            "remaining": remaining,
            "progress": progress,
            "current": streak,
            "eta": eta,
        })

    nxt = next((x for x in items if not x["reached"]), None)
    return {
        "ok": True,
        "streak": streak,
        "best": best,
        "gap_to_best": max(0, best - streak),
        "today": today_key,
        "today_signed": bool(today_signed),
        "milestones": items,
        "next": nxt,
        "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(compute(), ensure_ascii=False, indent=2))
