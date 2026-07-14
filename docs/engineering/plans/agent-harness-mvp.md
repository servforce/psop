# Agent Harness MVP 实施计划（已完成）

本文是阶段性实施计划，不是长期架构事实源。Agent Harness 的系统边界和长期架构以 [系统架构设计](../../architecture/system-architecture.md) 为准。

本文记录 `issue-1-psop-mvp` 分支上的 **Agent Harness MVP 实施计划与完成状态**。这个计划的目标不是一次性完成完整多智能体治理平台，而是先在现有代码上建立一个可运行、可测试、可扩展的 `agent_harness` 底座，并通过一个 demo 智能体验收：**Python 脚本可调用，运行过程中包含 skill 激活、tool 调用、memory 读写，并产出可审计的 agent events。**

## 状态

```text
状态：已完成
完成日期：2026-06-25
完成范围：Agent Harness MVP demo 底座
验收方式：
  - PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
  - PYTHONPATH=backend backend/.venv/bin/python tests/run_agent_demo.py --input "进入泵房前检查 PPE，确认阀门关闭，记录压力表读数。"
```

当前已完成：

```text
1. backend/app/agent_harness/ 已包含 agents、models、middlewares、sandbox、tools、skills、memory、runners、service、schemas、events 等基础组件。
2. demo.psop_harness_agent 已可通过 tests/run_agent_demo.py 运行。
3. demo 运行链路已覆盖 load_skill、demo_extract_check_items、demo_score_checklist、memory_put、write_demo_report。
4. demo 运行会生成 AgentResult、events.jsonl、memory.json、workspace/result.md。
5. pytest 中存在不依赖真实 LLM 的 scripted model 端到端测试。
6. RuntimeService、CompilerService、SkillTestService 等既有主流程未迁移，状态主权仍保持不变。
```

---

# 一、当前代码基线判断

当前 PSOP 后端已经是 Python 3.11+；Agent Harness 依赖 LangChain、LangGraph 和 LangChain OpenAI-compatible provider，`pyproject.toml` 还包含 FastAPI、SQLAlchemy、Pydantic Settings、PyYAML、对象存储、多模态处理等依赖。

`backend/app/agent_harness/` 模块已经实现 MVP demo 底座。当前 PSOP 主链路仍是 `Skills -> Publish -> Auto Compile -> Invocation -> Runtime -> Replay / Observability`；Agent Harness MVP 没有替换既有 Runtime/Compiler/SkillTest 主流程。

现有编译智能体 `SkillCompileAgent` 的实现方式仍是 Prompt Pack + Domain Pack + JSON payload 组装，然后直接调用 `LlmInferenceGateway.complete()`，再解析模型返回 JSON。 当前 runtime 的 LLM 节点也是直接渲染 prompt 后调用 `complete()` 或 `complete_multimodal()`；tool 节点目前只支持内置 demo tool。

Agent Harness 的 LLM 层参考 deer-flow：通过 LangChain model factory 直接构造 `BaseChatModel`，再交给 LangChain `create_agent`。`LlmInferenceGateway` 仍可服务 Runtime、Compiler、素材分析等既有链路，但不再是 Harness 的强制出口。

本计划已按以下核心策略完成：

```text
不要先改造 psop-runner runtime。
不要先做完整 builder/compiler/tester/audit/eval。
已新增 agent_harness 底座，并用 demo agent 证明：
  1. LangChain `create_agent` 能接入；
  2. LangChain model factory 能创建真实 ChatModel；
  3. tool / skill / memory / event 机制能跑通；
  4. 后续智能体可以复用同一套 harness。
```

---

# 二、技术选型收敛

前期只暴露一个顶层 Service：

```text
AgentHarnessService
```

内部默认使用 LangChain `create_agent`，不单独暴露 `LangGraphRunner`。LangGraph 作为 LangChain agent 的底层 runtime 存在，保留以后写 custom graph 的能力即可。PSOP 的 sandbox、tools、skills、memory、middleware 由 Agent Harness 自己治理，避免与 batteries-included harness 的默认能力重叠。

依赖建议：

```toml
dependencies = [
  ...
  "langchain>=1.2,<2.0",
  "langgraph>=1.2,<2.0",
  "langchain-core>=1.2.22,<2.0",
  "langchain-openai>=1.3,<2.0",
  "langchain-mcp-adapters>=0.3,<1.0",
]
```

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
    langchain_agent_executor.py

  models/
    __init__.py
    factory.py
    scripted_chat_model.py

  middlewares/
    __init__.py
    dangling_tool_call.py
    model_events.py
    token_usage.py
    tool_calls.py

  sandbox/
    __init__.py
    base.py
    provider.py
    local.py

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

  persistence/
    __init__.py
    models.py
    repository.py

  agents/
    __init__.py
    context.py
    factory.py
    registry.py
    demo/
      psop_harness_agent/
        agent.py
        prompt.py
        agent.yaml
        system.md
