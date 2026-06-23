# PR 1：Agent Harness MVP 基础底座实施方案

状态：实施计划  
目标 PR：`feat(agent-harness): introduce DeepAgents-based harness and demo agent run`  
适用范围：Milestone 1 的第一个代码 PR

## 1. PR 目标

PR 1 的目标是在 PSOP 后端引入 `langchain`、`langgraph`、`deepagents` 技术栈，完成后续 PSOP 智能体运行所依赖的 Agent Harness 基础底座，并通过一个 `psop-demo` 智能体完成一次真实运行。

本 PR 不实现 `psop-builder`、`psop-compiler`、`psop-tester` 业务智能体，只完成底座能力：

```text
AgentDefinition
  -> AgentRun
  -> Workspace
  -> DeepAgents-based AgentHarnessRunner
  -> ToolRegistry
  -> Tool call
  -> AgentEvent
  -> AgentArtifact
  -> AgentResult
```

## 2. 验收标准

PR 1 必须满足：

1. `backend/app/agent_harness/` 基础模块存在。
2. `backend/pyproject.toml` 引入 `langchain`、`langgraph`、`deepagents`。
3. 可以加载 repo-backed `AgentDefinition`：`psop-demo`。
4. 可以创建并持久化 `AgentRun`。
5. 每个 `AgentRun` 拥有独立 workspace。
6. 可以运行 DeepAgents-based demo agent。
7. demo agent 至少调用一个工具。
8. 工具调用产生 `AgentEvent`。
9. demo agent 输出产生 `AgentArtifact`。
10. 可以通过 API 查询 `AgentRun`、`AgentEvent`、`AgentArtifact`。
11. 单元测试可以使用 fake tool-calling model 保证稳定性。
12. 验收测试必须使用真实模型 `qwen3.7-plus` 跑通一次 demo agent。
13. 现有 compiler/runtime/skill_tests 主链路不被改动、不退化。

## 3. 非目标

PR 1 不实现：

- `psop-builder`。
- `psop-compiler` 迁移。
- `psop-tester`。
- `psop-audit`。
- `psop-eval`。
- 完整 MCP Gateway。
- production-grade sandbox。
- human approval。
- 完整前端 Agent 工作台。
- Runtime LLM node 接入 Agent Harness。
- DeepAgents 替代 RuntimeService。

## 4. 关键设计约束

### 4.1 Runtime 状态主权不变

`psop-runner` 仍由现有 `RuntimeService` 实现。Agent Harness 不接管 `Run`、`SessionTokenSnapshot`、`TerminalEvent`、`TraceEvent` 的正式状态主权。

### 4.2 LLM 调用必须经过 PSOP Gateway

生产链路和验收测试都必须通过 `LlmInferenceGateway` 或其受控适配器调用模型，不允许 DeepAgents 直接配置外部模型 provider。

### 4.3 PR 1 必须支持 tool calling

DeepAgents 的核心价值是工具调用、文件系统、上下文管理和长期任务能力。PR 1 的 demo agent 必须至少完成一次工具调用，因此 `LlmInferenceGateway` 需要新增最小 `chat()` / tool-calling 能力。

### 4.4 Fake model 只用于单元测试

Fake model 用于确定性单元测试，不作为 PR 验收标准。最终验收必须用真实模型：

```text
route_key: text
model: qwen3.7-plus
```

## 5. 依赖与配置

### 5.1 后端依赖

修改 `backend/pyproject.toml`：

```toml
dependencies = [
  ...
  "langchain>=1.2,<2.0",
  "langgraph>=1.1,<2.0",
  "deepagents>=0.6.11,<0.7.0",
]
```

实际版本以安装和测试结果为准。如果 `deepagents` 已经传递依赖 `langchain` 或 `langgraph`，仍建议在 PSOP 中显式声明，因为本项目会直接引用 LangChain/LangGraph 类型。

### 5.2 Settings

修改 `backend/app/core/config.py`：

