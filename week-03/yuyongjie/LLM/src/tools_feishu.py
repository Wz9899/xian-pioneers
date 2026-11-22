# -*- coding: utf-8 -*-
"""tools_feishu.py — 真实工具之三：调用飞书 API 推送消息卡片。

支持两种常见接入方式中最轻的一种——**自定义机器人 Webhook**：
不需要企业应用审核，把群里的 webhook 地址填进环境变量即可。

    FEISHU_WEBHOOK        机器人地址（必填）
    FEISHU_WEBHOOK_SECRET 加签密钥（可选，机器人开了「签名校验」才需要）

没配置时不会崩，而是返回明确的配置指引；配合 `dev/feishu_mock.py`
可以在**完全没有飞书账号**的情况下验证整条发送链路。

协议要点：
- 请求体 `{"msg_type": "interactive", "card": {...}}`；
- 成功返回 `{"code": 0, "msg": "success"}`，code 非 0 即为业务失败；
- 开签名的机器人要求带 `timestamp` 与 `sign`，算法见 `_sign()`。
"""
import base64
import hashlib
import hmac
import json
import os
import time

import httpx

from tools import tool


def _webhook():
    return (os.environ.get("FEISHU_WEBHOOK") or "").strip()


def _secret():
    return (os.environ.get("FEISHU_WEBHOOK_SECRET") or "").strip()


def _client():
    proxy = (os.environ.get("LLM_PROXY") or os.environ.get("HTTPS_PROXY")
             or os.environ.get("HTTP_PROXY") or "").strip() or None
    return httpx.Client(timeout=10.0, trust_env=False, proxy=proxy,
                        follow_redirects=True)


def _sign(timestamp, secret):
    """飞书加签：以 `timestamp\\nsecret` 为密钥，对空串做 HMAC-SHA256，再 base64。"""
    string_to_sign = "{0}\n{1}".format(timestamp, secret)
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_card(title, markdown, template="blue", footer="由 LLM Agent 生成"):
    """构造一张最简单的消息卡片（colored header + markdown 正文 + 脚注）。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:60]},
            "template": template,
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": markdown[:4000]}},
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": footer}]},
        ],
    }


def build_payload(title, markdown, template="blue", secret=None, now=None):
    """把卡片包成 webhook 请求体；开了签名就补 timestamp 与 sign。"""
    payload = {"msg_type": "interactive", "card": build_card(title, markdown, template)}
    if secret:
        timestamp = str(int(now if now is not None else time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = _sign(timestamp, secret)
    return payload


@tool(
    name="feishu_notify",
    description=(
        "把消息以卡片形式推送到飞书群。用于「把清单发我飞书」「通知一下团队」这类请求。"
        "需要标题和 markdown 正文，正文要精简（建议不超过 800 字）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "卡片标题，不超过 60 字"},
            "content": {"type": "string",
                        "description": "正文，支持 markdown（**加粗**、- 列表），建议精简"},
        },
        "required": ["title", "content"],
    },
)
def feishu_notify(title, content):
    title = (title or "").strip() or "通知"
    content = (content or "").strip()
    if not content:
        return "错误：正文不能为空。"

    hook = _webhook()
    if not hook:
        return ("未配置飞书机器人：请设置环境变量 FEISHU_WEBHOOK（群里「设置→机器人→自定义机器人」"
                "可获取）。本地自测可用 dev/feishu_mock.py。")

    payload = build_payload(title, content, secret=_secret())

    try:
        with _client() as client:
            response = client.post(hook, json=payload)
    except httpx.TimeoutException:
        return "推送超时：飞书在 10 秒内没有响应。"
    except httpx.HTTPError as exc:
        return "推送失败（网络）：{0}".format(exc)

    if response.status_code != 200:
        return "推送失败：HTTP {0} {1}".format(response.status_code, response.text[:200])

    try:
        data = response.json()
    except ValueError:
        return "推送失败：返回不是 JSON：{0}".format(response.text[:200])

    code = data.get("code", data.get("StatusCode", 0))
    if code != 0:
        return "飞书返回业务错误：code={0} msg={1}".format(code, data.get("msg", ""))

    return "已推送到飞书：{0}（{1} 字）".format(title, len(content))


@tool(
    name="feishu_card_preview",
    description="预览将要发给飞书的卡片 JSON（不真正发送）。调不通 webhook 时用来排查内容格式。",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["title", "content"],
    },
)
def feishu_card_preview(title, content):
    return json.dumps(build_payload(title or "预览", content or "", secret=None),
                      ensure_ascii=False, indent=2)


if __name__ == "__main__":
    print(feishu_notify("自测", "- 第一条\n- 第二条"))
    print()
    print(feishu_card_preview("本周任务", "- T3 补验证\n- T6 提交作业")[:400])
