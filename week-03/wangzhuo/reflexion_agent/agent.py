"""Agent 编排：ReAct 执行 → 结构化反思 → 带反馈修订 → 经验沉淀。

执行流程（对应文档中的架构图）：

    Act（ReAct 循环）
      → Critic 按评分卡打分
        → 未通过则把「问题 + 修改建议」作为新输入，回到 Act
          → 通过 / 预算耗尽 / 边际收益不足 时停止
            → 把可迁移的教训写入经验记忆

设计要点：
* 反思不是无条件重跑：只有分数低于阈值才进入修订；
* 每轮都复用已有工具观察，避免重复计算；
* 预算、阈值、边际收益三个开关共同防止「为反思而反思」。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .llm import LLMClient, LLMResponse, build_llm
from .reflection.critic import Critic
from .reflection.memory import Episode, ReflectiveMemory
from .tools import ToolRegistry, build_registry
from .trace import Tracer

ACTOR_SYSTEM = """你是一个会使用工具的严谨助理。可用工具：calculator（计算）、web_search（联网搜索）、internal_api（公司内部知识库）、exchange_rate（内部汇率换算）。

工作要求：
1. 涉及数值计算必须调用 calculator，不要心算；涉及外部事实必须调用 web_search；涉及公司制度必须调用 internal_api；涉及货币换算必须调用 exchange_rate。
2. 工具返回结果后再决定下一步。可以连续调用多个工具。
3. 如果工具报错或信息不足，可以换参数重试；仍无法确定时，明确说明「无法确定」，绝对不要编造数字或条款。
4. 最终答案先给 2~4 条关键步骤，然后另起一行写：答案：<最终结果>

{memory}
"""

REVISION_TEMPLATE = """【评审意见】上一轮答案未通过检查。

评分：{score:.2f}
问题：
{issues}
修改建议：{hint}

