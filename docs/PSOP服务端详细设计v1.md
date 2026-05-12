# PSOP服务端详细设计v1

## 1. 文档说明

### 1.1 文档定位

本文档是 PSOP 服务端、编译链路、运行时内核、数据库、接口与可观测体系的唯一有效详细设计基线。本文档用于指导后端、运行时、基础设施与前后端联调开发直接开工。

### 1.2 事实源

- [PSOP-Whitepaper-v3.md](./PSOP-Whitepaper-v3.md)
- [PSOP_execution_graph_formal_v5.md](./PSOP_execution_graph_formal_v5.md)
- [PSOP概要设计v1.md](./PSOP概要设计v1.md)

### 1.3 设计范围

- 当前范围覆盖 `Skills -> Publish -> Auto Compile -> EG Artifact -> Invocation -> Run -> Replay / OTel` 闭环。
- 当前范围覆盖 `Gateway`、`Runtime Kernel`、`Agent Module`、`MCP Gateway`、`LLM Inference Gateway`、`Terminal Gateway`。
- 当前阶段不覆盖租户、用户、权限、复杂审批流和 SaaS 控制面。

## 2. 设计目标与非目标

### 2.1 设计目标

- 让 skill 的发布、编译、调用、执行、回放、审计形成完整工程闭环。
- 把 formal v5 对 `EG artifact` 和 `runtime loop` 的约束落到可实现的服务端对象和接口上。
- 采用稳定的 `API + Worker + Scheduler` 进程模型，支持本地开发和企业私有部署。
- 保持状态主权清晰：`Session Token` 与 `Runtime Kernel` 是正式状态体系，gateway、agent module、MCP、模型调用都只能围绕其工作。

### 2.2 非目标

- 不在 v1 设计多租户隔离模型。
- 不在 v1 引入 Celery、Redis 或分布式队列中间件作为默认路径。
- 不把 DeerFlow 作为产品级模块边界或唯一实现来源。
- 不把 `MCP` 或 LLM provider 当作正式状态系统。

## 3. 共享事实源与不可变约束

- `Skills` 是用户定义对象；`EG` 是编译产物；`Runtime` 执行的是 `EG Compile Artifact`。
- `Skill source` 的正式事实源是 `GitLab revision`；数据库只保存元数据、冻结引用与索引快照。
- `EG Compile Artifact` 必须满足 [PSOP_execution_graph_formal_v5.md](./PSOP_execution_graph_formal_v5.md) 的形式约束。
- `Session Token` 是唯一正式状态对象，运行时的每次推进都必须可回放到 snapshot 与 trace。
- `Runtime Kernel` 是唯一正式状态主权者，负责 `Sync -> Enabled -> Sel -> Actor -> Merge -> Trace` 推进。
- `Run != OS 进程`。一个 run 是数据库对象，由 worker 承载执行，只有高风险节点才向 sandbox 借用隔离环境。
- `Gateway` 负责 invocation 接入与 I/O 模拟，不直接写正式状态。
- `Terminal Gateway` 不是普通命令行终端，而是 Web、模拟器、IoT、AR 与真实设备输入输出的统一运行时交互入口。
- 所有终端输入输出最终归一为 append-only `terminal_event`；WebSocket、MQTT、OPC-UA、设备 adapter 与内部 bus 都不是状态源。
- 内部 bus / queue 只用于唤醒 worker 或通知 run 有新事件；Runtime 恢复时必须能按 `run_id + seq_no` 从 `terminal_event` 补读。
- `Replay + OpenTelemetry` 是默认可观测与审计闭环。

## 4. 技术栈

| 维度 | 选型 | 说明 |
| --- | --- | --- |
| 语言 | `Python 3.11+` | 与当前项目脚手架一致 |
| Web 框架 | `FastAPI` | 适合 REST + WS + OpenAPI |
| API Contract / 校验 | `Pydantic v2` | 统一接口契约与内部模型边界 |
| ORM | `SQLAlchemy 2.x` | 明确会话与事务边界 |
| 数据库驱动 | `psycopg 3.x` | PostgreSQL 原生能力 |
| 关系型数据库 | `PostgreSQL 16+` | 主存储，承载状态、任务、索引 |
| 对象存储 | `S3-compatible / MinIO` | 承载 artifact、大对象证据、terminal 二进制数据 |
| Skill 仓库 | `GitLab` | 作为 skill source of truth，承载分支、tag、commit 与发布引用 |
| 可观测 | `OpenTelemetry SDK + Collector` | trace、metrics、logs 统一采集 |
| 应用服务 | `uvicorn` / `gunicorn + uvicorn worker` | 开发与部署双形态 |
| 运行隔离 | 进程 / 容器化 sandbox | 只对高风险节点启用 |

## 5. 进程模型与部署拓扑

### 5.1 进程模型

| 进程 | 职责 |
| --- | --- |
| `api` | 提供 REST、WebSocket、MCP 入口，接收 invocation 与控制请求 |
| `worker` | 执行 compile job、runtime job、gateway async job |
| `scheduler` | 扫描待执行 job、处理 lease 超时、重试、补偿 |

### 5.2 默认部署拓扑

```text
db            -> PostgreSQL
object-store  -> MinIO / S3-compatible
otel          -> OpenTelemetry Collector
app           -> api + worker + scheduler
sandbox       -> 按需创建的进程或容器，不常驻
```

### 5.3 拓扑原则

- 开发环境可在单机上运行 `api + worker + scheduler`。
- 生产环境优先同镜像多进程部署，避免 v1 过早分裂过多服务。
- `Sandbox Manager` 不作为常驻独立主进程；只在需要隔离时申请 lease。

### 5.4 本地环境变量约定

- PostgreSQL 与 GitLab 仍是基础必需配置，继续通过 `PSOP_DATABASE_*` 与 `PSOP_GITLAB_*` 注入。
- 对象存储通过 `PSOP_OBJECT_STORE_*` 注入，用于保存 `EG Compile Artifact`、大对象证据与未来 terminal 二进制内容。
- OpenTelemetry 通过 `PSOP_OTEL_*` 注入；本地默认使用 OTLP HTTP/protobuf `4318`，可关闭采集，但 trace 关联键和 `trace_event` 写入不能关闭。
- LLM Inference Gateway 初始采用 OpenAI-compatible 配置：`PSOP_LLM_PROVIDER=openai-compatible`、`PSOP_LLM_API_BASE_URL`、`PSOP_LLM_API_KEY`、`PSOP_LLM_DEFAULT_MODEL`。
- Runtime worker 通过 `PSOP_RUNTIME_*` 控制是否启用、job lease、最大尝试次数和单步超时。
- v1 默认不要求 Redis、Celery 或独立消息队列；所有 compile/runtime job 以 PostgreSQL 表作为事实源。

## 6. 模块拆分与职责

### 6.1 `SkillsModule`

- 负责 `skill_definition`、`skill_version`、`skill_publish_record` 以及 GitLab 仓库绑定管理。
- 负责草稿版本、已发布版本、版本冻结、发布记录与对 `Web IDE` 的读写模型。
- 负责维护 PostgreSQL 中 draft `skill_version.manifest_snapshot`，它是用户可视化配置、系统默认规则与 Skill 基础信息合成后的结构化机器契约草稿。
- 负责在保存、发布前根据 draft `manifest_snapshot` 生成或刷新发布版本的 frozen `manifest_snapshot`；用户不直接维护 `skill.yaml`。
- 负责在发布完成后创建 `skill_compile_request`，但不直接承担编译执行。

### 6.2 `Skill` 形式定义

- 一个正式 `skill` 至少由以下内容组成：
  - `identity`：`key`、`name`、`description`
  - `repository binding`：`gitlab_project_id`、`default_branch`、`manifest_path`
  - `source revision`：用于草稿或发布的 `branch / tag / commit SHA`
  - `execution goal`：该 skill 要帮助用户在现实世界达成的目标
  - `applicability`：适用条件、不适用条件、前置假设和停止边界
  - `required materials/tools`：用户、设备或环境需要准备的工具、材料、账号、系统权限或现场条件
  - `environment context`：终端、设备、现场、操作系统、网络、传感器或外部系统上下文
  - `workflow steps`：现实操作步骤，不是 LLM 自动执行步骤
  - `step completion criteria`：每个现实步骤的完成标准
  - `evidence requirements`：每步可接受的文本、图片、视频、音频、文件、传感器或设备反馈
  - `safety constraints`：风险提示、禁止操作、降级条件和必须停止的情形
  - `recovery paths`：失败、证据不足、不适用、用户中断或危险状态时如何恢复、重试、退出或转人工
  - `final acceptance criteria`：什么时候才算 skill 真正帮助用户完成现实目标
  - `interface contract`：入口、终端能力、持续交互语义、外部可见调用方式
  - `capability declarations`：所需 `terminal`、`MCP tool`、`LLM model`、`sandbox` 等能力声明
  - `compile config`：formal revision、编译目标、可选 `domain_pack`、校验规则
  - `runtime policy`：超时、重试、预算、并发、隔离等级
  - `publish metadata`：版本号、发布说明、发布时间、来源 revision
- `Skill` 是用户在 `Web IDE` 中定义与维护的正式现实世界任务契约，不是 `EG source`，也不是聊天 prompt 或一次性自动任务脚本。
- 新的 terminal invocation 不把 `input_envelope.user_input` 作为正常入口；`Invocation` 表示用户选择 skill 并建立连接，后续用户反馈和现场证据通过 `terminal_event` 进入。
- `Compiler` 必须基于这一形式定义生成符合 formal v5 的 `EG Compile Artifact`。

### 6.3 `GitLabSkillRepository`

- `GitLab` 是用户可读 source 的事实源，默认承载 `SKILL.md`、`README.md`、示例、脚本、引用资料等用户维护内容。
- `PostgreSQL` 是结构化机器契约的事实源，默认通过 draft `skill_version.manifest_snapshot` 承载当前草稿 manifest，通过 published `skill_version.manifest_snapshot` 承载发布冻结 manifest。
- `skill_definition` 绑定到 `GitLab project`、默认分支与可选 `manifest_path`；`skill_version` 同时绑定具体 `git ref` 与冻结的 `manifest_snapshot`。
- 发布动作必须冻结到明确的 `commit SHA`，并同时冻结当时的 `manifest_snapshot`；编译只读取冻结 commit 下的用户 source 与 frozen manifest snapshot，不读取草稿分支头或实时配置。
- `skill.yaml` 不再是用户必须编辑的源文件；如写入 GitLab，只能作为 PSOP 根据当前草稿 `manifest_snapshot` 生成的只读编译视图，用于预览、离线复现、代码审查和跨系统迁移。

