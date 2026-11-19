#!/usr/bin/env python3
"""把 personal-agent-workbench 技能复制到 Codex 的技能目录（仅标准库）。

默认只预览，不写任何文件：

    python3 scripts/install.py

确认后加 --apply 才复制：

    python3 scripts/install.py --apply

目标默认是 ${CODEX_HOME:-$HOME/.codex}/skills/personal-agent-workbench。
目标已存在时停止，不覆盖、不删除；需要更新时请先自己处理旧目录。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

SKILL_NAME = "personal-agent-workbench"
SOURCE = Path(__file__).resolve().parents[1] / "skills" / SKILL_NAME
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def default_skills_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return base / "skills"


def planned_files() -> list[Path]:
    return sorted(
        p.relative_to(SOURCE)
        for p in SOURCE.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store" and p.suffix != ".pyc"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装 personal-agent-workbench 技能（默认只预览）")
    parser.add_argument("--dest", type=Path, help="技能目录，默认 ${CODEX_HOME:-$HOME/.codex}/skills")
    parser.add_argument("--apply", action="store_true", help="确认复制；不加时只预览")
    args = parser.parse_args(argv)

    skills_dir = (args.dest.expanduser() if args.dest else default_skills_dir()).resolve()
    target = skills_dir / SKILL_NAME
    files = planned_files()
    print(f"来源：{SOURCE}")
    print(f"目标：{target}")
    print(f"将复制 {len(files)} 个文件：")
    for rel in files:
        print(f"  {rel}")

    if target.exists():
        print("目标已存在，已停止，没有改动任何文件。需要更新时，请先自行备份或移走旧目录再运行。")
        return 1
    if not args.apply:
        print("这是预览，没有写入任何文件。确认无误后加 --apply 再运行一次。")
        return 0

    skills_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE, target, ignore=IGNORED)
    print("已复制。在 Codex 里开一个新对话，输入 $personal-agent-workbench 开始。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
