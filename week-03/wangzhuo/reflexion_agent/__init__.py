"""reflexion-agent：带反思设计模式的工具调用 Agent。

本包实现了一个完整的 Agent 学习实验：
1. ReAct 工具调用循环（思考 → 行动 → 观察）；
2. 基于评分卡（rubric）的结构化反思与修订（Self-Refine）；
3. 跨任务的经验记忆（Reflexion）；
4. 可复现的消融实验与全链路追踪。

作者：wangzhuo（Wz9899）
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
