"""确定性判分器。

不使用「让另一个 LLM 打分」，原因是：
1. 消融实验要可复现，判分必须稳定；
2. 判分标准要能被人工核对（数字容差、必备词、禁词）；
3. 避免判分模型与被测模型同源带来的偏差。

每个用例的 reference 支持：
* numbers: 必须出现的数值（带容差）；
* required_groups: 必须命中的词组，组内为「或」关系，组间为「且」关系；
* forbidden_phrases: 不允许出现的文本（用于识别典型错误口径）；
* require_refusal: 是否要求明确表示「无法确定」；
* forbid_other_numbers / allowed_numbers: 用于「不可答」题，防止编造数字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
REFUSAL_MARKERS = ("无法", "不能确定", "未提供", "没有", "不确定", "无法回答", "查不到", "没有找到", "未公开")


def normalize(text: str) -> str:
    # 去掉空白与千分位逗号，让 "7,920" 与 "7920" 视为同一文本
    return re.sub(r"[\s,，]", "", (text or "").lower())


def extract_numbers(text: str) -> list[float]:
    values: list[float] = []
    for raw in NUMBER_RE.findall(text or ""):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return values


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Judgment:
    case_id: str
    score: float
    success: bool
    checks: list[Check] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "score": self.score,
            "success": self.success,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks],
        }


def judge(case_id: str, answer: str, reference: dict[str, Any]) -> Judgment:
    text = answer or ""
    norm = normalize(text)
    numbers = extract_numbers(text)
    checks: list[Check] = []

    # 1) 数值检查
    for item in reference.get("numbers", []):
        target = float(item["value"])
        tol = float(item.get("tol", 0.01))
        hit = any(abs(n - target) <= tol for n in numbers)
        checks.append(Check("number", hit, f"期望 {target}（容差 {tol}），实际数值 {numbers[:12]}"))

    # 2) 必备词组（组内或，组间且）
    for group in reference.get("required_groups", []):
        hit = any(normalize(alt) in norm for alt in group)
        checks.append(Check("phrase", hit, "或".join(group)))

    # 3) 禁词（典型错误口径）
    for phrase in reference.get("forbidden_phrases", []):
        hit = normalize(phrase) in norm
        checks.append(Check("forbidden", not hit, f"不应出现：{phrase}"))

    # 4) 明确拒答
    if reference.get("require_refusal"):
        hit = any(marker in text for marker in REFUSAL_MARKERS)
        checks.append(Check("refusal", hit, "应明确说明无法确定，而不是编造"))

    # 5) 不可编造额外数字
    if reference.get("forbid_other_numbers"):
        allowed = [float(x) for x in reference.get("allowed_numbers", [])]
        extras = [n for n in numbers if not any(abs(n - a) <= 0.01 for a in allowed)]
        checks.append(Check("no_fabrication", not extras, f"出现了未授权数字：{extras[:8]}"))

    total = len(checks) or 1
    passed = sum(1 for c in checks if c.passed)
    score = round(passed / total, 4)
    return Judgment(case_id=case_id, score=score, success=score >= 0.999, checks=checks)
