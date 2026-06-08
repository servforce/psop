# PSOP 服务端详细设计 v1

## 1. 文档说明

本文档是当前 `backend/app` 代码实现的服务端详细设计基线。它描述已经落地的 FastAPI、领域模块、数据库模型、接口、任务系统、运行时和可观测实现。

本文不把未来规划写成已实现事实。凡是当前代码没有表、路由或模块的能力，统一放在“未实现/保留项”中。

## 2. 当前服务端范围

当前代码覆盖以下闭环：

- `PSkills -> GitLab source -> Publish -> Compile -> EG Compile Artifact`
- `Invocation -> Run -> Terminal Session -> Session Token Snapshot -> RunTrace`
- `Run input/output -> RunEvent / RunEventPart -> Object Store`
- `Replay -> timeline / snapshots / run traces / run events`
- `Skill Test Scenario -> real invocation/run -> timeline driver -> semantic judge`
- `Agent Prompt Pack -> version -> published -> active binding`
- `Runtime Job -> DB claim -> embedded worker`
- `OpenTelemetry + structured logs`

当前代码不覆盖：

- 租户、用户、权限、审批流。
- 独立 MCP Gateway API。
- 独立 CapabilityHost 模块。
- 独立 SandboxManager 和 sandbox lease。
- 独立 scheduler 进程。
- Alembic migration。
- FastAPI 直接托管 `static/`。

## 3. 技术栈

| 维度 | 当前实现 |
| --- | --- |
| 语言 | Python 3.11+ |
| Web 框架 | FastAPI |
| DTO / 配置 | Pydantic v2 / pydantic-settings |
| ORM | SQLAlchemy 2.x |
| 数据库驱动 | psycopg 3.x；测试可用 sqlite |
| 对象存储 | boto3 + S3-compatible / MinIO |
| HTTP client | httpx |
| LLM Gateway | OpenAI-compatible `/chat/completions` |
| ASR Gateway | HTTP adapter |
| 可观测 | OpenTelemetry SDK、FastAPI/HTTPX/SQLAlchemy/Logging instrumentation |
| 前端静态预览 | Node dev server，独立于 FastAPI |

## 4. 应用启动与依赖注入

入口：

- `backend/app/main.py`：导出 `app = create_app()`。
- `backend/app/app.py`：创建 FastAPI app、注册路由、配置 CORS、异常处理、OTel、lifespan。

`create_app()` 在 `app.state` 中挂载：

- `settings`
- `db_manager`
- `gitlab_gateway`
- `inference_gateway`
- `asr_gateway`
- `object_store`
- `observability`

lifespan 行为：

- 配置日志和 OTel。
- 根据 `PSOP_DATABASE_AUTO_CREATE_SCHEMA` 可选执行 `Base.metadata.create_all()`。
- 根据 `PSOP_DATABASE_CHECK_ON_STARTUP` 可选检查数据库连接。
- 根据 `PSOP_RUNTIME_WORKER_ENABLED` 可选启动内置 `RuntimeJobWorker`。
- 退出时取消 worker、释放数据库引擎、关闭 OTel provider。

依赖注入在 `backend/app/api/dependencies.py` 中完成，router 只解析请求、获取 session/service 并返回 DTO。

## 5. 目录与模块边界

```text
backend/app/
  app.py                 FastAPI factory 与 lifespan
  main.py                uvicorn 入口
  api/
    router.py            /api/v1 聚合路由
    dependencies.py      Settings/DB/Gateway/Service 注入
    routes/              system/skills/compiler/runtime/skill_tests/agent_prompts/inference
  core/
    config.py            PSOP_* 配置
    logging.py           日志与上下文
    observability.py     OTel 配置和 helper
  domain/
    skills/              Skill、GitLab source、发布、素材、素材生成
    compiler/            编译请求、formal-v5 校验、EG artifact
    runtime/             Invocation、Run、Session Token、Terminal、Replay
    jobs/                共享 runtime_job 队列、worker、进度 read model
    skill_tests/         黑盒时序测试、driver、judge、fork
    agent_prompts/       Prompt Pack 定义、版本、binding
  gateway/
    gitlab.py            GitLab API adapter
    inference.py         OpenAI-compatible LLM adapter
    asr.py               ASR HTTP adapter
  infra/
    database.py          SQLAlchemy engine/session/Base
    object_store.py      S3-compatible object adapter
```

## 6. 配置项

配置类是 `app.core.config.Settings`，从 `backend/.env` 和根目录 `.env` 读取，前缀为 `PSOP_`。

