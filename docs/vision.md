# PSOP Vision

## 1. 文档定位

本文定义 PSOP 在当前阶段之后的产品愿景与系统演进方向。它不是对已经落地代码的逐项说明，而是用于统一后续架构、产品、智能体和工程实现的目标文档。

PSOP 的长期目标不是再做一个“会调用大模型的工作流系统”，也不是一个普通聊天式 Agent 平台。PSOP 要构建的是一套面向现实物理世界现场作业的 **AI Skill 操作系统**：把现场作业知识、行业标准、专家经验、运行证据、安全约束、测试反馈、审计归因和系统迭代全部纳入可编译、可执行、可回放、可审计、可持续改进的治理闭环。

当前 `issue-1-psop-mvp` 分支已经形成了 `Skills -> Publish -> Auto Compile -> Invocation -> Runtime -> Replay / Observability` 的 MVP 主链路。下一阶段的愿景是在这条主链路之上，引入统一的 `Agent Harness`，构建 PSOP 的整体智能体治理框架，服务完整业务目标：

```text
Build -> Compile -> Test -> Run -> Audit -> Eval -> Improve
```

## 2. 核心产品判断

PSOP 的核心对象不是 prompt，不是一次性脚本，也不是普通 workflow。PSOP 的核心对象是 `PSOP Skill`。

`PSOP Skill` 是一个现实世界任务契约。它描述作业目标、适用边界、现场步骤、证据要求、安全约束、异常恢复路径和完成标准。系统将 Skill 编译为正式的 `PSOP Execution Graph`，再由受控运行时智能体 `psop-runner` 引导现场人员完成真实作业。

因此，PSOP 必须同时具备三类能力：

1. **构建能力**：从视频、关键帧、转写文本、行业标准、专家经验中生成可维护的 pskill。
2. **执行能力**：把 pskill 编译为可运行的 PSOP-EG，并在 runtime 中安全推进真实作业。
3. **治理能力**：通过测试、运行、回放、审计和评估形成质量归因与系统迭代。

PSOP 的智能体体系不是为了替代 Runtime Kernel，而是为了围绕 Skill 生命周期形成受治理的多智能体生产系统。

## 3. 目标业务闭环

PSOP 的完整闭环由六类核心智能体组成：

```text
原始材料 / 标准 / 经验
        │
        ▼
psop-builder
  生成 pskill draft、证据映射、缺失信息问题
        │
        ▼
psop-compiler
  生成 psop-EG、编译诊断、能力需求
        │
        ▼
psop-tester
  基于世界模型生成正例/反例/边界场景
  调用 psop-runner 执行测试并产出反馈
        │
        ▼
psop-runner
  执行真实 invocation
  产生 terminal facts、trace、snapshot、replay
        │
        ▼
psop-audit
  基于真实运行事实进行审查和质量归因
        │
        ▼
psop-eval
  基于测试反馈与审计归因生成系统迭代提案
        │
        └────── 回流 builder / compiler / tester / runtime / prompts / code
```

其中 `psop-runner` 对应当前 RuntimeService 管理的运行时治理环境。它是 PSOP 运行真实作业的正式状态主权者，负责 Run、Session Token、Terminal Event、Trace Event、Replay 等事实对象。其它智能体围绕它构建、编译、测试、审计和改进。

## 4. 六类智能体的产品定位

### 4.1 psop-builder

`psop-builder` 负责帮助用户构建 PSOP Skill。

输入包括：

- 视频解析结果。
- 关键帧、图片、OCR、ASR、人工标注。
- 行业标准、企业制度、安全规范。
- 历史 Skill、测试反馈、审计归因。
- 用户补充的现场知识。

输出包括：

- pskill draft。
- evidence map。
- missing questions。
- safety constraints。
- workflow step candidates。
- expected evidence requirements。

builder 应采用 **skills-first** 的智能体实现方式：具体动作由 tools 执行，专业方法由 agent skills 提供，只有在上下文隔离、并行分析或长程任务时才引入 subagents。

### 4.2 psop-compiler

`psop-compiler` 负责将 pskill 编译为可由 `psop-runner` 执行的 PSOP-EG。

