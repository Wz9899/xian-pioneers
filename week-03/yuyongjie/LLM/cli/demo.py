# -*- coding: utf-8 -*-
"""demo.py — 一条命令跑完四个场景，输出可直接截图。

    .\\run.ps1 demo.py            # 用 .env 里的真实 key
    .\\run.ps1 demo.py --mock     # 无 key 时用本地 mock 模型验证整条链路
    .\\run.ps1 demo.py --only 2   # 只跑第 2 个场景
"""
import argparse
import os
import platform
import sys
import time

import _bootstrap          # noqa: F401 — 把 src/ 加入 sys.path
import console_io
import llm
import skills
import tools
from agent import Agent

ROOT = _bootstrap.PROJECT_ROOT

R = "\033[0m"
B = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
GRAY = "\033[90m"

THIN = "─" * 78
THICK = "═" * 78

MCP_ALLOW = {"list_allowed_directories", "list_directory", "read_text_file",
             "get_file_info", "search_files", "directory_tree"}

SCENARIOS = [
    ("场景 1 · 基础对话（流式 + 用量统计）",
     "用一句话解释什么是 MCP，以及它和普通函数调用的区别。", False),
    ("场景 2 · 本地工具（Function Calling）",
     "现在上海几点？顺便算一下 1250 元按年化 6% 复利 3 年后是多少。", False),
    ("场景 3 · Skill（渐进式披露）",
     "用 code-reviewer 这个 skill 审查一下 tools.py 里的 calculator 实现。", False),
    ("场景 4 · MCP（外部工具生态）",
     "通过 MCP 看看工作区里有哪些文件，然后读一下 .env.example 的内容。", True),
]


def head(title):
    print()
    print(THICK)
    print("{0}  {1}{2}".format(B, title, R))
    print(THICK)


def info(label, value):
    print("  {0}{1:<12}{2} {3}".format(GRAY, label, R, value))


def run_scenario(index, title, prompt, client, mcp_client):
    head(title)
    print("{0}用户 › {1}{2}".format(CYAN, prompt, R))
    print()

    agent = Agent(client, live=False)
    if mcp_client is not None:
        agent.attach_mcp(mcp_client, allow=MCP_ALLOW)

    started = time.perf_counter()
    answer = agent.run(prompt)
    elapsed = time.perf_counter() - started

    calls = [text for kind, text in agent.trace if kind == "call"]
    print(THIN)
    print(f"{GREEN}最终回答{R}")
    print(answer)
    print(THIN)
    print(f"{GRAY}工具调用 {len(calls)} 次，耗时 {elapsed:.2f}s{R}")
    return {"index": index, "calls": len(calls), "elapsed": elapsed}


def main():
    console_io.setup()

    parser = argparse.ArgumentParser(description="LLM 调用链路完整演示")
    parser.add_argument("--mock", action="store_true", help="用本地 mock 模型，不需要 API Key")
    parser.add_argument("--only", type=int, help="只跑第 N 个场景")
    parser.add_argument("--model", help="覆盖模型名")
    parser.add_argument("--no-mcp", action="store_true", help="跳过 MCP 场景")
    args = parser.parse_args()

    mock_server = None
    print()
    print(THICK)
    print("{0}  LLM 调用链路演示 · {1}{2}".format(B, "mock 模型（离线验证）" if args.mock else "真实模型",
                                                 R))
    print(THICK)

    if args.mock:
        from mock_llm import MockLLMServer
        mock_server = MockLLMServer().start()
        client = llm.LLMClient(api_key="sk-mock", base_url=mock_server.base_url, model="mock-model")
    else:
        client = llm.from_env(os.path.join(ROOT, ".env"), model=args.model)
        if not client.api_key:
            print("{0}没有找到 API Key。{1}".format(
                YELLOW,
                "请把 key 写进 .env 的 LLM_API_KEY=，或者加 --mock 用离线模式先验证链路。"))
            return 2

    info("Python", "{0}  ({1})".format(platform.python_version(), sys.executable))
    info("模型", client.model)
    info("接口", client.base_url)
    info("本地工具", ", ".join(tools.names()) or "无")
    info("Skill", ", ".join(skills.SKILLS) or "无")
    info("MCP", "未启用" if args.no_mcp else "filesystem server（按需启动）")

    # MCP server 只在场景 4 用得上，提前启动一次复用
    mcp_client = None
    need_mcp = not args.no_mcp
    if need_mcp and (args.only in (None, 4)):
        try:
            from mcp_client import filesystem_server
            mcp_client = filesystem_server(ROOT)
            mcp_client.start()
            info("MCP server", "{0} v{1}".format(
                mcp_client.server_info.get("name"), mcp_client.server_info.get("version")))
        except Exception as exc:
            print("{0}MCP 启动失败，跳过场景 4：{1}{2}".format(YELLOW, exc, R))
            mcp_client = None

    results = []
    try:
        for index, (title, prompt, wants_mcp) in enumerate(SCENARIOS, start=1):
            if args.only and index != args.only:
                continue
            if wants_mcp and mcp_client is None:
                print("{0}场景 4 需要 MCP，已跳过{1}".format(YELLOW, R))
                continue
            results.append(run_scenario(index, title, prompt, client,
                                        mcp_client if wants_mcp else None))
    finally:
        if mcp_client:
            mcp_client.close()
        client.close()
        if mock_server:
            mock_server.stop()

    print()
    print(THICK)
    print(f"{B}  汇总{R}")
    print(THICK)
    print("  {0:<6}{1:<12}{2:<10}{3}".format("场景", "工具调用", "耗时", "状态"))
    for item in results:
        print("  {0:<8}{1:<14}{2:<12}{3}".format(
            item["index"], item["calls"], "{0:.2f}s".format(item["elapsed"]),
            "OK" if item["calls"] or item["index"] == 1 else "-"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
