"""内部 API 工具：启动一个真实的本地 HTTP 服务并让 Agent 通过 HTTP 调用。

很多 Agent 作业把「内部 API」写成直接读本地 JSON 的函数，这并不算真正接入了 API。
这里用标准库起了一个 HTTP 服务（零额外依赖、随机端口、后台线程），
暴露三个端点：

* ``GET /health``                     健康检查
* ``GET /kb/search?q=&top_k=``        公司知识库检索
* ``GET /fx?base=&target=&amount=``   内部结算汇率换算

Agent 侧通过 ``urllib`` 发真实 HTTP 请求，完整走一遍「网络调用 → JSON 解析」链路。
"""

from __future__ import annotations

import atexit
import json
import re
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from .base import ToolError


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    words = re.findall(r"[a-z0-9]+", text)
    han = re.findall(r"[一-鿿]", text)
    bigrams = ["".join(han[i : i + 2]) for i in range(len(han) - 1)]
    return words + han + bigrams


def _rank(query: str, docs: list[dict], top_k: int) -> list[dict]:
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return []
    scored: list[tuple[float, dict]] = []
    for doc in docs:
        body = doc.get("title", "") + doc.get("text", "")
        doc_tokens = set(_tokenize(body))
        overlap = len(q_tokens & doc_tokens)
        if overlap:
            scored.append((round(overlap / len(q_tokens), 4), doc))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {"id": d["id"], "title": d["title"], "text": d["text"], "score": score}
        for score, d in scored[:top_k]
    ]


class _Handler(BaseHTTPRequestHandler):
    server_version = "ReflexionInternalAPI/1.0"

    def log_message(self, *args: Any) -> None:  # noqa: D401 —— 静默，避免污染 Agent 输出
        return

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 —— 标准库要求的方法名
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        data: dict = getattr(self.server, "payload", {})

        if parsed.path == "/health":
            self._json({"ok": True, "service": "internal-api"})
            return

        if parsed.path == "/kb/search":
            q = (query.get("q") or [""])[0]
            try:
                top_k = max(1, min(10, int((query.get("top_k") or ["3"])[0])))
            except ValueError:
                top_k = 3
            results = _rank(q, data.get("documents", []), top_k)
            self._json({"query": q, "count": len(results), "results": results})
            return

        if parsed.path == "/fx":
            base = (query.get("base") or ["USD"])[0].upper()
            target = (query.get("target") or ["CNY"])[0].upper()
            try:
                amount = float((query.get("amount") or ["1"])[0])
            except ValueError:
                self._json({"error": "amount 必须是数字"}, 400)
                return
            rates: dict = data.get("fx", {})
            rate = rates.get(base, {}).get(target)
            if rate is None:  # 支持反向汇率
                reverse = rates.get(target, {}).get(base)
                if reverse:
                    rate = 1 / reverse
            if rate is None:
                self._json({"error": f"不支持 {base} → {target}"}, 404)
                return
            self._json(
                {
                    "base": base,
                    "target": target,
                    "amount": amount,
                    "rate": round(rate, 6),
                    "converted": round(amount * rate, 4),
                }
            )
            return

        self._json({"error": "not found", "endpoints": ["/health", "/kb/search", "/fx"]}, 404)


class InternalAPI:
    """无序依赖的本地 HTTP 服务封装。"""

    def __init__(self, data_path: Path | None = None) -> None:
        self.data_path = data_path or (DATA_DIR / "kb.json")
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""

    def start(self) -> str:
        if self._server is not None:
            return self.base_url
        payload = json.loads(self.data_path.read_text(encoding="utf-8"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.payload = payload  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="internal-api", daemon=True)
        self._thread.start()
        host, port = server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        return self.base_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._thread = None

    def get(self, path: str, **params: Any) -> dict:
        base = self.start()
        url = f"{base}{path}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"内部 API 调用失败：{exc}") from exc


_api: InternalAPI | None = None
_lock = threading.Lock()


def get_api() -> InternalAPI:
    """进程内单例；第一次调用时才启动服务。"""
    global _api
    with _lock:
        if _api is None:
            _api = InternalAPI()
            _api.start()
            atexit.register(_api.stop)
        return _api


# ---------------------------------------------------------------------------
# 暴露给 Agent 的两个工具函数
# ---------------------------------------------------------------------------
def internal_api(query: str, top_k: int = 2) -> str:
    """检索公司内部知识库（通过本地 HTTP API）。"""
    payload = get_api().get("/kb/search", q=query, top_k=int(top_k))
    results = payload.get("results", [])
    if not results:
        return f"内部知识库没有找到与「{query}」相关的文档。不要编造制度条款。"
    lines = [f"内部知识库检索「{query}」，命中 {len(results)} 条："]
    for i, doc in enumerate(results, 1):
        text = re.sub(r"\s+", " ", doc["text"])[:600]
        lines.append(f"[{i}] {doc['id']}《{doc['title']}》（匹配度 {doc['score']}）\n    {text}")
    return "\n".join(lines)


def exchange_rate(base: str, target: str, amount: float = 1.0) -> str:
    """查询内部结算汇率并换算（通过本地 HTTP API）。"""
    payload = get_api().get("/fx", base=base, target=target, amount=amount)
    if "error" in payload:
        raise ToolError(payload["error"])
    return (
        f"内部结算汇率：1 {payload['base']} = {payload['rate']} {payload['target']}；"
        f"{payload['amount']:g} {payload['base']} = {payload['converted']:g} {payload['target']}"
    )
