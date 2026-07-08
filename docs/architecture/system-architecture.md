# PSOP 系统架构设计

版本：2026-06  
状态：项目架构基线  
适用范围：PSOP 后端、前端、Runtime、Agent Harness 与后续迭代

## 1. 文档边界

本文是 PSOP 的唯一系统架构设计基线，合并原概要设计、服务端详细设计、前端详细设计与 Agent Harness 设计。

本文负责定义：

- 当前已落地的系统边界。
- 核心对象与形式抽象。
- 后端、前端、Runtime、测试、任务、观测模块边界。
- Agent Harness 的目标架构和接入方式。
- 后续里程碑与迁移路径。

不在本文展开的专题：

- PSOP-EG 形式语义：见 `execution-graph-formal-v5.md`。
- 产品愿景与项目纲领：见 `../overview/vision.md`。
- Agent 协作规则：见 `../engineering/agent-rules.md`。

## 2. 架构总则

PSOP 采用确定性 Runtime 与受治理 Agent Harness 结合的架构。

核心约束：

1. `PSOP Skill` 是现实物理世界技能本体，不是 prompt。
2. `PSOP-EG` 是 formal-v5 执行图，是 runner 的正式输入。
3. `Session Token` 是真实运行实例的一等状态对象。
4. `RuntimeService` 是 `psop-runner-agent` 的正式治理环境。
5. `terminal_event` 是终端输入输出的 append-only 事实源。
6. `trace_event`、`session_token_snapshot`、`terminal_event`、`artifact_object` 是 Replay、Audit、Eval 的事实基础。
7. Runtime LLM / evidence evaluation 节点通过 `psop.runner` 进入 Agent Harness；compiler、skill test judge、素材分析等非 Runtime Runner 域服务可继续使用 `LlmInferenceGateway`。
8. Agent Harness 负责 builder、compiler、tester、audit、eval 的统一定义、运行、tools、MCP、Agent Skills、memory、sandbox、事件与产物治理。
9. Runtime 推进必须由 `runtime_job` worker 执行；router 不直接执行长耗时 Runtime、LLM 或 agent 调用。

## 3. 当前系统基线

当前代码主链路：

```text
Skills -> Publish -> Auto Compile -> Invocation -> Runtime -> Replay / Observability
```

已落地模块：

```text
backend/app/
  app.py                 FastAPI factory、lifespan、CORS、异常处理、OTel、worker 启动
  main.py                uvicorn 入口
  api/
    router.py            /api/v1 聚合路由
    dependencies.py      Settings/DB/Gateway/Service 注入
    routes/              system/skills/compiler/runtime/skill_tests/agent_prompts/inference
  core/
    config.py            PSOP_* 配置
    logging.py           日志与上下文
    observability.py     OTel 配置与 helper
  domain/
    skills/              Skill、Git-backed source、发布、素材、素材生成
    compiler/            编译请求、formal-v5 校验、EG artifact
    runtime/             Invocation、Run、Session Token、Terminal、Replay
    jobs/                DB-backed runtime_job 队列、worker、任务 read model
    skill_tests/         黑盒时序测试、timeline driver、semantic judge、fork
    agent_prompts/       Prompt Pack 定义、版本、binding
  gateway/
    gitlab.py            GitLab API adapter
    inference.py         OpenAI-compatible LLM adapter
    asr.py               ASR HTTP adapter
  infra/
    database.py          SQLAlchemy engine/session/Base
    object_store.py      S3-compatible object adapter
```

当前前端基线：

```text
static/
  index.html             Alpine App Shell
  css/                   Tailwind 编译产物与字体样式
  js/app.js              全局 helper、路由、初始状态
  js/app/*.js            页面域方法：skills、compiler、runtime、skill-test、tasks、agent-prompts
  pages/*.html           页面片段
  scripts/               CSS 构建与静态 dev server
```

当前前端一级菜单：

| 菜单 | 路由 | 对象 |
| --- | --- | --- |
| `Skills` | `/admin/skills` | Skill 生命周期 |
| `智能体` | `/admin/agent-prompts` | Prompt Pack 管理 |
| `任务` | `/admin/tasks` | Runtime jobs |

编译、运行、测试、Replay 通过 Skill 详情、列表动作或 deep link 进入。

## 4. 核心对象

### 4.1 PSOP Skill

`PSOP Skill` 是现实物理世界技能本体。它既包括用户正在编辑的草稿形态，也包括 Git-backed source、结构化 manifest snapshot、版本化发布记录和运行策略快照。

