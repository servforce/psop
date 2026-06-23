# PSOP Run Server外部项目调研与运行时架构建议

## 1. 文档定位

本文基于官方 GitHub 仓库与仓库直接链接的官方文档，对 `PSOP Run Server` 所涉及的运行时形态做外部对标调研，并在调研之后给出面向 PSOP 的明确架构建议。

本文不重新展开 `Skill -> Execution Graph` 编译体系，相关正式语义以：

- [Execution Graph 形式定义](../architecture/execution-graph-formal-v5.md)
- [系统架构设计](../architecture/system-architecture.md)

为准。

本文的检索基线日期为：`2026-04-20`。

---

## 2. 研究问题与结论先行

本文主要回答四个问题：

1. `Execution Graph Runtime` 应如何理解其执行宿主；
2. 每个执行图运行时是否应等同于一个 Linux 进程；
3. `Lead Agent / Runtime Kernel / Sandbox` 的边界如何划分；
4. PSOP 应从现有开源项目借鉴哪些运行时机制，避免哪些误区。

### 2.1 核心结论

**结论一：`Run` 是逻辑执行实例，不默认等同于 OS 进程。**

更准确地说：

- `Run` 是由 `Session Token` 驱动的正式事务实例；
- Linux 进程、容器、远程 sandbox、K8s pod 只是执行承载和隔离手段；
- 正式状态主权仍应属于 `Runtime Kernel + Session Token`，而不是某个 shell 进程或 agent 进程本身。

因此，PSOP 不应采用“一图一进程”作为基础定义。更稳妥的默认设计应是：

- **多 run 共享 worker 池**
- **按需分配 sandbox / process / container**
- **单 run 串行 formal commit**
- **状态与回放依赖持久化快照和 trace，而不是依赖进程内存存活**

### 2.2 推荐结论

结合外部项目，本文建议 PSOP 延续并强化以下定位：

1. `Runtime Kernel` 继续作为唯一状态主权者；
2. `Lead Agent` 继续作为图内建议者，而不是正式状态提交者；
3. `Capability Host` 继续作为 `MCP / Skills / Code / Sandbox / API` 的受控入口；
4. `Run`、`Worker`、`Sandbox` 必须分层，而不是混成“一个 agent 进程包打天下”；
5. `Sync -> Enabled -> Sel -> Actor -> Merge -> Trace` 应继续作为正式推进闭环；
6. 对高风险执行，应优先采用“共享调度 + 按需隔离”的模型，而不是把所有运行实例都直接抬升为独立进程。

---

## 3. 资料范围与筛选标准

本文只采用以下资料作为主证据：

- 官方 GitHub 仓库 README
- 仓库内官方文档入口
- 仓库 README 直接链接的官方文档

不采用社区二手博客、论坛帖子、营销文章作为主证据。

本文观察这些项目，不是为了照搬，而是为了回答不同子问题：

- `bytedance/deer-flow`、`langchain-ai/deepagents`：看“super agent harness / batteries-included harness”怎么组织 sub-agents、memory、sandbox、skills
- `langchain-ai/langgraph`：看“stateful / durable / human-in-the-loop”运行时基础设施
- `microsoft/agent-framework`：看 graph workflow、checkpoint、time-travel、middleware、hosting
- `agno-agi/agno`：看 session-scoped runtime、approval、audit、production API
- `temporalio/temporal`：看 durable execution 该如何把长事务从进程生命周期中解耦
- `modelcontextprotocol/modelcontextprotocol`、`modelcontextprotocol/servers`：看 capability boundary 应如何协议化与受控化
- `OpenHands/OpenHands`、`e2b-dev/E2B`、`daytonaio/daytona`：看 sandbox / execution environment / full computer 模型
- `crewAIInc/crewAI`、`langflow-ai/langflow`：看更偏应用编排与 flow builder 的设计取向

---

## 4. 项目分组调研

## 4.1 Agent Harness / Graph Runtime

### 4.1.1 DeerFlow

官方描述中，DeerFlow 2.0 已明确把自己定位为 **super agent harness**，并强调：

- sub-agents
- memory
- sandboxes
- extensible skills
- built on LangGraph and LangChain

同时它把 filesystem、sandbox-aware execution、skills、sub-agent planning 作为“agent 自带基础设施”的一部分，而不是单独外挂。  
来源：

- https://github.com/bytedance/deer-flow
- https://github.com/bytedance/deer-flow/blob/main/README.md

对 PSOP 的启发：

