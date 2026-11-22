# -*- coding: utf-8 -*-
"""tool_demo.py — 第三周作业：给 LLM 接真实工具（搜索 + 内部 API）。

    .\\run.ps1 tool_demo.py            # 或 .\\.venv\\Scripts\\python.exe cli\\tool_demo.py
    .\\run.ps1 tool_demo.py --no-api   # 只演示搜索

它会：
1. 在进程内启动「内部任务服务」（dev/task_api.py，端口 8765）；
2. 把 web_search / internal_task_api 两个真实工具交给模型；
3. 让模型自己决定调哪个，并打印完整调用链；
4. 结束后校验：内部系统的写入是否真的落到了 data/tasks.json。
"""
import argparse
import os
import sys
import time

import _bootstrap          # noqa: F401 — 把 src/ 加入 sys.path
import console_io
import llm
import taskdata
import tools_api          # noqa: F401 — import 即注册 internal_task_api 工具
import tools_web          # noqa: F401 — import 即注册 web_search 工具
from agent import Agent
from paths import PROJECT_ROOT

sys.path.insert(0, os.path.join(PROJECT_ROOT, "dev"))

DEMOS = [
    ("真实工具 0：计算器（本地对照）",
     "1250 元按年化 6% 复利 3 年，最后是多少？保留两位小数。"),
    ("真实工具 1：联网搜索",
     "帮我搜一下 Model Context Protocol 是什么，用两句话总结，并给出你引用的链接。"),
    ("真实工具 2：内部 HTTP 服务",
     "看看内部任务服务里现在还有哪些待办任务，然后告诉我最该先做哪一件。"),
    ("真实工具 3：写入内部系统",
     "在内部系统里把 T9 标记为完成，然后告诉我当前统计。"),
]


def main():
    console_io.setup()

    parser = argparse.ArgumentParser(description="真实工具演示")
    parser.add_argument("--no-api", action="store_true", help="不启动内部服务，只演示搜索")
    parser.add_argument("--only", type=int, help="只跑第 N 个演示")
    parser.add_argument("--model", help="覆盖模型")
    args = parser.parse_args()

    client = llm.from_env(os.path.join(PROJECT_ROOT, ".env"), model=args.model)
    if not client.api_key:
        print("缺少 API Key：把 LLM_API_KEY 写进 .env")
        return 2

    server = None
    if not args.no_api:
        from task_api import TaskAPIServer
        server = TaskAPIServer().start()
        time.sleep(0.3)

    print()
    print("═" * 78)
    print("  真实工具演示　模型={0}".format(client.model))
    print("═" * 78)
    print("  联网搜索　：Bing RSS（https://www.bing.com/search?format=rss）")
    if server:
        print("  内部任务服务：{0}（鉴权头 X-Api-Token）".format(server.base_url))
    else:
        print("  内部任务服务：未启动（--no-api）")

    before = taskdata.load_tasks()[0]

    agent = Agent(client, max_steps=8, live=False)
    try:
        for index, (title, prompt) in enumerate(DEMOS, start=1):
            if args.only and index != args.only:
                continue
            if args.no_api and index >= 3:      # 3、4 号演示依赖内部服务
                continue

            print()
            print("─" * 78)
            print("  {0}".format(title))
            print("─" * 78)
            print("用户 › {0}".format(prompt))
            print()

            started = time.perf_counter()
            answer = agent.run(prompt)
            elapsed = time.perf_counter() - started

            print()
            print("最终回答：")
            print(answer)
            print()
            print("（耗时 {0:.1f}s）".format(elapsed))
            agent.messages.append({"role": "assistant", "content": answer})
    finally:
        client.close()
        if server:
            server.stop()

    # 校验写入是否真的生效
    if not args.no_api and (args.only in (None, 4)):
        after = taskdata.load_tasks()[0]
        target = next((t for t in after if t["id"] == "T9"), None)
        print()
        print("─" * 78)
        print("  写入校验")
        print("─" * 78)
        if target and target["status"] == "done":
            print("  ✓ data/tasks.json 里 T9 已是 done，内部系统的写入真实落盘")
        else:
            print("  ✗ T9 状态仍为 {0}，写入未生效".format(
                target["status"] if target else "（任务不存在）"))
        print("  任务数：{0} → {1}".format(len(before), len(after)))

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