```python
agent_profile: str = "dev_open"
agent_workspace_root: str = ".data/agent-runs"
agent_shell_enabled: bool = True
agent_shell_timeout_seconds: int = 60
agent_tool_output_max_chars: int = 12000
agent_runner_sync_timeout_seconds: int = 600
agent_mcp_enabled: bool = False
agent_mcp_config_path: str | None = None
agent_demo_enabled: bool = True
```

真实模型验收依赖现有 LLM 配置：

```text
PSOP_LLM_PROVIDER=openai-compatible
PSOP_LLM_API_BASE_URL=...
PSOP_LLM_API_KEY=...
PSOP_LLM_TEXT_MODEL=qwen3.7-plus
PSOP_LLM_TEXT_ENABLE_THINKING=true
PSOP_LLM_TEXT_THINKING_BUDGET=8192
```

## 6. 目录结构

新增：

```text
backend/app/agent_harness/
  __init__.py
  definitions.py
  context.py
  schemas.py
  events.py
  service.py
  runner.py
  deepagent_factory.py
  workspace.py

  models/
    __init__.py
    psop_gateway_chat_model.py
    fake_tool_calling_model.py

  tools/
    __init__.py
    base.py
    registry.py
    demo_tools.py
    workspace_tools.py
    shell_tool.py

  skills/
    __init__.py
    loader.py
    manifest.py

  persistence/
    __init__.py
    models.py
    repository.py
    service.py

  agents/
    demo/
      agent.yaml
      SKILL.md
```

PR 1 不新增 builder/compiler/tester/audit/eval 业务工具目录。

## 7. 数据模型

### 7.1 AgentRun

新增表：`agent_run`

核心字段：

```text
id
agent_key
agent_version
runner_kind
profile
status
parent_agent_run_id
related_skill_definition_id
related_skill_version_id
related_compile_request_id
related_runtime_run_id
input_payload
output_payload
workspace_path
model_provider
model_name
token_usage
error_message
started_at
finished_at
created_at
updated_at
```

### 7.2 AgentEvent

新增表：`agent_event`

核心字段：

```text
id
agent_run_id
seq_no
event_type
payload
trace_event_id
occurred_at
```

`agent_run_id + seq_no` 必须唯一。

### 7.3 AgentArtifact

新增表：`agent_artifact`

核心字段：

```text
id
agent_run_id
artifact_type
artifact_object_id
inline_content
content_hash
provenance
status
created_at
```

### 7.4 Schema 集成

修改 `backend/app/infra/database.py`：

```python
from app.agent_harness.persistence import models as agent_harness_models  # noqa: F401
```

保持 `Base.metadata.create_all()` 机制不变。

## 8. Gateway tool-calling 能力

### 8.1 新增数据结构

在 `backend/app/gateway/inference.py` 中新增：

```python
@dataclass(slots=True)
class LlmChatMessage:
    role: str
    content: Any
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class LlmToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(slots=True)
class LlmChatCompletion:
    message: dict[str, Any]
    content: str
    tool_calls: list[dict[str, Any]]
    provider: str
    model: str
    raw_response: dict[str, Any]
    usage: dict[str, Any] = field(default_factory=dict)
    request: dict[str, Any] = field(default_factory=dict)
```

### 8.2 扩展 LlmInferenceGateway

```python
def chat(
    self,
    *,
    messages: list[LlmChatMessage],
    tools: list[LlmToolSpec] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    route_key: str = TEXT_ROUTE_KEY,
    response_format: dict[str, Any] | None = None,
) -> LlmChatCompletion:
    ...
```

`complete()` 和 `complete_multimodal()` 保持兼容，可内部调用 `chat()`。

### 8.3 OpenAI-compatible payload

`OpenAICompatibleInferenceGateway.chat()` 构造：

```json
{
  "model": "qwen3.7-plus",
  "messages": [],
  "temperature": 0.2,
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "demo.inspect_text",
        "description": "...",
        "parameters": {}
      }
    }
  ],
  "tool_choice": "auto"
}
```

响应中解析：

```text
choices[0].message.content
choices[0].message.tool_calls
usage
```

## 9. PsopGatewayChatModel

