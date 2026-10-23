#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tray.py —— WorkBuddy 签到系统托盘（Windows，pywin32，无第三方 GUI 依赖）。

- 右键菜单：打开面板 / 立即签到 / 打开报告 / 签到总览 / 生成分享卡片 / 退出
- 双击图标：打开面板
- 悬停提示：显示「距下次签到 X小时Y分」，每 20 秒刷新
- 每小时检查一次运行心跳：任务被禁用或长时间没跑时气泡+推送告警
- 打开面板时若服务未启动，会自动拉起 test-dashboard.py

用 pythonw 启动（无控制台）：  pythonw tray.py
"""
import ctypes
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import win32api
import win32con
import win32gui

from browser_open import open_url

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHONW = r"C:\Users\86139\AppData\Local\Programs\Python\Python311\pythonw.exe"
PANEL_URL = "http://127.0.0.1:8765/"
LOG = os.path.join(HERE, "tray.log")
ICONS = [
    r"C:\Users\86139\Desktop\workbuudy\WorkBuddy自动签到测试.ico",
    os.path.join(HERE, "icon.ico"),
]

WM_TRAY = win32con.WM_USER + 20
MENU_OPEN, MENU_RUN, MENU_REPORT, MENU_CARD, MENU_BOARD, MENU_QUIT = 1, 2, 3, 4, 5, 9

_HWND = None
_HICON = None
_TASKBAR_CREATED = 0


def _log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _next_run_text():
    try:
        import scheduler
        tasks = [t for t in scheduler.list_tasks() if t.get("enabled")]
        stamps = [t["next_run_ts"] for t in tasks if t.get("next_run_ts")]
        if not stamps:
            return "下次签到 —"
        ms = min(stamps) - int(time.time() * 1000)
        if ms <= 0:
            return "即将运行"
        s = ms // 1000
        h, m = s // 3600, (s % 3600) // 60
        if h > 0:
            return "距下次签到 %d小时%d分" % (h, m)
        if m > 0:
            return "距下次签到 %d分%d秒" % (m, s % 60)
        return "距下次签到 %d秒" % (s % 60)
    except Exception as e:
        _log("next_run_text error: %r" % (e,))
        return "WorkBuddy 签到"


def _tip_text():
    return ("WorkBuddy 自动签到 · " + _next_run_text())[:127]


def _shell_modify(flags, tip):
    if not _HWND:
        return
    try:
        win32gui.Shell_NotifyIcon(
            win32gui.NIM_MODIFY,
            (_HWND, 0, flags, WM_TRAY, _HICON, tip),
        )
    except Exception as e:
        _log("NIM_MODIFY error: %r" % (e,))


def _set_tip(tip):
    _shell_modify(win32gui.NIF_TIP, tip[:127])


def _update_tip():
    _set_tip(_tip_text())


def _tip_loop():
    while True:
        time.sleep(20)
        _update_tip()


def _heartbeat_loop():
    """每小时静默检查一次运行心跳。

    这是唯一能发现「电脑开着、但计划任务被禁用 / 解释器路径失效」的地方：
    那种状态下不会有任何签到进程运行，也就不会有推送、日志、报告。
    """
    time.sleep(60)          # 启动后先等一分钟，避开开机高峰
    while True:
        try:
            import heartbeat
            st = heartbeat.status()
            ok, why = heartbeat.should_alert(st)
            if ok:
                title, body = heartbeat.alert_text(st)
                _balloon(title, body)
                try:
                    import notify
                    notify.push_custom(title, body)
                except Exception as e:
                    _log("heartbeat push error: %r" % (e,))
                heartbeat.mark_alerted()
                _log("heartbeat alert: %s" % why)
            elif st.get("stale"):
                _log("heartbeat stale, alert skipped: %s" % why)
        except Exception as e:
            _log("heartbeat loop error: %r" % (e,))
        time.sleep(3600)


def _balloon(title, text, timeout=6000):
    """气泡提示（尽力而为；系统关闭通知时可能不显示）。"""
    if not _HWND:
        return
    try:
        # PyNOTIFYICONDATA 顺序：tip, info, timeout, infoTitle, infoFlags
        win32gui.Shell_NotifyIcon(
            win32gui.NIM_MODIFY,
            (_HWND, 0, win32gui.NIF_INFO, WM_TRAY, _HICON,
             _tip_text(), text[:255], timeout, title[:63], win32gui.NIIF_INFO),
        )
    except Exception as e:
        _log("balloon error: %r" % (e,))


def _ensure_server():
    try:
        urllib.request.urlopen(PANEL_URL, timeout=1.5)
        return True
    except Exception:
        pass
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([PYTHONW, os.path.join(HERE, "test-dashboard.py")],
                         cwd=HERE, creationflags=flags, close_fds=True)
        time.sleep(1.2)
        _log("server started")
        return True
    except Exception as e:
        _log("ensure_server error: %r" % (e,))
        return False


def _open_panel():
    _ensure_server()
    open_url(PANEL_URL)


def _open_report():
    p = os.path.join(HERE, "report.html")
    if os.path.exists(p):
        open_url("file:///" + p.replace("\\", "/"))
    else:
        _open_panel()


def _run_now():
    def work():
        try:
            from signin_runner import run_with_retry
            import history
            import notify
            payload = run_with_retry("auto")
            history.record(payload)
            try:
                import archive
                archive.save_evidence(payload, "tray")
            except Exception:
                pass
            notify.push(payload, "auto")
            state = "成功" if payload.get("ok") else "失败"
            data = payload.get("data") or {}
            detail = (data.get("report") or "")[:40]
            _set_tip(("WorkBuddy 签到%s：%s" % (state, detail))[:127])
            _log("run_now: ok=%s result=%s" % (payload.get("ok"), data.get("result")))
            time.sleep(10)
            _update_tip()
        except Exception as e:
            _log("run_now error: %r" % (e,))
            _update_tip()
    threading.Thread(target=work, daemon=True).start()


def _last_run_text():
    """菜单里的「上次定时运行」一行。没有定时心跳时退回最近一次手动运行。"""
    try:
        import heartbeat
        st = heartbeat.status()
        if not st.get("exists"):
            return None
        sch = st.get("scheduled") or {}
        target = sch if sch.get("exists") else st
        who = "定时" if sch.get("exists") else "手动"
        line = "上次%s运行 %s · %s" % (who, (target.get("at") or "")[5:16],
                                      target.get("result") or "—")
        if sch.get("exists") and sch.get("stale"):
            line += "（已停跑 %.0f 小时）" % (sch.get("age_hours") or 0)
        return line[:120]
    except Exception:
        return None


def _make_card():
    """生成连签卡片并打开图片（无头浏览器截图，约 2-5 秒）。"""
    def work():
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run([PYTHONW, os.path.join(HERE, "streak-card.py"), "--open"],
                                  cwd=HERE, creationflags=flags, timeout=120,
                                  capture_output=True)
            if proc.returncode == 0:
                _balloon("连签卡片已生成", "正在用浏览器打开 streak-card.png，可另存或直接发出去。")
                _log("card ok")
            else:
                _balloon("生成卡片失败", "详情见 tray.log / 直接运行 streak-card.py 看输出。")
                _log("card failed rc=%s" % proc.returncode)
        except Exception as e:
            _log("card error: %r" % (e,))
            _balloon("生成卡片失败", str(e)[:120])
    threading.Thread(target=work, daemon=True).start()


def _open_board():
    """生成并打开「签到总览」静态页（不需要服务在跑）。"""
    def work():
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run([PYTHONW, os.path.join(HERE, "history-page.py"), "--open"],
                           cwd=HERE, creationflags=flags, timeout=60, capture_output=True)
            _log("board ok")
        except Exception as e:
            _log("board error: %r" % (e,))
            _balloon("打开总览失败", str(e)[:120])
    threading.Thread(target=work, daemon=True).start()


def _show_menu():
    menu = win32gui.CreatePopupMenu()
    try:
        win32gui.AppendMenu(menu, win32con.MF_STRING | win32con.MF_GRAYED, 0, _next_run_text())
        last = _last_run_text()
        if last:
            win32gui.AppendMenu(menu, win32con.MF_STRING | win32con.MF_GRAYED, 0, last)
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_OPEN, "打开面板")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_RUN, "立即签到")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_REPORT, "打开报告")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_BOARD, "签到总览（图表）")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_CARD, "生成分享卡片")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_QUIT, "退出")
        pos = win32gui.GetCursorPos()
        try:
            win32gui.SetForegroundWindow(_HWND)
        except Exception:
            pass
        cmd = win32gui.TrackPopupMenu(
            menu,
            win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON | win32con.TPM_RETURNCMD,
            pos[0], pos[1], 0, _HWND, None,
        )
        win32gui.PostMessage(_HWND, win32con.WM_NULL, 0, 0)
        if isinstance(cmd, tuple):
            cmd = cmd[0]
        if cmd == MENU_OPEN:
            _open_panel()
        elif cmd == MENU_RUN:
            _run_now()
        elif cmd == MENU_REPORT:
            _open_report()
        elif cmd == MENU_CARD:
            _make_card()
        elif cmd == MENU_BOARD:
            _open_board()
        elif cmd == MENU_QUIT:
            win32gui.DestroyWindow(_HWND)
    finally:
        try:
            win32gui.DestroyMenu(menu)
        except Exception:
            pass


def _add_icon():
    if not _HWND:
        return
    try:
        nid = (_HWND, 0, win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
               WM_TRAY, _HICON, _tip_text())
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
    except Exception as e:
        _log("add_icon error: %r" % (e,))


def _wndproc(hwnd, msg, wparam, lparam):
    global _HWND
    _HWND = hwnd
    if _TASKBAR_CREATED and msg == _TASKBAR_CREATED:
        _add_icon()
        return 0
    if msg == WM_TRAY:
        code = (lparam & 0xFFFF) if isinstance(lparam, int) else lparam
        if code in (win32con.WM_RBUTTONUP, win32con.WM_CONTEXTMENU):
            _show_menu()
        elif code == win32con.WM_LBUTTONDBLCLK:
            _open_panel()
        return 0
    if msg == win32con.WM_TIMER:
        _update_tip()
        return 0
    if msg == win32con.WM_DESTROY:
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 0))
        except Exception:
            pass
        win32gui.PostQuitMessage(0)
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def _single_instance():
    try:
        k32 = ctypes.windll.kernel32
        h = k32.CreateMutexW(None, False, "Local\\WorkBuddyTray")
        if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return None
        return h
    except Exception:
        return True


def _load_icon():
    for p in ICONS:
        if p and os.path.exists(p):
            try:
                return win32gui.LoadImage(
                    0, p, win32con.IMAGE_ICON, 0, 0,
                    win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
                )
            except Exception:
                continue
    return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)


def main():
    global _HICON, _HWND, _TASKBAR_CREATED
    guard = _single_instance()
    if guard is None:
        # 已经有托盘在跑：直接打开面板，让"双击"有可见反馈
        _log("already running -> open panel")
        _open_panel()
        return
    hinst = win32api.GetModuleHandle(None)
    _HICON = _load_icon()
    wc = win32gui.WNDCLASS()
    wc.hInstance = hinst
    wc.lpszClassName = "WorkBuddyTrayWnd"
    wc.lpfnWndProc = _wndproc
    wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    wc.hbrBackground = win32con.COLOR_WINDOW
    atom = win32gui.RegisterClass(wc)
    hwnd = win32gui.CreateWindow(atom, "WorkBuddyTray", 0, 0, 0, 0, 0, 0, 0, hinst, None)
    _HWND = hwnd
    _TASKBAR_CREATED = win32gui.RegisterWindowMessage("TaskbarCreated")
    _add_icon()
    threading.Thread(target=_tip_loop, daemon=True).start()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    _log("tray started")
    _balloon("WorkBuddy 托盘已启动",
             "图标在右下角通知区域；没看到就点时钟旁的 ^ 展开。左键双击图标可打开面板。")
    win32gui.PumpMessages()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _log("fatal: %r" % (e,))
