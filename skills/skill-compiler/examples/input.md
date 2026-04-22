# Example Input

目标目录：

`skills/skill-creator/`

该目录至少包含：

- `SKILL.md`
- `skill.yaml`
- `prompts/system.md`
- `references/README.md`
- `examples/input.md`
- `examples/expected-output.md`
- `tests/checklist.md`

编译要求：

- 检查该目录是否满足当前最小 Skill 标准
- 读取并校验 `skill.yaml`
- 执行编译命令：`python3 skills/skill-compiler/scripts/compile.py --skill-dir skills/skill-creator`
- 生成最小运行产物草稿
- 运行产物满足 Run Server 消费契约（含 `graph_hash_algo`、step/transition 严格结构）
- 生成构建报告
- 记录未消费的扩展内容