主要配置：

| 配置组 | 字段 |
| --- | --- |
| App | `APP_NAME`、`APP_VERSION`、`ENVIRONMENT`、`DEBUG`、`API_PREFIX`、`LOG_LEVEL`、`LOG_FORMAT`、`CORS_ALLOW_ORIGINS` |
| Database | `DATABASE_URL` 或 `DATABASE_HOST/PORT/NAME/USER/PASSWORD`、`DATABASE_CHECK_ON_STARTUP`、`DATABASE_AUTO_CREATE_SCHEMA` |
| GitLab | `GITLAB_API_BASE_URL`、`GITLAB_TOKEN`、`GITLAB_SKILLS_GROUP_PATH`、`GITLAB_DEFAULT_BRANCH`、`GITLAB_TIMEOUT_SECONDS` |
| Object Store | `OBJECT_STORE_ENDPOINT`、`OBJECT_STORE_ACCESS_KEY`、`OBJECT_STORE_SECRET_KEY`、`OBJECT_STORE_BUCKET`、`OBJECT_STORE_REGION`、`OBJECT_STORE_SECURE` |
| Upload / Media | `TEST_DATA_MAX_UPLOAD_BYTES`、`RAW_MATERIAL_MAX_UPLOAD_BYTES`、`RAW_MATERIAL_VIDEO_MAX_UPLOAD_BYTES`、`RAW_MATERIAL_EXTRACT_TEXT_MAX_CHARS`、`VIDEO_MAX_ANALYZED_FRAMES` |
| OTel | `OTEL_ENABLED`、`OTEL_TRACES_ENABLED`、`OTEL_LOGS_ENABLED`、`OTEL_CONSOLE_EXPORTER`、`OTEL_EXPORTER_OTLP_ENDPOINT`、`OTEL_EXPORTER_OTLP_PROTOCOL`、`OTEL_SERVICE_NAME` |
| LLM | `LLM_PROVIDER`、`LLM_API_BASE_URL`、`LLM_API_KEY`、`LLM_TEXT_MODEL`、`LLM_MULTIMODAL_MODEL`、thinking 相关字段、`LLM_TIMEOUT_SECONDS` |
| ASR | `ASR_API_BASE_URL`、`ASR_LANGUAGE`、`ASR_TIMEOUT_SECONDS`、`ASR_TEMPERATURE` |
| Runtime Jobs | `RUNTIME_WORKER_ENABLED`、`RUNTIME_JOB_LEASE_SECONDS`、`RUNTIME_JOB_MAX_ATTEMPTS`、`RUNTIME_STEP_TIMEOUT_SECONDS` |

## 7. 领域设计

### 7.1 Skills

代码位置：`backend/app/pskills/*`

职责：

- 创建 Skill 时通过 GitLab gateway 创建项目和默认 `README.md`、`SKILL.md`、`skill.yaml`。
- 保存 Skill metadata、source 和 repository file 时同步 GitLab commit 和 draft `manifest_snapshot`。
- 发布 Skill 时冻结 GitLab branch head，创建 published `PPSkillVersion`、`PPSkillPublishRecord`、`SkillCompileRequest` 和 `RuntimeJob(job_type=compile)`。
- 管理 materials：上传、对象存储、分析、派生资产、从视频素材生成 Skill draft。

当前 Skill source 事实源：

- 用户可读源码：GitLab repository。
- 结构化机器契约：`pskill_version.manifest_snapshot`。
- `skill.yaml` 当前由服务端生成并写回 GitLab，作为结构化契约的序列化视图。

### 7.2 Compiler

代码位置：`backend/app/compiler/*`

职责：

- `SkillCompileRequest` 记录编译请求。
- `RuntimeJob(job_type=compile)` 承载异步编译执行。
- `SkillCompileAgent` 通过 active DB prompt binding `default.compile_agent` 或 repo fallback `skill_compilation/formal_v5_compile/v1` 构造 LLM 编译请求。
- `DomainPackRegistry` 解析 `generic/v1`、`industrial_inspection/v1`、`equipment_maintenance/v1` 等 domain pack。
- `formal_v5.validate_and_normalize_artifact()` 做确定性校验和规范化。
- 成功后写入 `ArtifactObject(content_json=artifact)` 和 `EgCompileArtifact`。

当前 MVP 支持：

- node kind：`start`、`input`、`llm`、`tool`、`terminal`。
- actor：`runtime.start`、`runtime.input`、`agent.llm`、`capability.demo_tool`、`runtime.terminal`。
- tool：`psop.demo.inspect_input`。
- guard DSL：`always`、`phase_is`、`field_exists`、`field_equals`、`all`、`any`、`not`。
- merge DSL：`op=set`。

