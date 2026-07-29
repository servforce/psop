# PSOP Agent Execution Calculus：形式定义与研究路线

## 摘要

PSOP Execution Graph（PSOP-EG）适合作为 PSOP 的核心形式系统，但当前定义仍需从架构概念收敛为可实现、可验证、可评估的执行模型。

其核心定位是：

> 面向真实现场作业的、状态主权明确、证据驱动、可测试演化、受形式安全约束的智能体执行系统。

PSOP-EG 不是普通流程图，也不是另一个 Agent Loop。它将状态、证据、约束、推理、运行轨迹和经验演化纳入统一治理，适用于长时程、部分可观测、可中断、可回放和可审计的现场任务。

## 1. 核心判断

当前设计中最重要的三个部分是：

1. **Session Token 掌握正式状态主权。** LLM context、scratchpad、框架内部 state 和工具返回均不是最终事实源。正式状态应由可持久化、可回放的 snapshot、trace 和 terminal event 重建。该设计与 Durable Execution 的 event history、replay 和确定性约束一致。[Temporal][1]
2. **外部世界按部分可观测系统建模。** 真实状态无法直接访问，只能依据照片、日志、用户输入和工具结果更新 belief。POMDP 中的策略也同时考虑动作效果与信息增益。[TU Delft][2]
3. **经验通过受治理的修订闭环生效。** 运行事实、失败归因、修改建议、审批和新 revision 必须分离，模型不能直接修改正式规则。

“PSOP-EG 记录全部状态”应改为：

> PSOP-EG 定义、约束并索引 PSOP 的全部正式状态；具体运行事实由 Session Token snapshot、trace、terminal events、artifact references 和 experience ledger 以 append-only 方式承载，并可据此重建。

EG 是状态语义和治理的根对象，不是所有运行数据的物理容器。

## 2. 形式定义

### 2.1 五层模型

建议将系统拆分为五层：

- **Core**：图结构、节点契约、不变量和终止条件。
- **Run**：一次执行的 Token、runtime handle 和事件历史。
- **Belief**：对不可直接观测世界状态的估计。
- **Experience**：运行结果、失败样本、先验和修订建议。
- **Governance**：权限、审批、风险、预算和版本策略。

一个不可变的 EG revision 定义为：

\[
\mathcal{G}^{r}
=
(\Sigma, N, C, I, H, Pol, ObsModel, Learn)
\]

其中：

- \(\Sigma\)：Session Token schema；
- \(N\)：节点集合；
- \(C\)：节点契约集合；
- \(I\)：安全和审计不变量；
- \(H\)：success、failure、waiting、deadlock 等终止谓词；
- \(Pol\)：调度、预算、重试、审批和风险策略；
- \(ObsModel\)：观测解释与 belief update 接口；
- \(Learn\)：允许生成 prior update 或 revision proposal 的条件。

时刻 \(t\) 的正式运行状态由 revision 和事件历史重建：

\[
R_t
=
Rebuild(\mathcal{G}^{r}, \mathcal{E}_{\le t})
\]

其中 \(\mathcal{E}_{\le t}\) 是 append-only 事件历史。Token snapshot 是恢复加速器，事件历史才是审计依据。

### 2.2 运行配置与状态转移

一次运行在时刻 \(t\) 的配置为：

\[
X_t=(\tau_t,b_t,\eta_t,\omega_t)
\]

其中：

- \(\tau_t\)：Session Token；
- \(b_t\)：对外部世界的 belief；
- \(\eta_t\)：runtime handle；
- \(\omega_t\)：不可直接访问的真实外部世界状态。

节点执行定义为：

\[
(\tau_t,b_t,\eta_t,\omega_t)
\xrightarrow{\,n,\beta,o\,}
(\tau_{t+1},b_{t+1},\eta_{t+1},\omega_{t+1})
\]

其中 \(n\) 是节点，\(\beta\) 是绑定后的输入，\(o\) 是执行产生的 observation。

### 2.3 Contract-first 节点

节点应优先定义契约：

\[
n=
(id,kind,R,W,Guard,Req,Project,Actor,Observe,Merge,
UpdateBelief,EmitExperience,Policy)
\]

关键约束如下：

- \(R/W\) 明确节点对 Token 的读写范围；
- `Guard` 和 `Req` 决定节点是否可执行；
- `Actor` 只能产生 observation，不能直接修改正式状态；
- `Merge` 校验 observation 后写入 Token；
- `UpdateBelief` 更新不确定状态；
- `EmitExperience` 只能追加经验事实或提出 revision proposal；
- 高风险 effect 必须经过 `Policy` 和 approval gate。

必须严格区分：

