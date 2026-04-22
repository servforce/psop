# PSOP Docs

本文档目录承载 PSOP 的产品纲领、形式定义、工程概要设计、实现级详细设计与协作规则。

## 推荐阅读顺序

1. [PSOP-Whitepaper-v3.md](./PSOP-Whitepaper-v3.md)
   - PSOP 产品纲领、问题定义、价值主张与长期方向。
2. [PSOP_execution_graph_formal_v5.md](./PSOP_execution_graph_formal_v5.md)
   - `Execution Graph` 的形式定义，以及编译产物和运行时必须满足的约束。
3. [PSOP概要设计v1.md](./PSOP概要设计v1.md)
   - 系统分层、模块边界、当前阶段范围与关键原则。
4. [PSOP前端详细设计v1.md](./PSOP前端详细设计v1.md)
   - `WEB IDE` 的实现级设计基线。
5. [PSOP服务端详细设计v1.md](./PSOP服务端详细设计v1.md)
   - 服务端、编译、运行时、数据库、接口与可观测的实现级设计基线。
6. [agent-rules/general.md](./agent-rules/general.md)
   - 项目协作、文档、开发与提交流程规则。

## 目录约定

- `docs/`
  - 当前有效的架构与实现级基线文档放在根目录。
- `docs/ui/`
  - 预留给未来的交互原型、页面专题补充说明，不承载当前有效详细设计基线。
- `docs/architecture/`
  - 预留给未来的专题扩展设计，不承载当前有效详细设计基线。
- `docs/reference/`
  - 预留给接口字典、配置字典、排障手册等稳定参考材料。
- `docs/agent-rules/`
  - AI agent 与人工协作规则。

## 维护规则

- 当前有效的详细设计基线只有两篇：`PSOP前端详细设计v1.md` 与 `PSOP服务端详细设计v1.md`。
- 如实现与详细设计冲突，先更新根目录下的详细设计文档，再推进实现。
- `docs/ui/`、`docs/architecture/`、`docs/reference/` 下的文件如果未来扩展，只能作为补充材料，不能覆盖根目录基线。
