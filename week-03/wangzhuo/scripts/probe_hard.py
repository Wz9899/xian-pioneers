"""开发期探针：更难的复合任务，检验是否能把强模型逼出错误。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reflexion_agent.agent import Agent
from reflexion_agent.config import Settings
from reflexion_agent.eval.judge import judge
from reflexion_agent.llm import build_llm

CASES = [
    {
        "id": "invoice-audit",
        "task": (
            "请依据公司制度审核这张 3 人出差 3 天 2 晚的一线城市报销单，逐项判断是否超标，并给出可报销总额：\n"
            "- 住宿：3 间房 × 2 晚，每间 480 元\n"
            "- 机票：3 人 × 1280 元\n"
            "- 餐补：3 人 × 3 天 × 120 元/天\n"
            "- 市内交通：3 人 × 2 天 × 90 元/天\n"
            "最后给出超标金额。"
        ),
        "reference": {
            "numbers": [{"value": 7920, "tol": 0.5}, {"value": 420, "tol": 0.5}],
            "required_groups": [["7920", "7,920"], ["420"]],
        },
    },
    {
        "id": "multi-city-budget",
        "task": (
            "2026 年 11 月，3 人出差 5 天：前 2 晚住一线城市，后 3 晚住二线城市，均按住宿上限；"
            "餐补按 5 天；市内交通每天每人 80 元；往返机票每人 1280 元。"
            "总费用是多少？公司预算 12000 元是否足够？"
        ),
        "reference": {
            "numbers": [{"value": 12390, "tol": 0.5}],
            "required_groups": [["12390", "12,390"], ["不够", "不足", "超出", "超预算"]],
        },
    },
    {
        "id": "over-cap-share",
        "task": "3 人一线城市出差 2 晚，实际住宿 500 元/晚/人。公司只报销上限内的部分，超出部分公司承担 50%、个人承担 50%。个人共需自付多少元？",
        "reference": {"numbers": [{"value": 150, "tol": 0.5}], "required_groups": [["150"]]},
    },
    {
        "id": "reimburse-vs-actual",
        "task": "3 人出差 2 天：住宿实际 480 元/晚/人 × 2 晚；机票 1280 元/人；餐补实际 120 元/天/人 × 3 天；市内交通 90 元/天/人 × 2 天。请按公司制度计算可报销总额与员工自付额。",
        "reference": {
            "numbers": [{"value": 7920, "tol": 0.5}, {"value": 420, "tol": 0.5}],
            "required_groups": [["7920", "7,920"], ["420"]],
        },
    },
    {
        "id": "procurement-multi",
        "task": "公司采购制度：所有采购需附几家比价记录？单笔 12000 元需要谁审批？单笔 8000 元需要谁审批？逐条回答。",
        "reference": {"required_groups": [["3 家", "三家", "3家"], ["总经理"], ["部门负责人"]]},
    },
    {
        "id": "leave-false-premise",
        "task": "员工 2021 年 7 月入职，2026 年 11 月申请年假。HR 说该员工每年年假是 5 天，对吗？应是多少天？",
        "reference": {"numbers": [{"value": 10, "tol": 0.01}], "required_groups": [["10 天", "10天", "十天"], ["不对", "不是", "并非", "错误"]]},
    },
    {
        "id": "nobel-budget",
        "task": "2024 年诺贝尔物理学奖奖金为 1100 万瑞典克朗。按内部汇率换成人民币，扣除 20% 税后，是否足够购买一台 800 万元的设备？请给出税后金额（万元）和结论。",
        "reference": {
            "numbers": [{"value": 598.4, "tol": 0.1}],
            "required_groups": [["598.4"], ["不够", "不足", "买不起", "低于"]],
        },
    },
    {
        "id": "fx-difference",
        "task": "500 美元按公司内部汇率 1 USD = 7.18 CNY 换人民币。如果汇率变成 7.00，会少换多少人民币？",
        "reference": {"numbers": [{"value": 90, "tol": 0.5}], "required_groups": [["90"]]},
    },
]


def main() -> None:
    settings = Settings.from_env()
    print(f"model={settings.model} temperature={settings.temperature}")
    agent = Agent(settings, llm=build_llm(settings))
    failed = 0
    for case in CASES:
        result = agent.run(
            case["task"], session=f"probeh_{case['id']}",
            use_memory=False, max_reflections=0, verbose=False, save_trace=False,
        )
        judgment = judge(case["id"], result.answer, case["reference"])
        failed += 0 if judgment.success else 1
        print(f"{'✓' if judgment.success else '✗'} {case['id']:<22} judge={judgment.score:.2f} tools={result.tool_calls}")
        if not judgment.success:
            print("   ANSWER:", result.answer[:400].replace("\n", " "))
            print("   CHECKS:", [(c.name, c.passed, c.detail) for c in judgment.checks])
    print(f"\nfailed {failed}/{len(CASES)}")


if __name__ == "__main__":
    sys.exit(main())
