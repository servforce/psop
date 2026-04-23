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

## 6. 模块拆分与职责

### 6.1 `SkillsModule`

- 负责 `skill_definition`、`skill_version`、`skill_publish_record` 以及 GitLab 仓库绑定管理。
- 负责草稿版本、已发布版本、版本冻结、发布审计与对 `Web IDE` 的读写模型。
- 负责在发布完成后创建 `skill_compile_request`，但不直接承担编译执行。

### 6.2 `Skill` 形式定义

- 一个正式 `skill` 至少由以下内容组成：
  - `identity`：`key`、`name`、`description`
  - `repository binding`：`gitlab_project_id`、`default_branch`、`manifest_path`
  - `source revision`：用于草稿或发布的 `branch / tag / commit SHA`
  - `interface contract`：入口、输入输出语义、外部可见调用方式
  - `capability declarations`：所需 `terminal`、`MCP tool`、`LLM model`、`sandbox` 等能力声明
  - `compile config`：formal revision、编译目标、校验规则
  - `runtime policy`：超时、重试、预算、并发、隔离等级
  - `publish metadata`：版本号、发布说明、发布时间、来源 revision
- `Skill` 是用户在 `Web IDE` 中定义与维护的正式对象，不是 `EG source`。
- `Compiler` 必须基于这一形式定义生成符合 formal v5 的 `EG Compile Artifact`。

### 6.3 `GitLabSkillRepository`

- `GitLab` 是 `skill source` 的正式事实源；PSOP 数据库只保存元数据、冻结引用、索引快照与运行时关联关系。
- `skill_definition` 绑定到 `GitLab project` 与 `manifest_path`；`skill_version` 绑定到具体 `git ref`。
- 发布动作必须冻结到明确的 `commit SHA`；编译只读取该 SHA 对应内容，确保可回放、可审计、可重放编译。
- 数据库可以缓存 `manifest_snapshot`、诊断摘要和检索索引，但这些缓存不替代 GitLab 事实源。

### 6.4 `SkillCompiler`

- 负责从 GitLab 中某个冻结 `skill version` 到 `EG Compile Artifact` 的转换。
- 输出 `eg_compile_artifact`、`compile_diagnostic` 和静态分析摘要。
- 编译成功的前提是 artifact 能映射到 formal v5 定义。
- 不负责 skill 发布、仓库绑定或版本冻结；`/api/compiler/*` 只承载编译相关接口。

### 6.5 `RuntimeKernel`

- 加载 compile artifact。
- 驱动 `Sync -> Enabled -> Sel -> Actor -> Merge -> Trace` 循环。
- 写入 `session_token_snapshot` 与 `trace_event`。
- 管理 run 生命周期与 terminal waiting 状态。

### 6.6 `CapabilityHost`

- 把运行时节点对能力的需求映射到 gateway 能力。
- 负责 capability binding、policy 检查、超时和预算控制。

### 6.7 `TerminalGateway`

- 接收来自 Web IDE 的文本、图像、语音、视频等模拟输入输出。
- 把外部输入封装成 terminal event，不直接改写运行时状态。

### 6.8 `MCPGateway`

- 管理 MCP server 接入、tool discovery、tool 调用与返回归一化。
- 只暴露受控 tool，不承担状态主权。

### 6.9 `LLMInferenceGateway`

- 提供模型 provider、模型路由、结构化输出、fallback、配额与调用审计。
- 统一所有模型调用，避免业务节点直连 provider。

### 6.10 `AgentModule`

- `AgentModule` 是 PSOP 中与智能体相关的正式模块边界，负责 agent runtime、sub-agent、memory、planning、tool-use orchestration 与 harness 抽象。
- 可借鉴或复用 DeerFlow 的优秀设计与实现，但 `DeerFlow` 不是产品事实源，也不是需要原样照搬的正式模块。
- `AgentModule` 只为 `Actor` 执行提供智能体能力，不拥有正式状态主权。

### 6.11 `ReplayService`

- 基于 `run`、`session_token_snapshot`、`trace_event`、`terminal_event` 构建回放视图。
- 向前端输出 timeline 与 trace detail。

### 6.12 `JobSystem`

- 以数据库为唯一事实源，负责编译、运行、补偿、重试相关任务。
- 使用 claim + lease 机制控制 worker 并发。

### 6.13 `SandboxManager`

