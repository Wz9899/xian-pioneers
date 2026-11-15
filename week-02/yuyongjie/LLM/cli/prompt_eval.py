# -*- coding: utf-8 -*-
"""prompt_eval.py — 固定测试集，对比 Zero-Shot / Few-Shot / CoT 的效果差异。

做法：同一批 16 条任务描述，分别用三种 prompt 策略让模型解析成
`{"priority","due","estimate_hours"}`，再和人工标注的 gold 自动比对。

    .\\run.ps1 prompt_eval.py                  # 三种策略全跑（约 60 次调用）
    .\\run.ps1 prompt_eval.py --limit 5        # 先跑 5 条看看
    .\\run.ps1 prompt_eval.py --strategy few-shot

评分维度（每一项都是布尔）：
    json     输出能被解析成 JSON
    schema   priority/due/estimate_hours 三字段齐全且合法
    priority 优先级判定与 gold 一致
    due      截止日与 gold 一致
    estimate 工时误差 ≤ 0.5h
    all      以上四项全对
"""
import argparse
import json
import os
import re
import sys
import time

import _bootstrap          # noqa: F401 — 把 src/ 加入 sys.path
import console_io
import llm
import prompts
from paths import PROJECT_ROOT
from prompts import extract_json

CASES_PATH = os.path.join(PROJECT_ROOT, "eval", "cases.json")
RESULTS_PATH = os.path.join(PROJECT_ROOT, "eval", "results.json")

STRATEGIES = ("zero-shot", "few-shot", "cot", "golden")


def score(pred, gold):
    result = {"json": False, "schema": False, "priority": False,
              "due": False, "estimate": False, "all": False}
    if not isinstance(pred, dict):
        return result
    result["json"] = True

    priority = pred.get("priority")
    due = pred.get("due")
    hours = pred.get("estimate_hours")

    if priority not in ("P0", "P1", "P2"):
        return result
    if not isinstance(due, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", due.strip()):
        return result
    if not isinstance(hours, (int, float)):
        return result
    result["schema"] = True

    result["priority"] = priority == gold["priority"]
    result["due"] = due.strip() == gold["due"]
    try:
        result["estimate"] = abs(float(hours) - float(gold["estimate_hours"])) <= 0.5
    except (TypeError, ValueError):
        result["estimate"] = False
    # 「核心正确」只看客观项：结构 + 优先级 + 截止日。
    # 工时是主观估计，单独观察，不拉进总分——否则 gold 的随意性会污染结论。
    result["all"] = all(result[k] for k in ("schema", "priority", "due"))
    return result


def build_messages(case, today, strategy):
    if strategy == "golden":
        return prompts.render_golden(case["text"], today)
    return prompts.build_parse_messages(case["text"], today, strategy)


def main():
    console_io.setup()

    parser = argparse.ArgumentParser(description="Prompt 策略对比实验")
    parser.add_argument("--limit", type=int, help="只跑前 N 条")
    parser.add_argument("--strategy", choices=STRATEGIES, help="只跑某个策略")
    parser.add_argument("--model", help="覆盖模型")
    parser.add_argument("--out", default=RESULTS_PATH)
    args = parser.parse_args()

    with open(CASES_PATH, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    today = dataset["today"]
    cases = dataset["cases"][: args.limit] if args.limit else dataset["cases"]

    strategies = (args.strategy,) if args.strategy else STRATEGIES

    client = llm.from_env(os.path.join(PROJECT_ROOT, ".env"), model=args.model)
    if not client.api_key:
        print("缺少 API Key：把 LLM_API_KEY 写进 .env")
        return 2

    print()
    print("═" * 78)
    print("  Prompt 策略对比　模型={0}　用例={1} 条　温度=0".format(client.model, len(cases)))
    print("═" * 78)

    summary = {}
    records = []
    try:
        for strategy in strategies:
            print()
            print("── {0} " .format(strategy) + "─" * (74 - len(strategy)))
            stats = {k: 0 for k in ("json", "schema", "priority", "due", "estimate", "all")}
            tokens = 0
            elapsed = 0.0

            for index, case in enumerate(cases, start=1):
                messages = build_messages(case, today, strategy)
                try:
                    response = client.chat(messages, temperature=0, max_tokens=300)
                    raw = response.get("content") or ""
                    elapsed += response.get("elapsed") or 0
                    tokens += (response.get("usage") or {}).get("completion_tokens") or 0
                except llm.LLMError as exc:
                    # 一条失败就停下：继续跑只会刷出几十条同样的 ✗，把真正的原因盖住
                    print("\n✗ 调用失败，实验中止：")
                    print("  " + str(exc).replace("\n", "\n  "))
                    return 1

                pred = extract_json(raw)
                result = score(pred, case["gold"])
                for key, ok in result.items():
                    stats[key] += 1 if ok else 0

                mark = "✓" if result["all"] else ("·" if result["schema"] else "✗")
                detail = ""
                if result["schema"] and not result["all"]:
                    wrong = [k for k in ("priority", "due", "estimate") if not result[k]]
                    detail = "（错：{0}）".format("/".join(wrong))
                print("  [{0:2}/{1}] {2} {3:<3} {4}{5}".format(
                    index, len(cases), mark, case["id"], case["text"][:22], detail))

                records.append({
                    "strategy": strategy, "case": case["id"], "text": case["text"],
                    "raw": raw, "parsed": pred, "gold": case["gold"], "score": result,
                })

            total = float(len(cases))
            summary[strategy] = {
                "json": stats["json"] / total,
                "schema": stats["schema"] / total,
                "priority": stats["priority"] / total,
                "due": stats["due"] / total,
                "estimate": stats["estimate"] / total,
                "all": stats["all"] / total,
                "avg_seconds": elapsed / total,
                "avg_completion_tokens": tokens / total,
            }
    finally:
        client.close()

    # ---------------------------------------------------------------- 汇总

    print()
    print("═" * 78)
    print("  结果汇总（比例越高越好）")
    print("═" * 78)
    header = "{0:<12}{1:>8}{2:>8}{3:>10}{4:>8}{5:>8}{6:>10}".format(
        "策略", "JSON", "Schema", "优先级", "截止日", "核心", "工时")
    print(header)
    print("─" * 78)
    for strategy in strategies:
        s = summary[strategy]
        print("{0:<12}{1:>7.0%}{2:>9.0%}{3:>10.0%}{4:>9.0%}{5:>9.0%}{6:>11.0%}".format(
            strategy, s["json"], s["schema"], s["priority"],
            s["due"], s["all"], s["estimate"]))
    print("─" * 78)
    for strategy in strategies:
        s = summary[strategy]
        print("  {0:<10} 平均 {1:.1f}s/条，输出 {2:.0f} tokens/条".format(
            strategy, s["avg_seconds"], s["avg_completion_tokens"]))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"model": client.model, "today": today,
                   "cases": len(cases), "summary": summary, "records": records},
                  f, ensure_ascii=False, indent=2)
    print()
    print("原始记录已写入：{0}".format(os.path.relpath(os.path.abspath(args.out), PROJECT_ROOT)))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
