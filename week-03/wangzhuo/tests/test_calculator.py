"""安全计算器测试：正确性 + 安全性。"""

from __future__ import annotations

import pytest

from reflexion_agent.tools.base import ToolError
from reflexion_agent.tools.calculator import evaluate, format_number


def test_basic_arithmetic() -> None:
    assert evaluate("(12+8)*3") == "60"
    assert evaluate("10/4") == "2.5"
    assert evaluate("7//2") == "3"
    assert evaluate("7%2") == "1"
    assert evaluate("2**10") == "1024"


def test_precedence_and_unary() -> None:
    assert evaluate("1+2*3") == "7"
    assert evaluate("-(2+3)*4") == "-20"
    assert evaluate("-2**2") == "-4"  # Python 语义：-(2**2)


def test_functions_and_constants() -> None:
    assert evaluate("sqrt(16)") == "4"
    assert evaluate("round(3.14159, 2)") == "3.14"
    assert evaluate("max(3, 8, 2)") == "8"
    assert evaluate("floor(pi)") == "3"


def test_float_formatting() -> None:
    assert evaluate("0.1+0.2") == "0.3"
    assert format_number(1.0) == "1"
    assert format_number(2.25) == "2.25"


def test_division_by_zero() -> None:
    with pytest.raises(ToolError):
        evaluate("1/0")


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('echo hi')",
        "open('secret.txt').read()",
        "x + 1",
        "[1, 2, 3]",
        "'a' * 3",
        "1 if True else 2",
        "lambda: 1",
        "2 ** 999",
        "(1).__class__",
    ],
)
def test_rejects_dangerous_or_unsupported(expr: str) -> None:
    with pytest.raises(ToolError):
        evaluate(expr)


def test_length_limit() -> None:
    with pytest.raises(ToolError):
        evaluate("1+" * 300 + "1")