当前实现对象：

```text
SkillDefinition
SkillVersion
SkillPublishRecord
SkillRawMaterial
SkillRawMaterialAnalysis
SkillRawMaterialDerivedAsset
SkillRawMaterialGeneration
```

当前源码与结构化表达：

```text
README.md
SKILL.md
skill.yaml
manifest_snapshot
runtime_policy_snapshot
raw_material_analysis
```

职责：

- 管理 Skill 元数据。
- 管理 Git-backed source。
- 管理 draft / published 版本。
- 管理 manifest snapshot 与 runtime policy snapshot。
- 管理 raw material、分析结果与生成链路。
- 发布时冻结 source commit 并创建 compile request。

`psop-builder` 生成或更新 PSOP Skill draft。`psop-compiler` 消费 PSOP Skill 并生成 PSOP-EG。

### 4.2 PSOP-EG formal-v5

当前实现对象：

```text
SkillCompileRequest
CompileDiagnostic
ArtifactObject
EgCompileArtifact
```

PSOP-EG 是 formal-v5 执行图，负责定义：

```text
nodes
actors
guards
merge rules
halt conditions
runtime policies
capability summary
view graph summary
```

当前 MVP 支持：

| 类别 | 支持项 |
| --- | --- |
| node kind | `start`、`input`、`llm`、`tool`、`terminal` |
| actor | `runtime.start`、`runtime.input`、`agent.llm`、`capability.demo_tool`、`runtime.terminal` |
| tool | `psop.demo.inspect_input` |
| guard DSL | `always`、`phase_is`、`field_exists`、`field_equals`、`all`、`any`、`not` |
| merge DSL | `op=set` |

### 4.3 Runtime Run

当前实现对象：

```text
SkillInvocation
Run
TerminalSession
RunCapabilityBinding
SessionTokenSnapshot
TraceEvent
TerminalEvent
TerminalEventPart
```

一次 invocation 创建一个逻辑 run。run 不是 OS 进程，而是可持久化、可回放、可审计的执行实例。

`POST /gateway/invocations` 的职责是创建 `SkillInvocation`、`Run`、`TerminalSession`、默认 binding 和初始 Session Token。Invocation 不再隐式生成首条终端提示，也不把 `input_envelope` 转换为 terminal event。新建 Run 进入可输入绑定态：

```text
Run.status = waiting_input
Run.runtime_phase = terminal_bound
Run.latest_terminal_seq = 0
TerminalSession.status = open
terminal_event = []
```

`input_envelope` 保留为兼容字段，不再作为运行时事实源。正式用户输入只能来自 `terminal_event`。

### 4.4 Session Token

Session Token 是运行实例的正式状态对象。工程实现为 `session_token_snapshot.token_payload`。

典型字段：

```json
{
  "phase": "start",
  "input_envelope": {},
  "observations": {},
  "budgets": {"llm_calls": 0, "tool_calls": 0},
  "outputs": {},
  "control": {
    "wait": {
      "checkpoint_id": "...",
      "workflow_step_id": "...",
      "input_window": {"accept_after_seq": 0, "policy": "checkpoint_scoped"}
    },
    "terminal_consumption": []
  },
  "metadata": {"artifact_version": "...", "terminal_cursor": 0},
  "terminal": {"events": [], "latest_seq": 0},
  "facts": {},
  "registers": {},
  "memory": {},
  "trace": [],
  "status": "running"
}
```

RuntimeService 只能通过受控 merge 与 snapshot 追加推进 Session Token。

### 4.5 Terminal Facts

`terminal_event` 是终端输入输出的 append-only 事实源。

`terminal_event_part` 表示输入事件中的多模态 part，例如文本、图片、音频、视频或文件。非文本内容通过 `artifact_object` 关联对象存储。

Runtime 同步所有 terminal facts，但 terminal input 只有在符合当前 wait checkpoint 的 `input_window` 且未出现在 `control.terminal_consumption` 时，才能进入 `wait.evidence` / `latest_evidence`。同一条 input 不能跨 checkpoint 或 workflow step 被重复当作 evidence。

`control.terminal_consumption` 只回答“某条 input 是否已经被当前 checkpoint 消费”，不回答“它满足了哪一项证据要求”。多证据 checkpoint 的验收进度由 `control.evidence_progress` 记录：Runtime 根据 `runtime_contract.expected_evidence[workflow_step_id]` 初始化证据项，并在每次 runner observation 返回后合并 `evidence_assessment.requirement_results`。已 `accepted` 的证据项是当前 checkpoint 的正式进度，后续 runner 不应要求用户重复提交；只有同一 `requirement_key` 被明确标记为 `rejected` 时，才会把该证据项改为不通过。

