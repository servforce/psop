# Agent Harness MVP 实施计划

本文是阶段性实施计划，不是长期架构事实源。Agent Harness 的系统边界和长期架构以 [系统架构设计](../../architecture/system-architecture.md) 为准。

下面是基于 `issue-1-psop-mvp` 当前代码现状制定的 **Agent Harness MVP 实施计划**。这个计划的目标不是一次性完成完整多智能体治理平台，而是先在现有代码上建立一个可运行、可测试、可扩展的 `agent_harness` 底座，并通过一个 demo 智能体验收：**Python 脚本可调用，运行过程中包含 skill 激活、tool 调用、memory 读写，并产出可审计的 agent events。**

---

# 一、当前代码基线判断

当前 PSOP 后端已经是 Python 3.11+，但依赖里还没有 LangChain、LangGraph、DeepAgents；`pyproject.toml` 目前主要包含 FastAPI、SQLAlchemy、Pydantic Settings、PyYAML、对象存储、多模态处理等依赖。

现有 README 已经把 `backend/app/agent_harness/` 标为目标模块，但代码中尚未实现该模块；当前 MVP 主链路仍是 `Skills -> Publish -> Auto Compile -> Invocation -> Runtime -> Replay / Observability`。

现有编译智能体 `SkillCompileAgent` 的实现方式仍是 Prompt Pack + Domain Pack + JSON payload 组装，然后直接调用 `LlmInferenceGateway.complete()`，再解析模型返回 JSON。 当前 runtime 的 LLM 节点也是直接渲染 prompt 后调用 `complete()` 或 `complete_multimodal()`；tool 节点目前只支持内置 demo tool。

还有一个关键点：当前 `LlmInferenceGateway` 只定义了 `complete()`、`complete_multimodal()`、`list_model_capabilities()`，并没有 tool-calling chat API；当前 OpenAI-compatible gateway 发送的 payload 也只有 `model/messages/temperature`，没有 `tools`、`tool_choice`、`tool_calls` 的处理。  这意味着如果要让 DeepAgents/LangChain 真正执行工具调用，必须补一个 **tool-calling model adapter**，否则只能做普通文本调用。

所以本计划的核心策略是：

```text
不要先改造 psop-runner runtime。
不要先做完整 builder/compiler/tester/audit/eval。
先新增 agent_harness 底座，并用 demo agent 证明：
  1. DeepAgents 能接入；
  2. PSOP Gateway 能作为 LangChain ChatModel 使用；
  3. tool / skill / memory / event 机制能跑通；
  4. 后续智能体可以复用同一套 harness。
```

---

# 二、技术选型收敛

前期只暴露一个顶层 Runner：

```text
AgentHarnessRunner
```

内部默认使用 DeepAgents，不单独暴露 `LangGraphRunner`。DeepAgents 本身就是 LangChain 生态中的 agent harness，并基于 LangGraph runtime；它内置 planning、filesystem、tools、skills、memory、subagents、human-in-the-loop 等能力。([LangChain 文档][1]) LangGraph 则作为 DeepAgents 底层 runtime 存在，保留以后写 custom graph 的能力即可，不要在 MVP 阶段增加第二套 runner 概念。([LangChain 文档][2])

依赖建议：

```toml
dependencies = [
  ...
  "langchain>=1.2,<2.0",
  "langgraph>=1.0,<2.0",
  "deepagents>=0.6,<0.7",
  "langchain-core>=1.2,<2.0",
  "langchain-mcp-adapters>=0.1,<1.0",
]
```

`deepagents 0.6.11` 是 2026-06-18 发布的版本，要求 Python `>=3.11,<4.0`，与当前 PSOP 后端 Python 3.11 基线匹配。([PyPI][3])

---

# 三、目标目录结构

新增目录：

```text
backend/app/agent_harness/
  __init__.py

  schemas.py
  service.py
  events.py
  errors.py

  runners/
    __init__.py
    deepagents_runner.py
    scripted_runner.py

  models/
    __init__.py
    psop_gateway_chat_model.py
    scripted_chat_model.py

  tools/
    __init__.py
    spec.py
    registry.py
    builtin.py
    mcp_provider.py

  skills/
    __init__.py
    spec.py
    loader.py
    registry.py

  memory/
    __init__.py
    store.py
    file_store.py

  workspace/
    __init__.py
    manager.py

  persistence/
    __init__.py
    models.py
    repository.py

  demo/
    agent.yaml
    system.md
    AGENTS.md
    skills/
      demo_psop_checklist/
        SKILL.md
```

