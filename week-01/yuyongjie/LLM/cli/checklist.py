# -*- coding: utf-8 -*-
"""checklist.py — 任务数据的调试与查看入口。

    .\\run.ps1 checklist.py --check              # 只校验数据，不调模型
    .\\run.ps1 checklist.py                      # 渲染清单
    .\\run.ps1 checklist.py --today              # 今天要做什么（含逾期）
    .\\run.ps1 checklist.py --reminders          # 未来 7 天的提醒计划
    .\\run.ps1 checklist.py --at "2026-11-10 08:00"   # 固定“现在”，复现逾期判定
    .\\run.ps1 checklist.py --file data/tasks_dirty.json --check   # 用脏数据看校验器
    .\\run.ps1 checklist.py --ask "我今天该先做哪件事"              # 交给模型判断

关键设计：所有涉及「现在」的逻辑都从参数注入，所以逾期/临期这些判定可以复现，
不依赖你运行的那一秒。
"""
import argparse
import os
import sys

import _bootstrap          # noqa: F401 — 把 src/ 加入 sys.path
import console_io
import taskdata

ROOT = _bootstrap.PROJECT_ROOT


def section(title):
    print()
    print("─" * 74)
    print("  " + title)
    print("─" * 74)


def report(tasks, errors, warnings):
    section("数据校验")
    print("  任务数：{0}".format(len(tasks)))
    status = {}
    for task in tasks:
        status[task["status"]] = status.get(task["status"], 0) + 1
    if status:
        print("  状态分布：" + "　".join(
            "{0}={1}".format(k, v) for k, v in sorted(status.items())))

    if errors:
        print()
        print("  错误 {0} 条（必须修）：".format(len(errors)))
        for item in errors:
            print("    ✗ " + item)
    else:
        print("  ✓ 没有错误")

    if warnings:
        print()
        print("  提醒 {0} 条（不阻塞）：".format(len(warnings)))
        for item in warnings:
            print("    ! " + item)


def main():
    console_io.setup()

    parser = argparse.ArgumentParser(description="任务数据调试")
    parser.add_argument("--file", help="任务数据文件，默认 data/tasks.json")
    parser.add_argument("--check", action="store_true", help="只做数据校验")
    parser.add_argument("--today", action="store_true", help="只看今天（含逾期）")
    parser.add_argument("--reminders", action="store_true", help="看未来几天的提醒计划")
    parser.add_argument("--days", type=int, default=7, help="提醒窗口天数，默认 7")
    parser.add_argument("--at", metavar="DATETIME", help="固定“现在”，如 2026-11-10 08:00")
    parser.add_argument("--ask", metavar="QUESTION", help="把问题交给模型，用任务工具回答")
    parser.add_argument("--model", help="覆盖模型名")
    args = parser.parse_args()

    try:
        now = taskdata.parse_dt(args.at) if args.at else taskdata.now_local()
    except ValueError as exc:
        print("--at 参数有误：{0}".format(exc))
        return 2

    tasks, errors, warnings = taskdata.load_tasks(args.file)

    # --ask：交给模型，先确认数据是好的
    if args.ask:
        if errors:
            report(tasks, errors, warnings)
            print()
            print("数据有错误，先修数据再问模型。")
            return 1
        return ask(args)

    if args.check:
        report(tasks, errors, warnings)
        print()
        return 1 if errors else 0

    if errors:
        report(tasks, errors, warnings)
        print()
        print("数据有错误，无法渲染。先跑 --check 看明细，或用 --file 换一份数据。")
        return 1

    print("现在：{0}（周{1}）".format(
        now.strftime("%Y-%m-%d %H:%M"), taskdata.WEEKDAYS[now.weekday()]))

    if args.reminders:
        items = taskdata.upcoming_reminders(tasks, now, days=args.days)
        section("未来 {0} 天的提醒计划".format(args.days))
        if not items:
            print("  没有需要提醒的事项。")
        for item in items:
            print("  {0}　{1}　{2}　（{3}，截止 {4}）".format(
                item["remind_at"], item["task_id"], item["title"],
                item["kind"], item["due"]))
        print()
        return 0

    if args.today:
        buckets = taskdata.classify(tasks, now)
        section("今天要做的事")
        focus = buckets["overdue"] + buckets["today"]
        if not focus:
            print("  今天没有到期或逾期的任务。")
        for task in focus:
            flag = "逾期" if task in buckets["overdue"] else "今天"
            print("  [{0}] {1} {2}（{3}，截止 {4}）".format(
                flag, task["id"], task["title"], task["priority"],
                task["due_dt"].strftime("%m-%d %H:%M")))
        print()
        return 0

    print(taskdata.render_checklist(tasks, now))
    return 0


def ask(args):
    """用任务工具让模型回答——复用 agent.py 的循环，不额外写调度逻辑。"""
    import llm
    from agent import Agent

    client = llm.from_env(os.path.join(ROOT, ".env"), model=args.model)
    if not client.api_key:
        print("缺少 API Key：把 LLM_API_KEY 写进 .env")
        return 2

    agent = Agent(client, max_steps=6, live=False)
    # 追加到既有 system prompt，而不是塞第二条 system（部分厂商对多 system 不友好）
    agent.messages[0]["content"] += (
        "\n\n你可以调用任务工具查看我的任务数据。回答时先给结论（先做哪件事、为什么），"
        "再列依据；不要编造数据里没有的任务。"
    )

    try:
        print("═" * 74)
        print("  提问：" + args.ask)
        print("═" * 74)
        answer = agent.run(args.ask)
        print()
        print(answer)
        print()
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