Formal v5 中的 `approval`、`timer`、`skill` 等节点可以作为已知概念存在，但当前 validator 会把它们判为 MVP Runtime 不支持。

### 7.3 Runtime

代码位置：`backend/app/runtime/*`

职责：

- 创建 invocation、run、terminal session、默认 terminal bindings、初始 token snapshot。
- 加载 artifact 并执行 Runtime loop。
- 维护 `SessionTokenSnapshot`、`RunTrace`、`RunEvent`、`RunEventPart`。
- 支持 waiting checkpoint、terminal evidence、evaluation decision、abort、recoverable failure。
- 构建 Replay detail。

当前 loop 语义：

1. 从最新 snapshot 读取 token。
2. 根据 terminal cursor 补读 `run_event`，把新 input 绑定到当前 wait checkpoint。
3. 根据 guard 计算 enabled nodes。
4. 按 priority 和 node id 选择节点。
5. 执行 actor：start/input/llm/tool/terminal。
6. 按 merge rule 写 token。
7. 必要时追加 terminal output event 或进入 wait checkpoint。
8. 追加 snapshot 与 run trace。
9. 命中 success/aborted/wait/failed 条件后更新 run 状态。

当前能力调用边界：

- LLM 由 `LlmInferenceGateway` 执行。
- 多模态 LLM 由 run event part 中的对象存储内容转 base64 后进入 OpenAI-compatible content。
- tool 只有内置 demo tool。
- 当前没有独立 `CapabilityHost` 类，能力解析和调用集中在 `RuntimeService`。

### 7.4 Run Events API

代码位置：`backend/app/api/routes/runtime.py` 和 `RuntimeService`

职责：

- `POST /runs/{run_id}/events` 支持 JSON text input 和 multipart multimodal input；旧 `/terminal/sessions/{run_id}/events` 保留为兼容入口。
- JSON 请求不接收客户端构造的 `parts`。
- multipart 请求必须包含 `event` JSON 字段；文件字段名不限，所有 `UploadFile` 都会被收集。
- 服务端生成 part id：`text_1`、`image_1`、`audio_1`、`video_1` 等。
- 非 text part 必须是 `image/*`、`audio/*`、`video/*`。
- 文件写入对象存储，同时创建 `artifact_object` 和 `run_event_part.artifact_object_id`。
- `Idempotency-Key` 或 `external_event_id` 映射到 `run_id + external_event_id` 去重。
- 事件 append 后通过当前进程内 WebSocket hub 广播新 run events；REST 仍是权威状态源。

### 7.5 Jobs

代码位置：`backend/app/jobs/*`

当前 job type：

- `material_analysis`
- `pskill_build`
- `pskill_compile`（兼容旧 `compile` 查询与 worker 处理）
- `pskill_test`（兼容旧 `skill_test_timeline_driver` 查询与 worker 处理）
- `runtime_step`（兼容旧 `runtime` 查询与 worker 处理）

状态：

- 常用状态：`pending`、`running`、`succeeded`、`failed`、`cancelled`、`dead_letter`。
- read model 兼容：`retryable_failed`、`canceled`、`deadletter`。

Claim 规则：

- 按 job type 顺序轮询。
- 查询 `status in ('pending', 'retryable_failed') and available_at <= now`。
- 使用 `with_for_update(skip_locked=True)` 锁定一条。
- claim 时设置 `status=running`、`attempt_no += 1`、`worker_name`、`lease_until`。
- claim 前恢复过期 lease：`running and lease_until <= now` 的任务若 `attempt_no < max_attempts`，回到 `pending` 并按 `5 * attempt_no` 秒退避；否则置为 `dead_letter`。
- lease 恢复会清空 `worker_name` / `lease_until`，写入 `last_error`，并在 `metrics.lease_recovery_count` / `metrics.last_lease_recovered_at` 中保留审计信息。
- worker 未处理异常按 `attempt_no < max_attempts` 退回 `pending`，耗尽重试后置为 `dead_letter`；业务执行失败仍使用 `failed`。
- 当前没有独立 scheduler；过期 lease 恢复由内置 worker 在轮询前执行。

### 7.6 Skill Tests

代码位置：`backend/app/testing/*`

职责：

