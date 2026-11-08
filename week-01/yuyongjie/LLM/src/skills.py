# -*- coding: utf-8 -*-
"""skills.py — Skill 机制（渐进式披露 / progressive disclosure）。

一个 Skill = skills/<name>/SKILL.md，文件头是 YAML frontmatter（name + description）。

关键设计：**默认只把 name + description 放进工具列表**，正文（步骤、规范、检查清单）
只在模型主动调用 load_skill 时才注入上下文。这样几十个 Skill 也不会撑爆 prompt，
模型也能按描述自己决定要不要加载——这就是所谓“渐进式披露”。

注册了两个工具：
  list_skills  列出所有可用 Skill（名字 + 一句话描述）
  load_skill   加载某个 Skill 的完整正文
"""
import os

from paths import SKILLS_DIR
from tools import tool

SKILLS_DIR = SKILLS_DIR          # 兼容旧引用，真实来源见 paths.py


def _parse_frontmatter(text):
    """极简 frontmatter 解析：只支持 key: value，不引入 yaml 依赖。"""
    if not text.lstrip().startswith("---"):
        return {}, text
    text = text.lstrip()
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    header, body = text[3:end], text[end + 4:]
    meta = {}
    for line in header.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body.lstrip("\n")


def load_all(skills_dir=None):
    """扫描所有 Skill，返回 {name: {...}}。"""
    root = skills_dir or SKILLS_DIR
    found = {}
    if not os.path.isdir(root):
        return found

    for entry in sorted(os.listdir(root)):
        path = os.path.join(root, entry, "SKILL.md")
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            meta, body = _parse_frontmatter(f.read())
        name = meta.get("name") or entry
        found[name] = {
            "name": name,
            "description": meta.get("description") or "（无描述）",
            "body": body,
            "path": path,
            "folder": entry,
        }
    return found


SKILLS = load_all()


def catalog():
    """给 system prompt 用的目录（只有名字和描述，不含正文）。"""
    if not SKILLS:
        return ""
    lines = ["可用 Skill（需要时先调用 load_skill 加载完整步骤）："]
    for s in SKILLS.values():
        lines.append("  - {0}: {1}".format(s["name"], s["description"]))
    return "\n".join(lines)


@tool(
    name="list_skills",
    description="列出所有可用 Skill 的名字和描述。用户的任务像是某个 Skill 负责的领域时，先看这里。",
    parameters={"type": "object", "properties": {}, "required": []},
)
def list_skills():
    if not SKILLS:
        return "（没有找到任何 Skill）"
    return "\n".join("{0}: {1}".format(s["name"], s["description"]) for s in SKILLS.values())


@tool(
    name="load_skill",
    description="加载指定 Skill 的完整说明（步骤、规范、输出格式）。加载后严格按其中的步骤执行。",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill 名，来自 list_skills 的结果"}
        },
        "required": ["name"],
    },
)
def load_skill(name):
    skill = SKILLS.get(name)
    if skill is None:
        return "错误：没有名为 {0} 的 Skill。可用：{1}".format(name, ", ".join(SKILLS))
    return (
        "已加载 Skill「{0}」。以下是它的完整说明，请严格按步骤执行。\n\n"
        "================ SKILL BEGIN ================\n{1}\n"
        "================= SKILL END =================".format(name, skill["body"])
    )


if __name__ == "__main__":
    print("skills dir:", SKILLS_DIR)
    print("found:", ", ".join(SKILLS) or "（空）")
    print("-" * 60)
    print(list_skills())
    print("-" * 60)
    print(load_skill(SKILLS_DIR and list(SKILLS)[0] if SKILLS else ""))
