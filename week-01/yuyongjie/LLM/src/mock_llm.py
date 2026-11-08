# -*- coding: utf-8 -*-
"""mock_llm.py — 本地假 LLM 服务（无 API Key 时用来验证 agent 循环）。

它实现了 /chat/completions 的 JSON 与 SSE 两种模式，并且会**故意把 tool_calls
拆成多个分片下发**，专门用来验证 llm.py 里最容易写错的「流式 tool_calls 增量拼装」。

注意：这不是交作业的演示，而是单元测试。

    server = MockLLMServer().start()   # http://127.0.0.1:8899
    ...同一套 Agent / LLMClient 代码...
    server.stop()

换成真模型时，走的完全是同一条代码路径，只是 base_url 和 key 变了。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8899

TOOL_LABELS = {
    "get_current_time": "当前时间",
    "calculator": "计算结果",
    "list_skills": "Skill 列表",
    "load_skill": "Skill 正文",
    "read_file": "文件内容",
    "mcp_list_directory": "MCP 目录列表",
}


class Brain:
    """一个规则引擎，扮演“会调用工具的模型”。"""

    def respond(self, messages):
        last_user = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                last_user = message.get("content") or ""
                break

        # 已经调用过的工具名 + 对应的结果
        used, results = [], []
        for message in messages:
            for call in message.get("tool_calls") or []:
                used.append(call["function"]["name"])
        results = [m.get("content") or "" for m in messages if m.get("role") == "tool"]

        plan = self._plan(last_user)
        pending = [item for item in plan if item[0] not in used]
        if pending:
            name, arguments = pending[0]
            return self._tool(name, arguments)

        # 加载完 Skill 之后按 Skill 的步骤去读代码
        if "load_skill" in used and "read_file" not in used:
            return self._tool("read_file", {"path": "tools.py"})

        return self._final(used, results, last_user)

    @staticmethod
    def _plan(text):
        lowered = text.lower()
        plan = []
        if "几点" in text or "什么时间" in text:
            plan.append(("get_current_time", {"timezone_name": "Asia/Shanghai"}))
        if any(word in text for word in ("计算", "算一下", "复利", "多少元", "年化")):
            plan.append(("calculator", {"expression": "1250 * 1.06 ** 3"}))
        if "skill" in lowered or "审查" in text or "review" in lowered:
            plan.append(("load_skill", {"name": "code-reviewer"}))
        if "mcp" in lowered and any(word in text for word in ("文件", "目录", "工作区")):
            plan.append(("mcp_list_directory", {"path": "."}))
        return plan

    def _final(self, used, results, last_user):
        if not used:
            return self._text("我在。你可以让我算数、查时间、审查代码，或通过 MCP 浏览文件。")

        if "read_file" in used and "load_skill" in used:
            return self._text(
                "已按 code-reviewer Skill 的四个维度审查完 tools.py 的 calculator。\n"
                "总评：结构清晰，AST 白名单方案是对的，异常统一收敛成字符串，未发现 P0 阻断问题。\n\n"
                "```json\n"
                '[{"level":"P1","where":"tools.py:_safe_path",'
                '"issue":"用字符串前缀比较判断路径归属，E:\\\\agent\\\\LLM-evil 这类同前缀目录会被误放行",'
                '"fix":"改用 os.path.commonpath([target, WORKSPACE]) == WORKSPACE 判断"},\n'
                ' {"level":"P2","where":"tools.py:calculator",'
                '"issue":"未限制指数规模，2**10**9 会把进程算死",'
                '"fix":"对 ast.Pow 的指数加 abs(exp) <= 1000 上限校验"}]\n'
                "```\n"
                "优先级：先修 P1 的路径判断，P2 可以顺手加上。")

        lines = []
        for name, content in zip(used, results):
            label = TOOL_LABELS.get(name, name)
            snippet = " ".join((content or "").split())[:160]
            lines.append("· {0}：{1}".format(label, snippet))

        return self._text(
            "我调用了 {0} 个工具，结果如下：\n{1}\n\n"
            "（以上答案由 mock 模型基于**真实工具返回**拼装，"
            "验证的是 agent 循环与工具链路，不是模型能力。）".format(len(used), "\n".join(lines)))

    def _text(self, content):
        return {"content": content, "tool_calls": [], "usage": self._usage(content)}

    @staticmethod
    def _tool(name, arguments, call_id="call_mock_1"):
        return {
            "content": "",
            "tool_calls": [{"id": call_id, "name": name,
                            "arguments": json.dumps(arguments, ensure_ascii=False)}],
            "usage": {"prompt_tokens": 128, "completion_tokens": 24, "total_tokens": 152},
        }

    @staticmethod
    def _usage(text):
        completion = max(4, len(text) // 2)
        return {"prompt_tokens": 128, "completion_tokens": completion,
                "total_tokens": 128 + completion}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    brain = None

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        reply = self.brain.respond(body.get("messages") or [])
        if body.get("stream"):
            self._stream(reply)
        else:
            self._json(reply)

    def _json(self, reply):
        payload = {
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": reply["content"] or None,
                    "tool_calls": [{
                        "id": c["id"], "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    } for c in reply["tool_calls"]] or None,
                },
                "finish_reason": "tool_calls" if reply["tool_calls"] else "stop",
            }],
            "usage": reply["usage"],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, reply):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj):
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        if reply["tool_calls"]:
            call = reply["tool_calls"][0]
            # 把 id / name / arguments 拆成 4 个分片，模拟真实服务端的分帧行为
            emit({"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                {"index": 0, "id": call["id"], "type": "function",
                 "function": {"name": "", "arguments": ""}}]}, "finish_reason": None}]})
            emit({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"name": call["name"]}}]}, "finish_reason": None}]})
            half = len(call["arguments"]) // 2
            for piece in (call["arguments"][:half], call["arguments"][half:]):
                emit({"choices": [{"index": 0, "delta": {"tool_calls": [
                    {"index": 0, "function": {"arguments": piece}}]}, "finish_reason": None}]})
            emit({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        else:
            text = reply["content"]
            for i in range(0, len(text), 12):
                emit({"choices": [{"index": 0, "delta": {"content": text[i:i + 12]},
                                   "finish_reason": None}]})
            emit({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                  "usage": reply["usage"]})

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class MockLLMServer:
    def __init__(self, port=PORT):
        self.port = port
        self.httpd = None
        self.thread = None

    def start(self):
        handler = type("Handler", (_Handler,), {"brain": Brain()})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    @property
    def base_url(self):
        return "http://127.0.0.1:{0}".format(self.port)

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


if __name__ == "__main__":
    server = MockLLMServer().start()
    print("mock LLM 已启动：{0}（Ctrl+C 停止）".format(server.base_url))
    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        server.stop()
