"""经验记忆测试：写入、检索、持久化、清空。"""

from __future__ import annotations

from reflexion_agent.reflection.memory import ReflectiveMemory, similarity


def test_similarity() -> None:
    assert similarity("计算 3 人出差总费用", "计算 3 人出差的总费用") > 0.7
    assert similarity("计算总费用", "VPN 审批流程") < 0.2


def test_add_retrieve_and_persist(tmp_path) -> None:
    path = tmp_path / "memory.json"
    memory = ReflectiveMemory(path)
    memory.add(
        task="计算 3 人出差总费用",
        lesson="折扣题要先算原价再乘折扣，不要直接减折扣率",
        score_before=0.4,
        score_after=0.9,
    )
    memory.add(
        task="申请 VPN 需要谁审批",
        lesson="流程题要同时回答审批人和生效时间",
        score_before=0.5,
        score_after=0.85,
    )
    assert len(memory) == 2

    hits = memory.retrieve("计算 5 人出差总费用", k=1)
    assert len(hits) == 1
    assert "折扣" in hits[0].lesson

    reloaded = ReflectiveMemory(path)
    assert len(reloaded) == 2
    assert reloaded.retrieve("VPN 审批", k=1)[0].lesson.startswith("流程题")

    reloaded.clear()
    assert len(ReflectiveMemory(path)) == 0