### 4.6 Agent Harness Objects

新增对象：

| 对象 | 定义 |
| --- | --- |
| `AgentDefinition` | 智能体声明式定义。 |
| `AgentSkill` | 智能体按需加载的方法、模板、规则和脚本。 |
| `AgentRun` | 一次智能体运行。 |
| `AgentEvent` | 智能体内部事件。 |
| `AgentArtifact` | 智能体输入输出产物。 |
| `AgentMemoryItem` | 后续长期记忆项。 |

Agent Harness 对象不替代 Runtime 对象，而是补充 builder、compiler、tester、audit、eval 的事实链。

## 5. 总体架构

```mermaid
flowchart TB
    subgraph UI[Static Web Console]
        Web[Alpine App Shell]
        SkillsUI[Skills]
        PromptsUI[Agent Prompts]
        TasksUI[Tasks]
        RunUI[Run Live / Replay]
        AgentUI[Agent Runs / Artifacts]
    end

    subgraph API[FastAPI]
        Router[api/router.py]
        Deps[api/dependencies.py]
        Routes[routes/*]
    end

    subgraph Harness[Agent Harness]
        Def[AgentDefinition]
        AgentService[AgentHarnessService]
        AgentSkills[Agent Skills]
        ToolRegistry[Tool Registry]
        Workspace[Workspace / Shell]
        Mcp[MCP Adapter]
        AgentFacts[AgentRun/Event/Artifact]
    end

    subgraph Domain[Core Domain]
        Skills[skills]
        Compiler[compiler]
        Runtime[runtime]
        Tests[skill_tests]
        Jobs[jobs]
        Prompts[agent_prompts]
    end

    subgraph Gateway[Gateways]
        GitLab[GitLab]
        LLM[LlmInferenceGateway / LangChain Model Factory]
        ASR[ASR]
    end

    subgraph Infra[Infra]
        DB[(Database)]
        S3[(Object Store)]
        OTel[OpenTelemetry]
    end

    UI --> API
    API --> Domain
    API --> Harness
    Harness --> Domain
    Harness --> Gateway
    Domain --> Gateway
    Domain --> Infra
    Harness --> Infra
```

## 6. Runtime 架构

### 6.1 RuntimeService 职责

`RuntimeService` 是 `psop-runner-agent` 的治理环境。

职责：

- 创建 invocation、run、terminal session、默认 binding、初始 snapshot。
- 读取 compile artifact。
- 从最新 Session Token snapshot 恢复状态。
- 同步 terminal events。
- 计算 enabled nodes。
- 选择节点。
- 执行 actor。
- 合并 observation。
- 根据 EG transition 解析下一 Runtime phase。
- 追加 snapshot 和 trace。
- 追加 terminal output。
- 处理 wait、success、aborted、failed、cancelled。
- 构建 replay detail。

RuntimeService 的同步边界：

- `create_invocation()` 只创建绑定和初始 token，不调用 `process_run()`。
- `append_terminal_event()` 只追加 terminal fact、调度 `job:runtime:{run_id}`，默认不调用 `process_run()`。
- `process_run()` 是 Runtime Kernel 推进入口，只由 worker 或显式测试调用。
- router 不直接执行 Runtime loop、LLM 或 Agent Harness 调用。
- worker 推进 Runtime 时可以在节点级 commit/publish；router 仍不得执行长耗时 Runtime。

### 6.2 Runtime Loop

```text
worker claims job:runtime:{run_id}
load latest snapshot
sync terminal events
evaluate enabled nodes
select node
execute actor
merge observation
resolve EG transition
apply terminal interaction
append snapshot
append trace
halt / wait / continue
```

RuntimeService 拥有节点推进权。`psop.runner` 或其它模型 actor 只能提交当前节点 observation，例如“证据足够”“证据不足”“重试”“中止”或“完成”；下一 Runtime phase 必须由 RuntimeService 根据当前节点的 `interaction.transitions` 解析。旧 EG 没有显式 transitions 时，RuntimeService 可以兼容读取 `dependency_graph_for_view` 中当前节点的真实可达边。模型输出中的 `next_phase` 只保留为兼容字段和诊断信息，不是状态主权来源。