新增脚本：

```text
tests/run_agent_demo.py
```

新增测试：

```text
tests/test_agent_harness_demo.py
tests/test_agent_harness_tools.py
tests/test_agent_harness_skills.py
tests/test_agent_harness_memory.py
```

---

# 四、实施计划

## Step 1：增加依赖与基础配置

修改：

```text
backend/pyproject.toml
backend/app/core/config.py
```

新增配置：

```python
agent_harness_enabled: bool = True
agent_harness_profile: str = "dev_open"
agent_harness_workspace_root: str = ".psop/agent-runs"
agent_harness_mcp_enabled: bool = False
```

理由：当前配置已集中在 `Settings` 中，LLM、对象存储、worker、runtime 等配置都在这里定义，所以 harness 配置也应走同一入口。

MVP 默认使用：

```text
PSOP_AGENT_HARNESS_PROFILE=dev_open
```

该 profile 允许 demo agent 使用文件 workspace、内置 tools、mock model；后续再扩展到真实 MCP、sandbox、approval。

---

## Step 2：定义 Harness 公共数据结构

新增 `backend/app/agent_harness/schemas.py`：

```python
class AgentInvocation(BaseModel):
    agent_key: str
    input: dict[str, Any]
    context: dict[str, Any] = {}
    memory_scope: str | None = None
    workspace_id: str | None = None
    use_mock_model: bool = False


class AgentResult(BaseModel):
    agent_run_id: str
    agent_key: str
    status: Literal["succeeded", "failed"]
    final_output: str
    structured_output: dict[str, Any] = {}
    events: list[AgentEvent] = []
    workspace_path: str | None = None


class AgentEvent(BaseModel):
    seq_no: int
    event_type: str
    payload: dict[str, Any] = {}
    occurred_at: datetime
```

事件类型先定义最小集：

```text
agent.run.started
agent.skill.loaded
agent.memory.read
agent.memory.write
agent.tool.started
agent.tool.completed
agent.tool.failed
agent.run.completed
agent.run.failed
```

这一步不需要先接入 runtime 的 `TraceEvent`。当前 runtime 的 `TraceEvent` 是 runner-agent 的运行事实表，和新 agent harness 的 agent events 是两个层级；后续可以做投影。当前 runtime 的 replay 基于 snapshot、trace event、terminal event 重建 timeline，这一机制不应被 demo harness 打断。

---

## Step 3：实现 WorkspaceManager

新增：

```text
backend/app/agent_harness/workspace/manager.py
```

职责：

```text
1. 为每次 agent run 创建 workspace。
2. 写入 input.json。
3. 写入 events.jsonl。
4. 写入 output.json。
5. 为 file tools、memory、skills 提供工作目录。
```

路径建议：

```text
.psop/agent-runs/{agent_run_id}/
  input.json
  output.json
  events.jsonl
  memory.json
  workspace/
    result.md
```

MVP 不直接暴露 repo 根目录写入权限。DeepAgents 支持虚拟文件系统、`read_file`、`write_file`、`edit_file`、`glob`、`grep`，以及在 sandbox backend 下暴露 `execute` shell tool；它也支持用 permission rules 限制读写路径。([LangChain 文档][1]) MVP 先限定在 agent workspace，可以同时满足“前期不复杂”和“不要污染工程目录”。

---

## Step 4：实现 MemoryStore

新增：

```text
backend/app/agent_harness/memory/store.py
backend/app/agent_harness/memory/file_store.py
```

MVP 只实现文件型 memory：

```python
class MemoryStore(Protocol):
    def read(self, scope: str) -> dict[str, Any]: ...
    def write(self, scope: str, key: str, value: Any) -> None: ...
```

Demo 使用：

```text
memory_scope = "demo.psop_harness_agent"
.psop/agent-runs/{agent_run_id}/memory.json
```

DeepAgents 文档中 memory 是 context management 的一部分，可持久化跨会话偏好、规范和项目约定。([LangChain 文档][1]) 但 PSOP MVP 不要一开始做向量库、长期记忆或跨用户记忆，只要能证明 memory 组件可以被 tool 和 agent runner 调用即可。