- 管理 `SkillTestScenario`、`SkillTestAsset`、`SkillTestScenarioRun`。
- 启动测试时创建真实 invocation/run/terminal session。
- 写入 `RuntimeJob(job_type=pskill_test)`。
- driver 按 timeline `at_ms` 追加真实 terminal input。
- 语义期望通过 LLM judge 评估，结果写入 `SkillTestExpectationEvaluation`。
- 支持从 review cursor fork 新 scenario 或 fork debug invocation。

### 7.7 Agent Prompts

代码位置：`backend/app/agent_prompts/*`、`backend/app/agents/*`

职责：

- 管理 Prompt Pack definition、version 和 usage binding。
- DB published version 是运行时优先事实源。
- repo-backed `backend/app/agents/*` 是 seed/fallback。
- 当前 usage key 包括 `default.compile_agent`、`runtime.llm_node_fallback`、`skill_test.semantic_judge` 等。

### 7.8 Gateways

- `GitLabSkillSourceGateway`
  - 创建项目、读取源码、提交源码、提交单文件、读取树、读取 branch head。
- `OpenAICompatibleInferenceGateway`
  - 提供 text 和 multimodal route capability。
  - 调用 `/chat/completions`。
  - 记录 redacted request snapshot、usage、provider/model。
- `HttpAsrGateway`
  - 用于视频素材分析中的语音识别。

当前没有独立 MCP Gateway API。

## 8. 数据库模型

当前所有表由 SQLAlchemy declarative models 定义，`DatabaseManager.create_schema()` 通过 `Base.metadata.create_all()` 创建。当前代码没有 Alembic migration。

### 8.1 Skills

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `pskill_definition` | `key`、`name`、`status`、`gitlab_project_id`、`repository_url`、`default_branch`、`manifest_path`、`latest_draft_version_id`、`latest_published_version_id` | Skill 总对象和 GitLab 绑定 |
| `pskill_version` | `pskill_definition_id`、`version_no`、`status`、`source_ref`、`source_commit_sha`、`manifest_snapshot`、`runtime_policy_snapshot` | draft/published 版本 |
| `pskill_publish_record` | `pskill_definition_id`、`pskill_version_id`、`publish_reason`、`publish_status`、`published_commit_sha`、`release_ref` | 发布记录 |
| `pskill_material` | `pskill_definition_id`、`artifact_object_id`、`material_kind`、`mime_type`、`status`、`size_bytes`、`checksum` | PSkill 素材 |
| `pskill_material_generation` | `pskill_definition_id`、`material_ids`、`status`、`prompt_metadata`、`generated_files`、`committed_commit_sha` | 从素材生成 PSkill draft |
| `pskill_material_analysis` | `pskill_definition_id`、`material_id`、`status`、`analysis_result`、`error_details` | 素材分析 |
| `pskill_material_derived_asset` | `material_id`、`analysis_id`、`artifact_object_id`、`asset_kind`、`timestamp_ms` | 视频关键帧等派生资产 |

### 8.2 Compiler / Artifact

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `skill_compile_request` | `pskill_definition_id`、`pskill_version_id`、`trigger_type`、`source_commit_sha`、`status`、`dedupe_key` | 编译请求 |
| `artifact_object` | `bucket`、`object_key`、`media_type`、`size_bytes`、`checksum`、`content_json` | 统一对象索引；JSON artifact 当前直接保存在 `content_json` |
| `eg_compile_artifact` | `skill_compile_request_id`、`pskill_version_id`、`artifact_object_id`、`formal_revision`、`artifact_version`、`graph_summary`、`capability_summary`、`status` | Runtime 正式执行输入索引 |
| `compile_diagnostic` | `skill_compile_request_id`、`pskill_version_id`、`severity`、`code`、`message`、`location`、`category` | 编译诊断 |

