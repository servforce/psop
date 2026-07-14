# PSOP Builder Skill 包

本目录是 `psop.builder` 的单一 Skill 包。根目录 `SKILL.md` 只声明入口规则、权限边界和必须加载的资源；具体构建方法拆分在子目录中，通过 `load_skill_resource` 渐进加载。

## 目录

```text
psop-builder/
  SKILL.md
  README.md
  core/SKILL.md
  evidence-mapping/SKILL.md
  quality-review/SKILL.md
```

## 加载顺序

1. `core/SKILL.md`：先建立 PSOP Skill 源级契约、物理世界任务建模和主构建流程。
2. `evidence-mapping/SKILL.md`：映射关键结论、素材事实、参考资产、行业标准和人工确认缺口前读取。
3. `quality-review/SKILL.md`：提交 builder candidate 前读取并执行发布级自检。

这些子目录中的 `SKILL.md` 是 `psop-builder` 包内资源，不是独立 Agent Skill。它们不声明额外工具权限；实际可见业务工具仍由根 `SKILL.md` 的 `allowed-tools` 与 `AgentDefinition.tools` 交集决定。
