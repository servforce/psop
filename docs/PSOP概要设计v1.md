# PSOP 概要设计 v1

## 1. 文档定位

本文是 `PSOP` 的工程概要设计文档，目标是给出系统分层、模块边界与阶段性落地路径。

本文不替代：

- [PSOP Run Server外部项目调研与运行时架构建议.md](./PSOP%20Run%20Server%E5%A4%96%E9%83%A8%E9%A1%B9%E7%9B%AE%E8%B0%83%E7%A0%94%E4%B8%8E%E8%BF%90%E8%A1%8C%E6%97%B6%E6%9E%B6%E6%9E%84%E5%BB%BA%E8%AE%AE.md)
- [PSOP_execution_graph_formal_v5.md](./PSOP_execution_graph_formal_v5.md)
- [PSOP-Whitepaper-v3.md](./PSOP-Whitepaper-v3.md)

其中：

- `PSOP_execution_graph_formal_v5.md` 是 `EG` 的形式定义
- `Skills` 的编译产物必须符合这一定义
- `Runtime` 也必须围绕这一定义推进 `EG` 的执行

## 2. 当前阶段的对象边界

当前阶段必须明确三层对象：

1. `Skills`
   - 用户在 `Web IDE` 中定义、编辑、发布的现实世界任务契约
2. `EG`
   - `Skills` 发布后自动编译得到的形式化执行图
3. `Runtime`
   - 加载某个 `Skill` 对应 `EG` 并推进执行的宿主

因此：

- 用户定义的不是 `EG source`
- 用户定义的是 `Skills`
- `EG` 是编译对象，不是用户主编辑对象
- `Skill` 不是聊天 prompt，也不是一次输入后自动完成的任务脚本；它描述的是如何帮助用户在现实世界中完成某个目标

## 3. 三阶段主线

### 3.1 运行前

- `Web IDE` 构建 `Skills`，沉淀目标、适用边界、现实步骤、证据要求、安全约束、恢复路径和完成标准
- `Skills` 发布后自动编译出 `EG`
- 编译产物必须满足形式定义

### 3.2 运行时

- 用户通过 `Gateway` 选择某个 `Skill` 并建立受控运行连接
- server 加载该 `Skill` 对应的 `EG`
- `Runtime Kernel` 以 `Session Token` 为唯一正式状态，主动引导用户执行现实步骤、等待现场证据、评估证据并推进或终止

### 3.3 运行后

- `Web IDE` 可实时观测运行中的 `Skills`
- `Web IDE` 可查看已执行完成 `Skills` 的运行历史
- 观测基础是 `Trace / Replay / OpenTelemetry`

## 4. 设计目标与核心原则

### 4.1 设计目标

`PSOP v1` 当前阶段要先回答三个问题：

1. 如何让用户在 `Web IDE` 中稳定构建和发布 `Skills`
2. 如何把已发布 `Skills` 自动编译为符合形式定义的 `EG`
3. 如何让 `Runtime Kernel` 围绕该形式定义稳定推进执行与观测闭环

### 4.2 核心原则

1. `Skills` 是用户创作对象，`EG` 是编译对象
2. `Session Token` 是唯一正式状态对象
3. `Runtime Kernel` 是唯一状态主权者
4. PSOP 只有一种运行范式：`Real-World Assisted Execution`，即现实世界协作执行
5. `Lead Agent` 只做建议，不做正式状态提交
6. `Agent Prompt Assets` 按智能体职责和产品场景版本化管理，行业差异通过 `Domain Pack` 注入，不作为第一层模块边界
7. `Run != OS 进程`，执行层采用 `Run -> Worker -> Sandbox` 分层
8. `MCP` 是能力协议，不是状态协议
9. `OpenTelemetry + Trace/Replay` 统一承载运行时观测闭环

## 5. 总体架构

### 5.1 一句话定义

`PSOP v1` 应被定义为：

