# -*- coding: utf-8 -*-
"""feishu_demo.py — 把「调用 API」做成飞书推送的完整展示。

    .\\.venv\\Scripts\\python.exe cli\\feishu_demo.py            # 用本地假 webhook 演示（无需飞书账号）
    .\\.venv\\Scripts\\python.exe cli\\feishu_demo.py --no-agent # 不走模型，直接渲染清单并推送
    .\\.venv\\Scripts\\python.exe cli\\feishu_demo.py --real     # 用环境变量里的真实 webhook

三种模式都走同一条发送路径（tools_feishu.feishu_notify），
差别只在 webhook 地址指向本地假服务还是真飞书。
"""
import argparse
import os
import sys
import time

import _bootstrap          # noqa: F401 — 把 src/ 加入 sys.path
import console_io
import llm
import taskdata
import tools_feishu
from agent import Agent
from paths import PROJECT_ROOT

sys.path.insert(0, os.path.join(PROJECT_ROOT, "dev"))

PROMPT = (
    "把今天还没完成的任务整理成一条飞书卡片发出去。要求：\n"
    "1. 先用 my_checklist 拿到真实清单，不要自己编；\n"
    "2. 标题写「本周待办 · {date}」；\n"
    "3. 正文用 markdown 列表，每条一行：编号、标题、优先级、截止时间；\n"
    "4. 逾期的最多列 5 条，标出「逾期」；\n"
    "5. 发出去之后告诉我发了几条。"
)


def pending_markdown(tasks, limit=5):
    buckets = taskdata.classify(tasks)
    lines = []
    for task in (buckets["overdue"] + buckets["today"] + buckets["upcoming"])[:limit]:
        lines.append("- **{0}** {1}　`{2}`　截止 {3}".format(
            task["id"], task["title"], task["priority"],
            taskdata.fmt_due(task["due_dt"])))
    return "\n".join(lines) or "- 今天没有待办"


def main():
    console_io.setup()

    parser = argparse.ArgumentParser(description="飞书推送演示")
    parser.add_argument("--no-agent", action="store_true", help="不走模型，确定性渲染")
    parser.add_argument("--real", action="store_true", help="使用环境变量里的真实 webhook")
    parser.add_argument("--model", help="覆盖模型")
    args = parser.parse_args()

    server = None
    if not args.real:
        from feishu_mock import FeishuMockServer
        server = FeishuMockServer().start()
        os.environ["FEISHU_WEBHOOK"] = server.webhook
        time.sleep(0.2)

    print()
    print("═" * 78)
    print("  飞书推送演示　{0}".format("真实 webhook" if args.real else "本地假 webhook（无需账号）"))
    print("═" * 78)
    print("  webhook：{0}".format(os.environ.get("FEISHU_WEBHOOK", "（未配置）")))

    try:
        if args.no_agent:
            tasks, errors, _ = taskdata.load_tasks()
            if errors:
                print("任务数据有误，先修数据。")
                return 1
            title = "本周待办 · {0}".format(taskdata.now_local().strftime("%m-%d"))
            content = pending_markdown(tasks)
            print()
            print("  标题：{0}".format(title))
            print("  正文：\n{0}".format(content))
            print()
            print("  → {0}".format(tools_feishu.feishu_notify(title, content)))
            return 0

        client = llm.from_env(os.path.join(PROJECT_ROOT, ".env"), model=args.model)
        if not client.api_key:
            print("缺少 API Key：把 LLM_API_KEY 写进 .env")
            return 2

        agent = Agent(client, max_steps=6, live=False)
        prompt = PROMPT.format(date=taskdata.now_local().strftime("%m-%d"))
        print()
        print("  用户 › {0}".format(prompt.replace("\n", " ")))
        print()

        try:
            answer = agent.run(prompt)
        finally:
            client.close()

        print()
        print("─" * 78)
        print("最终回答：")
        print(answer)
        print("─" * 78)
        return 0
    finally:
        if server:
            server.stop()


if __name__ == "__main__":
    sys.exit(main())
