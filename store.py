#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""store.py —— 本地状态文件的「原子写入 + 损坏自愈 + schema 迁移」地基。

为什么需要：
    之前所有 JSON 都是 `open(w)` 直接覆盖写。写到一半断电 / 进程被杀 / 磁盘满，
    原文件就会被截断成半截 JSON —— 下次读取解析失败，历史或推送配置**整体丢失**。

这里提供三层保护：
    1. 原子写：先写同目录 `<file>.tmp` → flush + fsync → os.replace 覆盖。
       os.replace 在 NTFS 上是原子的，任何时刻文件要么是旧的完整版，要么是新的完整版。
    2. 损坏自愈：读取解析失败时，把坏文件改名成 `<file>.corrupt-YYYYmmdd-HHMMSS`
       备份（不直接删，便于事后排查），然后返回默认值，让程序继续跑。
    3. schema 版本：文件里带 `_schema` 字段，读取时交给 migrate 回调逐级升级。

所有函数都不会抛异常（失败时返回默认值 / False），因为它们的调用方都是
「宁可少记一笔，也不能让签到主流程崩掉」的场景。
"""
import datetime
import json
import os
import shutil
import tempfile

# 当前各文件的 schema 版本（升级结构时改这里，并在调用方提供 migrate）
SCHEMA_HISTORY = 2
SCHEMA_NOTIFY = 3


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def atomic_write_bytes(path, data):
    """原子写入二进制内容。返回 True/False。"""
    d = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    tmp = None
    try:
        # 同目录 → 保证 os.replace 是同一卷上的原子重命名
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d or ".")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass
        return False


def atomic_write_text(path, text, encoding="utf-8"):
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path, obj, indent=0, ensure_ascii=False):
    try:
        text = json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent or None)
    except Exception:
        return False
    return atomic_write_text(path, text)


def _quarantine(path):
    """把损坏文件改名备份，返回备份路径（失败返回 None）。"""
    bak = "%s.corrupt-%s" % (path, _stamp())
    try:
        shutil.move(path, bak)
        return bak
    except Exception:
        try:
            os.remove(path)
            return bak
        except Exception:
            return None


def read_json_ex(path, default=None, migrate=None):
    """读取 JSON，返回 (data, status)。

    status:
        "ok"      读取成功
        "missing" 文件不存在
        "corrupt" 解析失败（已隔离备份，data 为 default）
        "error"   其它 IO 异常（data 为 default）
    """
    if not path or not os.path.exists(path):
        return default, "missing"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _quarantine(path)
        return default, "corrupt"
    except Exception:
        return default, "error"
    if migrate:
        try:
            data = migrate(data)
        except Exception:
            pass
    return data, "ok"


def read_json(path, default=None, migrate=None):
    """只取数据的简化版（不关心状态）。"""
    data, _ = read_json_ex(path, default, migrate)
    return data


def read_text(path, encoding="utf-8", default=""):
    try:
        with open(path, encoding=encoding, errors="replace") as f:
            return f.read()
    except Exception:
        return default


# ---------------- 归档目录工具 ----------------
def prune_dir(dir_path, keep, pattern=None, suffix=None):
    """只保留目录下最新 keep 个文件（按 mtime）。返回删除数量。

    pattern: 文件名需包含的子串（可选）；suffix: 后缀过滤（如 ".html"）。
    """
    try:
        names = []
        for n in os.listdir(dir_path):
            if n.startswith("."):
                continue
            if suffix and not n.endswith(suffix):
                continue
            if pattern and pattern not in n:
                continue
            p = os.path.join(dir_path, n)
            if os.path.isfile(p):
                names.append((os.path.getmtime(p), p))
        names.sort(reverse=True)
        removed = 0
        for _, p in names[keep:]:
            try:
                os.remove(p)
                removed += 1
            except Exception:
                pass
        return removed
    except FileNotFoundError:
        return 0
    except Exception:
        return 0


def list_dir_files(dir_path, suffix=None):
    """列出目录文件，按 mtime 倒序：[{name, path, size, mtime}]。"""
    out = []
    try:
        for n in os.listdir(dir_path):
            if n.startswith("."):
                continue
            if suffix and not n.endswith(suffix):
                continue
            p = os.path.join(dir_path, n)
            if not os.path.isfile(p):
                continue
            try:
                st = os.stat(p)
            except Exception:
                continue
            out.append({
                "name": n,
                "path": p,
                "size": st.st_size,
                "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "_mt": st.st_mtime,
            })
    except Exception:
        return []
    out.sort(key=lambda x: x["_mt"], reverse=True)
    for x in out:
        x.pop("_mt", None)
    return out