> 一个以 `Skills` 为现实世界任务契约、以 `PSOP-EG` 为编译后控制核、以 `Session Token` 为唯一正式状态、以 `Runtime Kernel` 为唯一状态主权者、以 `Gateway` 为 skill invocation 入口、以 `Terminal Gateway` 为持续现场交互入口、以 `OpenTelemetry + Trace/Replay` 为观测闭环的 `Skill Runtime + Web IDE Control Plane`。

### 5.2 六层视图

```mermaid
flowchart TB
    subgraph Web["Web IDE / Control Plane"]
        SkillStudio["Skill Studio"]
        RuntimeUI["Runtime Monitor"]
        ReplayUI["Replay"]
        ObsUI["Observability"]
    end

    subgraph Authoring["Skills / Compile Layer"]
        SkillRepo["Skills Module"]
        GitLabRepo["GitLab Skill Repository"]
        Compiler["Skill Compiler"]
        EGArtifact["EG Compile Artifact"]
    end

    subgraph Runtime["Runtime Kernel Layer"]
        RuntimeKernel["Runtime Kernel"]
        Scheduler["Sync / Enabled / Sel / Actor / Merge / Trace"]
    end

    subgraph Agents["Agent Layer"]
        AgentModule["Agent Module"]
        CapabilityHost["Capability Host"]
        PromptAssets["Agent Prompt Assets"]
        DomainPacks["Domain Packs"]
    end

    subgraph Exec["Worker / Sandbox Layer"]
        WorkerPool["Worker Pool"]
        SandboxMgr["Sandbox Manager"]
    end

    subgraph Store["State / Trace / Object / OTel"]
        StateStore["State Store"]
        TraceStore["Trace Store"]
        ObjectStore["Object Store"]
        OTel["OpenTelemetry"]
    end

    Web --> Authoring
    Authoring --> Runtime
    Web --> Runtime
    Web --> GitLabRepo
    Runtime --> Agents
    AgentModule --> PromptAssets
    PromptAssets --> DomainPacks
    Agents --> Exec
    Runtime --> Store
    Exec --> Store
    Web --> OTel
```

## 6. 三块核心设计

### 6.1 运行时环境

`PSOP v1` 的运行时环境不是单个 agent 进程，而是一个分层的 `Agent Runtime`：

- `Run` 是逻辑事务实例，其本体是 `Session Token` 演化链
- `Runtime Kernel` 是正式执行器，负责推进符合形式定义且面向现实世界协作执行的 `EG`
- `Worker Pool` 承载普通节点执行
- `Sandbox Manager` 按需为高风险节点提供隔离环境
- `State Store + Trace Store + Object Store` 负责恢复、回放与对象证据存储
- `Agent Module` 是 PSOP 的智能体能力层，负责 sub-agent、memory、planning 与 tool-use orchestration
- `Agent Prompt Assets` 是智能体提示词、输入模板、输出约束和测试样例的版本化控制面资产；当前阶段由 DB 管理 draft/published/active 版本，repo 中 `backend/app/agents/*` 仅作为初始化种子与故障兜底
- `Domain Packs` 是行业术语、流程模式、质量标准和安全边界的可选增强包，不改变正式 `Skill -> EG -> Runtime` 主链路
- `DeerFlow` 可以作为可借鉴或可复用的 harness 参考实现，但不是产品级模块边界，也不是正式状态主权者

### 6.2 Gateway / 输入输出模拟器

当前阶段 `Gateway` 同时承担两类职责：

1. `Skill Invocation` 入口
2. 输入输出模拟器

它包含三个子层：