它不是开放式创作 agent，而是受约束的编译 agent。它必须遵守 formal revision、runtime actor 白名单、guard DSL、merge DSL、tool capability policy，并把所有编译失败转化为结构化 diagnostics。

输出包括：

- PSOP-EG artifact。
- compile diagnostics。
- capability summary。
- graph summary。
- source/evidence provenance。

### 4.3 psop-tester

`psop-tester` 负责基于世界模型生成大量正例、反例、边界场景和异常场景，并通过真实 runner 执行测试。

它不只是“写测试样例”，而是构建可版本化、可执行、可覆盖分析的测试工厂。

输出包括：

- test scenario suite。
- positive / negative / edge cases。
- synthetic terminal timeline。
- runner execution result。
- semantic judge result。
- coverage report。
- feedback to builder/compiler。

### 4.4 psop-runner

`psop-runner` 是当前 RuntimeService 的智能体化命名。它负责执行 PSOP-EG，并且是正式运行状态主权者。

它管理：

- skill_invocation。
- run。
- terminal_session。
- session_token_snapshot。
- terminal_event / terminal_event_part。
- trace_event。
- run_capability_binding。
- replay timeline。

runner 不应被普通 DeepAgent loop 取代。其它 agent 可以调用 runner，也可以在 runner 的某些 LLM/tool 节点内部使用 Agent Harness 能力，但不能接管 runner 的正式状态。

### 4.5 psop-audit

`psop-audit` 负责审查 pskill 的真实执行结果，并基于运行事实进行质量归因。

输入包括：

- Replay timeline。
- Session Token snapshots。
- Trace Events。
- Terminal Events。
- EG artifact。
- pskill / SkillVersion。
- Test result。

输出包括：

- audit report。
- deviation points。
- evidence refs。
- quality attribution。
- root cause analysis。
- suggested followups。

质量归因至少区分：skill 设计问题、编译问题、runner 问题、操作员问题、环境问题、工具/集成问题、模型输出问题。

### 4.6 psop-eval

`psop-eval` 负责基于测试反馈和审计归因，生成 PSOP 系统迭代提案。

它可以生成：

- prompt patch proposal。
- skill patch proposal。
- compiler rule proposal。
- tester coverage proposal。
- code patch draft。
- release checklist。
- environment quality report。

前期它必须 proposal-first，不直接修改生产代码、不直接发布、不直接跳过测试。后续可在受控 sandbox、CI、审批和发布门禁下进行代码更新和版本发布。

## 5. Agent Harness 的产品原则

### 5.1 DeepAgents-first，避免过早复杂化

PSOP 前期不同时暴露多个 runner 概念。系统对上只提供统一的 `AgentHarnessRunner`，默认基于 DeepAgents 构建 agent。LangGraph 作为 DeepAgents 体系内的底层 runtime 能力保留，但不在第一阶段暴露独立 `LangGraphRunner`。

第一阶段只区分两类 runner：

```text
deep_agent   # builder / compiler / tester / audit / eval 默认使用
psop_runtime # runner 使用，映射现有 RuntimeService
```

### 5.2 Skills-first，而不是 subagents-first

PSOP 的智能体实现优先采用：

```text
tools 负责动作
skills 负责方法和知识
subagents 负责上下文隔离、并行推理和复杂长任务
```

前期不把每个动作都拆成 subagent。builder、compiler、tester 的 MVP 都应先由主 agent + tools + skills 完成。subagents 在后续复杂化阶段再按需引入。

### 5.3 闭环优先，治理逐步强化

前期优先跑通：

```text
raw materials -> pskill -> psop-EG -> tests -> runner result -> tester feedback
```

为降低实现复杂度，开发期可以默认暴露 shell、文件写入、MCP tools 等能力，但必须限制在 agent workspace，并记录 agent event。生产期再逐步引入更严格的 tool policy、MCP trust registry、approval workflow、sandbox hardening 和 release gate。

### 5.4 所有智能体行为必须变成事实

任何 agent run 都必须产生可查询、可回放、可审计的事实：

