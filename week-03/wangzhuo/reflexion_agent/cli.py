"""命令行入口。

    python -m reflexion_agent demo                 # 完整演示（含反思与工具调用）
    python -m reflexion_agent ask "问题"            # 单次问答
    python -m reflexion_agent eval                 # 消融实验
    python -m reflexion_agent memory --show        # 查看经验记忆
    python -m reflexion_agent demo --offline       # 无 Key 离线回放
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .agent import Agent
from .config import RESULTS_DIR, Settings
from .llm import RecordingClient, build_llm
from .reflection.memory import ReflectiveMemory

DEMO_TASK = (
    "请完成两件事：\n"
    "1. 查公司差旅标准，计算 3 人出差的总费用：往返机票每人 1280 元，"
    "一线城市住宿 2 晚（按报销上限），餐补 3 天。\n"
    "2. 把 500 美元按公司内部结算汇率换算成人民币。\n"
    "最后给出总费用和美元换算结果。"
)


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    if getattr(args, "offline", False):
        settings.offline = True
    if getattr(args, "model", None):
        settings.model = args.model
    settings.ensure_dirs()
    return settings


def _banner(settings: Settings) -> None:
    mode = "离线回放（无需 Key / 网络）" if settings.use_mock else f"在线 · {settings.model}"
    print("=" * 66)
    print(f" Reflexion Agent v{__version__}  |  {mode}")
    print(" 反思设计模式 + 真实工具调用（计算 / 搜索 / 内部 API / 汇率）")
    print("=" * 66)


def cmd_demo(args: argparse.Namespace) -> int:
    settings = _settings(args)
    _banner(settings)
    if settings.use_mock:
        print("提示：未检测到 LLM_API_KEY，使用仓库内置的真实运行录制进行回放。\n")

    llm = build_llm(settings)
    recorder: RecordingClient | None = None
    if args.record:
        recorder = RecordingClient(llm)
        llm = recorder

    agent = Agent(settings, llm=llm)
    task = args.task or DEMO_TASK
    result = agent.run(
        task,
        session="demo",
        use_memory=not args.no_memory,
        verbose=True,
        save_trace=True,
    )
    if recorder:
        recorder.flush()
        print(f"\n已录制到：{recorder.out_dir / 'demo.json'}")

    print(f"\n轨迹已保存：{result.trace_path}" if result.trace_path else "")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    settings = _settings(args)
    _banner(settings)
    agent = Agent(settings)
    result = agent.run(
        args.question,
        session="ask",
        use_memory=not args.no_memory,
        verbose=True,
        save_trace=False,
    )
    if not result.answer:
        print("未得到答案。")
        return 1
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.run_eval import main as eval_main

    forwarded = ["--config", args.config]
    if args.limit:
        forwarded += ["--limit", str(args.limit)]
    if args.ids:
        forwarded += ["--ids", args.ids]
    if args.record:
        forwarded.append("--record")
    if args.offline:
        forwarded.append("--offline")
    if args.traces:
        forwarded.append("--traces")
    return eval_main(forwarded) or 0


def cmd_memory(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    memory = ReflectiveMemory(settings.memory_path)
    if args.clear:
        memory.clear()
        print(f"已清空经验记忆：{settings.memory_path}")
        return 0
    print(f"经验记忆：{len(memory)} 条（{settings.memory_path}）")
    for i, episode in enumerate(memory.episodes, 1):
        print(f"{i}. 任务：{episode.task}")
        print(f"   教训：{episode.lesson}")
        print(f"   评分：{episode.score_before:.2f} → {episode.score_after:.2f} | {episode.created_at}")
    if not memory.episodes:
        print("（暂无。运行 demo 或 eval 后会自动沉淀。）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reflexion-agent",
        description="带反思设计模式的工具调用 Agent",
    )
    parser.add_argument("--version", action="version", version=f"reflexion-agent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="运行完整演示")
    demo.add_argument("--task", help="自定义任务")
    demo.add_argument("--offline", action="store_true", help="强制离线回放")
    demo.add_argument("--record", action="store_true", help="录制真实模型响应（供离线回放）")
    demo.add_argument("--no-memory", action="store_true", help="不使用经验记忆")
    demo.add_argument("--model", help="覆盖模型名")
    demo.set_defaults(func=cmd_demo)

    ask = sub.add_parser("ask", help="单次问答")
    ask.add_argument("question", help="要问的问题")
    ask.add_argument("--offline", action="store_true")
    ask.add_argument("--no-memory", action="store_true")
    ask.add_argument("--model")
    ask.set_defaults(func=cmd_ask)

    ev = sub.add_parser("eval", help="运行消融实验")
    ev.add_argument("--config", default="all", choices=["all", "no_reflection", "one_shot", "full"])
    ev.add_argument("--limit", type=int, default=0)
    ev.add_argument("--ids", default="", help="只跑指定用例 ID，逗号分隔")
    ev.add_argument("--record", action="store_true")
    ev.add_argument("--offline", action="store_true")
    ev.add_argument("--traces", action="store_true")
    ev.set_defaults(func=cmd_eval)

    mem = sub.add_parser("memory", help="查看/清空经验记忆")
    mem.add_argument("--show", action="store_true", help="显示全部记忆（默认行为）")
    mem.add_argument("--clear", action="store_true", help="清空记忆")
    mem.set_defaults(func=cmd_memory)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
