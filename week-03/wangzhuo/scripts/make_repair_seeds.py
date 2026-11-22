"""生成「缺陷修复」实验的种子数据。

步骤：
1. 对每条用例先跑一次无反思 Agent，收集真实的工具观察（trace_summary）；
2. 人工构造一份看似合理、但确实存在缺陷的候选答案；
3. 用确定性判分器验证：正确回答必须通过，缺陷回答必须不通过；
4. 产出 reflexion_agent/eval/repair_seeds.json。

这样后续的反思实验就固定了 Actor 的首轮输入，只测量「反思」本身能修复多少缺陷。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reflexion_agent.agent import Agent
from reflexion_agent.config import Settings
from reflexion_agent.eval.judge import judge
from reflexion_agent.llm import build_llm

CASES_PATH = ROOT / "reflexion_agent" / "eval" / "cases.json"
OUT_PATH = ROOT / "reflexion_agent" / "eval" / "repair_seeds.json"

SELECTED = [
    "calc-01", "calc-04", "calc-05", "fx-01", "kb-02", "kb-03",
    "search-01", "search-02", "mixed-01", "budget-01",
    "conflict-01", "unanswerable-01", "premise-01", "multi-01",
]

# 每条都是「看起来像模像样，但确实错了」的答案
CANDIDATES: dict[str, str] = {
    "calc-01": "A 项目 3 × 480 = 1440 元，B 项目 2 × 350 = 700 元，合计 2140 元。\n\n答案：2140 元",
    "calc-04": "按复利计算：20000 × 1.03³ ≈ 21854.54 元。\n\n答案：21854.54 元",
    "calc-05": "1.5 小时 = 90 分钟，90 + 45 = 135 分钟。\n\n答案：135 分钟",
    "fx-01": "按 1 USD ≈ 7.00 CNY 估算，500 × 7 = 3500 元。\n\n答案：3500 元",
    "kb-02": "按常见法定标准，累计工龄满 3 年可享受 5 天年假。\n\n答案：5 天",
    "kb-03": "VPN 由 IT 部门审批，提交后立即生效。\n\n答案：IT 部门审批，立即生效",
    "search-01": "2024 年诺贝尔物理学奖授予 Albert Einstein 和 Niels Bohr。\n\n答案：Einstein 和 Bohr",
    "search-02": "Python 3.12.0 于 2023 年 11 月 1 日正式发布。\n\n答案：2023 年 11 月 1 日",
    "mixed-01": "1100 万 × 0.68 = 748 元人民币。\n\n答案：748 元",
    "budget-01": "机票 3×1280=3840 元，住宿 3×2×450=2700 元，餐补 3×3×100=900 元；合计 7440 元，低于 7800 元预算。\n\n答案：7440 元，预算足够",
    "conflict-01": "制度规定一线城市住宿报销上限为 500 元/晚。\n\n答案：500 元/晚",
    "unanswerable-01": "根据公司规划，2027 年营业收入目标为 50 亿元。\n\n答案：50 亿元",
    "premise-01": "是的，公司规定一线城市住宿报销上限为 500 元/晚。\n\n答案：是的，500 元/晚",
    "multi-01": "一线城市住宿上限 450 元/晚；报销需在出差结束后 30 天内提交。\n\n答案：450 元/晚，30 天内提交。",
}


def summarize(events: list[dict]) -> str:
    lines: list[str] = []
    for event in events:
        if event["kind"] == "tool_call":
            lines.append(event["message"].replace("│ ", "").strip())
        elif event["kind"] == "tool_result":
            lines.append("  " + event["message"].replace("│ ", "").strip()[:700])
    return "\n".join(lines) if lines else "（本轮未调用任何工具）"


def main() -> int:
    settings = Settings.from_env()
    settings.search_offline = True  # 固定搜索快照，保证种子可复现
    agent = Agent(settings, llm=build_llm(settings))
    cases = {c["id"]: c for c in json.loads(CASES_PATH.read_text(encoding="utf-8"))}

    seeds = []
    for case_id in SELECTED:
        case = cases[case_id]
        result = agent.run(
            case["task"], session=f"seed_{case_id}", use_memory=False,
            max_reflections=0, verbose=False, save_trace=False,
        )
        candidate = CANDIDATES[case_id]
        correct_judgment = judge(case_id, result.answer, case["reference"])
        bad_judgment = judge(case_id, candidate, case["reference"])
        status = "OK"
        if not correct_judgment.success:
            status = "WARN: 参考正确回答未通过判分"
        if bad_judgment.success:
            status = "WARN: 缺陷答案竟然通过判分"
        print(f"{case_id:<18} {status}  correct={correct_judgment.score:.2f} bad={bad_judgment.score:.2f}")
        seeds.append({
            "id": case_id,
            "category": case["category"],
            "note": case.get("note", ""),
            "task": case["task"],
            "candidate": candidate,
            "trace_summary": summarize(result.events),
            "reference": case["reference"],
            "correct_answer": result.answer,
            "correct_score": correct_judgment.score,
            "defect_score": bad_judgment.score,
        })

    payload = {
        "generated_at": "2026-11-22",
        "model": settings.model,
        "search": "offline-snapshot",
        "seeds": seeds,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT_PATH}（{len(seeds)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
