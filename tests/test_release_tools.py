"""发布检查与安装脚本的行为测试（仅标准库）。

测试在系统临时目录里构造小样本，不读写发布包以外的用户文件。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_check = load_script("release_check")
install = load_script("install")


def png_bytes(extra_chunk: bytes | None = None) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    body = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    if extra_chunk:
        body += chunk(extra_chunk, b"Comment\x00sample")
    body += chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + body


class ReleaseCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "a.md").write_text("# 标题\n\n见 [b](b.md) 与 [标题](#标题)。\n", encoding="utf-8")
        (self.root / "docs" / "b.md").write_text("示例邮箱 someone@example.com\n", encoding="utf-8")
        (self.root / "img.png").write_bytes(png_bytes())

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel: str, text: str):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_clean_sample_passes(self):
        """干净样本的每一类检查都没有问题。"""
        self.assertTrue(all(not errors for errors in release_check.run(self.root).values()))

    def test_detects_secrets_contacts_paths_and_ids(self):
        """能发现令牌、真实域名邮箱、手机号、本机路径、UUID 和白名单外链接。"""
        samples = {
            "GitHub 令牌": "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
            "非示例域名的邮箱": "someone@" + "corp-mail.cn",
            "中国大陆手机号": "138" + "00138000",
            "本机路径": "/Us" + "ers/someone/notes",
            "UUID": "123e4567-e89b-42d3-a456-" + "426614174000",
            "不在白名单里的外部链接": "https://" + "intranet-docs.cn/page",
            "密码或令牌赋值": "pass" + "word=hunter22",
        }
        for label, sample in samples.items():
            with self.subTest(label=label):
                self.write("docs/b.md", f"内容 {sample}\n")
                errors = release_check.check_content(self.root)
                self.assertTrue(any(label in e for e in errors), errors)

    def test_output_does_not_echo_matched_values(self):
        """报告只写文件、行号和规则名，不把检出的凭据或联系方式打印出来。"""
        secret = "pass" + "word=demoOnly123"
        phone = "139" + "00139000"
        self.write("docs/b.md", f"第一行\n{secret}\n{phone}\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = release_check.main(["--root", str(self.root)])
        self.assertEqual(code, 1)
        text = output.getvalue()
        self.assertIn("docs/b.md:2", text)
        self.assertNotIn("demoOnly123", text)
        self.assertNotIn(phone, text)

    def test_url_allowlist_is_exact(self):
        """白名单里的仓库地址不能被拼接出的其它地址冒用。"""
        self.assertTrue(release_check.url_allowed("https://github.com/superNos/personal-agent-workbench-skill"))
        self.assertTrue(release_check.url_allowed("https://github.com/superNos/personal-agent-workbench-skill/security"))
        self.assertTrue(release_check.url_allowed("https://github.com/superNos/personal-agent-workbench-skill.git"))
        self.assertFalse(release_check.url_allowed("https://github.com/superNos/personal-agent-workbench-skill" + "-fork"))

    def test_local_paths_cover_common_forms(self):
        """本机路径规则覆盖 /home 下大小写开头的用户名和 /private 下的各类目录。"""
        for sample in ("/ho" + "me/Alice/x", "/pri" + "vate/etc/hosts", "C:\\" + "Users\\bob\\x"):
            with self.subTest(sample=sample):
                self.write("docs/b.md", sample + "\n")
                self.assertTrue(any("本机路径" in e for e in release_check.check_content(self.root)))

    def test_private_blocklist(self):
        """设置私有名称清单后能发现清单里的名称；清单不能放在被检查目录里。"""
        self.write("docs/b.md", "提到了 Sample Internal Project\n")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
            handle.write("# 注释\nsample internal project\n")
        try:
            with mock.patch.dict(os.environ, {release_check.BLOCKLIST_ENV: handle.name}):
                errors = release_check.check_content(self.root)
            self.assertTrue(any("私有名称" in e for e in errors), errors)
        finally:
            os.unlink(handle.name)
        inside = self.root / "blocklist.txt"
        inside.write_text("无关词\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {release_check.BLOCKLIST_ENV: str(inside)}):
            errors = release_check.check_content(self.root)
        self.assertTrue(any("清单放在发布包内" in e for e in errors), errors)

    def test_detects_png_metadata(self):
        """PNG 里的文字元数据块会被发现。"""
        (self.root / "img.png").write_bytes(png_bytes(b"tEXt"))
        self.assertTrue(release_check.check_images(self.root))

    def test_detects_hidden_and_cache_files(self):
        """隐藏文件、.DS_Store 和 __pycache__ 会被发现，.gitignore 允许存在。"""
        self.write(".gitignore", "__pycache__/\n")
        self.write(".env", "x\n")
        self.write("sub/.DS_Store", "x")
        self.write("sub/__pycache__/m.pyc", "x")
        errors = "\n".join(release_check.check_junk(self.root))
        self.assertIn(".env", errors)
        self.assertIn(".DS_Store", errors)
        self.assertIn("__pycache__", errors)
        self.assertNotIn(".gitignore", errors)

    def test_detects_broken_links_and_missing_alt(self):
        """不存在的链接目标、指向外部目录的链接、缺失的锚点和空替代文字都会被发现。"""
        self.write(
            "docs/a.md",
            "# 标题\n\n[x](missing.md) [y](../../outside.md) [z](b.md#没有) ![](../img.png)\n",
        )
        errors = "\n".join(release_check.check_links(self.root))
        self.assertIn("missing.md", errors)
        self.assertIn("发布包以外", errors)
        self.assertIn("锚点不存在", errors)
        self.assertIn("替代文字", errors)


class InstallScriptTest(unittest.TestCase):
    def run_install(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = install.main(list(args))
        return code, output.getvalue()

    def test_preview_writes_nothing(self):
        """不加 --apply 时只预览，不创建任何文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "skills"
            code, out = self.run_install("--dest", str(dest))
            self.assertEqual(code, 0)
            self.assertIn("预览", out)
            self.assertFalse(dest.exists())

    def test_apply_copies_and_never_overwrites(self):
        """--apply 复制技能；目标已存在时停止，原有文件保持不变。"""
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "skills"
            code, _ = self.run_install("--dest", str(dest), "--apply")
            self.assertEqual(code, 0)
            target = dest / "personal-agent-workbench"
            self.assertTrue((target / "SKILL.md").is_file())
            (target / "SKILL.md").write_text("用户自己的改动", encoding="utf-8")
            code, out = self.run_install("--dest", str(dest), "--apply")
            self.assertEqual(code, 1)
            self.assertIn("已存在", out)
            self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "用户自己的改动")


if __name__ == "__main__":
    unittest.main()
