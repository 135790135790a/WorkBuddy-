#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signin_runner.py —— 外层"整轮运行级"重试与智能判定（供 make-report.py 与 test-dashboard.py 共用）。

signin.py 内部已按失败类型做退避重试（网络/超时），但仍有它自身没扛住、
或机器刚唤醒后网络需更久就绪的情况。本模块在"跑一次 signin.py 拿到结果"之上
再包一层：

  - 对【可重试】结果（NETWORK / TIMEOUT / UNKNOWN / ERROR）做指数退避重试；
  - 对【成功 / 今日已签 / 业务不可重试】立即结束，不再耗时间；
  - 对【凭据过期】先尝试一次自愈（拉起桌面端续期 → 再跑一次），失败才告警；
  - 每次结束都写一次运行心跳（heartbeat.py），让"到底有没有跑"可被外部察觉；
  - 最终只在"重试耗尽仍硬失败"时标记 needs_alert=True。

智能告警的核心就在这里：瞬时抖动自愈后不会报警，避免误报轰炸。

所有入口（定时任务 notify-run.py、托盘、面板、快捷方式）都经过 run_with_retry，
所以心跳写在这一层就不会漏。
"""
import ctypes
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\86139\AppData\Local\Programs\Python\Python311\python.exe"
SIGNIN = os.path.join(HERE, "signin.py")
# 经验证的成功 / 已知终态（不重试）
SUCCESS_RESULTS = {"ALREADY", "GROWTH", "CLAIMED", "INACTIVE", "AUTH_READY"}
# 认证 / 权限类：重试无意义，直接判定需关注
NO_RETRY_RESULTS = {"AUTH_REJECTED", "FORBIDDEN", "NO_AUTH", "AUTH_ERROR"}
# 可重试的瞬时 / 硬失败
# AUTH_BUSY = 凭据文件被客户端占用，等一会儿自己会好，值得外层退避重试
RETRYABLE_RESULTS = {"NETWORK", "TIMEOUT", "UNKNOWN", "ERROR", "AUTH_BUSY"}

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF = [10, 30]  # 第1次失败后等10s，第2次失败后等30s


def log_path():
    """signin.py 的日志路径（与它的 WORKBUDDY_SIGNIN_LOG 保持一致）。"""
    return os.environ.get("WORKBUDDY_SIGNIN_LOG") or os.path.join(HERE, "signin.log")


def _log_offset():
    """运行前的日志字节偏移，用来只认本次新增的行。"""
    try:
        return os.path.getsize(log_path())
    except OSError:
        return 0


def _parse_log_since(offset):
    """从日志里取本次运行写入的最后一条 JSON。

    `silent` 模式不打印 stdout（只有日志），所以跑定时任务时本模块拿不到结果：
    推送只能说"签到完成"、心跳只能记 OK、历史也存不到积分。这里从本次新增的
    日志行里把 JSON 捞回来。空跑（未写入任何行）时返回 None，不会误读上一轮的记录。
    """
    try:
        with open(log_path(), "rb") as f:
            if offset:
                f.seek(offset)
            text = decode(f.read())
    except OSError:
        return None
    data = None
    for line in text.splitlines():
        line = line.strip()
        brace = line.find("{")
        if not line.startswith("[") or brace < 0:
            continue
        try:
            data = json.loads(line[brace:])
        except json.JSONDecodeError:
            continue
    return data


def decode(b):
    """稳健解码子进程输出：优先 UTF-8，失败退回 GBK（Windows 中文环境）。"""
    if not b:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def parse_result(raw):
    """从 signin.py 输出里取最后一行 JSON，返回 dict 或 None。"""
    data = None
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return data


def classify(data, exit_code):
    """返回 ('success' | 'retry' | 'fail', result_str)。

    关键修正：旧逻辑只用"有没有 JSON"判 ok，导致 ERROR/TIMEOUT 被误判为成功。
    这里严格按 signin.py 的 result 字段分类。
    """
    if data is None:
        # 没有任何结构化输出：退出码 0 才算成功，否则判失败（无输出）
        if exit_code == 0:
            return "success", None
        return "fail", "NO_OUTPUT"
    result = data.get("result")
    if result in SUCCESS_RESULTS:
        return "success", result
    if result in NO_RETRY_RESULTS:
        # 认证 / 权限失败：需关注，但重试无意义，直接定终态
        return "fail", result
    if result in RETRYABLE_RESULTS:
        return "retry", result
    # 未知 result：以 needs_attention 兜底
    if data.get("needs_attention"):
        return "fail", (result or "NEEDS_ATTENTION")
    return "success", result


def run_once(mode):
    """跑一次 signin.py，返回结构化字典。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONLEGACYWINDOWSSTDIO="")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    offset = _log_offset()
    proc = subprocess.run(
        [PY, SIGNIN, mode], cwd=HERE,
        capture_output=True, timeout=180, env=env, creationflags=flags,
    )
    raw = decode(proc.stdout).strip()
    data = parse_result(raw)
    if data is None:
        # silent 系列只写日志不打印，从本次新增的日志行里兜底
        data = _parse_log_since(offset)
    return {
        "exit_code": proc.returncode,
        "data": data,
        "raw": raw,
        "stderr": decode(proc.stderr).strip(),
        "mode": mode,
    }