### 8.3 Runtime / Terminal / Replay

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `skill_invocation` | `pskill_definition_id`、`pskill_version_id`、`compile_artifact_id`、`gateway_type`、`input_envelope`、`terminal_context`、`binding_preferences`、`status`、`idempotency_key` | Gateway 调用对象 |
| `run` | `invocation_id`、`pskill_definition_id`、`pskill_version_id`、`compile_artifact_id`、`terminal_session_id`、`status`、`runtime_phase`、`latest_*_seq`、`final_output`、`exit_reason` | 逻辑运行实例 |
| `session_token_snapshot` | `run_id`、`seq_no`、`token_payload`、`enabled_set`、`selection_summary`、`snapshot_hash` | 正式状态快照 |
| `run_trace` | `run_id`、`seq_no`、`phase`、`event_type`、`trace_id`、`span_id`、`payload` | replay/observability/OTel 关联事件 |
| `terminal_session` | `run_id`、`mode`、`status`、`opened_at`、`closed_at` | Run 的 I/O 会话 |
| `run_capability_binding` | `run_id`、`compile_artifact_id`、`requirement_key`、`binding_type`、`capability`、`target_kind`、`target_ref`、`channel`、`schema_ref`、`policy_snapshot` | 当前 run 的 terminal binding |
| `run_event` | `terminal_session_id`、`run_id`、`run_trace_id`、`artifact_object_id`、`run_capability_binding_id`、`direction`、`event_kind`、`payload_inline`、`seq_no`、`external_event_id` | append-only transcript |
| `run_event_part` | `run_event_id`、`run_id`、`artifact_object_id`、`part_id`、`order_index`、`kind`、`mime_type`、`text_inline`、`checksum`、`metadata` | 多模态输入 part |

### 8.4 Jobs / Tests / Prompts

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `runtime_job` | `job_type`、`status`、`payload`、`run_id`、`compile_request_id`、`lease_until`、`dedupe_key`、`attempt_no`、`max_attempts`、`metrics` | 数据库任务队列 |
| `skill_test_scenario` | `pskill_definition_id`、`target_compile_artifact_id`、`duration_ms`、`timeline`、`judge_policy`、`fork_seed`、`status` | 测试场景 |
| `skill_test_asset` | `pskill_definition_id`、`scenario_id`、`artifact_object_id`、`lane_id`、`filename`、`mime_type`、`checksum` | 场景资源 |
| `skill_test_scenario_run` | `pskill_definition_id`、`scenario_id`、`invocation_id`、`run_id`、`status`、`driver_status`、`driver_cursor`、`driver_events`、`timeline`、`result_summary` | 一次测试运行 |
| `skill_test_expectation_evaluation` | `scenario_run_id`、`expectation_id`、`status`、`confidence`、`reason`、`evidence_refs`、`judge_provider`、`judge_model`、`prompt_hash` | 语义期望判断 |
| `agent_prompt_definition` | `key`、`agent_id`、`scenario`、`name`、`status`、`active_version_id` | Prompt Pack 定义 |
| `agent_prompt_version` | `definition_id`、`version_no`、`version_label`、`status`、`route_key`、`files`、`content_hash` | Prompt Pack 版本 |
| `agent_prompt_binding` | `usage_key`、`definition_id`、`active_version_id` | usage key 到 active prompt version |

## 9. REST API

所有 API router 默认挂在 `PSOP_API_PREFIX`，当前默认 `/api/v1`。根路径 `/` 和 `/healthz` 不带该前缀。

### 9.1 System

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/` | 服务信息 |
| `GET` | `/healthz` | 根健康检查 |
| `GET` | `/api/v1/system` | 服务信息 |
| `GET` | `/api/v1/system/health` | API 健康检查 |

当前没有 `/api/v1/system/summary` 或 `/api/v1/system/config`。

### 9.2 PSkills / Materials

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/pskills` | PSkill 列表；支持 `search`、`status`、`is_published` |
| `POST` | `/api/v1/pskills` | 创建 PSkill 和 GitLab project |
| `GET` | `/api/v1/pskills/{skill_id}` | PSkill 详情 |
| `PATCH` | `/api/v1/pskills/{skill_id}` | 更新名称/描述 |
| `DELETE` | `/api/v1/pskills/{skill_id}` | 按确认名称归档 PSkill |
| `GET` | `/api/v1/pskills/{skill_id}/source` | 读取 `README.md`、`SKILL.md`、`skill.yaml` |
| `PUT` | `/api/v1/pskills/{skill_id}/source` | 保存三类 source |
| `GET` | `/api/v1/pskills/{skill_id}/repository/tree` | GitLab 仓库树 |
| `GET` | `/api/v1/pskills/{skill_id}/repository/files` | 读取仓库文件 |
| `PUT` | `/api/v1/pskills/{skill_id}/repository/files` | 保存仓库文件 |
| `POST` | `/api/v1/pskills/{skill_id}/repository/files` | 新建仓库文件 |
| `POST` | `/api/v1/pskills/{skill_id}/repository/folders` | 新建文件夹 `.gitkeep` |
| `POST` | `/api/v1/pskills/{skill_id}/publish` | 发布 PSkill 并创建 compile job |
| `GET` | `/api/v1/pskills/{skill_id}/publishes` | 发布记录 |
| `POST` | `/api/v1/pskills/{skill_id}/materials` | 上传素材 |
| `GET` | `/api/v1/pskills/{skill_id}/materials` | 素材列表 |
| `POST` | `/api/v1/pskills/{skill_id}/materials/generate-skill-draft` | 从素材生成 PSkill draft |
| `POST` | `/api/v1/pskills/{skill_id}/materials/{material_id}/analyze` | 重新分析素材 |
| `GET` | `/api/v1/pskills/{skill_id}/materials/{material_id}/analysis` | 读取分析 |
| `GET` | `/api/v1/pskills/{skill_id}/materials/{material_id}` | 素材详情 |
| `GET` | `/api/v1/pskills/{skill_id}/materials/{material_id}/content` | 素材内容，支持 Range |
| `GET` | `/api/v1/pskills/{skill_id}/materials/{material_id}/derived-assets/{asset_id}/content` | 派生资产内容 |
| `DELETE` | `/api/v1/pskills/{skill_id}/materials/{material_id}` | 归档素材 |

