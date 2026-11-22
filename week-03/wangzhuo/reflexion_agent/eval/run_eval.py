"""消融实验：同一批用例、同一个模型，只改变反思策略。

三组配置：
* no_reflection：只做一次 ReAct，不反思（基线）；
* one_shot      ：最多 1 次「评审 → 修订」；
* full          ：最多 2 次反思 + 跨任务经验记忆 + 边际收益提前停止。

运行：
    python -m reflexion_agent.eval.run_eval --config all
    python -m reflexion_agent.eval.run_eval --config all --record   # 录制离线回放
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from ..agent import Agent
from ..config import RESULTS_DIR, Settings
from ..llm import RecordingClient, build_llm
from ..reflection.memory import ReflectiveMemory
from .judge import judge

CASES_PATH = Path(__file__).with_name("cases.json")

CONFIGS: dict[str, dict[str, Any]] = {
    "no_reflection": {"label": "无反思", "max_reflections": 0, "use_memory": False},
    "one_shot": {"label": "单轮反思", "max_reflections": 1, "use_memory": False},
    "full": {"label": "完整反思+记忆", "max_reflections": 2, "use_memory": True},
}


def _mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 3) if values else 0.0


def _load_cases(limit: int | None = None) -> list[dict]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return cases[:limit] if limit else cases


def run_config(
    name: str,
    cfg: dict[str, Any],
    cases: list[dict],
    settings: Settings,
    llm,
    save_traces: bool = False,
) -> dict[str, Any]:
    memory_path = RESULTS_DIR / "eval_memory" / f"{name}.json"
    memory = ReflectiveMemory(memory_path)
    memory.clear()
    agent = Agent(settings, llm=llm, memory=memory)

    records: list[dict[str, Any]] = []
    print(f"\n=== {cfg['label']}（{name}） ===")
    for case in cases:
        case_id = case["id"]
        session = f"eval_{name}_{case_id}"
        result = agent.run(
            case["task"],
            session=session,
            use_memory=cfg["use_memory"],
            max_reflections=cfg["max_reflections"],
            verbose=False,
            save_trace=save_traces,
            record_memory=cfg["use_memory"],
        )
        judgment = judge(case_id, result.answer, case["reference"])
        mark = "✓" if judgment.success else "✗"
        print(
            f"  {mark} {case_id:<16} judge={judgment.score:.2f} "
            f"refl={result.reflections} tools={result.tool_calls} tokens={result.total_tokens}"
            + (f"  answer={result.answer[:60]!r}" if not judgment.success else "")
        )
        record = {
            "id": case_id,
            "category": case["category"],
            "note": case.get("note", ""),
            "task": case["task"],
            "answer": result.answer,
            "judge": judgment.to_dict(),
            "success": judgment.success,
            "agent": result.to_dict(include_events=False),
        }
        records.append(record)

    successes = [r for r in records if r["success"]]
    categories = sorted({r["category"] for r in records})
    per_category = {}
    for cat in categories:
        items = [r for r in records if r["category"] == cat]
        per_category[cat] = {
            "cases": len(items),
            "success": sum(1 for r in items if r["success"]),
            "success_rate": round(sum(1 for r in items if r["success"]) / len(items), 4),
            "avg_score": _mean([r["judge"]["score"] for r in items]),
        }

    summary = {
        "cases": len(records),
        "success": len(successes),
        "success_rate": round(len(successes) / len(records), 4) if records else 0.0,
        "avg_score": _mean([r["judge"]["score"] for r in records]),
        "avg_reflections": _mean([r["agent"]["reflections"] for r in records]),
        "avg_tool_calls": _mean([r["agent"]["tool_calls"] for r in records]),
        "avg_llm_calls": _mean([r["agent"]["llm_calls"] for r in records]),
        "avg_tokens": _mean([r["agent"]["total_tokens"] for r in records]),
        "total_tokens": sum(r["agent"]["total_tokens"] for r in records),
        "avg_latency": _mean([r["agent"]["latency"] for r in records]),
        "memory_written": sum(1 for r in records if r["agent"]["memory_written"]),
        "memory_hits": sum(1 for r in records if r["agent"]["memory_hit"]),
    }
    return {"label": cfg["label"], "summary": summary,
            "per_category": per_category, "cases": records}


def render_markdown(results: dict[str, Any]) -> str:
    lines = [
        "| 配置 | 成功率 | 平均判定分 | 平均反思轮数 | 平均工具调用 | 平均 LLM 调用 | 平均 tokens | 平均耗时 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, data in results["configs"].items():
        s = data["summary"]
        lines.append(
            f"| {data['label']} | {s['success']}/{s['cases']}（{s['success_rate'] * 100:.1f}%） "
            f"| {s['avg_score']:.3f} | {s['avg_reflections']:.2f} | {s['avg_tool_calls']:.2f} "
            f"| {s['avg_llm_calls']:.2f} | {s['avg_tokens']:.0f} | {s['avg_latency']:.2f}s |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="all", choices=["all", *CONFIGS])
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条，便于冒烟测试")
    parser.add_argument("--ids", default="", help="只跑指定用例 ID，逗号分隔")
    parser.add_argument("--record", action="store_true", help="把真实模型响应录制到 recordings/")
    parser.add_argument("--offline", action="store_true", help="强制离线回放")
    parser.add_argument("--traces", action="store_true", help="保存每个用例的 JSONL 轨迹")
    parser.add_argument("--output", default=str(RESULTS_DIR / "eval_results.json"))
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.offline:
        settings.offline = True
    if args.record and settings.use_mock:
        parser.error("--record 需要真实 API Key（LLM_API_KEY）")

    cases = _load_cases(args.limit or None)
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        cases = [c for c in cases if c["id"] in wanted]
    names = list(CONFIGS) if args.config == "all" else [args.config]

    llm = build_llm(settings)
    recorder: RecordingClient | None = None
    if args.record:
        recorder = RecordingClient(llm)
        llm = recorder

    started = time.time()
    results: dict[str, Any] = {
        "meta": {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": settings.model if not settings.use_mock else "offline-replay",
            "mode": "offline" if settings.use_mock else "online",
            "threshold": settings.reflection_threshold,
            "max_steps": settings.max_steps,
            "cases": len(cases),
        },
        "configs": {},
    }

    for name in names:
        results["configs"][name] = run_config(
            name, CONFIGS[name], cases, settings, llm, save_traces=args.traces
        )

    if recorder:
        recorder.flush()
        print(f"\n已录制离线回放：{recorder.out_dir}")

    results["meta"]["elapsed"] = round(time.time() - started, 2)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown = render_markdown(results)
    (output.parent / "eval_summary.md").write_text(markdown + "\n", encoding="utf-8")

    print("\n=== 汇总 ===")
    print(markdown)
    print(f"\n结果已保存：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
