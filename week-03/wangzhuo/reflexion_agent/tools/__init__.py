"""工具层统一出口。"""

from __future__ import annotations

from ..config import Settings
from .base import ToolError, ToolRegistry, ToolResult, ToolSpec
from .calculator import calculator
from .internal_api import exchange_rate, internal_api
from .search import SearchTool

__all__ = [
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "build_registry",
]


def build_registry(settings: Settings) -> ToolRegistry:
    """注册四个真实工具：计算、联网搜索、内部知识库、汇率换算。"""
    registry = ToolRegistry(settings)
    # 离线模式（无 Key 回放）下也禁用联网检索，保证回放快速、可复现
    use_network = not (settings.search_offline or settings.use_mock)
    search = SearchTool(top_k=settings.search_top_k, use_network=use_network)

    registry.register(
        ToolSpec(
            name="calculator",
            description=(
                "计算数学表达式，支持 + - * / // % **、括号、sqrt/abs/round/log 等函数。"
                "凡是涉及数值计算都应调用本工具，不要心算，避免算术错误。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "要计算的表达式，例如 (3*480+2*350)*0.9",
                    }
                },
                "required": ["expression"],
            },
            handler=calculator,
        )
    )

    registry.register(
        ToolSpec(
            name="web_search",
            description=(
                "联网搜索公开信息。需要人物、日期、新闻、定义等外部事实时调用。"
                "返回标题、摘要和链接；摘要不足以回答时请换关键词重试。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，尽量具体"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 3", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
            },
            handler=search,
        )
    )

    registry.register(
        ToolSpec(
            name="internal_api",
            description=(
                "查询公司内部知识库，覆盖差旅报销、年假、VPN、采购审批、会议室等制度流程。"
                "回答公司内部规定时必须先查此工具，不得编造条款或数字。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索问题或关键词，例如「出差住宿报销上限」"},
                    "top_k": {"type": "integer", "description": "返回文档数，默认 2", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
            },
            handler=internal_api,
        )
    )

    registry.register(
        ToolSpec(
            name="exchange_rate",
            description=(
                "按公司内部结算汇率换算货币，支持 USD/EUR/SEK/CNY。"
                "涉及金额换算时先调用本工具拿到汇率与结果。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "base": {"type": "string", "description": "源货币代码，如 USD"},
                    "target": {"type": "string", "description": "目标货币代码，如 CNY"},
                    "amount": {"type": "number", "description": "金额，默认 1"},
                },
                "required": ["base", "target"],
            },
            handler=exchange_rate,
        )
    )
    return registry