\[
Actor \rightarrow Observation \rightarrow Validation \rightarrow Merge
\]

LLM、工具、人工输入、审批回调和第三方 Skill 均不得绕过该路径。

## 3. 必须满足的系统性质

### 3.1 状态主权

Session Token 是正式事务状态。模型上下文、Agent thread state 和框架缓存只能作为临时计算介质。

### 3.2 代数结构

节点执行通常不可逆：事件已追加、预算已消耗、外部副作用已发生。因此主数学对象不应是群，而应是由节点诱导的部分变换幺半群：

\[
\mathsf{M}_{\mathcal{G}}
=
\left\langle F_{n,\beta,o} \right\rangle
\]

群只适用于设备 ID 重命名、语言本地化、等价工具替换等可逆对称变换。安全约束可表示为 forward invariant，坏状态规避可表示为：

\[
Reach(Init)\cap Bad=\varnothing
\]

### 3.3 活性

系统除 safety 外还需保证不会无限运行：

\[
running
\Rightarrow
\Diamond(success \lor failure \lor waiting \lor escalation)
\]

必须限制无限 sensing、无限 retry 和永久等待。

### 3.4 并发一致性

terminal event、tool callback、approval callback、timer 和 runner loop 可能并发到达，因此需定义：

- snapshot version 和乐观并发控制；
- event ordering 和 merge conflict；
- idempotency key；
- at-least-once 交付下的去重；
- 外部副作用的一致性与补偿策略。

### 3.5 确定性回放

所有非确定性输入必须被记录：

- model、prompt projection hash 和 sampling 参数；
- tool input、tool output 和 idempotency key；
- random seed；
- approval decision；
- artifact hash；
- graph、policy 和 schema revision。

回放应能从相同事件历史重建相同正式状态，而不要求再次调用外部模型或工具。

## 4. 相对通用 Agent Framework 的差异

LangGraph、OpenAI Agents SDK 和 Microsoft Agent Framework 已提供 durable state、graph workflow、handoff、guardrail、memory、human-in-the-loop 和 tracing 等能力。[LangGraph][3] [OpenAI Agents SDK][4] [Microsoft Agent Framework][5]

PSOP 的差异不在 Agent Loop，而在以下五点：

1. **状态主权**：正式状态属于 Session Token snapshot 链，而非模型或临时 thread。
2. **证据治理**：动作必须满足 Guard、Evidence、Belief、Approval 和 RiskBound。
3. **部分可观测推理**：Runner 执行 belief update、active sensing 和风险调度。
4. **测试驱动演化**：正向测试验证成功可达性，负向测试验证安全拒绝，反向和反事实测试搜索危险路径。
5. **审计闭环**：失败、等待、拒绝、升级和人工干预进入 experience ledger，再通过 proposal 和审批形成新 revision。

可将这一差异概括为：

> PSOP 将智能体从 prompt-time intelligence 推进到 runtime-governed intelligence：智能由可持久化状态、可验证约束、可回放轨迹、可学习经验和可审计治理共同构成。

## 5. 场景级世界模型

PSOP 可以训练独立的场景级世界模型，但应区分两类实现。

### 5.1 符号或概率模型

首先从状态变量、节点动作、工具返回、terminal events 和测试轨迹中学习：

\[
P(s_{t+1},o_{t+1},risk,cost,terminal
\mid s_t,a_t,context)
\]

该模型可表示状态转移、故障先验、人类响应、工具失败和证据质量，无需先训练大规模神经模型。

### 5.2 神经场景模型

神经世界模型输入 Token、动作、上下文和历史 trace，预测 observation、状态增量、风险、成本与终止概率：

\[
W_\phi(\tau_t,a_t)
\rightarrow
(\hat{o}_{t+1},
\widehat{\Delta\tau},
\widehat{risk},
\widehat{cost},
\widehat{terminal},
\hat{b}_{t+1})
\]

主要用途：

1. 为 Runner 预测信息增益较高的 sensing action；
2. 为 Tester 生成边界、反事实和长尾轨迹；
3. 在模拟环境中训练调度策略；
4. 比较真实轨迹与预期轨迹，辅助异常归因。

世界模型研究已验证了通过预测行动后果和 imagination training 降低真实交互成本的价值，尤其适用于真实操作昂贵或危险的场景。[Dreamer 4][6] [World Models as an Intermediary][7]

PSOP 可天然生成结构化训练样本：

\[
(\tau_t,a_t,o_{t+1},\tau_{t+1},judge,risk,cost,terminal)
\]

权限边界必须保持不变：

> 世界模型只能提供 prediction、prior、simulation 和 test generation；不能写入正式事实，也不能绕过 Guard、Req 或 Approval。预测进入 belief 或 experience prior，真实 observation 才能进入 facts。

