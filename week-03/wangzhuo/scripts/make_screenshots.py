"""生成真实的终端运行截图。

做法不是 P 图，而是：
1. 以 FORCE_COLOR=1 真实执行命令，捕获 stdout/stderr；
2. 把 ANSI 颜色转换成 HTML（终端配色 + 窗口标题栏）；
3. 用本机 Edge/Chrome 无头模式截图，输出 PNG。

这样截图内容与实际运行完全一致，可随时重跑。
"""

from __future__ import annotations

import html
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
PY = sys.executable

ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")
COLORS = {
    31: "#ff7b72", 32: "#7ee787", 33: "#e3b341", 34: "#79c0ff",
    35: "#d2a8ff", 36: "#76e3ea", 37: "#c9d1d9", 90: "#8b949e",
}
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def ansi_to_html(text: str) -> str:
    out: list[str] = []
    open_span = False
    for part in re.split(r"(\x1b\[[0-9;]*m)", text):
        if not part:
            continue
        if ANSI_RE.fullmatch(part):
            codes = [int(c) for c in ANSI_RE.fullmatch(part).group(1).split(";") if c]
            if open_span:
                out.append("</span>")
                open_span = False
            if not codes or 0 in codes:
                continue
            styles = []
            for code in codes:
                if code == 1:
                    styles.append("font-weight:700")
                elif code == 2:
                    styles.append("opacity:.65")
                elif code in COLORS:
                    styles.append(f"color:{COLORS[code]}")
            if styles:
                out.append(f'<span style="{";".join(styles)}">')
                open_span = True
        else:
            out.append(html.escape(part))
    if open_span:
        out.append("</span>")
    return "".join(out)


def display_width(text: str) -> int:
    width = 0
    for ch in strip_ansi(text):
        width += 2 if ord(ch) > 0x2E80 else 1
    return width


def estimate_height(text: str, width_chars: int = 150) -> int:
    lines = text.splitlines() or [""]
    wrapped = sum(max(1, math.ceil(display_width(line) / width_chars)) for line in lines)
    return min(2800, max(420, int(wrapped * 23 + 120)))


def render_html(command: str, output: str) -> str:
    body = ansi_to_html(output)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#0d1117; }}
  .window {{
    width:1280px; margin:0; background:#0d1117;
    border:1px solid #30363d; border-radius:10px; overflow:hidden;
    box-shadow:0 10px 30px rgba(0,0,0,.35);
  }}
  .bar {{
    height:42px; background:#161b22; border-bottom:1px solid #30363d;
    display:flex; align-items:center; padding:0 16px; gap:8px;
  }}
  .dot {{ width:12px; height:12px; border-radius:50%; }}
  .r {{ background:#ff5f56; }} .y {{ background:#ffbd2e; }} .g {{ background:#27c93f; }}
  .title {{
    margin-left:12px; color:#8b949e; font:13px "Segoe UI", sans-serif;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }}
  pre {{
    margin:0; padding:18px 22px 24px; color:#c9d1d9;
    font-family:"Cascadia Mono","Consolas","Microsoft YaHei",monospace;
    font-size:14px; line-height:1.62; white-space:pre-wrap; word-break:break-all;
  }}
  .cmd {{ color:#7ee787; font-weight:700; }}
</style></head>
<body><div class="window">
  <div class="bar"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>
    <span class="title">reflexion-agent — {html.escape(command)}</span></div>
  <pre><span class="cmd">$ {html.escape(command)}</span>
{body}</pre>
</div></body></html>"""


def find_browser() -> str | None:
    for path in BROWSERS:
        if Path(path).exists():
            return path
    return shutil.which("msedge") or shutil.which("chrome")


def browser_screenshot(html_text: str, png: Path, width: int = 1280, height: int = 900) -> bool:
    browser = find_browser()
    if not browser:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "shot.html"
        page.write_text(html_text, encoding="utf-8")
        for flag in ("--headless=new", "--headless"):
            if png.exists():
                png.unlink()
            profile = Path(tmp) / f"profile-{flag.strip('-')}"  # 独立 profile，避免与已开浏览器互相锁住
            cmd = [
                browser, flag, "--disable-gpu", "--hide-scrollbars",
                "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                f"--user-data-dir={profile}",
                "--force-device-scale-factor=1",
                f"--window-size={width},{height}",
                f"--screenshot={png}", page.as_uri(),
            ]
            try:
                subprocess.run(cmd, capture_output=True, timeout=45)
            except subprocess.TimeoutExpired:
                continue
            if png.exists() and png.stat().st_size > 1000:
                return True
    return False


def output_to_png(command: str, output: str, name: str, height: int | None = None) -> None:
    height = height or estimate_height(output)
    html_text = render_html(command, output)
    png = ASSETS / name
    if browser_screenshot(html_text, png, height=height):
        print(f"saved {png}  ({height}px)")
        return
    raise RuntimeError("未找到可用的 Edge/Chrome，无法生成截图")


def run(cmd: list[str], env: dict[str, str]) -> str:
    proc = subprocess.run(
        cmd, cwd=ROOT, env=env, capture_output=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        print(f"WARN: {' '.join(cmd)} exited {proc.returncode}")
    return strip_ansi(output) if "\x1b[" not in output else output


def _env(force_color: bool = True) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("LLM_API_KEY", None)  # 截图统一走离线回放，证明无 Key 可运行
    env["OFFLINE"] = "1"
    env["SEARCH_OFFLINE"] = "1"   # 回放时不访问外网，避免慢超时
    if force_color:
        env["FORCE_COLOR"] = "1"
    return env


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    env = _env()

    # 1) 演示：完整 ReAct + 反思 + 工具调用（离线回放真实录制）
    demo_cmd = "python -m reflexion_agent demo --offline"
    demo = run([PY, "-m", "reflexion_agent", "demo", "--offline"], env)
    output_to_png(demo_cmd, demo, "screenshot-demo.png")

    # 2) 修复实验汇总
    repair_cmd = "python -m reflexion_agent.eval.run_repair --offline --output results/repair_replay.json"
    repair = run([PY, "-m", "reflexion_agent.eval.run_repair", "--offline",
                  "--output", "results/repair_replay.json"], env)
    marker = "=== 无反思"
    repair_tail = repair[repair.find(marker):] if marker in repair else repair[-4000:]
    output_to_png(repair_cmd, repair_tail, "screenshot-repair.png",
                  height=min(1800, estimate_height(repair_tail)))

    # 3) 测试
    test_cmd = "python -m pytest -q"
    tests = run([PY, "-m", "pytest", "-q"], env)
    output_to_png(test_cmd, tests, "screenshot-tests.png", height=300)

    # 4) 端到端消融汇总（仅回放已录制的 10 条代表用例）
    eval_ids = ("calc-01,calc-04,calc-05,fx-01,kb-02,kb-03,"
                "search-01,budget-01,conflict-01,unanswerable-01")
    eval_cmd = (f"python -m reflexion_agent.eval.run_eval --ids {eval_ids} "
                "--offline --output results/eval_replay.json")
    e2e = run([PY, "-m", "reflexion_agent.eval.run_eval", "--ids", eval_ids,
               "--offline", "--output", "results/eval_replay.json"], env)
    marker = "=== 无反思"
    e2e_tail = e2e[e2e.find(marker):] if marker in e2e else e2e[-4000:]
    output_to_png(eval_cmd, e2e_tail, "screenshot-eval.png",
                  height=min(1800, estimate_height(e2e_tail)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
