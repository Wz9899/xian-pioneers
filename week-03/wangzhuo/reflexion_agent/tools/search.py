"""联网搜索工具。

优先调用 DuckDuckGo（免 Key 的真实外部工具）；在无网络、超时或被限流时，
自动回退到仓库内置的离线快照，保证 Agent 仍能完成演示与回放。
回退不是假装联网：返回文本会明确标注「离线快照」。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import DATA_DIR


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text)
    han = re.findall(r"[一-鿿]", text)
    bigrams = ["".join(han[i : i + 2]) for i in range(len(han) - 1)]
    return words + han + bigrams


class SearchTool:
    """搜索工具：实时 DDG 优先，离线快照兜底。"""

    def __init__(
        self,
        top_k: int = 3,
        index_path: Path | None = None,
        use_network: bool = True,
    ) -> None:
        self.top_k = top_k
        self.index_path = index_path or (DATA_DIR / "search_index.json")
        self.use_network = use_network
        self._index = self._load_index()

    def _load_index(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    # -- 对外入口 -----------------------------------------------------------
    def __call__(self, query: str, top_k: int | None = None) -> str:
        k = int(top_k or self.top_k)
        results, source = self._online(query, k) if self.use_network else ([], "")
        if not results:
            results, source = self._offline(query, k), "离线快照"
        if not results:
            return f"未找到与「{query}」相关的结果。可以换一个更具体的关键词，或说明信息不足。"
        return self._format(query, results, source)

    # -- 在线 ---------------------------------------------------------------
    def _online(self, query: str, top_k: int) -> tuple[list[dict], str]:
        try:
            from ddgs import DDGS  # 延迟导入：离线环境不因缺包而失败

            with DDGS(timeout=12) as ddgs:
                raw = list(ddgs.text(query, max_results=top_k))
            results = [
                {
                    "title": (r.get("title") or "").strip(),
                    "snippet": (r.get("body") or "").strip(),
                    "url": (r.get("href") or "").strip(),
                }
                for r in raw
            ]
            return [r for r in results if r["snippet"]], "DuckDuckGo 实时检索"
        except Exception:  # noqa: BLE001 —— 网络问题一律降级，不打断 Agent
            return [], ""

    # -- 离线 ---------------------------------------------------------------
    def _offline(self, query: str, top_k: int) -> list[dict]:
        q_tokens = set(_tokenize(query))
        if not q_tokens:
            return []
        scored: list[tuple[float, dict]] = []
        for doc in self._index:
            doc_tokens = set(_tokenize(doc["title"] + " " + doc["snippet"] + " " + " ".join(doc.get("keywords", []))))
            overlap = len(q_tokens & doc_tokens)
            if overlap:
                scored.append((overlap / len(q_tokens), doc))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"title": d["title"], "snippet": d["snippet"], "url": d["url"]}
            for _, d in scored[:top_k]
        ]

    # -- 输出 ---------------------------------------------------------------
    @staticmethod
    def _format(query: str, results: list[dict], source: str) -> str:
        lines = [f"搜索「{query}」（来源：{source}）"]
        for i, r in enumerate(results, 1):
            snippet = re.sub(r"\s+", " ", r["snippet"])[:400]
            lines.append(f"[{i}] {r['title']}\n    {snippet}\n    {r['url']}")
        return "\n".join(lines)