```

Agent Skill 源目录统一位于仓库根目录：

```text
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
agent_harness_profile: str = "dev_open"
agent_harness_sandbox_provider: str = "local"
agent_harness_sandbox_root: str = ".psop/agent-runs"
agent_harness_mcp_enabled: bool = False
```

理由：Agent Harness 是 PSOP 系统基础能力，启用不需要额外开关；这里只保留 profile、sandbox 和 MCP adapter 等运行参数。

MVP 默认使用：

```text
PSOP_AGENT_HARNESS_PROFILE=dev_open
```

该 profile 允许 demo agent 使用 local sandbox 和内置 tools；后续再扩展到真实 MCP、远程 sandbox、approval。

---

## Step 2：定义 Harness 公共数据结构

新增 `backend/app/agent_harness/schemas.py`：

```python
class AgentInvocation(BaseModel):
    agent_key: str
    input: dict[str, Any]
    context: dict[str, Any] = {}
    memory_scope: str | None = None
    agent_run_id: str | None = None


class AgentResult(BaseModel):
    agent_run_id: str
    agent_key: str
    status: Literal["succeeded", "failed"]
    final_output: str
    structured_output: dict[str, Any] = {}
    events: list[AgentEvent] = []
    sandbox_path: str | None = None


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

## Step 3：实现 Local Sandbox

新增：

```text
backend/app/agent_harness/sandbox/base.py
backend/app/agent_harness/sandbox/provider.py
backend/app/agent_harness/sandbox/local.py
```

职责：

```text
1. 为每次 agent run 创建 local sandbox。
2. 写入 input.json。
3. 写入 events.jsonl。
4. 写入 output.json。
5. 为 file tools、memory、skills 提供受控虚拟路径。
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
  outputs/
```

MVP 不直接暴露 repo 根目录写入权限。工具只使用 `/mnt/psop/workspace`、`/mnt/psop/outputs` 虚拟路径，local sandbox 负责映射到 `.psop/agent-runs/{agent_run_id}`。首版不提供 shell/bash。

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

LangChain agent 的状态与 store 能承载会话内外上下文，但 PSOP MVP 不要一开始做向量库、长期记忆或跨用户记忆，只要能证明 memory 组件可以被 tool 和 agent runner 调用即可。([LangChain 文档][1])

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

LangChain 的 tools 本质是带有清晰输入输出的 callable functions，模型根据上下文决定何时调用；官方建议通过类型标注定义 schema，并用 docstring 描述工具用途。([LangChain 文档][3])

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

MVP 不要求真实连接 MCP server，但接口要留好。MCP tool 本身有 name、description、inputSchema、outputSchema，并通过 `tools/list`、`tools/call` 暴露和调用；客户端应验证工具结果并记录工具调用。([Model Context Protocol][4])

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
skills/demo_psop_checklist/
  SKILL.md
```

`SKILL.md` 示例：

```markdown
---
name: demo_psop_checklist
description: 将一段现场作业描述拆解为检查项，并生成简体中文检查报告。
allowed-tools:
  - demo_extract_check_items
  - demo_score_checklist
  - memory_put
  - write_demo_report
---

# Demo PSOP Checklist Skill

你负责把用户输入的现场作业描述转换为检查清单。
必须调用 demo_extract_check_items。
必须调用 demo_score_checklist。
必须调用 memory_put。
最后将报告写入 /mnt/psop/workspace/result.md。
```

实现策略：

```text
1. 解析 SKILL.md frontmatter。
2. 记录 agent.skill.loaded event。
3. 将 skill instruction 拼接进 system prompt。
4. 将 skill 声明的 allowed-tools 作为权限收缩，不允许提升 agent.yaml 的工具授权。
5. 当前阶段只从仓库根目录 `skills/` 加载 Agent Skill，不支持 agent 私有 skills。
```

Agent Skill 采用 progressive disclosure：启动时只读取 `SKILL.md` frontmatter，需要时再通过 `load_skill` 读取完整内容。Skill 目录可以包含脚本、模板和参考文档。

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

## Step 7：实现 Harness Model Factory

这是整个计划的关键改造点。

Agent Harness 参考 deer-flow，不再要求 LLM 调用经过 `LlmInferenceGateway`，而是通过 LangChain model factory 直接构造 `BaseChatModel`。Runtime、Compiler 等既有链路可继续使用 `LlmInferenceGateway`。

新增：

```text
backend/app/agent_harness/models/factory.py
```

职责：

```text
1. 从 Settings 的 PSOP_LLM_* 生成默认 HarnessModelConfig。
2. 解析 use=module:Class 的 LangChain provider。
3. 构造 BaseChatModel。
4. 支持 thinking_enabled / when_thinking_enabled / when_thinking_disabled。
```

默认 provider 使用 `langchain_openai:ChatOpenAI`。

---

## Step 8：实现 Scripted ChatModel

新增：

```text
backend/app/agent_harness/models/scripted_chat_model.py
```

职责：

```text
LangChain BaseChatModel
  -> 支持 bind_tools()
  -> 固定触发 demo tool_calls
  -> 输出 usage_metadata，覆盖 token middleware