#### 6.3.1 `manifest draft / snapshot` 生成规则

- `SKILL.md` 是用户与 Agent 面向的执行说明正文，负责描述目标、适用边界、现实步骤、完成标准、证据要求、约束、示例与注意事项。
- `README.md` 负责补充用户可读说明、示例、背景知识和使用建议，不替代机器契约。
- draft `skill_version.manifest_snapshot` 是机器契约草稿和编译视图，来源于 Web IDE 表单、Skill 基础信息、系统默认规则、受控策略模板以及当前 `SKILL.md` / `README.md` 正文；它必须冻结能力、策略、交互要求、证据要求和安全边界。
- `manifest snapshot` 是发布时冻结的结构化契约，必须与 `skill_version.source_commit_sha` 一起被写入 `skill_version`。
- `manifest_snapshot.prompt_material` 必须保存当前草稿的 `SKILL.md` 与 `README.md` 正文；`SKILL.md` 变更会重建 draft snapshot 的 prompt material，但不会自动改写输入输出、capability 或 runtime policy 等结构化机器契约字段。
- draft snapshot 是随草稿变化的临时投影，不维护单独审计或变更记录；只有发布时复制出的 published snapshot 才会作为编译输入产生正式影响。
- `skill.yaml` 是 `manifest snapshot` 的可选序列化视图；系统可以在创建或发布时写回 GitLab，但普通编辑页默认隐藏或标记为系统生成文件。
- 准确性保障不依赖自然语言抽取，而依赖当前 source 到 snapshot 的确定性投影、结构化字段校验、默认值填充、编译 diagnostics 和运行时 artifact 绑定。

### 6.4 `SkillCompiler`

- 负责从 GitLab 中某个冻结 `skill version` 到 `EG Compile Artifact` 的转换。
- 输出 `eg_compile_artifact`、`compile_diagnostic` 和静态分析摘要。
- 编译成功的前提是 artifact 能映射到 formal v5 定义。
- 编译目标固定为现实世界协作执行 `EG`，不得生成一次输入后自动执行到 `terminal(success)` 的纯线性自动图。
- 不负责 skill 发布、仓库绑定或版本冻结；`/api/compiler/*` 只承载编译相关接口。
- 编译输入固定来自冻结 `source_commit_sha` 下的 `SKILL.md`、`README.md`、后续扩展目录，以及 `skill_version.manifest_snapshot`；不得读取草稿分支头或工作区临时内容。
- 如果 GitLab 中存在系统生成的 `skill.yaml`，编译器只能把它作为只读编译视图校验是否与 `manifest_snapshot` 一致，不得以用户可修改文件覆盖数据库中的冻结机器契约。
- `SkillCompileAgent` 的系统提示词、输入契约、修复契约与输出 schema 必须来自 repo-backed `Agent Prompt Pack`，当前默认包为 `skill_compilation/formal_v5_compile/v1`。
- 行业差异不拆分编译器；编译器从 `skill.compile_config.domain_pack` 选择可选 `Domain Pack`，当前内置 `generic/v1`、`industrial_inspection/v1`、`equipment_maintenance/v1`，未知值回退到 `generic/v1` 并写入 warning diagnostic。
- 编译流程分为四阶段：
  - `parse`：读取 published `manifest_snapshot`，其中 `prompt_material.skill_md` 和 `prompt_material.readme` 是发布时冻结的 agent-facing 说明与用户说明；冻结 commit 下的文件可作为一致性校验和兜底读取。
  - `normalize`：构造 `SkillCompileAgent` 的输入上下文，包括 Skill metadata、`SKILL.md`、`README.md`、manifest snapshot、Agent Prompt Pack metadata、Domain Pack guidance、允许的节点类型、actor、tool、受控 DSL 约束和现实世界协作执行 profile。
  - `infer`：通过 `LLM Inference Gateway` 调用 SKILL 编译智能体，要求其输出 formal v5 EG candidate；每次调用必须记录 agent id、prompt version、prompt hash、domain pack id 与 domain pack hash；如果输出不是合法 JSON 或不满足校验，服务端将 diagnostics 回传给智能体做一次修正。
  - `validate`：由服务端先对常见 LLM 近似 DSL 做确定性规范化，例如 `op/value` guard、历史 `token.user_input` 路径别名等，再校验 formal revision、必需字段、节点类型、guard DSL、merge DSL、actor/tool 白名单、启动节点、等待 checkpoint、恢复 phase、证据评估链路和终止条件；所有错误写入 `compile_diagnostic`。
  - `emit`：生成 formal v5 `EG Compile Artifact`，写入对象存储，并在 `eg_compile_artifact` 与 `artifact_object` 中建立索引。
- 编译成功至少产生三个逻辑产物：
  - `eg.compile.artifact.json`：RuntimeKernel 的正式执行输入，包含 graph、节点定义、guard、actor、merge、policy 与 metadata。
  - `compile_summary.json`：供前端展示的图摘要、能力摘要、输入输出摘要与静态分析摘要。
  - `diagnostics.json`：warnings、notes 与 source location，失败时作为定位依据。
- 成功 artifact 顶层必须包含 `compiler_metadata`，记录 Agent Prompt Pack 与 Domain Pack 的版本和 hash，用于 Replay、OTel 与问题排查。
- `skills/skill-compiler` 当前目录内的 step/transition v1 契约只能作为历史参考；服务端正式编译目标必须以 formal v5 和本文档为准。

#### 6.4.1 SKILL 编译智能体

- SKILL 编译智能体只负责把 Skill source 转换为 formal v5 EG candidate，不执行业务任务，不调用外部工具，不输出非 JSON 内容。
- 智能体系统提示词必须固化 formal v5 的核心语义：Session Token、guarded rewrite、Prompt View、Actor、Merge、Halt 与 policy。
- 智能体系统提示词必须固化 PSOP 的现实世界协作执行语义：连接建立后 Runtime 可以主动介绍任务并给出第一步指令；每个现实操作步骤都必须可等待、可恢复、可评估；完成状态必须代表现实目标已被验证达成。
- 智能体系统提示词不得硬编码在业务 service 中；应从 `backend/app/agents/skill_compilation/formal_v5_compile/v*/system.md` 读取，并通过 hash 追踪。
- `Domain Pack` 只能提供行业术语、常见流程、质量标准和安全提醒，不能改变 formal v5、actor/tool 白名单、guard DSL、merge DSL 或 Runtime 状态主权。
- 智能体输出只是一份候选产物；是否可运行由服务端 validator 决定，不能直接信任模型输出。
- MVP 支持节点类型：`start`、`input`、`llm`、`tool`、`terminal`；`approval`、`timer`、`skill` 可作为 formal v5 已知类型保留，但当前 Runtime 不执行。
- MVP 支持 actor：`runtime.start`、`runtime.input`、`agent.llm`、`capability.demo_tool`、`runtime.terminal`；tool 白名单仅包含 `psop.demo.inspect_input`。
- MVP guard/merge 不允许生成或执行任意代码，只允许受控 JSON DSL：`always`、`phase_is`、`field_exists`、`field_equals`、`all`、`any`、`not` 和 `op=set`。
- 编译智能体应把不同 skill 统一编译为以下状态机片段，而不是照搬某个任务示例：
  - `bootstrap/introduction`：建立任务上下文，输出任务摘要、适用范围、安全提醒、准备要求和第一步入口。
  - `instruct_step`：生成当前现实步骤的可执行终端指令。
  - `wait_step_evidence`：进入合法等待，声明等待原因、期望输入类型和恢复 phase。
  - `evaluate_step_evidence`：根据 terminal event、artifact object、设备反馈或传感器事实判断步骤是否完成。
  - `recover_or_retry`：证据不足、执行失败、不适用、危险或用户中断时给出恢复路径、重试指令、退出建议或转人工信号。
  - `final_verify`：验证最终完成标准。
  - `terminal(success/failure)`：只在完成标准被验证或失败条件明确后终结。
- `runtime_contract` 必须沉淀跨 skill 通用执行契约：`execution_goal`、`applicability`、`workflow_steps`、`expected_evidence`、`safety_constraints`、`wait_checkpoints`、`completion_criteria`、`recovery_paths`。
- 新 artifact 不允许出现 `input -> step1 -> step2 -> terminal(success)` 的纯线性自动执行结构；中间指导输出不得写入 `outputs.final_response`，`terminal(success)` 前必须经过完成标准验证。

#### 6.4.2 Formal v5 Artifact 最小结构

编译产物至少包含：

- `formal_revision`
- `schema`
- `nodes`
- `init`
- `halt`
- `policies`
- `dependency_graph_for_view`
- `runtime_contract`

`dependency_graph_for_view` 仅供前端展示和静态分析，不是运行时固定边；正式可执行性仍由 Session Token 和 guard 动态诱导。

`runtime_contract` 除 `workflow_steps` 外，必须能够支持现实世界协作执行：

- `execution_goal`：现实世界目标。
- `applicability`：适用条件、不适用条件和停止边界。
- `expected_evidence`：每类等待点可接受的 terminal event kind、mime type、artifact object 或设备反馈。
- `safety_constraints`：运行时必须提示、遵守和回放的安全约束。
- `wait_checkpoints`：等待原因、当前步骤、期望输入、恢复 phase、超时或取消策略。
- `completion_criteria`：最终成功前必须验证的条件。
- `recovery_paths`：重试、补充证据、回退、失败、取消或转人工的路径。

### 6.5 `RuntimeKernel`