---

## Step 5：实现 ToolRegistry 与内置 Demo Tools

新增：

```text
backend/app/agent_harness/tools/spec.py
backend/app/agent_harness/tools/registry.py
backend/app/agent_harness/tools/builtin.py
```

`ToolSpec`：

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    source: Literal["builtin", "skill", "mcp"] = "builtin"
```

内置 demo tools：

```text
demo_extract_check_items(text: str) -> dict
demo_score_checklist(items: list[str]) -> dict
memory_put(key: str, value: str) -> dict
memory_get(key: str) -> dict
write_demo_report(filename: str, content: str) -> dict
```

LangChain 的 tools 本质是带有清晰输入输出的 callable functions，模型根据上下文决定何时调用；官方建议通过类型标注定义 schema，并用 docstring 描述工具用途。([LangChain 文档][4])

每个 tool wrapper 必须做三件事：

```text
1. 写 agent.tool.started event
2. 执行 tool
3. 写 agent.tool.completed 或 agent.tool.failed event
```

MCP 先做 provider skeleton：

```text
McpToolProvider
  - load_config()
  - list_tools()
  - to_langchain_tools()
```

MVP 不要求真实连接 MCP server，但接口要留好。MCP tool 本身有 name、description、inputSchema、outputSchema，并通过 `tools/list`、`tools/call` 暴露和调用；客户端应验证工具结果并记录工具调用。([Model Context Protocol][5]) ([Model Context Protocol][5])

---

## Step 6：实现 Agent Skills Loader

新增：

```text
backend/app/agent_harness/skills/spec.py
backend/app/agent_harness/skills/loader.py
backend/app/agent_harness/skills/registry.py
```

MVP skill 目录结构：

```text
backend/app/agent_harness/demo/skills/demo_psop_checklist/
  SKILL.md
```

`SKILL.md` 示例：

```markdown
---
name: demo_psop_checklist
description: 将一段现场作业描述拆解为检查项，并生成简体中文检查报告。
tools:
  - demo_extract_check_items
  - demo_score_checklist
  - write_demo_report
---

# Demo PSOP Checklist Skill

你负责把用户输入的现场作业描述转换为检查清单。
必须调用 demo_extract_check_items。
必须调用 demo_score_checklist。
最后将报告写入 result.md。
```

实现策略：

```text
1. 解析 SKILL.md frontmatter。
2. 记录 agent.skill.loaded event。
3. 将 skill instruction 拼接进 system prompt。
4. 将 skill 声明的 tools 加入 ToolRegistry。
```

DeepAgents 的 skills 机制是按需加载专业工作流、领域知识和说明，技能目录包含 `SKILL.md`，也可以包含脚本、模板、参考文档；它采用 progressive disclosure，启动时读取 frontmatter，需要时再读取完整内容。([LangChain 文档][1])

MVP 可以先不做完整 progressive disclosure，只做：

```text
load selected skill at run start
```

但事件里要明确记录：

```json
{
  "event_type": "agent.skill.loaded",
  "payload": {
    "skill_name": "demo_psop_checklist",
    "tools": ["demo_extract_check_items", "demo_score_checklist", "write_demo_report"]
  }
}
```

这样就能满足 “skills 执行” 的验收要求：skill 被加载、skill 绑定 tools、agent 在该 skill 指令下完成任务。

---

## Step 7：扩展 LLM Gateway 支持 Tool Calling

这是整个计划的关键改造点。

当前 `LlmInferenceGateway` 不支持 tool calling；如果直接把 DeepAgents 接上现有 gateway，模型无法返回 `tool_calls`，也就无法形成真正的工具调用 loop。现有 gateway 只返回 `content/provider/model/raw_response/usage/request`，并只解析 message content。

建议新增协议，不破坏旧接口：

```python
@dataclass(slots=True)
class LlmToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class LlmChatMessage:
    role: str
    content: str | None = None
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[LlmToolCall] = field(default_factory=list)


@dataclass(slots=True)
class LlmChatCompletion:
    message: LlmChatMessage
    provider: str
    model: str
    raw_response: dict
    usage: dict[str, Any] = field(default_factory=dict)
    request: dict[str, Any] = field(default_factory=dict)


