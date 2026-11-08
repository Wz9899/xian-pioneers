# -*- coding: utf-8 -*-
"""taskdata.py — 任务数据层：模型 / 校验 / 视图 / 清单渲染 / 提醒计算。

设计上刻意**不接任何平台**。数据结构和校验规则先在本地立住，将来接飞书或企业微信，
只需要换掉「渲染 + 发送」那一层，这里一行都不用改。

时间一律通过参数传入（now），不直接读系统时钟——否则「逾期判定」这种逻辑没法测。
"""
import json
import os
from datetime import date, datetime, time, timedelta, timezone

from paths import DATA_DIR, PROJECT_ROOT
from tools import tool

ROOT = PROJECT_ROOT
TASKS_PATH = os.path.join(DATA_DIR, "tasks.json")

TZ = timezone(timedelta(hours=8))
PRIORITIES = ("P0", "P1", "P2")
STATUSES = ("todo", "doing", "done")
WEEKDAYS = "一二三四五六日"
PRIORITY_ORDER = {p: i for i, p in enumerate(PRIORITIES)}


def now_local():
    return datetime.now(TZ)


def parse_dt(value):
    """支持 'YYYY-MM-DD' 与 'YYYY-MM-DD HH:MM' 两种写法。"""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=TZ)
        except (AttributeError, ValueError):
            continue
    raise ValueError("无法解析的日期：{0!r}（应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM）".format(value))


# ------------------------------------------------------------------ 校验

def validate(raw):
    """返回 (normalized_tasks, errors, warnings)。errors 非空时不该继续往下渲染。"""
    errors, warnings = [], []

    if isinstance(raw, dict):
        items = raw.get("tasks")
    else:
        items = raw
    if not isinstance(items, list) or not items:
        return [], ["数据里没有 tasks 数组，或它是空的"], warnings

    seen = {}
    normalized = []
    today = now_local().date()

    for index, item in enumerate(items, start=1):
        where = "tasks[{0}]".format(index)
        if not isinstance(item, dict):
            errors.append("{0} 不是对象".format(where))
            continue

        task = dict(item)
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            errors.append("{0} 缺少 id".format(where))
            continue
        if task_id in seen:
            errors.append("{0} 的 id「{1}」与 tasks[{2}] 重复".format(where, task_id, seen[task_id]))
        seen[task_id] = index
        task["id"] = task_id

        title = str(task.get("title") or "").strip()
        if not title:
            errors.append("{0}（{1}）缺少 title".format(where, task_id))
        task["title"] = title

        task["priority"] = str(task.get("priority") or "P2").strip().upper()
        if task["priority"] not in PRIORITIES:
            errors.append("{0}（{1}）的 priority「{2}」不合法，应为 P0/P1/P2".format(
                where, task_id, task["priority"]))
            task["priority"] = "P2"

        task["status"] = str(task.get("status") or "todo").strip().lower()
        if task["status"] not in STATUSES:
            errors.append("{0}（{1}）的 status「{2}」不合法，应为 todo/doing/done".format(
                where, task_id, task["status"]))
            task["status"] = "todo"

        # 截止时间
        try:
            task["due_dt"] = parse_dt(task.get("due"))
        except ValueError as exc:
            errors.append("{0}（{1}）{2}".format(where, task_id, exc))
            task["due_dt"] = None

        # 开始时间（可选），不能晚于截止
        start_raw = task.get("start")
        if start_raw:
            try:
                task["start_dt"] = parse_dt(start_raw)
                if task["due_dt"] and task["start_dt"] > task["due_dt"]:
                    errors.append("{0}（{1}）的 start 晚于 due".format(where, task_id))
            except ValueError as exc:
                errors.append("{0}（{1}）start {2}".format(where, task_id, exc))

        # 预估工时（可选）
        if task.get("estimate_hours") is not None:
            try:
                hours = float(task["estimate_hours"])
                if not 0 < hours <= 8:
                    warnings.append("{0}（{1}）estimate_hours={2} 超出合理范围 0~8".format(
                        where, task_id, hours))
                task["estimate_hours"] = hours
            except (TypeError, ValueError):
                warnings.append("{0}（{1}）estimate_hours 不是数字，已忽略".format(where, task_id))
                task.pop("estimate_hours", None)

        for key in ("source", "link", "note"):
            if task.get(key):
                task[key] = str(task[key]).strip()

        task["done_at"] = task.get("done_at") or None
        if task["status"] == "done" and not task["done_at"]:
            warnings.append("{0}（{1}）状态是 done 但没有 done_at，建议补上完成时间".format(
                where, task_id))
        normalized.append(task)

    return normalized, errors, warnings