- 加载 compile artifact。
- 驱动 `Sync -> Enabled -> Sel -> Actor -> Merge -> Trace` 循环。
- 写入 `session_token_snapshot` 与 `trace_event`。
- 管理 run 生命周期与 terminal waiting 状态。
- RuntimeKernel 是唯一允许推进 `run.status`、`runtime_phase`、`latest_snapshot_seq` 和正式 Session Token 的模块。
- 每次小步推进必须在同一事务边界内完成：读取当前 snapshot、计算 enabled set、选择节点、执行 actor、merge observation、追加 snapshot 与 trace event。
- 终止条件包括：`final` 节点完成、actor 失败且不可重试、预算耗尽、run 超时、用户取消、或进入 `waiting_input`。
- Runtime 启动后不应空等首条用户输入；如果 artifact 中存在 introduction、preparation 或 first instruction 节点，Runtime 应主动推进到第一个合法等待 checkpoint。
- RuntimeKernel 不直接调用模型、MCP 或外部 tool；所有能力调用都经 `CapabilityHost`，并将返回 observation 后再 merge。
- RuntimeKernel 不直接监听 WebSocket、设备连接、MQTT、OPC-UA 或内部 bus；它只在 `Sync` 阶段按 run cursor 读取已持久化的 `terminal_event`。
- `terminal_event` 只有经过 `Merge` 合入 `Session Token` 后才影响正式运行状态，Gateway、adapter 与前端都不能绕过 RuntimeKernel 写正式状态。
- 运行态不能读取未发布草稿；只能加载与 `skill_invocation.compile_artifact_id` 绑定的 artifact。
- 完成状态必须代表现实任务完成标准已被验证，而不是“系统已经把步骤讲完”。
- 中间 terminal 指令、等待 checkpoint、证据评估结果和恢复决策都必须通过 snapshot / trace / terminal transcript 可回放。

`Session Token` 在现实世界协作执行中至少需要表达以下运行态字段：

- 当前现实步骤与对应 `workflow_step_id`。
- 当前等待原因、期望输入类型、恢复 phase 和 checkpoint id。
- 最近消费的 terminal event seq，以及该事件绑定到哪个 checkpoint。
- 用户证据、设备反馈或对象证据引用。
- 当前步骤的评估结果：`proceed | retry | need_more_evidence | abort | complete`。
- 下一步恢复 phase、失败原因或最终验证状态。

#### 6.5.1 小步推进细则

1. `Sync`：把 gateway 输入、terminal event 或取消信号同步到当前 Token 的只读候选视图；terminal 输入按 `run_id + seq_no cursor` 从 `terminal_event` 补读，无外部变化时保持空同步。如果 Token 当前处于等待 checkpoint，新的 terminal input 必须绑定到该 checkpoint，并清除等待状态、设置恢复 phase。
2. `Enabled`：根据 artifact guards 与当前 Token 计算 enabled nodes，并写入 snapshot 的 `enabled_set`。
3. `Sel`：MVP 使用 artifact 中的 priority 与节点 id 稳定排序选择 enabled 节点；多候选时必须记录 `selection_summary`。
4. `Actor`：根据节点类型调用本地 actor、AgentModule 或 CapabilityHost，并得到 observation，不直接改 Token。指令型节点优先返回 `terminal_message`，判断型节点优先返回结构化 `decision`、`reason`、`next_phase` 和可选 `terminal_message`。
5. `Merge`：由 RuntimeKernel 按 artifact merge rule 将 observation 合入 Token，生成新的 `token_payload` 与 `snapshot_hash`。若节点声明需要输出到终端，必须先追加 `terminal_event(direction=output)`；若节点声明 `wait_after_output`，必须写入 wait checkpoint 并将 run 置为 `waiting_input`。
6. `Trace`：追加 `trace_event`，并把 `run_id`、`compile_artifact_id`、`node_id`、`tool_call_id`、`span_id` 等关联键写入事件 payload。

#### 6.5.2 等待与恢复语义

- 指令节点可以向 terminal 输出中间指导，但不得把该指导写为 `outputs.final_response`。
- wait checkpoint 必须记录 `wait_reason`、`expected_inputs`、`checkpoint_id`、`workflow_step_id`、`resume_phase`。
- `Run` 进入等待时状态为 `waiting_input`，`runtime_phase` 应能表达当前等待点。
- 新 terminal input 到来后，`Sync` 读取事件并写入当前 checkpoint 的证据集合；只有 RuntimeKernel merge 后该输入才影响正式状态。
- evaluation 节点根据当前证据输出结构化判断；`proceed` 进入下一步，`retry` 回到当前步骤指令，`need_more_evidence` 返回等待，`abort` 进入失败或转人工路径，`complete` 进入最终验证。
- `terminal(success)` 只能在 `final_verify` 或等价完成标准验证节点之后进入。

### 6.6 `CapabilityHost`

- 把运行时节点对能力的需求映射到 gateway 能力。
- 负责 capability binding、policy 检查、超时和预算控制。
- 所有 LLM、MCP/tool、terminal、sandbox 能力调用都必须通过 CapabilityHost 进入，不允许 actor 绕过统一边界直连 provider。
- CapabilityHost 读取 artifact 中的 `capability_binding` 与 runtime policy，执行目标解析、预算扣减、超时控制、错误归一化和 trace metadata 生成。
- CapabilityHost 负责把 artifact 级抽象能力声明解析为本次 run 的 `run_capability_binding`，例如把 `terminal.text.input.v1` 绑定到 Web terminal，或把 `sensor.temperature.reading.v1` 绑定到具体设备、channel 与 schema。
- CapabilityHost 必须将 terminal 输出、LLM 推理、设备反馈、MCP/tool 结果统一归一为 actor observation，由 RuntimeKernel 决定是否进入 Token。
- 对等待 checkpoint 需要的能力，CapabilityHost 只负责能力解析与 policy 校验，不直接决定步骤完成与否。
- issue #1 的内置 demo tool 也必须注册为受控 capability，便于未来平滑替换为 MCP tool。
- CapabilityHost 返回的是 observation 和调用审计摘要；它不写 Session Token，不直接更新 run 终态。

### 6.7 `TerminalGateway`

- 接收来自 Web IDE、模拟器、IoT、AR 与真实设备的文本、图像、语音、视频、传感器读数、设备 ACK 等输入输出。
- 把外部输入输出统一封装为 append-only `terminal_event`，不直接改写运行时状态。
- 对 WebSocket、MQTT、OPC-UA、Modbus、设备 SDK 等外部协议只做 adapter 接入和 DTO 归一化；原始协议不得直接进入 RuntimeKernel。
- 校验 `run_id`、`terminal_session_id`、`run_capability_binding_id`、`event_kind`、`mime_type`、schema、幂等键、大小限制、速率限制与 policy 后才允许追加事件。
- 小文本、小 JSON 可写入 `payload_inline`；图片、音频、视频、大日志与大批量传感器数据必须写入对象存储并通过 `artifact_object_id` 引用。
- `direction=output` 可记录 Runtime 给用户的任务摘要、准备事项、现实步骤指令、补充证据请求、失败说明和最终结果。
- `direction=input` 可记录用户确认、文本描述、图片、视频、文件、设备 ACK、传感器读数等现场证据。
- 事件落库后可以通过内部 bus / queue 唤醒 worker 或通知前端，但 bus 不是状态源，丢失通知时 Runtime 必须能从 `terminal_event` 补读恢复。

### 6.8 `MCPGateway`

- 管理 MCP server 接入、tool discovery、tool 调用与返回归一化。
- 只暴露受控 tool，不承担状态主权。

### 6.9 `LLMInferenceGateway`

- 提供模型 provider、模型路由、结构化输出、fallback、配额与调用审计。
- 统一所有模型调用，避免业务节点直连 provider。

### 6.10 `AgentModule`

- `AgentModule` 是 PSOP 中与智能体相关的正式模块边界，负责 agent runtime、sub-agent、memory、planning、tool-use orchestration 与 harness 抽象。
- `AgentModule` 负责消费 repo-backed `Agent Prompt Assets` 与可选 `Domain Packs`，但提示词资产本身不是正式运行时状态对象。
- `Agent Prompt Assets` 第一层按 `skill_creation`、`skill_compilation`、`runtime_execution` 等职责/场景分类；行业知识只能作为 `domain_packs/*/v*` 注入，避免为每个行业复制一套智能体。
- 可借鉴或复用 DeerFlow 的优秀设计与实现，但 `DeerFlow` 不是产品事实源，也不是需要原样照搬的正式模块。
- `AgentModule` 只为 `Actor` 执行提供智能体能力，不拥有正式状态主权。
- 在 issue #1 中，AgentModule 的最小职责是：构造 LLM Prompt View、调用 LLM Inference Gateway、解析模型输出、决定是否需要受控 tool，并把结果归一化为 actor observation。
- Runtime Agent 的职责是根据当前 `EG`、`Session Token`、checkpoint 证据和节点投影稳定执行当前节点，不重新规划整个 skill，不绕过 artifact 自行决定状态迁移。
- 指令型 LLM 节点应输出结构化 `terminal_message`；判断型 LLM 节点应输出结构化 `decision`、`reason`、`next_phase` 和可选 `terminal_message`。解析失败必须进入可观测错误或失败路径，不能静默继续。
- AgentModule 可以维护短生命周期执行上下文，但该上下文不是正式状态；进程重启后必须能从 `session_token_snapshot` 与 artifact 重新构造所需视图。
- AgentModule 不直接写 `run`、`session_token_snapshot`、`trace_event`，也不直接调用 provider SDK；这些分别由 RuntimeKernel、TraceBus/Repository、CapabilityHost/LLMInferenceGateway 负责。
- DeerFlow、LangGraph 或其他 harness 只能作为适配层或参考实现接入 `harness/`，不得替代 RuntimeKernel 的状态主权。

### 6.11 `ReplayService`

- 基于 `run`、`session_token_snapshot`、`trace_event`、`terminal_event`、`run_capability_binding` 构建回放视图。
- 向前端输出 timeline 与 trace detail。
- Replay timeline 只重组已持久化事实，不自行推断未落库状态。

### 6.12 `SkillTestService`

