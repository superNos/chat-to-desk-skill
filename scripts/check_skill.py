#!/usr/bin/env python3
"""检查 personal-agent-workbench 的入口、调用名与参考文件路由（仅标准库）。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "personal-agent-workbench"
INVOCATION = "$personal-agent-workbench"


def check() -> list[str]:
    errors: list[str] = []
    skill_md = SKILL_DIR / "SKILL.md"
    yaml_path = SKILL_DIR / "agents" / "openai.yaml"
    if not skill_md.is_file() or not yaml_path.is_file():
        return ["技能缺少 SKILL.md 或 agents/openai.yaml"]
    text = skill_md.read_text(encoding="utf-8")
    if not re.match(r"---\nname: personal-agent-workbench\ndescription: \S", text):
        errors.append("SKILL.md frontmatter 的 name 或 description 不正确")
    for path in (skill_md, yaml_path, ROOT / "README.md"):
        if not path.is_file() or INVOCATION not in path.read_text(encoding="utf-8"):
            errors.append(f"{path.relative_to(ROOT)} 缺少准确调用名：{INVOCATION}")
    linked = set(re.findall(r"\]\((references/[^)]+\.md)\)", text))
    for rel in sorted(linked):
        if not (SKILL_DIR / rel).is_file():
            errors.append(f"SKILL.md 链接的文件不存在：{rel}")
    present = {f"references/{p.name}" for p in (SKILL_DIR / "references").glob("*.md")}
    for rel in sorted(present - linked):
        errors.append(f"参考文件没有从 SKILL.md 路由：{rel}")
    allowed_top = {"SKILL.md", "agents", "references"}
    for child in SKILL_DIR.iterdir():
        if child.name not in allowed_top:
            errors.append(f"技能目录出现了预期之外的内容：{child.name}")
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("技能检查失败：")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("技能检查通过：入口、调用名与参考文件路由一致，技能目录只含说明文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
