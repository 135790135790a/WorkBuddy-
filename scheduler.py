#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scheduler.py —— 用 Windows 计划任务 COM 接口管理 WorkBuddy 签到任务。

不依赖 schtasks.exe（可能被安全策略禁用），直接调用 Task Scheduler 的
COM 服务（Schedule.Service），走已安装的 pywin32。所有操作限定在
「名字以 WorkBuddy 开头」的命名空间内，避免误伤系统任务。

公共 API：
    list_tasks()                          -> [ {name, enabled, state, next_run, ...} ]
    create_or_update(name, times, mode)   -> 创建或更新（times 为 ["HH:MM", ...]）
    delete_task(name)                     -> 删除
    run_task(name)                        -> 立即运行
    set_enabled(name, enabled)            -> 启用 / 停用
"""
import datetime
import os
import re

import pythoncom
import win32com.client

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHONW = r"C:\Users\86139\AppData\Local\Programs\Python\Python311\pythonw.exe"
SIGNIN = os.path.join(HERE, "signin.py")
RUNNER = os.path.join(HERE, "notify-run.py")  # 跑完签到的推送包装器
NAMESPACE_PREFIX = "workbuddy"  # 小写比较用

ALLOWED_MODES = ("silent", "silent-poll", "auto", "growth", "status")

# Task Scheduler COM 常量（避免依赖 win32com.client.constants 是否齐全）
_TASK_ACTION_EXEC = 0
_TASK_TRIGGER_DAILY = 2
_TASK_CREATE_OR_UPDATE = 6
_TASK_LOGON_INTERACTIVE_TOKEN = 3
_TASK_RUNLEVEL_LUA = 0

_STATE = {
    0: "未知", 1: "已禁用", 2: "排队中", 3: "就绪", 4: "运行中",
}
# 0x41303 = 任务尚未运行
_LAST_RESULT = {
    0: "成功",
    267011: "尚未运行",
}


def _com(fn):
    """包装：每个调用独立初始化 COM（ThreadingHTTPServer 每个请求一个新线程）。"""
    def wrapper(*args, **kwargs):
        pythoncom.CoInitialize()
        try:
            return fn(*args, **kwargs)
        finally:
            pythoncom.CoUninitialize()
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _service():
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    return svc


def _folder():
    return _service().GetFolder("\\")


def _safe_name(name):
    """规范任务名：强制 WorkBuddy 前缀、去掉危险字符。"""
    n = (name or "").strip()
    if not n.lower().startswith(NAMESPACE_PREFIX):
        n = "WorkBuddy_" + re.sub(r"[^\w\u4e00-\u9fa5-]", "", n or "Task")
    return n[:120] or "WorkBuddy_Task"


def _guard(name):
    """只允许操作本命名空间内的任务。"""
    n = (name or "").strip()
    if not n.lower().startswith(NAMESPACE_PREFIX):
        raise ValueError("只允许操作 WorkBuddy 开头的任务：%r" % name)
    return n


def _parse_hhmm(s):
    m = re.match(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$", str(s or ""))
    if not m:
        raise ValueError("时间格式应为 HH:MM：%r" % s)
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("时间越界：%r" % s)
    return hh, mm


def _fmt_time(dt):
    """pywintypes.datetime -> 'YYYY-MM-DD HH:MM'；1999 哨兵视为从未运行。"""
    if dt is None:
        return None
    try:
        if getattr(dt, "year", 0) <= 1999:
            return None
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)


def _ts(dt):
    """转成 epoch 毫秒（前端算倒计时用）。

    注意：pywin32 返回的 NextRunTime 会带一个**错误的 UTC 时区**
    （TimeZoneInfo('GMT Standard Time')），但它的年月日时分其实是**本地墙钟时间**。
    所以这里丢掉 tzinfo，按本地时间构造 naive datetime 再取时间戳。
    """
    if dt is None:
        return None
    try:
        if getattr(dt, "year", 0) <= 1999:
            return None
        naive = datetime.datetime(dt.year, dt.month, dt.day,
                                  dt.hour, dt.minute, dt.second)
        return int(naive.timestamp() * 1000)
    except Exception:
        return None


def _action_of(task):
    """读取动作（Path / Arguments），失败返回空。"""
    try:
        acts = task.Definition.Actions
        if acts.Count >= 1:
            a = acts.Item(1)  # COM 集合 1-based
            return {"path": str(a.Path), "arguments": str(a.Arguments)}
    except Exception:
        pass
    return {"path": "", "arguments": ""}


def _args_info(args):
    """从动作参数解析 (runner, mode)。runner: signin | notify。"""
    a = str(args or "")
    runner = "notify" if "notify-run" in a else "signin"
    parts = a.replace('"', " ").split()
    mode = parts[-1] if parts else ""
    return runner, mode


def _trigger_times(task):
    """返回触发时间点列表 ['HH:MM', ...]（仅每日触发器）。"""
    out = []
    try:
        trs = task.Definition.Triggers
        for i in range(1, trs.Count + 1):
            tr = trs.Item(i)
            typ = None
            try:
                typ = int(tr.Type)
            except Exception:
                pass
            if typ == _TASK_TRIGGER_DAILY:
                sb = getattr(tr, "StartBoundary", None)
                hm = None
                if sb is not None:
                    # StartBoundary 读回可能是 datetime，也可能是字符串，都兜住
                    if hasattr(sb, "strftime"):
                        try:
                            hm = sb.strftime("%H:%M")
                        except Exception:
                            hm = None
                    if not hm:
                        m = re.search(r"T(\d{1,2}):(\d{2})", str(sb))
                        if m:
                            hm = "%02d:%s" % (int(m.group(1)), m.group(2))
                out.append(hm or "每日")
    except Exception:
        pass
    return out


def _task_info(task):
    lr = None
    try:
        lr = int(task.LastTaskResult)
    except Exception:
        pass
    act = _action_of(task)
    runner, mode = _args_info(act.get("arguments"))
    return {
        "name": task.Name,
        "enabled": bool(task.Enabled),
        "state": _STATE.get(int(task.State), str(task.State)),
        "state_code": int(task.State),
        "next_run": _fmt_time(task.NextRunTime),
        "next_run_ts": _ts(task.NextRunTime),
        "last_run": _fmt_time(task.LastRunTime),
        "last_result": lr,
        "last_result_text": _LAST_RESULT.get(lr, ("0x%08X" % (lr & 0xFFFFFFFF)) if lr is not None else "—"),
        "missed": int(task.NumberOfMissedRuns or 0),
        "action": act,
        "runner": runner,
        "mode": mode,
        "times": _trigger_times(task),
    }


@_com
def list_tasks():
    """列出 WorkBuddy 命名空间下的计划任务。"""
    root = _folder()
    out = []
    # 标志 1 = TASK_ENUM_HIDDEN：我们创建的任务是 Hidden=True，必须带上才列得出来
    for t in root.GetTasks(1):
        try:
            if not t.Name.lower().startswith(NAMESPACE_PREFIX):
                continue
            out.append(_task_info(t))
        except Exception:
            continue
    out.sort(key=lambda x: x["name"].lower())
    return out


@_com
def _create_or_update(name, times, mode, description, runner="signin"):
    name = _safe_name(name)
    mode = mode if mode in ALLOWED_MODES else "silent"
    if not times:
        raise ValueError("至少需要一个运行时间点")
    times = ["%02d:%02d" % _parse_hhmm(t) for t in times]

    svc = _service()
    root = svc.GetFolder("\\")
    td = svc.NewTask(0)

    td.RegistrationInfo.Description = description or "WorkBuddy 自动签到（由面板创建）"

    # 当前用户、交互令牌、普通权限（与官方 install-windows.ps1 一致）
    try:
        td.Principal.UserId = os.environ.get("USERNAME") or str(td.Principal.UserId)
    except Exception:
        pass
    td.Principal.LogonType = _TASK_LOGON_INTERACTIVE_TOKEN
    td.Principal.RunLevel = _TASK_RUNLEVEL_LUA

    s = td.Settings
    s.Enabled = True
    s.StartWhenAvailable = True
    s.Hidden = True
    s.DisallowStartIfOnBatteries = False
    s.StopIfGoingOnBatteries = False
    s.ExecutionTimeLimit = "PT10M"

    act = td.Actions.Create(_TASK_ACTION_EXEC)
    act.Path = PYTHONW
    if runner == "notify":
        act.Arguments = '"%s" %s' % (RUNNER, mode)
    else:
        act.Arguments = '"%s" %s' % (SIGNIN, mode)
    act.WorkingDirectory = HERE

    today = datetime.date.today()
    for hm in times:
        hh, mm = _parse_hhmm(hm)
        tr = td.Triggers.Create(_TASK_TRIGGER_DAILY)
        tr.StartBoundary = "%sT%02d:%02d:00" % (today.isoformat(), hh, mm)
        tr.DaysInterval = 1

    root.RegisterTaskDefinition(
        name, td, _TASK_CREATE_OR_UPDATE, None, None,
        _TASK_LOGON_INTERACTIVE_TOKEN,
    )
    t = root.GetTask(name)
    return _task_info(t)


@_com
def create_or_update(name, times, mode="silent", description="", runner="signin"):
    return _create_or_update(name, times, mode, description, runner)


@_com
def delete_task(name):
    name = _guard(name)
    _folder().DeleteTask(name, 0)
    return {"deleted": name}


@_com
def run_task(name):
    name = _guard(name)
    t = _folder().GetTask(name)
    t.Run(None)
    return {"started": name}


@_com
def set_enabled(name, enabled):
    name = _guard(name)
    t = _folder().GetTask(name)
    t.Enabled = bool(enabled)
    return {"name": name, "enabled": bool(enabled)}


# ---- 预设（对应官方安装脚本的两个任务）----
PRESETS = {
    "daily": {
        "name": "WorkBuddyAutoSignin",
        "times": ["00:05"],
        "mode": "silent",
        "runner": "notify",
        "description": "WorkBuddy 每日自动签到（静默，跑完推送）",
    },
    "poll": {
        "name": "WorkBuddyGrowthPoll",
        "times": ["01:00", "05:00", "09:00", "13:00", "17:00", "21:00"],
        "mode": "silent-poll",
        "runner": "notify",
        "description": "WorkBuddy 轮询补签 + 成长中心（跑完推送，错过自动补跑）",
    },
}


if __name__ == "__main__":
    # 简单自检：打印当前 WorkBuddy 任务
    for item in list_tasks():
        print(item["name"], "|", item["state"], "| next:", item["next_run"], "| times:", item["times"])
