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
    args = parser.parse_args()

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
