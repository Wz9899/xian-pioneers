# -*- coding: utf-8 -*-
"""chat.py — 最小示例：一次问答（默认流式）。

    .\\run.ps1 chat.py "用一句话解释什么是 SSR"
    .\\run.ps1 chat.py "你好" --no-stream
    .\\run.ps1 chat.py "你好" --model deepseek-reasoner

想跑「工具 + Skill + MCP」的完整链路，看 demo.py。
"""
import argparse
import os
import sys

import _bootstrap          # noqa: F401 — 把 src/ 加入 sys.path
import console_io
import llm

ROOT = _bootstrap.PROJECT_ROOT


def main():
    console_io.setup()

    parser = argparse.ArgumentParser(description="最小 LLM 调用示例")
    parser.add_argument("prompt", nargs="?", default="用一句话说明什么是 API。")
    parser.add_argument("--no-stream", action="store_true", help="非流式，一次性返回")
    parser.add_argument("--model", help="覆盖 .env 里的 LLM_MODEL")
    parser.add_argument("--system", default="You are a helpful assistant.",
                        help="system prompt；传 '' 可去掉")
    args = parser.parse_args()

    client = llm.from_env(os.path.join(ROOT, ".env"), model=args.model)
    if not client.api_key:
        print("缺少 API Key：把 LLM_API_KEY=sk-xxx 写进 {0}".format(
            os.path.join(ROOT, ".env")))
        return 2

    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.prompt})

    print("[model={0} | base_url={1} | stream={2}]".format(
        client.model, client.base_url, not args.no_stream), file=sys.stderr)

    try:
        response = client.chat(
            messages,
            stream=not args.no_stream,
            on_delta=(lambda piece: (sys.stdout.write(piece), sys.stdout.flush()))
            if not args.no_stream else None,
        )
    except llm.LLMError as exc:
        print("调用失败：{0}".format(exc))
        return 1
    finally:
        client.close()

    if args.no_stream:
        print(response["content"])

    usage = response.get("usage") or {}
    print("\n--- {0:.2f}s | tokens: 输入 {1} / 输出 {2} / 合计 {3}".format(
        response["elapsed"], usage.get("prompt_tokens", "?"),
        usage.get("completion_tokens", "?"), usage.get("total_tokens", "?")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