- `Terminal Gateway`
  - 当前阶段承接文本、图像、语音、视频等输入输出模拟，供 `Web IDE` 驱动 skill 运行
  - 不是普通命令行终端，而是 Web、模拟器、IoT、AR 与真实设备输入输出的统一运行时交互入口
  - `Invocation` 只负责创建受控运行会话并提交 `terminal_context`、环境与能力信息；真实用户输入、多模态文件和设备读数必须作为后续 `terminal_event` 进入
  - Runtime 建立连接后可以先输出任务摘要、安全边界、准备事项和第一步可执行指令，再进入等待证据状态
  - 用户反馈、现场证据、设备数据、系统中间指导和最终结果都必须归一为 append-only `terminal_event`
  - 所有终端输入输出最终归一为 append-only `terminal_event`，由 RuntimeKernel 在 `Sync -> Merge` 中决定是否进入正式 `Session Token`
- `MCP Gateway`
  - discovery、policy、allowlist、budget、结果归一化、调用审计
- `LLM Inference Gateway`
  - 模型路由、provider 抽象、结构化输出、tool calling 代理、配额与回退

### 6.3 WEB 端管理界面

`WEB` 端不是单页 IDE，而是 `Web IDE + Runtime Console` 的组合：

- `Skill Studio`
  - skill 创建、编辑、版本和发布
- `Publish & Diagnostics`
  - 查看发布结果、编译诊断和 artifact 信息
- `Runtime Monitor`
  - 查看运行中的 skills、终端输入输出和当前执行状态
- `Replay`
  - 回看已执行完成 skills 的 trace、token 快照和对象证据
- `Skill Test`
  - 针对当前 skill 维护黑盒时序测试场景、场景资源、场景运行与语义评估记录
  - 测试场景在相对时间轴上编排文本、图片、音频、视频等多信道输入，并在语义输出信道上定义期望
  - 测试执行不是模拟 Runtime，而是创建真实 invocation/run/terminal session，按时间轴把输入追加为真实 `terminal_event`，并复用 Runtime Kernel、Replay 与 OTel
  - 语义输出判断以期望事件所在时间点为切面，只判断该时间点以前的真实 terminal output 是否满足期望
- `Agent Prompts`
  - 管理平台级 Agent Prompt Pack、版本、发布与启用绑定
  - 覆盖 skill 创建、skill 编译、runtime execution、skill test judge 等职责场景
  - Prompt Pack 不拥有 Runtime 正式状态主权；每次调用只通过版本、hash 与 binding metadata 进入审计链路
- `Observability`
  - 查看 OTel traces、metrics、logs、异常拓扑与慢调用

## 7. 当前阶段方案与演进目标

### 7.1 当前阶段方案

- `Web IDE` 同时承担 Skill Studio 与运行观测控制台职责
- `Skills Module` 负责 skill 定义、GitLab 绑定、版本与发布；`Compiler` 只负责编译
- `GitLab` 是 `skill source` 的正式事实源
- Agent 提示词按 `skill_creation`、`skill_compilation`、`runtime_execution`、`skill_test` 等职责分类保存；DB 中的 active binding 是运行时解析事实源，repo-backed 文件只用于 bootstrap/fallback；行业知识通过 `generic`、`industrial_inspection`、`equipment_maintenance` 等 `Domain Pack` 注入
- `Skills` 发布后自动编译出现实世界协作执行 `EG`，不生成一次输入后自动完成的纯线性执行图
- `Runtime Kernel` 只加载 compile artifact，不直接解释 skill 源码
- `Gateway` 统一承接 invocation、模拟 I/O 和外部能力接入
- `terminal_event` 是终端交互事实源；WebSocket、MQTT、OPC-UA、设备 adapter 与内部 bus 只负责传输、接入或唤醒，不拥有正式状态
- `binding` 把 artifact 中的抽象能力需求解析为本次 run 的具体终端、设备、channel、schema 与 policy；skill 不应写死具体设备实例
- `OpenTelemetry` 在 v1 就接入，至少建立 `skill -> compile -> invocation -> run -> trace` 的关联链路

### 7.2 演进目标

- `Web IDE` 与 `Control Plane Console` 后续可拆分
- `Terminal Gateway` 从“模拟优先”过渡到“真实设备接入优先”
- `Inference Gateway` 逐步支持多模型、多 provider、配额和回退
- `Replay` 与 `Observability` 从基础检索升级到问题定位和性能分析工作台

