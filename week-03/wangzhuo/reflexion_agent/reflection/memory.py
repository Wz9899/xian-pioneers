"""跨任务经验记忆（Reflexion 的 episodic memory）。

与「把整段对话塞回上下文」不同，这里只沉淀一句可迁移的教训：
* 任务成功但走过弯路 → 记录「下次这类题要注意什么」；
* 任务失败 → 记录失败原因与修正方向。

检索用字符二元组 + 词元的 Jaccard 相似度，不依赖分词库；
记忆持久化为 JSON，进程重启后仍可复用。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import MEMORY_PATH


@dataclass
class Episode:
    task: str
    lesson: str
    score_before: float = 0.0
    score_after: float = 0.0
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> dict:
        return asdict(self)


def _tokens(text: str) -> set[str]:
    text = text.lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    han = re.findall(r"[一-鿿]", text)
    bigrams = {"".join(han[i : i + 2]) for i in range(len(han) - 1)}
    return words | set(han) | bigrams


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class ReflectiveMemory:
    def __init__(self, path: Path | None = None, max_items: int = 50) -> None:
        self.path = path or MEMORY_PATH
        self.max_items = max_items
        self.episodes: list[Episode] = []
        self.load()

    # -- 持久化 ------------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            self.episodes = []
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.episodes = [Episode(**item) for item in raw]
        except (json.JSONDecodeError, TypeError):
            self.episodes = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([e.to_dict() for e in self.episodes], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # -- 写入 --------------------------------------------------------------
    def add(self, task: str, lesson: str, score_before: float, score_after: float) -> Episode:
        lesson = re.sub(r"\s+", " ", lesson).strip()[:300]
        episode = Episode(task=task[:200], lesson=lesson,
                          score_before=score_before, score_after=score_after)
        # 相似任务 + 相似教训 → 用新经验覆盖旧经验，避免记忆膨胀
        for i, old in enumerate(self.episodes):
            if similarity(old.task, task) > 0.8 and similarity(old.lesson, lesson) > 0.6:
                self.episodes[i] = episode
                self.save()
                return episode
        self.episodes.append(episode)
        if len(self.episodes) > self.max_items:
            self.episodes = self.episodes[-self.max_items :]
        self.save()
        return episode

    # -- 检索 --------------------------------------------------------------
    def retrieve(self, task: str, k: int = 2, min_similarity: float = 0.12) -> list[Episode]:
        scored = [(similarity(e.task, task), e) for e in self.episodes]
        scored = [(s, e) for s, e in scored if s >= min_similarity]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [e for _, e in scored[:k]]

    @staticmethod
    def format(episodes: list[Episode]) -> str:
        if not episodes:
            return ""
        lines = ["【历史经验（来自相似任务，仅供参考，不要照搬数字）】"]
        for i, e in enumerate(episodes, 1):
            lines.append(f"{i}. 任务：{e.task}\n   教训：{e.lesson}")
        return "\n".join(lines)

    def clear(self) -> None:
        self.episodes = []
        self.save()

    def __len__(self) -> int:
        return len(self.episodes)