- AgentRun。
- AgentEvent。
- AgentArtifact。
- Tool call event。
- Model usage。
- Workspace artifact。
- Related PSOP runtime run / compile request / skill version。

PSOP 不接受“黑盒智能体后台改动系统”。

## 6. 第一阶段 MVP 范围

第一阶段目标是：**Agent Harness MVP + Build/Compile/Test closed loop**。

范围包括：

1. 引入 DeepAgents / LangChain / LangGraph 作为智能体技术底座。
2. 新增 `backend/app/agent_harness/`。
3. 新增 `AgentDefinition`、`AgentRun`、`AgentEvent`、`AgentArtifact`。
4. 新增统一 `AgentHarnessRunner`。
5. 新增 `PsopGatewayChatModel`，确保生产模型调用仍经过 `LlmInferenceGateway`。
6. 新增 Agent Skills loader。
7. 新增 Tool Registry。
8. 默认暴露 workspace file tools、shell tool、MCP tool adapter、PSOP runtime tools。
9. 实现 `psop-builder` MVP。
10. 实现 `psop-compiler` MVP，并替换当前编译智能体的直接 prompt 调用。
11. 实现 `psop-tester` MVP，并调用现有 RuntimeService 执行测试。
12. 新增端到端演示链路。

第一阶段不追求完整权限系统，不实现复杂审批流，不做自动生产发布，不强制完整 UI。

## 7. 第二阶段目标

第二阶段目标是：**Audit/Eval closed loop**。

范围包括：

1. `psop-audit` 读取 replay、trace、terminal events、snapshots。
2. 生成结构化质量归因报告。
3. `psop-eval` 读取 test report、audit report、compile diagnostics。
4. 生成系统改进提案。
5. 在 workspace 内生成 prompt patch、skill patch、code patch draft。
6. 支持运行测试，但不自动发布生产版本。

## 8. 第三阶段目标

第三阶段目标是：**治理强化与生产化**。

范围包括：

1. tool policy 和 risk profile。
2. MCP server trust registry。
3. approval workflow。
4. sandbox hardening。
5. long-term memory。
6. eval 自动生成 PR。
7. release gate。
8. 多智能体并行与 subagent specialization。
9. 更完整的可观测、回放、成本、质量仪表盘。

## 9. 成功标准

### 9.1 MVP 成功标准

第一阶段成功标准：

- 用户可以基于视频解析结果和标准材料生成 pskill draft。
- 系统可以把 pskill 编译为 PSOP-EG。
- 系统可以生成至少一组正例和反例测试场景。
- 测试场景可以通过现有 psop-runner 执行。
- 测试反馈可以回流到 builder/compiler。
- 每次 agent run、tool call、artifact 输出都有持久化记录。

### 9.2 系统成功标准

长期成功标准：

- PSOP Skill 的构建质量可持续提升。
- 编译失败可以被诊断、修复和回归测试覆盖。
- 真实运行过程可以被完整审计和质量归因。
- 系统改进不依赖隐性经验，而是基于测试、运行和审计事实。
- agent 能力可以安全扩展到 MCP、shell、文件系统、代码更新和发布流程。

## 10. 非目标

当前阶段明确不做：

- 不把 PSOP 变成通用聊天机器人。
- 不让普通 agent loop 接管 psop-runner 的正式状态主权。
- 不把 Agent Skill 和 PSOP Skill 混为一谈。
- 不在第一阶段构建完整企业级权限/审批/租户体系。
- 不追求一开始就覆盖所有行业和所有现场设备集成。

## 11. 术语约定

- `PSOP Skill`：现实现场作业契约。
- `pskill`：PSOP Skill 的源码/草稿表达。
- `PSOP-EG`：可由 psop-runner 执行的 Execution Graph。
- `psop-runner`：当前 RuntimeService 对应的受控执行智能体。
- `Agent Skill`：供智能体按需加载的专业方法、模板、知识和工具说明，不等同于 PSOP Skill。
- `Agent Harness`：PSOP 多智能体的统一开发和治理底座。
- `AgentRun`：一次智能体执行事实。
- `AgentArtifact`：智能体产生的结构化产物。
- `AgentEvent`：智能体执行过程中的可审计事件。
