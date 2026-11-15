# -*- coding: utf-8 -*-
"""_bootstrap.py — 把 src/ 加进 sys.path。

cli/ 下的脚本第一行 `import _bootstrap` 即可。这样不依赖 PYTHONPATH 环境变量，
命令行、VS Code Code Runner、双击运行都能找到库模块。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