- 为高风险节点申请隔离环境。
- 通过 `sandbox_lease` 生命周期管理执行环境。

## 7. 主链路详细设计

### 7.1 运行前：Skill 构建、发布与编译

1. `WEB IDE` 创建或更新 `skill_definition`，并绑定对应的 GitLab 仓库、默认分支与 manifest 路径。
2. 草稿版本跟踪某个 GitLab 工作分支或引用；用户编辑的正式 skill source 保存在 GitLab 中。
3. 用户对某个 draft version 或指定 git revision 发起 publish。
4. `SkillsModule` 解析并冻结 `source_commit_sha`，写入 `skill_publish_record`，将待发布版本固化为正式编译输入。
5. `SkillsModule` 创建 `skill_compile_request` 并投递 compile job。
6. `SkillCompiler` 读取 GitLab 中该冻结 commit 对应的 skill source，执行 parse、normalize、validate、emit。
7. 编译成功则写入 `eg_compile_artifact`，失败则写入 `compile_diagnostic`；最新已发布版本只指向最新成功 artifact。

### 7.2 运行时：Invocation 与 Runtime Execution

1. `Gateway` 接收某个 skill 的调用请求，创建 `skill_invocation`。
2. 系统解析该 skill 当前生效的 `skill_version` 与 `compile_artifact`。
3. 创建 `run`、首个 `session_token_snapshot`、`terminal_session`。
4. 写入 `runtime_job`，由 worker claim。
5. `RuntimeKernel` 根据 formal v5 循环推进：
   - `Sync`
   - `Enabled`
   - `Sel`
   - `Actor`
   - `Merge`
   - `Trace`
6. 如果 actor 需要 terminal、MCP 或 LLM 能力，则经 `CapabilityHost` 调用对应 gateway。
7. 每次推进都生成新的 snapshot 与 trace event，直到 run 结束或进入等待输入状态。

### 7.3 运行后：Replay 与 Observability

1. `ReplayService` 读取 run 相关 snapshot、trace、terminal transcript。
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
- 主链路关联：skill 总对象与 GitLab 仓库绑定，供发布、调用和检索使用

#### 8.2.2 `skill_version`

- 主键：`id UUID`
- 外键：`skill_definition_id -> skill_definition.id`
- 关键字段：`version_no`、`source_ref`、`source_commit_sha`、`manifest_snapshot JSONB`、`runtime_policy_snapshot JSONB`、`status`
- 状态枚举：`draft | published | archived`
- 唯一约束：`uk_skill_version_definition_version_no`
- 索引：`idx_skill_version_definition_status`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：skill 的草稿或冻结版本；published version 必须绑定明确 `commit SHA`

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

#### 8.2.6 `compile_diagnostic`

- 主键：`id UUID`
- 外键：`skill_compile_request_id`、`skill_version_id`
- 关键字段：`severity`、`code`、`message`、`location JSONB`、`category`
- 状态枚举：无独立状态，按 request 生命周期归属
- 唯一约束：无
- 索引：`idx_compile_diagnostic_request_severity`
- 审计字段：`created_at`
- 主链路关联：publish / compile 失败与告警定位

#### 8.2.7 `skill_invocation`

- 主键：`id UUID`
- 外键：`skill_definition_id`、`skill_version_id`、`compile_artifact_id`
- 关键字段：`gateway_type`、`input_envelope JSONB`、`status`、`idempotency_key`
- 状态枚举：`accepted | queued | running | succeeded | failed | cancelled`
- 唯一约束：`uk_skill_invocation_idempotency_key`
- 索引：`idx_skill_invocation_status_created_at`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：gateway 进入系统后的正式调用对象

#### 8.2.8 `run`

- 主键：`id UUID`
- 外键：`invocation_id`、`skill_definition_id`、`skill_version_id`、`compile_artifact_id`、`terminal_session_id`
- 关键字段：`status`、`runtime_phase`、`latest_snapshot_seq`、`exit_reason`
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
- 外键：`terminal_session_id`、`run_id`、`trace_event_id`、`artifact_object_id`
- 关键字段：`direction`、`event_kind`、`mime_type`、`payload_inline`、`seq_no`
- 状态枚举：无独立状态
- 唯一约束：`uk_terminal_event_session_seq`
- 索引：`idx_terminal_event_run_seq`
- 审计字段：`created_at`
- 主链路关联：terminal transcript 与输入输出审计

