"""开发期探针：快速检查候选用例在「无反思」下是否会失败。

用途：设计消融实验时，先验证题目确实有区分度，避免所有配置都满分。
不属于运行时依赖。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reflexion_agent.agent import Agent
from reflexion_agent.config import Settings
from reflexion_agent.eval.judge import judge
from reflexion_agent.llm import build_llm

CASES = [
    {
        "id": "false-premise-fee",
        "task": "公司餐补标准是 120 元/天。请计算 3 人出差 3 天的餐补总额。",
        "reference": {"numbers": [{"value": 900, "tol": 0.5}], "required_groups": [["900"], ["100"]]},
    },
    {
        "id": "six-parts",
        "task": "请根据公司制度回答：(1) 一线城市住宿上限？(2) 二线城市住宿上限？(3) 餐补标准？(4) 市内交通上限？(5) 报销提交时限？(6) 逾期由谁特批？",
        "reference": {"required_groups": [["450"], ["350"], ["100"], ["80"], ["30"], ["部门负责人"]]},
    },
    {
        "id": "two-city",
        "task": "3 人出差 5 天：前 2 晚住一线城市，后 3 晚住二线城市，均按住宿上限；餐补 5 天；市内交通每天每人 80 元（按 5 天）；往返机票每人 1280 元。总费用多少？",
        "reference": {"numbers": [{"value": 12390, "tol": 0.5}], "required_groups": [["12390", "12,390"]]},
    },
    {
        "id": "scenario-diff",
        "task": "3 人出差 2 晚：机票每人 1280 元；餐补 3 天；市内交通 2 天、每人每天 80 元。若住一线城市（按上限）总费用多少？若改住二线城市（按上限），能省多少元？",
        "reference": {
            "numbers": [{"value": 7920, "tol": 0.5}, {"value": 600, "tol": 0.5}],
            "required_groups": [["7920", "7,920"], ["600"]],
        },
    },
    {
        "id": "nobel-tax",
        "task": "2024 年诺贝尔物理学奖奖金为 1100 万瑞典克朗。按公司内部汇率换成人民币，再扣除 20% 税，税后折合多少万元人民币？保留两位小数。",
        "reference": {"numbers": [{"value": 598.4, "tol": 0.05}], "required_groups": [["598.4"]]},
    },
    {
        "id": "split-fx",
        "task": "500 美元换人民币：其中 300 美元按公司内部汇率（1 USD = 7.18 CNY），剩余 200 美元按市场汇率 7.00 计算。合计换得多少人民币？",
        "reference": {"numbers": [{"value": 3554, "tol": 0.5}], "required_groups": [["3554", "3,554"]]},
    },
]


def main() -> None:
    settings = Settings.from_env()
    agent = Agent(settings, llm=build_llm(settings))
    results = []
    for case in CASES:
        result = agent.run(
            case["task"], session=f"probe_{case['id']}",
            use_memory=False, max_reflections=0, verbose=False, save_trace=False,
        )
        judgment = judge(case["id"], result.answer, case["reference"])
        results.append({
            "id": case["id"], "success": judgment.success, "score": judgment.score,
            "answer": result.answer, "checks": judgment.to_dict()["checks"],
        })
        print(f"{'✓' if judgment.success else '✗'} {case['id']} judge={judgment.score:.2f} tools={result.tool_calls}")
        print(result.answer[:500], "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
