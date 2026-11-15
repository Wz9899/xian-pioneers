# -*- coding: utf-8 -*-
"""paths.py — 全项目唯一的路径定义。

重构前每个模块都自己算 `dirname(__file__)`，文件一挪位置就全错。
现在只在这里算一次，其余模块 `from paths import ...` 即可。
"""
import os

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SKILLS_DIR = os.path.join(PROJECT_ROOT, "skills")
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
MCP_DIR = os.path.join(PROJECT_ROOT, ".mcp")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots")
