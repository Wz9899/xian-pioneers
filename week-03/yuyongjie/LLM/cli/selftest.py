# -*- coding: utf-8 -*-
"""selftest.py — 离线自检。不调模型、不烧 token，用来确认链路的每一段都是好的。

    .\\run.ps1 selftest.py            # 全部离线检查（约 3 秒）
    .\\run.ps1 selftest.py --online   # 额外做一次真实 API 调用

设计意图：调试一个 agent 项目时，「跑一下看有没有报错」太粗了——出错你也不知道
是哪一段坏。这里把链路拆成 8 个独立检查点（`--online` 再加 1 个），每个单独捕获
异常并单独报告，失败的那个检查点就是你要看的地方。
"""
import argparse
import os
import sys
import traceback

import _bootstrap          # noqa: F401 — 把 src/ 加入 sys.path
import console_io

ROOT = _bootstrap.PROJECT_ROOT

OK, SKIP = "PASS", "SKIP"
results = []


def check(title):
    """装饰器：注册一个检查点，异常不影响后面的检查。"""
    def decorator(fn):
        results.append((title, fn))
        return fn
    return decorator


# ------------------------------------------------------------------ 检查点

@check("运行环境")
def _env():
    import platform
    if sys.version_info < (3, 8):
        raise AssertionError("需要 Python 3.8+，当前 " + platform.python_version())
    if sys.prefix == sys.base_prefix:
        raise AssertionError("不在虚拟环境里运行: " + sys.prefix)
    return "Python {0} | venv {1}".format(platform.python_version(), sys.prefix)


@check("依赖 httpx")
def _deps():
    import httpx
    return "httpx " + httpx.__version__