- 管理 skill 级黑盒时序测试场景、场景资源、场景运行、时间轴 driver 与语义期望评估。
- 测试执行必须创建真实 `skill_invocation / run / terminal_session`，并复用 Terminal Gateway、RuntimeKernel、Replay 与 OTel；测试层不模拟 Runtime，也不直接写 Session Token。
- 测试场景使用 `timeline.schema_version = psop-skill-test-timeline/v1` 描述相对时间轴：输入信道包含文本、图片、音频、视频，输出信道包含语义期望。
- 场景运行创建真实 invocation/run 后写入 `runtime_job.job_type = skill_test_timeline_driver`；driver 按 `event.at_ms` 到点调用 `RuntimeService.append_terminal_event(...)` 追加真实 terminal input。
- 输入事件即使发生在 Runtime 尚未进入 `waiting_input` 时，也先落库为终端事实；Runtime 后续在 `Sync` 阶段按 terminal cursor 消费。
- 输出判断按“时间点以前”执行：每条语义期望只把 `occurred_at <= time_origin + at_ms` 的真实 terminal output 提供给 Judge。
- Judge 必须通过 `LLM Inference Gateway`，默认 route key 为 `skill-test-judge`；评估结果保存状态、置信度、证据引用、理由、prompt hash 和 raw response。
- Review 支持基于 `time_ms + terminal_seq + snapshot_seq` 的切面 fork：可 fork 新测试场景，也可 fork 到独立调试会话继续手动输入。

### 6.13 `ObjectStoreService`

- 封装 S3-compatible / MinIO 上传能力，负责 bucket、object_key、media_type、size、checksum 与 metadata 的一致性。
- 小文本/JSON 仍可走 `payload_inline`；图片、音频、视频、PDF、大日志等测试数据必须走对象存储。

### 6.14 `JobSystem`

- 以数据库为唯一事实源，负责编译、运行、补偿、重试相关任务。
- 使用 claim + lease 机制控制 worker 并发。

### 6.15 `SandboxManager`

- 为高风险节点申请隔离环境。
- 通过 `sandbox_lease` 生命周期管理执行环境。

## 7. 主链路详细设计

### 7.1 运行前：Skill 构建、发布与编译

1. `WEB IDE` 创建或更新 `skill_definition`，并绑定对应的 GitLab 仓库、默认分支与系统管理的 draft manifest。
2. 草稿版本跟踪某个 GitLab 工作分支或引用；用户编辑的正式 skill source 保存在 GitLab 中，结构化机器契约保存在 draft `skill_version.manifest_snapshot` 中。
3. 用户对某个 draft version 或指定 git revision 发起 publish。
4. `SkillsModule` 先写入 `skill_publish_record`，初始状态为 `compiling`；随后解析并冻结 `source_commit_sha`，同时复制当前 draft `manifest_snapshot` 到 published version，将待发布版本固化为正式编译输入。若冻结源码阶段失败，该发布记录必须更新为 `failed`，用于前端与数据库回溯。
5. `SkillsModule` 创建 `skill_compile_request` 并投递 compile job；`POST /skills/{skill_id}/publish` 必须快速返回 `202 Accepted`，不在 API 请求内同步等待 LLM 编译完成。
6. `runtime_job.payload` 保存发布进度阶段，固定阶段为 `source_frozen -> compile_request_created -> source_loaded -> manifest_checked -> agent_compiling -> artifact_validating -> artifact_emitting -> publish_finalizing`，每个阶段状态为 `pending | running | succeeded | failed`。
7. 前端通过 `/api/compiler/requests/{compile_request_id}/events` 订阅 `text/event-stream`；SSE 只读取数据库快照并推送 `publish.progress / publish.terminal / publish.error`，断线后可通过 `/progress` 恢复。
8. Worker claim compile job 后，`SkillCompiler` 读取 GitLab 中该冻结 commit 对应的用户 source 与数据库中的 frozen manifest snapshot，经 SKILL 编译智能体生成 EG candidate，并执行 formal v5 validator。
9. 编译成功则写入 `eg_compile_artifact`，将 `skill_publish_record.publish_status` 更新为 `published`，并推进 `skill_definition.latest_published_version_id`；失败则写入 `compile_diagnostic`，将发布记录更新为 `failed`，且最新已发布版本不前进。

### 7.2 运行时：Invocation 与 Runtime Execution

1. `Gateway` 接收用户选择某个 skill 的调用请求，创建 `skill_invocation`；请求主体描述要运行哪个 skill、终端环境与能力，不要求携带 `user_input`。
2. 系统解析该 skill 当前生效的 `skill_version` 与 `compile_artifact`。
3. 创建 `run`、首个 `session_token_snapshot`、`terminal_session`。
4. 根据 artifact 的抽象能力声明与 invocation 的 `terminal_context / binding_preferences` 解析本次运行的 `run_capability_binding`；MVP 默认把文本输入输出绑定到 Web terminal。
5. 写入 `runtime_job`，由 worker claim。
6. `RuntimeKernel` 加载现实世界协作执行 artifact，并主动推进 introduction / first instruction，向 terminal 输出任务摘要、安全边界、准备事项或第一步指令。
7. `RuntimeKernel` 根据 formal v5 循环推进：
   - `Sync`
   - `Enabled`
   - `Sel`
   - `Actor`
   - `Merge`
   - `Trace`
8. 如果 actor 需要 terminal、MCP、LLM、设备或 sandbox 能力，则经 `CapabilityHost` 调用对应 gateway。
9. 中间 terminal 指令以 `terminal_event(direction=output)` 落库；若该指令需要用户完成现实动作，Runtime 写入 wait checkpoint 并将 run 置为 `waiting_input`。
10. 终端输入进入 `TerminalGateway` 后先追加为 `terminal_event`，再通过内部 bus / queue 唤醒 worker；bus 不是状态源。
11. `RuntimeKernel` 在下一轮 `Sync` 按 `run_id + seq_no cursor` 消费 terminal events，把文本、图片、视频、文件、传感器或设备反馈绑定到当前 wait checkpoint，并在 `Merge` 后更新正式 `Session Token`。
12. evaluation 节点根据证据判断进入下一步、重试、要求补充证据、终止或最终验证。
13. 每次推进都生成新的 snapshot 与 trace event，直到完成标准被验证后成功、失败条件成立、用户取消或再次进入等待输入状态。

terminal invocation 创建时即使没有任何已落库输入事件，Runtime 也应根据 artifact 主动推进到第一个任务介绍或步骤指令；只有 artifact 当前没有可主动执行节点时，run 才直接进入 `waiting_input`。后续用户文本、多模态测试数据或设备事件通过 `/api/terminal/sessions/{run_id}/events` 追加后，再唤醒 RuntimeKernel 继续执行。

终端真实运行链路固定为：

```text
POST /api/gateway/invocations with terminal_context
-> create skill_invocation / run / terminal_session / initial session_token_snapshot
-> resolve run bindings
-> RuntimeKernel loop
-> introduce task / safety boundary / first actionable instruction
-> append terminal output event
-> write wait checkpoint and run waiting_input
-> terminal input append terminal_event
-> bus wakeup worker
-> RuntimeKernel Sync consumes events as checkpoint evidence
-> Merge updates Session Token
-> evaluate evidence and choose next step / retry / abort / finish
-> trace_event + terminal_event + snapshot support Replay
```

### 7.3 运行后：Replay 与 Observability

1. `ReplayService` 读取 run 相关 snapshot、trace、terminal transcript 与 run binding。
2. 对外提供 replay timeline 和 trace detail。
3. `OpenTelemetry` 统一记录 compile、invocation、gateway、runtime、sandbox 相关 span。
4. 前端可从 `run_id`、`trace_id` 双入口查看运行结果与平台观测。

## 8. 数据库详细设计

### 8.1 设计原则

- 主存储固定为 `PostgreSQL`，大对象放入对象存储并通过 `artifact_object` 建立索引。
- `Skill source` 的正式副本固定存放于 `GitLab`；数据库仅保存仓库绑定、冻结 revision、索引快照与运行期引用关系。
- 所有主链路对象都必须包含时间审计字段。
- 所有可重试操作对象都必须有明确状态枚举与幂等键。
- `Session Token`、`Trace Event`、`Artifact Object` 三者共同构成回放和审计基础，不可互相替代。

### 8.2 核心表设计

#### 8.2.1 `skill_definition`

- 主键：`id UUID`
- 外键：`latest_draft_version_id -> skill_version.id`，`latest_published_version_id -> skill_version.id`
- 关键字段：`key`、`name`、`description`、`status`、`gitlab_project_id`、`repository_url`、`default_branch`、`manifest_path`
- 状态枚举：`active | archived`
- 唯一约束：`uk_skill_definition_key`
- 索引：`idx_skill_definition_status_updated_at`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：skill 总对象与 GitLab 仓库绑定，供发布、调用和检索使用；当前草稿机器契约保存在 draft `skill_version.manifest_snapshot`

#### 8.2.2 `skill_version`

- 主键：`id UUID`
- 外键：`skill_definition_id -> skill_definition.id`
- 关键字段：`version_no`、`source_ref`、`source_commit_sha`、`manifest_snapshot JSONB`、`runtime_policy_snapshot JSONB`、`status`
- 状态枚举：`draft | published | archived`
- 唯一约束：`uk_skill_version_definition_version_no`
- 索引：`idx_skill_version_definition_status`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：skill 的草稿或冻结版本；published version 必须绑定明确 `commit SHA` 与当时的 `manifest_snapshot`

#### 8.2.3 `skill_publish_record`

- 主键：`id UUID`
- 外键：`skill_definition_id`、`skill_version_id`、`compile_request_id`
- 关键字段：`publish_reason`、`publish_status`、`published_at`、`published_commit_sha`、`release_ref`
- 状态枚举：`requested | compiling | published | failed`
- 唯一约束：`uk_skill_publish_record_version_once`
- 索引：`idx_skill_publish_record_definition_published_at`
- 审计字段：`created_at`
- 主链路关联：记录一次 publish 行为、其冻结 revision 与 compile 关联

#### 8.2.4 `skill_compile_request`

- 主键：`id UUID`
- 外键：`skill_definition_id`、`skill_version_id`
- 关键字段：`trigger_type`、`source_commit_sha`、`status`、`dedupe_key`、`requested_at`、`started_at`、`finished_at`
- 状态枚举：`pending | running | succeeded | failed | cancelled`
- 唯一约束：`uk_skill_compile_request_dedupe_key`
- 索引：`idx_skill_compile_request_status_requested_at`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：指向冻结 skill version 与 GitLab revision 的编译任务调度对象