class LlmInferenceGateway(Protocol):
    ...
    def complete_chat(
        self,
        *,
        messages: list[LlmChatMessage],
        tools: list[dict[str, Any]] | None = None,
        route_key: str = TEXT_ROUTE_KEY,
    ) -> LlmChatCompletion:
        ...
```

`OpenAICompatibleInferenceGateway.complete_chat()` 负责：

```text
1. messages -> OpenAI-compatible messages
2. tools -> OpenAI-compatible tools schema
3. response.choices[0].message.tool_calls -> LlmToolCall
4. usage/provider/model/request redaction 保持一致
```

旧的 `complete()` 和 `complete_multimodal()` 继续保留，以免影响 compiler/runtime 现有逻辑。

---

## Step 8：实现 PsopGatewayChatModel

新增：

```text
backend/app/agent_harness/models/psop_gateway_chat_model.py
```

职责：

```text
LangChain BaseChatModel
  -> 调用 LlmInferenceGateway.complete_chat()
  -> 支持 bind_tools()
  -> 将 AIMessage.tool_calls 转回 LangChain 标准结构
```

这一步的价值是：后续所有 PSOP agent 都可以使用 DeepAgents/LangChain 的 tool loop，但模型出口仍然经过 PSOP 的 `LlmInferenceGateway`，符合 README 里“大模型调用必须经过 LLM Inference Gateway”的平台约束。

同时新增：

```text
backend/app/agent_harness/models/scripted_chat_model.py
```

用于测试和 demo：

```text
ScriptedToolCallingChatModel
```

它不调用真实 LLM，而是按固定脚本返回：

```text
1. AIMessage(tool_calls=[demo_extract_check_items])
2. AIMessage(tool_calls=[demo_score_checklist])
3. AIMessage(tool_calls=[write_demo_report])
4. AIMessage(final answer)
```

这样 CI 和本地脚本可以在没有 API key 的情况下稳定验收。

---

## Step 9：实现 DeepAgents Runner

新增：

```text
backend/app/agent_harness/runners/deepagents_runner.py
```

核心逻辑：

```python
class DeepAgentsRunner:
    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        run = create_agent_run()
        workspace = workspace_manager.create(run.id)
        memory = memory_store.read(invocation.memory_scope)

        tools = tool_registry.resolve(...)
        skills = skill_loader.load(...)

        agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=render_system_prompt(...),
            # 后续再接 permissions/backend/checkpointer
        )

        result = agent.invoke({
            "messages": [
                {"role": "user", "content": invocation.input["text"]}
            ]
        })

        persist_output()
        return AgentResult(...)
```

DeepAgents 的 quickstart 就是通过 `create_deep_agent(model=..., tools=..., system_prompt=...)` 创建 agent，然后调用 `agent.invoke(...)`。([LangChain 文档][1])

MVP 暂时不启用复杂 subagents，不做 LangSmith，不做 human approval，不做长期 store backend。只要把主 agent、skill、tool、memory 跑通。

---

## Step 10：实现 AgentHarnessService

新增：

```text
backend/app/agent_harness/service.py
```

接口：

```python
class AgentHarnessService:
    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        ...
```

职责：

```text
1. 创建 agent_run_id。
2. 初始化 event writer。
3. 初始化 workspace。
4. 加载 agent definition。
5. 调用 DeepAgentsRunner。
6. 返回 AgentResult。
```

同时新增 FastAPI dependency，但不强制改造现有 compiler/runtime：

```python
def get_agent_harness_service(request: Request) -> AgentHarnessService:
    return request.app.state.agent_harness_service
```

当前 `app.py` 已经在 app state 中集中挂载 settings、db_manager、gitlab_gateway、inference_gateway、asr_gateway、object_store。 所以 harness service 也应在 `create_app()` 中初始化。

不过 MVP 的首个验收入口建议先走 Python 脚本，不急着开放 API。

---

# 五、Demo 智能体定义

新增：

```text
backend/app/agent_harness/demo/agent.yaml
backend/app/agent_harness/demo/system.md
backend/app/agent_harness/demo/AGENTS.md
```

`agent.yaml`：

```yaml
agent_key: demo.psop_harness_agent
version: v1
runner: deepagents
route_key: text
description: Demo agent for validating PSOP agent harness tools, skills, and memory.
skills:
  - demo_psop_checklist
