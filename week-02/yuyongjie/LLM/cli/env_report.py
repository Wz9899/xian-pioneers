# -*- coding: utf-8 -*-
"""env_report.py — 环境自检，用来出「开发环境搭建」那张截图。

不调模型，只打印：解释器 / 依赖 / Node / MCP server / 密钥配置状态 / 项目结构。
"""
import argparse
import os
import platform
import subprocess
import sys
import unicodedata

import _bootstrap          # noqa: F401 — 把 src/ 加入 sys.path
import console_io

ROOT = _bootstrap.PROJECT_ROOT

R = "\033[0m"
B = "\033[1m"
GRAY = "\033[90m"
GREEN = "\033[32m"
YELLOW = "\033[33m"

MCP_ENTRY = os.path.join(ROOT, ".mcp", "node_modules", "@modelcontextprotocol",
                         "server-filesystem", "dist", "index.js")


def _display_width(text):
    """中文算两个字符宽，否则终端里对不齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text, width):
    return text + " " * max(0, width - _display_width(text))


def row(label, value, color=""):
    print("  {0}{1}{2}{3}{4}".format(GRAY, _pad(label, 18), R, color, value))


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                              encoding="utf-8", errors="replace").stdout.strip()
    except Exception as exc:                     # 工具不存在也不该让报告崩掉
        return "<{0}>".format(exc.__class__.__name__)


def main():
    console_io.setup()

    parser = argparse.ArgumentParser(description="环境自检")
    parser.add_argument("--no-tree", action="store_true", help="只打印环境信息，不列项目结构")
    args = parser.parse_args()

    print()
    print("═" * 78)
    print(f"{B}  开发环境自检{R}")
    print("═" * 78)

    row("操作系统", "{0} {1}".format(platform.system(), platform.release()))
    row("Python", "{0}  (venv: {1})".format(platform.python_version(), sys.prefix))
    try:
        import httpx
        row("httpx", httpx.__version__, GREEN)
    except ImportError:
        row("httpx", "未安装", YELLOW)

    row("Node", run("node", "--version") or "<not found>")
    row("npm", run("cmd", "/c", "npm", "--version") or "<not found>")

    if os.path.isfile(MCP_ENTRY):
        row("MCP server", "filesystem 已安装（官方实现）", GREEN)
    else:
        row("MCP server", "未安装", YELLOW)

    # 密钥只显示掩码，避免截图泄漏
    import llm as llm_mod
    llm_mod.load_env(os.path.join(ROOT, ".env"))
    key = (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key and "在此填写" not in key:
        row("API Key", llm_mod.redact(key) + "  (已配置)", GREEN)
    else:
        row("API Key", "未配置（demo.py --mock 可离线验证）", YELLOW)
    row("BASE_URL", os.environ.get("LLM_BASE_URL") or os.environ.get("BASE_URL") or "-")
    row("MODEL", os.environ.get("LLM_MODEL") or os.environ.get("MODEL") or "-")

    if args.no_tree:
        print()
        return 0

    print()
    print("─" * 78)
    print(f"{B}  项目结构{R}")
    print("─" * 78)
    skip = {"__pycache__", ".venv", ".mcp", ".git"}
    for name in sorted(os.listdir(ROOT)):
        path = os.path.join(ROOT, name)
        if name in skip:
            print("  {0}{1}/{2}".format(GRAY, name, R))
            continue
        if os.path.isdir(path):
            print("  {0}/".format(name))
            for child in sorted(os.listdir(path)):
                if child in skip:
                    continue
                print("      {0}".format(child))
        else:
            print("  {0:<26}{1:>8,} B".format(name, os.path.getsize(path)))

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
