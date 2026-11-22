"""全链路追踪与终端渲染。

每一次思考、工具调用、评审、修订都会写入事件流：
* 终端里用颜色和分层标题展示（截图即来源于此）；
* 同时可落盘为 JSONL，便于事后回放、统计和排错。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
MAGENTA = "\x1b[35m"
BLUE = "\x1b[34m"

_COLORS = {
    "task": CYAN,
    "round": BOLD + BLUE,
    "llm": DIM,
    "tool_call": YELLOW,
    "tool_result": GREEN,
    "answer": CYAN,
    "critique": MAGENTA,
    "revision": YELLOW,
    "memory": BLUE,
    "final": BOLD + GREEN,
    "error": RED,
}


def _supports_color() -> bool:
    """支持 FORCE_COLOR，便于把带颜色的真实输出渲染成运行截图。"""
    if os.environ.get("FORCE_COLOR", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return sys.stdout.isatty()


class Tracer:
    def __init__(self, verbose: bool = True, color: bool | None = None) -> None:
        self.verbose = verbose
        self.color = _supports_color() if color is None else color
        self.events: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def _paint(self, text: str, kind: str) -> str:
        if not self.color:
            return text
        return f"{_COLORS.get(kind, '')}{text}{RESET}"

    def event(self, kind: str, message: str, **data: Any) -> None:
        record = {"ts": round(time.time(), 3), "kind": kind, "message": message, **data}
        self.events.append(record)
        if self.verbose:
            print(self._paint(message, kind), flush=True)

    def section(self, title: str) -> None:
        line = "─" * max(8, 62 - len(title))
        message = f"\n┌─ {title} {line}"
        self.event("round", message)

    def close_section(self) -> None:
        if self.verbose:
            print(self._paint("└" + "─" * 64, "round"), flush=True)

    # ------------------------------------------------------------------
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for event in self.events:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    @property
    def tool_events(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] in {"tool_call", "tool_result"}]
