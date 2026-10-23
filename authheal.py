#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""authheal.py —— 登录态失效时自动续期：拉起桌面端 → 等凭据刷新 → 允许重试一次。

背景：
    signin.py 靠桌面端写出的凭据文件鉴权。客户端不常开就不续期，accessToken
    过期后签到会稳定返回 AUTH_ERROR / AUTH_REJECTED，而唯一的解法是"人去点一下
    桌面端"。定时任务跑在半夜，没人点 → 当天断签，第二天才发现。

策略（只做有把握的事）：
    1. 本机没有凭据文件 → 直接放弃。这种是没登录过，拉客户端也没用。
    2. 客户端已经在跑 → 不重复拉起。token 还是旧的，拉起不解决，交给人登录。
    3. 客户端没跑 → 启动它，轮询凭据文件里 accessToken 的指纹，最长等 75 秒。
       指纹变了 = 已换新 token，允许外层重跑一次；没变 = 如实放弃。

安全性：
    - 只在 AUTH 类失败时调用，不会对网络抖动乱开客户端。
    - 启动的是客户端本体，不加任何参数；失败只影响本次重试，不影响签到主流程。
    - 不读取、不打印 token 原文，只比较 sha256 指纹的前 12 位。

环境变量：
    WORKBUDDY_AUTH_HEAL=0   关闭自愈（默认开启）
    WORKBUDDY_HEAL_WAIT     等待秒数，默认 75，夹在 5..300
"""
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time

# 值得自愈的终态：凭据过期 / 被拒。NO_AUTH（没有凭据文件）不在内，
# 那种情况启动客户端不会有任何改变。
HEAL_RESULTS = {"AUTH_ERROR", "AUTH_REJECTED"}

DEFAULT_WAIT = 75
POLL_SECONDS = 3


def enabled():
    v = (os.environ.get("WORKBUDDY_AUTH_HEAL") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def should_heal(result):
    return enabled() and (result or "") in HEAL_RESULTS


def wait_seconds():
    try:
        v = int(float(os.environ.get("WORKBUDDY_HEAL_WAIT") or DEFAULT_WAIT))
    except Exception:
        v = DEFAULT_WAIT
    return max(5, min(v, 300))


def fingerprint(auth_file):
    """凭据里 accessToken 的指纹。用于判断"有没有换新 token"，不落盘不外传。"""
    try:
        with open(auth_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = (data.get("auth") or {}).get("accessToken")
        if token in (None, ""):
            return None
        if not isinstance(token, str):
            token = json.dumps(token, sort_keys=True)
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return None


def _decode(b):
    if not b:
        return ""
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("utf-8", errors="replace")


def is_running(name="WorkBuddy.exe"):
    """客户端是否已在运行。探测失败一律当作"没在跑"，交给后续流程判断。"""
    try:
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % name, "/NH"],
                               capture_output=True, timeout=10, creationflags=flags)
            return name.lower() in _decode(p.stdout).lower()
        p = subprocess.run(["pgrep", "-f", name], capture_output=True, timeout=10)
        return p.returncode == 0
    except Exception:
        return False


def _exe():
    """复用 signin.py 的运行时发现逻辑（环境变量 → 默认目录 → 注册表）。"""
    import signin
    return signin.find_workbuddy_runtime()


def _auth_file():
    import signin
    path, _ = signin.find_auth_file()
    return path


def _minimize_window(pid):
    """把刚启动的客户端窗口最小化，尽量不打断正在用电脑的人（尽力而为）。"""
    if sys.platform != "win32" or not pid:
        return False
    try:
        user32 = ctypes.windll.user32
        found = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _cb(hwnd, _lparam):
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), None)
        for hwnd in found:
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        return bool(found)
    except Exception:
        return False


def launch(exe):
    """启动客户端本体（不带参数）。"""
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([exe], close_fds=True, creationflags=flags)


def heal(auth_file=None, exe=None, wait=None, launcher=None, sleeper=None,
         poll=POLL_SECONDS):
    """尝试一次自愈。返回结构化结果，永不抛异常。

    launcher / sleeper / poll 供测试注入；正常调用只传 auth_file/exe。
    """
    out = {
        "attempted": True, "launched": False, "refreshed": False,
        "reason": "", "waited": 0.0, "exe": "", "auth_file": "",
    }
    sleeper = sleeper or time.sleep
    if not enabled():
        out.update(attempted=False, reason="disabled")
        return out
    limit = wait_seconds() if wait is None else max(0, int(wait))

    try:
        path = auth_file or _auth_file()
    except Exception:
        path = auth_file
    out["auth_file"] = path or ""
    if not path or not os.path.exists(path):
        out["reason"] = "no-auth-file"
        return out

    before = fingerprint(path)
    if before is None:
        out["reason"] = "unreadable-credential"
        return out

    try:
        exe = exe or _exe()
    except Exception:
        out["reason"] = "runtime-not-found"
        return out
    out["exe"] = exe
    name = os.path.basename(exe) or "WorkBuddy.exe"

    if is_running(name):
        # 客户端开着还失败，说明需要人重新登录，拉起不会有帮助
        out["reason"] = "client-already-running"
        return out

    try:
        if launcher is None:
            proc = launch(exe)
            sleeper(2)
            _minimize_window(getattr(proc, "pid", 0))
        else:
            launcher(exe)
    except Exception:
        out["reason"] = "launch-failed"
        return out
    out["launched"] = True

    waited = 0.0
    while waited < limit:
        sleeper(poll)
        waited += poll
        if fingerprint(path) != before:
            out.update(refreshed=True, reason="credential-refreshed",
                       waited=round(waited, 1))
            return out
    out.update(reason="no-refresh", waited=round(waited, 1))
    return out
