# LLM API 调用练手工程

从「手动发一个 HTTP 请求」一路做到「带工具、Skill 和 MCP 的小 agent」，全程不依赖
OpenAI/Anthropic 官方 SDK，把协议细节摊开看。

运行截图见 `screenshots/`。

- 本周任务反馈：[docs/本周任务反馈.md](docs/本周任务反馈.md)（交付说明、关键实现、踩坑记录）
- 技术细节附录：[docs/项目说明书.md](docs/项目说明书.md)（接口、数据格式、维护须知）

## 一分钟理解架构

```
                       ┌──────────────────────────────┐
   用户输入 ─────────▶ │  agent.py  （ReAct 循环）      │
                       │  模型说「调工具」→ 执行 → 回灌  │
                       └──────────────┬───────────────┘
                                      │ 统一的 Function Calling schema
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
      ┌───────────────┐      ┌────────────────┐      ┌────────────────┐
      │ tools.py      │      │ skills.py      │      │ mcp_client.py  │
      │ 本地 Python 函数│      │ SKILL.md 渐进式 │      │ 子进程 + JSON-RPC│
      │ 计算/时间/读文件│      │ 披露式加载      │      │ 官方 filesystem │
      └───────────────┘      └────────────────┘      └────────────────┘
                                      │
                                      ▼
                         ┌──────────────────────────┐
                         │ llm.py  OpenAI 兼容协议   │
                         │ 流式 / tool_calls / 重试  │
                         └──────────────────────────┘
```

关键点：**三类能力最终都变成同一种东西——Function Calling 的工具定义**。
所以 agent 的循环代码完全不需要区分「这是本地函数、Skill 还是 MCP 远程工具」。

## 快速开始

```powershell
# 1. 环境（只依赖 httpx；MCP server 用 npm 装到 .mcp/）
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
npm install --prefix .mcp @modelcontextprotocol/server-filesystem

# 2. 配置密钥
copy .env.example .env      # 然后把 LLM_API_KEY 换成真实 key

# 3. 跑
.\run.ps1 env_report.py          # 环境自检
.\run.ps1 chat.py "你好"          # 最小一次问答（流式）
.\run.ps1 demo.py                # 四个场景完整演示
.\run.ps1 demo.py --mock         # 没有 key 时用本地 mock 模型验证链路
```

> `run.ps1` 只做三件事：切 UTF-8 代码页、指向 `.venv` 的 python、透传参数。
> 不想用脚本就直接 `python demo.py`，前提是控制台编码设成 UTF-8。

### 在 VS Code 里运行

项目自带 `.vscode/settings.json`，已经处理好两件事：

- `code-runner.respectShebang: false`——否则 Code Runner 会按文件里的
  `#!/usr/bin/env python` 去执行，在 Windows 上直接报「系统找不到指定的路径」；
- `code-runner.executorMap.python` 指向 `.venv\Scripts\python.exe`，
  并给终端注入 `PYTHONUTF8=1`。

所以直接点右上角 ▶ 就能跑。若依旧报错，检查设置里是不是被 workspace 之外的
用户级配置覆盖了。命令行永远是更可靠的方式：

```powershell
.\.venv\Scripts\python.exe demo.py --mock
```

## 四个能力，逐个看

### 1. 基础调用 —— `llm.py`

手写 `/chat/completions`，而不是套 SDK，因为要看清楚三件事：

- **流式响应是 SSE 分帧**：每行 `data: {...}`，以 `data: [DONE]` 结束；
- **`tool_calls` 在流式下是增量下发的**：`id`、`name`、`arguments` 会分片到达，
  必须按 `index` 各自拼接（`llm.py:_consume_stream`）；
- **重试要有边界**：429/5xx/网络异常指数退避，401/402/400 直接失败并翻译成人话。

换厂商只需改 `.env` 两行，因为 DeepSeek / Moonshot / 通义 / Ollama / vLLM 都实现了同一套协议：

