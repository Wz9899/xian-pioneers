# -*- coding: utf-8 -*-
"""tools_api.py — 真实工具之二：调用「内部任务服务」的 HTTP 接口。

与本地工具的区别（也是作业要求的「真实工具」）：
- 跨进程通信，走真实 HTTP 请求；
- 需要鉴权头 `X-Api-Token`；
- 服务没启动时要给出可操作的报错，而不是崩掉。

服务端见 `dev/task_api.py`。
"""
import os

import httpx

from tools import tool

API_BASE = os.environ.get("TASK_API_URL", "http://127.0.0.1:8765")
API_TOKEN = os.environ.get("TASK_API_TOKEN", "dev-token-123")

ACTIONS = ("list", "stats", "complete")


def _client():
    proxy = (os.environ.get("LLM_PROXY") or os.environ.get("HTTPS_PROXY")
             or os.environ.get("HTTP_PROXY") or "").strip() or None
    return httpx.Client(base_url=API_BASE, timeout=8.0, trust_env=False,
                        proxy=proxy, headers={"X-Api-Token": API_TOKEN})


@tool(
    name="internal_task_api",
    description=(
        "访问公司内部任务服务（HTTP）。action=list 列出任务，"
        "stats 看统计，complete 把某个任务标记完成（需要 task_id）。"
        "当用户问「内部系统里还有什么没做」或要求「在系统里标完成」时使用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS),
                       "description": "list / stats / complete"},
            "task_id": {"type": "string", "description": "action=complete 时必填，如 T3"},
            "status": {"type": "string", "description": "action=list 时可选，如 todo"},
        },
        "required": ["action"],
    },
)
def internal_task_api(action, task_id="", status=""):
    action = (action or "").strip().lower()
    if action not in ACTIONS:
        return "错误：action 只能是 {0}".format(" / ".join(ACTIONS))

    try:
        with _client() as client:
            if action == "list":
                response = client.get("/tasks", params={"status": status} if status else None)
            elif action == "stats":
                response = client.get("/stats")
            else:
                if not task_id:
                    return "错误：action=complete 时必须给 task_id。"
                response = client.post("/tasks/{0}/done".format(task_id))
    except httpx.ConnectError:
        return ("内部任务服务未启动。请先运行：\n"
                "  .\\.venv\\Scripts\\python.exe dev\\task_api.py")
    except httpx.TimeoutException:
        return "内部任务服务响应超时（8 秒）。"

    if response.status_code == 401:
        return "鉴权失败：X-Api-Token 不匹配，检查 dev/task_api.py 里的 TOKEN。"
    if response.status_code == 404:
        return "接口或任务不存在：{0}".format(response.text[:200])
    if response.status_code != 200:
        return "调用失败：HTTP {0} {1}".format(response.status_code, response.text[:200])

    data = response.json()

    if action == "list":
        if not data.get("tasks"):
            return "内部任务服务里没有符合条件的任务。"
        lines = ["内部任务服务返回 {0} 条：".format(data["count"])]
        for task in data["tasks"]:
            lines.append("  {0}　{1}　[{2}]　{3}　截止 {4}".format(
                task["id"], task["title"], task["status"],
                task["priority"], task["due"]))
        return "\n".join(lines)

    if action == "stats":
        return "内部任务统计：共 {total} 条，待办 {todo}，进行中 {doing}，已完成 {done}".format(**data)

    if data.get("changed"):
        return "已在内部系统中把 {0}「{1}」标记为完成。".format(data["id"], data.get("title", ""))
    return "{0} 之前就已经是完成状态。".format(data["id"])


if __name__ == "__main__":
    print(internal_task_api("stats"))
