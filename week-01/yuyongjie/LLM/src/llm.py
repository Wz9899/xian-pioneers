# -*- coding: utf-8 -*-
"""llm.py — OpenAI 兼容协议的 LLM 客户端。

一套接口通吃 DeepSeek / Moonshot / 通义百炼 / 本地 Ollama / vLLM，
因为它们都实现了 /chat/completions 这个事实标准。

能力：
  * 流式 / 非流式统一返回结构
  * 原生 Function Calling（含流式下 tool_calls 的增量拼装）
  * 429、5xx 指数退避重试；401/402/400 直接失败并给出人话解释
  * 零第三方依赖（只用 httpx）

返回结构（dict）::

    {"content": "...", "reasoning": "...",
     "tool_calls": [{"id": "...", "name": "...", "arguments": {...}}],
     "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
     "finish_reason": "stop", "elapsed": 1.23}
"""
import json
import os
import sys
import time

import httpx

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

# 值得重试的状态码：限流、服务端临时故障
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

STATUS_HINT = {
    400: "请求格式有问题（比如给 deepseek-reasoner 传了 temperature）",
    401: "API Key 无效、写错或已删除",
    402: "账户余额不足",
    403: "无权访问该模型",
    404: "接口路径不对，检查 BASE_URL 是否少了 /v1",
    422: "参数校验失败",
    429: "触发限流或并发上限",
    500: "服务端内部错误",
    503: "服务端过载",
}


def loads_tolerant(text):
    """容错 JSON 解析。

    真实模型的 tool arguments 里经常出现未转义的换行/制表符（JSON 规范要求转义，
    但模型常常直接敲回车），严格模式会直接报错。strict=False 允许控制字符。
    """
    if not text or not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(text, strict=False)


class LLMError(Exception):
    """调用 LLM 失败。status 为 None 表示网络层错误。"""

    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload

    @property
    def retryable(self):
        return self.status is None or self.status in RETRYABLE_STATUS


def redact(key):
    """日志/截图里不出现完整 key，只留头尾各 4 位。"""
    if not key:
        return "<空>"
    return key[:4] + "***" + key[-4:] if len(key) > 12 else "***"


