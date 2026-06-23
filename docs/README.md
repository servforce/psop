# PSOP Docs

本文档目录承载 PSOP 的产品纲领、架构基线、接入手册、工程协作规则、技术调研与参考资料。目录按文档用途和稳定性组织，不按临时主题堆放。

## 推荐阅读顺序

1. [产品愿景](overview/vision.md)
   - PSOP 的产品判断、目标业务闭环、智能体定位、阶段目标与术语。
2. [系统架构设计](architecture/system-architecture.md)
   - 当前系统架构基线，覆盖后端、前端、Runtime、Agent Harness、数据模型、API 与迁移策略。
3. [Execution Graph 形式定义](architecture/execution-graph-formal-v5.md)
   - `PSOP-EG`、`Session Token`、运行语义、Harness Runtime 的形式事实源。
4. [工程协作规则](engineering/agent-rules.md)
   - 项目协作、编码、架构边界、文档推进与 review 规则。

## 目录结构

```text
docs/
  overview/      产品愿景、产品规划、路线图、术语等高层文档
  architecture/  系统架构基线、形式定义、核心技术设计
  guides/        面向接入方、使用方、运维方的操作手册
  engineering/   工程协作、开发流程、代码库约定
  research/      外部调研、技术预研、方案比较
  reference/     稳定参考资料、外部材料、原始资产
  archive/       过期但需要保留历史上下文的文档
```

## 事实源优先级

1. `overview/` 下的产品方向、规划与术语。
2. `architecture/` 下的架构基线和形式定义。
3. 当前代码、测试与接口行为。
4. `guides/` 下的正式接入手册。
5. `engineering/` 下的协作与开发规则。
6. `research/` 下的调研建议。
7. `reference/` 和 `archive/` 下的背景材料。

如果文档之间冲突，先按以上优先级判断；如果文档与当前代码事实冲突，先指出冲突，再按任务目标修正文档或实现。

## 维护规则

- 新文档必须放入明确目录，不在 `docs/` 根目录继续堆放专题文档。
- 文件名使用小写英文 kebab-case；文档标题和正文默认使用简体中文。
- 架构事实源只放在 `architecture/`；`research/` 只能提出建议，不直接约束实现。
- 接入方可执行的手册放入 `guides/`；内部协作规则放入 `engineering/`。
- 外部图片、PDF、需求清单等原始材料放入 `reference/assets/` 或 `reference/`。
- 过期文档如仍有历史价值，移入 `archive/`，并在文件开头标注替代文档路径。
