"""Agent 编排测试：工具循环、反思修订、反思预算、记忆沉淀。

全部使用 ScriptedClient，不联网、不花 token，因此可以在 CI 里稳定运行。
"""

from __future__ import annotations

from reflexion_agent.agent import Agent
from reflexion_agent.config import Settings
from reflexion_agent.llm import LLMResponse, ScriptedClient, ToolCall
from reflexion_agent.reflection.memory import ReflectiveMemory

CRITIC_REVISE = """{"correctness":0.2,"tool_use":0.5,"completeness":0.5,"grounding":0.5,
"score":0.35,"verdict":"revise","issues":["计算结果错误"],"fix_hint":"使用计算器重新计算"}"""


def make_settings(**kwargs) -> Settings:
    defaults = {"offline": True, "max_reflections": 1}
    defaults.update(kwargs)
    return Settings(**defaults)


def test_tool_loop() -> None:
    llm = ScriptedClient(
        [
            LLMResponse(tool_calls=[ToolCall("call_1", "calculator", {"expression": "(12+8)*3"})]),
            LLMResponse(content="答案：60"),
        ]
    )
    agent = Agent(make_settings(max_reflections=0), llm=llm)
    result = agent.run("(12+8)*3 等于多少？", use_memory=False)

    assert "60" in result.answer
    assert result.tool_calls == 1
    assert result.llm_calls == 2
    assert result.critiques == []


def test_reflection_triggers_revision() -> None:
    llm = ScriptedClient(
        [
            LLMResponse(content="答案：100"),          # 第一轮：错误答案
            LLMResponse(content=CRITIC_REVISE),        # Critic：低分，要求修订
            LLMResponse(content="答案：1926"),         # 第二轮：修正后的答案
        ]
    )
    memory = ReflectiveMemory()
    memory.clear()
    agent = Agent(make_settings(max_reflections=1), llm=llm, memory=memory)
    result = agent.run("团建总费用是多少？", use_memory=True, record_memory=False)

    assert result.reflections == 1
    assert len(result.critiques) == 1
    assert "1926" in result.answer
    assert result.llm_calls == 3
    # 修订提示确实带上了评审问题
    second_round_messages = llm.calls[2]
    assert any("计算结果错误" in str(m.get("content", "")) for m in second_round_messages)


def test_no_reflection_skips_critic() -> None:
    llm = ScriptedClient([LLMResponse(content="答案：42")])
    agent = Agent(make_settings(max_reflections=0), llm=llm)
    result = agent.run("随便问", use_memory=False)
    assert result.reflections == 0
    assert result.critiques == []
    assert result.llm_calls == 1


def test_memory_written_after_reflection(tmp_path) -> None:
    llm = ScriptedClient(
        [
            LLMResponse(content="答案：100"),
            LLMResponse(content=CRITIC_REVISE),
            LLMResponse(content="答案：1926"),
        ]
    )
    memory = ReflectiveMemory(tmp_path / "memory.json")
    memory.clear()
    agent = Agent(make_settings(max_reflections=1), llm=llm, memory=memory)
    result = agent.run("团建总费用是多少？", use_memory=True, record_memory=True)

    assert result.memory_written
    assert len(memory) == 1
    assert "计算结果错误" in memory.episodes[0].lesson


def test_max_steps_forces_final_answer() -> None:
    # 模型一直要调用工具，超过 max_steps 后应被强制收尾
    llm = ScriptedClient(
        [LLMResponse(tool_calls=[ToolCall(f"call_{i}", "calculator", {"expression": "1+1"})]) for i in range(2)]
        + [LLMResponse(content="答案：2")]
    )
    agent = Agent(make_settings(max_reflections=0, max_steps=2), llm=llm)
    result = agent.run("1+1", use_memory=False)
    assert result.answer == "答案：2"
    assert result.tool_calls == 2
