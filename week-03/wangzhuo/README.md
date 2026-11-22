# W3 · 反思设计模式 + 工具调用 Agent

> 完整作业文档：[W3-作业提交.md](W3-作业提交.md)

## 提交文件

- `W3-作业提交.md`：GitHub / 仓库阅读版。
- `W3-作业提交-单文件.md`：单文件提交版，图片已内嵌，只上传这一个文件也不会丢图。

一个可运行的 ReAct Agent：调用 4 个真实工具，按四维评分卡做结构化反思，
把可迁移的教训写入跨任务经验记忆；在 14 条注入缺陷的案例上，
**修复率从 0% 提升到 100%，首轮检出率 100%**。

## 快速开始（无需 API Key）

```bash
pip install -r requirements.txt
python -m reflexion_agent demo --offline   # 离线回放真实运行录制
python -m pytest -q                        # 31 个单元测试
```

## 接入真实模型

```bash
export LLM_BASE_URL=https://api.deepseek.com
export LLM_MODEL=deepseek-flash
export LLM_API_KEY=sk-xxx

python -m reflexion_agent demo             # 在线运行
python -m reflexion_agent ask "问题"
python -m reflexion_agent.eval.run_eval    # 端到端消融
python -m reflexion_agent.eval.run_repair  # 缺陷修复消融
```

不设置 `LLM_API_KEY` 时会自动进入离线回放模式，命令与在线模式完全一致。

## 目录

```
reflexion_agent/
├── agent.py            # Act → Critique → Revise 编排
├── llm.py              # 在线 / 离线回放 / 测试替身
├── tools/              # calculator、web_search、internal_api、exchange_rate
├── reflection/         # Critic、ReflectiveMemory
├── eval/               # 20 条用例、确定性判分、两套消融实验
└── recordings/         # 真实模型运行录制（离线回放用）
assets/                 # 架构图、实验图、运行截图
scripts/                # 出图、截图、种子生成
tests/                  # 31 个测试
```

## 实验一览

| 实验 | 结论 |
|---|---|
| 缺陷修复消融 | 无反思 0% → 单轮/完整反思 100% 修复，首轮检出 100% |
| 端到端消融 | 强模型首轮已近满分，出现天花板效应；反思的主要价值是兜底与可审计 |

详细分析、代码说明与运行截图见 [W3-作业提交.md](W3-作业提交.md)。