请在已有工具观察的基础上修正答案。需要时可以重新调用工具验证，但不要重复已经成功的计算。输出完整的最终答案，最后一行仍然写：答案：<最终结果>。
"""


@dataclass
class AgentResult:
    task: str
    answer: str
    score: float | None
    passed: bool | None
    reflections: int
    tool_calls: int
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency: float
    critiques: list[dict[str, Any]] = field(default_factory=list)
    memory_hit: bool = False
    memory_written: bool = False
    memory_episodes: list[str] = field(default_factory=list)
    trace_path: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, include_events: bool = False) -> dict[str, Any]:
        data = {
            "task": self.task,
            "answer": self.answer,
            "score": self.score,
            "passed": self.passed,
            "reflections": self.reflections,
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency": round(self.latency, 2),
            "critiques": self.critiques,
            "memory_hit": self.memory_hit,
            "memory_written": self.memory_written,
            "memory_episodes": self.memory_episodes,
            "trace_path": self.trace_path,
        }
        if include_events:
            data["events"] = self.events
        return data


class Agent:
    def __init__(
        self,
        settings: Settings,
        llm: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        memory: ReflectiveMemory | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm or build_llm(settings)
        self.registry = registry or build_registry(settings)
        self.memory = memory if memory is not None else ReflectiveMemory(settings.memory_path)
        self.critic = Critic(self.llm, settings.reflection_threshold)

    # ------------------------------------------------------------------
    def run(
        self,
        task: str,
        *,
        session: str = "",
        use_memory: bool = True,
        max_reflections: int | None = None,
        verbose: bool = False,
        save_trace: bool = True,
        record_memory: bool = True,
    ) -> AgentResult:
        started = time.perf_counter()
        max_reflections = self.settings.max_reflections if max_reflections is None else max_reflections
        if session and hasattr(self.llm, "set_session"):
            self.llm.set_session(session)  # type: ignore[attr-defined]

        # 离线回放缺少该会话时，不做无意义的反思与记忆写入
        offline_missing = (
            getattr(self.llm, "mode", "") == "offline"
            and not getattr(self.llm, "session_found", True)
        )
        if offline_missing:
            max_reflections = 0
            use_memory = False

        tracer = Tracer(verbose=verbose)
        if offline_missing:
            tracer.event(
                "error",
                "离线回放缺少该会话录制：请设置 LLM_API_KEY 运行，或先执行 demo/eval --record。",
            )
        stats = {
            "llm_calls": 0,
            "tool_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        tracer.event("task", f"任务：{task}")

        # ---- 1. 记忆检索 -------------------------------------------------
        episodes: list[Episode] = []
        memory_block = ""
        if use_memory:
            episodes = self.memory.retrieve(task, k=2)
            if episodes:
                memory_block = ReflectiveMemory.format(episodes)
                tracer.event(
                    "memory",
                    f"记忆命中 {len(episodes)} 条："
                    + " | ".join(e.lesson[:60] for e in episodes),
                )

        system = ACTOR_SYSTEM.format(memory=memory_block)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]

        answer = ""
        critiques = []
        reflections = 0
        prev_score: float | None = None
        seen_calls: set[str] = set()

        # ---- 2. Act → Critique 循环 --------------------------------------
        for attempt in range(max_reflections + 1):
            tracer.section(f"第 {attempt + 1} 轮 · Actor（ReAct）")
            answer = self._react(messages, stats, tracer, seen_calls)
            tracer.event("answer", f"│ 候选答案：{_one_line(answer, 240)}")
            tracer.close_section()

            if attempt >= max_reflections:
                break  # 反思预算已用完

            summary = self._trace_summary(tracer)
            critique = self.critic.evaluate(task, answer, summary)
            stats["llm_calls"] += 1
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                stats[key] += int(self.critic.last_usage.get(key, 0))
            critiques.append(critique.to_dict())

            tracer.section(f"反思 #{attempt + 1} · Critic")
            dims = " ".join(f"{k}={v:.2f}" for k, v in critique.dimensions.items())
            tracer.event("critique", f"│ 评分：{critique.score:.2f}  （{dims}）")
            tracer.event(
                "critique",
                f"│ 判定：{'通过' if critique.passed else '需要修订'}"
                + (f"｜问题：{'; '.join(critique.issues[:3])}" if critique.issues else ""),
            )
            if critique.fix_hint:
                tracer.event("critique", f"│ 建议：{critique.fix_hint}")
            tracer.close_section()

            if critique.passed:
                break

            if prev_score is not None and (critique.score - prev_score) < self.settings.min_improvement:
                tracer.event("critique", "│ 分数提升低于阈值，提前停止反思（避免无效消耗）")
                break

            prev_score = critique.score
            issues_text = "\n".join(f"- {i}" for i in critique.issues) or "- 答案未达到通过标准"
            messages.append(
                {
                    "role": "user",
                    "content": REVISION_TEMPLATE.format(
                        score=critique.score,
                        issues=issues_text,
                        hint=critique.fix_hint or "重新核对数值、单位与工具的原始输出",
                    ),
                }
            )
            reflections += 1
            tracer.event("revision", f"→ 进入第 {reflections} 次修订")

        # ---- 3. 经验沉淀 --------------------------------------------------
        # 只有「真正发生过修订」才值得写入；首轮满分的任务不产生经验。
        memory_written = False
        if use_memory and record_memory and reflections > 0 and critiques:
            source = next((c for c in critiques if c.get("issues")), None)
            if source:
                issues = source.get("issues") or []
                lesson = "；".join(issues[:3])
                if source.get("fix_hint"):
                    lesson = f"{lesson}。修正：{source['fix_hint']}" if lesson else source["fix_hint"]
                if lesson.strip() and "无需修改" not in lesson:
                    ep = self.memory.add(
                        task=task,
                        lesson=lesson,
                        score_before=critiques[0].get("score", 0.0),
                        score_after=critiques[-1].get("score", 0.0),
                    )
                    memory_written = True
                    tracer.event("memory", f"写入经验：{ep.lesson[:100]}")

        # ---- 4. 收尾与落盘 ------------------------------------------------
        score = critiques[-1].get("score") if critiques else None
        passed = bool(critiques[-1].get("verdict") == "pass") if critiques else None
        latency = time.perf_counter() - started

        tracer.section("最终结果")
        tracer.event("final", f"│ 答案：{_one_line(answer, 500)}")
        if score is not None:
            tracer.event("final", f"│ 评审得分：{score:.2f}（{'通过' if passed else '未通过'}）")
        tracer.event(
            "final",
            "│ 统计：反思 {r} 次 | 工具调用 {t} 次 | LLM 调用 {l} 次 | "
            "tokens {tok} | 耗时 {sec:.2f}s".format(
                r=reflections,
                t=stats["tool_calls"],
                l=stats["llm_calls"],
                tok=stats["total_tokens"],
                sec=latency,
            ),
        )
        tracer.close_section()

        trace_path = ""
        if save_trace and session:
            safe = re.sub(r"[^0-9A-Za-z._-]+", "_", session)
            path = self.settings.trace_dir / f"{safe}.jsonl"
            tracer.save(path)
            trace_path = str(path)

        return AgentResult(
            task=task,
            answer=answer,
            score=score,
            passed=passed,
            reflections=reflections,
            tool_calls=stats["tool_calls"],
            llm_calls=stats["llm_calls"],
            prompt_tokens=stats["prompt_tokens"],
            completion_tokens=stats["completion_tokens"],
            total_tokens=stats["total_tokens"],
            latency=latency,
            critiques=critiques,
            memory_hit=bool(episodes),
            memory_written=memory_written,
            memory_episodes=[e.lesson for e in episodes],
            trace_path=trace_path,
            events=tracer.events,
        )

    # ------------------------------------------------------------------
    def repair(
        self,
        task: str,
        candidate: str,
        trace_summary: str,
        *,
        session: str = "",
        use_memory: bool = False,
        max_reflections: int = 1,
        verbose: bool = False,
        save_trace: bool = False,
        record_memory: bool = False,
    ) -> AgentResult:
        """从一份「已有缺陷的候选答案」出发，执行评审 → 修订。

        这是隔离反思模块的实验入口：Actor 的首轮能力被固定为候选答案，
        因此可以直接测量反思本身能修复多少缺陷。
        """
        started = time.perf_counter()
        if session and hasattr(self.llm, "set_session"):
            self.llm.set_session(session)  # type: ignore[attr-defined]

        tracer = Tracer(verbose=verbose)
        stats = {"llm_calls": 0, "tool_calls": 0,
                 "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        tracer.event("task", f"任务：{task}")
        tracer.event("answer", f"初始候选：{_one_line(candidate, 300)}")

        episodes: list[Episode] = []
        memory_block = ""
        if use_memory:
            episodes = self.memory.retrieve(task, k=2)
            if episodes:
                memory_block = ReflectiveMemory.format(episodes)
                tracer.event("memory", f"记忆命中 {len(episodes)} 条")

        system = ACTOR_SYSTEM.format(memory=memory_block)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"{task}\n\n【已有的工具观察】\n{trace_summary or '（无）'}\n\n"
                    f"【上一轮候选答案】\n{candidate}\n\n"
                    "请审查这份候选答案是否正确、完整、有据。如有问题，请依据工具观察修正，"
                    "必要时可以重新调用工具验证。最后一行仍写：答案：<最终结果>"
                ),
            },
        ]

        answer = candidate
        critiques = []
        reflections = 0
        prev_score: float | None = None
        seen_calls: set[str] = set()

        for attempt in range(max_reflections + 1):
            full_trace = trace_summary
            new_trace = self._trace_summary(tracer)
            if new_trace:
                full_trace = f"{trace_summary}\n{new_trace}" if trace_summary else new_trace

            critique = self.critic.evaluate(task, answer, full_trace)
            stats["llm_calls"] += 1
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                stats[key] += int(self.critic.last_usage.get(key, 0))
            critiques.append(critique.to_dict())

            tracer.section(f"反思 #{attempt + 1} · Critic")
            dims = " ".join(f"{k}={v:.2f}" for k, v in critique.dimensions.items())
            tracer.event("critique", f"│ 评分：{critique.score:.2f}  （{dims}）")
            tracer.event("critique", f"│ 判定：{'通过' if critique.passed else '需要修订'}"
                                       + (f"｜问题：{'; '.join(critique.issues[:3])}" if critique.issues else ""))
            if critique.fix_hint:
                tracer.event("critique", f"│ 建议：{critique.fix_hint}")
            tracer.close_section()

            if critique.passed or attempt >= max_reflections:
                break
            if prev_score is not None and (critique.score - prev_score) < self.settings.min_improvement:
                tracer.event("critique", "│ 分数提升低于阈值，提前停止")
                break
            prev_score = critique.score

            issues_text = "\n".join(f"- {i}" for i in critique.issues) or "- 答案未达到通过标准"
            messages.append({
                "role": "user",
                "content": REVISION_TEMPLATE.format(
                    score=critique.score,
                    issues=issues_text,
                    hint=critique.fix_hint or "依据工具观察重新核对",
                ),
            })
            reflections += 1
            tracer.section(f"第 {reflections + 1} 轮 · 修订")
            answer = self._react(messages, stats, tracer, seen_calls)
            tracer.event("answer", f"│ 修订后：{_one_line(answer, 300)}")
            tracer.close_section()

        memory_written = False
        if use_memory and record_memory and reflections > 0 and critiques:
            source = next((c for c in critiques if c.get("issues")), None)
            if source:
                lesson = "；".join((source.get("issues") or [])[:3])
                if source.get("fix_hint"):
                    lesson = f"{lesson}。修正：{source['fix_hint']}" if lesson else source["fix_hint"]
                if lesson.strip() and "无需修改" not in lesson:
                    self.memory.add(task, lesson,
                                    critiques[0].get("score", 0.0), critiques[-1].get("score", 0.0))
                    memory_written = True

        latency = time.perf_counter() - started
        score = critiques[-1].get("score") if critiques else None
        passed = critiques[-1].get("verdict") == "pass" if critiques else None
        tracer.event(
            "final",
            "│ 修复完成：反思 {r} 次 | 工具 {t} 次 | LLM {l} 次 | tokens {tok} | {sec:.2f}s".format(
                r=reflections, t=stats["tool_calls"], l=stats["llm_calls"],
                tok=stats["total_tokens"], sec=latency),
        )

        trace_path = ""
        if save_trace and session:
            safe = re.sub(r"[^0-9A-Za-z._-]+", "_", session)
            path = self.settings.trace_dir / f"{safe}.jsonl"
            tracer.save(path)
            trace_path = str(path)

        return AgentResult(
            task=task, answer=answer, score=score, passed=passed,
            reflections=reflections, tool_calls=stats["tool_calls"], llm_calls=stats["llm_calls"],
            prompt_tokens=stats["prompt_tokens"], completion_tokens=stats["completion_tokens"],
            total_tokens=stats["total_tokens"], latency=latency, critiques=critiques,
            memory_hit=bool(episodes), memory_written=memory_written,
            memory_episodes=[e.lesson for e in episodes], trace_path=trace_path,
            events=tracer.events,
        )

    # ------------------------------------------------------------------
    def _chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None,
        stats: dict[str, int],
    ) -> LLMResponse:
        resp = self.llm.chat(messages, tools)
        stats["llm_calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            stats[key] += int(resp.usage.get(key, 0))
        return resp

    def _react(
        self,
        messages: list[dict[str, Any]],
        stats: dict[str, int],
        tracer: Tracer,
        seen_calls: set[str],
    ) -> str:
        """执行一轮 ReAct：调用工具直到模型给出最终答案。"""
        for _step in range(self.settings.max_steps):
            resp = self._chat(messages, self.registry.specs(), stats)

            if resp.tool_calls:
                if resp.content:
                    tracer.event("llm", f"│ 思考：{_one_line(resp.content, 180)}")
                messages.append(
                    {
                        "role": "assistant",
                        "content": resp.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                                },
                            }
                            for tc in resp.tool_calls
                        ],
                    }
                )
                for tc in resp.tool_calls:
                    key = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True, ensure_ascii=False)}"
                    duplicate = key in seen_calls
                    seen_calls.add(key)
                    tracer.event(
                        "tool_call",
                        f"│ 工具 {tc.name}({_compact_args(tc.arguments)})"
                        + ("  [重复调用]" if duplicate else ""),
                    )
                    result = self.registry.call(tc.name, tc.arguments)
                    stats["tool_calls"] += 1
                    display = _one_line(result.render(), 260)
                    tracer.event(
                        "tool_result",
                        f"│ 结果：{display}" if result.ok else f"│ 失败：{result.error}",
                    )
                    content = result.render()
                    if duplicate:
                        content += "\n（提示：这是重复调用，结果与上次相同，请勿再重复。）"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.name,
                            "content": content,
                        }
                    )
                continue

            # 没有工具调用 → 最终答案
            answer = (resp.content or "").strip()
            messages.append({"role": "assistant", "content": answer})
            return answer

        # 步数耗尽：强制收尾，避免死循环
        tracer.event("error", "│ 达到最大步数，强制要求给出最终答案")
        messages.append(
            {
                "role": "user",
                "content": "已达到工具调用上限。请基于以上观察直接给出最终答案，不要再调用工具；信息不足请明确说明。",
            }
        )
        resp = self._chat(messages, None, stats)
        answer = (resp.content or "").strip()
        messages.append({"role": "assistant", "content": answer})
        return answer

    @staticmethod
    def _trace_summary(tracer: Tracer) -> str:
        lines: list[str] = []
        for event in tracer.events:
            if event["kind"] == "tool_call":
                lines.append(event["message"].replace("│ ", "").strip())
            elif event["kind"] == "tool_result":
                lines.append("  " + event["message"].replace("│ ", "").strip()[:500])
        text = "\n".join(lines)
        return text[:3000] if text else ""


def _one_line(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text[:limit] + ("…" if len(text) > limit else "")


def _compact_args(args: dict[str, Any]) -> str:
    try:
        raw = json.dumps(args, ensure_ascii=False)
    except TypeError:
        raw = str(args)
    return raw[:220]