def load_tasks(path=None):
    """读取并校验。返回 (tasks, errors, warnings)。"""
    path = path or TASKS_PATH
    if not os.path.exists(path):
        return [], ["文件不存在：{0}".format(path)], []
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = json.load(f)
    return validate(raw)


def save_tasks(tasks, path=None):
    path = path or TASKS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"owner": "我", "synced_at": now_local().strftime("%Y-%m-%d %H:%M:%S"),
               "tasks": []}
    for task in tasks:
        item = dict(task)
        item.pop("due_dt", None)          # 派生字段不落盘
        item.pop("start_dt", None)
        payload["tasks"].append(item)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


# ------------------------------------------------------------------ 视图

def classify(tasks, now=None):
    """把任务分到 逾期 / 今天 / 接下来 / 已完成 四个桶。"""
    now = now or now_local()
    today = now.date()
    buckets = {"overdue": [], "today": [], "upcoming": [], "done": []}

    for task in tasks:
        if task["status"] == "done":
            buckets["done"].append(task)
            continue
        due = task["due_dt"]
        if due is None:                   # 校验已报错，这里兜底
            buckets["upcoming"].append(task)
        elif due < now:                   # 按时刻判，不是按日期：17:00 到期、18:00 就是逾期
            buckets["overdue"].append(task)
        elif due.date() == today:
            buckets["today"].append(task)
        else:
            buckets["upcoming"].append(task)

    for name in buckets:
        buckets[name].sort(key=lambda t: (
            PRIORITY_ORDER.get(t["priority"], 9),
            t["due_dt"] or datetime.max.replace(tzinfo=TZ),
            t["id"],
        ))
    return buckets


def render_checklist(tasks, now=None, title="我的任务清单"):
    """渲染成 Markdown 勾选清单。"""
    now = now or now_local()
    buckets = classify(tasks, now)
    today = now.date()

    total = len(tasks)
    done = len(buckets["done"])
    lines = [
        "# {0}".format(title),
        "",
        "生成于 {0}（周{1}）".format(
            now.strftime("%Y-%m-%d %H:%M"), WEEKDAYS[now.weekday()]),
        "",
        "## 概览",
        "",
        "- 共 {0} 条：待办 {1} · 进行中 {2} · 已完成 {3}".format(
            total,
            sum(1 for t in tasks if t["status"] == "todo"),
            sum(1 for t in tasks if t["status"] == "doing"),
            done),
        "- 进度 {0:.0f}%（{1}/{2}）".format(100.0 * done / total if total else 0, done, total),
    ]
    if buckets["overdue"]:
        lines.append("- **逾期 {0} 条**".format(len(buckets["overdue"])))

    def remaining(due):
        """把「距截止还有多久」说成人话；逾期按小时/天分级。"""
        delta = due - now
        total_hours = delta.total_seconds() / 3600.0
        if total_hours < 0:
            hours = -total_hours
            return "**逾期 {0:.0f} 小时**".format(hours) if hours < 24 \
                else "**逾期 {0:.0f} 天**".format(hours / 24.0)
        if due.date() == today:
            return "今天 {0} 到期".format(due.strftime("%H:%M"))
        if total_hours < 24:
            return "还剩 {0:.0f} 小时".format(total_hours)
        return "还剩 {0} 天".format((due.date() - today).days)

    def section(heading, items, marker=""):
        if not items:
            return
        lines.extend(["", "## " + heading, ""])
        for task in items:
            checkbox = "x" if task["status"] == "done" else " "
            due = task["due_dt"]
            suffix = ""
            if due and marker and task["status"] != "done":
                suffix = "　" + remaining(due)
            lines.append("- [{0}] **{1} {2}**　`{3}`　截止 {4}{5}".format(
                checkbox, task["id"], task["title"], task["priority"],
                due.strftime("%Y-%m-%d %H:%M") if due else "未设置", suffix))
            if task.get("note"):
                lines.append("  - " + task["note"])
            if task.get("source"):
                lines.append("  - 来源：" + task["source"])

    section("逾期（{0}）".format(len(buckets["overdue"])), buckets["overdue"], marker="yes")
    section("今天（{0}）".format(len(buckets["today"])), buckets["today"], marker="yes")
    section("接下来（{0}）".format(len(buckets["upcoming"])), buckets["upcoming"], marker="yes")
    section("已完成（{0}）".format(len(buckets["done"])), buckets["done"])

    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 提醒