Runtime 写终端输出前必须先完成 transition 校验。对于 evaluation 节点，如果 `continue` / `complete` / `abort` 等结果无法解析出合法下一 phase，Runtime 进入 recoverable failure，并且不得先输出“已进入下一阶段”等成功消息。

终局用户可见输出只有一个所有者：`terminal` 节点。`final_verify` 等 evaluation 节点可以生成 `terminal_message` / `final_response` 并写入 `outputs.final_response`，但当其 `complete` 或 `abort` transition 进入 terminal 类节点时，Runtime 不从该 evaluation 节点追加 terminal output，避免最终完成或中止消息重复发送。

每次进入 wait checkpoint 时，RuntimeService 在 `control.wait.input_window` 记录 `accept_after_seq` 和 `policy=checkpoint_scoped`。首个 checkpoint 可以从本轮 Runtime 推进前的 terminal cursor 之后消费输入，以兼容“用户先发输入、worker 后推进”的场景；非首个 checkpoint 的 `accept_after_seq` 必须锚定到 checkpoint 创建时 token 已同步的最新 terminal seq，防止同轮触发指令节点的 input 被新 checkpoint 立即复用为 evidence。当前 checkpoint 只消费 `seq_no > accept_after_seq` 且未出现在 `control.terminal_consumption` 的 input event；消费后写入账本，记录 `seq_no`、`event_id`、`checkpoint_id`、`workflow_step_id` 和 `consumed_at`。旧 snapshot 没有窗口或账本时按空值兼容，并在首次恢复时补齐。

如果 terminal input 在首次 Runtime 推进前已经入库，RuntimeService 会先同步该 input，再从初始 EG 节点推进；首个 wait checkpoint 可以消费这类尚未入账的 input，避免首个终端输入被初始提示吞掉。进入后续 checkpoint 后，历史 input 默认不能跨 checkpoint 自动复用。

worker 在每个节点执行完 `_append_runtime_step()` 后提交本节点新增 snapshot、trace 和 terminal output。若节点未进入 wait/success/failure，提交并发布后继续下一节点；若节点进入 wait/success/failure，先设置 Run/Job 状态，再提交并发布。REST 仍是权威恢复路径，WebSocket 只发布增量提示。

### 6.3 Runner 与 Agent Harness 的关系

`psop-runner` 是特殊智能体：

```text
runner_kind: psop_runtime
implementation: RuntimeService
state_authority: SessionTokenSnapshot
```

LangChain Agent / LangGraph 不接管 runner 的正式状态。后续 Runtime 中的 LLM 节点可以通过 Agent Harness 执行，但输出仍以 observation 形式回到 RuntimeService merge。

Runner observation 中的 `next_phase` 不拥有推进权。RuntimeService 会记录模型的原始建议用于排查，但正式 phase 只能来自 EG transition 解析结果。

## 7. Agent Harness 架构

### 7.1 目标

Agent Harness 为以下智能体提供统一底座：

```text
psop-builder
psop-compiler
psop-tester
psop-audit
psop-eval
```

顶层只暴露一个：

```text
AgentHarnessService
```

内部策略：

- LangChain `create_agent` first。
- LangGraph 作为 LangChain agent 底层能力，不作为业务层 runner 分类。
- Skills-first，Subagents-later。
- dev_open profile 优先跑通闭环。
- Runtime LLM / evidence evaluation 节点通过 `psop.runner` 使用 Agent Harness；compiler、skill test judge、素材分析等非 Runtime Runner 域服务可继续使用 `LlmInferenceGateway`。

### 7.2 模块目录

```text
backend/app/agent_harness/
  schemas.py
  service.py
  events.py
  errors.py

  runners/
    langchain_agent_executor.py

  models/
    factory.py
    scripted_chat_model.py

  middlewares/
    dangling_tool_call.py
    model_events.py
    token_usage.py
    tool_calls.py

  sandbox/
    base.py
    provider.py
    local.py

  skills/
    loader.py
    manifest.py

  tools/
    base.py
    registry.py
    workspace_tools.py
    mcp_tools.py
    psop_skill_tools.py
    psop_compiler_tools.py
    psop_runtime_tools.py
    psop_test_tools.py
    psop_audit_tools.py
    psop_eval_tools.py

  memory/
    scopes.py
    store.py
    workspace_memory.py

  persistence/
    models.py
    repository.py
    service.py

  agents/
    context.py
    factory.py
    registry.py
    builder/agent.py
    builder/prompt.py
    builder/agent.yaml
    compiler/agent.py
    compiler/prompt.py
    compiler/agent.yaml
    tester/agent.py
    tester/prompt.py
    tester/agent.yaml
    audit/agent.py
    audit/prompt.py
    audit/agent.yaml
    eval/agent.py
    eval/prompt.py
    eval/agent.yaml
```

