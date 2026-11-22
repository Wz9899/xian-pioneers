"""反思评估器（Critic）。

反思模式的关键不是「让模型再想一遍」，而是先定义**可检查的评分标准**，
再让模型按标准挑错。这里使用四维评分卡：

| 维度 | 权重 | 检查内容 |
|---|---|---|
| correctness  | 0.50 | 数值/事实是否正确，是否与工具结果一致 |
| tool_use     | 0.20 | 是否调用了必要工具，是否误用/漏用 |
| completeness | 0.20 | 是否回答任务的全部要求 |
| grounding    | 0.10 | 是否编造了轨迹之外的信息 |

总分 = 加权和。低于阈值则进入修订轮。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..llm import LLMClient, LLMResponse, extract_json_object

WEIGHTS = {
    "correctness": 0.45,
    "tool_use": 0.15,
    "completeness": 0.30,
    "grounding": 0.10,
}

CRITIC_SYSTEM = """你是一名极其严格但公正的结果评审员。你的职责是检查候选答案是否正确、是否有据、是否完整。

评分维度（每项 0~1 之间的小数）：
- correctness：数值/事实是否正确，是否与工具返回结果一致。算错、单位错、口径错都算低分。
- tool_use：是否调用了完成任务所必需的工具；是否漏用工具导致凭空作答；是否误用工具。
- completeness：是否回答了任务的全部子问题，是否遗漏约束条件。
- grounding：答案中的关键数字/事实是否能在工具观察中找到依据；编造信息应给 0 分。

判定规则：
- 总分 = 0.45*correctness + 0.15*tool_use + 0.30*completeness + 0.10*grounding。
- 只要存在任何一个实质问题（子问题遗漏、单位/口径错误、编造信息、与工具结果不符、没有明确回答），verdict 必须是 "revise"，即使加权分较高。
- 只有答案完全正确、完整、有据、且明确回答了用户的每一问时，verdict 才是 "pass"。
- 只输出一个 JSON 对象，不要输出 Markdown 代码块或额外解释。

输出格式：
{"correctness":0.0,"tool_use":0.0,"completeness":0.0,"grounding":0.0,"score":0.0,"verdict":"pass","issues":["具体问题1","具体问题2"],"fix_hint":"下一轮应该怎么改，一句话"}
"""


@dataclass
class Critique:
    score: float
    dimensions: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    fix_hint: str = ""
    verdict: str = "revise"
    raw: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "dimensions": self.dimensions,
            "issues": self.issues,
            "fix_hint": self.fix_hint,
            "verdict": self.verdict,
        }


class Critic:
    def __init__(self, llm: LLMClient, threshold: float = 0.85) -> None:
        self.llm = llm
        self.threshold = threshold
        self.last_usage: dict[str, int] = {}

    def evaluate(self, task: str, answer: str, trace_summary: str) -> Critique:
        if not answer.strip():
            return Critique(
                score=0.0,
                dimensions={k: 0.0 for k in WEIGHTS},
                issues=["没有给出最终答案"],
                fix_hint="重新完成任务并给出明确答案",
                verdict="revise",
            )

        user = (
            f"【任务】\n{task}\n\n"
            f"【工具调用轨迹】\n{trace_summary or '（未调用任何工具）'}\n\n"
            f"【候选答案】\n{answer}\n\n"
            "请严格按评分卡输出 JSON。"
        )
        resp: LLMResponse = self.llm.chat(
            [
                {"role": "system", "content": CRITIC_SYSTEM},
                {"role": "user", "content": user},
            ]
        )
        self.last_usage = resp.usage
        return self._parse(resp, answer, trace_summary)

    # ------------------------------------------------------------------
    def _parse(self, resp: LLMResponse, answer: str, trace_summary: str) -> Critique:
        data = extract_json_object(resp.content)
        if not data:
            return self._heuristic(answer, trace_summary, raw=resp.content)

        dims: dict[str, float] = {}
        for key in WEIGHTS:
            try:
                dims[key] = max(0.0, min(1.0, float(data.get(key, 0.0))))
            except (TypeError, ValueError):
                dims[key] = 0.0

        # 总分由我们按固定权重计算，避免模型自报分数与维度不一致
        score = round(sum(dims[k] * w for k, w in WEIGHTS.items()), 3)
        issues = data.get("issues") or []
        if isinstance(issues, str):
            issues = [issues]
        issues = [str(i).strip() for i in issues if str(i).strip()][:5]

        model_verdict = str(data.get("verdict", "")).strip().lower()
        model_pass = model_verdict == "pass" if model_verdict in {"pass", "revise"} else score >= self.threshold
        verdict = "pass" if (model_pass and score >= self.threshold
                             and not any("严重" in i for i in issues)) else "revise"
        return Critique(
            score=score,
            dimensions=dims,
            issues=issues,
            fix_hint=str(data.get("fix_hint", "")).strip(),
            verdict=verdict,
            raw=resp.content,
        )

    def _heuristic(self, answer: str, trace_summary: str, raw: str = "") -> Critique:
        """评审输出不可解析时的降级逻辑，保证循环不会因格式问题死掉。"""
        dims = {
            "correctness": 0.6,
            "tool_use": 0.8 if trace_summary else 0.3,
            "completeness": 0.7 if len(answer) > 20 else 0.4,
            "grounding": 0.6 if trace_summary else 0.2,
        }
        score = round(sum(dims[k] * w for k, w in WEIGHTS.items()), 3)
        return Critique(
            score=score,
            dimensions=dims,
            issues=["评审器输出无法解析为 JSON，已使用保守估计"],
            fix_hint="检查答案是否包含完整步骤与最终结果",
            verdict="revise" if score < self.threshold else "pass",
            raw=raw,
        )