@check("配置 .env")
def _config():
    import llm
    llm.load_env(os.path.join(ROOT, ".env"))
    key = (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key or "在此填写" in key:
        return None, "未配置 key（离线检查不受影响，--online 会跳过）"
    return "key={0} | model={1}".format(
        llm.redact(key), os.environ.get("LLM_MODEL") or os.environ.get("MODEL") or "?")


@check("本地工具")
def _tools():
    import tools
    calc = tools.call("calculator", {"expression": "1250 * 1.06 ** 3"})
    if "1488.77" not in calc:
        raise AssertionError("calculator 结果不对：" + calc)

    now = tools.call("get_current_time", {})
    if ":" not in now:
        raise AssertionError("get_current_time 返回异常：" + now)

    escape = tools.call("read_file", {"path": "../../windows/win.ini"})
    if not escape.startswith("错误"):
        raise AssertionError("路径穿越没被拦住：" + escape)

    unknown = tools.call("not_a_tool", {})
    if "不存在" not in unknown:
        raise AssertionError("未知工具没有友好报错：" + unknown)

    return "calculator / get_current_time / 路径防护 / 未知工具 均正常"


@check("Skill 加载")
def _skills():
    import skills
    if "code-reviewer" not in skills.SKILLS:
        raise AssertionError("没扫到 code-reviewer，当前：" + ", ".join(skills.SKILLS))
    body = skills.load_skill("code-reviewer")
    if "SKILL BEGIN" not in body or "步骤" not in body:
        raise AssertionError("Skill 正文没有正确注入")
    catalog = skills.catalog()
    if "code-reviewer" not in catalog:
        raise AssertionError("Skill 目录没进 system prompt")
    return "扫到 {0} 个 Skill，正文与目录均正常".format(len(skills.SKILLS))


@check("工具 Schema")
def _schemas():
    import tools
    schemas = tools.schemas()
    if not schemas:
        raise AssertionError("没有生成任何 tool schema")

    names = set()
    for item in schemas:
        if item.get("type") != "function":
            raise AssertionError("schema 结构不是 function：" + str(item)[:80])
        fn = item.get("function") or {}
        for field in ("name", "description", "parameters"):
            if not fn.get(field):
                raise AssertionError("schema 缺少字段 {0}：{1}".format(field, fn.get("name")))
        if (fn["parameters"] or {}).get("type") != "object":
            raise AssertionError("parameters 必须是 object：" + fn["name"])
        names.add(fn["name"])

    for required in ("calculator", "get_current_time", "load_skill"):
        if required not in names:
            raise AssertionError("缺少内置工具 " + required)
    return "{0} 个工具 schema 结构合法".format(len(schemas))


@check("Prompt 模块")
def _prompts():
    import prompts

    variants = {s: prompts.build_parse_messages("明天交作业", "2026-11-15", s)
                for s in ("zero-shot", "few-shot", "cot")}
    zero = variants["zero-shot"][0]["content"]
    few = variants["few-shot"][0]["content"]
    cot = variants["cot"][0]["content"]

    if "示例" not in few or len(few) <= len(zero):
        raise AssertionError("few-shot 没有比 zero-shot 多出示例")
    if "reasoning" not in cot:
        raise AssertionError("cot 变体缺少推理要求")
    if "2026-11-15" not in zero:
        raise AssertionError("system prompt 没有注入今天日期")

    if prompts.extract_json('```json\n{"a": 1}\n```') != {"a": 1}:
        raise AssertionError("extract_json 处理不了代码块包裹")
    if prompts.extract_json("好的，结果如下：{\"a\": 2} 以上") != {"a": 2}:
        raise AssertionError("extract_json 处理不了前后缀噪声")
    if prompts.extract_json("没有 JSON") is not None:
        raise AssertionError("extract_json 对无 JSON 的输入应返回 None")

    golden = prompts.render_golden("明天交作业")
    if "自检" not in golden[0]["content"]:
        raise AssertionError("黄金公式缺少自检段")

    return "三个变体差异正确；extract_json 三种输入均通过"


@check("真实工具注册")
def _real_tools():
    """第三周的两个真实工具：一个外网搜索、一个内部 HTTP 服务。

    这里只验证「注册 + 失败路径」——不联网、不依赖服务在线，
    所以可以在离线自检里跑。
    """
    import tools
    import tools_api   # noqa: F401 — import 即注册
    import tools_web   # noqa: F401

    names = set(tools.names())
    for required in ("web_search", "internal_task_api"):
        if required not in names:
            raise AssertionError("没注册工具 " + required)

    # 服务未启动时必须是可操作的提示，而不是抛异常
    message = tools.call("internal_task_api", {"action": "stats"})
    if not isinstance(message, str) or not message:
        raise AssertionError("内部服务离线时应返回字符串提示")
    if "未启动" not in message and "统计" not in message:
        raise AssertionError("内部服务离线时的提示不清楚：" + message[:80])

    # 非法 action 要被拦住
    bad = tools.call("internal_task_api", {"action": "delete_all"})
    if "只能是" not in bad:
        raise AssertionError("非法 action 没有被拦截：" + bad[:80])

    return "web_search / internal_task_api 已注册，失败路径返回友好提示"


@check("飞书工具")
def _feishu():
    """离线验证卡片结构与签名，不发任何真实请求。"""
    import os as _os
    import tools_feishu

    card = tools_feishu.build_card("本周待办", "- **T3** 补验证")
    if card["header"]["title"]["content"] != "本周待办":
        raise AssertionError("卡片标题没写进 header")
    if card["elements"][0]["text"]["tag"] != "lark_md":
        raise AssertionError("正文不是 lark_md 类型")

    payload = tools_feishu.build_payload("t", "c", secret="s", now=1700000000)
    if payload["timestamp"] != "1700000000" or len(payload["sign"]) < 10:
        raise AssertionError("加签结果不对：{0}".format(payload.get("sign")))

    # 未配置 webhook 时必须是可操作的提示，而不是抛异常
    saved = _os.environ.pop("FEISHU_WEBHOOK", None)
    try:
        message = tools_feishu.feishu_notify("t", "c")
    finally:
        if saved is not None:
            _os.environ["FEISHU_WEBHOOK"] = saved
    if "未配置" not in message and "已推送" not in message:
        raise AssertionError("未配置时的提示不清楚：" + message[:80])

    return "卡片结构 / 加签 / 未配置提示 均正常"


@check("工具裁剪")
def _profiles():
    """任务 agent 不该被塞进开发者工具——这既是上下文成本，也是干扰。"""
    import taskdata   # noqa: F401
    import tools
    import tools_feishu   # noqa: F401
    import tools_web      # noqa: F401

    task = set(tools.names("task"))
    research = set(tools.names("task-research"))

    for required in ("my_checklist", "finish_task", "feishu_notify"):
        if required not in task:
            raise AssertionError("task profile 缺 " + required)

    for unwanted in ("calculator", "read_file", "web_search", "internal_task_api"):
        if unwanted in task:
            raise AssertionError("task profile 不该包含 {0}（开发者工具）".format(unwanted))

    if "web_search" not in research or "fetch_page" not in research:
        raise AssertionError("task-research profile 没带上联网工具")
    if not task.issubset(research):
        raise AssertionError("task-research 应当是 task 的超集")

    return "task {0} 个 / task-research {1} 个，裁剪正确".format(len(task), len(research))


@check("任务数据")
def _tasks():
    import taskdata
    tasks, errors, warnings = taskdata.load_tasks()
    if errors:
        raise AssertionError("样例数据校验失败：" + "；".join(errors))

    # 固定「现在」，让逾期/今天的判定可复现
    now = taskdata.parse_dt("2026-11-10 08:00")
    buckets = taskdata.classify(tasks, now)
    if not any(t["id"] == "T4" for t in buckets["overdue"]):
        raise AssertionError("逾期任务没被识别（期望 T4）")
    if not any(t["id"] == "T3" for t in buckets["today"]):
        raise AssertionError("今天到期没被识别（期望 T3）")

    markdown = taskdata.render_checklist(tasks, now)
    for keyword in ("## 逾期", "## 今天", "## 接下来", "## 已完成", "- [x]"):
        if keyword not in markdown:
            raise AssertionError("清单缺少「{0}」".format(keyword))

    reminders = taskdata.upcoming_reminders(tasks, now)
    if not reminders:
        raise AssertionError("没有算出任何提醒")
    if any(r["remind_at"] <= "2026-11-10 08:00" for r in reminders):
        raise AssertionError("算出了已经过去的提醒（不该补发）")

    # 脏数据必须被拦住
    import os as _os
    _, dirty_errors, _ = taskdata.load_tasks(
        _os.path.join(ROOT, "data", "tasks_dirty.json"))
    if len(dirty_errors) < 5:
        raise AssertionError("脏数据只报出 {0} 个错误，漏检了".format(len(dirty_errors)))

    return "{0} 条任务；逾期/今天/提醒分类正确；脏数据报出 {1} 个错误".format(
        len(tasks), len(dirty_errors))


@check("MCP 握手")
def _mcp():
    from mcp_client import filesystem_server
    client = filesystem_server(ROOT)
    try:
        client.start()
        discovered = client.list_tools()
        if len(discovered) < 5:
            raise AssertionError("只发现 {0} 个工具，疑似异常".format(len(discovered)))
        allowed = client.call_tool("list_allowed_directories", {})
        if "LLM" not in allowed:
            raise AssertionError("allowed directories 返回异常：" + allowed[:200])
        return "{0} v{1}，发现 {2} 个工具".format(
            client.server_info.get("name"), client.server_info.get("version"),
            len(discovered))
    finally:
        client.close()


@check("真实模型调用")
def _online():
    import llm
    client = llm.from_env(os.path.join(ROOT, ".env"))
    if not client.api_key or "在此填写" in client.api_key:
        return None, "未配置 key，跳过"
    try:
        response = client.chat(
            [{"role": "user", "content": "只回复两个字：收到"}], stream=False)
        content = (response.get("content") or "").strip()
        if not content:
            raise AssertionError("模型返回为空")
        usage = response.get("usage") or {}
        return "「{0}」 {1:.2f}s tokens={2}".format(
            content[:12], response["elapsed"], usage.get("total_tokens", "?"))
    finally:
        client.close()


# ------------------------------------------------------------------ 执行

def main():
    console_io.setup()

    parser = argparse.ArgumentParser(description="离线自检")
    parser.add_argument("--online", action="store_true", help="额外做一次真实 API 调用")
    parser.add_argument("--tools", action="store_true", help="只打印已注册的工具清单，不跑检查")
    args = parser.parse_args()

    if args.tools:
        import taskdata   # noqa: F401 — 触发注册
        import tools
        import tools_api   # noqa: F401
        import tools_feishu  # noqa: F401
        import tools_web   # noqa: F401
        import skills

        print()
        print("═" * 78)
        print("  已注册工具　共 {0} 个　（import 即注册，无需改 agent 代码）".format(len(tools.schemas())))
        print("═" * 78)
        for item in tools.schemas():
            fn = item["function"]
            print("  {0:<22} {1}".format(fn["name"], fn["description"].split("\n")[0][:44]))
        print()
        print("  已加载 Skill：{0}".format("、".join(skills.SKILLS) or "无"))
        print()
        print("  按场景裁剪（agent 只挂它需要的）：")
        for profile in ("task", "task-research", "dev"):
            selected = tools.names(profile)
            print("    {0:<14} {1:>2} 个　{2}".format(
                profile, len(selected), ", ".join(selected)))
        print()
        return 0

    selected = [item for item in results if item[0] != "真实模型调用"]
    if args.online:
        selected += [item for item in results if item[0] == "真实模型调用"]

    print()
    print("═" * 78)
    print("  链路自检（离线）" + ("" if not args.online else " + 线上调用"))
    print("═" * 78)

    passed = skipped = failed = 0
    failures = []
    total = len(selected)
    for index, (title, fn) in enumerate(selected, start=1):
        tag = "[{0}/{1}]".format(index, total)
        try:
            outcome = fn()
            if isinstance(outcome, tuple) and outcome[0] is None:
                skipped += 1
                print("{0} {1:<18} SKIP  {2}".format(tag, title, outcome[1]))
            else:
                passed += 1
                print("{0} {1:<18} PASS  {2}".format(tag, title, outcome))
        except Exception as exc:                 # 单个检查失败不该中断整轮
            failed += 1
            print("{0} {1:<18} FAIL  {2}".format(tag, title, exc))
            failures.append((title, exc, traceback.format_exc()))

    print("─" * 78)
    print("通过 {0} · 跳过 {1} · 失败 {2}".format(passed, skipped, failed))

    if failures:
        print()
        print("失败详情：")
        for title, exc, tb in failures:
            print("  [{0}] {1}: {2}".format(title, type(exc).__name__, exc))
            print("    " + tb.strip().splitlines()[-1].strip())
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
