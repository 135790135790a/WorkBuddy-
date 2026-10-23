#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""open-panel.py —— 一键打开面板（服务没在跑就自己拉起来）。

为什么需要它：
    面板是本地 http 服务（127.0.0.1:8765），不是双击就能开的静态文件。
    之前要么靠命令行 `python test-dashboard.py`，要么靠我临时起的进程（会话一结束
    就没了），所以很容易出现"打开是空白 / 打不开"。

它做的事：
    1. 探测 8765 上是不是我们的面板（能拿到带 wb-token 的页面才算）
    2. 不是 → 用 pythonw 无窗口启动 test-dashboard.py，最多等 12 秒
    3. 用 Edge 打开（找不到 Edge 退回默认浏览器）
    4. 失败 → 弹消息框说明原因（pythonw 没有控制台，否则用户完全没反馈）

用法：
    pythonw open-panel.py          # 正常使用（双击快捷方式）
    python open-panel.py --check   # 只探测并打印状态，不开浏览器
"""
import ctypes
import http.client
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
URL = "http://127.0.0.1:%d/" % PORT
PYTHONW = r"C:\Users\86139\AppData\Local\Programs\Python\Python311\pythonw.exe"
PYTHON = r"C:\Users\86139\AppData\Local\Programs\Python\Python311\python.exe"
SERVER = os.path.join(HERE, "test-dashboard.py")

_MB_ICONERROR = 0x10
_MB_ICONINFO = 0x40


def _msgbox(text, title="WorkBuddy 面板", flags=_MB_ICONINFO):
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:
        pass


def probe(port=PORT, timeout=1.5):
    """端口上是否是我们的面板（页面里带 wb-token 这个注入标记）。"""
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", "/")
        r = c.getresponse()
        body = r.read(8192)
        c.close()
        return r.status == 200 and b"wb-token" in body
    except Exception:
        return False


def _port_in_use(port=PORT):
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=0.8)
        c.request("HEAD", "/")
        c.getresponse().read()
        c.close()
        return True
    except Exception:
        return False


def start_server():
    """无窗口拉起面板服务，返回 (ok, 说明)。"""
    exe = PYTHONW if os.path.exists(PYTHONW) else PYTHON
    if not os.path.exists(SERVER):
        return False, "找不到 %s" % SERVER
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([exe, SERVER], cwd=HERE, creationflags=flags, close_fds=True)
    except Exception as e:
        return False, "启动失败：%s" % e
    for _ in range(24):  # 最多等 12 秒
        time.sleep(0.5)
        if probe():
            return True, "已启动"
    return False, "启动后仍无法访问（可能被安全软件拦截，或端口被其它程序占用）"


def main():
    check_only = "--check" in sys.argv

    if probe():
        state = "已在运行"
    elif check_only:
        # 只检查，不启动（便于排查）
        print("probe: False | port_in_use: %s | state: 未运行" % _port_in_use())
        return 1
    elif _port_in_use():
        # 端口被别的程序占着（不是我们的面板），启动新实例会因 SO_REUSEADDR 抢不到
        state = "port-conflict"
    else:
        ok, detail = start_server()
        state = "已启动" if ok else "failed"

    if check_only:
        print("probe:", probe(), "| port_in_use:", _port_in_use(), "| state:", state)
        return 0 if state in ("已在运行", "已启动") else 1

    if state == "port-conflict":
        _msgbox("端口 %d 被其它程序占用了，面板无法启动。\n\n"
                "可以用命令查看占用者：\n  netstat -ano | findstr :%d\n"
                "结束对应进程后再试。" % (PORT, PORT), flags=_MB_ICONERROR)
        return 1
    if state == "failed":
        _msgbox("面板服务启动失败。\n\n可以手动在下面目录运行：\n"
                "  python test-dashboard.py\n\n目录：%s" % HERE, flags=_MB_ICONERROR)
        return 1

    try:
        from browser_open import open_url
        open_url(URL)
    except Exception:
        try:
            import webbrowser
            webbrowser.open(URL)
        except Exception as e:
            _msgbox("面板已就绪，但打开浏览器失败：%s\n\n请手动访问：%s" % (e, URL),
                    flags=_MB_ICONERROR)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