- “agent harness” 这个术语非常适合描述 `Run Server` 的大框架层；
- sandbox、memory、skills 的确应该被纳入 harness 语义，而不是散落在工具层；
- 但 DeerFlow 的运行时更偏“通用 agent 工作台”，PSOP 仍然需要比它更强的 formal state ownership。

### 4.1.2 LangGraph

LangGraph 明确把自己定义为 **low-level orchestration framework for building stateful agents**，核心卖点包括：

- durable execution
- human-in-the-loop
- short-term / long-term memory
- long-running, stateful agents

来源：

- https://github.com/langchain-ai/langgraph
- https://github.com/langchain-ai/langgraph/blob/main/README.md

对 PSOP 的启发：

- “长时运行 + 显式状态 + 可恢复 + 可人工介入”是 agent runtime 的硬需求；
- PSOP 当前把 `Session Token` 作为一等对象，这一点与 LangGraph 的 stateful runtime 思路高度同向；
- 但 PSOP 比 LangGraph 更进一步，要求 formal commit 必须通过 `Runtime Kernel` 完成。

### 4.1.3 Deep Agents

Deep Agents 把自己定义为 **batteries-included agent harness**，内置：

- planning
- filesystem
- shell access
- sub-agents
- context management
- sandboxing
- persistent memory
- human-in-the-loop approval

并明确说明其底层返回的是编译后的 LangGraph graph。  
来源：

- https://github.com/langchain-ai/deepagents
- https://github.com/langchain-ai/deepagents/blob/main/README.md

对 PSOP 的启发：

- harness 层要天然集成 planning、files、shell、sub-agent、memory，而不是靠业务方自己拼；
- Deep Agents 明确提出 “边界应在 tool/sandbox 层 enforced，而不是寄希望于模型自律”，这和 PSOP 的 `Capability Host + Guardrails` 设计高度一致；
- 但它仍以通用 coding/research agent 为主，不提供 PSOP 所需的 formal graph state discipline。

### 4.1.4 Microsoft Agent Framework

Microsoft Agent Framework 在官方 README 中强调：

- graph-based workflows
- streaming
- checkpointing
- human-in-the-loop
- time-travel
- middleware
- observability

并把 hosting、workflow、agent provider、DevUI 组织成完整开发与运行体系。  
来源：

- https://github.com/microsoft/agent-framework
- https://github.com/microsoft/agent-framework/blob/main/README.md

对 PSOP 的启发：

- `checkpoint` 与 `time-travel` 说明运行时必须把“可恢复的推进点”当成一等概念；
- middleware 和 hosting 层说明 agent runtime 不应只有 prompt/工具调用，还应有正式的 execution pipeline；
- 但其 graph workflow 更偏通用编排框架，PSOP 仍应坚持 `Session Token` 中心化建模。

### 4.1.5 Agno

Agno 在官方 README 中把自己拆成：

- Framework
- Runtime
- Control Plane

并强调：

- stateless, session-scoped FastAPI runtime
- approval workflows
- runtime enforcement
- traces and audit logs
- per-user / per-session isolation

来源：

- https://github.com/agno-agi/agno
- https://github.com/agno-agi/agno/blob/main/README.md

对 PSOP 的启发：

- `Runtime` 与 `Control Plane` 分层非常值得借鉴；
- approval / audit / runtime enforcement 都应是 engine 能力，而非业务补丁；
- 但 Agno 的“stateless runtime”更偏 Web API 风格，PSOP 还需要更强的 formal replay / token version 语义。

## 4.2 Durable Execution / Workflow Engine

### 4.2.1 Temporal

Temporal 官方将自己定义为 **durable execution platform**，强调：

- server executes workflows resiliently
- automatically handles intermittent failures
- retries failed operations

并明确把 workflow / worker / server 区分开。  
来源：

- https://github.com/temporalio/temporal
- https://github.com/temporalio/temporal/blob/main/README.md

对 PSOP 的启发：

- 长事务执行不能绑死在某个进程内存里；
- `Run` 和 `Worker` 必须分离；
- checkpoint / retry / replay 应依赖 durable state，而不是依赖 agent 进程持续在线。

这也是本文反对“一图一进程”成为基础定义的最关键外部参照。

## 4.3 Capability Boundary / MCP

### 4.3.1 MCP Specification

`modelcontextprotocol/modelcontextprotocol` 官方仓库提供：

- MCP specification
- protocol schema
- official documentation

来源：

- https://github.com/modelcontextprotocol/modelcontextprotocol
- https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/README.md

### 4.3.2 MCP Reference Servers

`modelcontextprotocol/servers` 官方仓库强调：

- reference implementations
- demonstrate MCP features and SDK usage
- not production-ready solutions
- secure, controlled access to tools and data sources

来源：

