"""工具抽象与注册表。

一个工具 = 名字 + 自然语言描述 + JSON Schema 参数 + 可调用实现。
注册表负责三件事：
1. 把工具描述转换成 OpenAI function-calling 的 ``tools`` 参数；
2. 校验/纠正常见参数错误（缺参、类型写成字符串等）；
3. 统一捕获异常并截断过长输出，保证 Agent 不会因工具崩溃而中断。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Settings


class ToolError(Exception):
    """工具执行失败（业务错误，而非程序错误）。"""


@dataclass
class ToolResult:
    ok: bool
    content: str
    error: str = ""

    def render(self) -> str:
        return self.content if self.ok else f"工具执行失败：{self.error}"


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    timeout: float = 20.0

    def schema(self) -> dict[str, Any]:
        """转换为 OpenAI function-calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolRegistry:
    settings: Settings
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    # -- 注册 ---------------------------------------------------------------
    def register(self, spec: ToolSpec) -> None:
        if not spec.name.replace("_", "").isalnum():
            raise ValueError(f"非法工具名：{spec.name}")
        self._tools[spec.name] = spec

    # -- 暴露 ---------------------------------------------------------------
    def specs(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # -- 调用 ---------------------------------------------------------------
    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(False, "", f"未知工具 {name!r}，可用工具：{', '.join(self.names())}")
        if not isinstance(arguments, dict):
            return ToolResult(False, "", "工具参数必须是 JSON 对象")

        try:
            kwargs = self._validate(spec, arguments)
        except ToolError as exc:
            return ToolResult(False, "", str(exc))

        try:
            output = spec.handler(**kwargs)
        except ToolError as exc:
            return ToolResult(False, "", str(exc))
        except Exception as exc:  # noqa: BLE001 —— 工具层必须兜住所有异常
            return ToolResult(False, "", f"{type(exc).__name__}: {exc}")

        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        if len(text) > self.settings.tool_output_limit:
            text = text[: self.settings.tool_output_limit] + "\n…（输出过长已截断）"
        return ToolResult(True, text)

    @staticmethod
    def _validate(spec: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
        """做最小但有用的参数校验：存在性 + 基础类型纠正。"""
        schema = spec.parameters or {}
        props: dict[str, Any] = schema.get("properties", {})
        required: list[str] = schema.get("required", [])

        missing = [k for k in required if k not in arguments or arguments[k] in ("", None)]
        if missing:
            raise ToolError(f"缺少必填参数：{', '.join(missing)}")

        cleaned: dict[str, Any] = {}
        for key, value in arguments.items():
            if key not in props:
                continue  # 忽略模型多写的字段，避免无谓失败
            expected = props[key].get("type")
            try:
                if expected == "integer" and isinstance(value, str):
                    value = int(float(value))
                elif expected == "number" and isinstance(value, str):
                    value = float(value)
                elif expected == "string" and not isinstance(value, str):
                    value = str(value)
            except (TypeError, ValueError) as exc:
                raise ToolError(f"参数 {key} 类型不正确，应为 {expected}") from exc
            cleaned[key] = value
        return cleaned
