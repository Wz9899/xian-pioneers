"""工具层测试：注册表、参数校验、真实本地 HTTP API、离线搜索。"""

from __future__ import annotations

from reflexion_agent.config import Settings
from reflexion_agent.tools import build_registry
from reflexion_agent.tools.internal_api import exchange_rate, internal_api
from reflexion_agent.tools.search import SearchTool


def test_registry_schema_and_call() -> None:
    registry = build_registry(Settings())
    names = set(registry.names())
    assert {"calculator", "web_search", "internal_api", "exchange_rate"} <= names

    specs = registry.specs()
    assert all(s["type"] == "function" for s in specs)
    calculator = next(s for s in specs if s["function"]["name"] == "calculator")
    assert calculator["function"]["parameters"]["required"] == ["expression"]

    result = registry.call("calculator", {"expression": "2+2"})
    assert result.ok and "4" in result.content


def test_registry_error_handling() -> None:
    registry = build_registry(Settings())
    assert not registry.call("no_such_tool", {}).ok
    assert not registry.call("calculator", {}).ok
    assert not registry.call("calculator", {"expression": "1/0"}).ok
    # 字符串数字会被纠正成整数
    ok = registry.call("internal_api", {"query": "住宿报销", "top_k": "2"})
    assert ok.ok and "450" in ok.content


def test_internal_api_is_real_http() -> None:
    text = internal_api("一线城市住宿报销上限")
    assert "450" in text and "hr-001" in text
    fx = exchange_rate("USD", "CNY", 500)
    assert "7.18" in fx and "3590" in fx


def test_offline_search_fallback() -> None:
    tool = SearchTool(use_network=False)
    text = tool("2024 诺贝尔物理学奖")
    assert "离线快照" in text
    assert "Hopfield" in text and "Hinton" in text


def test_search_empty_query() -> None:
    tool = SearchTool(use_network=False)
    assert "未找到" in tool("完全不存在的关键词xyz")