## 8. 核心接口 / 类型

- `Skill Definition`
  - 用户在 Web IDE 中定义的现实世界任务契约，描述目标、适用边界、步骤、证据、安全约束和完成标准
- `Skills Module`
  - 负责 skill 定义、版本、GitLab 绑定与发布
- `EG Compile Artifact`
  - 符合 `PSOP-EG` 形式定义的运行时输入对象，必须表达引导、等待、证据评估、恢复和最终验证
- `Agent Module`
  - 负责智能体相关的运行时组织与 harness 抽象
- `Agent Prompt Assets`
  - 按职责/场景版本化保存智能体提示词、模板、schema、示例和测试
- `Domain Pack`
  - 为 Skill 创建、编译和运行时 LLM 节点提供行业上下文增强，不拥有状态主权
- `Runtime Kernel`
  - 唯一正式执行与状态提交边界
- `Session Token`
  - 唯一正式状态快照对象
- `Terminal Gateway`
  - 统一 Web 模拟 I/O 与未来真实设备输入协议
- `Terminal Event`
  - run 内 append-only 的终端输入输出事件，是用户反馈、现场证据、系统指导、terminal transcript、Replay 与 Runtime `Sync` 的事实源
- `Run Capability Binding`
  - 本次 run 的能力解析结果，把 `terminal`、设备、MCP、LLM、sandbox 等抽象需求绑定到具体受控目标
- `Skill Test Scenario`
  - skill 级黑盒时序测试场景，包含 `duration_ms`、多信道 `timeline`、语义输出期望、场景资源引用、Judge 策略与可选 fork seed
- `Skill Test Asset`
  - 场景级图片、音频、视频等测试资源引用，底层二进制内容进入对象存储，并由 `artifact_object` 统一索引
- `Skill Test Scenario Run`
  - 某个 scenario 的一次真实执行，关联真实 invocation/run/terminal transcript/replay，记录时间轴 driver 状态、输入发送事实与结果摘要
- `Skill Test Expectation Evaluation`
  - 针对语义输出期望的 Judge 评估记录，保存 `passed / failed / inconclusive`、置信度、证据引用、理由与原始响应
- `MCP Gateway`
  - 统一 MCP server/tool 受控接入
- `LLM Inference Gateway`
  - 统一模型 provider、路由、回退与结构化输出

## 9. 典型闭环

一条典型主链路如下：

1. 用户在 `Web IDE` 中创建一个 `Skill`，并将其绑定到 GitLab 仓库
2. 用户发布某个 skill version 时，系统冻结对应 Git revision 并自动编译，生成符合形式定义的 `EG Compile Artifact`
3. 用户在终端选择某个 `Skill`，通过 `Gateway` 创建 `Skill Invocation`
4. `Runtime Kernel` 加载该 artifact 并创建 `Run + Session Token + Terminal Session`
5. `Runtime Kernel` 主动输出任务摘要、安全边界、准备事项和第一步可执行指令
6. `Run` 进入合法等待状态，等待用户文本、图片、视频、文件、传感器或设备反馈等现场证据
7. `Terminal Gateway` 将现场证据追加为 `terminal_event`
8. `Runtime Kernel` 在 `Sync` 阶段消费新事件，将证据绑定到当前等待 checkpoint
9. Runtime actor 根据当前 `EG` 和 `Session Token` 判断证据是否满足步骤完成标准，并进入下一步、要求补充证据、重试、终止或最终验证
10. 完成标准被验证后才进入 `terminal success`
11. `Trace Store + Replay + OTel` 提供运行中与运行后的统一观测

## 10. 小结

当前阶段的 PSOP，不是一个“用户直接画 EG 的系统”，也不是一个“一次输入后自动回答”的系统，而是一个“用户构建现实世界任务 Skill，系统自动编译为协作执行 EG，再由 Runtime 引导用户在真实环境中完成任务并观测回放”的系统。