## 6. 与 Cosmos 3 Reasoner 的关系

两者可以类比，但不能等同：

- Cosmos 3 Reasoner Tower 是神经多模态物理理解模块；
- PSOP-EG 是由 execution graph、Session Token 和 policy system 构成的符号—运行时 governed reasoner；
- PSOP-WM 是可选的 learned scenario world model。

NVIDIA 将 Cosmos 3 描述为：Reasoner Tower 解释多模态观察和物理上下文，Generator Tower 据此生成未来观察与动作序列。[NVIDIA][8]

三者可统一抽象为：

\[
Observation
\rightarrow
State\ Belief
\rightarrow
Governed\ Action
\]

PSOP 的创新是将 reason-before-action 落到企业现场作业、工具调用、人工审批、测试演化和审计回放中。

## 7. 论文与评估路线

该方向适合 systems、formal methods 与 agent runtime 交叉研究，不宜只写成 position paper。arXiv CS 对未经同行评审的 review 和 position paper 更谨慎，论文应包含原创形式系统、可运行实现和实验结果。[arXiv Blog][9] [arXiv Moderation][10]

建议题目：

> **PSOP Execution Graph: A State-Sovereign, Test-Evolving Execution Model for Governed LLM Agents under Partial Observability**

论文应包含五项贡献：

1. **Formalism**：Session Token、Guarded Rewrite、Belief State、Experience Ledger 和 Invariant Core；
2. **Runtime**：Runner 如何执行节点、合并 observation、追加 trace 和更新 belief；
3. **Testing Loop**：positive、negative、reverse 和 counterfactual tests 如何驱动 revision；
4. **Safety and Auditability**：risk gate、approval、append-only facts 和 deterministic replay；
5. **Evaluation**：与通用 Agent Loop 和 graph orchestration baseline 比较。

最低实验集：

- **Safety**：缺少证据、设备错误、工具越权和高风险动作时能否拒绝、等待或升级；
- **Efficiency**：belief 与 active sensing 能否减少无效节点、工具调用和完成成本；
- **Evolution**：Tester 发现反例后，revision 能否提高通过率并控制 regression；
- **Replay**：相同事件历史能否稳定重建相同正式状态；
- **Concurrency**：重复、乱序和并发事件是否破坏不变量。

世界模型可作为后续工作：

> **PSOP-WM: Learning Scenario-Level World Models from Auditable Agent Execution Traces**

重点评估其对测试覆盖率、主动采证、运行成本和失败预警的影响。

## 8. 实施优先级

1. 明确 EG、Run、Belief、Experience 和 Governance 的边界；
2. 固化 contract-first 节点接口和 Actor—Observation—Merge 路径；
3. 定义并验证 safety、liveness、concurrency 和 replay semantics；
4. MVP 使用结构化 confidence，形式上兼容 Bayesian update 或 POMDP；
5. 将 Tester—Experience—Proposal—Revision 闭环作为核心能力；
6. 建立设备维保、现场检查或工具审批 benchmark；
7. 在积累足够审计轨迹后训练场景级世界模型。

PSOP-EG 的核心价值可以归结为：

> 定义一种面向真实作业、状态主权明确、证据驱动、可测试演化且可审计的 governed agent execution calculus。

[1]: https://learn.temporal.io/tutorials/go/background-check/durable-execution/ "Develop code that durably executes | Learn Temporal"
[2]: https://research.tudelft.nl/en/publications/decision-theoretic-planning-under-uncertainty-with-information-re "Decision-theoretic planning under uncertainty with information rewards for active cooperative perception"
[3]: https://docs.langchain.com/oss/python/langgraph/overview "LangGraph overview"
[4]: https://openai.github.io/openai-agents-python/ "OpenAI Agents SDK"
[5]: https://learn.microsoft.com/en-us/agent-framework/migration-guide/from-autogen/ "Microsoft Agent Framework"
[6]: https://arxiv.org/pdf/2509.24527 "Training Agents Inside of Scalable World Models"
[7]: https://arxiv.org/html/2602.00785v1 "World Models as an Intermediary between Agents and the Real World"
[8]: https://developer.nvidia.com/blog/develop-physical-ai-reasoning-world-and-action-models-with-nvidia-cosmos-3/ "NVIDIA Cosmos 3"
[9]: https://blog.arxiv.org/2025/10/31/attention-authors-updated-practice-for-review-articles-and-position-papers-in-arxiv-cs-category/ "arXiv policy for review articles and position papers"
[10]: https://info.arxiv.org/help/moderation/index.html "arXiv content moderation"