#### 8.2.5 `eg_compile_artifact`

- 主键：`id UUID`
- 外键：`skill_compile_request_id`、`skill_version_id`、`artifact_object_id`
- 关键字段：`formal_revision`、`artifact_version`、`graph_summary JSONB`、`capability_summary JSONB`、`status`
- 状态枚举：`ready | superseded | invalidated`
- 唯一约束：`uk_eg_compile_artifact_request`
- 索引：`idx_eg_compile_artifact_version_status`
- 审计字段：`created_at`
- 主链路关联：runtime 的正式执行输入
- v1 不为 prompt/domain 追溯新增独立列；`compiler_metadata` 写入 `artifact_object.content_json` 顶层，包含 agent id、prompt version、prompt hash、domain pack id 与 domain pack hash。

#### 8.2.6 `compile_diagnostic`

- 主键：`id UUID`
- 外键：`skill_compile_request_id`、`skill_version_id`
- 关键字段：`severity`、`code`、`message`、`location JSONB`、`category`
- 状态枚举：无独立状态，按 request 生命周期归属
- 唯一约束：无
- 索引：`idx_compile_diagnostic_request_severity`
- 审计字段：`created_at`
- 主链路关联：publish / compile 失败与告警定位
- `compile.agent.prompt_pack` 与 `compile.agent.domain_pack_fallback` 等诊断通过 `location JSONB` 保存 Agent Prompt Pack / Domain Pack 元数据，不新增数据库迁移。

#### 8.2.7 `skill_invocation`

- 主键：`id UUID`
- 外键：`skill_definition_id`、`skill_version_id`、`compile_artifact_id`
- 关键字段：`gateway_type`、`input_envelope JSONB`、`terminal_context JSONB`、`binding_preferences JSONB`、`status`、`idempotency_key`
- 状态枚举：`accepted | queued | running | succeeded | failed | cancelled`
- 唯一约束：`uk_skill_invocation_idempotency_key`
- 索引：`idx_skill_invocation_status_created_at`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：gateway 进入系统后的正式调用对象

#### 8.2.8 `run`

- 主键：`id UUID`
- 外键：`invocation_id`、`skill_definition_id`、`skill_version_id`、`compile_artifact_id`、`terminal_session_id`
- 关键字段：`status`、`runtime_phase`、`latest_snapshot_seq`、`latest_terminal_seq`、`latest_trace_seq`、`exit_reason`
- 状态枚举：`queued | running | waiting_input | succeeded | failed | cancelled`
- 唯一约束：`uk_run_invocation`
- 索引：`idx_run_status_updated_at`、`idx_run_skill_definition_created_at`
- 审计字段：`created_at`、`started_at`、`ended_at`、`updated_at`
- 主链路关联：一次真正的执行实体

#### 8.2.9 `session_token_snapshot`

- 主键：`id UUID`
- 外键：`run_id -> run.id`
- 关键字段：`seq_no`、`token_payload JSONB`、`enabled_set JSONB`、`selection_summary JSONB`、`snapshot_hash`
- 状态枚举：无独立状态
- 唯一约束：`uk_session_token_snapshot_run_seq`
- 索引：`idx_session_token_snapshot_run_seq`
- 审计字段：`created_at`
- 主链路关联：formal state 的正式快照

#### 8.2.10 `trace_event`

- 主键：`id UUID`
- 外键：`run_id -> run.id`
- 关键字段：`seq_no`、`phase`、`event_type`、`span_id`、`parent_span_id`、`payload JSONB`
- 状态枚举：无独立状态
- 唯一约束：`uk_trace_event_run_seq`
- 索引：`idx_trace_event_run_phase_seq`、`idx_trace_event_span_id`
- 审计字段：`occurred_at`
- 主链路关联：append-only 事件流，是 replay 与 observability 的桥梁

#### 8.2.11 `terminal_session`

- 主键：`id UUID`
- 外键：`run_id -> run.id`
- 关键字段：`mode`、`status`、`opened_at`、`closed_at`
- 状态枚举：`open | closed | error`
- 唯一约束：`uk_terminal_session_run`
- 索引：`idx_terminal_session_status_opened_at`
- 审计字段：`created_at`
- 主链路关联：run 的 I/O 会话容器

#### 8.2.12 `terminal_event`

- 主键：`id UUID`
- 外键：`terminal_session_id`、`run_id`、`trace_event_id`、`artifact_object_id`、`run_capability_binding_id`
- 关键字段：`direction`、`event_kind`、`mime_type`、`payload_inline`、`seq_no`、`external_event_id`、`source_ref`
- 状态枚举：无独立状态
- 唯一约束：`uk_terminal_event_session_seq`
- 索引：`idx_terminal_event_run_seq`、`idx_terminal_event_binding_seq`
- 审计字段：`created_at`、`occurred_at`
- 主链路关联：terminal transcript 与输入输出审计
- 约束：`seq_no` 由服务端分配；`external_event_id` 或 `Idempotency-Key` 用于输入幂等。
- 约束：小文本、小 JSON 使用 `payload_inline`；图片、音频、视频、大日志、大批量传感器数据使用 `artifact_object_id`。
- 约束：IoT、MQTT、OPC-UA、Modbus 等原始协议不直接进入 Runtime，必须先由 adapter 转为内部 terminal event DTO。

#### 8.2.13 `skill_test_scenario`

- 主键：`id UUID`
- 外键：`skill_definition_id -> skill_definition.id`、`target_compile_artifact_id -> eg_compile_artifact.id`
- 关键字段：`name`、`description`、`target_version_selector`、`duration_ms`、`timeline JSONB`、`judge_policy JSONB`、`fork_seed JSONB`、`status`
- 状态枚举：`active | archived`
- 索引：`idx_skill_test_scenario_skill_status_updated_at`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：skill 级黑盒时序测试场景定义；普通创建默认使用 latest published ready artifact，`target_compile_artifact_id` 仅作为高级/兼容指定入口。
- 约束：`timeline.schema_version` 为 `psop-skill-test-timeline/v1`，`duration_ms` 默认 30 分钟；输入事件按 `at_ms` 排序，输出期望事件固定使用 `lane_id = expected.semantic`。

#### 8.2.14 `skill_test_asset`

- 主键：`id UUID`
- 外键：`skill_definition_id -> skill_definition.id`、`scenario_id -> skill_test_scenario.id`、`artifact_object_id -> artifact_object.id`
- 关键字段：`name`、`description`、`lane_id`、`filename`、`mime_type`、`size_bytes`、`checksum`
- 状态枚举：无独立状态，删除场景时级联删除引用；对象内容仍由对象存储与 `artifact_object` 管理
- 索引：`idx_skill_test_asset_scenario_created_at`
- 审计字段：`created_at`
- 主链路关联：场景时间轴中图片、音频、视频等多模态输入事件的资源引用。

#### 8.2.15 `skill_test_scenario_run`

- 主键：`id UUID`
- 外键：`skill_definition_id -> skill_definition.id`、`scenario_id -> skill_test_scenario.id`、`invocation_id -> skill_invocation.id`、`run_id -> run.id`
- 关键字段：`status`、`driver_status`、`driver_cursor`、`driver_events JSONB`、`timeline JSONB`、`result_summary JSONB`、`time_origin`
- 状态枚举：`pending | queued | running | waiting_input | passed | failed | cancelled`
- 索引：`idx_skill_test_scenario_run_scenario_created_at`、`idx_skill_test_scenario_run_status_created_at`
- 审计字段：`created_at`、`updated_at`、`started_at`、`ended_at`
- 主链路关联：一次黑盒时序测试执行，关联真实 invocation/run/replay，并记录 driver 对时间轴输入的发送事实。
- 约束：同一 scenario 同时只允许一个 open run；必需输入尚未发送完而 Runtime 终结时，scenario run 判定为 failed。

#### 8.2.16 `skill_test_expectation_evaluation`

- 主键：`id UUID`
- 外键：`scenario_run_id -> skill_test_scenario_run.id`
- 关键字段：`expectation_id`、`status`、`confidence`、`reason`、`evidence_refs JSONB`、`judge_provider`、`judge_model`、`prompt_hash`、`raw_response JSONB`
- 状态枚举：`passed | failed | inconclusive`
- 索引：`idx_skill_test_expectation_eval_run_created_at`、`idx_skill_test_expectation_eval_run_expectation`
- 审计字段：`created_at`
- 主链路关联：语义期望的 LLM Judge 审计记录；`inconclusive` 默认按失败计入结果摘要。

#### 8.2.17 `mcp_server`

- 主键：`id UUID`
- 外键：无
- 关键字段：`name`、`endpoint`、`transport`、`status`、`discovery_snapshot JSONB`
- 状态枚举：`enabled | disabled | error`
- 唯一约束：`uk_mcp_server_name`
- 索引：`idx_mcp_server_status_updated_at`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：MCP tool discovery 与调用入口配置

#### 8.2.18 `mcp_tool`

- 主键：`id UUID`
- 外键：`mcp_server_id -> mcp_server.id`
- 关键字段：`tool_name`、`tool_schema JSONB`、`enabled`
- 状态枚举：通过 `enabled` 布尔控制
- 唯一约束：`uk_mcp_tool_server_tool_name`
- 索引：`idx_mcp_tool_server_enabled`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：CapabilityHost 的可调用工具目录

#### 8.2.19 `inference_provider`

- 主键：`id UUID`
- 外键：无
- 关键字段：`provider_key`、`endpoint`、`status`、`credential_ref`
- 状态枚举：`enabled | disabled | error`
- 唯一约束：`uk_inference_provider_key`
- 索引：`idx_inference_provider_status`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：模型调用 provider 抽象

#### 8.2.20 `model_catalog`

- 主键：`id UUID`
- 外键：`provider_id -> inference_provider.id`
- 关键字段：`model_key`、`model_family`、`supports_tools`、`supports_structured_output`、`status`
- 状态枚举：`active | deprecated | disabled`
- 唯一约束：`uk_model_catalog_provider_model_key`
- 索引：`idx_model_catalog_provider_status`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：Inference Gateway 路由目标清单

