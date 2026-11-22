"""缺陷修复实验：隔离测量「反思」本身的价值。

端到端实验中，能力较强的模型往往首轮就做对，出现天花板效应。
本实验固定 Actor 的首轮输出为一份「已注入缺陷」的答案，只让反思模块决定
是否检出、是否修订，因此能直接比较不同反思策略的修复能力。

三组配置：
* no_repair：保留缺陷答案（下界）；
* one_shot ：评审一次 + 最多一次修订；
* full     ：最多两次反思 + 跨任务经验记忆。

用法：
    python -m reflexion_agent.eval.run_repair
    python -m reflexion_agent.eval.run_repair --record   # 录制离线回放
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

SEEDS_PATH = Path(__file__).with_name("repair_seeds.json")

CONFIGS: dict[str, dict[str, Any]] = {
    "no_repair": {"label": "无反思（保留缺陷）", "max_reflections": 0, "use_memory": False},
    "one_shot": {"label": "单轮反思", "max_reflections": 1, "use_memory": False},
    "full": {"label": "完整反思+记忆", "max_reflections": 2, "use_memory": True},
}


def _mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 3) if values else 0.0


def run_config(name: str, cfg: dict[str, Any], seeds: list[dict], settings: Settings,
               llm, save_traces: bool) -> dict[str, Any]:
    memory_path = RESULTS_DIR / "repair_memory" / f"{name}.json"
    memory = ReflectiveMemory(memory_path)
    memory.clear()
    agent = Agent(settings, llm=llm, memory=memory)

    records: list[dict[str, Any]] = []
    print(f"\n=== {cfg['label']}（{name}） ===")
    for seed in seeds:
        seed_id = seed["id"]
        if name == "no_repair":
            answer = seed["candidate"]
            judgment = judge(seed_id, answer, seed["reference"])
            record_agent = {"reflections": 0, "tool_calls": 0, "llm_calls": 0,
                            "total_tokens": 0, "latency": 0.0, "critiques": [],
                            "memory_hit": False, "memory_written": False, "score": None}
            detected = None
        else:
            result = agent.repair(
                seed["task"],
                seed["candidate"],
                seed["trace_summary"],
                session=f"repair_{name}_{seed_id}",
                use_memory=cfg["use_memory"],
                max_reflections=cfg["max_reflections"],
                verbose=False,
                save_trace=save_traces,
                record_memory=cfg["use_memory"],
            )
            answer = result.answer
            judgment = judge(seed_id, answer, seed["reference"])
            record_agent = result.to_dict(include_events=False)
            first = result.critiques[0] if result.critiques else {}
            detected = bool(first.get("verdict") != "pass" or first.get("issues"))

        mark = "✓" if judgment.success else "✗"
        print(
            f"  {mark} {seed_id:<18} judge={judgment.score:.2f} "
            f"detected={'-' if detected is None else int(detected)} "
            f"refl={record_agent['reflections']} tokens={record_agent['total_tokens']}"
        )
        records.append({
            "id": seed_id,
            "category": seed["category"],
            "task": seed["task"],
            "candidate": seed["candidate"],
            "answer": answer,
            "judge": judgment.to_dict(),
            "success": judgment.success,
            "detected": detected,
            "agent": record_agent,
        })

    categories = sorted({r["category"] for r in records})
    per_category = {}
    for cat in categories:
        items = [r for r in records if r["category"] == cat]
        per_category[cat] = {
            "cases": len(items),
            "repaired": sum(1 for r in items if r["success"]),
            "repair_rate": round(sum(1 for r in items if r["success"]) / len(items), 4),
        }

    detected_items = [r for r in records if r["detected"]]
    summary = {
        "cases": len(records),
        "repaired": sum(1 for r in records if r["success"]),
        "repair_rate": round(sum(1 for r in records if r["success"]) / len(records), 4) if records else 0.0,
        "avg_score": _mean([r["judge"]["score"] for r in records]),
        "detection_rate": round(len(detected_items) / len(records), 4) if name != "no_repair" else None,
        "avg_reflections": _mean([r["agent"]["reflections"] for r in records]),
        "avg_tokens": _mean([r["agent"]["total_tokens"] for r in records]),
        "avg_latency": _mean([r["agent"]["latency"] for r in records]),
        "total_tokens": sum(r["agent"]["total_tokens"] for r in records),
        "memory_written": sum(1 for r in records if r["agent"].get("memory_written")),
    }
    return {"label": cfg["label"], "summary": summary,
            "per_category": per_category, "cases": records}


def render_markdown(results: dict[str, Any]) -> str:
    lines = [
        "| 配置 | 修复率 | 平均判定分 | 首轮检出率 | 平均修订轮数 | 平均 tokens | 平均耗时 |",
        "|---|---|---|---|---|---|---|",
    ]
    for _, data in results["configs"].items():
        s = data["summary"]
        detect = "—" if s["detection_rate"] is None else f"{s['detection_rate'] * 100:.1f}%"
        lines.append(
            f"| {data['label']} | {s['repaired']}/{s['cases']}（{s['repair_rate'] * 100:.1f}%） "
            f"| {s['avg_score']:.3f} | {detect} | {s['avg_reflections']:.2f} "
            f"| {s['avg_tokens']:.0f} | {s['avg_latency']:.2f}s |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="all", choices=["all", *CONFIGS])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--traces", action="store_true")
    parser.add_argument("--output", default=str(RESULTS_DIR / "repair_results.json"))
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.offline:
        settings.offline = True
    if args.record and settings.use_mock:
        parser.error("--record 需要真实 API Key（LLM_API_KEY）")

    payload = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    seeds = payload["seeds"]
    if args.limit:
        seeds = seeds[: args.limit]
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
            "seeds": len(seeds),
            "seed_model": payload.get("model"),
        },
        "configs": {},
    }

    for name in names:
        results["configs"][name] = run_config(
            name, CONFIGS[name], seeds, settings, llm, save_traces=args.traces
        )

    if recorder:
        recorder.flush()
        print(f"\n已录制离线回放：{recorder.out_dir}")

    results["meta"]["elapsed"] = round(time.time() - started, 2)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown = render_markdown(results)
    (output.parent / "repair_summary.md").write_text(markdown + "\n", encoding="utf-8")
    print("\n=== 汇总 ===")
    print(markdown)
    print(f"\n结果已保存：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