### 9.3 Compiler

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/compiler/requests` | 编译请求列表；支持 `skill_id`、`status` |
| `POST` | `/api/v1/compiler/pskills/{skill_id}/compile` | 手动创建编译请求 |
| `GET` | `/api/v1/compiler/requests/{compile_request_id}` | 编译请求详情 |
| `POST` | `/api/v1/compiler/requests/{compile_request_id}/retry` | 同步重试该请求 |
| `GET` | `/api/v1/compiler/requests/{compile_request_id}/progress` | 发布/编译进度快照 |
| `GET` | `/api/v1/compiler/requests/{compile_request_id}/events` | SSE 进度流 |
| `GET` | `/api/v1/compiler/requests/{compile_request_id}/diagnostics` | 诊断列表 |
| `GET` | `/api/v1/compiler/artifacts/{compile_artifact_id}` | artifact 详情，含 `artifact` JSON |
| `PUT` | `/api/v1/compiler/artifacts/{compile_artifact_id}` | 替换并重新校验 artifact |

### 9.4 Runtime / Gateway / Terminal / Replay

| Method | Path | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/gateway/invocations` | 创建 invocation/run/terminal session |
| `GET` | `/api/v1/gateway/invocations` | invocation 列表；支持 `skill_key`、`status` |
| `GET` | `/api/v1/gateway/invocations/{invocation_id}` | invocation 详情 |
| `GET` | `/api/v1/runs` | run 列表；支持 `status`、`skill_id` |
| `GET` | `/api/v1/runs/{run_id}` | run 详情 |
| `POST` | `/api/v1/runs/{run_id}/cancel` | 取消 run；同时取消该 run 下未决工具授权 |
| `GET` | `/api/v1/runs/{run_id}/snapshots` | Session Token snapshots |
| `GET` | `/api/v1/runs/{run_id}/traces` | run traces；支持 `event_type` |
| `GET` | `/api/v1/runs/{run_id}/binding-requirements` | 当前 MVP 固定 terminal input/output requirement |
| `GET` | `/api/v1/runs/{run_id}/bindings` | run bindings |
| `POST` | `/api/v1/runs/{run_id}/bindings/resolve` | 更新/补充 run binding |
| `GET` | `/api/v1/runs/{run_id}/bindings/{binding_id}` | 单个 binding |
| `GET` | `/api/v1/terminal/sessions/{run_id}` | terminal session 摘要 |
| `GET` | `/api/v1/runs/{run_id}/events` | run events；支持 `from_seq`、`to_seq` |
| `POST` | `/api/v1/runs/{run_id}/events` | 追加 JSON/multipart run event |
| `GET` | `/api/v1/runs/{run_id}/events/{event_id}/content` | 事件级对象内容，支持 Range |
| `GET` | `/api/v1/runs/{run_id}/events/{event_id}/parts/{part_id}/content` | part 内容，支持 Range |
| `GET` | `/api/v1/replay/runs` | replay run 列表 |
| `GET` | `/api/v1/replay/runs/{run_id}` | replay detail；包含 provenance 关联 `invocation_id`、`run_id`、`pskill_version_id`、`compile_artifact_id`、`compile_request_id` 和最新 Session Token snapshot |
| `GET` | `/api/v1/replay/traces/{trace_id}` | 按 RunTrace 记录 id 或 OTel trace_id 定位 replay detail 和选中 timeline item |
| `GET` | `/api/v1/runtime/jobs/stats` | job 统计；支持 `window_hours` |
| `GET` | `/api/v1/runtime/jobs` | job 列表；支持状态、类型、关键字、时间和分页 |

