"""安全计算器工具。

不允许 ``eval``。做法是解析成 AST 后只放行白名单节点与白名单函数：
* 支持四则运算、整除、取余、幂、括号、正负号；
* 支持 sqrt / abs / round / min / max / log / log10 / exp / floor / ceil；
* 常量 pi、e；
* 限制表达式长度、幂指数与数值范围，防止 ``9**9**9`` 这类资源耗尽。
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from .base import ToolError

MAX_EXPR_LEN = 500
MAX_POW_EXP = 100
MAX_ABS_VALUE = 1e15
MAX_DEPTH = 30

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
}

_CONSTS = {"pi": math.pi, "e": math.e}


def _check_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("表达式中只能出现数字")
    if not math.isfinite(float(value)):
        raise ToolError("结果不是有限数值")
    if abs(value) > MAX_ABS_VALUE:
        raise ToolError("数值超出允许范围")
    return value


def _eval(node: ast.AST, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise ToolError("表达式嵌套过深")
    if isinstance(node, ast.Expression):
        return _eval(node.body, depth + 1)
    if isinstance(node, ast.Constant):
        return _check_number(node.value)
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, depth + 1), _eval(node.right, depth + 1)
        op_type = type(node.op)
        if op_type not in _BIN_OPS:
            raise ToolError("不支持的运算符")
        if op_type is ast.Pow and abs(right) > MAX_POW_EXP:
            raise ToolError("幂指数过大")
        try:
            return _check_number(_BIN_OPS[op_type](left, right))
        except ZeroDivisionError as exc:
            raise ToolError("除数不能为 0") from exc
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, depth + 1)
        if type(node.op) not in _UNARY_OPS:
            raise ToolError("不支持的一元运算符")
        return _check_number(_UNARY_OPS[type(node.op)](operand))
    if isinstance(node, ast.Name):
        if node.id not in _CONSTS:
            raise ToolError(f"未知名称：{node.id}")
        return _CONSTS[node.id]
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ToolError("不允许的函数调用")
        if node.keywords:
            raise ToolError("函数不支持关键字参数")
        args = [_eval(a, depth + 1) for a in node.args]
        try:
            return _check_number(_FUNCS[node.func.id](*args))
        except (TypeError, ValueError) as exc:
            raise ToolError(f"函数参数不合法：{exc}") from exc
    raise ToolError(f"不支持的语法：{type(node).__name__}")


def format_number(value: float) -> str:
    """把浮点结果格式化成人类可读形式，避免 0.30000000000000004。"""
    if isinstance(value, int):
        return str(value)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text or "0"


def evaluate(expression: str) -> str:
    """计算表达式并返回字符串结果；出错抛 ToolError。"""
    expression = (expression or "").strip()
    if not expression:
        raise ToolError("表达式不能为空")
    if len(expression) > MAX_EXPR_LEN:
        raise ToolError("表达式过长")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"表达式语法错误：{exc.msg}") from exc
    return format_number(_eval(tree))


def calculator(expression: str) -> str:
    """供 Agent 调用的工具函数。"""
    return f"{expression} = {evaluate(expression)}"
