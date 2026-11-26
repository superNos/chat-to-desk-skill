#!/usr/bin/env python3
"""发布前安全检查（仅标准库）。

在仓库根目录运行：

    python3 scripts/release_check.py

检查项：密钥与令牌、高熵长串、邮箱、电话号码、本机路径、UUID、内部地址、
不在白名单里的外部链接、私有名称清单、图片元数据、隐藏文件与缓存文件、
内部链接和图片替代文字。任何一项不通过时退出码为 1。

检查结果只报告文件、行号和规则名，不输出命中的内容，避免把凭据或联系方式带进终端或持续集成日志。

人名、学校、公司是否明显虚构无法可靠地自动判断。脚本会列出看起来像称呼或机构名的词，
供人工确认，这一部分不影响退出码。

私有名称清单：把真实组织、项目或人名写进发布包以外的一个文本文件（每行一个，# 开头为注释），
运行前用环境变量 WORKBENCH_PRIVATE_BLOCKLIST 指向它。清单本身不要放进发布包。
"""
from __future__ import annotations

import argparse
import math
import os
import re
import struct
import sys
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
BLOCKLIST_ENV = "WORKBENCH_PRIVATE_BLOCKLIST"

# 允许出现的隐藏文件；.git 目录整体跳过。
ALLOWED_HIDDEN = {".gitignore", ".openspec.yaml"}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__", ".ipynb_checkpoints"}
JUNK_SUFFIXES = (".pyc", ".pyo", ".swp", ".swo", "~", ".orig", ".rej", ".bak")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}

