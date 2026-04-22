# PSOP AGENTS

本文只保留本项目最高频、全局、立即生效的协作规则。完整协作规则见 [docs/agent-rules/general.md](./docs/agent-rules/general.md)。

## 1. 架构事实源

- [docs/PSOP-Whitepaper-v3.md](./docs/PSOP-Whitepaper-v3.md) 是产品纲领事实源。
- [docs/PSOP_execution_graph_formal_v5.md](./docs/PSOP_execution_graph_formal_v5.md) 是 `EG` 形式定义事实源。
- [docs/PSOP概要设计v1.md](./docs/PSOP概要设计v1.md) 是系统分层、模块边界与总体约束事实源。
- [docs/PSOP前端详细设计v1.md](./docs/PSOP前端详细设计v1.md) 是 `WEB IDE` 的唯一有效详细设计基线。
- [docs/PSOP服务端详细设计v1.md](./docs/PSOP服务端详细设计v1.md) 是服务端、编译、运行时、数据库、接口与可观测的唯一有效详细设计基线。

## 2. 不可破坏的核心约束

- 用户在 `WEB IDE` 中定义的是 `Skills`，系统编译和执行的是 `EG`。
- `EG Compile Artifact` 必须符合 formal v5。
- `Session Token` 是唯一正式状态对象。
- `Runtime Kernel` 是唯一正式状态主权者。
- `Run != OS 进程`，默认执行模型是 `Run -> Worker -> Sandbox`。
- `Gateway` 负责 skill invocation 的受控接入，不直接持有正式运行时状态。
- `MCP` 是能力协议，不是状态协议。
- 大模型调用必须经过 `LLM Inference Gateway`。
- `Replay + OpenTelemetry` 是默认排障闭环。
- `DeerFlow` 只作为 harness 适配层复用，不接管正式状态。

## 3. 当前阶段优先级

- 优先打通 `Skills -> Publish -> Auto Compile -> Invocation -> Runtime -> Replay / OTel`。
- 当前阶段不扩展租户、用户、权限、复杂审批流。
- 详细设计基线只认根目录下两篇文档，不再把 `docs/ui/` 或 `docs/architecture/` 下的说明文档当作一线事实源。

## 4. 项目内 Skills 使用约定

- 涉及 `job`、`worker`、`scheduler`、`lease`、`retry` 时，优先参考 `skills/job-system-design/`。
- 涉及 Python Web、FastAPI、SQLAlchemy 结构调整时，优先参考 `skills/python-web-refactor/`。
- 涉及 `static/` 下控制台页面、Alpine.js、Tailwind CSS 时，优先参考 `skills/static-ui/`。