Agent Skill 源统一放在仓库根目录 `skills/`。`backend/app/agent_harness/skills/` 只包含加载、解析和治理代码，不存放具体 Skill 内容。

### 7.3 AgentDefinition

`psop.builder` 的详细职责、工具、Agent Skills、输入输出、校验和审计约束见 [PSOP Builder Agent 详细设计](psop-builder-agent-design.md)。`psop.compiler` 的详细职责、工具、Agent Skills、formal-v5 校验、输入输出和审计约束见 [PSOP Compiler Agent 详细设计](psop-compiler-agent-design.md)。`psop.runner` 的详细职责、RuntimeService 接入方式、终端协作、证据评估、参考图片输出和 observation 契约见 [PSOP Runner Agent 详细设计](psop-runner-agent-design.md)。

```yaml
agent_key: psop.builder
version: v1
runner_kind: langchain_agent
factory: make_builder_agent
profile: dev_open
purpose: Build PSOP Skill draft candidates from raw materials and standards.
model:
  name: default
  thinking_enabled: false
skills:
  - psop-builder
tools:
  - workspace.read_text
  - workspace.write_text
  - workspace.list
  - psop.builder.read_current_source
  - psop.builder.list_materials
  - psop.builder.read_material_analysis
  - psop.builder.list_reference_assets
  - psop.standard.search
  - psop.builder.submit_candidate
mcp:
  enabled: false
  servers: []
memory_scope: psop.builder
input_schema_ref: psop.builder.input.v1
output_schema_ref: psop.builder.output.v1
```

### 7.4 AgentHarnessService

调用流程：

```text
resolve AgentDefinition
create AgentRun
prepare sandbox
load Agent Skill metadata
call agent factory
resolve tools, middleware, model and prompt
create LangChain agent
invoke agent
record AgentEvents
persist AgentArtifacts
return AgentResult
```

### 7.5 Tool Registry

工具命名空间：

| 命名空间 | 职责 |
| --- | --- |
| `workspace.*` | sandbox 虚拟 workspace 文件读写、目录、grep、glob。 |
| `outputs.*` | sandbox 虚拟 outputs 产物写入与登记。 |
| `shell.*` | 后续可选能力；首版不开放 shell/bash。 |
| `mcp.*` | MCP tools adapter。 |
| `psop.raw_material.*` | 素材、关键帧、ASR/OCR、分析结果读取。 |
| `psop.skill.*` | PSOP Skill source / draft 读写。 |
| `psop.compiler.*` | formal-v5 validator、artifact writer、diagnostics。 |
| `psop.runtime.*` | invocation、terminal event、replay、trace 读取。 |
| `psop.test.*` | test scenario、timeline、judge、coverage。 |
| `psop.audit.*` | replay facts、quality attribution artifact。 |
| `psop.eval.*` | proposal、patch draft、test plan。 |

首版默认只开启 local sandbox 和内置工具，工具只暴露 `/mnt/psop/workspace`、`/mnt/psop/outputs` 虚拟路径；MCP 保留 skeleton，不默认连接真实 server；首版不开放 shell/bash。所有工具调用通过 middleware 记录 `agent_event`。

### 7.6 Agent Skills

Agent Skill 目录结构：

```text
skills/<skill-name>/
  SKILL.md
  schemas/
  templates/
  scripts/
  examples/
  references/
```

用途：

- 领域方法。
- 输出模板。
- 编译规则说明。
- 测试生成策略。
- 审计归因准则。
- Eval patch 策略。

Agent Skill 与 PSOP Skill 是不同对象。

### 7.7 Memory

首版：

- sandbox workspace memory：agent run 内文件和中间产物。
- artifact memory：历史 audit/test/eval artifact 摘要。

后续：

- semantic memory：行业标准、设备知识、企业规范。
- episodic memory：历史失败、历史决策、历史修复。
- procedural memory：prompt、rubric、compiler rule、testing strategy。

## 8. 智能体设计

