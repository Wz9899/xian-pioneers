# -*- coding: utf-8 -*-
"""agent.py — 把 LLM、本地工具、Skill、MCP 串成一个能自主决策的小 agent。

核心就是一个 ReAct 风格的循环：

    用户输入 → 模型决定「直接回答」还是「调工具」
            → 调工具则执行、把结果回灌 → 再问模型
            → 直到模型不再要求调工具，输出最终答案

三类能力统一成同一种形态（Function Calling），所以循环代码不需要关心
它是本地 Python 函数、Skill 还是 MCP 远程工具。
"""
import json
import sys

import llm
import skills
import tools

BASE_SYSTEM = """你是一个严谨的工程助手，运行在用户本机的 Windows 环境。

行为准则：
1. 任何时候都不要凭记忆猜测实时信息（当前时间、文件内容、目录结构）——必须调用工具获取。
2. 需要一个技能（Skill）才能完成的任务，先 list_skills / load_skill，再严格按其中的步骤执行。
3. 工具返回错误时，先读懂错误原因再决定是否重试，不要盲目重复同一个调用。
4. 回答用中文，简洁直接；结论先行，不要堆客套话。"""


def build_system_prompt():
    parts = [BASE_SYSTEM]
    catalog = skills.catalog()
    if catalog:
        parts.append(catalog)
    return "\n\n".join(parts)


class Agent:
    def __init__(self, client, max_steps=8, verbose=True, stream=True, live=False,
                 tool_output_limit=4000):
        self.client = client
        self.max_steps = max_steps
        self.verbose = verbose
        self.stream = stream
        self.live = live              # True 时把模型输出的每个 token 实时打到终端
        self.tool_output_limit = tool_output_limit
        self.messages = [{"role": "system", "content": build_system_prompt()}]
        # MCP 路由：本地工具名 -> (client 对象, 原始工具名)
        self._mcp_routes = {}
        self._mcp_schemas = []
        self.trace = []          # [(step, kind, text)] 供 demo 汇总

    # ------------------------------------------------------------ MCP

    def attach_mcp(self, client, allow=None, prefix="mcp_"):
        """把 MCP server 的工具挂进 agent。allow 为白名单（None 表示全挂）。"""
        added = 0
        self._mcp_client = client
        for spec in client.list_tools():
            if allow is not None and spec["name"] not in allow:
                continue
            local = (prefix + spec["name"])[:64]
            self._mcp_routes[local] = spec["name"]
            self._mcp_schemas.append({
                "type": "function",
                "function": {
                    "name": local,
                    "description": "[MCP:{0}] {1}".format(
                        client.name, spec.get("description") or spec["name"])[:1024],
                    "parameters": spec.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
            added += 1
        if self.verbose:
            self._log("mcp", "挂载 {0} 个 MCP 工具：{1}".format(
                added, ", ".join(self._mcp_routes)))
        return added

    # ------------------------------------------------------------ 主循环

    def run(self, user_input):
        self.messages.append({"role": "user", "content": user_input})
        self.trace.append(("user", user_input))

        for step in range(1, self.max_steps + 1):
            response = self._call_model(step)

            if not response["tool_calls"]:
                return response["content"]

            assistant = {
                "role": "assistant",
                "content": response["content"] or "",
                "tool_calls": [{
                    "id": call["id"] or "call_{0}_{1}".format(step, i),
                    "type": "function",
                    "function": {"name": call["name"],
                                 "arguments": call["arguments_raw"] or "{}"},
                } for i, call in enumerate(response["tool_calls"])],
            }
            self.messages.append(assistant)

            for i, call in enumerate(response["tool_calls"]):
                call_id = assistant["tool_calls"][i]["id"]
                self._log("call", "{0}({1})".format(
                    call["name"], json.dumps(call["arguments"], ensure_ascii=False)))
                output = self._execute(call["name"], call["arguments"])
                self._log("result", output)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": output[: self.tool_output_limit],
                })

        return "（已达到最大步数 {0}，任务未完成。最后一步状态见上方工具结果。）".format(
            self.max_steps)

    def _call_model(self, step):
        self._log("step", "第 {0} 步：请求模型".format(step))
        printed_any = [False]

        def on_delta(piece):
            printed_any[0] = True
            sys.stdout.write(piece)
            sys.stdout.flush()

        def on_reasoning(piece):
            if not printed_any[0]:
                sys.stdout.write("\033[90m")   # 灰色显示思考过程
                sys.stdout.flush()
            sys.stdout.write(piece)
            sys.stdout.flush()

        response = self.client.chat(
            self.messages,
            tools=self._all_schemas(),
            stream=self.stream,
            on_delta=on_delta if self.live else None,
            on_reasoning=on_reasoning if self.live else None,
        )
        if self.verbose and printed_any[0]:
            sys.stdout.write("\033[0m\n")
            sys.stdout.flush()
        # 输出被截断时给出明确信号，否则只会表现成「工具参数不是合法 JSON」
        if response.get("finish_reason") == "length":
            print("[警告] 模型输出被 max_tokens 截断，结果可能不完整"
                  "（可用 LLM_MAX_TOKENS 调大）", file=sys.stderr)
        return response

    def _all_schemas(self):
        return tools.schemas() + self._mcp_schemas

    def _execute(self, name, arguments):
        if name in self._mcp_routes:
            original = self._mcp_routes[name]
            try:
                return self._mcp_client.call_tool(original, arguments or {})
            except Exception as exc:
                return "MCP 调用失败：{0}".format(exc)
        return tools.call(name, arguments)

    # ------------------------------------------------------------ 日志

    def _log(self, kind, text):
        self.trace.append((kind, text))
        if not self.verbose:
            return
        color = {"user": "\033[36m", "step": "\033[90m", "call": "\033[33m",
                 "result": "\033[32m", "mcp": "\033[35m"}.get(kind, "")
        label = {"user": "用户", "step": "步骤", "call": "调用工具",
                 "result": "工具结果", "mcp": "MCP"}.get(kind, kind)
        body = text if len(text) <= 110 else text[:110] + " …"
        print("{0}[{1}] {2}\033[0m".format(color, label, body))

    def closed(self):
        self.client.close()


def demo_agent(client, mcp=True):
    """不接 MCP 也能跑：mcp=False 时只有本地工具 + Skill。"""
    from paths import PROJECT_ROOT
    agent = Agent(client)
    if mcp:
        from mcp_client import filesystem_server
        mcp_client = filesystem_server(PROJECT_ROOT)
        mcp_client.start()
        agent.attach_mcp(mcp_client, allow={
            "list_allowed_directories", "list_directory", "read_text_file",
            "get_file_info", "search_files", "directory_tree",
        })
    return agent
