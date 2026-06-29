# Architecture

本目录保存 PSOP 的架构事实源和核心技术定义。这里的文档可以约束实现、测试和接入手册。

## 当前文档

- [system-architecture.md](system-architecture.md)
  - 当前系统架构基线，覆盖后端、前端、Runtime、Agent Harness、数据模型、API 与迁移策略。
- [execution-graph-formal-v5.md](execution-graph-formal-v5.md)
  - `PSOP-EG` 与 `Session Token` 的形式定义和运行语义。
- [psop-builder-agent-design.md](psop-builder-agent-design.md)
  - `psop.builder` 智能体的职责边界、核心循环、工具、Agent Skills、校验、上下文和审计设计。

## 维护原则

- 只有已经决定成为项目约束的设计才进入本目录。
- 外部项目调研、对比矩阵和可行性预研放入 `../research/`。
- MVP 实施计划、开发拆分和验收清单放入 `../engineering/plans/`。
- 面向接入方的操作步骤放入 `../guides/`。
- 如果本目录文档之间冲突，以 `system-architecture.md` 和 `execution-graph-formal-v5.md` 的明确约束为准，并尽快修正文档。
