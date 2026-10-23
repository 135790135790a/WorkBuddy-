#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify-run.py —— 计划任务入口：跑一次签到，然后按配置推送结果。

由 Windows 计划任务以 `pythonw.exe notify-run.py <mode>` 调用（无窗口）。
mode 与 signin.py 一致：auto / silent / silent-poll / growth / status。

退出码：签到成功 0，失败 1（与 signin.py 语义一致，便于任务历史判断）。
"""
import sys


def _say(msg):
    """pythonw 下 sys.stdout 可能为 None，打印失败不能让流程崩。"""
    try:
        print(msg)
    except Exception:
        pass


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else "silent"
    try:
        from signin_runner import run_with_retry
        payload = run_with_retry(mode)
    except Exception as e:
        payload = {"ok": False, "data": None, "raw": "", "stderr": "%s: %s" % (type(e).__name__, e),
                   "exit_code": -1, "mode": mode}
    try:
        import history
        history.record(payload)
    except Exception:
        pass
    # 留证：定时任务静默运行，不生成 HTML 报告，但原始返回必须存下来。
    # status 是只读探针，不是一次签到，不占归档（否则总览里全是"—"的行）。
    if mode != "status":
        try:
            import archive
            archive.save_evidence(payload, mode)
        except Exception:
            pass
    # 总览页顺手刷新：静态文件，托盘/双击都能直接看，不需要服务在跑
    try:
        import board
        if mode != "status":
            board.write()
    except Exception:
        pass
    try:
        import notify
        result = notify.push(payload, mode)
        _say("notify: %r" % (result,))
    except Exception as e:
        _say("notify error: %r" % (e,))

    # 运行心跳：本次运行前发现有断档（关机多日 / 任务被停用）→ 补一条告警。
    # 走同一个静默期，不会和上面的失败推送重复轰炸。
    try:
        import heartbeat
        alert = heartbeat.gap_alert(payload.get("heartbeat") or {})
        if alert:
            import notify
            title, body = alert
            sent = notify.push_custom(title, body)
            heartbeat.mark_alerted()
            _say("gap alert: %r" % (sent,))
    except Exception as e:
        _say("heartbeat error: %r" % (e,))
    # 接口失效监测：命中"疑似改版"就额外推一条带修复建议的告警（6 小时内不重复）
    try:
        import monitor
        import notify
        s = monitor.scan()
        ok_alert, why = monitor.should_alert(s)
        if ok_alert:
            title, body = monitor.alert_message(s)
            sent = notify.push_custom(title, body)
            monitor.mark_alerted(s)
            _say("drift alert: %r (%s)" % (sent, why))
        else:
            _say("drift alert skipped: %s" % (why,))
    except Exception as e:
        _say("monitor error: %r" % (e,))
    sys.exit(0 if payload.get("ok") else 1)


if __name__ == "__main__":
    main()