#### 8.2.13 `mcp_server`

- 主键：`id UUID`
- 外键：无
- 关键字段：`name`、`endpoint`、`transport`、`status`、`discovery_snapshot JSONB`
- 状态枚举：`enabled | disabled | error`
- 唯一约束：`uk_mcp_server_name`
- 索引：`idx_mcp_server_status_updated_at`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：MCP tool discovery 与调用入口配置

#### 8.2.14 `mcp_tool`

- 主键：`id UUID`
- 外键：`mcp_server_id -> mcp_server.id`
- 关键字段：`tool_name`、`tool_schema JSONB`、`enabled`
- 状态枚举：通过 `enabled` 布尔控制
- 唯一约束：`uk_mcp_tool_server_tool_name`
- 索引：`idx_mcp_tool_server_enabled`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：CapabilityHost 的可调用工具目录

#### 8.2.15 `inference_provider`

- 主键：`id UUID`
- 外键：无
- 关键字段：`provider_key`、`endpoint`、`status`、`credential_ref`
- 状态枚举：`enabled | disabled | error`
- 唯一约束：`uk_inference_provider_key`
- 索引：`idx_inference_provider_status`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：模型调用 provider 抽象

#### 8.2.16 `model_catalog`

- 主键：`id UUID`
- 外键：`provider_id -> inference_provider.id`
- 关键字段：`model_key`、`model_family`、`supports_tools`、`supports_structured_output`、`status`
- 状态枚举：`active | deprecated | disabled`
- 唯一约束：`uk_model_catalog_provider_model_key`
- 索引：`idx_model_catalog_provider_status`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：Inference Gateway 路由目标清单

#### 8.2.17 `gateway_policy`

- 主键：`id UUID`
- 外键：无
- 关键字段：`policy_type`、`target_ref`、`rules JSONB`、`status`
- 状态枚举：`active | disabled`
- 唯一约束：`uk_gateway_policy_type_target_ref`
- 索引：`idx_gateway_policy_type_status`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：tool、model、budget、timeout 等统一策略

#### 8.2.18 `capability_binding`

- 主键：`id UUID`
- 外键：`compile_artifact_id`、`gateway_policy_id`
- 关键字段：`binding_key`、`binding_type`、`target_ref`、`node_selector JSONB`
- 状态枚举：通过 `binding_type` 与 `enabled` 控制
- 唯一约束：`uk_capability_binding_artifact_binding_key`
- 索引：`idx_capability_binding_artifact_type`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：artifact 中节点与能力入口的正式绑定

#### 8.2.19 `runtime_job`

- 主键：`id UUID`
- 外键：`run_id`、`compile_request_id`
- 关键字段：`job_type`、`status`、`payload JSONB`、`lease_until`、`dedupe_key`、`attempt_no`
- 状态枚举：`pending | claimed | running | succeeded | failed | cancelled | deadletter`
- 唯一约束：`uk_runtime_job_dedupe_key`
- 索引：`idx_runtime_job_status_available_at`、`idx_runtime_job_lease_until`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：数据库驱动的异步任务系统

#### 8.2.20 `worker_heartbeat`

- 主键：`id UUID`
- 外键：无
- 关键字段：`worker_name`、`worker_type`、`capabilities JSONB`、`last_seen_at`、`status`
- 状态枚举：`alive | stale | drained`
- 唯一约束：`uk_worker_heartbeat_worker_name`
- 索引：`idx_worker_heartbeat_status_last_seen_at`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：worker 健康检查与调度可见性

#### 8.2.21 `sandbox_lease`

- 主键：`id UUID`
- 外键：`run_id`、`runtime_job_id`
- 关键字段：`sandbox_key`、`lease_status`、`lease_until`、`driver_type`、`connection_info JSONB`
- 状态枚举：`requested | active | released | expired | error`
- 唯一约束：`uk_sandbox_lease_sandbox_key`
- 索引：`idx_sandbox_lease_status_lease_until`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：高风险 actor 的隔离执行租约

#### 8.2.22 `artifact_object`

- 主键：`id UUID`
- 外键：无
- 关键字段：`bucket`、`object_key`、`media_type`、`size_bytes`、`checksum`
- 状态枚举：无独立状态
- 唯一约束：`uk_artifact_object_bucket_object_key`
- 索引：`idx_artifact_object_media_type_created_at`
- 审计字段：`created_at`
- 主链路关联：artifact 文件、terminal 二进制内容、大对象证据

