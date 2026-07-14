# PSOP Runner Skill 包

本目录是 `psop.runner` 的单一 Skill 包。根目录 `SKILL.md` 只声明入口规则、权限边界和必须加载的资源；具体运行协作方法拆分在子目录中，通过 `load_skill_resource` 渐进加载。

## 目录

```text
psop-runner/
  SKILL.md
  README.md
  core/SKILL.md
  terminal-guidance/SKILL.md
  evidence-evaluation/SKILL.md
```

## 加载顺序

1. `core/SKILL.md`：先建立 Runtime 状态边界、事实优先级和 observation 主流程。
2. `terminal-guidance/SKILL.md`：生成终端提示、等待说明、停止说明或参考图片 caption 前读取。
3. `evidence-evaluation/SKILL.md`：评估终端文本、图片、音频或视频证据前读取。

这些子目录中的 `SKILL.md` 是 `psop-runner` 包内资源，不是独立 Agent Skill。它们不声明额外工具权限；实际可见业务工具仍由根 `SKILL.md` 的 `allowed-tools` 与 `AgentDefinition.tools` 交集决定。