- https://github.com/modelcontextprotocol/servers
- https://github.com/modelcontextprotocol/servers/blob/main/README.md

对 PSOP 的启发：

- `MCP` 适合作为 capability protocol，不适合作为 formal state protocol；
- 生产环境仍需要 `Capability Host` 二次封装、权限、审计、策略校验；
- “secure, controlled access” 与 PSOP 当前的能力边界理念一致；
- reference server 不等于 production runtime，这一点要在 PSOP 文档中保持清醒。

## 4.4 Sandbox / Execution Environment

### 4.4.1 OpenHands

OpenHands 把自己拆为：

- Software Agent SDK
- CLI
- Local GUI
- Cloud
- Enterprise

并明确支持本地、云端和企业私有化承载。  
来源：

- https://github.com/OpenHands/OpenHands
- https://github.com/OpenHands/OpenHands/blob/main/README.md

对 PSOP 的启发：

- agent engine、CLI、GUI、Cloud 可以共享同一个 agent runtime 内核，但控制面和执行面必须分离；
- 这与 PSOP 中 `Web IDE / Run Server / Android App` 的职责分层是一致的。

### 4.4.2 E2B

E2B 官方把自己定义为：

- open-source infrastructure
- run AI-generated code in secure isolated sandboxes in the cloud

来源：

- https://github.com/e2b-dev/E2B
- https://github.com/e2b-dev/E2B/blob/main/README.md

对 PSOP 的启发：

- sandbox 应被视为基础设施能力，而不是某个 tool 的附属功能；
- “AI-generated code execution” 的隔离边界应下沉到专门运行环境，而不是混在主 runtime 进程里。

### 4.4.3 Daytona

Daytona 官方将自己定义为：

- secure and elastic infrastructure runtime for AI-generated code execution and agent workflows
- sandboxes are full composable computers
- complete isolation
- dedicated kernel / filesystem / network stack
- snapshots for persistent agent operations across sessions

来源：

- https://github.com/daytonaio/daytona
- https://github.com/daytonaio/daytona/blob/main/README.md

对 PSOP 的启发：

- “sandbox = full computer” 的抽象很适合高风险 `tool/code/skill` 执行；
- snapshots 非常适合作为 PSOP 后续 `sandbox checkpoint / replay` 的参考；
- Daytona 同时有 interface plane / control plane / compute plane，这与 PSOP 的 `Terminal Gateway / Runtime Kernel / Worker & Sandbox` 分层高度契合。

## 4.5 轻量补充对标

### 4.5.1 CrewAI

CrewAI 区分：

- Crews：偏自治协作
- Flows：偏事件驱动、精确控制、生产架构

来源：

- https://github.com/crewAIInc/crewAI
- https://github.com/crewAIInc/crewAI/blob/main/README.md

对 PSOP 的启发：

- “autonomy” 与 “precision” 需要分层而不是混写；
- 对 PSOP 来说，`Lead Agent` 更接近 autonomy 层，`Runtime Kernel` 更接近 precision 层。

### 4.5.2 Langflow

Langflow 强调：

- visual authoring
- API server
- MCP server
- flows turned into tools

来源：

- https://github.com/langflow-ai/langflow
- https://github.com/langflow-ai/langflow/blob/main/README.md

对 PSOP 的启发：

- flow builder、API、MCP server 非常适合作为设计面/接入面；
- 但 Langflow 更适合“把流程暴露成工具”，不适合作为 PSOP formal runtime 的直接原型。

---

## 5. 对比矩阵

