"""把 GitHub 版文档里的图片压缩后以内嵌 data URI 写入「单文件版」md。

为什么需要单文件版：
* GitHub 版用 `![](assets/x.png)` 引用仓库图片，渲染正常；
* 但很多提交平台只接收一个 md 文件，不会带上 assets/ 目录，图片就会丢。
* 本脚本把 7 张图压成 900px 宽的 JPEG，再 base64 内嵌，生成可直接上传的单文件。

注意：GitHub 会出于安全策略剥离 data URI 的 src，因此单文件版不适合直接放 GitHub；
仓库里同时保留 `W3-作业提交.md`（GitHub 渲染版）与本文件（平台提交版）。

用法：
    python scripts/embed_images.py
"""

from __future__ import annotations

import base64
import html
import re
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "W3-作业提交.md"
TARGET = ROOT / "W3-作业提交-单文件.md"
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((assets/[^)\s]+\.png)\)")
MAX_WIDTH = 900
JPEG_QUALITY = 85

def embed_image(alt: str, rel_path: str) -> str:
    from PIL import Image

    path = ROOT / rel_path
    image = Image.open(path).convert("RGB")
    image.thumbnail((MAX_WIDTH, 100000), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True, subsampling=1)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return (
        f'<img alt="{html.escape(alt, quote=True)}" '
        f'src="data:image/jpeg;base64,{encoded}" width="{MAX_WIDTH}" />'
    )


def main() -> int:
    if not SOURCE.exists():
        print(f"找不到源文件：{SOURCE}")
        return 1
    markdown = SOURCE.read_text(encoding="utf-8")
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        alt, rel_path = match.group(1), match.group(2)
        print(f"  embed {rel_path}")
        return embed_image(alt, rel_path)

    result = IMAGE_RE.sub(replace, markdown)
    TARGET.write_text(result, encoding="utf-8")
    size_kb = TARGET.stat().st_size // 1024
    print(f"\n已生成 {TARGET.name}：{count} 张图，{size_kb} KB")
    if IMAGE_RE.search(result):
        print("WARN: 仍存在 assets/ 图片引用")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