| 智能体 | runner_kind | 输入 | 输出 | 首版工具 |
| --- | --- | --- | --- | --- |
| `psop-builder` | `langchain_agent` | raw material、keyframes、transcript、LightRAG standards、user goal | PSOP Skill draft candidate、evidence map、standard usage、missing questions、safety constraints | raw_material read、reference assets read、standard search、builder candidate、workspace |
| `psop-compiler` | `langchain_agent` | PSOP Skill、manifest、domain pack、allowed runtime | PSOP-EG、compile diagnostics、summary | skill read、formal-v5 validate、artifact write、workspace |
| `psop-tester` | `langchain_agent` | PSOP Skill、PSOP-EG、world model | test suite、scenario runs、coverage、feedback | test scenario、runtime invocation、terminal event、replay、judge |
| `psop-runner` | `psop_runtime` | invocation、PSOP-EG、terminal events | Run Package、Replay、final output | RuntimeService 内置 actor/tool |
| `psop-audit` | `langchain_agent` | replay、trace、terminal events、PSOP Skill、EG、test report | audit report、quality attribution、evidence refs | replay read、trace read、skill/EG read、workspace |
| `psop-eval` | `langchain_agent` | audit reports、test reports、diagnostics、prompt/code history | improvement proposal、patch draft、test plan、release checklist | audit/test read、prompt draft、skill patch、workspace、MCP skeleton |

## 9. 数据模型

### 9.1 现有核心表

| 领域 | 表 |
| --- | --- |
| Skills | `skill_definition`、`skill_version`、`skill_publish_record`、`skill_raw_material`、`skill_raw_material_analysis`、`skill_raw_material_derived_asset`、`skill_raw_material_generation` |
| Compiler | `skill_compile_request`、`compile_diagnostic`、`artifact_object`、`eg_compile_artifact` |
| Runtime | `skill_invocation`、`run`、`session_token_snapshot`、`trace_event`、`terminal_session`、`run_capability_binding`、`terminal_event`、`terminal_event_part` |
| Jobs | `runtime_job` |
| Skill Tests | `skill_test_scenario`、`skill_test_asset`、`skill_test_scenario_run`、`skill_test_expectation_evaluation` |
| Prompts | `agent_prompt_definition`、`agent_prompt_version`、`agent_prompt_binding` |

### 9.2 新增 Agent Harness 表

#### agent_run

| 字段 | 说明 |
| --- | --- |
| `id` | Agent Run ID |
| `agent_key` | 智能体 key |
| `agent_version` | 智能体版本 |
| `runner_kind` | `langchain_agent` / `psop_runtime` |
| `profile` | `dev_open` / `prod_guarded` |
| `status` | pending / running / succeeded / failed / cancelled |
| `parent_agent_run_id` | 父 Agent Run |
| `related_skill_definition_id` | 关联 Skill |
| `related_skill_version_id` | 关联 Skill Version |
| `related_compile_request_id` | 关联 Compile Request |
| `related_runtime_run_id` | 关联 Runtime Run |
| `input_payload` | 输入摘要 |
| `output_payload` | 输出摘要 |
| `sandbox_path` | sandbox 根路径 |
| `model_provider` | 模型 provider |
| `model_name` | 模型名 |
| `token_usage` | token usage |
| `error_message` | 错误 |
| `started_at` / `finished_at` | 运行时间 |

#### agent_event

| 字段 | 说明 |
| --- | --- |
| `id` | Event ID |
| `agent_run_id` | Agent Run |
| `seq_no` | 递增序号 |
| `event_type` | 事件类型 |
| `payload` | 事件内容 |
| `trace_event_id` | 可选映射到 Runtime trace |
| `occurred_at` | 发生时间 |

#### agent_artifact

| 字段 | 说明 |
| --- | --- |
| `id` | Artifact ID |
| `agent_run_id` | Agent Run |
| `artifact_type` | skill_draft / eg_candidate / test_report / audit_report / proposal 等 |
| `artifact_object_id` | 对应 artifact_object |
| `inline_content` | 小型 JSON 产物 |
| `content_hash` | 内容哈希 |
| `provenance` | 来源、输入、工具、事件引用 |
| `status` | draft / ready / superseded |

#### agent_memory_item（后续）

| 字段 | 说明 |
| --- | --- |
| `scope_type` | domain / skill / agent / system |
| `scope_id` | scope 标识 |
| `memory_kind` | semantic / episodic / procedural |
| `content` | 记忆内容 |
| `metadata` | 元数据 |
| `source_artifact_id` | 来源 artifact |
| `status` | active / archived |