### 9.5 Inference

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/gateway/inference/models` | 返回当前配置的 text/multimodal route capability |

当前没有 provider/model/route 的 CRUD API。

### 9.6 Skill Packages

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/skills` | Skill 包列表 |
| `POST` | `/api/v1/skills/sync` | 同步 `skills/psop` 和 `skills/public` 下的 Skill 包 |
| `GET` | `/api/v1/skills/{package_name}` | Skill 包详情 |
| `GET` | `/api/v1/skills/{package_name}/versions` | Skill 包版本列表 |
| `POST` | `/api/v1/skills/{package_name}/versions` | 创建 Skill 包候选版本 |
| `POST` | `/api/v1/skills/{package_name}/versions/{version_id}/validate` | 校验 Skill 包版本 |
| `POST` | `/api/v1/skills/{package_name}/versions/{version_id}/activate` | 激活 Skill 包版本 |

### 9.7 Agent Prompts

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/agent-prompts` | Prompt Pack 列表 |
| `POST` | `/api/v1/agent-prompts` | 创建 Prompt Pack |
| `GET` | `/api/v1/agent-prompts/{definition_id}` | Prompt Pack 详情；支持 `version_id` |
| `POST` | `/api/v1/agent-prompts/{definition_id}/versions` | 创建 draft version |
| `PUT` | `/api/v1/agent-prompts/{definition_id}/versions/{version_id}/files` | 保存 draft files |
| `POST` | `/api/v1/agent-prompts/{definition_id}/versions/{version_id}/validate` | 校验 version |
| `POST` | `/api/v1/agent-prompts/{definition_id}/versions/{version_id}/publish` | 发布 version |
| `POST` | `/api/v1/agent-prompts/{definition_id}/versions/{version_id}/activate` | 激活 usage binding |
| `GET` | `/api/v1/agent-prompt-bindings` | binding 列表 |
| `PUT` | `/api/v1/agent-prompt-bindings/{usage_key}` | 更新 binding |

### 9.8 Skill Tests

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/pskills/{skill_id}/test-scenarios` | 场景列表 |
| `POST` | `/api/v1/pskills/{skill_id}/test-scenarios` | 创建场景 |
| `GET` | `/api/v1/pskills/{skill_id}/test-scenarios/{scenario_id}` | 场景详情 |
| `PATCH` | `/api/v1/pskills/{skill_id}/test-scenarios/{scenario_id}` | 更新场景 |
| `DELETE` | `/api/v1/pskills/{skill_id}/test-scenarios/{scenario_id}` | 归档场景 |
| `POST` | `/api/v1/pskills/{skill_id}/test-scenarios/{scenario_id}/assets` | 上传场景资源 |
| `GET` | `/api/v1/pskills/{skill_id}/test-scenarios/{scenario_id}/assets` | 资源列表 |
| `GET` | `/api/v1/pskills/{skill_id}/test-scenarios/{scenario_id}/assets/{asset_id}/content` | 资源内容 |
| `DELETE` | `/api/v1/pskills/{skill_id}/test-scenarios/{scenario_id}/assets/{asset_id}` | 删除资源引用 |
| `POST` | `/api/v1/pskills/{skill_id}/test-scenarios/{scenario_id}/runs` | 启动场景运行 |
| `GET` | `/api/v1/pskills/{skill_id}/test-scenarios/{scenario_id}/runs` | 场景运行历史 |
| `GET` | `/api/v1/skill-test-scenario-runs/{scenario_run_id}` | 场景运行详情 |
| `POST` | `/api/v1/skill-test-scenario-runs/{scenario_run_id}/cancel` | 取消场景运行 |
| `GET` | `/api/v1/skill-test-scenario-runs/{scenario_run_id}/review` | review |
| `POST` | `/api/v1/skill-test-scenario-runs/{scenario_run_id}/evaluate` | 重新评估 |
| `POST` | `/api/v1/skill-test-scenario-runs/{scenario_run_id}/fork-scenario` | fork 新测试场景 |
| `POST` | `/api/v1/skill-test-scenario-runs/{scenario_run_id}/fork-debug` | fork 调试 invocation |