#### 8.2.23 `runtime_config`

- 主键：`id UUID`
- 外键：无
- 关键字段：`config_key`、`config_scope`、`config_value JSONB`、`status`
- 状态枚举：`active | disabled`
- 唯一约束：`uk_runtime_config_scope_key`
- 索引：`idx_runtime_config_scope_status`
- 审计字段：`created_at`、`updated_at`
- 主链路关联：运行时与 gateway 的可调配置

#### 8.2.24 `operation_log`

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
| `POST` | `/api/skills` | 创建 skill 与 GitLab 绑定 | `{ key, name, description, gitlab_project_id, default_branch, manifest_path }` | `skill detail` |
| `GET` | `/api/skills/{skill_id}` | skill 详情 | 无 | `skill detail + versions summary` |
| `PATCH` | `/api/skills/{skill_id}` | 更新 skill 元数据与仓库绑定 | `{ name, description, status, default_branch, manifest_path }` | `skill detail` |
| `POST` | `/api/skills/{skill_id}/versions` | 创建草稿版本 | `{ base_version_id?, source_ref? }` | `skill version detail` |
| `GET` | `/api/skills/{skill_id}/versions/{skill_version_id}` | 版本详情 | 无 | `skill version detail` |
| `PATCH` | `/api/skills/{skill_id}/versions/{skill_version_id}` | 更新 draft 版本元数据或跟踪引用 | `{ source_ref, compile_config, runtime_policy }` | `skill version detail` |
| `POST` | `/api/skills/{skill_id}/publish` | 发布 skill 并触发自动编译 | `{ skill_version_id, publish_reason, expected_commit_sha? }` | `{ publish_record, compile_request }` |
| `GET` | `/api/skills/{skill_id}/publishes` | 发布记录列表 | `query: status,page` | `publish record list` |

状态要求：

- `PATCH skill version` 只允许 `draft`。
- `publish` 必须冻结到明确的 `source_commit_sha`。
- 对已发布版本的变更必须通过创建新 draft 完成。

### 9.5 `/api/compiler/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/compiler/requests` | compile request 列表 | `query: skill_id,status,page` | `compile request summary list` |
| `GET` | `/api/compiler/requests/{compile_request_id}` | request 详情 | 无 | `compile request detail` |
| `POST` | `/api/compiler/requests/{compile_request_id}/retry` | 重试编译 | 无 | `compile request detail` |
| `GET` | `/api/compiler/requests/{compile_request_id}/diagnostics` | 诊断列表 | 无 | `compile diagnostic list` |
| `GET` | `/api/compiler/artifacts/{compile_artifact_id}` | artifact 详情 | 无 | `compile artifact detail` |

状态要求：

- `/api/compiler/*` 只处理编译对象，不承载 skill 发布动作。
- 每个 compile request 都必须绑定冻结的 `skill_version` 与 `source_commit_sha`。
- `retry` 必须保留历史 request，不覆盖原记录。

### 9.6 `/api/gateway/invocations/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `POST` | `/api/gateway/invocations` | 发起 skill 调用 | `{ skill_key, version_selector, input_envelope, gateway_type }` | `invocation detail` |
| `GET` | `/api/gateway/invocations` | invocation 列表 | `query: skill_key,status,page` | `invocation summary list` |
| `GET` | `/api/gateway/invocations/{invocation_id}` | invocation 详情 | 无 | `invocation detail + run summary` |

状态要求：

- `POST` 必须按 `skill_key + version_selector` 解析到具体 `skill_version` 与 `compile_artifact`。
- 同一个 `Idempotency-Key` 不得生成重复 invocation。

### 9.7 `/api/runs/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/runs` | run 列表 | `query: status,skill_id,page` | `run summary list` |
| `GET` | `/api/runs/{run_id}` | run 详情 | 无 | `run detail` |
| `POST` | `/api/runs/{run_id}/cancel` | 取消 run | `{ reason }` | `run detail` |
| `GET` | `/api/runs/{run_id}/snapshots` | snapshot 列表 | `query: from_seq,to_seq` | `session token snapshot list` |
| `GET` | `/api/runs/{run_id}/trace-events` | trace 列表 | `query: from_seq,to_seq,type` | `trace event list` |

