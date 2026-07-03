# PSOP Compiler Skill 包

本目录是 `psop.compiler` 的单一 Skill 包。根目录 `SKILL.md` 只声明入口规则、权限边界和必须加载的资源；具体编译方法拆分在子目录中，通过 `load_skill_resource` 渐进加载。

## 目录

```text
psop-compiler/
  SKILL.md
  README.md
  core/SKILL.md
  contract/SKILL.md
  mapping/SKILL.md
  review/SKILL.md
```

## 加载顺序

1. `core/SKILL.md`：先建立事实边界、source traceability 和主编译流程。
2. `mapping/SKILL.md`：抽取 workflow steps 并形成 scaffold tool 输入前读取。
3. `contract/SKILL.md`：调用 scaffold、validate 或修复 formal-v5 diagnostics 前读取。
4. `review/SKILL.md`：提交 candidate 前读取并执行自检。

这些子目录中的 `SKILL.md` 是 `psop-compiler` 包内资源，不是独立 Agent Skill。它们不声明额外工具权限；实际可见业务工具仍由根 `SKILL.md` 的 `allowed-tools` 与 `AgentDefinition.tools` 交集决定。