新增 `backend/app/agent_harness/models/psop_gateway_chat_model.py`。

职责：

```text
LangChain messages/tools
  -> LlmChatMessage / LlmToolSpec
  -> LlmInferenceGateway.chat()
  -> AIMessage(content, tool_calls, response_metadata)
```

MVP 要求：

- 支持 `invoke`。
- 支持 `bind_tools`。
- 支持 tool calls 透传。
- 支持 provider/model/usage 写入 `response_metadata`。
- 不实现 streaming。

## 10. Agent Definition 与 Demo Agent

### 10.1 AgentDefinition

新增 `backend/app/agent_harness/definitions.py`：

```python
class AgentDefinition(BaseModel):
    agent_key: str
    version: str
    runner_kind: Literal["deep_agent", "psop_runtime"] = "deep_agent"
    profile: str = "dev_open"
    purpose: str
    model: AgentModelPolicy = Field(default_factory=AgentModelPolicy)
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    mcp: AgentMcpPolicy = Field(default_factory=AgentMcpPolicy)
    memory: AgentMemoryPolicy = Field(default_factory=AgentMemoryPolicy)
    input_schema_ref: str | None = None
    output_schema_ref: str | None = None
```

### 10.2 demo/agent.yaml

```yaml
agent_key: psop-demo
version: v1
runner_kind: deep_agent
profile: dev_open
purpose: Demonstrate PSOP Agent Harness runtime with one tool call and one artifact.
model:
  route_key: text
skills: []
tools:
  - demo.inspect_text
  - demo.write_note
  - workspace.read_file
  - workspace.write_file
mcp:
  enabled: false
memory:
  read_scopes: []
  write_scopes: []
input_schema_ref: psop-demo.input.v1
output_schema_ref: psop-demo.output.v1
```

### 10.3 demo/SKILL.md

```markdown
# psop-demo

你是 PSOP Agent Harness 的演示智能体。

任务：
1. 阅读用户输入 text。
2. 调用 `demo.inspect_text` 分析文本。
3. 调用 `demo.write_note` 写入 `output/demo-note.md`。
4. 最终只输出 JSON：

{
  "summary": "...",
  "contains_question": true,
  "note_path": "output/demo-note.md"
}
```

## 11. Workspace 与工具

### 11.1 Workspace

每个 AgentRun 创建：

```text
.data/agent-runs/{agent_run_id}/workspace/
  input/
  output/
  scratch/
  artifacts/
  logs/
```

### 11.2 Demo tools

新增 `backend/app/agent_harness/tools/demo_tools.py`。

#### demo.inspect_text

输入：

```json
{"text":"string"}
```

输出：

```json
{
  "length": 12,
  "contains_question": true,
  "summary": "..."
}
```

#### demo.write_note

输入：

```json
{
  "filename": "output/demo-note.md",
  "content": "..."
}
```

输出：

```json
{
  "path": "output/demo-note.md",
  "size_chars": 123
}
```

### 11.3 Workspace tools

实现：

```text
workspace.read_file
workspace.write_file
workspace.list_dir
```

### 11.4 Shell tool

实现但 demo agent 不强制调用：

```text
workspace.shell
```

shell 限制：

```text
cwd = workspace
timeout = settings.agent_shell_timeout_seconds
stdout/stderr 截断写入 AgentEvent
```

## 12. ToolRegistry

新增：

```text
backend/app/agent_harness/tools/base.py
backend/app/agent_harness/tools/registry.py
```

每个 tool wrapper 必须记录：

```text
agent.tool.started
agent.tool.completed
agent.tool.failed
```

PR 1 必须保证 demo agent 的工具调用进入 `agent_event`。

## 13. AgentHarnessRunner

新增：

```text
backend/app/agent_harness/service.py
backend/app/agent_harness/runner.py
backend/app/agent_harness/deepagent_factory.py
```

执行流程：

