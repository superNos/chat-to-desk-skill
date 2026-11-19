"""personal-agent-workbench 技能与评估材料的结构校验（仅标准库）。

这些检查只守住结构不变量，不证明 Agent 的产出质量；质量按
evals/personal-agent-workbench/checks.md 判断。
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import re
import sys
import unittest
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "personal-agent-workbench"
EVAL_DIR = ROOT / "evals" / "personal-agent-workbench"
DEMO_DIR = EVAL_DIR / "demos"
CHANGE_DIR = ROOT / "openspec" / "changes" / "design-personal-agent-workbench"
SKILL_NAME = "personal-agent-workbench"

CONCLUSIONS = {"需要工作台", "轻量做法", "暂不需要复杂工作台"}
DESTINATIONS = {"新工作", "继续某件事", "修改结果", "需先询问"}
STATUSES = {"已发送", "已开始", "已完成", "待确认", "失败", "需手动完成", "无法确认"}
PROBE_KINDS = {"用户输入", "平台事件"}
CAPABILITIES = {"读取历史", "发起工作", "继续同一工作", "取回结果"}
CAPABILITY_RESULTS = ("已核实", "不支持", "待核实")
USABLE_CALIBRATION = {"确认", "修正", "限定范围"}

# 发布包只含本技能，不应提到或依赖开发仓库里的其它技能、脚本。规则写法不会匹配到本文件自身。
OLD_REPO_REFERENCES = re.compile(r"(ai-control-(?:workflow|panel)\b|\baicp\.py\b|\binstall\.sh\b|codex-control-work(?:flow)\b)")


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_check = load_script("release_check")


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def load_scenarios() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((EVAL_DIR / "scenarios").glob("*.json"))
    ]


def office_files() -> list[Path]:
    """评估演示里 Agent 生成的表格等压缩格式文件。"""
    return [p for p in EVAL_DIR.rglob("*") if p.is_file() and p.suffix in {".xlsx", ".docx", ".pptx"}]


class SkillEntryTest(unittest.TestCase):
    def test_frontmatter_has_name_and_description(self):
        """SKILL.md 的 frontmatter 含 name 与 description，且 name 与目录名一致。"""
        fields = frontmatter((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(fields.get("name"), SKILL_NAME)
        self.assertTrue(fields.get("description"))

    def test_openai_yaml_uses_invocation_name(self):
        """agents/openai.yaml 的默认提示使用准确调用名。"""
        text = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")
        prompt = re.search(r'default_prompt:\s*"(.*)"', text)
        self.assertIsNotNone(prompt)
        self.assertIn(f"${SKILL_NAME}", prompt.group(1))

    def test_references_are_routed_and_exist(self):
        """SKILL.md 链接的参考文件都存在，references 下没有未被路由的文件。"""
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        linked = set(re.findall(r"\]\((references/[^)]+\.md)\)", text))
        for rel in linked:
            self.assertTrue((SKILL_DIR / rel).is_file(), rel)
        present = {f"references/{p.name}" for p in (SKILL_DIR / "references").glob("*.md")}
        self.assertEqual(present - linked, set(), "存在未从 SKILL.md 路由的参考文件")

    def test_relative_links_inside_references_resolve(self):
        """参考文件之间的相对链接都能找到目标。"""
        for path in (SKILL_DIR / "references").glob("*.md"):
            for target in re.findall(r"\]\(([^)#:]+\.md)\)", path.read_text(encoding="utf-8")):
                self.assertTrue((path.parent / target).is_file(), f"{path.name} -> {target}")

    def test_skill_ships_no_fixed_runtime(self):
        """技能不内置页面模板或运行脚本，避免形成默认工作台。"""
        self.assertFalse((SKILL_DIR / "assets").exists())
        self.assertFalse((SKILL_DIR / "scripts").exists())

    def test_eval_material_not_inside_skill(self):
        """含期望结论的评估材料不放进技能目录。"""
        for path in SKILL_DIR.rglob("*"):
            self.assertNotEqual(path.suffix, ".json", path)

    def test_skill_does_not_use_demos_as_template(self):
        """技能文件不引用评估演示，演示说明写明不是默认模板。"""
        for path in SKILL_DIR.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("demos", text, path)
                self.assertNotIn("preview.html", text, path)
        self.assertIn("不是默认模板", (DEMO_DIR / "README.md").read_text(encoding="utf-8"))

    def test_demos_cover_two_scenarios_with_required_outputs(self):
        """每个演示都有工作模型、工作台说明、静态预览、截图和过程记录，预览不引用外部资源。"""
        for name in ("e1-class-teacher", "e3-business-analyst"):
            demo = DEMO_DIR / name
            output = demo / "agent-output"
            # 文件名由被评估的 Agent 自行决定，这里按作用匹配
            names = {p.name for p in output.glob("*.md")}
            self.assertTrue(
                any("工作模型" in n or "我对你工作的理解" in n for n in names), f"{name} 缺工作模型"
            )
            self.assertTrue(any("工作台" in n and "说明" in n for n in names), f"{name} 缺工作台说明")
            for required in ("preview.html", "过程记录.md"):
                self.assertTrue((output / required).is_file(), f"{name} 缺 {required}")
            self.assertTrue((demo / "screenshot.png").is_file(), f"{name} 缺截图")
            self.assertTrue((demo / "transcript.md").is_file(), f"{name} 缺逐轮记录")
            html = (output / "preview.html").read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"(https?:)?//[a-z0-9.-]+\.[a-z]{2,}/", html, re.I), name)


class ScenarioStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = load_scenarios()

    def test_scenario_coverage(self):
        """至少 3 组场景，包含非开发岗位与“暂不需要复杂工作台”结论。"""
        self.assertGreaterEqual(len(self.scenarios), 3)
        self.assertIn("非开发", {s["role_type"] for s in self.scenarios})
        conclusions = {s["expected"]["conclusion"] for s in self.scenarios}
        self.assertIn("暂不需要复杂工作台", conclusions)
        self.assertGreaterEqual(len(conclusions), 2)
        self.assertTrue(conclusions <= CONCLUSIONS, conclusions - CONCLUSIONS)

    def test_ids_are_unique(self):
        """场景编号唯一，每个场景内的证据编号唯一。"""
        ids = [s["id"] for s in self.scenarios]
        self.assertEqual(len(ids), len(set(ids)))
        for s in self.scenarios:
            hist = [h["id"] for h in s["history"]]
            self.assertEqual(len(hist), len(set(hist)), s["id"])

    def test_elements_trace_to_usable_evidence(self):
        """每个期望元素都有证据，且证据存在并经用户确认、修正或限定范围。"""
        for s in self.scenarios:
            hist = {h["id"] for h in s["history"]}
            usable = {
                ref
                for item in s["user_calibration"]
                if item["response"] in USABLE_CALIBRATION
                for ref in item["refers"]
            }
            for item in s["user_calibration"]:
                self.assertTrue(set(item["refers"]) <= hist, s["id"])
            for element in s["expected"]["elements"]:
                self.assertTrue(element["evidence"], f'{s["id"]} {element["name"]} 无证据')
                for ref in element["evidence"]:
                    self.assertIn(ref, hist, f'{s["id"]} {element["name"]} 引用不存在的 {ref}')
                    self.assertIn(ref, usable, f'{s["id"]} {element["name"]} 引用未确认的 {ref}')

    def test_no_workbench_means_no_elements(self):
        """结论为暂不需要时不列元素，并说明何时再评估。"""
        for s in self.scenarios:
            if s["expected"]["conclusion"] == "暂不需要复杂工作台":
                self.assertEqual(s["expected"]["elements"], [], s["id"])
                self.assertTrue(s["expected"].get("reevaluate_when"), s["id"])
            else:
                self.assertTrue(s["expected"]["elements"], s["id"])

    def test_elements_avoid_own_forbidden_list(self):
        """期望元素不命中本场景的禁止清单。"""
        for s in self.scenarios:
            for element in s["expected"]["elements"]:
                for banned in s["expected"]["must_not_include"]:
                    self.assertNotIn(banned, element["name"], f'{s["id"]} {element["name"]}')

    def test_scenarios_get_different_workbenches(self):
        """不同场景的期望元素不重名，避免用同一套模板。"""
        names = {s["id"]: {e["name"] for e in s["expected"]["elements"]} for s in self.scenarios}
        for left, right in itertools.combinations(names, 2):
            self.assertEqual(names[left] & names[right], set(), f"{left} 与 {right} 有相同元素")

    def test_usage_probes_follow_conventions(self):
        """使用探针的去向和状态取值合法；需确认的动作状态为待确认。"""
        for s in self.scenarios:
            for probe in s["usage_probes"]:
                self.assertIn(probe["destination"], DESTINATIONS, s["id"])
                self.assertIn(probe["expected_status"], STATUSES, s["id"])
                if probe["requires_confirmation"]:
                    self.assertEqual(probe["expected_status"], "待确认", f'{s["id"]} {probe["input"]}')

    def test_external_actions_listed_for_confirmation(self):
        """存在需确认的探针时，场景必须列出每次需确认的动作。"""
        for s in self.scenarios:
            if any(p["requires_confirmation"] for p in s["usage_probes"]):
                self.assertTrue(s["expected"]["confirm_each_time"], s["id"])

    def test_platform_capabilities_recorded(self):
        """每个场景分别记录四项平台能力，取值只有已核实、不支持、待核实。"""
        for s in self.scenarios:
            caps = s["platform_capabilities"]
            self.assertEqual(set(caps), CAPABILITIES, s["id"])
            for name, value in caps.items():
                self.assertTrue(value.startswith(CAPABILITY_RESULTS), f'{s["id"]} {name}：{value}')

    def test_probe_status_matches_capabilities(self):
        """未核实发起或继续能力时，相应交接只能是需手动完成，不能声称已发送。"""
        needed = {"新工作": "发起工作", "继续某件事": "继续同一工作", "修改结果": "继续同一工作"}
        for s in self.scenarios:
            caps = s["platform_capabilities"]
            for probe in s["usage_probes"]:
                self.assertIn(probe["kind"], PROBE_KINDS, s["id"])
                if probe["kind"] != "用户输入" or probe["requires_confirmation"]:
                    continue
                capability = needed.get(probe["destination"])
                if capability and not caps[capability].startswith("已核实"):
                    self.assertEqual(
                        probe["expected_status"], "需手动完成", f'{s["id"]} {probe["input"]}'
                    )

    def test_saving_is_authorized_separately(self):
        """发现与规则的保存授权分开记录；背景资料未获保存规则授权时写明处理方式。"""
        for s in self.scenarios:
            auth = s["authorization"]
            self.assertIn("save_findings", auth, s["id"])
            self.assertIn("save_rules", auth, s["id"])
            has_background = any(e["kind"] == "背景资料" for e in s["expected"]["elements"])
            if has_background and not auth["save_rules"].startswith("已同意"):
                self.assertTrue(s["expected"]["authorization_notes"], s["id"])


class PublicSafetyTest(unittest.TestCase):
    def test_package_content_passes_release_rules(self):
        """发布包的全部文本没有令牌、联系方式、本机路径、UUID、白名单外链接或私有名称（设置清单时）。"""
        self.assertEqual(release_check.check_content(ROOT), [])

    def test_images_have_no_metadata(self):
        """发布包里的图片没有文字、EXIF、C2PA 等元数据块。"""
        self.assertEqual(release_check.check_images(ROOT), [])

    def test_internal_links_resolve(self):
        """所有 Markdown 与 HTML 内部链接都能在发布包内找到目标，图片都有替代文字。"""
        self.assertEqual(release_check.check_links(ROOT), [])

    def test_no_references_to_development_repository(self):
        """发布包不提到开发仓库里的其它技能、中控台脚本或旧安装脚本。"""
        for path, rel in release_check.iter_files(ROOT):
            if path.is_file() and path.suffix in {".md", ".json", ".yaml", ".html", ".py", ".txt"}:
                found = OLD_REPO_REFERENCES.search(path.read_text(encoding="utf-8"))
                self.assertIsNone(found, f"{rel} 提到 {found and found.group(0)}")

    def test_office_files_have_no_sensitive_patterns(self):
        """演示中的表格文件内部（含作者等元数据）同样不含敏感模式。"""
        for path in office_files():
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    text = archive.read(name).decode("utf-8", errors="ignore")
                    for label, pattern in release_check.CONTENT_RULES:
                        found = pattern.search(text)
                        self.assertIsNone(found, f"{path.relative_to(ROOT)}:{name} 含{label}")


if __name__ == "__main__":
    unittest.main()