tools:
  - demo_extract_check_items
  - demo_score_checklist
  - memory_get
  - memory_put
  - write_demo_report
memory_scope: demo.psop_harness_agent
```

`system.md`：

```markdown
你是 PSOP Agent Harness 的验收 Demo 智能体。
你的任务是读取用户输入的现场作业描述，使用 demo_psop_checklist skill 生成检查清单。
你必须调用工具完成：
1. demo_extract_check_items
2. demo_score_checklist
3. memory_put
4. write_demo_report

最终使用简体中文输出运行摘要，并说明报告文件路径。
```

`AGENTS.md`：

```markdown
# Demo Memory

- 所有面向用户的自然语言必须使用简体中文。
- 输出应包含：检查项数量、风险等级、报告路径。
```

---

# 六、Demo 脚本验收方式

新增：

```text
tests/run_agent_demo.py
```

运行方式：

```bash
PYTHONPATH=backend backend/.venv/bin/python tests/run_agent_demo.py \
  --input "进入泵房前检查 PPE，确认阀门关闭，记录压力表读数。"
```

默认使用真实 LLM，并读取 `PSOP_LLM_*` 配置。

本地测试如需使用 mock model，应在测试代码中显式传入 `AgentInvocation(use_mock_model=True)`，不要再通过环境变量切换。

```bash
PSOP_LLM_API_KEY=... \
PYTHONPATH=backend backend/.venv/bin/python tests/run_agent_demo.py \
  --input "进入泵房前检查 PPE，确认阀门关闭，记录压力表读数。"
```

预期输出：

```json
{
  "agent_key": "demo.psop_harness_agent",
  "status": "succeeded",
  "final_output": "已完成检查清单生成，共识别 3 个检查项，风险等级 medium，报告已写入 workspace/result.md。",
  "workspace_path": ".psop/agent-runs/<agent_run_id>/workspace",
  "events": [
    {"event_type": "agent.run.started"},
    {"event_type": "agent.skill.loaded"},
    {"event_type": "agent.memory.read"},
    {"event_type": "agent.tool.started", "payload": {"tool_name": "demo_extract_check_items"}},
    {"event_type": "agent.tool.completed", "payload": {"tool_name": "demo_extract_check_items"}},
    {"event_type": "agent.tool.started", "payload": {"tool_name": "demo_score_checklist"}},
    {"event_type": "agent.tool.completed", "payload": {"tool_name": "demo_score_checklist"}},
    {"event_type": "agent.memory.write"},
    {"event_type": "agent.tool.started", "payload": {"tool_name": "write_demo_report"}},
    {"event_type": "agent.tool.completed", "payload": {"tool_name": "write_demo_report"}},
    {"event_type": "agent.run.completed"}
  ]
}
```

同时生成文件：

```text
.psop/agent-runs/<agent_run_id>/
  input.json
  output.json
  events.jsonl
  memory.json
  workspace/
    result.md
```

---

# 七、测试计划

新增测试不依赖真实 LLM。

## 1. `test_agent_harness_skills.py`

验证：

```text
- 能解析 SKILL.md frontmatter
- 能读取 skill name/description/tools
- 加载 skill 时产生 agent.skill.loaded event
```

## 2. `test_agent_harness_tools.py`

验证：

```text
- ToolRegistry 能注册 demo tools
- tool 输入参数能校验
- tool 执行成功写 started/completed event
- tool 执行失败写 failed event
```

## 3. `test_agent_harness_memory.py`

验证：

```text
- FileMemoryStore 能 read/write
- memory_put 工具能写 memory.json
- memory_get 工具能读取已写入内容
```

## 4. `test_agent_harness_demo.py`

端到端验证：

```text
AgentHarnessService.invoke()
  -> 加载 demo agent
  -> 加载 demo skill
  -> mock model 触发 tool calls
  -> tool calls 执行
  -> memory 写入
  -> result.md 写入
  -> 返回 AgentResult
```

断言：

```text
- result.status == "succeeded"
- events 中包含 agent.skill.loaded
- events 中包含至少 3 次 agent.tool.completed
- events 中包含 agent.memory.write
- workspace/result.md 存在
- output.json 存在
```

运行方式沿用当前项目测试命令：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
```