#### 8.2.21 `gateway_policy`

- 主键：`id UUID`
- 外键：无
- 关键字段：`policy_type`、`target_ref`、`rules JSONB`、`status`
- 状态枚举：`active | disabled`
- 唯一约束：`uk_gateway_policy_type_target_ref`
- 索引：`idx_gateway_policy_type_status`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：tool、model、budget、timeout 等统一策略

#### 8.2.22 `capability_binding`

- 主键：`id UUID`
- 外键：`compile_artifact_id`、`gateway_policy_id`
- 关键字段：`binding_key`、`binding_type`、`target_ref`、`node_selector JSONB`
- 状态枚举：通过 `binding_type` 与 `enabled` 控制
- 唯一约束：`uk_capability_binding_artifact_binding_key`
- 索引：`idx_capability_binding_artifact_type`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：artifact 级静态能力声明或编译期绑定，不代表某次 run 的具体设备或终端实例

#### 8.2.23 `run_capability_binding`

- 主键：`id UUID`
- 外键：`run_id`、`compile_artifact_id`、`capability_binding_id`、`gateway_policy_id`
- 关键字段：`requirement_key`、`binding_type`、`capability`、`target_kind`、`target_ref`、`channel`、`schema_ref`、`manifest_hash`、`policy_snapshot JSONB`
- 状态枚举：`pending | active | rejected | disabled`
- 唯一约束：`uk_run_capability_binding_run_requirement`
- 索引：`idx_run_capability_binding_run_status`、`idx_run_capability_binding_target`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：run 级具体能力解析结果，用于把本次运行的抽象能力绑定到具体 Web terminal、device、channel、schema 与 policy
- 约束：MVP 默认把 `terminal.text.input.v1` 与 `terminal.text.output.v1` 绑定到 Web terminal；IoT / 真实设备绑定先作为 post-MVP reserved design。

#### 8.2.24 `runtime_job`

- 主键：`id UUID`
- 外键：`run_id`、`compile_request_id`
- 关键字段：`job_type`、`status`、`payload JSONB`、`lease_until`、`dedupe_key`、`attempt_no`
- 状态枚举：`pending | claimed | running | succeeded | failed | cancelled | deadletter`
- compile job 的 `payload.progress_stages` 是发布阶段进度事实源，供 `/progress` 与 SSE 读取；不新增独立进度表。
- `skill_test_timeline_driver` job 的 `payload = { scenario_run_id }`，`dedupe_key = job:skill-test-timeline-driver:{scenario_run_id}`。
- 唯一约束：`uk_runtime_job_dedupe_key`
- 索引：`idx_runtime_job_status_available_at`、`idx_runtime_job_lease_until`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：数据库驱动的异步任务系统

#### 8.2.25 `worker_heartbeat`

- 主键：`id UUID`
- 外键：无
- 关键字段：`worker_name`、`worker_type`、`capabilities JSONB`、`last_seen_at`、`status`
- 状态枚举：`alive | stale | drained`
- 唯一约束：`uk_worker_heartbeat_worker_name`
- 索引：`idx_worker_heartbeat_status_last_seen_at`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：worker 健康检查与调度可见性

#### 8.2.26 `sandbox_lease`

- 主键：`id UUID`
- 外键：`run_id`、`runtime_job_id`
- 关键字段：`sandbox_key`、`lease_status`、`lease_until`、`driver_type`、`connection_info JSONB`
- 状态枚举：`requested | active | released | expired | error`
- 唯一约束：`uk_sandbox_lease_sandbox_key`
- 索引：`idx_sandbox_lease_status_lease_until`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：高风险 actor 的隔离执行租约

#### 8.2.27 `artifact_object`

- 主键：`id UUID`
- 外键：无
- 关键字段：`bucket`、`object_key`、`media_type`、`size_bytes`、`checksum`
- 状态枚举：无独立状态
- 唯一约束：`uk_artifact_object_bucket_object_key`
- 索引：`idx_artifact_object_media_type_created_at`
- 审计字段：`created_at`
- 主链路关联：artifact 文件、terminal 二进制内容、大对象证据

#### 8.2.28 `runtime_config`

- 主键：`id UUID`
- 外键：无
- 关键字段：`config_key`、`config_scope`、`config_value JSONB`、`status`
- 状态枚举：`active | disabled`
- 唯一约束：`uk_runtime_config_scope_key`
- 索引：`idx_runtime_config_scope_status`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：运行时与 gateway 的可调配置

#### 8.2.29 `operation_log`

- 主键：`id UUID`
- 外键：`run_id`、`invocation_id`、`compile_request_id`
- 关键字段：`operation_type`、`level`、`message`、`details JSONB`
- 状态枚举：无独立状态
- 唯一约束：无
- 索引：`idx_operation_log_level_created_at`、`idx_operation_log_run_id`
- 审计字段：`created_at`
- 主链路关联：平台运维与系统行为审计

## 9. 接口详细设计

### 9.1 协议约定

- 所有 REST 接口使用 `JSON UTF-8`。
- 所有 ID 使用 `UUID` 字符串。
- 时间字段统一使用 `ISO-8601 UTC`。
- 写接口支持 `Idempotency-Key` 头。
- 错误模型统一为：

```json
{
  "code": "string",
  "message": "string",
  "details": {},
  "request_id": "uuid",
  "trace_id": "string"
}
```

### 9.2 接口契约原则

- 系统设计文档只定义资源边界、动作语义、状态迁移与幂等要求，不固化具体 DTO 类名。
- 具体请求响应 schema 由实现阶段的 `OpenAPI + Pydantic` 产出，并以代码作为最终事实源。
- 列表接口统一区分 `summary` 与 `detail` 视图；详情接口返回单资源完整视图；异步动作接口返回动作结果与关联资源标识。
- 与 skill 相关的契约必须显式暴露 `skill_id / skill_version_id / source_commit_sha / compile_request_id / run_id / trace_id` 等关联键，以支撑审计与回放。

### 9.3 `/api/system/*`

| Method | Path | 用途 | Response |
| --- | --- | --- | --- |
| `GET` | `/api/system/health` | 系统健康检查 | `{ status, db, object_store, otel, worker_count }` |
| `GET` | `/api/system/summary` | 首页聚合摘要 | `overview payload` |
| `GET` | `/api/system/config` | 前端所需系统配置 | `runtime/public config` |

### 9.4 `/api/skills/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/skills` | 列表与筛选 | `query: key,status,page` | `skill summary list` |
| `POST` | `/api/skills` | 创建 skill、GitLab 绑定与 manifest draft | `{ key, name, description, gitlab_project_id?, default_branch?, manifest_config? }` | `skill detail` |
| `GET` | `/api/skills/{skill_id}` | skill 详情 | 无 | `skill detail + versions summary` |
| `PATCH` | `/api/skills/{skill_id}` | 更新 skill 元数据、仓库绑定与 manifest draft 镜像字段 | `{ name, description, status, default_branch?, manifest_config? }` | `skill detail` |
| `POST` | `/api/skills/{skill_id}/versions` | 创建草稿版本 | `{ base_version_id?, source_ref? }` | `skill version detail` |
| `GET` | `/api/skills/{skill_id}/versions/{skill_version_id}` | 版本详情 | 无 | `skill version detail` |
| `PATCH` | `/api/skills/{skill_id}/versions/{skill_version_id}` | 更新 draft 版本元数据、跟踪引用或结构化 manifest draft | `{ source_ref?, interface_contract?, capabilities?, compile_config?, runtime_policy? }` | `skill version detail` |
| `POST` | `/api/skills/{skill_id}/publish` | 发布 skill 并触发自动编译 | `{ skill_version_id, publish_reason, expected_commit_sha? }` | `{ publish_record, compile_request }` |
| `GET` | `/api/skills/{skill_id}/publishes` | 发布记录列表 | `query: status,page` | `publish record list` |

状态要求：

- `PATCH skill version` 只允许 `draft`。
- `publish` 必须冻结到明确的 `source_commit_sha`。
- `publish` 必须同时冻结 `manifest_snapshot`，编译以该 snapshot 为机器契约事实源。
- `domain_pack` 不新增 REST 入口；如需指定行业包，通过 draft `manifest_snapshot.skill.compile_config.domain_pack` 或对应 manifest 配置字段进入发布冻结视图。
- 对已发布版本的变更必须通过创建新 draft 完成。

### 9.5 `/api/compiler/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/compiler/requests` | compile request 列表 | `query: skill_id,status,page` | `compile request summary list` |
| `GET` | `/api/compiler/requests/{compile_request_id}` | request 详情 | 无 | `compile request detail` |
| `POST` | `/api/compiler/requests/{compile_request_id}/retry` | 重试编译 | 无 | `compile request detail` |
| `GET` | `/api/compiler/requests/{compile_request_id}/progress` | 发布/编译阶段进度快照 | 无 | `publish progress snapshot` |
| `GET` | `/api/compiler/requests/{compile_request_id}/events` | 发布/编译阶段 SSE 事件流 | 无 | `text/event-stream` |
| `GET` | `/api/compiler/requests/{compile_request_id}/diagnostics` | 诊断列表 | 无 | `compile diagnostic list` |
| `GET` | `/api/compiler/artifacts/{compile_artifact_id}` | artifact 详情 | 无 | `compile artifact detail` |

状态要求：

- `/api/compiler/*` 只处理编译对象，不承载 skill 发布动作。
- 每个 compile request 都必须绑定冻结的 `skill_version` 与 `source_commit_sha`。
- compiler response DTO 不新增单独 prompt 字段；prompt/domain 追溯通过 artifact payload 的 `compiler_metadata` 与 diagnostics 的 `location` 暴露。
- `retry` 必须保留历史 request，不覆盖原记录。

### 9.6 `/api/gateway/invocations/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `POST` | `/api/gateway/invocations` | 用户选择 skill 并建立真实终端协作连接 | `{ skill_key, version_selector, gateway_type, terminal_context, binding_preferences?, input_envelope? }` | `{ invocation_id, run_id, terminal_session_id, status }` |
| `GET` | `/api/gateway/invocations` | invocation 列表 | `query: skill_key,status,page` | `invocation summary list` |
| `GET` | `/api/gateway/invocations/{invocation_id}` | invocation 详情 | 无 | `invocation detail + run summary` |

