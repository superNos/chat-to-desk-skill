# 更新记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-11-19

首个公开版本。

### 新增

- `$personal-agent-workbench` 技能：指导用户自己的 Agent 在授权范围内理解用户过去的工作、与用户校准，再按证据共同设计、试用和调整专属工作台。不内置固定页面，允许得出“暂不需要工作台”的结论。包含历史分析、校准访谈、工作台设计、交互约定、平台四项能力核实、质量检查、持续调整和虚构案例。
- 评估材料：5 组虚构用户历史（班主任、网店店主、业务分析师、独立开发者、偶尔使用的退休用户），以及差异测试、反模板测试、使用测试的方法。
- 两个前向演示：班主任与业务分析师两位虚构的非开发用户，由互不相通的新会话按技能推导出的工作台，含工作模型、工作台说明、静态预览、截图、过程记录和差异对比。它们是评估演示，不是模板。
- OpenSpec 变更 `design-personal-agent-workbench`：提案、设计、4 个能力的规格和任务。
- 只用 Python 标准库的脚本：技能检查 `scripts/check_skill.py`、发布前安全检查 `scripts/release_check.py`、默认只预览的安装脚本 `scripts/install.py`，以及对应测试。
- README 的流程图和对比图（HTML 源文件在 `docs/diagrams/`）。