```

用于测试：

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

## Step 9：实现 Agent Factory + LangChain Executor

新增：

```text
backend/app/agent_harness/agents/demo/psop_harness_agent/agent.py
backend/app/agent_harness/agents/demo/psop_harness_agent/prompt.py
backend/app/agent_harness/runners/langchain_agent_executor.py
```

核心逻辑：

```python
def make_demo_agent(context: AgentBuildContext):
    model = context.create_model()
    tools = resolve_tools(...)
    middleware = build_middlewares(...)
    system_prompt = apply_prompt_template(...)
    return create_agent(model=model, tools=tools, middleware=middleware, system_prompt=system_prompt)
```

MVP 暂时不启用 subagents，不做 LangSmith，不做 human approval，不做长期 store backend。只要把主 agent、skill 渐进式披露、tool、memory 跑通。

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
5. 调用 agent factory 创建可执行 agent。
6. 通过 LangChainAgentExecutor 执行 agent 并归一化 AgentResult。
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
backend/app/agent_harness/agents/demo/psop_harness_agent/agent.yaml
backend/app/agent_harness/agents/demo/psop_harness_agent/system.md
```

`agent.yaml`：

```yaml
agent_key: demo.psop_harness_agent
version: v1
runner_kind: langchain_agent
factory: make_demo_agent
description: Demo agent for validating PSOP agent harness tools, skills, memory, sandbox, and middleware.
model:
  name: default
  thinking_enabled: false
system_prompt_file: system.md
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

所有面向用户的自然语言必须使用简体中文。
输出应包含：检查项数量、风险等级、报告路径。
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

默认使用真实 LLM，并读取 `PSOP_LLM_*` 配置，通过 LangChain model factory 创建模型。

pytest 中使用 `ScriptedToolCallingChatModel` 作为 fake `BaseChatModel` 注入，但仍必须经过 LangChain `create_agent`。

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
  "final_output": "已完成检查清单生成，共识别 3 个检查项，风险等级 medium，报告已写入 /mnt/psop/workspace/result.md。",
  "sandbox_path": ".psop/agent-runs/<agent_run_id>",
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

当前 `RuntimeService` 是 psop-runner-agent 的正式运行环境，会创建 `SkillInvocation`、`Run`、`TerminalSession`、`SessionTokenSnapshot` 和 `RuntimeJob`。 它的状态主权仍应保留，不在本次 MVP 中接入 Agent Harness。

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
- pyproject.toml 增加 langchain/langgraph/langchain-mcp-adapters
- Settings 增加 agent_harness_* 配置
- 增加 langchain-openai 作为默认 OpenAI-compatible model provider
```

## Commit 2：Harness core

```text
- schemas.py
- events.py
- local sandbox
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

## Commit 4：Model factory + Middlewares + Agent factory

```text
- LangChain model factory
- Agent Harness middlewares
- ScriptedToolCallingChatModel
- demo agent.py / prompt.py
- LangChainAgentExecutor
```

## Commit 5：Demo script + tests

```text
- demo agents/demo/psop_harness_agent/agent.yaml/system.md
- tests/run_agent_demo.py
- pytest e2e
```

---

# 十、最终验收标准

本计划当前已满足以下验收条件：

```text
1. backend/app/agent_harness/ 模块存在，并包含 tools、skills、memory、runner、model factory、sandbox、middlewares 基础组件。
2. 项目依赖中已引入 langchain、langgraph、langchain-openai。
3. 存在 demo.psop_harness_agent。
4. 可以通过 Python 脚本运行 demo agent。
5. demo 运行过程中能加载至少一个 skill。
6. demo 运行过程中能执行至少两个 tools。
7. demo 运行过程中能写入 memory。
8. demo 运行过程中能写入 sandbox workspace 文件。
9. demo 输出 AgentResult。
10. demo 产生 events.jsonl，可看到 skill/tool/memory/run 全链路事件。
11. pytest 中存在不依赖真实 LLM 的端到端测试。
12. 现有 compiler/runtime/skill_tests 主流程不被破坏。
```

---

# 十一、后续演进路径

Demo harness 已完成，后续进入第二阶段：

```text
1. psop-builder 接入 AgentHarnessService
2. psop-compiler 接入 AgentHarnessService
3. psop-tester 接入 AgentHarnessService
4. MCP provider 接入真实 MCP server
5. local sandbox 扩展为可替换的远程 sandbox provider
6. agent_run / agent_event 持久化到数据库
7. agent events 投影到 PSOP observability/replay
```

这个顺序可以在已完成的 demo 底座上逐步迁移真实业务智能体，同时继续避免破坏当前 PSOP 最重要的 runtime/publish/compile 主链路。

[1]: https://docs.langchain.com/oss/python/langchain/agents "Agents - Docs by LangChain"
[2]: https://docs.langchain.com/oss/python/langgraph/overview "LangGraph overview - Docs by LangChain"
[3]: https://docs.langchain.com/oss/python/langchain/tools "Tools - Docs by LangChain"
[4]: https://modelcontextprotocol.io/docs/concepts/tools "Tools - Model Context Protocol"
