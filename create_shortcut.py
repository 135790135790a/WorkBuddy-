#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 pywin32 的 IShellLink 创建合法 .lnk 快捷方式。

目标改为 pythonw.exe（无控制台窗口）：双击后静默生成报告，再自动弹出浏览器。
"""
import os

import pythoncom
from win32com.shell import shell

PROJECT = r"C:\Users\86139\WorkBuddy\2026-10-17-21-14-30\workbuddy-auto-signin"
PYTHONW = r"C:\Users\86139\AppData\Local\Programs\Python\Python311\pythonw.exe"
SCRIPT = os.path.join(PROJECT, "make-report.py")
LNK = r"C:\Users\86139\Desktop\workbuudy\WorkBuddy自动签到测试.lnk"
ICON = r"C:\Users\86139\Desktop\workbuudy\WorkBuddy自动签到测试.ico"

if not os.path.exists(ICON):
    ICON = r"C:\Users\86139\AppData\Local\Programs\WorkBuddy\WorkBuddy.exe"

sl = pythoncom.CoCreateInstance(
    shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
    shell.IID_IShellLink,
)
sl.SetPath(PYTHONW)
sl.SetArguments('"%s" auto --open' % SCRIPT)
sl.SetWorkingDirectory(PROJECT)
sl.SetDescription("静默运行 WorkBuddy 自动签到测试并打开可视化报告")
sl.SetIconLocation(ICON, 0)
pf = sl.QueryInterface(pythoncom.IID_IPersistFile)
# 先存到项目目录（可写），再复制到桌面，避免 COM 直接写桌面被拒绝
TMP = os.path.join(PROJECT, "_shortcut_tmp.lnk")
pf.Save(TMP, 0)
import shutil
shutil.copyfile(TMP, LNK)
os.remove(TMP)

print("shortcut created:", LNK)
print("target :", PYTHONW)
print("args   :", '"%s" auto --open' % SCRIPT)
print("icon   :", ICON)
