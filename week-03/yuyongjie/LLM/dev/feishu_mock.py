# -*- coding: utf-8 -*-
"""dev/feishu_mock.py — 假飞书 webhook 接收端。

为什么需要：没有飞书账号 / 不想往真群里发消息时，用它验证「发送链路 + 卡片结构」。
它模拟真实接口的两个关键行为：
1. 接收 POST，打印收到的 JSON（就是真实飞书会收到的内容）；
2. 返回 `{"code": 0, "msg": "success"}`，让客户端走成功分支。

启动：
    .\\.venv\\Scripts\\python.exe dev\\feishu_mock.py
    # 另开一个终端：
    $env:FEISHU_WEBHOOK = "http://127.0.0.1:8766/webhook"
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8766


class Handler(BaseHTTPRequestHandler):
    server_version = "FeishuMock/1.0"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) or b"{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"__raw__": raw[:400].decode("utf-8", "replace")}

        card = payload.get("card") or {}
        title = ""
        try:
            title = card["header"]["title"]["content"]
        except (KeyError, TypeError):
            pass

        print()
        print("─" * 70)
        print("收到推送　{0}".format(self.path))
        print("  标题：{0}".format(title or "（无）"))
        print("  类型：{0}".format(payload.get("msg_type")))
        if payload.get("sign"):
            print("  签名：{0}…".format(payload["sign"][:12]))
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        print("  完整 payload（前 800 字）：")
        print(body[:800])
        sys.stdout.flush()

        result = json.dumps({"code": 0, "msg": "success", "data": {}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)


class FeishuMockServer:
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

    @property
    def webhook(self):
        return self.base_url + "/webhook"

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


if __name__ == "__main__":
    server = FeishuMockServer().start()
    print("假飞书 webhook 已启动：{0}".format(server.webhook))
    print("在另一个终端设置：$env:FEISHU_WEBHOOK = \"{0}\"".format(server.webhook))
    try:
        import threading
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()
