#!/usr/bin/env python3
"""检查 chat-to-desk 的入口、调用名与参考文件路由（仅标准库）。"""
from __future__ import annotations

import re
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "chat-to-desk"
INVOCATION = "$chat-to-desk"
RUNTIME_FILES = {"workbench.py", "client.js"}
PAGE_SUFFIXES = {".html", ".htm", ".css", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}


def check() -> list[str]:
    errors: list[str] = []
    skill_md = SKILL_DIR / "SKILL.md"
    yaml_path = SKILL_DIR / "agents" / "openai.yaml"
    if not skill_md.is_file() or not yaml_path.is_file():
        return ["技能缺少 SKILL.md 或 agents/openai.yaml"]
    text = skill_md.read_text(encoding="utf-8")
    if not re.match(r"---\nname: chat-to-desk\ndescription: \S", text):
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
    allowed_top = {"SKILL.md", "agents", "references", "scripts"}
    for child in SKILL_DIR.iterdir():
        if child.name not in allowed_top:
            errors.append(f"技能目录出现了预期之外的内容：{child.name}")
    # 运行支持只允许这两个与工作内容无关的文件；页面、样式、图片一律不放进技能目录
    scripts = {p.name for p in (SKILL_DIR / "scripts").iterdir()} if (SKILL_DIR / "scripts").is_dir() else set()
    if scripts != RUNTIME_FILES:
        errors.append(f"scripts/ 只应包含 {sorted(RUNTIME_FILES)}，实际是 {sorted(scripts)}")
    for path in SKILL_DIR.rglob("*"):
        if path.suffix.lower() in PAGE_SUFFIXES:
            errors.append(f"技能目录里不应有页面或图片文件：{path.relative_to(SKILL_DIR)}")
    runtime_doc = SKILL_DIR / "references" / "runtime.md"
    if runtime_doc.is_file():
        doc = runtime_doc.read_text(encoding="utf-8")
        for name in sorted(RUNTIME_FILES):
            if name not in doc:
                errors.append(f"references/runtime.md 没有说明 scripts/{name}")
        tree = ast.parse((SKILL_DIR / "scripts/workbench.py").read_text(encoding="utf-8"))
        mapping = next(ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "USER_STATUS" for t in node.targets))
        client = (SKILL_DIR / "scripts/client.js").read_text(encoding="utf-8")
        for evidence, display in mapping.items():
            if evidence not in doc or display not in doc:
                errors.append(f"运行指南缺少状态映射：{evidence} → {display}")
            if f'"{evidence}": "{display}"' not in client:
                errors.append(f"客户端状态映射不一致：{evidence}")
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("技能检查失败：")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("技能检查通过：入口、调用名与参考文件路由一致；技能目录只含说明文件和与工作内容无关的运行支持，没有页面或图片。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