状态要求：

- `POST` 必须按 `skill_key + version_selector` 解析到具体 `skill_version` 与 `compile_artifact`。
- 同一个 `Idempotency-Key` 不得生成重复 invocation。
- `POST` 只负责创建 invocation、run、terminal session、初始 snapshot 与必要的 runtime job，不直接把用户输入写入正式 Runtime 状态。
- `POST` 的正常语义是“建立连接和能力绑定”；Runtime 后续可主动输出任务摘要、安全边界、准备事项和第一步指令。
- `terminal_context` 用于描述本次入口是 `web | device | simulator` 以及可选 `device_id`；`binding_preferences` 只表达偏好，最终绑定以 `run_capability_binding` 为准。
- 新 terminal 调用方应把用户文本、图片、视频、文件、传感器读数或设备反馈作为后续 `terminal_event` 注入；`input_envelope.user_input` 仅作为旧 Web MVP 的兼容入口，不是新运行链路的正常入口。

`POST /api/gateway/invocations` 请求示例：

```json
{
  "skill_key": "equipment.inspect",
  "version_selector": "latest",
  "gateway_type": "terminal",
  "terminal_context": {
    "terminal_kind": "web",
    "device_id": null,
    "supported_inputs": [
      "terminal.text.input.v1",
      "terminal.file.input.v1",
      "terminal.image.input.v1"
    ],
    "supported_outputs": [
      "terminal.text.output.v1",
      "terminal.markdown.output.v1"
    ]
  },
  "binding_preferences": []
}
```

响应必须包含：

```json
{
  "invocation_id": "uuid",
  "run_id": "uuid",
  "terminal_session_id": "uuid",
  "status": "accepted"
}
```

### 9.7 `/api/runs/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/runs` | run 列表 | `query: status,skill_id,page` | `run summary list` |
| `GET` | `/api/runs/{run_id}` | run 详情 | 无 | `run detail` |
| `POST` | `/api/runs/{run_id}/cancel` | 取消 run | `{ reason }` | `run detail` |
| `GET` | `/api/runs/{run_id}/snapshots` | snapshot 列表 | `query: from_seq,to_seq` | `session token snapshot list` |
| `GET` | `/api/runs/{run_id}/trace-events` | trace 列表 | `query: from_seq,to_seq,type` | `trace event list` |
| `GET` | `/api/runs/{run_id}/binding-requirements` | 查看 artifact 对运行环境的抽象能力需求 | 无 | `binding requirement list` |
| `GET` | `/api/runs/{run_id}/bindings` | 查看本次 run 的能力绑定 | 无 | `run capability binding list` |
| `POST` | `/api/runs/{run_id}/bindings/resolve` | 解析或补充本次 run 的能力绑定 | `{ bindings }` | `run capability binding list` |
| `GET` | `/api/runs/{run_id}/bindings/{binding_id}` | 查看单个 run binding | 无 | `run capability binding detail` |

状态要求：

- `GET /api/runs/{run_id}` 必须暴露 `status`、`runtime_phase`、`terminal_session_id`、`latest_snapshot_seq`、`latest_terminal_seq`、`latest_trace_seq`、`binding_summary`。
- `GET /api/runs/{run_id}` 应暴露当前等待上下文：`current_step`、`wait_reason`、`expected_inputs`、`checkpoint_id`、`resume_phase`、`latest_terminal_seq` 和最近 evaluation decision。
- `bindings/resolve` 只写 `run_capability_binding` 与 trace/audit 事实，不直接推进 `Session Token`。

### 9.8 `/api/terminal/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/terminal/sessions/{run_id}` | 获取 run 的 terminal session | 无 | `{ terminal_session, transcript_summary }` |
| `POST` | `/api/terminal/sessions/{run_id}/events` | 注入 terminal input 或记录 output | `{ direction, event_kind, mime_type, payload_inline, artifact_object_id?, binding_id?, source?, external_event_id?, occurred_at? }` | `{ accepted, event_id, seq_no }` |
| `GET` | `/api/terminal/sessions/{run_id}/events` | transcript 列表 | `query: from_seq,to_seq` | `terminal events` |

状态要求：

- `POST events` 必须先校验 terminal session、run binding、event kind、mime type、schema、幂等键与 policy，再追加 append-only `terminal_event`。
- `binding_id` 指向 `run_capability_binding.id`，缺省时只允许使用该 run 的默认 Web terminal binding。
- `direction=input` 的事件只在 RuntimeKernel 后续 `Sync -> Merge` 后影响正式状态；`direction=output` 的事件用于 terminal transcript、前端展示与设备下发审计。
- `direction=output` 可表示任务介绍、步骤指令、补充证据请求、恢复建议、失败说明或最终结果；只有最终完成标准验证后的 output 才可被视为 final output。
- `direction=input` 可表示用户确认、文本描述、图片、视频、文件、设备 ACK、传感器读数等现场证据，并应由 RuntimeKernel 绑定到当前 wait checkpoint。
- WebSocket 可承载低延迟输入命令，但服务端仍必须先落库为 `terminal_event`，再广播 `terminal.event.appended`。

`POST /api/terminal/sessions/{run_id}/events` 请求示例：

```json
{
  "direction": "input",
  "event_kind": "terminal.text.input.v1",
  "mime_type": "text/plain",
  "payload_inline": "继续执行",
  "artifact_object_id": null,
  "binding_id": "uuid",
  "source": {
    "kind": "web",
    "device_id": null,
    "connection_id": "uuid"
  },
  "external_event_id": "client-msg-001",
  "occurred_at": "2026-05-06T10:00:00Z"
}
```

### 9.9 `/api/skills/{skill_id}/test-scenarios/*`

该组接口用于 `Skill Detail -> 测试`。测试层管理黑盒时序测试场景，不替代 Runtime；每次 scenario run 都必须创建真实 invocation/run。

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/skills/{skill_id}/test-scenarios` | 测试场景列表 | 无 | `skill test scenario list` |
| `POST` | `/api/skills/{skill_id}/test-scenarios` | 创建测试场景 | `{ name, description?, duration_ms?, timeline?, judge_policy?, fork_seed? }` | `skill test scenario detail` |
| `GET` | `/api/skills/{skill_id}/test-scenarios/{scenario_id}` | 测试场景详情 | 无 | `skill test scenario detail` |
| `PATCH` | `/api/skills/{skill_id}/test-scenarios/{scenario_id}` | 更新测试场景 | partial scenario payload | `skill test scenario detail` |
| `DELETE` | `/api/skills/{skill_id}/test-scenarios/{scenario_id}` | 归档测试场景 | 无 | `skill test scenario detail` |

状态要求：

- 普通客户端默认不暴露版本/artifact 选择，服务端以 `target_version_selector = latest` 启动真实 run；`target_compile_artifact_id` 仅作为高级/兼容字段保留。
- `DELETE` 采用归档语义，将 `status` 设置为 `archived`，不物理删除历史运行。
- `timeline` 最小结构：

```json
{
  "schema_version": "psop-skill-test-timeline/v1",
  "duration_ms": 1800000,
  "lanes": [
    { "id": "input.text", "kind": "input", "label": "文本", "event_kind": "terminal.text.input.v1" },
    { "id": "input.image", "kind": "input", "label": "图片", "event_kind": "terminal.image.input.v1" },
    { "id": "input.audio", "kind": "input", "label": "音频", "event_kind": "terminal.audio.input.v1" },
    { "id": "input.video", "kind": "input", "label": "视频", "event_kind": "terminal.video.input.v1" },
    { "id": "expected.semantic", "kind": "output", "label": "语义输出" }
  ],
  "events": [
    {
      "id": "initial_fault_context",
      "lane_id": "input.text",
      "at_ms": 0,
      "event_kind": "terminal.text.input.v1",
      "mime_type": "text/plain",
      "payload_inline": "现场描述文本",
      "required": true
    },
    {
      "id": "expect_completion",
      "lane_id": "expected.semantic",
      "at_ms": 60000,
      "expectation": "该时间点以前应输出可执行的下一步指导。",
      "required": true
    }
  ]
}
```

### 9.10 `/api/skills/{skill_id}/test-scenarios/{scenario_id}/assets`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `POST` | `/api/skills/{skill_id}/test-scenarios/{scenario_id}/assets` | 上传场景资源 | `multipart/form-data: file,name?,description?,lane_id?` | `skill test asset` |
| `GET` | `/api/skills/{skill_id}/test-scenarios/{scenario_id}/assets` | 场景资源列表 | 无 | `skill test asset list` |
| `DELETE` | `/api/skills/{skill_id}/test-scenarios/{scenario_id}/assets/{asset_id}` | 删除场景资源引用 | 无 | `{ deleted, asset_id }` |

状态要求：

- 上传内容进入对象存储，并创建 `artifact_object`；`skill_test_asset` 只保存场景级引用与文件元数据。
- 图片、音频、视频输入事件通过 `asset_id` 引用场景资源；driver 发送时转换为 `terminal_event.artifact_object_id`。

### 9.11 `/api/skill-test-scenario-runs/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `POST` | `/api/skills/{skill_id}/test-scenarios/{scenario_id}/runs` | 启动一次场景运行 | `{ timeline_override?, terminal_context_override? }` | `skill test scenario run detail` |
| `GET` | `/api/skills/{skill_id}/test-scenarios/{scenario_id}/runs` | 场景运行历史 | 无 | `skill test scenario run list` |
| `GET` | `/api/skill-test-scenario-runs/{scenario_run_id}` | 场景运行详情 | 无 | `skill test scenario run detail` |
| `GET` | `/api/skill-test-scenario-runs/{scenario_run_id}/review` | 场景运行 Review | 无 | `scenario + scenario_run + replay + driver_events + expectation_evaluations + cursor_anchors` |
| `POST` | `/api/skill-test-scenario-runs/{scenario_run_id}/evaluate` | 重新评估语义期望 | 无 | `skill test scenario run detail` |
| `POST` | `/api/skill-test-scenario-runs/{scenario_run_id}/fork-scenario` | 从 Review 切面 fork 新测试场景 | `{ cursor: { time_ms, terminal_seq, snapshot_seq }, name?, description? }` | `skill test scenario detail` |
| `POST` | `/api/skill-test-scenario-runs/{scenario_run_id}/fork-debug` | 从 Review 切面 fork 调试会话 | `{ cursor: { time_ms, terminal_seq, snapshot_seq } }` | `skill invocation detail` |

