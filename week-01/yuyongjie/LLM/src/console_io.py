# -*- coding: utf-8 -*-
"""console_io.py — 统一处理 Python 的输出编码。

为什么需要它：同一种「强制 UTF-8」在不同环境里结果是相反的。

* 管道/重定向（VS Code Code Runner、CI、`| Out-File`）：下游按 UTF-8 解码，
  所以这里必须输出 UTF-8，否则中文变乱码；
* 真实控制台（cmd.exe 默认 GBK）：如果强制 UTF-8，控制台会按 GBK 解码，反而乱码。
  这时应该保持系统编码不动。

所以按 `isatty()` 分流，而不是一刀切。
"""
import sys


def setup():
    for stream in (sys.stdout, sys.stderr):
        if not hasattr(stream, "reconfigure"):
            continue
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")               # 控制台：沿用系统编码
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")  # 管道：固定 UTF-8
        except (ValueError, OSError):
            pass                                                   # 极端环境下不应阻断主流程
