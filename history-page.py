#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""history-page.py —— 生成并打开「签到总览」（history.html）。

生成逻辑在 board.py 里（定时任务也要调它，做成模块才好复用）；
这个文件只负责给人用：打印结果、可选地打开浏览器。

用法：
    python history-page.py           # 生成 history.html
    python history-page.py --open    # 生成并打开
"""
import os
import sys

import board
from browser_open import open_url


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ok, msg = board.write()
    print(("已生成 " + msg) if ok else msg)
    if ok and "--open" in argv:
        try:
            open_url("file:///" + board.OUT.replace("\\", "/"))
        except Exception:
            pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