| 项目 | 状态模型 | graph / orchestration | durability / checkpoint | human-in-the-loop | tool / MCP boundary | sandbox | session isolation | runtime topology | 适合借鉴 | 不应直接照搬 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DeerFlow | harness 内部上下文与 memory | sub-agents + skills | 有长任务与上下文压缩，但 formal state 弱于 workflow engine | 支持 | 支持 MCP 与 skills | 强 | 强 | harness + gateway + sandbox | super agent harness 组织方式 | 其 agent-first 语义不能替代 PSOP formal state |
| LangGraph | stateful agent state | graph orchestration | 强 | 强 | 工具边界依赖集成层 | 中 | 中 | graph runtime | durable execution / memory / HITL | 不能直接当 PSOP formal token model |
| Deep Agents | batteries-included agent state | compiled graph | 强 | 强 | 支持 MCP adapters | 强 | 强 | harness + sandbox | 内建 planning/files/shell/sub-agents | trust-the-LLM 取向需要 PSOP 加强 guardrails |
| Microsoft Agent Framework | workflow state | graph workflow | 强 | 强 | middleware / providers | 中 | 中 | framework + hosting + DevUI | checkpoint / time-travel / middleware | 通用框架不等于 PSOP 执行核 |
| Agno | session-scoped state | agents / teams / workflows | 中强 | 强 | MCPTools | 中 | 强 | framework + runtime + control plane | runtime / control plane 分层 | stateless API 风格不足以覆盖 PSOP formal replay |
| Temporal | workflow state | workflow + worker | 极强 | 间接支持 | 非 agent capability 协议 | 无内建 | 强 | server + worker | durable execution 心智模型 | 不提供 agent/harness 语义 |
| MCP spec + servers | 无 formal task state | 无 | 无 | 无 | 极强 | 无 | 无 | protocol + reference servers | capability 协议化边界 | 不可把 MCP 当成生产 runtime 内核 |
| OpenHands | agent task state | agentic workflow | 中 | 有 | SDK / integrations | 强 | 中强 | SDK + CLI + GUI + cloud | 控制面/执行面分离 | 偏 coding-agent，不是 formal graph runtime |
| E2B | sandbox state | 无 | 中 | 无 | SDK API | 极强 | 强 | infra + SDK | 独立 sandbox 基础设施 | 不提供 formal orchestration |
| Daytona | sandbox + snapshots | execution environment | 强 | 中 | SDK / API / MCP server | 极强 | 强 | interface / control / compute planes | full computer sandbox + snapshots | 计算平面不等于事务状态主权 |
| CrewAI | crew / flow state | autonomy + event-driven flows | 中 | 有 | 工具集成 | 弱到中 | 中 | framework + control plane | autonomy/precision 分层心智 | 宣传式比较不应直接采信为架构事实 |
| Langflow | flow state | visual workflow | 中 | 有 | API + MCP server | 弱 | 中 | builder + runtime surface | 可视化与接入面 | 不适合作 formal execution kernel |

---

## 6. 面向 PSOP 的运行时架构建议

## 6.1 一句话总建议

`Run Server` 应被定义为：

> 一个以 `Session Token` 为唯一正式状态、以 `Runtime Kernel` 为唯一状态主权者、以 `Lead Agent` 为图内建议者、以 `Worker + Sandbox` 为执行承载、以 `Capability Host` 为能力边界、以 `Trace + Replay` 为可解释性基础的 `Harness Runtime`。

## 6.2 不采用“一图一进程”的原因

如果把每个执行图运行时直接定义成一个 Linux 进程，会带来三个问题：

1. **错误地把逻辑实例与承载实例绑定。**  
   `Run` 的本体应是 `Session Token` 演化链，而不是某个 PID。

2. **恢复与回放会被进程生命周期绑架。**  
   一旦进程崩溃，如果 formal state 主要在进程内存里，系统就难以满足 PSOP 的 replay / audit 目标。

3. **无法优雅区分“共享调度”和“高风险隔离”。**  
   某些 run 只需要共享 worker，某些节点才需要独立 sandbox / process / container；“一刀切一图一进程”会把成本抬得过高。

## 6.3 推荐分层：Run、Worker、Sandbox

建议把执行层拆成三层：

### （1）Run

- 逻辑事务实例
- 一等对象是 `Session Token`
- 正式推进由 `Runtime Kernel` 完成
- 串行 formal commit

### （2）Worker

- 负责执行选中的节点实例
- 可承载多个 run 的非隔离型任务
- 可与模型服务、能力宿主、上下文编译器协作

### （3）Sandbox

- 按需分配给高风险或高隔离需求的节点
- 可表现为本地进程、容器、远程 runtime、K8s pod
- 负责 shell、文件系统、代码执行、多媒体预处理等危险或重资源操作

于是推荐拓扑是：

```text
Run Server
-> Runtime Kernel
-> Run Scheduler / Pick / Sel
-> Worker Pool
-> Sandbox Manager
-> Capability Host
-> State Store + Trace Store + Object Store
```

而不是：

```text
一个 Execution Graph = 一个 Linux 进程 = 一个 agent runtime
```

## 6.4 `Lead Agent` 与 `Runtime Kernel` 的边界

建议继续坚持你现有文档中的边界，不做退让：

- `Lead Agent` 负责：
  - 候选评估
  - retrieval 计划
  - capability 选择建议
  - projection / compaction 建议
  - terminal presentation 建议
- `Lead Agent` 不负责：
  - 修改 graph 结构
  - 引入图外正式节点
  - 直接写 `Session Token`
  - 跳过审批与权限检查
- `Runtime Kernel` 负责：
  - `Sync -> Enabled -> Sel -> Actor -> Merge -> Trace`
  - formal state commit
  - policy / approval / budget enforcement
  - replay 事实沉淀

