"""LLM 适配层。

三种实现共享同一个极简接口 ``chat(messages, tools) -> LLMResponse``：

* ``OpenAICompatClient`` —— 真实模型，走 OpenAI 兼容协议（DeepSeek/OpenAI/…）；
* ``ReplayClient``        —— 离线回放真实录制，零 Key、零网络即可复现；
* ``ScriptedClient``      —— 单元测试用的脚本化假模型。

把模型调用抽象成一层，是「反思循环」能被测试、被消融、被回放的前提。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .config import RECORDINGS_DIR, Settings


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"
    latency: float = 0.0
    model: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [
                {"id": c.id, "name": c.name, "arguments": c.arguments}
                for c in self.tool_calls
            ],
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "model": self.model,
        }

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "LLMResponse":
        return cls(
            content=rec.get("content", ""),
            tool_calls=[
                ToolCall(c.get("id", f"call_{i}"), c.get("name", ""), c.get("arguments") or {})
                for i, c in enumerate(rec.get("tool_calls") or [])
            ],
            usage=rec.get("usage") or {},
            finish_reason=rec.get("finish_reason", "stop"),
            model=rec.get("model", ""),
        )


class LLMClient(Protocol):
    mode: str

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None) -> LLMResponse:
        ...


# ---------------------------------------------------------------------------
# 真实模型：OpenAI 兼容协议
# ---------------------------------------------------------------------------
class OpenAICompatClient:
    """DeepSeek、OpenAI、Moonshot、通义等 OpenAI 兼容接口的通用客户端。"""

    mode = "online"

    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI  # 延迟导入，离线模式不需要

        self.settings = settings
        self.model = settings.model
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
            max_retries=1,
        )

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None) -> LLMResponse:
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.settings.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    args = {"value": args}
            except json.JSONDecodeError:
                args = {"__parse_error__": raw}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        usage = {}
        if resp.usage:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
        return LLMResponse(
            content=(msg.content or "").strip(),
            tool_calls=calls,
            usage=usage,
            finish_reason=choice.finish_reason or "stop",
            latency=time.perf_counter() - started,
            model=self.model,
        )


# ---------------------------------------------------------------------------
# 回放：把真实运行录制下来，无 Key 也能完整复现
# ---------------------------------------------------------------------------
class ReplayClient:
    """按会话顺序回放录制好的模型响应。

    录制文件位于 ``reflexion_agent/recordings/<session>.json``。
    若会话不存在（例如用户离线问了一个没录过的问题），返回一条说明性文本，
    Agent 会正常收尾而不是抛异常。
    """

    mode = "offline"

    def __init__(self, recordings_dir: Path | None = None) -> None:
        self.recordings_dir = recordings_dir or RECORDINGS_DIR
        self.session = ""
        self._responses: list[dict] = []
        self._cursor = 0
        self._missing_notice = False
        self.session_found = True

    def set_session(self, session: str) -> None:
        self.session = session
        self._cursor = 0
        self._missing_notice = False
        path = self.recordings_dir / f"{session}.json"
        self.session_found = path.exists()
        if self.session_found:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._responses = data.get("responses", [])
        else:
            self._responses = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None) -> LLMResponse:
        if self._cursor < len(self._responses):
            rec = self._responses[self._cursor]
            self._cursor += 1
            return LLMResponse.from_record(rec)
        if not self._missing_notice:
            self._missing_notice = True
            return LLMResponse(
                content=(
                    "当前为离线回放模式，本次会话没有内置录制。"
                    "请设置 LLM_API_KEY 后使用真实模型运行，"
                    "或运行 `python -m reflexion_agent demo` 查看已录制示例。"
                ),
                finish_reason="offline_stub",
            )
        return LLMResponse(content="（离线回放结束）", finish_reason="offline_stub")


class RecordingClient:
    """装饰器：包装真实客户端，把每次响应按会话写入录制文件。"""

    def __init__(self, inner: LLMClient, out_dir: Path | None = None) -> None:
        self.inner = inner
        self.mode = getattr(inner, "mode", "record")
        self.out_dir = out_dir or RECORDINGS_DIR
        self.session = ""
        self._records: dict[str, list[dict]] = {}
        self._model = getattr(inner, "model", "")

    def set_session(self, session: str) -> None:
        self.session = session
        # 让被包装的客户端也支持会话切换（例如嵌套回放）
        if hasattr(self.inner, "set_session"):
            self.inner.set_session(session)  # type: ignore[attr-defined]

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None) -> LLMResponse:
        resp = self.inner.chat(messages, tools)
        self._records.setdefault(self.session, []).append(resp.to_record())
        return resp

    def flush(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for session, responses in self._records.items():
            path = self.out_dir / f"{session}.json"
            payload = {"session": session, "model": self._model, "responses": responses}
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class ScriptedClient:
    """按顺序吐出预设响应，用于单元测试反思循环。"""

    mode = "scripted"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None) -> LLMResponse:
        self.calls.append(messages)
        if not self._responses:
            return LLMResponse(content="脚本已耗尽")
        return self._responses.pop(0)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def build_llm(settings: Settings) -> LLMClient:
    if settings.use_mock:
        return ReplayClient()
    return OpenAICompatClient(settings)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出中稳健地提取第一个 JSON 对象。

    模型经常把 JSON 包在 ```json 代码块或解释性文字里，这里逐层降级：
    直接解析 → 去代码围栏 → 截取首尾花括号 → 正则兜底。
    """
    if not text:
        return None
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        candidates.append(fenced.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for raw in candidates:
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None
