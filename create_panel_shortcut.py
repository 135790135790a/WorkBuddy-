#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创建桌面「WorkBuddy 面板」快捷方式（指向 pythonw open-panel.py，无控制台）。

双击行为：服务没跑就自动拉起，然后用 Edge 打开 http://127.0.0.1:8765/
"""
import os
import shutil

import pythoncom
from win32com.shell import shell

PROJECT = r"C:\Users\86139\WorkBuddy\2026-10-17-21-14-30\workbuddy-auto-signin"
PYTHONW = r"C:\Users\86139\AppData\Local\Programs\Python\Python311\pythonw.exe"
SCRIPT = os.path.join(PROJECT, "open-panel.py")
LNK = r"C:\Users\86139\Desktop\workbuudy\WorkBuddy面板.lnk"
ICON = r"C:\Users\86139\Desktop\workbuudy\WorkBuddy自动签到测试.ico"

sl = pythoncom.CoCreateInstance(
    shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
    shell.IID_IShellLink,
)
sl.SetPath(PYTHONW)
sl.SetArguments('"%s"' % SCRIPT)
sl.SetWorkingDirectory(PROJECT)
sl.SetDescription("打开 WorkBuddy 签到面板（会自动启动本地服务）")
if os.path.exists(ICON):
    sl.SetIconLocation(ICON, 0)

pf = sl.QueryInterface(pythoncom.IID_IPersistFile)
TMP = os.path.join(PROJECT, "_panel_tmp.lnk")
pf.Save(TMP, 0)
os.makedirs(os.path.dirname(LNK), exist_ok=True)
shutil.copyfile(TMP, LNK)
os.remove(TMP)

print("shortcut created:", LNK)
print("target :", PYTHONW)
print("args   :", '"%s"' % SCRIPT)
