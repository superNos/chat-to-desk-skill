# personal-agent-workbench 评估材料

本目录用于评估 `$personal-agent-workbench` 是否按证据设计工作台。它**不随技能安装**，因为里面写有期望结论；把它交给被评估的 Agent 会让 Agent 对号入座。

所有人物、订单代号、学生代号、同事代号、日期和对话都是虚构的，不来自任何真实对话。

## 内容

| 文件 | 人物 | 岗位类型 | 期望结论 |
| --- | --- | --- | --- |
| `scenarios/e1-class-teacher.json` | 小学班主任 | 非开发 | 需要工作台 |
| `scenarios/e2-shop-owner.json` | 网店店主 | 非开发 | 轻量做法 |
| `scenarios/e3-business-analyst.json` | 业务分析师 | 非开发 | 需要工作台（历史里讨论过看板但用户否定） |
| `scenarios/e4-solo-developer.json` | 独立开发者 | 开发 | 轻量做法（开发者也不默认得到多任务台账） |
| `scenarios/e5-occasional-user.json` | 偶尔使用的退休用户 | 非开发 | 暂不需要复杂工作台 |
| [checks.md](checks.md) | 差异测试、反模板测试、使用测试的执行方法与评分表 | — | — |
| [demos/](demos/README.md) | E1 班主任与 E3 业务分析师的前向演示产物、截图和差异对比；评估演示，不是默认模板 | 非开发 | — |

每个场景文件包含：

- `authorization`：用户给出的授权范围，其中保存发现（`save_findings`）与保存规则（`save_rules`）分开记录；
- `platform_capabilities`：四项平台能力在该场景中的核实结果；
- `history`：带编号（H1、H2……）的虚构对话摘要；
- `user_calibration`：用户对发现的校准回应；
- `expected`：期望结论、应出现的元素及其证据编号、不应出现的元素、每次需确认的动作、隐私与授权要求；
- `usage_probes`：试用阶段的用户输入或平台事件，及期望去向、状态和是否需要确认。

`expected.elements` 的名称只是判断要点，被评估的产物不必用相同措辞，只要元素作用相同且依据指向相同证据即可。

## 自动校验的范围

`tests/test_personal_agent_workbench.py` 只检查结构：场景数量与类型、证据编号是否存在且经确认、期望元素之间是否不同、是否有“暂不需要”场景、状态和去向取值是否合法、探针状态是否与平台能力一致、保存授权是否分开、公开文件是否含通用敏感模式。它**不能**证明 Agent 的产出质量，产出质量按 [checks.md](checks.md) 人工或由独立复核者判断。

公开测试只含通用敏感模式。维护者若要额外检查真实组织或项目名称，把名称写在仓库外的本机清单（每行一个），运行时用环境变量 `WORKBENCH_PRIVATE_BLOCKLIST` 指向它；清单本身不要提交。