_MUTEX_NAME = "Local\\WorkBuddyAutoSigninRun"


def _acquire_lock():
    """命名互斥体：保证同一时刻只有一个签到进程。返回 (handle, acquired)。

    进程退出（含崩溃）时内核自动释放，不会留下死锁文件。
    """
    if os.name != "nt":
        return None, True
    try:
        k32 = ctypes.windll.kernel32
        handle = k32.CreateMutexW(None, False, _MUTEX_NAME)
        already = (k32.GetLastError() == 183)  # ERROR_ALREADY_EXISTS
        return handle, (not already)
    except Exception:
        return None, True


def _release_lock(handle):
    if handle:
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass


def _heal_auth(result):
    """AUTH 类失败时尝试一次自动续期。任何异常都只降级为"没做成"。"""
    try:
        import authheal
    except Exception:
        return {"attempted": False, "reason": "module-missing"}
    if not authheal.should_heal(result):
        return {"attempted": False, "reason": "not-healable"}
    try:
        return authheal.heal()
    except Exception as e:
        return {"attempted": False, "reason": type(e).__name__}


def _done(payload, mode):
    """收尾：写心跳并把断档信息带回给调用方。心跳失败不影响签到结果。

    `status` 是只读探针，不代表"今天真的跑过签到"，所以不写心跳——
    否则手动看一眼状态就能把断档告警洗掉，信号就假了。
    """
    if mode == "status":
        return payload
    try:
        import heartbeat
        info = heartbeat.beat(payload, mode)
        payload = dict(payload)
        payload["heartbeat"] = info
    except Exception:
        pass
    return payload


def run_with_retry(mode="auto", attempts=DEFAULT_ATTEMPTS, backoff=DEFAULT_BACKOFF):
    """带外层重试的运行。返回字典含：ok / needs_alert / history /
    attempts_used / retried / heartbeat，以及单次运行的 exit_code / data / raw / stderr / mode。

    单实例：若已有签到在跑，本次直接返回 skipped=locked（ok=True，不告警）。
    """
    if mode not in ("auto", "status", "silent", "silent-poll", "growth"):
        mode = "auto"
    handle, acquired = _acquire_lock()
    if not acquired:
        _release_lock(handle)
        return {
            "ok": True, "skipped": "locked", "mode": mode,
            "data": {"result": "SKIPPED", "report": "另一个签到正在进行，本次跳过"},
            "raw": "", "stderr": "", "exit_code": 0,
            "history": [], "attempts_used": 0, "retried": False,
            "needs_alert": False,
        }
    try:
        history = []
        last = None
        healed = False
        allowed = attempts
        i = 0
        while i < allowed:
            i += 1
            try:
                res = run_once(mode)
            except subprocess.TimeoutExpired:
                res = {"exit_code": -1, "data": None, "raw": "",
                       "stderr": "运行超时（>180s）", "mode": mode}
            last = res
            kind, result = classify(res["data"], res["exit_code"])
            entry = {"attempt": i, "result": result, "kind": kind,
                     "exit_code": res["exit_code"]}
            history.append(entry)
            # 凭据过期：先试一次自愈（只试一次，且不占用退避预算）
            if kind == "fail" and not healed:
                heal = _heal_auth(result)
                if heal.get("attempted"):
                    entry["auth_heal"] = heal
                if heal.get("refreshed"):
                    healed = True
                    allowed = max(allowed, i + 1)  # 续期成功后允许再跑一次
                    time.sleep(2)
                    continue
            if kind in ("success", "fail"):
                # 成功或不可重试的硬失败：立即结束，不再耗时间
                return _done(_final(res, history, i, i > 1), mode)
            # 可重试：等待退避后进入下一轮
            if i < allowed:
                wait = backoff[i - 1] if (i - 1) < len(backoff) else backoff[-1]
                time.sleep(wait)
        # 走到这里说明所有 attempts 都是 retry 类（重试耗尽）
        if last is None:
            last = {"exit_code": -1, "data": None, "raw": "",
                    "stderr": "没有执行任何尝试", "mode": mode}
        return _done(_final(last, history, i, i > 1), mode)
    finally:
        _release_lock(handle)


def _final(res, history, attempts_used, retried):
    out = dict(res)
    out["history"] = history
    out["attempts_used"] = attempts_used
    out["retried"] = retried
    # 最终判定的 ok 以 classify 为准（与循环内一致）
    out["ok"] = classify(res["data"], res["exit_code"])[0] == "success"
    # 智能告警：仅当最终判定失败才需要人关注（瞬时抖动自愈后不会报警）
    out["needs_alert"] = not out["ok"]
    return out
