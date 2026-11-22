"""集中配置。

设计原则：
* 所有可变参数都来自环境变量，代码里不留任何密钥；
* 没有 API Key 时自动降级为「离线回放」模式，保证任何人克隆后都能直接运行；
* 路径全部相对本文件解析，因此从任何工作目录启动都不会找不到数据。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# week-03/wangzhuo/
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"
RESULTS_DIR = ROOT / "results"
TRACE_DIR = RESULTS_DIR / "traces"
MEMORY_PATH = RESULTS_DIR / "memory.json"

# 内置的真实运行录制（供无 Key 环境回放）
RECORDINGS_DIR = Path(__file__).resolve().parent / "recordings"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """一次运行的全部配置。"""

    # ---- LLM ----
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    api_key: str = ""
    temperature: float = 0.0
    timeout: float = 90.0

    # ---- Agent ----
    max_steps: int = 8
    max_reflections: int = 2
    reflection_threshold: float = 0.85
    min_improvement: float = 0.03

    # ---- 工具 ----
    search_top_k: int = 3
    search_offline: bool = False
    tool_timeout: float = 20.0
    tool_output_limit: int = 4000

    # ---- 模式 ----
    offline: bool = False

    # ---- 路径 ----
    trace_dir: Path = field(default_factory=lambda: TRACE_DIR)
    memory_path: Path = field(default_factory=lambda: MEMORY_PATH)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            base_url=os.environ.get("LLM_BASE_URL", cls.base_url).rstrip("/"),
            model=os.environ.get("LLM_MODEL", cls.model),
            api_key=os.environ.get("LLM_API_KEY", "").strip(),
            temperature=_env_float("LLM_TEMPERATURE", cls.temperature),
            timeout=_env_float("LLM_TIMEOUT", cls.timeout),
            max_steps=_env_int("MAX_STEPS", cls.max_steps),
            max_reflections=_env_int("MAX_REFLECTIONS", cls.max_reflections),
            reflection_threshold=_env_float("REFLECTION_THRESHOLD", cls.reflection_threshold),
            min_improvement=_env_float("MIN_IMPROVEMENT", cls.min_improvement),
            search_top_k=_env_int("SEARCH_TOP_K", cls.search_top_k),
            search_offline=_env_bool("SEARCH_OFFLINE", False),
            offline=_env_bool("OFFLINE", False),
        )

    @property
    def use_mock(self) -> bool:
        """无 Key 或显式 OFFLINE 时使用离线回放，绝不因缺 Key 而崩溃。"""
        return self.offline or not self.api_key

    def ensure_dirs(self) -> None:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
