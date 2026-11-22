"""根据真实实验结果生成文档图表。

产出：
* assets/architecture.png   系统架构图
* assets/repair-ablation.png 缺陷修复消融
* assets/e2e-ablation.png    端到端消融（若已运行 eval）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

ASSETS = ROOT / "assets"
RESULTS = ROOT / "results"

NAVY = "#1f3a5f"
TEAL = "#2a9d8f"
ORANGE = "#e76f51"
SAND = "#f4a261"
LIGHT = "#eef3f8"
GRAY = "#6c757d"
INK = "#1d2733"


def _box(ax, x, y, w, h, text, fc=LIGHT, ec=NAVY, fs=12, bold=False, tc=INK):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.35,rounding_size=1.4",
        linewidth=1.6, edgecolor=ec, facecolor=fc, zorder=2,
    )
    ax.add_patch(patch)
    ax.text(
        x + w / 2, y + h / 2, text, ha="center", va="center",
        fontsize=fs, color=tc, zorder=3,
        fontweight="bold" if bold else "normal", linespacing=1.5,
    )
    return patch


def _arrow(ax, start, end, color=NAVY, style="-|>", rad=0.0, lw=1.8, text="", tpos=0.5, fs=9):
    arrow = FancyArrowPatch(
        start, end, arrowstyle=style, mutation_scale=16, linewidth=lw,
        color=color, connectionstyle=f"arc3,rad={rad}", zorder=4,
    )
    ax.add_patch(arrow)
    if text:
        x = start[0] + (end[0] - start[0]) * tpos
        y = start[1] + (end[1] - start[1]) * tpos
        ax.text(x, y + 1.2, text, fontsize=fs, color=color, ha="center", va="bottom")


def draw_architecture() -> None:
    fig, ax = plt.subplots(figsize=(13, 7.6), dpi=200)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 62)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    ax.text(50, 58.5, "Reflexion Agent · 系统架构",
            ha="center", va="center", fontsize=20, fontweight="bold", color=NAVY)
    ax.text(50, 55.2, "ReAct 工具调用 + 结构化反思 + 跨任务经验记忆",
            ha="center", va="center", fontsize=11, color=GRAY)

    # 用户任务 / 最终答案
    _box(ax, 2, 43, 17, 7, "用户任务", fc="#e8f1fb", fs=13, bold=True)
    _box(ax, 44, 4, 13, 7, "最终答案\n(含判定分)", fc="#e7f6f2", ec=TEAL, fs=11)

    # Actor
    _box(ax, 25, 38, 31, 14,
         "ReAct 执行器\n\nThought  →  Action  →  Observation\n多步工具调用，直到给出答案",
         fc="#f7fbff", fs=12, bold=False)
    ax.text(40.5, 49.8, "Actor", fontsize=13, fontweight="bold", color=NAVY, ha="center")

    # Tools
    tools = [
        ("calculator\n安全 AST 求值", 46),
        ("web_search\nDuckDuckGo 实时检索", 38),
        ("internal_api\n本地 HTTP 知识库", 30),
        ("exchange_rate\n内部结算汇率", 22),
    ]
    ax.text(80, 56, "真实工具层（统一注册表 + JSON Schema）", ha="center",
            fontsize=11, color=GRAY)
    for label, y in tools:
        _box(ax, 66, y, 30, 6, label, fc="#fff8ec", ec=SAND, fs=10.5)
        _arrow(ax, (56.5, 44.5), (65.5, y + 3), color=SAND, style="<|-|>", lw=1.3)

    # Critic / memory / reviser
    _box(ax, 25, 20, 31, 11,
         "Critic · 四维评分卡\ncorrectness 0.45 · tool_use 0.15\ncompleteness 0.30 · grounding 0.10",
         fc="#fff1ed", ec=ORANGE, fs=10.5)
    _box(ax, 2, 20, 17, 11, "Episodic Memory\n跨任务经验（JSON）\n相似度检索 top-2",
         fc="#eef7f5", ec=TEAL, fs=10)
    _box(ax, 25, 5, 14, 8, "Reviser\n带 critique 重做", fc="#fff8ec", ec=SAND, fs=10.5)

    # Arrows
    _arrow(ax, (19.5, 46.5), (24.5, 46.5), color=NAVY, text="任务", tpos=0.5, fs=9)
    _arrow(ax, (40.5, 37.5), (40.5, 31.5), color=ORANGE, text="候选答案 + 工具轨迹", tpos=0.55, fs=9)
    _arrow(ax, (39.5, 19.5), (34.5, 13.5), color=ORANGE, text="未通过", tpos=0.5, fs=9)
    _arrow(ax, (25, 9), (21, 37.5), color=SAND, style="-|>", rad=-0.1, lw=2.0)
    ax.text(20.1, 24, "重做", fontsize=8.5, color=SAND, ha="center", va="center", rotation=90)
    _arrow(ax, (51.5, 20), (50.5, 11.5), color=TEAL, lw=2.0,
           text="通过", tpos=0.45, fs=9)
    _arrow(ax, (25.2, 28), (18.8, 28), color=TEAL, style="<|-|>", lw=1.6)
    ax.text(22, 33.5, "检索 / 沉淀", fontsize=8.5, color=TEAL, ha="center")

    # 标注
    ax.text(63, 12, "停止条件：分数 ≥ 阈值 / 预算耗尽 / 边际收益 < 0.03",
            fontsize=9, color=GRAY, ha="left")
    ax.text(63, 8.5, "离线可跑：内置真实运行录制，无 Key 也能完整回放",
            fontsize=9, color=GRAY, ha="left")

    fig.tight_layout()
    fig.savefig(ASSETS / "architecture.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("saved", ASSETS / "architecture.png")


def _load(path: Path) -> dict | None:
    if not path.exists():
        print("skip (missing):", path)
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def draw_repair(data: dict) -> None:
    configs = data["configs"]
    names = [k for k in ("no_repair", "one_shot", "full") if k in configs]
    labels = [configs[k]["label"].replace("（保留缺陷）", "\n(保留缺陷)") for k in names]
    rates = [configs[k]["summary"]["repair_rate"] * 100 for k in names]
    tokens = [configs[k]["summary"]["avg_tokens"] for k in names]
    colors = ["#adb5bd", TEAL, NAVY]

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8), dpi=200,
                             gridspec_kw={"width_ratios": [1.15, 1]})
    fig.patch.set_facecolor("white")

    ax = axes[0]
    bars = ax.bar(labels, rates, color=colors, width=0.58, zorder=2)
    ax.set_ylim(0, 112)
    ax.set_ylabel("修复率（%）", fontsize=11)
    ax.set_title(f"缺陷修复率：0% → {max(rates):.0f}%", fontsize=13, fontweight="bold", color=NAVY)
    ax.grid(axis="y", linestyle="--", alpha=0.35, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    for bar, value in zip(bars, rates):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 2.5, f"{value:.1f}%",
                ha="center", fontsize=11, fontweight="bold", color=INK)

    ax2 = axes[1]
    bars2 = ax2.bar(labels, tokens, color=colors, width=0.58, zorder=2)
    ax2.set_ylabel("平均 tokens / 条", fontsize=11)
    ax2.set_title("反思的代价：token 消耗", fontsize=13, fontweight="bold", color=NAVY)
    ax2.grid(axis="y", linestyle="--", alpha=0.35, zorder=0)
    ax2.spines[["top", "right"]].set_visible(False)
    for bar, value in zip(bars2, tokens):
        ax2.text(bar.get_x() + bar.get_width() / 2, value + max(tokens) * 0.02,
                 f"{value:.0f}", ha="center", fontsize=11, fontweight="bold", color=INK)

    fig.suptitle("实验一：固定 Actor 首轮输出，只测量反思的修复能力",
                 fontsize=15, fontweight="bold", color=NAVY, y=1.02)
    fig.tight_layout()
    fig.savefig(ASSETS / "repair-ablation.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("saved", ASSETS / "repair-ablation.png")


def draw_e2e(data: dict) -> None:
    configs = data["configs"]
    names = [k for k in ("no_reflection", "one_shot", "full") if k in configs]
    labels = [configs[k]["label"] for k in names]
    rates = [configs[k]["summary"]["success_rate"] * 100 for k in names]
    tokens = [configs[k]["summary"]["avg_tokens"] for k in names]
    colors = ["#adb5bd", TEAL, NAVY]

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6), dpi=200)
    fig.patch.set_facecolor("white")

    for ax, values, title, ylabel, fmt in [
        (axes[0], rates, "任务成功率（天花板效应）", "成功率（%）", "{:.0f}%"),
        (axes[1], tokens, "平均 token 成本", "平均 tokens / 条", "{:.0f}"),
    ]:
        bars = ax.bar(labels, values, color=colors, width=0.58, zorder=2)
        top = max(values) if max(values) else 1
        ax.set_ylim(0, top * 1.18 if top else 1)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_title(title, fontsize=13, fontweight="bold", color=NAVY)
        ax.grid(axis="y", linestyle="--", alpha=0.35, zorder=0)
        ax.spines[["top", "right"]].set_visible(False)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + top * 0.03 if top else 0,
                    fmt.format(value), ha="center", fontsize=11, fontweight="bold", color=INK)

    fig.suptitle("实验二：端到端消融（强模型首轮已接近满分）",
                 fontsize=15, fontweight="bold", color=NAVY, y=1.02)
    fig.tight_layout()
    fig.savefig(ASSETS / "e2e-ablation.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("saved", ASSETS / "e2e-ablation.png")


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    draw_architecture()
    repair = _load(RESULTS / "repair_results.json")
    if repair:
        draw_repair(repair)
    e2e = _load(RESULTS / "eval_results.json")
    if e2e:
        draw_e2e(e2e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
