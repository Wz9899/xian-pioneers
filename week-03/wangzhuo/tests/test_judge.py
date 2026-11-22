"""确定性判分器测试。"""

from __future__ import annotations

from reflexion_agent.eval.judge import judge


def test_number_with_tolerance() -> None:
    ref = {"numbers": [{"value": 1926, "tol": 0.5}]}
    assert judge("calc", "答案：1926 元", ref).success
    assert judge("calc", "答案：1,926 元", ref).success
    assert not judge("calc", "答案：1930 元", ref).success


def test_phrase_groups_are_and_between_groups() -> None:
    ref = {"required_groups": [["Hopfield", "霍普菲尔德"], ["Hinton", "辛顿"]]}
    assert judge("search", "获奖者是 Hopfield 与 Hinton。", ref).success
    assert not judge("search", "获奖者是 Hopfield。", ref).success


def test_forbidden_phrase_catches_wrong_caliber() -> None:
    ref = {"numbers": [{"value": 21800, "tol": 0.5}], "forbidden_phrases": ["21854"]}
    assert judge("calc", "单利本息 21800 元。", ref).success
    assert not judge("calc", "按复利计算 21854.54 元。", ref).success


def test_refusal_and_no_fabrication() -> None:
    ref = {"require_refusal": True, "forbid_other_numbers": True, "allowed_numbers": [2027]}
    result = judge("unanswerable", "内部资料没有提供 2027 年营收目标，无法确定。", ref)
    assert result.success
    bad = judge("unanswerable", "2027 年营收目标是 50 亿元。", ref)
    assert not bad.success