def upcoming_reminders(tasks, now=None, days=7, lead_days=1, hour=9):
    """算未来若干天要发哪些提醒。返回 [{task_id, title, due, remind_at, kind}]。

    规则：截止前 lead_days 天的 hour 点提醒一次 + 截止当天 hour 点再提醒一次。
    已经过去的时间点直接跳过，不补发——补发一堆过期提醒比不发更烦人。
    """
    now = now or now_local()
    horizon = now + timedelta(days=days)
    reminders = []

    for task in tasks:
        due = task.get("due_dt")
        if task["status"] == "done" or due is None:
            continue
        if due > horizon:
            continue

        for kind, offset in (("提前提醒", lead_days), ("今天到期", 0)):
            remind_at = datetime.combine(due.date(), time(hour=hour), TZ) - timedelta(days=offset)
            if remind_at <= now:
                continue
            reminders.append({
                "task_id": task["id"],
                "title": task["title"],
                "due": due.strftime("%Y-%m-%d %H:%M"),
                "remind_at": remind_at.strftime("%Y-%m-%d %H:%M"),
                "kind": kind,
                "_at": remind_at,
            })

    reminders.sort(key=lambda r: (r["_at"], r["task_id"]))
    for item in reminders:
        item.pop("_at", None)
    return reminders


# ------------------------------------------------------------------ Agent 工具

def _pending(tasks):
    return [t for t in tasks if t["status"] != "done"]


@tool(
    name="list_my_tasks",
    description="列出我的任务。可按状态或优先级过滤，用于回答「我还有哪些事没做」。",
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["", "todo", "doing", "done"],
                       "description": "留空表示全部"},
            "priority": {"type": "string", "enum": ["", "P0", "P1", "P2"],
                         "description": "留空表示全部"},
        },
        "required": [],
    },
)
def list_my_tasks(status="", priority=""):
    tasks, errors, _ = load_tasks()
    if errors:
        return "任务数据有问题：\n" + "\n".join("- " + e for e in errors)

    selected = tasks
    if status:
        selected = [t for t in selected if t["status"] == status]
    if priority:
        selected = [t for t in selected if t["priority"] == priority.upper()]
    if not selected:
        return "没有符合条件的任务。"

    lines = ["共 {0} 条：".format(len(selected))]
    for task in selected:
        lines.append("- [{0}] {1} {2}（{3}，截止 {4}）".format(
            task["status"], task["id"], task["title"], task["priority"],
            task["due_dt"].strftime("%Y-%m-%d %H:%M") if task["due_dt"] else "未设置"))
    return "\n".join(lines)


@tool(
    name="my_checklist",
    description="生成我当前的任务清单（按逾期/今天/接下来/已完成分组）。用户问「今天做什么 / 我的清单」时调用。",
    parameters={"type": "object", "properties": {}, "required": []},
)
def my_checklist():
    tasks, errors, _ = load_tasks()
    if errors:
        return "任务数据有问题：\n" + "\n".join("- " + e for e in errors)
    return render_checklist(tasks)


@tool(
    name="finish_task",
    description="把某个任务标记为已完成，写回数据文件。",
    parameters={
        "type": "object",
        "properties": {"task_id": {"type": "string", "description": "任务 ID，如 T3"}},
        "required": ["task_id"],
    },
)
def finish_task(task_id):
    tasks, errors, _ = load_tasks()
    if errors:
        return "任务数据有问题，先修数据：\n" + "\n".join("- " + e for e in errors)

    target = None
    for task in tasks:
        if task["id"] == task_id:
            target = task
            break
    if target is None:
        return "找不到任务 {0}。当前任务：{1}".format(
            task_id, "、".join(t["id"] for t in tasks))
    if target["status"] == "done":
        return "任务 {0} 已经是完成状态。".format(task_id)

    target["status"] = "done"
    target["done_at"] = now_local().strftime("%Y-%m-%d %H:%M")
    save_tasks(tasks)
    done = sum(1 for t in tasks if t["status"] == "done")
    return "已把 {0}「{1}」标记为完成。进度 {2}/{3}。".format(
        task_id, target["title"], done, len(tasks))


@tool(
    name="pending_reminders",
    description="列出未来几天的提醒计划（什么时间提醒哪件事），用于回答「接下来要提醒我什么」。",
    parameters={
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "看未来几天，默认 7"}
        },
        "required": [],
    },
)
def pending_reminders(days=7):
    tasks, errors, _ = load_tasks()
    if errors:
        return "任务数据有问题：\n" + "\n".join("- " + e for e in errors)
    items = upcoming_reminders(tasks, days=int(days))
    if not items:
        return "未来 {0} 天没有需要提醒的事项。".format(days)
    return "\n".join("{0}　{1}　{2}（{3}）".format(
        r["remind_at"], r["task_id"], r["title"], r["kind"]) for r in items)
