#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创建桌面「WorkBuddy 托盘」快捷方式（指向 pythonw tray.py，无控制台）。"""
import os
import shutil

import pythoncom
from win32com.shell import shell

PROJECT = r"C:\Users\86139\WorkBuddy\2026-10-17-21-14-30\workbuddy-auto-signin"
PYTHONW = r"C:\Users\86139\AppData\Local\Programs\Python\Python311\pythonw.exe"
SCRIPT = os.path.join(PROJECT, "tray.py")
LNK = r"C:\Users\86139\Desktop\workbuudy\WorkBuddy托盘.lnk"
ICON = r"C:\Users\86139\Desktop\workbuudy\WorkBuddy自动签到测试.ico"

sl = pythoncom.CoCreateInstance(
    shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
    shell.IID_IShellLink,
)
sl.SetPath(PYTHONW)
sl.SetArguments('"%s"' % SCRIPT)
sl.SetWorkingDirectory(PROJECT)
sl.SetDescription("WorkBuddy 签到系统托盘（倒计时 / 立即签到 / 打开面板）")
if os.path.exists(ICON):
    sl.SetIconLocation(ICON, 0)

pf = sl.QueryInterface(pythoncom.IID_IPersistFile)
TMP = os.path.join(PROJECT, "_tray_tmp.lnk")
pf.Save(TMP, 0)
shutil.copyfile(TMP, LNK)
os.remove(TMP)

print("shortcut created:", LNK)
print("target :", PYTHONW)
print("args   :", '"%s"' % SCRIPT)
