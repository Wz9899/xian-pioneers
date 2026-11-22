# -*- coding: utf-8 -*-
"""tools_web.py — 真实外网工具：联网搜索。

用 Bing 的 RSS 输出（`&format=rss`）而不是抓 HTML，三个理由：
1. 返回结构化 XML，标准库 `xml.etree` 就能解析，不用装 bs4；
2. 页面 DOM 经常改版，RSS 的 schema 相对稳定；
3. 不需要 API key，适合学习与演示。

⚠️ 搜索引擎的服务条款限制自动化抓取，这里仅用于课程演示。
生产环境请换官方搜索 API（Bing Web Search / SerpAPI / Tavily 等）。
"""
import os
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import httpx

from tools import tool

BING_URL = "https://www.bing.com/search"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _client():
    """显式指定代理策略：不让系统代理把一个失效端口变成「网络异常」。"""
    proxy = (os.environ.get("LLM_PROXY") or os.environ.get("HTTPS_PROXY")
             or os.environ.get("HTTP_PROXY") or "").strip() or None
    return httpx.Client(timeout=httpx.Timeout(20.0, connect=8.0),
                        trust_env=False, proxy=proxy,
                        follow_redirects=True,   # Bing 会 302 跳到 cn.bing.com
                        headers={"User-Agent": USER_AGENT,
                                 "Accept-Language": "zh-CN,zh;q=0.9"})


QUESTION_MARKERS = ("是什么", "什么是", "怎么", "如何", "为什么", "介绍",
                    "官方文档", "求", "？", "?")


def _looks_like_sentence(query):
    """关键词引擎对长句、中英混写很敏感，会退化成匹配单个词。"""
    return len(query.split()) > 6 or len(query) > 28 or any(m in query for m in QUESTION_MARKERS)


def _suggest(query):
    """把一整句话压成 2~5 个关键词，作为「怎么改」的建议。"""
    cleaned = query
    for marker in QUESTION_MARKERS:
        cleaned = cleaned.replace(marker, " ")
    parts = [p for p in cleaned.replace("，", " ").replace("、", " ").split() if p]
    return " ".join(parts[:5]) or query


@tool(
    name="web_search",
    description=(
        "联网搜索实时信息。模型自身知识有截止时间，凡是涉及最新版本、价格、新闻、"
        "文档细节的问题，必须先调用本工具，不要凭记忆回答。\n"
        "用法要点（很重要）：\n"
        "1. query 用 2~5 个关键词，不要写整句、不要带「是什么/怎么/官方文档」这类疑问词；\n"
        "2. 中文问题用中文关键词，英文专有名词保留原文，不要一句话里中英混写；\n"
        "3. 需要精确匹配时给关键词加英文双引号，或加 site:域名 限定站点；\n"
        "4. 结果不相关就换更短的关键词重搜，而不是重复同样的查询。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "2~5 个关键词，例如「MCP 协议」「deepseek api 价格」"},
            "count": {"type": "integer", "description": "返回条数，1~8，默认 5"},
        },
        "required": ["query"],
    },
)
def web_search(query, count=5):
    query = " ".join((query or "").split()).strip(" ，。,.?？")
    if not query:
        return "错误：搜索关键词不能为空。"

    try:
        count = max(1, min(int(count or 5), 8))
    except (TypeError, ValueError):
        count = 5

    try:
        with _client() as client:
            response = client.get(BING_URL, params={
                "q": query, "format": "rss", "mkt": "zh-CN", "count": count})
    except httpx.TimeoutException:
        return "搜索超时：Bing 没有在 20 秒内返回，可换个关键词重试。"
    except httpx.HTTPError as exc:
        return "搜索失败（网络）：{0}".format(exc)

    if response.status_code != 200:
        return "搜索失败：HTTP {0}".format(response.status_code)

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        return "搜索失败：返回内容不是合法 XML（{0}）".format(exc)

    items = root.findall("./channel/item")
    if not items:
        return "没有搜到「{0}」的结果，建议换关键词。".format(query)

    lines = ["「{0}」的搜索结果（共 {1} 条）：".format(query, len(items[:count]))]
    if _looks_like_sentence(query):
        lines.insert(0, "【提示】查询「{0}」偏长或含疑问词，关键词引擎会拆成单个词、"
                        "结果容易发散。建议改成 2~5 个关键词，例如「{1}」，或用引号/ site: 限定。"
                        .format(query, _suggest(query)))
    for index, item in enumerate(items[:count], start=1):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = " ".join((item.findtext("description") or "").split())
        if len(desc) > 160:
            desc = desc[:160] + "…"
        lines.append("{0}. {1}\n   {2}\n   {3}".format(index, title, link, desc))
    return "\n".join(lines)


class _TextExtractor(HTMLParser):
    """把 HTML 压成纯文本：跳过 script/style，块级标签换行。"""

    SKIP = {"script", "style", "noscript", "svg", "head", "iframe"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "table", "ul", "ol", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth > 0:
            self.depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.depth:
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text + " ")


def _html_to_text(html):
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:            # 残缺 HTML 不该让整个工具失败
        pass
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


@tool(
    name="fetch_page",
    description=(
        "打开一个网页并提取正文（去掉脚本和样式）。搜索结果只有摘要，"
        "当需要表格、具体数值、完整段落时，用本工具打开结果里的链接再读。"
        "单次最多返回 4000 字。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整的 http/https 链接"},
            "max_chars": {"type": "integer", "description": "最多返回多少字，默认 4000"},
        },
        "required": ["url"],
    },
)
def fetch_page(url, max_chars=4000):
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "错误：url 必须以 http:// 或 https:// 开头。"
    try:
        max_chars = max(500, min(int(max_chars or 4000), 8000))
    except (TypeError, ValueError):
        max_chars = 4000

    try:
        with _client() as client:
            response = client.get(url, headers={"Accept": "text/html,*/*"})
    except httpx.TimeoutException:
        return "抓取超时：页面 20 秒内没有返回。"
    except httpx.HTTPError as exc:
        return "抓取失败（网络）：{0}".format(exc)

    if response.status_code != 200:
        return "抓取失败：HTTP {0}".format(response.status_code)

    content_type = (response.headers.get("content-type") or "").lower()
    if "text" not in content_type and "html" not in content_type:
        return "不是网页内容（{0}），不处理二进制文件。".format(content_type or "未知类型")

    text = _html_to_text(response.text)
    if not text:
        return "页面没有可读正文（可能是纯 JS 渲染）。"

    suffix = ""
    if len(text) > max_chars:
        text = text[:max_chars]
        suffix = "\n…（已截断，原文 {0} 字）".format(len(_html_to_text(response.text)))
    return "【{0}】\n{1}{2}".format(url, text, suffix)


if __name__ == "__main__":
    print(web_search("DeepSeek API 定价", 3))
    print()
    print(fetch_page("https://api-docs.deepseek.com/zh-cn/quick_start/pricing", 800))