### 9.9 Agent / Skills / Tools / Memory / Governance

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/agents` | Agent 定义列表 |
| `GET` | `/api/v1/agent-runs` | AgentRun 列表 |
| `GET` | `/api/v1/skills` | Skill 包列表 |
| `GET` | `/api/v1/tools` | ToolPolicy 工具列表 |
| `GET` | `/api/v1/memory` | Memory 列表 |
| `GET` | `/api/v1/memory/{memory_id}` | Memory 详情 |
| `POST` | `/api/v1/memory/search` | Memory 搜索 |
| `POST` | `/api/v1/memory/compactions/queue` | 创建 memory compaction job |
| `PATCH` | `/api/v1/memory/{memory_id}` | 审核或编辑 Memory |
| `GET` | `/api/v1/evaluations` | Run evaluation report 列表 |
| `GET` | `/api/v1/evaluations/findings` | Run evaluation finding 列表 |
| `GET` | `/api/v1/governance/proposals` | 治理提案列表 |
| `GET` | `/api/v1/tool-authorizations` | AgentRun 工具授权列表 |

## 10. WebSocket

当前注册两个等价路径：

- `/ws/runs/{run_id}`
- `/api/v1/ws/runs/{run_id}`

行为：

- 连接后发送 `ws.connected`。
- 服务端不接收业务输入；循环中只等待客户端消息以维持连接。
- REST 成功追加 run event 后，当前进程内 hub 广播 `terminal.event.appended`。
- REST 触发 Runtime Kernel 推进并新增 run trace 后，当前进程内 hub 广播 `trace.event.appended`。
- REST 触发 Runtime Kernel 推进并新增 session token snapshot 后，当前进程内 hub 广播 `session_token.snapshot.appended`。
- REST 触发 Runtime Kernel 改变 run 元数据后，当前进程内 hub 广播 `run.updated`。
- REST 解析或更新 run capability binding 后，当前进程内 hub 广播 `binding.updated`。
- WebSocket 不是状态源；客户端必须通过 REST 补齐缺失事件。

## 11. 可观测

当前 OTel 由 `configure_observability()` 控制：

- FastAPI instrumentation。
- HTTPX instrumentation。
- SQLAlchemy instrumentation。
- Logging instrumentation。
- 可选 OTLP HTTP exporter 或 console exporter。

显式 span 名称包括：

- `publish.source_freeze`
- `compile.source_load`
- `compile.manifest_check`
- `compile.agent`
- `compile.agent.invoke`
- `compile.validate`
- `compile.emit`
- `job.compile`
- `job.claim`
- `job.process`
- `runtime.loop`
- `runtime.actor`
- `gateway.gitlab`
- `gateway.inference`

运行时 trace 事件写入数据库 `run_trace`，Replay 直接读取持久化事件，不推断未落库状态。Replay detail 中的 `provenance` 直接串联 `invocation_id / run_id / pskill_version_id / compile_artifact_id / compile_request_id / latest_session_token_snapshot_id`。Runtime 关键 span（`runtime.loop`、`runtime.actor`、`gateway.inference`）写入同一组 provenance 属性，并额外包含当前输入 `session_token_id / session_token_seq`。`/api/v1/replay/traces/{trace_id}` 优先按 RunTrace 记录 id 查询，未命中时按 `run_trace.trace_id` 反查最近一条 OTel 关联事件，用于从 OTel 排障上下文跳转到 Replay。

`/api/v1/observability/metrics` 返回窗口内 Runtime、Agent、Evaluation、Governance 与 OpenTelemetry 状态聚合：

- Runtime：run、run event、run trace 数量和分布。
- Agent：AgentRun、AgentEvent、ModelCall、ToolCall、SkillActivation、ToolAuthorization 数量和分布。
- Evaluation：RunEvaluation、RunEvaluationFinding、quality score、outcome、finding status/category/severity 分布。
- Governance：Proposal、Experiment、source run/evaluation/finding 关联数量和状态/type 分布。

## 12. 当前实现缺口

下列条目是当前代码缺口，不应在接口文档中作为已实现能力使用：

- 无 Alembic migration；需要生产化时补迁移系统。
- 无独立 scheduler；过期 lease 已由内置 worker 恢复，dead-letter 已作为耗尽重试后的失败去向，独立 scheduler 仍未完整落地。
- 无 `worker_heartbeat`、`sandbox_lease`、`mcp_server`、`mcp_tool`、`inference_provider`、`model_catalog`、`gateway_policy`、`capability_binding`、`runtime_config`、`operation_log` 表。
- 无 MCP Gateway REST API。
- 无独立 CapabilityHost 类；当前由 RuntimeService 内聚执行。
- 无 sandbox 执行。
- 无 `/api/v1/runtime/workers` 或 `/api/v1/runtime/sandboxes`。
- 无 FastAPI 静态文件挂载；Web 控制台由独立静态宿主运行。