这与 Deep Agents 的 “工具边界必须靠 tool/sandbox enforce”，以及 Agno 的 runtime approval enforcement，是相同方向；同时也保留了 PSOP 比这些框架更强的 formal state discipline。

## 6.5 `MCP / Skills` 的接入原则

建议维持：

- `MCP / Skills` 是能力协议，不是状态协议
- agent 不直接连生产能力
- 所有能力调用必须经 `Capability Host`
- `Capability Host` 负责：
  - catalog / discovery
  - allowlist / policy check
  - auth / approval / budget
  - structured result normalization
  - traceable binding record

MCP 官方仓库本身已经明确 reference servers 不是 production-ready solution，因此 PSOP 必须在其上再包一层生产级 host。

## 6.6 推荐进程模型

推荐把进程模型写成如下结论：

### 默认模式

- 多 `Run` 共享 `Runtime Kernel` 进程
- 多 `Run` 共享 `Worker Pool`
- 单 `Run` 串行正式提交
- 非高风险节点尽量在共享 worker 中执行

### 按需隔离模式

以下场景可升级为独立执行承载：

- shell / code 执行
- 高风险 MCP / skill
- 文件系统重写
- 多媒体重算
- 资源占用显著的长时任务

隔离承载可按成本从低到高依次选择：

1. 进程级隔离
2. 容器级隔离
3. 远程 sandbox
4. 专用 runner / pod

### 不建议作为默认模式

- 一图一进程
- 一 run 一容器
- 一 run 一整套 agent runtime

除非该 run 被显式标记为高风险、高资源、强隔离任务。

---

## 7. 风险与开放问题

当前仍需继续专题化设计的问题包括：

### 7.1 进程级隔离成本

- 如果高风险节点频繁触发独立 sandbox，调度成本和冷启动成本会快速上升；
- 需要单独设计 sandbox warm pool、snapshot 恢复与资源配额。

### 7.2 长会话恢复与 checkpoint

- 需要明确 `Run checkpoint` 与 `Sandbox snapshot` 的关系；
- 二者不能混为同一个“恢复点”概念。

### 7.3 多模态 I/O 与终端协议

- 文本、音频、视频、图像、实时流输入应继续归入统一 terminal protocol；
- 但重媒体处理是否进入 shared worker，还是必须进入 sandbox，还要进一步分级。

### 7.4 agent 建议与 formal commit 冲突

- 当 `Lead Agent` 给出的 capability / retrieval / selection 建议与 policy 冲突时，必须由 `Runtime Kernel` 否决并留下 trace；
- 这类冲突本身应成为 replay 的一等可见对象。

---

## 8. 最终建议

结合外部项目，本文给出 PSOP 的最终运行时建议如下：

1. `PSOP Run Server` 的核心身份继续定义为 `Harness Runtime / EG 执行器`。
2. `Run` 是逻辑实例，不默认等同 Linux 进程。
3. `Runtime Kernel` 继续是唯一状态主权者，`Lead Agent` 继续是图内建议者。
4. 采用 `Run -> Worker -> Sandbox` 三层分离，而不是“一图一进程”。
5. 默认多 run 共享 worker，按需分配 sandbox / process / container。
6. `MCP / Skills` 必须继续通过 `Capability Host` 受控接入。
7. 回放、恢复、审计应依赖 `Session Token` 版本链与 `Trace`，而不是依赖进程存活。

这一路线既能吸收 DeerFlow、LangGraph、Deep Agents、Temporal、Daytona 等项目的长处，又不会牺牲 PSOP 现在最有价值的 formal state ownership。

---

## 9. 参考来源

以下链接均为官方 GitHub 仓库或其 README 直接给出的官方文档入口，检索日期均为 `2026-04-20`：

- DeerFlow: https://github.com/bytedance/deer-flow
- LangGraph: https://github.com/langchain-ai/langgraph
- Deep Agents: https://github.com/langchain-ai/deepagents
- Microsoft Agent Framework: https://github.com/microsoft/agent-framework
- Agno: https://github.com/agno-agi/agno
- Temporal: https://github.com/temporalio/temporal
- MCP Specification: https://github.com/modelcontextprotocol/modelcontextprotocol
- MCP Reference Servers: https://github.com/modelcontextprotocol/servers
- OpenHands: https://github.com/OpenHands/OpenHands
- E2B: https://github.com/e2b-dev/E2B
- Daytona: https://github.com/daytonaio/daytona
- CrewAI: https://github.com/crewAIInc/crewAI
- Langflow: https://github.com/langflow-ai/langflow