执行要求：

- 启动场景运行时创建真实 `skill_invocation / run / terminal_session / run_capability_binding`，`terminal_context.operator_mode = test`，`test_context.kind = skill_blackbox_timeline_test`。
- 服务端写入 `skill_test_timeline_driver` job；driver 以 scenario run 的 `time_origin` 为原点，按输入事件的 `at_ms` 到点追加真实 terminal input。
- driver 追加输入时使用幂等 `external_event_id = skill-test-scenario-run:{scenario_run_id}:timeline:{event_id}`，并记录 `scheduled_at / actual_sent_at / drift_ms / terminal_event_id / terminal_seq`。
- 输出期望评估只向 Judge 提供时间切面以前的真实 terminal output；Judge 失败、JSON 不合法或证据不足均落为 `inconclusive`，默认按失败计入。
- `fork-scenario` 保存 `fork_seed` 并把切面以后的 timeline 事件平移到新场景；`fork-debug` 使用指定 snapshot 与 terminal prefix 创建真实 debug invocation。

### 9.12 `/api/terminal/devices/*` post-MVP reserved

该组接口用于未来 IoT / 真实设备接入，不纳入 issue #1 最小完成定义。设备注册只说明“设备能做什么”，真正进入某次 run 必须通过 `run_capability_binding`。

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `POST` | `/api/terminal/devices/register` | 注册终端设备及能力 manifest | `{ device_key, device_kind, display_name, transports, capability_manifest }` | `terminal device detail` |
| `GET` | `/api/terminal/devices` | 设备列表 | `query: status,kind` | `terminal device list` |
| `GET` | `/api/terminal/devices/{device_id}` | 设备详情 | 无 | `terminal device detail` |
| `PATCH` | `/api/terminal/devices/{device_id}/manifest` | 更新设备 capability manifest | `{ capability_manifest }` | `terminal device detail` |
| `POST` | `/api/terminal/devices/{device_id}/heartbeat` | 设备心跳 | `{ status, connection_info? }` | `{ accepted }` |
| `POST` | `/api/terminal/devices/{device_id}/events` | 设备事件入口，由 adapter 归一为 terminal event | `{ run_id, terminal_session_id, binding_id, event_kind, mime_type, payload_inline?, artifact_object_id?, external_event_id?, occurred_at? }` | `{ accepted, event_id, seq_no }` |

`capability_manifest` 至少表达 `inputs`、`outputs`、`event_kind`、`mime_type`、`schema_ref`、`streaming`、`ack_required`、`requires_approval`。MQTT、OPC-UA、Modbus 等协议由 adapter 转换为内部 DTO 后再进入 `TerminalGateway`。

### 9.13 `/api/replay/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/replay/runs` | replay 检索 | `query: skill_id,status,time range` | `run summary list` |
| `GET` | `/api/replay/runs/{run_id}` | 完整回放 | 无 | `replay timeline detail` |
| `GET` | `/api/replay/traces/{trace_id}` | trace 详情 | 无 | `trace detail payload` |

### 9.14 `/api/gateway/mcp/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/gateway/mcp/servers` | MCP server 列表 | 无 | `mcp server list` |
| `POST` | `/api/gateway/mcp/servers` | 注册或更新 server | `{ name, endpoint, transport }` | `mcp server detail` |
| `POST` | `/api/gateway/mcp/servers/{server_id}/discover` | 触发 tool discovery | 无 | `{ tools_count }` |
| `GET` | `/api/gateway/mcp/servers/{server_id}/tools` | tool 列表 | 无 | `mcp tool list` |

### 9.15 `/api/gateway/inference/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/gateway/inference/providers` | provider 列表 | 无 | `inference provider list` |
| `POST` | `/api/gateway/inference/providers` | 注册或更新 provider | `{ provider_key, endpoint, credential_ref }` | `inference provider detail` |
| `GET` | `/api/gateway/inference/models` | model catalog | `query: provider_id` | `model list` |
| `POST` | `/api/gateway/inference/routes` | 更新模型路由 | `{ provider_id, model_id, route_key, status }` | `model route detail` |

### 9.16 `/api/runtime/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/runtime/jobs` | 查看 job 状态 | `query: status,type` | `runtime job list` |
| `GET` | `/api/runtime/workers` | 查看 worker 心跳 | 无 | `worker heartbeat list` |
| `GET` | `/api/runtime/sandboxes` | 查看 sandbox lease | 无 | `sandbox lease list` |

## 10. WebSocket 与 MCP 入口设计

### 10.1 WebSocket

- `GET /ws/invocations/{invocation_id}`
  - 事件类型：`invocation.accepted`、`invocation.bound`、`invocation.completed`
- `GET /ws/runs/{run_id}`
  - 事件类型：
    - `run.started`
    - `run.phase.changed`
    - `binding.resolved`
    - `binding.updated`
    - `session_token.snapshot.appended`
    - `trace.event.appended`
    - `terminal.event.appended`
    - `run.completed`
    - `run.failed`
    - `run.cancelled`

统一事件包结构：

```json
{
  "event_type": "trace.event.appended",
  "run_id": "uuid",
  "invocation_id": "uuid",
  "seq_no": 12,
  "occurred_at": "2026-04-22T10:00:00Z",
  "payload": {}
}
```

WS 策略：

- `GET /ws/runs/{run_id}` 是实时传输通道，不是状态源。
- WS 可承载 `terminal.input.append` 等低延迟输入命令，但服务端处理后必须先写入 `terminal_event`，再广播 `terminal.event.appended`。
- 前端断线重连后必须按 REST 拉取缺失的 terminal / trace / snapshot seq，并在 store 层排序去重。
- Replay timeline 由 `session_token_snapshot`、`trace_event`、`terminal_event`、`run_capability_binding` 共同重组，不自行推断未持久化状态。

### 10.2 `/mcp`

- 暴露 PSOP 作为一个受控的 MCP server。
- 对外只开放受控操作，不直接暴露内部数据库对象。
- 首批工具建议：
  - `psop.skill.list_published`
  - `psop.skill.invoke`
  - `psop.run.get`
  - `psop.replay.get_timeline`
- 所有经 `/mcp` 进入的调用仍要落到 `skill_invocation` 与 `run` 对象上。

## 11. 可观测与审计设计

### 11.1 OTel 关联键

以下字段必须贯穿日志、trace、metrics 与 replay：

- `skill_id`
- `skill_version_id`
- `source_commit_sha`
- `compile_request_id`
- `compile_artifact_id`
- `invocation_id`
- `run_id`
- `run_capability_binding_id`
- `trace_id`
- `span_id`
- `tool_call_id`
- `agent_id`
- `agent_prompt_hash`
- `domain_pack_id`
- `domain_pack_hash`

### 11.2 Span 设计

- `compile.request`
- `compile.parse`
- `compile.validate`
- `compile.emit`
- `invocation.accept`
- `runtime.loop`
- `runtime.actor`
- `gateway.terminal`
- `gateway.mcp`
- `gateway.inference`
- `sandbox.execute`

### 11.3 Metrics 设计

- `compile_requests_total`
- `compile_failures_total`
- `run_started_total`
- `run_completed_total`
- `run_duration_seconds`
- `gateway_call_duration_seconds`
- `llm_inference_duration_seconds`
- `llm_inference_input_tokens_total`
- `llm_inference_output_tokens_total`
- `llm_inference_total_tokens_total`
- `terminal_wait_duration_seconds`

### 11.4 审计原则

- 所有关键状态变更都必须可通过 `operation_log + trace_event + snapshot` 追溯。
- replay 服务不能自行推断不存在的状态；只允许重组已持久化事实。
- gateway 与模型调用必须记录请求摘要、超时、错误与结果摘要。
- 大模型调用必须记录完整输入、完整输出与 token 消耗；运行时 LLM 节点的 `trace_event.payload.observation` 至少包含 `input.system_prompt`、`input.user_prompt`、`output.content`、`usage.input_tokens`、`usage.output_tokens`、`usage.total_tokens` 与 provider 原始 usage 摘要。

## 12. 开发切片、实现顺序与完成定义

### 12.1 开发切片

1. `Slice A`：数据库骨架、基础 FastAPI、接口契约骨架、健康检查、对象存储、GitLab 接线与 OTel 接线
2. `Slice B`：`SkillsModule + GitLab binding + Publish flow + SkillCompiler`
3. `Slice C`：`Invocation + Run + Run Binding + RuntimeKernel` 主循环
4. `Slice D`：`TerminalGateway + terminal_event + MCPGateway + LLMInferenceGateway + CapabilityHost + AgentModule`
5. `Slice E`：`ReplayService + WebSocket + runtime admin endpoints`

### 12.2 实现顺序

1. 先落核心数据模型和迁移，确保服务端对象稳定。
2. 再落 `skills -> publish -> compile -> artifact`，让运行前链路可闭环。
3. 随后落 `invocation -> run -> run binding -> snapshot -> trace`，让运行时链路可闭环。
4. 再接入 terminal event、MCP、LLM gateway 与 `AgentModule`，并按需复用 DeerFlow 的参考实现。
5. 最后补 replay、WS、OTel 与 runtime 运维接口。

### 12.3 完成定义

- 技能发布后能够冻结 GitLab revision、自动生成 compile request，并得到成功或失败结果。
- 成功发布的 skill 能被 gateway 调用，并创建 run。
- `RuntimeKernel` 能围绕 formal v5 约束推进至少一条完整 run。
- 运行中能写出 snapshot、trace、terminal transcript、run binding，并通过 API / WS 对外提供。
- 运行完成后能通过 replay 和 OTel 进行排障。
- 读完本文档后，后端、运行时与基础设施团队无需再补关键架构决策即可开工。
