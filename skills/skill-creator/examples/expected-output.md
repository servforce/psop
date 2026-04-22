# Expected Output

期望输出不是一段简介，而是一份完整可审阅的 Skill 草稿包，至少包括：

```text
skills/skill-builder/
├─ SKILL.md
├─ skill.yaml
├─ prompts/
│  └─ system.md
├─ references/
│  └─ README.md
├─ examples/
│  ├─ input.md
│  └─ expected-output.md
└─ tests/
   └─ checklist.md
```

并且每个文件都应有完整草稿内容，例如：

- `SKILL.md`
  清楚说明 `skill-builder` 的目标、工作步骤、限制、产物
- `skill.yaml`
  定义输入、输出、工作流步骤与约束
- `prompts/system.md`
  定义 `skill-builder` 的执行角色与工作原则
- `tests/checklist.md`
  列出如何验证它是否完成了最小编译目标

输出末尾还应附一段审阅提示，提醒用户重点检查：

- 目标是否准确
- 步骤是否合理
- 编译产物定义是否清楚
- 约束是否符合当前阶段范围
