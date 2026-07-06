# PSOP General Rules

本文件定义本项目默认的协作、编码、设计与文档规则。根级 [AGENTS.md](../../AGENTS.md) 只保留最高频摘要，本文件负责展开细则。

## 1. 通用原则

- 任何重要判断都应尽量落在可验证的文档、代码、接口或测试事实上，而不是口头印象。
- 优先做小而完整的改动：文档、接口、实现、验证尽量闭环。

## 2. 架构边界规则

- `PSOP Skill` 是用户创作对象，`EG` 是编译后的形式对象；不要把二者混成一个概念。
- [execution-graph-formal-v5.md](../architecture/execution-graph-formal-v5.md) 是 `EG` 的形式事实源；编译产物与 runtime 推进逻辑都必须与其一致。
- `Session Token` 是唯一正式状态对象，禁止引入并行“第二状态源”。
- `Runtime Kernel` 是唯一正式状态提交边界；agent、tool、worker、sandbox 都不是状态主权者。
- `MCP Gateway` 负责受控接入 MCP server/tool；禁止在业务代码中直接把 reference server 当平台内核。
- LLM 调用必须经过平台认可的受治理入口。Runtime LLM / evidence evaluation 节点通过 `psop.runner` 进入 Agent Harness；compiler、skill test judge、素材分析等非 Runtime Runner 域服务可继续使用 `LlmInferenceGateway`。禁止业务节点自行直连具体模型厂商。
- `Capability Host` 负责把 harness 的能力建议变成正式执行绑定；禁止绕过策略裁决直接执行高风险能力。

## 3. 模块与目录规则

- 后端应显式区分 `skills/`、`compiler/`、`runtime/` 三层，不要把“用户对象”和“运行时对象”混在一起。
- 当前后端包结构以 `backend/app/` 为准；新增代码按 `api / domain / gateway / infra / core` 分层扩张，按领域而不是按“杂项 util”扩张。
- 当前前端以 `static/index.html` 作为主控制台入口，页面片段放在 `static/pages/`，保持静态轻栈，不在 v1 默认引入重量级前端框架。
- 文档按 `overview / architecture / guides / engineering / research / reference / archive` 分层，避免所有知识都堆进一个文件。

## 4. 数据与接口规则

- 外部接口优先围绕 `skill -> publish -> compile -> invocation -> run` 主线组织，而不是直接暴露“用户编辑 EG”的心智模型。
- 数据库结构改动必须同步更新数据字典或详细设计中的对应章节。
- 运行时事件、trace、编译记录、上传对象等记录必须具备稳定 ID、时间戳、来源与关联链路。

## 5. 任务系统与进程规则

- v1 默认采用数据库驱动的 job system，不引入 Redis/Celery 作为默认依赖。
- 当前实现由 FastAPI lifespan 可选启动内置 `RuntimeJobWorker`；长耗时能力应进入 `runtime_job`，不要在 router 中直接执行。独立 `scheduler / sandbox` 仍是后续演进项。
- 任务领取必须具备原子 claim、lease、重试、幂等和恢复机制。
- `Sandbox Manager` 只在需要更强隔离时介入，不作为默认常驻独立主进程。

## 6. 前端与交互规则

- `Skill Studio`、`Publish & Diagnostics`、`Runtime Monitor`、`Replay`、`Observability`、`Gateway Console` 是产品规划中的控制台能力面；当前静态控制台入口以 `Skills`、`智能体`、`任务` 和 deep link 页面为准。
- UI 实现优先复用 `.agents/skills/static-ui/` 中的组织方式、构建方式与样式约束。
- 关键页面必须能追溯到 `skill / skill_version / compile_artifact / invocation / run / trace`。

## 7. 可观测与审计规则

- 新能力默认需要同时考虑 logs、metrics、traces 三类观测面。
- 重要执行链路必须进入 `OpenTelemetry` 关联体系，至少保证 `skill_version_id / compile_request_id / run_id / session_token_id / trace_id / span_id` 可串联。
- 影响正式执行的动作应产生审计记录，尤其是发布、编译、重试、取消、手动事件注入、策略变更、网关配置变更。

## 8. 文档推进规则

- 概要与详细设计文档应始终保持同一个对象边界：用户定义的是 `Skills`，系统编译并执行的是 `EG`。
- 主设计书用于定义系统全貌；专题细化时新增专题文档并从索引文档挂出。
- 如果实现已经偏离设计，应补文档解释，而不是让偏离长期悬空。

## 9. Review 规则

- 评审优先看边界是否正确，再看代码是否优雅。
- 对编译器、运行时、数据库、任务系统、网关策略的改动，优先指出形式定义偏差、回放、恢复、幂等风险。
- 对前端改动，优先确认信息架构、关键状态、异常状态、加载状态是否完整。
