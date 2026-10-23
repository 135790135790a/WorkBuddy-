#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""browser_open.py —— 统一打开链接。

默认优先用 Microsoft Edge 打开（避免 .html 关联到别的浏览器导致"跳浏览器"）；
找不到 Edge 时退回系统默认浏览器。
可用环境变量 WB_BROWSER=default 强制走默认浏览器。
"""
import os
import subprocess
import webbrowser

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 r"Microsoft\Edge\Application\msedge.exe"),
]


def find_edge():
    for p in EDGE_PATHS:
        if p and os.path.exists(p):
            return p
    return None


def open_url(url, prefer="edge"):
    """打开 URL，返回实际使用的浏览器：'edge' 或 'default'。"""
    want = (os.environ.get("WB_BROWSER") or prefer or "edge").lower()
    if want != "default":
        edge = find_edge()
        if edge:
            try:
                subprocess.Popen([edge, url], close_fds=True)
                return "edge"
            except Exception:
                pass
    try:
        webbrowser.open(url)
    except Exception:
        pass
    return "default"


if __name__ == "__main__":
    import sys
    print("edge:", find_edge())
    if len(sys.argv) > 1:
        print("opened with:", open_url(sys.argv[1]))