## 10. API 架构

### 10.1 现有 API 分组

| 分组 | 路径前缀 | 职责 |
| --- | --- | --- |
| System | `/api/v1/system` | 服务信息与健康检查 |
| Skills | `/api/v1/skills` | Skill 生命周期、source、repository、raw materials、publish |
| Compiler | `/api/v1/compiler` | compile request、progress、artifact |
| Runtime Gateway | `/api/v1/gateway/invocations` | 创建 invocation |
| Runtime | `/api/v1/runs`、`/api/v1/terminal`、`/api/v1/replay` | run、snapshot、trace、terminal、replay |
| Skill Tests | `/api/v1/skills/{skill_id}/test-scenarios`、`/api/v1/skill-test-scenario-runs` | 测试场景和测试运行 |
| Agent Prompts | `/api/v1/agent-prompts`、`/api/v1/agent-prompt-bindings` | Prompt Pack 管理 |
| Inference | `/api/v1/gateway/inference/models` | 模型能力 |
| Jobs | `/api/v1/runtime/jobs` | runtime_job read model |

### 10.2 新增 Agent API

首版：

```text
POST /api/v1/agents/{agent_key}/runs
GET  /api/v1/agents/runs/{agent_run_id}
GET  /api/v1/agents/runs/{agent_run_id}/events
GET  /api/v1/agents/runs/{agent_run_id}/artifacts
```

后续：

```text
GET  /api/v1/agents/definitions
GET  /api/v1/agents/definitions/{agent_key}
POST /api/v1/agents/definitions/{agent_key}/versions
POST /api/v1/agents/runs/{agent_run_id}/cancel
POST /api/v1/agents/approvals/{approval_id}/approve
```

## 11. Job 与执行调度

当前系统使用 `runtime_job` 表作为 DB-backed 任务队列。

现有 job type：

```text
compile
runtime
skill_test_timeline_driver
raw_material_analysis
skill_raw_material_generation
```

新增首版 job type：

```text
agent_run
```

payload：

```json
{
  "agent_run_id": "...",
  "agent_key": "psop-builder"
}
```

后续如需要队列隔离，再拆分：

```text
agent_builder
agent_compiler
agent_tester
agent_audit
agent_eval
```

`runtime` job 使用单 Run 单 job 的 dedupe 模型：

```text
dedupe_key = job:runtime:{run_id}
payload.run_id = run_id
```

终端输入到来时：

- job 不存在或非 `running`：置为 `pending`，`available_at=now`，清空 lease/error，并把本轮 `attempt_no` 重置为 `0`。
- job 已是 `running`：不抢占，不改 running 状态，只在 `payload.rerun_requested=true` 标记需要再跑一轮。
- `process_run()` 在提交 wait/success/failure 前检查是否仍有未同步 input terminal events；如有，把同一 job 重新置为 `pending`，防止运行中新增输入丢失。

worker 是 Runtime 推进唯一生产路径。测试可以显式调用 `process_run()`，但 router、terminal event API 和 invocation API 不直接推进 Runtime。

## 12. Sandbox 与工具执行

每个 AgentRun 创建独立 local sandbox：

```text
.psop/agent-runs/{agent_run_id}/
```

目录：

```text
input.json
output.json
events.jsonl
memory.json
workspace/
outputs/
```

`dev_open` 规则：

- 默认允许工具读写 `/mnt/psop/workspace` 与 `/mnt/psop/outputs` 对应的 sandbox 内目录。
- 首版 local sandbox 不是安全隔离边界，不开放 shell/bash。
- 输入材料通过 `input.json` 或后续 provider 挂载进入 sandbox。
- 输出产物写入 `workspace/` 或 `outputs/`。
- 禁止默认写项目根目录、`.env`、数据库文件、对象存储根路径。
- 禁止 host 绝对路径、`..` 和越界 symlink。
- 重要输出登记为 `agent_artifact`。

## 13. Observability 与 Replay

### 13.1 Runtime 事件

Runtime 事件继续写入：

```text
trace_event
terminal_event
session_token_snapshot
```

RuntimeService 在 commit 后通过轻量 event sink 发布本次新增 `terminal_event` 和 `trace_event`。worker 产生的 output/trace 按节点级 commit 增量发布；REST 追加 input 后也会在 commit 后发布已接受的 input event。FastAPI lifespan 内的 broadcaster 把这些事件转发到 WebSocket hub。WebSocket 只提供增量提示，消息包括：