| 厂商 | LLM_BASE_URL | LLM_MODEL |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` / `deepseek-reasoner` |
| Moonshot | `https://api.moonshot.cn/v1` | `kimi-k2-0905-preview` |
| 阿里百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5:7b` |

### 2. 工具调用 —— `tools.py`

用 `@tool` 装饰器注册，`schemas()` 自动生成 OpenAI 格式的 tools 数组，
模型看到的是描述 + JSON Schema，而不是代码。

内置四个工具，都刻意做了防御：

| 工具 | 设计要点 |
|---|---|
| `calculator` | **不用 `eval`**，走 AST 白名单求值，只放行数字与四则运算 |
| `get_current_time` | 逼模型别凭记忆编时间——它必须调工具才能知道 |
| `list_files` / `read_file` | 路径限制在工作区内，挡掉 `../` 穿越 |

工具执行出错**不抛异常**，而是把错误信息当字符串回灌给模型，让它自己决定要不要改参重试。

### 3. Skill —— `skills.py` + `skills/*/SKILL.md`

Skill 就是一份 Markdown 操作手册，带 frontmatter：

```markdown
---
name: code-reviewer
description: 审查一段 Python 代码，按「严重/建议/风格」三档输出结构化问题清单。
---
## 步骤 ...
## 输出格式 ...
```

**渐进式披露（progressive disclosure）**是这里的核心：

- 平时只把 `name + description` 放进上下文（几十个 Skill 也只占几百 token）；
- 模型判断「这个任务归它管」时，才调 `load_skill` 把正文注入；
- 正文里有强制输出格式（比如必须给 JSON 数组），模型的输出因此可被程序解析。

自带两个：`code-reviewer`（代码审查）、`sql-explain`（SQL 执行计划解读）。

### 4. MCP —— `mcp_client.py`

MCP（Model Context Protocol）用 JSON-RPC 2.0 走 stdio，把外部能力标准化。
客户端自己实现，不依赖官方 SDK：

```
client → initialize          （协商协议版本与能力）
client → notifications/initialized
client → tools/list          （发现能力，支持 cursor 分页）
client → tools/call          （调用，结果在 result.content[].text）
```

实测连上官方 `@modelcontextprotocol/server-filesystem`，发现 14 个工具
（`read_text_file`、`list_directory`、`search_files`、`get_file_info` …）。

agent 只挂**只读子集**，并且加上 `mcp_` 前缀避免和本地工具重名：

```python
agent.attach_mcp(mcp_client, allow={
    "list_allowed_directories", "list_directory", "read_text_file",
    "get_file_info", "search_files", "directory_tree"})
```

## 目录结构

按「库 / 入口 / 开发工具」分层。`src/` 只放被 import 的模块，`cli/` 只放能直接运行的
脚本——想找「从哪开始跑」，看 `cli/` 就够了。

```
src/                    库（被 import，不直接运行）
  paths.py              全项目唯一的路径定义，其余模块都从这里取
  llm.py                OpenAI 兼容客户端（流式 / tool_calls / 重试）
  agent.py              ReAct 循环、工具路由、MCP 挂载
  tools.py              本地工具注册表 + JSON Schema 生成
  skills.py             Skill 扫描与渐进式加载
  taskdata.py           任务数据层：校验 / 视图 / 清单渲染 / 提醒计算
  mcp_client.py         MCP 客户端（stdio + JSON-RPC）
  mock_llm.py           假模型服务（离线自测用）
  console_io.py         输出编码适配（控制台 GBK / 管道 UTF-8）

cli/                    入口（直接运行）
  _bootstrap.py         把 src/ 加进 sys.path，不依赖 PYTHONPATH
  chat.py               最小示例：一次问答
  demo.py               四个能力的演示（基础 / 工具 / Skill / MCP）
  checklist.py          任务数据：--check / --today / --reminders / --ask
  env_report.py         环境自检
  selftest.py           链路自检：离线 8 项 + 可选真实调用

data/                   样例数据：tasks.json / tasks_dirty.json
skills/                 示例 Skill：code-reviewer / sql-explain
docs/                   项目说明书（交接文档）
dev/                    开发工具：console_shot.ps1（抓真实窗口截图）
screenshots/            运行截图
run.ps1                 一键运行入口（自动把 demo.py 映射到 cli/demo.py）
.mcp/  .venv/           依赖目录，可随时删除重装
```

`cli/` 下每个脚本第一行都是 `import _bootstrap`，它把 `src/` 塞进 `sys.path`。
所以命令行、Code Runner、双击运行三种方式都能找到库模块，不需要配环境变量。

## 任务数据层

这一层只解决「数据要说清楚」，**不接任何平台**。将来接飞书，只换「渲染 + 发送」，
数据结构、校验规则、逾期判定一行都不用改。

```powershell
.\run.ps1 checklist.py                                # 渲染清单
.\run.ps1 checklist.py --today                        # 今天要做什么（含逾期）
.\run.ps1 checklist.py --reminders                    # 未来 7 天的提醒计划
.\run.ps1 checklist.py --check                        # 只校验数据
.\run.ps1 checklist.py --at "2026-11-10 08:00"        # 固定“现在”，复现判定
.\run.ps1 checklist.py --file data/tasks_dirty.json --check   # 看校验器怎么报错
.\run.ps1 checklist.py --ask "我今天该先做哪件事"      # 交给模型用任务工具回答
```

### 数据格式（`data/tasks.json`）

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✓ | 唯一标识，重复会被拦下 |
| `title` | ✓ | 一句话任务名 |
| `due` | ✓ | `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM` |
| `priority` | | `P0` / `P1` / `P2`，默认 P2 |
| `status` | | `todo` / `doing` / `done`，默认 todo |
| `start` | | 可选，不能晚于 `due` |
| `estimate_hours` | | 可选，超出 0~8 只警告不拦截 |
| `source` / `note` / `link` | | 自由文本 |

`data/tasks_dirty.json` 是故意写脏的数据（重复 ID、错误枚举、错日期、缺字段、start>due），
用来验证校验器——`--check` 会一条条指出问题，而不是笼统地说「数据有误」。

### 两个关键设计

1. **逾期按时刻算，不是按日期**。17:00 到期的任务，18:00 就该进「逾期」。
   最初写成按日期比较，结果模型和清单都把「今天 17:00 已过期」判成了「今天到期」。
2. **「现在」从参数注入**（`--at`）。否则「逾期 2 天」这种结论没法复现、没法测，
   回归时谁也不知道当时是什么时间点。

### Agent 工具

注册了 4 个：`list_my_tasks` / `my_checklist` / `finish_task` / `pending_reminders`。
`--ask` 就是让模型用这些工具回答，调度逻辑完全复用 `agent.py`，没有另写一套。

### 提醒规则

截止前 1 天 09:00 提醒一次 + 截止当天 09:00 再提醒一次；**已经过去的时间点直接跳过**，
不补发——攒一堆过期提醒比不发更烦人。接入平台时，把 `upcoming_reminders()` 的输出
交给定时任务即可。

## 为什么不用现成 SDK

这是练手项目的取舍，不是教条：

- **好处**：请求体、SSE 分帧、`tool_calls` 增量拼装、重试策略全在明面上，
  换厂商只改 base_url，不会被 SDK 的版本和抽象层绊住；
- **代价**：函数调用、图片输入、结构化输出这些高级特性要自己补；
  生产项目仍建议用官方 SDK。

## 调试

先跑自检，再花钱调模型。`selftest.py` 把链路拆成 8 个独立检查点（`--online` 再加 1 个），
各自捕获异常、各自报告——失败的那一项就是该看的地方，不会因为一个错拖垮整轮。

```powershell
.\run.ps1 selftest.py            # 离线：不花 token，约 3 秒
.\run.ps1 selftest.py --online   # 额外做一次真实调用，验证 key 与网络
```

| 检查点 | 覆盖什么 |
|---|---|
| 运行环境 | Python 版本、是否在 venv 内 |
| 依赖 httpx | 版本可用 |
| 配置 .env | key 是否就绪（只显示掩码） |
| 本地工具 | 计算结果、时间、**路径穿越防护**、未知工具报错 |
| Skill 加载 | 扫描、正文注入、目录进 system prompt |
| 工具 Schema | 每个工具的 name/description/parameters 结构合法 |
| 任务数据 | 样例数据零错误；逾期/今天/提醒分类可复现；脏数据必须报错 |
| MCP 握手 | 连接官方 server、发现工具、真实调用一次 |

## 验证记录

### 真实模型（DeepSeek `deepseek-chat`，2026-11-08）

| 场景 | 工具调用 | 耗时 | 结果 |
|---|---|---|---|
| 1 基础对话 | 0 | 1.61s | 正常返回，流式输出与用量统计正确 |
| 2 本地工具 | 2（一步内并行） | 2.11s | 模型自行调 `get_current_time` + `calculator`，算出 1488.77 元 |
| 3 Skill | 2（`load_skill` → `read_file`） | 7.94s | 按 Skill 规定的 JSON 格式输出 5 条审查意见，含 1 条 P0 |
| 4 MCP | 3（MCP 与本地工具混用） | 3.88s | 先查 allowed directories，再列目录、读文件，全部来自 MCP |

### 离线 / 单元验证

| 验证项 | 方法 | 结果 |
|---|---|---|
| 真实端点连通 | 假 key 打 `api.deepseek.com` | `HTTP 401`，正确解析服务端 error 与 request_id |
| SSE 解析 | 本地 mock 分片推送 | 正文/用量/首字延迟全部正确 |
| 流式 `tool_calls` 拼装 | mock 把 id/name/arguments 拆 4 片 | 名称、参数、调用链正确（这里踩过坑，见下） |
| 本地工具 | `tools.py` 自测 | 计算、时间、列目录、读文件均通过 |
| Skill 加载 | `skills.py` 自测 | 扫描到 2 个，正文正确注入 |
| MCP 握手 | 连官方 filesystem server | 识别 `secure-filesystem-server v0.2.0`，14 个工具 |

### 踩过的坑（值得记下来）

1. **流式 `tool_calls` 归一化**：分片累积出来的是扁平槽位 `{id,name,arguments}`，
   而 `_normalize_tool_calls` 期望 OpenAI 的包装格式 `{id,function:{name,arguments}}`，
   直接传进去会导致工具名为空、参数变 `{}`。表现是「模型一直在调一个不存在的工具」，
   循环跑满 max_steps。修法：消费流时先转成包装格式再归一化。
2. **PowerShell 5.1 把无 BOM 的 UTF-8 脚本当 GBK**：中文注释会把括号吃掉导致语法错误。
   `.ps1` 要么存成纯 ASCII，要么带 BOM。
3. **`.format()` 里的 `{R}`（ANSI 重置色）** 会被当成占位符，抛 `KeyError: 'R'`。
   含 ANSI 常量的字符串统一改用 f-string。
4. **模型吐的 tool arguments 里可能有裸换行**（JSON 要求 `\n`，模型常直接敲回车），
   `json.loads` 严格模式会整段失败。用 `strict=False` 容错，仍失败才降级成 `__raw__`。
5. **`max_tokens` 太小时结构化输出会被截断**，表现却是「工具参数不是合法 JSON」，
   极难排查。现在默认 8192，并在 `finish_reason == "length"` 时打印明确警告。

## 截图清单

按顺序跑，每张对应一个能力：

| 截图 | 命令 | 展示内容 |
|---|---|---|
| 环境搭建 | `.\run.ps1 env_report.py --no-tree` | Python/httpx/Node/npm/MCP 就绪状态、key 掩码 |
| 基础调用 | `.\run.ps1 chat.py "用一句话解释什么是 MCP"` | 流式逐字输出 + token 用量 |
| 工具调用 | `.\run.ps1 demo.py --only 2` | 模型自主调计算器与时间工具 |
| Skill | `.\run.ps1 demo.py --only 3` | 渐进式加载 + 按 Skill 格式输出审查结果 |
| MCP | `.\run.ps1 demo.py --only 4` | 调用官方 filesystem server |
| 汇总 | `.\run.ps1 demo.py` | 四场景 + 汇总表 |

> 场景 3 输出较长（含完整 JSON），建议截图前把终端窗口调高，或只截后半段最终回答。

## 排错

| 现象 | 原因 |
|---|---|
| `HTTP 401` | key 错、带空格、已失效 |
| `HTTP 402` | 余额不足 |
| `HTTP 429` | 限流，脚本会自动退避重试 3 次 |
| 中文乱码 | 用 `run.ps1` 跑，它切了 UTF-8；或 `chcp 65001` |
| `MCP server 已退出` | 没装 `.mcp/`，执行文首的 npm install |
| `没有拿到 MCP 工具` | Node 不在 PATH，或 `.mcp` 路径被移动 |

## 安全

- key 只从 `.env` / 环境变量读，代码里没有硬编码；`.env` 在 `.gitignore` 里
- 截图里的 key 一律掩码（`env_report.py` 只打印 `sk-abc***1234`）
- 文件类工具限制在工作区内，MCP 只挂只读工具