# 每条规则都要求前缀后面紧跟实际内容，所以规则源码本身不会被匹配。
CONTENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI 类密钥", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}")),
    ("GitHub 令牌", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("GitHub 细粒度令牌", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("Figma 令牌", re.compile(r"\bfigd_[A-Za-z0-9_-]{16,}")),
    ("AWS 访问密钥", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Slack 令牌", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Bearer 凭据", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
    ("私钥", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    (
        "密码或令牌赋值",
        re.compile(
            r"\b(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key)"
            r"(?:\s*[=:]\s*(['\"])[^'\"\s]{6,}\1|=[^\s'\"<>{}()]{6,})",
            re.I,
        ),
    ),
    ("本机路径", re.compile(r"(?:/Users/[A-Za-z0-9._-]|/home/[A-Za-z0-9._-]|[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]|/var/folders/[A-Za-z0-9]|/private/[A-Za-z0-9])")),
    ("UUID", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("内部代码托管地址", re.compile(r"(?:\bgitlab\.[a-z0-9-]+\.[a-z]{2,}|\bgit@[a-z0-9.-]+:)", re.I)),
    ("内网主机名", re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:internal|corp|intranet|lan)\b", re.I)),
    ("中国大陆手机号", re.compile(r"(?<![\d.])1[3-9]\d{9}(?![\d.])")),
    ("国际电话号码", re.compile(r"(?<![\w.])\+\d{1,3}[\s-]?\(?\d{2,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}(?!\d)")),
    ("固定电话号码", re.compile(r"(?<![\d.-])0\d{2,3}-\d{7,8}(?![\d-])")),
    ("北美格式电话号码", re.compile(r"(?<![\d.-])\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\d-])")),
]

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")
# RFC 2606 保留的示例域名
ALLOWED_EMAIL_DOMAINS = re.compile(r"(?:^|\.)(?:example\.(?:com|org|net)|example|test|invalid|localhost)$", re.I)

URL = re.compile(r"https?://[^\s)\"'<>`]+")
# 以 / 结尾的是站点前缀；其余必须完全相同，或后面紧跟 / ? # 或 .git。
ALLOWED_URL_PREFIXES = (
    "https://github.com/superNos/personal-agent-workbench-skill",
    "https://docs.github.com/",
    "https://opensource.org/",
    "https://keepachangelog.com/",
    "https://semver.org/",
    "https://ant.design/docs/react/customize-theme/",  # 组件库官方主题说明
    "https://element-plus.org/en-US/component/config-provider.html",  # 官方全局配置
    "http://www.w3.org/2000/svg",  # SVG 命名空间标识，不会被访问
)

LONG_TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{32,}")

MD_LINK = re.compile(r"(!?)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
# 形如 [文字](<占位>/路径) 的链接：尖括号在路径中途就闭合了，既不是合法的 Markdown 链接，也打不开
PLACEHOLDER_LINK = re.compile(r"\]\(\s*<[^>\n]*>[^)\s]")
HTML_REF = re.compile(r"\b(href|src)\s*=\s*\"([^\"]+)\"", re.I)
HTML_IMG = re.compile(r"<img\b[^>]*>", re.I)
HTML_ID = re.compile(r"\bid\s*=\s*\"([^\"]+)\"", re.I)
MD_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)

# 只用于人工确认清单：常见姓氏 + 称呼，以及带机构后缀的名称
SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹喻柏窦章苏潘葛范彭鲁马方俞任袁柳鲍史唐薛雷贺倪汤罗毕郝安常于傅齐康余顾孟黄萧尹姚邵汪毛狄米明戴宋庞熊纪舒屈项祝董梁杜阮蓝林钟徐邱骆高夏蔡田胡凌霍万卢莫丁邓郭曾程"
NAME_HINT = re.compile(rf"[{SURNAMES}](?:老师|先生|女士|同学|医生|经理|律师)")
ORG_HINT = re.compile(r"[\u4e00-\u9fff]{1,8}(?:小学|中学|大学|学院|学校|有限公司|股份公司|集团|银行|医院)")


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == ".git":
            continue
        yield path, rel


def shannon_entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


# 本机回环地址和 RFC 2606 保留的示例域名不会指向真实的外部服务
LOCAL_OR_EXAMPLE_HOST = re.compile(
    r"^https?://(?:127\.0\.0\.1|localhost|(?:[a-z0-9-]+\.)*(?:example\.(?:com|org|net)|example|test|invalid))(?::[^/?#\s]*)?(?:[/?#]|$)",
    re.I,
)


def url_allowed(url: str) -> bool:
    if LOCAL_OR_EXAMPLE_HOST.match(url):
        return True
    for prefix in ALLOWED_URL_PREFIXES:
        if prefix.endswith("/"):
            if url.startswith(prefix):
                return True
        elif url == prefix or url == prefix + ".git" or (url.startswith(prefix) and url[len(prefix)] in "/?#"):
            return True
    return False


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def check_junk(root: Path) -> list[str]:
    errors = []
    for path, rel in iter_files(root):
        for part in rel.parts:
            if part in JUNK_NAMES:
                errors.append(f"缓存或系统文件：{rel}")
                break
            if part.startswith(".") and part not in ALLOWED_HIDDEN:
                errors.append(f"隐藏文件或目录：{rel}")
                break
        else:
            if path.is_file() and rel.name.endswith(JUNK_SUFFIXES):
                errors.append(f"缓存或临时文件：{rel}")
    return sorted(set(errors))


def text_files(root: Path):
    """返回 (相对路径, 文本)。无法按 UTF-8 解码的非图片文件记为错误。"""
    result, errors = [], []
    for path, rel in iter_files(root):
        if not path.is_file() or path.suffix.lower() in IMAGE_SUFFIXES:
            continue
        if any(part in JUNK_NAMES for part in rel.parts) or rel.name.endswith(JUNK_SUFFIXES):
            continue
        try:
            result.append((rel, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            errors.append(f"无法识别的二进制文件：{rel}")
    return result, errors


def private_terms() -> tuple[list[str], str | None]:
    value = os.environ.get(BLOCKLIST_ENV)
    if not value:
        return [], None
    path = Path(value).expanduser()
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")], str(path)


def check_content(root: Path) -> list[str]:
    files, errors = text_files(root)
    terms, blocklist_path = private_terms()
    if blocklist_path and Path(blocklist_path).resolve().is_relative_to(root.resolve()):
        errors.append("私有名称清单放在发布包内，请移到发布包以外")
    for rel, text in files:
        for label, pattern in CONTENT_RULES:
            for match in pattern.finditer(text):
                errors.append(f"{rel}:{line_of(text, match.start())}：{label}")
        for match in EMAIL.finditer(text):
            if not ALLOWED_EMAIL_DOMAINS.search(match.group(1)):
                errors.append(f"{rel}:{line_of(text, match.start())}：非示例域名的邮箱")
        for match in URL.finditer(text):
            url = match.group(0).rstrip(".,;:，。")
            if not url_allowed(url):
                errors.append(f"{rel}:{line_of(text, match.start())}：不在白名单里的外部链接")
        for match in LONG_TOKEN.finditer(text):
            token = match.group(0)
            segments = re.split(r"[-_/.]", token)
            if len(segments) >= 3 and all(len(s) <= 16 for s in segments):
                continue  # 文件路径或连字符名称
            has_mix = re.search(r"[A-Za-z]", token) and re.search(r"\d", token)
            if has_mix and shannon_entropy(token) >= 4.0:
                errors.append(f"{rel}:{line_of(text, match.start())}：疑似密钥的高熵长串")
        lowered = text.lower()
        for term in terms:
            index = lowered.find(term.lower())
            if index >= 0:
                errors.append(f"{rel}:{line_of(text, index)}：命中私有名称清单中的一项")
    return errors


def png_chunks(data: bytes) -> list[str]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("不是 PNG 文件")
    chunks, offset = [], 8
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        chunks.append(data[offset + 4 : offset + 8].decode("latin-1"))
        offset += 12 + length
    return chunks


def jpeg_segments(data: bytes) -> list[tuple[int, bytes]]:
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("不是 JPEG 文件")
    segments, offset = [], 2
    while offset + 4 <= len(data) and data[offset] == 0xFF:
        marker = data[offset + 1]
        if marker == 0xDA:  # 图像数据开始
            break
        (length,) = struct.unpack(">H", data[offset + 2 : offset + 4])
        segments.append((marker, data[offset + 4 : offset + 4 + min(length - 2, 16)]))
        offset += 2 + length
    return segments


# 只描述图像本身的 PNG 数据块；文字、EXIF、C2PA 等元数据块一律不允许。
PNG_ALLOWED = {"IHDR", "PLTE", "IDAT", "IEND", "tRNS", "gAMA", "cHRM", "sRGB", "iCCP", "sBIT", "bKGD", "pHYs"}


def check_images(root: Path) -> list[str]:
    errors = []
    for path, rel in iter_files(root):
        suffix = path.suffix.lower()
        if not path.is_file() or suffix not in IMAGE_SUFFIXES:
            continue
        data = path.read_bytes()
        try:
            if suffix == ".png":
                extra = sorted(set(png_chunks(data)) - PNG_ALLOWED)
                if extra:
                    errors.append(f"{rel}：PNG 含元数据块 {', '.join(extra)}")
            else:
                for marker, head in jpeg_segments(data):
                    if marker == 0xE1 or (0xE2 <= marker <= 0xEF and not head.startswith(b"ICC_PROFILE")):
                        errors.append(f"{rel}：JPEG 含 APP{marker - 0xE0} 元数据段")
                    if marker == 0xFE:
                        errors.append(f"{rel}：JPEG 含注释段")
        except ValueError as exc:
            errors.append(f"{rel}：{exc}")
    return errors


def heading_slugs(text: str) -> set[str]:
    """按 GitHub 的规则近似生成标题锚点。"""
    slugs: set[str] = set()
    seen: Counter[str] = Counter()
    for heading in MD_HEADING.findall(text):
        plain = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", plain).replace(" ", "-")
        slugs.add(slug if not seen[slug] else f"{slug}-{seen[slug]}")
        seen[slug] += 1
    return slugs


RUNTIME_CLIENT_URL = "/_workbench/client.js"
RUNTIME_CLIENT_FILE = Path("skills") / "personal-agent-workbench" / "scripts" / "client.js"


def workbench_page_root(path: Path) -> Path | None:
    """工作台目录里 page/ 下的页面返回 page/ 的绝对路径，其它文件返回 None。"""
    for parent in path.resolve().parents:
        if parent.name == "page" and (parent.parent / "workbench.json").is_file():
            return parent
    return None


def check_links(root: Path) -> list[str]:
    """Markdown 与 HTML 中的相对链接必须在发布包内存在；图片必须有替代文字。"""
    errors = []
    root = root.resolve()
    for path, rel in iter_files(root):
        if not path.is_file() or path.suffix.lower() not in {".md", ".html"}:
            continue
        text = path.read_text(encoding="utf-8")
        page_root = workbench_page_root(path)
        targets = []
        # Markdown 代码块里是示例代码（如运行时提供的 /_workbench/client.js），不是发布包内链接
        scan = text
        if path.suffix.lower() == ".md":
            scan = re.sub(r"```.*?```", "", text, flags=re.S)
            for match in PLACEHOLDER_LINK.finditer(scan):
                errors.append(f"{rel}：链接目标是占位写法，打不开：{scan[match.start():match.end() + 30].splitlines()[0]}")
            for bang, label, target in MD_LINK.findall(scan):
                if bang and not label.strip():
                    errors.append(f"{rel}：图片缺少替代文字：{target}")
                targets.append(target)
        for tag in HTML_IMG.findall(scan):
            alt = re.search(r"\balt\s*=\s*\"([^\"]*)\"", tag, re.I)
            if not alt or not alt.group(1).strip():
                errors.append(f"{rel}：<img> 缺少替代文字")
        targets.extend(value for _, value in HTML_REF.findall(scan))
        for target in targets:
            if re.match(r"^(?:https?:|mailto:|data:)", target, re.I):
                continue
            file_part, _, anchor = target.partition("#")
            if file_part.startswith("/") and page_root is not None:
                # 工作台页面里以 / 开头的地址由运行支持解析：/_workbench/ 是运行支持自带的接口封装，其余对应 page/ 目录
                if file_part == RUNTIME_CLIENT_URL:
                    dest = (root / RUNTIME_CLIENT_FILE).resolve()
                else:
                    dest = (page_root / file_part.lstrip("/")).resolve()
                    if not dest.is_relative_to(page_root):
                        errors.append(f"{rel}：链接指向工作台页面目录以外：{target}")
                        continue
            else:
                dest = (path.parent / file_part).resolve() if file_part else path.resolve()
            if not dest.is_relative_to(root):
                errors.append(f"{rel}：链接指向发布包以外：{target}")
                continue
            if not dest.exists():
                errors.append(f"{rel}：链接目标不存在：{target}")
                continue
            if anchor and dest.is_file():
                dest_text = dest.read_text(encoding="utf-8")
                if dest.suffix.lower() == ".html":
                    ok = anchor in HTML_ID.findall(dest_text)
                elif dest.suffix.lower() == ".md":
                    ok = anchor.lower() in heading_slugs(dest_text)
                else:
                    ok = True
                if not ok:
                    errors.append(f"{rel}：锚点不存在：{target}")
    return errors


def review_hints(root: Path) -> list[str]:
    files, _ = text_files(root)
    found: dict[str, set[str]] = {}
    for rel, text in files:
        if rel.suffix not in {".md", ".json", ".html"}:
            continue
        for pattern in (NAME_HINT, ORG_HINT):
            for match in pattern.finditer(text):
                found.setdefault(match.group(0), set()).add(str(rel.parts[0]))
    return [f"{word}（{', '.join(sorted(dirs))}）" for word, dirs in sorted(found.items())]


def run(root: Path) -> dict[str, list[str]]:
    return {
        "隐藏文件与缓存": check_junk(root),
        "文本内容（令牌、联系方式、路径、UUID、链接白名单、私有名称）": check_content(root),
        "图片元数据": check_images(root),
        "内部链接与替代文字": check_links(root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布前安全检查")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="要检查的目录，默认是本仓库")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    results = run(root)
    count = sum(1 for path, _ in iter_files(root) if path.is_file())
    print(f"检查目录中的 {count} 个文件。")
    terms, _ = private_terms()
    print(f"私有名称清单：{'已加载 ' + str(len(terms)) + ' 项' if terms else '未设置 ' + BLOCKLIST_ENV + '，跳过'}")
    failed = False
    for label, errors in results.items():
        if errors:
            failed = True
            print(f"[不通过] {label}：{len(errors)} 处")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"[通过] {label}")
    hints = review_hints(root)
    print("需人工确认是否明显虚构的称呼和机构名（不影响结果）：" + ("、".join(hints) if hints else "无"))
    print("结论：" + ("不通过" if failed else "通过"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