```text
1. load AgentDefinition
2. create AgentRun(status=pending)
3. create workspace
4. write input to workspace/input/request.json
5. append agent.started
6. mark AgentRun running
7. load demo/SKILL.md as system prompt
8. resolve tools
9. create PsopGatewayChatModel
10. create_deep_agent(model, tools, system_prompt)
11. invoke agent
12. parse final output
13. create AgentArtifact(type=demo_result)
14. append agent.completed
15. mark AgentRun succeeded
16. return AgentResult
```

失败时必须：

```text
AgentRun.status = failed
AgentRun.error_message = str(exc)
append agent.failed
```

## 14. API

新增 `backend/app/api/routes/agents.py`。

### 14.1 POST run

```text
POST /api/v1/agents/{agent_key}/runs
```

请求：

```json
{
  "sync": true,
  "input": {
    "text": "请检查这段现场说明是否包含问题？"
  },
  "metadata": {}
}
```

响应：

```json
{
  "agent_run_id": "...",
  "agent_key": "psop-demo",
  "status": "succeeded",
  "output": {
    "summary": "...",
    "contains_question": true,
    "note_path": "output/demo-note.md"
  },
  "artifact_ids": ["..."],
  "event_count": 8
}
```

### 14.2 GET run

```text
GET /api/v1/agents/runs/{agent_run_id}
```

### 14.3 GET events

```text
GET /api/v1/agents/runs/{agent_run_id}/events
```

### 14.4 GET artifacts

```text
GET /api/v1/agents/runs/{agent_run_id}/artifacts
```

### 14.5 Router 集成

修改 `backend/app/api/router.py`：

```python
from app.api.routes.agents import router as agents_router
api_router.include_router(agents_router)
```

修改 `backend/app/api/dependencies.py`：

```python
def get_agent_harness_service(request: Request) -> AgentHarnessService:
    return AgentHarnessService(
        settings=get_app_settings(request),
        inference_gateway=get_inference_gateway(request),
        object_store=get_object_store(request),
    )
```

## 15. 真实模型验收

PR 1 必须增加一个手动验收说明，使用真实模型 `qwen3.7-plus`。

### 15.1 环境变量

```bash
export PSOP_LLM_PROVIDER=openai-compatible
export PSOP_LLM_API_BASE_URL=<your-openai-compatible-base-url>
export PSOP_LLM_API_KEY=<your-api-key>
export PSOP_LLM_TEXT_MODEL=qwen3.7-plus
export PSOP_LLM_TEXT_ENABLE_THINKING=true
export PSOP_LLM_TEXT_THINKING_BUDGET=8192
```

### 15.2 启动服务

```bash
scripts/dev/run-server.sh
```

### 15.3 执行 demo agent

```bash
curl -X POST http://127.0.0.1:8001/api/v1/agents/psop-demo/runs \
  -H "Content-Type: application/json" \
  -d '{
    "sync": true,
    "input": {
      "text": "请检查这段现场说明是否包含问题？"
    }
  }'
```

### 15.4 验收结果

响应中必须满足：

```text
status == succeeded
output.contains_question == true
output.note_path == output/demo-note.md
artifact_ids 非空
event_count >= 6
```

事件中必须包含：

```text
agent.started
agent.tool.started demo.inspect_text
agent.tool.completed demo.inspect_text
agent.tool.started demo.write_note
agent.tool.completed demo.write_note
agent.artifact.created
agent.completed
```

Artifact 中必须包含：

```text
artifact_type = demo_result
inline_content.note_path = output/demo-note.md
```

Workspace 中必须存在：

```text
.data/agent-runs/{agent_run_id}/workspace/output/demo-note.md
```

## 16. 自动测试策略

自动测试仍使用 fake model，避免 CI 依赖真实模型和 API key。

新增测试：

```text
tests/test_agent_harness_definitions.py
tests/test_agent_harness_persistence.py
tests/test_agent_harness_workspace.py
tests/test_agent_harness_tools.py
tests/test_gateway_chat_tool_calling.py
tests/test_psop_gateway_chat_model.py
tests/test_agent_harness_demo_agent.py
tests/test_agents_api.py
```

覆盖：