README 里已经把该命令作为后端测试方式。

---

# 八、与现有模块的关系

## 1. 不改 RuntimeService 主流程

当前 `RuntimeService` 是 psop-runner-agent 的正式运行环境，会创建 `SkillInvocation`、`Run`、`TerminalSession`、`SessionTokenSnapshot` 和 `RuntimeJob`。 它的状态主权仍应保留，不在本次 MVP 中接入 DeepAgents。

## 2. 暂不替换 SkillCompileAgent

现有 `CompilerService` 在 `process_compile_request()` 中加载 Skill source、校验 manifest、调用 `_compile_with_agent()`、写入 artifact object 和 `EgCompileArtifact`。

本次验收只做 demo agent。下一阶段再把 `SkillCompileAgent` 改成：

```text
SkillCompileAgent
  -> AgentHarnessService.invoke(agent_key="psop-compiler")
```

不要在第一步就重构 compiler，以免影响 publish/compile 主链路。

## 3. 暂不接入 JobWorker

当前 worker 会处理 `compile`、`runtime`、`skill_test_timeline_driver`、`raw_material_analysis`、`skill_raw_material_generation` 等 job。 本次 demo agent 先通过脚本运行，不新增 job type。下一阶段再考虑：

```text
job_type = "agent_run"
```

---

# 九、交付拆分建议

虽然这是一个 MVP，但建议按 5 个小 PR 或 5 个 commit 阶段推进，方便 review。

## Commit 1：依赖和配置

```text
- pyproject.toml 增加 langchain/langgraph/deepagents/langchain-mcp-adapters
- Settings 增加 agent_harness_* 配置
```

## Commit 2：Harness core

```text
- schemas.py
- events.py
- workspace manager
- memory store
- AgentHarnessService skeleton
```

## Commit 3：Tools + Skills

```text
- ToolSpec / ToolRegistry
- builtin demo tools
- Skill loader
- demo skill
```

## Commit 4：Model adapter + Runner

```text
- LlmGateway complete_chat 扩展
- PsopGatewayChatModel
- ScriptedToolCallingChatModel
- DeepAgentsRunner
```

## Commit 5：Demo script + tests

```text
- demo agent.yaml/system.md/AGENTS.md
- tests/run_agent_demo.py
- pytest e2e
```

---

# 十、最终验收标准

本计划完成后，应满足以下验收条件：

```text
1. backend/app/agent_harness/ 模块存在，并包含 tools、skills、memory、runner、model adapter、workspace 基础组件。
2. 项目依赖中已引入 langchain、langgraph、deepagents。
3. 存在 demo.psop_harness_agent。
4. 可以通过 Python 脚本运行 demo agent。
5. demo 运行过程中能加载至少一个 skill。
6. demo 运行过程中能执行至少两个 tools。
7. demo 运行过程中能写入 memory。
8. demo 运行过程中能写入 workspace 文件。
9. demo 输出 AgentResult。
10. demo 产生 events.jsonl，可看到 skill/tool/memory/run 全链路事件。
11. pytest 中存在不依赖真实 LLM 的端到端测试。
12. 现有 compiler/runtime/skill_tests 主流程不被破坏。
```

---

# 十一、后续演进路径

完成 demo harness 后，再进入第二阶段：

```text
1. psop-builder 接入 AgentHarnessService
2. psop-compiler 接入 AgentHarnessService
3. psop-tester 接入 AgentHarnessService
4. MCP provider 接入真实 MCP server
5. workspace shell/file tools 升级为 sandbox backend
6. agent_run / agent_event 持久化到数据库
7. agent events 投影到 PSOP observability/replay
```

这个顺序可以保证第一步就有一个可运行的智能体底座，同时不会破坏当前 PSOP 最重要的 runtime/publish/compile 主链路。

[1]: https://docs.langchain.com/oss/python/deepagents/overview "Deep Agents overview - Docs by LangChain"
[2]: https://docs.langchain.com/oss/python/langgraph/overview "LangGraph overview - Docs by LangChain"
[3]: https://pypi.org/project/deepagents/ "deepagents · PyPI"
[4]: https://docs.langchain.com/oss/python/langchain/tools "Tools - Docs by LangChain"
[5]: https://modelcontextprotocol.io/docs/concepts/tools "Tools - Model Context Protocol"
