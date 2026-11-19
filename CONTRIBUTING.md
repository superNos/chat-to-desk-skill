# 参与贡献

欢迎改进这个技能。提交之前请先读一下下面几条。

1. **先讲清真实的使用问题。** 例如“Agent 在用户答‘说不清’之后替用户做了决定”。不要把某一类用户的工作台做法写成所有人的规则。
2. **不要加默认模板。** 技能不内置页面、组件或固定栏目。新增的指导应该帮助 Agent 按用户的证据做判断，而不是规定工作台长什么样。
3. **只用虚构数据。** 案例、评估场景、截图里不能出现真实的人名、单位、对话、账号、令牌、内部地址或本机路径。邮箱请用 `example.com` 这类保留域名。
4. **评估材料和技能分开。** `evals/` 里有期望结论，不要从 `skills/` 引用它，也不要把它交给被评估的 Agent。修改技能后，如果会影响推导结果，请重新运行前向演示，不要手工改演示产物。
5. **提交前运行：**

   ```bash
   PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
   python3 scripts/check_skill.py
   python3 scripts/release_check.py
   openspec validate design-personal-agent-workbench --strict
   ```

   最后一条需要另装 OpenSpec 命令行工具；没有安装时请在说明里写明未运行。
6. **改了流程图或对比图**，请同时修改 `docs/diagrams/` 下的源文件和 `docs/images/` 下的截图，截图前确认中文显示正常。
7. 安全问题不要发公开 issue，按 [SECURITY.md](SECURITY.md) 私下报告。