### 9.8 `/api/terminal/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/terminal/sessions/{run_id}` | 获取 run 的 terminal session | 无 | `{ terminal_session, transcript_summary }` |
| `POST` | `/api/terminal/sessions/{run_id}/events` | 注入 terminal input 或记录 output | `{ direction, event_kind, mime_type, payload_inline, artifact_object_id }` | `{ accepted, event_id }` |
| `GET` | `/api/terminal/sessions/{run_id}/events` | transcript 列表 | `query: from_seq,to_seq` | `terminal events` |

### 9.9 `/api/replay/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/replay/runs` | replay 检索 | `query: skill_id,status,time range` | `run summary list` |
| `GET` | `/api/replay/runs/{run_id}` | 完整回放 | 无 | `replay timeline detail` |
| `GET` | `/api/replay/traces/{trace_id}` | trace 详情 | 无 | `trace detail payload` |

### 9.10 `/api/gateway/mcp/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/gateway/mcp/servers` | MCP server 列表 | 无 | `mcp server list` |
| `POST` | `/api/gateway/mcp/servers` | 注册或更新 server | `{ name, endpoint, transport }` | `mcp server detail` |
| `POST` | `/api/gateway/mcp/servers/{server_id}/discover` | 触发 tool discovery | 无 | `{ tools_count }` |
| `GET` | `/api/gateway/mcp/servers/{server_id}/tools` | tool 列表 | 无 | `mcp tool list` |

### 9.11 `/api/gateway/inference/*`

| Method | Path | 用途 | Request | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/gateway/inference/providers` | provider 列表 | 无 | `inference provider list` |
| `POST` | `/api/gateway/inference/providers` | 注册或更新 provider | `{ provider_key, endpoint, credential_ref }` | `inference provider detail` |
| `GET` | `/api/gateway/inference/models` | model catalog | `query: provider_id` | `model list` |
| `POST` | `/api/gateway/inference/routes` | 更新模型路由 | `{ provider_id, model_id, route_key, status }` | `model route detail` |

### 9.12 `/api/runtime/*`

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
    - `session_token.snapshot.appended`
    - `trace.event.appended`
    - `terminal.event.appended`
    - `run.completed`

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
- `trace_id`
- `span_id`
- `tool_call_id`

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
- `terminal_wait_duration_seconds`

### 11.4 审计原则

- 所有关键状态变更都必须可通过 `operation_log + trace_event + snapshot` 追溯。
- replay 服务不能自行推断不存在的状态；只允许重组已持久化事实。
- gateway 与模型调用必须记录请求摘要、超时、错误与结果摘要。

## 12. 开发切片、实现顺序与完成定义

### 12.1 开发切片

1. `Slice A`：数据库骨架、基础 FastAPI、接口契约骨架、健康检查、对象存储、GitLab 接线与 OTel 接线
2. `Slice B`：`SkillsModule + GitLab binding + Publish flow + SkillCompiler`
3. `Slice C`：`Invocation + Run + RuntimeKernel` 主循环
4. `Slice D`：`TerminalGateway + MCPGateway + LLMInferenceGateway + CapabilityHost + AgentModule`
5. `Slice E`：`ReplayService + WebSocket + runtime admin endpoints`

### 12.2 实现顺序

1. 先落核心数据模型和迁移，确保服务端对象稳定。
2. 再落 `skills -> publish -> compile -> artifact`，让运行前链路可闭环。
3. 随后落 `invocation -> run -> snapshot -> trace`，让运行时链路可闭环。
4. 再接入 terminal、MCP、LLM gateway 与 `AgentModule`，并按需复用 DeerFlow 的参考实现。
5. 最后补 replay、WS、OTel 与 runtime 运维接口。

### 12.3 完成定义

- 技能发布后能够冻结 GitLab revision、自动生成 compile request，并得到成功或失败结果。
- 成功发布的 skill 能被 gateway 调用，并创建 run。
- `RuntimeKernel` 能围绕 formal v5 约束推进至少一条完整 run。
- 运行中能写出 snapshot、trace、terminal transcript，并通过 API / WS 对外提供。
- 运行完成后能通过 replay 和 OTel 进行排障。
- 读完本文档后，后端、运行时与基础设施团队无需再补关键架构决策即可开工。
