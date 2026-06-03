# PSOP Docs

本文档目录承载 PSOP 的产品纲领、产品规划、形式定义、工程概要设计、实现级详细设计与协作规则。根目录下的概要、前端详细设计、服务端详细设计按当前代码实现维护，不把尚未落地的表、接口或模块写成已实现事实。

## 推荐阅读顺序

1. [PSOP-Whitepaper-v3.md](./PSOP-Whitepaper-v3.md)
   - PSOP 产品纲领、问题定义、价值主张与长期方向。
2. [PSOP产品安全规划v1.md](./PSOP产品安全规划v1.md)
   - PSOP 产品安全主张、安全旅程、安全能力地图与阶段性规划。
3. [PSOP_execution_graph_formal_v5.md](./PSOP_execution_graph_formal_v5.md)
   - `Execution Graph` 的形式定义，以及编译产物和运行时必须满足的约束。
4. [PSOP概要设计v1.md](./PSOP概要设计v1.md)
   - 当前代码实现的系统分层、模块边界、当前阶段范围与未实现项。
5. [PSOP前端详细设计v1.md](./PSOP前端详细设计v1.md)
   - `static/` Web 控制台的实现级设计基线。
6. [PSOP服务端详细设计v1.md](./PSOP服务端详细设计v1.md)
   - `backend/app` 服务端、编译、运行时、数据库、接口与可观测的实现级设计基线。
7. [PSOP终端接入说明v1.md](./PSOP终端接入说明v1.md)
   - 终端接入开发者手册，覆盖 Invocation、Run、Terminal Session、事件追加、文件上传、WebSocket 与断线恢复。
8. [agent-rules/general.md](./agent-rules/general.md)
   - 项目协作、文档、开发与提交流程规则。

## 目录约定

- `docs/`
  - 当前有效的产品规划、架构与实现级基线文档放在根目录。
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
- 如实现与详细设计冲突，先按当前代码事实修订根目录下的详细设计文档，再推进实现或重构。
- `docs/ui/`、`docs/architecture/`、`docs/reference/` 下的文件如果未来扩展，只能作为补充材料，不能覆盖根目录基线。
