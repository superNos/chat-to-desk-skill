## 1. 技能入口

- [x] 1.1 创建 `skills/chat-to-desk/SKILL.md`：frontmatter 含 `name`、`description`；正文写循环、不可违反的边界和阶段到参考文件的路由
- [x] 1.2 创建 `skills/chat-to-desk/agents/openai.yaml`，`default_prompt` 使用 `$chat-to-desk`

## 2. 参考资料

- [x] 2.1 `references/history-analysis.md`：授权范围确认话术、三类授权、证据记录、事实与推测区分、敏感信息处理、无历史时的替代方式、工作模型格式
- [x] 2.2 `references/calibration-interview.md`：用用户语言展示发现、确认/修正/否定/限定范围、补充历史中看不到的内容、非开发用户沟通要点
- [x] 2.3 `references/workbench-design.md`：依据链、无默认模板、暂不需要的判断条件、搭建形式选择、小范围试用、工作台说明格式
- [x] 2.4 `references/agent-interaction.md`：输入去向、交接内容格式、结果回写、五种状态及证据、单次确认动作、规则不扩大授权
- [x] 2.5 `references/platform-adaptation.md`：四项能力的核实方法与记录格式，Codex/Claude/WorkBuddy/其它平台均按待核实起步，手动交接降级
- [x] 2.6 `references/quality-checks.md`：交付前和每轮调整前的检查清单，包括依据链、反模板、状态、授权、隐私、非开发用户可读性
- [x] 2.7 `references/evolution.md`：使用证据回顾、调整建议与确认记录、规则管理（保留/修改/限定/删除）
- [x] 2.8 `references/cases.md`：多组差异明显的虚构案例（含开发总控、非开发岗位、暂不需要），说明为何得到不同结论并标明不可复制

## 3. 评估材料

- [x] 3.1 创建 `evals/personal-agent-workbench/` 说明文件，写明用途、不随技能安装、只含虚构数据
- [x] 3.2 编写至少 3 组（含非开发岗位和零散使用）虚构用户历史，每条带证据编号
- [x] 3.3 为每组写期望判断要点：应出现的元素及证据编号、不应出现的元素、结论类型
- [x] 3.4 编写差异测试、反模板测试、使用测试的检查方法

## 4. 自动校验

- [x] 4.1 新增 `tests/test_personal_agent_workbench.py`（仅标准库 unittest，用例说明使用中文），覆盖 design 决策 5 的结构检查与公开安全扫描
- [x] 4.2 新增 `scripts/check_skill.py`，检查技能入口、调用名与参考文件路由
- [x] 4.3 运行 `python3 -m unittest discover -s tests -v` 与 `python3 scripts/check_skill.py` 通过

## 5. 文档

- [x] 5.1 README：面向非开发者的定位、工作方式、两个演示的对比、上手、隐私与授权、适合与不适合、常见问题、仓库结构与验证、许可证
- [x] 5.2 CHANGELOG：v0.1.0 条目
- [x] 5.3 SECURITY：历史分析授权、最小化保存、删除、单次确认、评估材料只用虚构数据
- [x] 5.4 CONTRIBUTING、LICENSE（MIT）与 `.gitignore`

## 6. 迭代与复核

- [x] 6.1 从全新用户和非开发用户两个角度走读技能并修订
- [x] 6.2 运行 `openspec validate design-personal-agent-workbench --strict` 通过
- [x] 6.3 由独立复核者按 specs、反模板、隐私与公开安全要求复核；不通过则返工并再复核，直至通过

## 7. 前向演示

- [x] 7.1 为 E1 班主任和 E3 业务分析师分别在隔离的全新会话中运行技能：只提供技能文件、授权后的模拟平台内容和预设用户回答，不提供期望结论、使用探针或另一场景
- [x] 7.2 每个演示保存工作模型、工作台说明、静态 HTML 预览、截图和过程记录，放在 `evals/personal-agent-workbench/demos/`，并写明不是默认模板
- [x] 7.3 编写两个工作台在工作对象、输入方式、状态、结果回写、授权边界上的对比，并列出对应证据
- [x] 7.4 演示暴露技能缺口时先修改技能再重跑演示；安全扫描覆盖演示中的 `.html`、`.txt` 与表格文件内部内容
- [x] 7.5 由独立复核者复核演示是否由技能推导、是否套模板、元素是否可追溯到证据及公开安全；不通过则返工并再复核，直至在最终状态上通过

## 8. 发布包

- [x] 8.1 整理只含本技能的发布包：技能、评估材料与演示、本变更文档、针对本技能的测试和检查脚本；不依赖、不链接发布包外的文件
- [x] 8.2 新增 `scripts/release_check.py`（仅标准库），扫描密钥与令牌、邮箱、手机号、本机路径、UUID、私有名称清单、图片元数据、隐藏文件与缓存、内部链接
- [x] 8.3 绘制“它是怎么工作的”流程图和两个演示的对比图，截图前确认渲染正确，README 写有意义的替代文字
- [x] 8.4 新增 `scripts/install.py`：默认只预览，加 `--apply` 才复制，目标已存在时停止，不覆盖、不删除
- [x] 8.5 由独立复核者在发布包的最终状态上复核 README 可读性、图片、链接和安全扫描

## 9. v0.2.0 方向修正：主要产出改为可交互工作台

- [x] 9.1 修改 proposal、design 与各能力规格：主要产出是本机可交互工作台，“暂不需要复杂工作台”改为最小工作台；新增 `workbench-local-runtime` 能力
- [x] 9.2 新增运行支持 `skills/chat-to-desk/scripts/workbench.py`（仅标准库）与 `scripts/client.js`：页面服务、记录保存、交接、状态证据、Codex 适配器与 `codex-probe`、`check`；不含任何页面布局或职业字段
- [x] 9.3 新增 `references/runtime.md`，并同步修改 `SKILL.md`、`agents/openai.yaml` 与各参考文件，去掉“可以只是文档”的规则
- [x] 9.4 新增 `tests/test_workbench_runtime.py`：令牌、Host/Origin、目标白名单、请求大小、私有文件、状态不倒退与最新情况未知、手动交接路径、Codex 适配器（假会话）、审批请求拒绝
- [x] 9.5 调整 `scripts/check_skill.py`：技能目录允许且只允许这两个运行支持文件，仍禁止页面、样式和图片；调整场景结论与对应测试
- [x] 9.6 用隔离的临时 CODEX_HOME 和只用于测试的临时对话，核实 Codex 适配器的发起与取回各一次（维护者自述，记录未随发布包公开）；未核实的环节如实记录：Claude、WorkBuddy 未验证，审批拒绝只用假会话测试
- [x] 9.7 在互不相通的全新会话中从头重跑 E1、E3 前向演示，产出工作台配置、页面、工作台说明和过程记录；真实启动工作台，在页面上完成“发起 → 看到状态 → 得到结果”并截图
- [x] 9.8 更新评估说明、演示说明与对比、README、对比图与流程图、CHANGELOG（v0.2.0）、SECURITY
- [x] 9.9 运行全部测试、`scripts/check_skill.py`、`scripts/release_check.py` 与 `openspec validate design-personal-agent-workbench --strict`
- [x] 9.10 由独立复核者在最终文件状态上复核方向、因人而异、可追溯、状态诚实、本机安全、公开安全扫描、README 可读性和截图；不通过则返工并再复核