class LLMClient:
    def __init__(self, api_key, base_url=None, model=None, *,
                 timeout=180.0, max_retries=3, temperature=0.7,
                 max_tokens=8192, verbose=False):
        if not api_key:
            raise LLMError("缺少 API Key")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.verbose = verbose
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            # 连接阶段快速失败（网络问题早暴露），读阶段给足时间（长输出）
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0),
        )

    # ---------------------------------------------------------------- 公开接口

    def chat(self, messages, tools=None, stream=False,
             on_delta=None, on_reasoning=None, **overrides):
        """发起一次对话。

        :param messages: [{"role": "user", "content": "..."}]
        :param tools:    工具 schema 列表（OpenAI 格式）
        :param stream:   True 时逐段回调 on_delta
        :param on_delta: callable(str) 每收到一段正文
        :param on_reasoning: callable(str) 每收到一段思考（R1 才有）
        """
        payload = self.build_payload(messages, tools, stream, **overrides)

        if self.verbose:
            print("[llm] POST {0}/chat/completions model={1} stream={2} msgs={3}".format(
                self.base_url, self.model, stream, len(messages)), file=sys.stderr)

        started = time.perf_counter()
        if stream:
            return self._stream(payload, on_delta, on_reasoning, started)
        return self._once(payload, started)

    def build_payload(self, messages, tools=None, stream=False, **overrides):
        payload = {"model": self.model, "messages": messages, "stream": stream}
        # 输出上限：默认 4096，规划类任务输出结构化 JSON 很容易超，截断会毁掉整个 JSON
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        # R1 不接受采样参数，传了会 400
        if "reasoner" not in self.model:
            payload["temperature"] = self.temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload.update(overrides)
        return payload

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- 非流式

    def _once(self, payload, started):
        resp = self._post_with_retry(payload)
        data = resp.json()
        elapsed = time.perf_counter() - started

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        return {
            "content": msg.get("content") or "",
            "reasoning": msg.get("reasoning_content") or "",
            "tool_calls": self._normalize_tool_calls(msg.get("tool_calls") or []),
            "usage": data.get("usage") or {},
            "finish_reason": choice.get("finish_reason"),
            "elapsed": elapsed,
        }

    # ---------------------------------------------------------------- 流式

    def _stream(self, payload, on_delta, on_reasoning, started):
        attempt = 0
        while True:
            attempt += 1
            try:
                with self._client.stream("POST", "/chat/completions", json=payload,
                                         headers={"Accept": "text/event-stream"}) as resp:
                    if resp.status_code == 200:
                        return self._consume_stream(resp, on_delta, on_reasoning, started)
                    resp.read()
                    error = self._error_from(resp)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                error = LLMError("网络异常：{0}".format(exc))

            if error.retryable and attempt < self.max_retries:
                self._sleep_backoff(attempt)
                continue
            raise error

    def _consume_stream(self, resp, on_delta, on_reasoning, started):
        content, reasoning = [], []
        tool_calls = {}
        usage = {}
        first_token_at = None
        finish_reason = None

        for line in resp.iter_lines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data in ("[DONE]", ""):
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue          # 跳过 keep-alive 之类的噪声

            if chunk.get("usage"):
                usage = chunk["usage"]

            for choice in chunk.get("choices") or []:
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}

                if delta.get("reasoning_content"):
                    if first_token_at is None:
                        first_token_at = time.perf_counter() - started
                    reasoning.append(delta["reasoning_content"])
                    if on_reasoning:
                        on_reasoning(delta["reasoning_content"])

                if delta.get("content"):
                    if first_token_at is None:
                        first_token_at = time.perf_counter() - started
                    content.append(delta["content"])
                    if on_delta:
                        on_delta(delta["content"])

                # tool_calls 是分片下发的，按 index 累积 id/name/arguments
                for i, part in enumerate(delta.get("tool_calls") or []):
                    idx = part.get("index", i)
                    slot = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if part.get("id"):
                        slot["id"] = part["id"]
                    fn = part.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]

        # 流式累积出来的是扁平槽位，这里转成和非流式一致的 OpenAI 包装格式
        ordered = [{
            "id": tool_calls[k]["id"],
            "function": {"name": tool_calls[k]["name"],
                         "arguments": tool_calls[k]["arguments"]},
        } for k in sorted(tool_calls)]
        return {
            "content": "".join(content),
            "reasoning": "".join(reasoning),
            "tool_calls": self._normalize_tool_calls(ordered),
            "usage": usage,
            "finish_reason": finish_reason or ("tool_calls" if ordered else "stop"),
            "elapsed": time.perf_counter() - started,
            "first_token_at": first_token_at,
        }

    # ---------------------------------------------------------------- 内部

    def _post_with_retry(self, payload):
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._client.post("/chat/completions", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise LLMError("网络异常：{0}".format(exc))

            if resp.status_code == 200:
                try:
                    return resp
                except ValueError:
                    raise LLMError("响应不是 JSON：{0}".format(resp.text[:200]))

            error = self._error_from(resp)
            if error.retryable and attempt < self.max_retries:
                self._sleep_backoff(attempt, resp)
                continue
            raise error

    def _error_from(self, resp):
        try:
            body = resp.json()
            detail = body.get("error", body)
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
        except ValueError:
            detail = resp.text[:300]

        hint = STATUS_HINT.get(resp.status_code, "")
        message = "HTTP {0}{1}: {2}".format(
            resp.status_code, "（" + hint + "）" if hint else "", str(detail)[:400])
        return LLMError(message, status=resp.status_code, payload=detail)

    def _sleep_backoff(self, attempt, resp=None):
        delay = min(2 ** (attempt - 1), 8)
        if resp is not None:
            try:
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    delay = max(delay, float(retry_after))
            except (TypeError, ValueError):
                pass
        if self.verbose:
            print("[llm] 第 {0} 次失败，{1:.1f}s 后重试".format(attempt, delay), file=sys.stderr)
        time.sleep(delay)

    @staticmethod
    def _normalize_tool_calls(raw_calls):
        normalized = []
        for call in raw_calls:
            fn = call.get("function") or {}
            args_raw = fn.get("arguments") or ""
            try:
                args = loads_tolerant(args_raw)
            except ValueError:
                args = {"__raw__": args_raw}
            normalized.append({
                "id": call.get("id") or "",
                "name": fn.get("name") or "",
                "arguments": args,
                "arguments_raw": args_raw,
            })
        return normalized


# ---------------------------------------------------------------- 便捷构造

def from_env(env_file=None, model=None, verbose=False):
    """从环境变量 / .env 构造客户端。"""
    if env_file:
        load_env(env_file)
    api_key = (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    try:
        max_tokens = int(os.environ.get("LLM_MAX_TOKENS") or 8192)
    except ValueError:
        max_tokens = 8192
    return LLMClient(
        api_key=api_key,
        base_url=os.environ.get("LLM_BASE_URL") or os.environ.get("BASE_URL"),
        model=model or os.environ.get("LLM_MODEL") or os.environ.get("MODEL"),
        max_tokens=max_tokens,
        verbose=verbose,
    )


def load_env(path):
    """极简 .env 解析；已存在的环境变量优先。"""
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