```text
AgentDefinition loader
AgentRun create / mark succeeded / mark failed
AgentEvent seq_no 递增
AgentArtifact content_hash
workspace path 防逃逸
workspace.write_file 防逃逸
demo.inspect_text 输出
demo.write_note 写文件
gateway chat payload 包含 tools
gateway chat response 解析 tool_calls
PsopGatewayChatModel bind_tools
AgentHarnessService 使用 fake model 完成 demo run
API 创建并查询 demo run
```

PR 合并前运行：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
cd static && npm test -- --runInBand
cd static && npm run build:css
```

## 17. 实施顺序

### Step 1：依赖与配置

改动：

```text
backend/pyproject.toml
backend/app/core/config.py
```

验收：

```text
依赖安装成功。
Settings 默认值可加载。
```

### Step 2：持久化层

改动：

```text
agent_harness/persistence/models.py
agent_harness/persistence/repository.py
agent_harness/persistence/service.py
infra/database.py
```

验收：

```text
create_schema 创建 agent_* 表。
repository 测试通过。
```

### Step 3：Agent Definition 与 demo agent

改动：

```text
definitions.py
skills/loader.py
agents/demo/agent.yaml
agents/demo/SKILL.md
```

验收：

```text
load psop-demo 成功。
system prompt 包含 demo/SKILL.md。
```

### Step 4：Gateway chat/tool calling

改动：

```text
gateway/inference.py
models/psop_gateway_chat_model.py
models/fake_tool_calling_model.py
```

验收：

```text
complete()/complete_multimodal() 兼容旧行为。
chat() 可发送 tools。
PsopGatewayChatModel.bind_tools() 可用。
```

### Step 5：Workspace 与 tools

改动：

```text
workspace.py
tools/base.py
tools/registry.py
tools/demo_tools.py
tools/workspace_tools.py
tools/shell_tool.py
```

验收：

```text
工具执行写 agent_event。
workspace 写入不越界。
shell timeout 生效。
```

### Step 6：AgentHarnessRunner

改动：

```text
service.py
runner.py
deepagent_factory.py
schemas.py
events.py
```

验收：

```text
fake model demo agent run succeeded。
AgentEvent / AgentArtifact 完整。
```

### Step 7：API

改动：

```text
api/routes/agents.py
api/router.py
api/dependencies.py
```

验收：

```text
API demo run succeeded。
GET events/artifacts 可用。
```

### Step 8：真实模型验收

使用 `qwen3.7-plus` 按第 15 节执行手动验收。

## 18. 风险与处理

| 风险 | 处理 |
| --- | --- |
| qwen3.7-plus tool calling 格式与 OpenAI 标准有差异 | 在 `OpenAICompatibleInferenceGateway.chat()` 中保留 raw response，并把 tool_calls 解析逻辑做成兼容函数。 |
| 真实模型不稳定调用工具 | demo/SKILL.md 明确要求必须调用 `demo.inspect_text` 和 `demo.write_note`；验收时检查 agent_event。 |
| DeepAgents 与 LangChain 版本不匹配 | PR 中固定兼容版本范围，并在本地安装验证。 |
| 真实模型验收依赖 API key | 自动测试使用 fake model；真实模型验收作为手动验收步骤。 |
| workspace 写入越界 | 所有 path 经过 `resolve_path()` 和 `assert_inside_workspace()`。 |
| 引入 agent_harness 影响现有主链路 | PR 1 不接入 compiler/runtime/skill_tests，只新增 API 和独立模块。 |

## 19. PR 完成定义

PR 1 完成后，系统应具备：

```text
可运行的 Agent Harness 基础底座；
repo-backed AgentDefinition；
AgentRun/Event/Artifact 持久化；
DeepAgents-based AgentHarnessRunner；
PSOP LLM Gateway ChatModel adapter；
ToolRegistry；
workspace file/shell 工具；
psop-demo 智能体；
API 触发和查询能力；
fake model 自动测试；
qwen3.7-plus 真实模型验收记录。
```

PR 1 完成后，后续 PR 可以在这个底座上继续实现：

```text
psop-builder
psop-compiler
psop-tester
psop-audit
psop-eval
```