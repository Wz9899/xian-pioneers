# -*- coding: utf-8 -*-
"""dev/task_api.py — 一个「内部任务服务」（真实 HTTP 服务，非模拟）。

为什么需要它：作业要求接「真实工具」，搜索之外再补一个**内部 API** 更有说服力——
它展示了跨进程 HTTP 调用、鉴权头、超时处理、错误分支，而不是一个本地函数。

接口：
    GET  /tasks?status=todo     列出任务
    GET  /stats                 统计
    POST /tasks/{id}/done       标记完成

鉴权：请求头必须带 `X-Api-Token: dev-token-123`

启动：
    .\\.venv\\Scripts\\python.exe dev\\task_api.py
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

import taskdata  # noqa: E402

PORT = 8765
TOKEN = "dev-token-123"


class Handler(BaseHTTPRequestHandler):
    server_version = "TaskAPI/1.0"

    def log_message(self, *args):        # 静音，避免刷屏
        pass

    # ------------------------------------------------------------ 基础

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if self.headers.get("X-Api-Token") != TOKEN:
            self._json(401, {"error": "invalid or missing X-Api-Token"})
            return False
        return True

    def _tasks(self):
        tasks, errors, _ = taskdata.load_tasks()
        if errors:
            self._json(500, {"error": "task data invalid", "details": errors})
            return None
        return tasks

    # ------------------------------------------------------------ 路由

    def do_GET(self):
        if not self._auth():
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/tasks":
            tasks = self._tasks()
            if tasks is None:
                return
            status = (query.get("status") or [""])[0]
            if status:
                tasks = [t for t in tasks if t["status"] == status]
            self._json(200, {"count": len(tasks), "tasks": [{
                "id": t["id"], "title": t["title"], "priority": t["priority"],
                "status": t["status"], "due": taskdata.fmt_due(t["due_dt"]),
            } for t in tasks]})
            return

        if parsed.path == "/stats":
            tasks = self._tasks()
            if tasks is None:
                return
            self._json(200, {
                "total": len(tasks),
                "todo": sum(1 for t in tasks if t["status"] == "todo"),
                "doing": sum(1 for t in tasks if t["status"] == "doing"),
                "done": sum(1 for t in tasks if t["status"] == "done"),
            })
            return

        self._json(404, {"error": "not found", "path": parsed.path})

    def do_POST(self):
        if not self._auth():
            return
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        # /tasks/{id}/done
        if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "done":
            tasks = self._tasks()
            if tasks is None:
                return
            task_id = parts[1]
            target = next((t for t in tasks if t["id"] == task_id), None)
            if target is None:
                self._json(404, {"error": "task not found", "id": task_id})
                return
            if target["status"] == "done":
                self._json(200, {"ok": True, "changed": False, "id": task_id})
                return
            target["status"] = "done"
            target["done_at"] = taskdata.now_local().strftime("%Y-%m-%d %H:%M")
            taskdata.save_tasks(tasks)
            self._json(200, {"ok": True, "changed": True, "id": task_id,
                             "title": target["title"]})
            return

        self._json(404, {"error": "not found", "path": parsed.path})


class TaskAPIServer:
    """可以在进程内启动，方便 demo 脚本一键跑起来。"""

    def __init__(self, port=PORT):
        self.port = port
        self.httpd = None

    def start(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        import threading
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    @property
    def base_url(self):
        return "http://127.0.0.1:{0}".format(self.port)

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


if __name__ == "__main__":
    server = TaskAPIServer().start()
    print("内部任务服务已启动：{0}".format(server.base_url))
    print("鉴权头：X-Api-Token: {0}".format(TOKEN))
    print("试试：curl -H \"X-Api-Token: {0}\" {1}/tasks?status=todo".format(TOKEN, server.base_url))
    try:
        import threading
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()
