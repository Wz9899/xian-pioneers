# -*- coding: utf-8 -*-
"""mcp_client.py — 极简 MCP 客户端（stdio 传输，JSON-RPC 2.0）。

MCP（Model Context Protocol）是 Anthropic 提出的标准协议，让模型通过统一接口
访问外部能力（文件系统、数据库、浏览器、GitHub……）。官方 server 生态已有上百个。

传输方式：把 server 作为子进程启动，用 stdin/stdout 收发 **按行分隔的 JSON-RPC**。
握手流程：
    1. client → initialize
    2. server → result（能力协商）
    3. client → notifications/initialized
    4. client → tools/list          （发现能力）
    5. client → tools/call          （调用能力）

本文件只实现 client 侧必需的最小集合，不依赖官方 SDK，便于理解协议本身。
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time

PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    pass


class MCPClient:
    def __init__(self, command, args=None, cwd=None, env=None,
                 timeout=30.0, name="mcp", debug=False):
        self.command = command
        self.args = list(args or [])
        self.cwd = cwd
        self.timeout = timeout
        self.name = name
        self.debug = debug
        self.proc = None
        self.server_info = {}
        self._inbox = queue.Queue()
        self._next_id = 0
        self._stderr_tail = []
        self._env = env

    # ------------------------------------------------------------ 生命周期

    def start(self):
        merged_env = dict(os.environ)
        if self._env:
            merged_env.update(self._env)
        self.proc = subprocess.Popen(
            [self.command] + self.args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.cwd, env=merged_env,
            encoding="utf-8", errors="replace", bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self._handshake()
        return self

    def _read_stdout(self):
        for line in self.proc.stdout:          # 按行读，MCP stdio 就是 NDJSON
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if self.debug:
                print("[mcp<] " + line[:300], file=sys.stderr)
            self._inbox.put(message)

    def _read_stderr(self):
        for line in self.proc.stderr:
            line = line.rstrip()
            if line:
                self._stderr_tail = (self._stderr_tail + [line])[-20:]
                if self.debug:
                    print("[mcp!] " + line[:300], file=sys.stderr)

    def _handshake(self):
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"roots": {"listChanged": False}, "sampling": {}},
            "clientInfo": {"name": "dsh-mini-agent", "version": "1.0.0"},
        })
        self.server_info = result.get("serverInfo") or {}
        self._notify("notifications/initialized")

    def close(self, timeout=5):
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------ 能力

    def list_tools(self):
        """tools/list，返回 [{"name", "description", "inputSchema"}]。"""
        tools, cursor = [], None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:                     # 支持分页，官方 server 通常一页给完
                break
        return tools

    def call_tool(self, name, arguments=None):
        """tools/call，把 content 里的文本拼起来返回。"""
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            return "MCP 工具报错：{0}".format(self._flatten(result))
        return self._flatten(result)

    @staticmethod
    def _flatten(result):
        parts = []
        for item in result.get("content") or []:
            if item.get("type") == "text":
                parts.append(item.get("text") or "")
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts).strip() or json.dumps(result, ensure_ascii=False)

    # ------------------------------------------------------------ JSON-RPC

    def _request(self, method, params=None):
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method,
                    "params": params or {}})

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError("等待 {0} 响应超时（{1}s）。server stderr:\n{2}".format(
                    method, self.timeout, "\n".join(self._stderr_tail)))
            try:
                message = self._inbox.get(timeout=remaining)
            except queue.Empty:
                continue

            if message.get("id") != request_id:
                continue                       # 通知或过期响应，丢弃
            if "error" in message:
                err = message["error"]
                raise MCPError("{0} 失败：{1}（code={2}）".format(
                    method, err.get("message"), err.get("code")))
            return message.get("result") or {}

    def _notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _send(self, message):
        if self.proc is None or self.proc.poll() is not None:
            raise MCPError("MCP server 已退出。stderr:\n{0}".format("\n".join(self._stderr_tail)))
        if self.debug:
            print("[mcp>] " + json.dumps(message, ensure_ascii=False)[:300], file=sys.stderr)
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()


# ---------------------------------------------------------------- 常用 server

def filesystem_server(workspace, name="filesystem"):
    """官方 filesystem MCP server（需要 npm 装过，见 .mcp/node_modules）。"""
    from paths import MCP_DIR, PROJECT_ROOT
    entry = os.path.join(MCP_DIR, "node_modules", "@modelcontextprotocol",
                         "server-filesystem", "dist", "index.js")
    if not os.path.isfile(entry):
        raise MCPError("没找到 filesystem server：{0}\n请先执行：\n"
                       "  npm install --prefix .mcp @modelcontextprotocol/server-filesystem".format(entry))
    node = os.environ.get("NODE_EXE") or "node"
    return MCPClient(node, [entry, workspace], name=name, cwd=PROJECT_ROOT)


if __name__ == "__main__":
    # 自测：连接 filesystem server，列出目录。输出刻意控制在一屏内，方便截图。
    from paths import PROJECT_ROOT
    workspace = PROJECT_ROOT
    client = filesystem_server(workspace)
    try:
        client.start()
        print("server   : {0} v{1}".format(
            client.server_info.get("name"), client.server_info.get("version")))
        print("protocol : {0}".format(PROTOCOL_VERSION))
        discovered = client.list_tools()
        print("tools    : 共 {0} 个".format(len(discovered)))
        print("           " + ", ".join(t["name"] for t in discovered))
        print("-" * 78)
        print("调用 list_directory(path='.')：")
        print(client.call_tool("list_directory", {"path": "."}))
    finally:
        client.close()
