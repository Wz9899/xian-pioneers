# -*- coding: utf-8 -*-
"""tools.py — 本地工具注册表 + Function Calling schema 自动生成。

一个工具 = 名字 + 给模型看的描述 + JSON Schema 参数 + 可调用实现。
用 @tool 装饰器注册，schemas() 自动产出 OpenAI 格式的 tools 数组。

已内置：
  calculator        四则运算（AST 白名单求值，不用 eval）
  get_current_time  指定时区的当前时间
  list_files        列工作区文件
  read_file         读工作区文本文件（带路径穿越防护）
"""
import ast
import json
import operator
import os
import sys
from datetime import datetime, timedelta, timezone

from paths import PROJECT_ROOT

WORKSPACE = PROJECT_ROOT

_REGISTRY = {}


def tool(name, description, parameters):
    """注册一个工具。parameters 是 JSON Schema 的 object 定义。"""
    def decorator(fn):
        _REGISTRY[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "func": fn,
        }
        return fn
    return decorator


def schemas():
    """产出 OpenAI tools 数组。"""
    return [{
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["parameters"],
        },
    } for t in _REGISTRY.values()]


def names():
    return list(_REGISTRY)


def call(name, arguments):
    """执行工具，返回字符串结果（给模型看）。异常也转成字符串，避免中断循环。"""
    spec = _REGISTRY.get(name)
    if spec is None:
        return "错误：不存在名为 {0} 的工具。可用工具：{1}".format(name, ", ".join(names()))
    if isinstance(arguments, str):
        try:
            from llm import loads_tolerant
            arguments = loads_tolerant(arguments)
        except ValueError:
            return "错误：工具参数不是合法 JSON。请精简参数后重新调用。"
    # LLM 的 arguments 解析失败时，llm.py 会退化成 {"__raw__": 原始字符串}
    if isinstance(arguments, dict) and "__raw__" in arguments:
        return ("错误：工具参数不是合法 JSON（通常是输出被 max_tokens 截断）。"
                "请把参数拆小或精简后重新调用。")
    try:
        result = spec["func"](**(arguments or {}))
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, indent=2)
        return result
    except TypeError as exc:
        return "错误：参数不匹配（{0}）。期望参数：{1}".format(
            exc, ", ".join((spec["parameters"].get("properties") or {}).keys()))
    except Exception as exc:                      # 工具内部异常不该炸掉 agent
        return "错误：{0}: {1}".format(type(exc).__name__, exc)


# ------------------------------------------------------------------ 计算器

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("只允许数字常量")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("不支持的表达式：{0}".format(type(node).__name__))


@tool(
    name="calculator",
    description="计算一个数学表达式，支持 + - * / // % ** 和括号。用于任何算术、百分比、复利计算。",
    parameters={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "例如 (1250 * 1.06) ** 3 - 100"}
        },
        "required": ["expression"],
    },
)
def calculator(expression):
    tree = ast.parse(expression, mode="eval")
    value = _eval_node(tree)
    # 浮点结果去掉无意义的尾零
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return "{0} = {1}".format(expression, value)


# ------------------------------------------------------------------ 时间

_TZ_OFFSETS = {
    "UTC": 0, "Asia/Shanghai": 8, "Asia/Tokyo": 9, "Asia/Singapore": 8,
    "Europe/London": 0, "Europe/Berlin": 1, "America/New_York": -5,
    "America/Los_Angeles": -8,
}


@tool(
    name="get_current_time",
    description="获取指定时区的当前日期时间。用户问“现在几点/今天几号”时必须调用，不要凭记忆回答。",
    parameters={
        "type": "object",
        "properties": {
            "timezone_name": {
                "type": "string",
                "description": "IANA 时区名，如 Asia/Shanghai、UTC",
                "default": "Asia/Shanghai",
            }
        },
        "required": [],
    },
)
def get_current_time(timezone_name="Asia/Shanghai"):
    offset = _TZ_OFFSETS.get(timezone_name)
    if offset is None:
        return "错误：不支持的时区 {0}，可选：{1}".format(
            timezone_name, ", ".join(sorted(_TZ_OFFSETS)))
    now = datetime.now(timezone(timedelta(hours=offset)))
    weekday = "一二三四五六日"[now.weekday()]
    return "{0} 周{1}（{2}）".format(
        now.strftime("%Y-%m-%d %H:%M:%S"), weekday, timezone_name)


# ------------------------------------------------------------------ 文件（只读）

def _safe_path(rel):
    """把相对路径限制在 WORKSPACE 内，挡掉 ../ 穿越和绝对路径。"""
    target = os.path.abspath(os.path.join(WORKSPACE, rel))
    if target != WORKSPACE and not target.startswith(WORKSPACE + os.sep):
        raise ValueError("只允许访问工作区内的文件：{0}".format(WORKSPACE))
    return target


@tool(
    name="list_files",
    description="列出工作区（E:\\agent\\LLM）某个子目录下的文件。用户想了解项目结构时调用。",
    parameters={
        "type": "object",
        "properties": {
            "subdir": {"type": "string", "description": "相对路径，默认根目录", "default": ""}
        },
        "required": [],
    },
)
def list_files(subdir=""):
    root = _safe_path(subdir)
    if not os.path.isdir(root):
        return "错误：目录不存在：{0}".format(subdir or ".")
    lines = []
    for entry in sorted(os.scandir(root), key=lambda e: (not e.is_dir(), e.name)):
        if entry.name in ("__pycache__", ".venv", ".git", ".mcp"):
            continue
        size = "" if entry.is_dir() else "  {0:,} B".format(entry.stat().st_size)
        lines.append("{0}{1}{2}".format(entry.name, "/" if entry.is_dir() else "", size))
    return "\n".join(lines) or "（空目录）"


@tool(
    name="read_file",
    description="读取工作区内一个文本文件的完整内容（单文件上限 6000 字符）。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作区的路径，如 llm.py"}
        },
        "required": ["path"],
    },
)
def read_file(path):
    target = _safe_path(path)
    if not os.path.isfile(target):
        return "错误：文件不存在：{0}".format(path)
    with open(target, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    if len(text) > 6000:
        return text[:6000] + "\n...[已截断，共 {0} 字符]".format(len(text))
    return text


if __name__ == "__main__":
    # 直接运行本文件 = 自测所有本地工具
    for name in names():
        print("=" * 60)
        print("tool:", name)
        if name == "calculator":
            print(call(name, {"expression": "(1250 * 1.06) ** 3"}))
        elif name == "get_current_time":
            print(call(name, {}))
        elif name == "list_files":
            print(call(name, {}))
        elif name == "read_file":
            print(call(name, {"path": ".gitignore"})[:200])