```text
terminal.event.appended
trace.event.appended
```

REST 读取 Run、TerminalEvent、TraceEvent、Snapshot 和 Replay 仍是断线恢复与权威补齐路径。

### 13.2 Agent 事件

建议事件类型：

```text
agent.started
agent.completed
agent.failed
agent.model.requested
agent.model.completed
agent.model.failed
agent.tool.started
agent.tool.completed
agent.tool.failed
agent.file.read
agent.file.written
agent.shell.started
agent.shell.completed
agent.mcp.tool_started
agent.mcp.tool_completed
agent.artifact.created
agent.memory.read
agent.memory.written
```

### 13.3 Replay 扩展

后续 Replay 可以展示：

```text
Run timeline
Agent timeline
Tool timeline
Artifact lineage
Audit attribution
Eval proposal
```

## 14. 前端演进

当前前端保持静态 Alpine 控制台。

新增 Agent Harness 后，前端应增加：

| 页面 | 说明 |
| --- | --- |
| Agent Runs | 查看 AgentRun 列表、状态、输入输出摘要。 |
| Agent Run Detail | 查看 AgentEvent timeline、sandbox artifact、错误信息。 |
| Build Workspace | 触发 builder，查看 PSOP Skill draft、evidence map、missing questions。 |
| Test Feedback | 查看 tester 生成的测试、执行结果和覆盖度。 |
| Audit Reports | 查看质量归因。 |
| Eval Proposals | 查看改进提案和 patch draft。 |

首版可复用现有 `任务` 和 Skill 详情页，不强制新增完整一级导航。

## 15. Milestone 计划

### Milestone 1：Agent Harness MVP + Build/Compile/Test 闭环

范围：

- `backend/app/agent_harness/` 基础模块。
- LangChain `create_agent` based agent factory and executor。
- LangChain model factory。
- AgentDefinition YAML loader。
- AgentRun / AgentEvent / AgentArtifact 持久化。
- Agent Skills loader。
- sandbox file tools。
- MCP adapter skeleton。
- `psop-builder` MVP。
- `psop-compiler` MVP。
- `psop-tester` MVP。
- 调用现有 `psop-runner` 执行测试。

验收链路：

```text
raw material summary + standard snippets
  -> PSOP Skill draft
  -> PSOP-EG
  -> generated positive/negative tests
  -> runner execution
  -> tester feedback
```

### Milestone 2：Audit + Eval 闭环

范围：

- `psop-audit` MVP。
- audit report schema。
- `psop-eval` MVP。
- improvement proposal schema。
- sandbox patch draft。
- 测试运行能力。

验收链路：

```text
run replay/test report
  -> audit attribution
  -> eval improvement proposal
  -> prompt/skill/test/code patch draft
```

### Milestone 3：生产治理强化

范围：

- `prod_guarded` profile。
- MCP trust registry。
- tool allowlist / denylist。
- human approval。
- sandbox hardening。
- long-term memory。
- release gate。
- 自动 PR / staged release。

## 16. 迁移策略

### 16.1 文档迁移

项目文档结构以 `docs/README.md` 为准；本文只维护系统架构基线，不再重复定义文档目录治理规则。

### 16.2 代码迁移

优先顺序：

1. 新增 Agent Harness 基础模块。
2. 接入 AgentRun / AgentEvent / AgentArtifact。
3. 新增 sandbox file tools 与 MCP adapter skeleton。
4. 迁移 compiler 到 `psop-compiler` agent，保留 formal-v5 validator 和现有 artifact 写入。
5. 实现 builder。
6. 实现 tester，并调用现有 RuntimeService 执行测试。
7. 实现 audit。
8. 实现 eval。
9. 强化 production governance。

## 17. 非目标

当前阶段不实现：

- 完整租户、用户、权限、审批流。
- 生产级 MCP security scanner。
- 自动 merge / deploy。
- 大规模 subagent 自治协作。
- 用 Agent Harness 替换 RuntimeService。
- 将 Agent Skill 与 PSOP Skill 合并为同一对象。

## 18. 架构结论

PSOP 的系统架构基线是：

```text
formal-v5 PSOP-EG + Session Token Runtime + Agent Harness Governance
```

其中：

- PSOP-EG 提供确定性执行骨架。
- Session Token Runtime 提供真实运行状态主权。
- Agent Harness 提供构建、编译、测试、审计、评估和改进的统一智能体底座。

后续工程迭代必须保持这三层边界清晰。
