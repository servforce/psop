# PSOP Execution Graph Formal v5.1

## 状态主权、部分可观测推理与测试演化的 Agent 执行图

---

## 摘要

本文给出 PSOP Execution Graph（简称 **PSOP-EG**）的 v5.1 形式化定义。相对于 v5 中把 PSOP-EG 定义为面向 Agent Harness 的 session-driven guarded rewrite system，v5.1 进一步明确三件事：

1. **状态主权**：PSOP-EG 不应被理解为“物理保存所有数据的单个图文件”，而应被理解为定义、约束、索引并可重建 PSOP 正式状态的治理根对象。真实运行事实由 Session Token snapshot、append-only trace、artifact reference、terminal event、belief record 与 experience ledger 共同承载。
2. **运行时 Reasoner**：PSOP-EG 不只是 workflow graph，也不是 prompt chain，而是 Runner / Agent Harness 的状态感知推理核。它在每个 step 中回答：当前状态是什么、哪些证据足够、哪些节点 enabled、下一步动作的风险和信息增益如何、哪些不变量必须保持、哪些历史经验可以复用。
3. **测试演化**：PSOP-EG 是可测试驱动演化的执行对象。正向 tester 强化可复用成功路径，负向 tester 固化安全边界，反向 tester 从坏状态或失败终点反推缺失 guard、证据要求、主动探测节点、恢复路径和不变量。

本文把 PSOP-EG 组织为五层：Graph Definition、Runtime State、Belief State、Experience Ledger、Governance / Revision。形式系统以 Session Token 为运行时一等状态，以节点 contract 为执行原语，以 guarded rewrite 为操作语义，以 invariant / reachability / liveness 为静态分析对象，以 belief update / active sensing 处理部分可观测外部环境，以 experience ledger 和 tester loop 支持版本化改进。

数学上，PSOP-EG 的节点执行一般不是可逆群作用，而是作用在 Token 空间上的**部分变换**；所有节点生成一个 partial transformation monoid。安全约束是不变量，坏状态规避是可达性问题，相似 skill 迁移是对称变换下的等变性问题，经验闭环的稳定状态是学习更新算子的近似固定点。这样的表述比单纯类比群论或环论更严格，也更容易落到工程实现和论文评估。

---

## 目录

1. [设计定位与边界](#1-设计定位与边界)
2. [分层对象模型](#2-分层对象模型)
3. [节点 Contract 与执行原语](#3-节点-contract-与执行原语)
4. [运行语义](#4-运行语义)
5. [状态感知、主动探测与推理加速](#5-状态感知主动探测与推理加速)
6. [经验闭环与 Tester 驱动演化](#6-经验闭环与-tester-驱动演化)
7. [静态分析、验证与发布门禁](#7-静态分析验证与发布门禁)
8. [数学说明：变换幺半群、不变量、等变性与固定点](#8-数学说明变换幺半群不变量等变性与固定点)
9. [场景级世界模型](#9-场景级世界模型)
10. [设备维保参考实例](#10-设备维保参考实例)
11. [工程映射与 Runner Step 伪代码](#11-工程映射与-runner-step-伪代码)
12. [结语](#12-结语)

---

# 1. 设计定位与边界

## 1.1 PSOP-EG 试图解决的问题

PSOP 面向开放现实环境中的服务作业、现场作业和工具增强型智能体执行。此类任务并不是一次模型调用可以完成的封闭推理问题，而是具有以下特征的长时程事务：

1. 任务由多个可复用步骤组成，例如采集信息、核验证据、调用工具、调用代码、调用 LLM、等待审批、调用外部 skill、执行恢复、归档结果；
2. 步骤是否能执行不只取决于静态边，而取决于当前事务状态、证据质量、权限、预算、风险、状态信念和历史经验；
3. 外部世界部分可观测。系统经常不能直接知道设备真实状态、用户意图、工单事实、审批结果或工具真实可用性；
4. 系统需要支持暂停、恢复、回放、审计、重试、人工介入和版本化改进；
5. 每次运行、测试、反例、失败诊断都可能产生经验，并反过来改变 graph 的 guard、requirement、scheduler、prior、probe policy 或 invariant。

因此，PSOP-EG 的核心问题不是“如何画一张流程图”，而是：

> 如何把真实作业中的状态、证据、推理、工具调用、人工审批、测试反馈和经验演化，统一到一个可执行、可审计、可验证、可迭代的智能体执行图中。

## 1.2 从控制核到运行时 Reasoner

v5 中 PSOP-EG 被定义为 Agent Harness 的 control kernel。v5.1 保留这个定义，但进一步强调它也是 **runtime reasoner**：

- 它不是纯静态 workflow；
- 它不是 LLM prompt；
- 它不是 agent scratchpad；
- 它不是单独的世界模型；
- 它是把正式状态、证据约束、调度策略、状态信念、工具执行、审计轨迹和经验反馈连接起来的推理—执行核心。

在每个 Runner step 中，PSOP-EG 至少参与以下推理：

1. 当前 Session Token 是否和 schema / invariant 一致；
2. 当前外部事件是否需要同步进入正式状态；
3. 当前外部世界状态的 belief 如何更新；
4. 哪些节点满足 guard、requirement、权限、预算和风险门禁；
5. 多个候选节点之间如何基于效用、成本、风险和信息增益选步；
6. 节点观察结果如何 merge 回正式状态；
7. 本次事件对 trace、belief、experience、tester case、revision proposal 有何影响。

## 1.3 状态主权的精确定义

本文避免使用“graph 本身物理记录全部状态”这种容易被误解的表述。更严格的定义是：

> **PSOP-EG 定义、约束、索引并可重建 PSOP 的全部正式状态；正式状态由 canonical store 中的 Session Token snapshot、append-only trace、artifact reference、terminal event、belief record、experience ledger 和 graph revision 共同承载。**

也就是说：

- Graph Definition 定义状态空间、节点、guard、requirement、invariant、policy、halt predicates；
- Session Token snapshot 记录单次事务的正式状态；
- Trace / terminal events 记录状态如何演化；
- Artifact refs 记录大对象和外部证据；
- Belief records 记录对不可完全观测世界的概率估计；
- Experience ledger 记录跨运行、跨测试、跨版本的经验和反例；
- Graph revision 记录定义层如何随测试和经验演化。

这样，PSOP-EG 是状态治理的根，而不是所有数据的物理容器。

## 1.4 与传统 workflow / Petri net / agent loop 的差异

PSOP-EG 与传统 workflow 的主要差异：

- workflow 通常以显式边为主，PSOP-EG 以状态诱导 enabled nodes；
- workflow 的状态常常散落在任务变量中，PSOP-EG 把 Session Token 作为一等正式状态；
- workflow 通常弱化 LLM prompt budget 和上下文投影，PSOP-EG 把 Prompt View 纳入形式系统；
- workflow 通常不内生测试演化，PSOP-EG 把 positive / negative / reverse tester 纳入 graph revision 语义。

PSOP-EG 与普通 LLM agent loop 的主要差异：

- agent loop 往往让模型决定下一步，PSOP-EG 先用 guard / requirement / invariant / policy 约束动作空间；
- agent memory 往往是自然语言或向量检索，PSOP-EG 区分正式事实、经验先验和模型预测；
- agent trace 往往只用于观察，PSOP-EG 的 trace 是 replay、audit、eval、learn 的正式输入；
- agent 自改进容易不可控，PSOP-EG 要求 revision proposal、测试门禁和版本发布。

## 1.5 设计原则

PSOP-EG v5.1 采用以下原则。

### 原则一：节点静态，合法转移由 EG 表达

节点集合由 graph revision 定义。运行时 enabledness 可以由 Session Token、Belief State、Experience Ref、guard、requirement 和 policy 动态诱导，但 evaluation 节点在不同 decision 下的合法下一 phase 必须由 EG 表达，例如 `node.interaction.transitions`。旧 artifact 可以用 `dependency_graph_for_view` 表达可达边作为兼容来源，但它不应替代新 artifact 的显式 transition。

### 原则二：Actor 不直接写正式状态

节点 actor 可以调用 LLM、tool、code、approval、external skill 或 human input，但 actor 的结果必须先成为 observation。正式状态只能通过 merge 函数和 runtime commit 写入。

Actor observation 不能拥有执行游标。对于运行期 evidence evaluation，模型可以判断当前节点 `proceed`、`need_more_evidence`、`retry`、`abort` 或 `complete`，但不能把 `observation.next_phase` 当作正式 phase 写入来源；Runtime 必须依据 EG transition 决定下一 phase。

### 原则三：事实、信念、经验、预测分离

- `facts` 是已经进入正式状态的事实；
- `belief` 是对不可直接观察状态的估计；
- `experience` 是跨运行的先验、案例和反例；
- `world_model_prediction` 是模型预测，不得直接作为事实。

### 原则四：所有副作用可审计

任何工具调用、人工审批、LLM 输出、外部回调、状态修改、预算消耗和失败恢复都必须生成 trace event。

### 原则五：学习不等于静默改图

运行经验可以生成经验条目和 revision proposal，但不能静默修改已发布 graph。发布新 revision 必须通过 tester、静态分析和人工或策略门禁。

### 原则六：安全优先于智能

当 belief 不确定、证据不足、权限不够、风险过高或反向测试命中时，PSOP-EG 应优先等待、探测、升级、拒绝或失败，而不是让模型“猜一个合理答案”。

---

# 2. 分层对象模型

## 2.1 基本集合

为避免歧义，本文使用以下集合：

- \(Input\)：外部输入空间；
- \(InstId\)：运行实例标识空间；
- \(GraphId\)：graph 标识空间；
- \(RevId\)：graph revision 标识空间；
- \(Goal\)：目标和子目标空间；
- \(Meta\)：元信息空间；
- \(Facts\)：正式事实空间；
- \(Reg\)：工作寄存器空间；
- \(Mem\)：运行内记忆、摘要、检索视图空间；
- \(Trace\)：事件轨迹空间；
- \(Ctrl\)：控制字段空间；
- \(Stat\)：实例状态空间；
- \(Bel\)：状态信念空间；
- \(ExpRef\)：经验引用空间；
- \(\Omega\)：外部世界真实状态空间；
- \(\Delta(\Omega)\)：\(\Omega\) 上的概率分布空间；
- \(\Eta\)：Harness 内部句柄空间；
- \(Obs\)：观察结果空间；
- \(Act\)：节点动作或主动探测动作空间；
- \(Msg\)：消息对象空间；
- \(V\)：模型词表；
- \(Case\)：tester 用例空间；
- \(Inv\)：不变量集合；
- \(Artifact\)：外部证据和大对象引用空间；
- \(Proposal\)：graph revision proposal 空间；
- \(\Gamma\)：相似 skill / 场景之间的可逆结构变换集合。

其中：

\[
Stat \supseteq \{running, waiting, success, failure, deadlock, escalated, cancelled\}
\]

## 2.2 Graph Definition

一个发布后的 PSOP-EG revision 定义为：

\[
\mathcal G^r=(\Sigma,N,Init,H,Inv,Pol,ObsModel,Learn,Ver)
\]

其中：

- \(r\in RevId\)：revision ID；
- \(\Sigma\)：Session Token、Belief、Experience Ref、Artifact Ref 的 schema；
- \(N\)：有限节点集合；
- \(Init:Input\to Tok^+\)：初始化函数；
- \(H=(h_{succ},h_{fail},h_{wait},h_{escalate})\)：终止和等待谓词；
- \(Inv=Inv_{schema}\cup Inv_{safety}\cup Inv_{audit}\cup Inv_{policy}\)：不变量集合；
- \(Pol\)：调度、预算、审批、重试、主动探测、风险控制策略；
- \(ObsModel\)：观察模型与 belief update 接口；
- \(Learn\)：从 trace / tester / human review 生成经验和 proposal 的规则；
- \(Ver\)：版本、血缘、发布状态、兼容性和回滚信息。

\(\mathcal G^r\) 是定义层对象。它不保存所有运行数据，但它定义运行数据应如何产生、校验、解释、回放和演化。

## 2.3 Living Graph State

为了表达测试和经验驱动演化，引入 living graph state：

\[
\mathfrak G_t=(\mathcal G^r,\mathcal E_{\le t},\Theta_t,Index_t,Metrics_t)
\]

其中：

- \(\mathcal G^r\)：当前生效或待评审的 graph revision；
- \(\mathcal E_{\le t}\)：截至时间 \(t\) 的经验账本；
- \(\Theta_t\)：当前 prior、调度权重、risk threshold、probe threshold、retrieval policy；
- \(Index_t\)：经验检索索引、trace summary 索引、反例索引、artifact 索引；
- \(Metrics_t\)：测试通过率、安全违规率、平均成本、平均步数、状态误差、回归失败率等指标。

运行时读取 \(\mathfrak G_t\)，但不能在单次节点执行中直接静默修改 \(\mathcal G^r\)。修改 graph 必须生成 revision proposal。

## 2.4 Session Token

**定义 2.1（Base Session Token）**

单次运行的基本事务状态定义为：

\[
Tok = InstId \times GraphId \times RevId \times Goal \times Meta \times Facts \times Reg \times Mem \times Trace \times Ctrl \times Stat
\]

记：

\[
\tau=(iid,gid,rid,goal,m,f,reg,mem,h,ctrl,stat)\in Tok
\]

其中：

- \(iid\)：运行实例 ID；
- \(gid\)：所属 graph ID；
- \(rid\)：执行所绑定的 graph revision；
- \(goal\)：主目标和子目标栈；
- \(m\)：稳定元信息，如工单、设备、站点、用户、权限、策略配置；
- \(f\)：正式事实，如用户输入、照片引用、日志、工具返回、审批结论、完成证据；
- \(reg\)：运行中寄存器，如局部判断、中间结果、临时计划、候选节点缓存；
- \(mem\)：运行上下文摘要、检索视图、压缩历史；
- \(h\)：append-only trace；
- \(ctrl\)：预算、重试、租约、锁、等待原因、截止时间、人工介入状态；
- \(stat\)：运行状态。

**定义 2.2（Extended Session Token）**

为支持状态感知和经验复用，定义扩展 Token：

\[
Tok^+=Tok\times Bel\times ExpRef
\]

记：

\[
\tau^+=(\tau,b,xref)
\]

其中：

- \(b\in Bel\subseteq\Delta(\Omega)\)：对外部真实状态的信念；
- \(xref\in ExpRef\)：当前运行命中的经验、先验、反例、tester 证据引用。

工程实现中，\(b\) 与 \(xref\) 可以物化在独立表，也可以作为 Token snapshot 的字段；形式语义中它们是一等对象。

## 2.5 Canonical Store 与正式状态

定义 canonical store：

\[
Store=(Snapshots,Events,Artifacts,Beliefs,Experiences,Revisions,Proposals,Metrics)
\]

正式状态不是单个对象，而是可重建视图：

\[
FormalState(iid,t)=Rebuild(Store,iid,t,\mathcal G^r)
\]

其中 `Rebuild` 至少依赖：

1. graph revision；
2. 初始输入；
3. snapshot 链；
4. append-only events；
5. artifact hashes / refs；
6. belief records；
7. policy version；
8. experience refs；
9. terminal decisions。

如果 `Rebuild` 无法复现某次状态，则该 run 不满足 replay determinism。

## 2.6 Trace Event

**定义 2.3（Trace Event）**

执行轨迹为：

\[
Trace=Event^*
\]

单个事件：

\[
e=(eid,iid,rid,nid,kind,\beta,input\_ref,obs\_ref,\Delta,status,cost,belief\_delta,exp\_delta,det,ts_b,ts_e,summary)
\]

其中：

- \(eid\)：事件 ID；
- \(iid\)：运行实例 ID；
- \(rid\)：graph revision；
- \(nid\)：节点 ID；
- \(kind\)：节点类型；
- \(\beta\)：局部绑定；
- `input_ref`：prompt、tool input、approval request、code input 等引用；
- `obs_ref`：节点产生的 observation 引用；
- \(\Delta\)：Token 关键增量摘要；
- `status`：success / failure / timeout / retryable / rejected / skipped；
- `cost`：token、费用、时延、人工等待、工具调用次数；
- `belief_delta`：状态信念变化摘要；
- `exp_delta`：产生或命中的经验变化摘要；
- `det`：replay determinism 元数据；
- \(ts_b,ts_e\)：开始和结束时间；
- `summary`：供审计、解释和检索的摘要。

`det` 至少应包含：

- actor implementation version；
- model name / version；
- prompt projection hash；
- tool input hash；
- tool output hash；
- policy version；
- random seed 或 nondeterminism marker；
- artifact hash；
- external callback ID；
- approval decision ID。

Trace 必须 append-only。修正既有结论只能追加 correction event，不能篡改历史 event。

## 2.7 外部世界与部分可观测性

外部真实状态为：

\[
\omega\in\Omega
\]

\(\omega\) 可包含：

- 设备真实状态；
- 用户真实意图；
- 工单真实状态；
- 工程师现场行为；
- 外部 API 当前状态；
- 审批者真实决策；
- 当前时间和现场环境；
- 工具、模型和网络可用性；
- 尚未同步进入 Token 的回调。

PSOP Runner 通常不能直接访问 \(\omega\)，只能通过观察获得信息：

\[
O_u:\Omega\to\mathcal P(Obs)
\]

其中 \(u\in Act\cup\{passive\}\)。

## 2.8 Belief State

Belief State 表示系统对外部世界状态的估计：

\[
b\in Bel\subseteq\Delta(\Omega)
\]

工程实现不要求完整枚举 \(\Omega\)。Belief 可以由下列近似表达：

- 状态分类及 confidence；
- 多个状态假设和权重；
- 风险概率；
- 证据质量分数；
- LLM judge 置信度；
- 规则系统得分；
- embedding 检索相似度；
- calibrated classifier 输出；
- 粒子或采样集合。

关键约束：belief 不是 fact。Belief 可以影响 guard、scheduler、probe policy、prompt projection，但不能直接替代完成证据。

## 2.9 Experience Ledger

定义经验账本：

\[
\mathcal E=(C^+,C^-,C^{rev},T,E_s,E_f,Pri,Models,Notes)
\]

其中：

- \(C^+\)：正向 tester cases；
- \(C^-\)：负向 tester cases；
- \(C^{rev}\)：反向 tester cases；
- \(T\)：真实运行 trace summary 和 replay 结果；
- \(E_s\)：成功经验；
- \(E_f\)：失败经验和反例；
- \(Pri\)：先验统计，例如状态频率、节点成功率、工具延迟、风险概率；
- \(Models\)：状态估计器、世界模型、judge 模型、retriever 版本；
- \(Notes\)：人工审查、专家规则、版本变更说明。

Experience Ledger 是 graph 的长期经验记忆，但不等于单次运行事实。

## 2.10 Harness Runtime

完整 Harness Runtime 定义为：

\[
\mathcal H=(Store,Sync,Observe,Estimate,Retrieve,Pick,Sel,Project,Comp,Execute,Merge,Commit,Guardrails,TraceBus,EvalLoop,Learn,Publish)
\]

其中：

- `Store`：正式状态存储；
- `Sync`：把外部事件同步进入 Token；
- `Observe`：被动采集或主动探测；
- `Estimate`：belief update；
- `Retrieve`：经验检索和 prior 装配；
- `Pick`：多实例调度；
- `Sel`：实例内选步；
- `Project`：生成 Prompt View 或 tool input；
- `Comp`：上下文压缩与 budget 控制；
- `Execute`：执行 actor；
- `Merge`：把 observation 写回正式状态；
- `Commit`：原子提交 snapshot / event / artifact refs；
- `Guardrails`：权限、安全、风险和合规检查；
- `TraceBus`：日志、遥测和审计事件；
- `EvalLoop`：持续评估；
- `Learn`：经验沉淀和 proposal 生成；
- `Publish`：revision 发布、回滚和迁移。

因此：

> **PSOP-EG 是 Harness Runtime 解释执行的治理型 Reasoner；Harness Runtime 是 PSOP-EG 的执行宿主；Store 是正式状态的物理载体。**

---

# 3. 节点 Contract 与执行原语

## 3.1 节点定义

每个节点 \(n\in N\) 定义为：

\[
n=(id,kind,Bind,enum,R,W,G,Req,Project,Actor,ObsMap,Merge,BeliefUpdate,EmitExp,Policy)
\]

其中：

- `id`：节点唯一 ID；
- `kind`：节点类型；
- `Bind` / `enum`：局部绑定空间与枚举函数；
- \(R\)：读字段集合；
- \(W\)：写字段集合；
- \(G\)：guard；
- `Req`：证据、权限、风险、预算、审批等要求；
- `Project`：把 Token 投影为模型 / 工具 / 人工任务输入；
- `Actor`：真实执行关系；
- `ObsMap`：把 actor 返回规范化为 observation；
- `Merge`：把 observation 写入 Token；
- `BeliefUpdate`：更新状态信念；
- `EmitExp`：生成经验候选或诊断信号；
- `Policy`：节点级调度、重试、超时、成本、risk annotation。

## 3.2 节点类型

节点类型集合：

\[
K=\{start,input,sense,code,llm,tool,skill,timer,approval,human,terminal,eval,repair\}
\]

含义如下：

- `start`：初始化或启动节点；
- `input`：接收外部输入、回调或 terminal event；
- `sense`：主动或被动状态感知节点；
- `code`：执行确定性或半确定性代码；
- `llm`：调用语言或多模态模型；
- `tool`：调用 API、数据库、MCP、内部服务；
- `skill`：调用外部 skill / agent；
- `timer`：由时间条件触发；
- `approval`：审批、授权、人工确认；
- `human`：向人类提问、请求补充证据、等待现场操作；
- `terminal`：显式终止节点；
- `eval`：评估、judge、回放、测试节点；
- `repair`：失败恢复、补偿、回滚、升级节点。

`eval` 和 `repair` 可以在工程实现中落到 `code`、`llm`、`tool` 或 `human`，但在形式语义中单独列出有助于分析。

## 3.3 局部绑定

某些节点需要从 Token 中选择一个局部对象执行，例如对多个待处理照片逐张核验，或对多个候选工具逐个测试。定义：

\[
\beta\in Bind_n
\]

枚举函数：

\[
enum_n:Tok^+\to\mathcal P(Bind_n)
\]

若节点不需要局部绑定，则：

\[
Bind_n=\{\star\},\qquad enum_n(\tau^+)=\{\star\}
\]

## 3.4 读写足迹

节点声明：

\[
R_n,W_n\subseteq Fields(Tok^+)
\]

读写足迹用于：

1. 静态依赖分析；
2. 并发冲突检测；
3. replay dependency；
4. prompt projection 最小化；
5. experience retrieval key 构造；
6. invariant preservation 检查。

## 3.5 Guard

Guard 是纯判定函数：

\[
G_n:Tok^+\times Bind_n\to\{true,false\}
\]

Guard 必须满足：

1. 纯函数；
2. 总定义；
3. 无副作用；
4. 不直接读取外部世界；
5. 只依赖 Token、belief、experience ref 和 policy 中可审计的信息；
6. 版本化且可测试。

外部世界变化若要影响 guard，必须先经过 `Sync`、`Observe` 或 `Estimate` 进入正式状态或 belief。

## 3.6 Requirement

Guard 判断“是否在结构上可进入该节点”；Requirement 判断“执行该节点前证据、权限、预算、风险是否足够”。定义：

\[
Req_n:Tok^+\times Bind_n\to ReqResult
\]

其中：

\[
ReqResult=\{satisfied,missing\_evidence,need\_approval,over\_budget,high\_risk,blocked,unknown\}
\]

Requirement 可包含：

- 必须存在的证据字段；
- 证据质量阈值；
- 权限断言；
- 审批状态；
- budget 阈值；
- risk threshold；
- belief confidence；
- negative case 命中排除；
- artifact hash / provenance；
- human confirmation。

重要约束：对于高风险节点，`G=true` 但 `Req!=satisfied` 时，不得执行 actor。Runner 应选择主动探测、审批、等待、升级或失败恢复。

## 3.7 Prompt Projection 与 Model Tokens

为避免混淆，区分三层 token：

\[
\text{Session Token}\xrightarrow{Project}\text{Prompt View}\xrightarrow{Encode}\text{Model Tokens}
\]

对 LLM 节点：

\[
Project_n:Tok^+\times Bind_n\to Msg^*
\]

\[
Encode_n:Msg^*\to V^*
\]

token 用量：

\[
TokCount_n(\tau^+,\beta)=\left|Encode_n(Project_n(\tau^+,\beta))\right|
\]

若预算 \(B_n\) 存在，则应满足：

\[
TokCount_n(\tau^+,\beta)\le B_n
\]

否则必须先执行 `Comp` 或 `Retrieve`：

\[
\tau_c^+=Comp(Retrieve(\tau^+,\mathcal E),B_n)
\]

Prompt View 不应包含所有历史；它应是当前节点的最小充分上下文，包含：

- 当前 goal；
- 与节点相关的 facts；
- 必要 trace summary；
- 相关 experience refs；
- 当前 belief 摘要；
- required output schema；
- safety constraints；
- 不允许模型越权修改的状态字段。

## 3.8 Actor

Actor 定义为关系：

\[
Actor_n\subseteq Tok^+\times Bind_n\times InputView_n\times \Omega\times \Eta\times RawObs\times \Omega\times \Eta
\]

记：

\[
(\tau^+,\beta,v,\omega,\eta,raw,\omega',\eta')\in Actor_n
\]

表示：节点 \(n\) 在当前状态、绑定、输入视图、外部世界和 runtime handle 下，产生原始返回 \(raw\)，并可能改变外部世界和 runtime handle。

Actor 可以有副作用，例如发消息、调用工具、提交审批、写外部系统。但这些副作用必须记录到 trace，且正式 Token 写入仍只能通过 Merge。

## 3.9 Observation Normalization

原始返回需要规范化：

\[
ObsMap_n:RawObs\times Tok^+\times Bind_n\to Obs
\]

Observation 应尽量结构化，包含：

- status；
- structured payload；
- confidence；
- evidence refs；
- error code；
- provenance；
- risk flags；
- suggested state delta；
- suggested belief delta；
- suggested experience delta。

LLM 输出不得直接成为正式状态；它只是 observation，需要通过 schema validation、judge、guardrail 和 merge。

## 3.10 Merge

Merge 函数：

\[
Merge_n:Tok^+\times Bind_n\times Obs\to Tok^+
\]

必须满足写足迹约束：

\[
\forall f\notin W_n,\quad Merge_n(\tau^+,\beta,o).f=\tau^+.f
\]

Merge 还必须满足：

1. schema preservation；
2. invariant preservation 或显式 violation event；
3. append-only trace 约束；
4. artifact 不内联，使用 hash / ref；
5. facts 与 belief 不混写；
6. 不得清除未解决的 high-risk flags，除非存在对应 recovery / approval event。

对于 evaluation 节点，Merge 不得通过 `phase = observation.next_phase` 推进 Runtime phase。`next_phase` 只可作为兼容字段或诊断信息被记录；正式 phase 由 Runtime 根据 EG transition 在 commit 前解析。

## 3.11 Belief Update

每个产生 observation 的节点可以声明：

\[
BU_n:Bel\times Obs\times Tok^+\times Bind_n\to Bel
\]

若采用概率更新，可写为：

\[
b'(\omega)=\frac{P(o\mid\omega,n,\beta,\tau^+)b(\omega)}{\sum_{\omega'\in\Omega}P(o\mid\omega',n,\beta,\tau^+)b(\omega')}
\]

若采用近似实现，则 \(BU_n\) 可以是：

- 规则更新；
- 分类器；
- LLM judge；
- embedding case retrieval；
- calibrator；
- ensemble；
- 粒子滤波；
- 人工确认。

Belief update 必须记录 estimator version 和 evidence provenance。

## 3.12 Experience Emission

节点可以产生经验候选：

\[
EmitExp_n:Tok^+\times Bind_n\times Obs\to ExpDelta
\]

经验候选包括：

- 成功 pattern；
- 失败 pattern；
- 反例；
- 新的 retrieval key；
- prior update；
- tester case seed；
- revision proposal hint；
- world model training sample。

经验候选不能直接修改 graph revision，只能写入 experience ledger 或 proposal queue。

## 3.13 节点 Contract 法则

PSOP-EG 节点必须遵守以下 contract 法则：

1. **Guard purity**：guard 无副作用；
2. **Actor isolation**：actor 不直接写正式 Token；
3. **Merge ownership**：正式状态写入只通过 merge；
4. **Write footprint**：merge 不得写出 \(W_n\)；
5. **Trace append-only**：执行必须追加事件；
6. **Belief/fact separation**：信念不得伪装成事实；
7. **Experience quarantine**：经验只作为先验或 proposal，不直接覆盖事实；
8. **Replay metadata**：所有非确定性必须可审计；
9. **Risk monotonicity**：高风险标记不得被普通节点静默清除；
10. **Terminal explicitness**：success / failure / cancelled / escalated 必须有明确终止证据。

---

# 4. 运行语义

## 4.1 单实例配置

单实例配置定义为：

\[
C_t=(\tau_t^+,\omega_t,\eta_t,\mathfrak G_t)
\]

其中：

- \(\tau_t^+\)：扩展 Session Token；
- \(\omega_t\)：外部真实世界状态；
- \(\eta_t\)：runtime handle；
- \(\mathfrak G_t\)：当前 living graph state。

## 4.2 同步

调度前先执行同步：

\[
Sync:Tok^+\times \Omega\times \Eta\times \mathfrak G_t\to Tok^+
\]

\[
\tau_s^+=Sync(\tau_t^+,\omega_t,\eta_t,\mathfrak G_t)
\]

`Sync` 把外部回调、timer、approval result、terminal input、tool callback 等转化为正式事件或待处理 observation。Guard 不直接读取外部世界，只读取同步后的 Token / belief。

## 4.3 经验检索和 prior 装配

运行时可以根据当前状态检索经验：

\[
xref_t=Retrieve(\tau_s^+,\mathcal E_{\le t},Index_t)
\]

并更新扩展 Token：

\[
\tau_r^+=(\tau_s,b_s,xref_t)
\]

经验检索必须带适用边界：

- 适用设备 / 场景 / 站点 / 角色；
- 与当前事实的相似度；
- 被哪些反例否定过；
- 是否需要人工确认；
- 是否只可作为 prior，不可作为 fact。

## 4.4 状态估计

被动观察：

\[
o_t^{passive}\sim O_{passive}(\omega_t)
\]

主动观察动作由 probe policy 决定：

\[
u_t=ProbePolicy(\tau_r^+,\mathfrak G_t)
\]

主动观察：

\[
o_t^{active}\sim O_{u_t}(\omega_t)
\]

状态信念更新：

\[
b_t'=Estimate(b_t,o_t^{passive},o_t^{active},\tau_r^+,\mathfrak G_t)
\]

得到：

\[
\tau_e^+=(\tau_s,b_t',xref_t)
\]

在 MVP 中，如果暂不实现主动 probe，可令 \(u_t=\varnothing\)，仅执行被动同步和结构化 confidence 更新。

## 4.5 Enabled 与 Ready

节点实例空间：

\[
Inst=\bigsqcup_{n\in N}(\{n\}\times Bind_n)
\]

定义 enabled：

\[
Enabled_{\mathcal G}(\tau^+)=\{(n,\beta)\in Inst\mid \beta\in enum_n(\tau^+)\land G_n(\tau^+,\beta)=true\}
\]

定义 ready：

\[
Ready_{\mathcal G}(\tau^+)=\{(n,\beta)\in Enabled_{\mathcal G}(\tau^+)\mid Req_n(\tau^+,\beta)=satisfied\land Guardrails(\tau^+,n,\beta)=pass\}
\]

区别：

- `Enabled`：结构上可进入；
- `Ready`：证据、权限、预算、风险、审批均满足，可以执行 actor。

若 \((n,\beta)\in Enabled\setminus Ready\)，Runner 应进入探测、审批、等待、恢复或失败路径，而不是直接执行 \(n\)。

## 4.6 调度器

多候选节点由调度器选择：

\[
Sel:Tok^+\times \mathcal P(Inst)\times \mathfrak G_t\to Inst
\]

状态感知调度可写为：

\[
Sel(\tau^+,E,\mathfrak G_t)=\arg\max_{(n,\beta)\in E}\left(
\mathbb E_{\omega\sim b}[U(n,\beta,\tau^+,\omega)]
-\lambda Cost(n,\beta)
-\mu Risk(n,\beta,\tau^+)
+\kappa IG(n,\beta,\tau^+)
-\xi Regret(n,\beta,\tau^+)
\right)
\]

其中：

- \(U\)：推进目标或完成任务的期望效用；
- \(Cost\)：token、时间、费用、人工等待等成本；
- \(Risk\)：安全、权限、误判、不可逆操作风险；
- \(IG\)：预期信息增益；
- \(Regret\)：如果选错导致返工或危险的预期损失；
- \(\lambda,\mu,\kappa,\xi\)：策略权重。

若 `Ready` 非空，则 `Sel` 通常从 `Ready` 中选取。若 `Ready` 为空但 `Enabled` 非空，则 `Sel` 应选择可满足缺失 requirement 的辅助节点。

## 4.7 小步转移

给定配置：

\[
C_t=(\tau_t^+,\omega_t,\eta_t,\mathfrak G_t)
\]

经过同步、检索、估计后得到 \(\tau_e^+\)。若存在：

\[
(n,\beta)\in Ready_{\mathcal G}(\tau_e^+)
\]

并且：

\[
v=Project_n(\tau_e^+,\beta)
\]

\[
(\tau_e^+,\beta,v,\omega_t,\eta_t,raw,\omega_{t+1},\eta_{t+1})\in Actor_n
\]

规范化 observation：

\[
o=ObsMap_n(raw,\tau_e^+,\beta)
\]

更新 belief：

\[
b_{t+1}=BU_n(b_t',o,\tau_e^+,\beta)
\]

merge：

\[
\bar\tau^+=Merge_n((\tau_e,b_{t+1},xref_t),\beta,o)
\]

追加 trace 并原子提交：

\[
\tau_{t+1}^+=Commit(append(\bar\tau^+,e),Store)
\]

定义单步转移：

\[
(\tau_t^+,\omega_t,\eta_t,\mathfrak G_t)\xrightarrow{n,\beta,o}(\tau_{t+1}^+,\omega_{t+1},\eta_{t+1},\mathfrak G_t)
\]

若本次执行只产生 experience delta 或 proposal delta，而不发布新 graph，则 \(\mathfrak G_t\) 对当前 run 仍保持 revision 不变；experience ledger 可以 append。

## 4.8 等待、终止与异常状态

定义状态函数：

\[
Status(C)=
\begin{cases}
Success,& h_{succ}(\tau^+)=true\\
Failure,& h_{fail}(\tau^+)=true\\
Escalated,& h_{escalate}(\tau^+)=true\\
Waiting,& h_{wait}(\tau^+)=true\land Ready_{\mathcal G}(\tau^+)=\varnothing\\
Deadlock,& \neg h_{succ}\land\neg h_{fail}\land\neg h_{wait}\land\neg h_{escalate}\land Ready_{\mathcal G}(\tau^+)=\varnothing\\
Running,& \text{otherwise}
\end{cases}
\]

注意：

- `Waiting` 必须有明确等待原因、等待对象和恢复条件；
- `Deadlock` 是既无合法等待理由又无可执行节点；
- `Failure` 应有失败证据和可审计原因；
- `Success` 应满足完成证据和 terminal invariant；
- `Escalated` 表示进入人工或上级系统处理。

## 4.9 全局多实例语义

全局状态：

\[
\mathcal R_t=(A_t,\Omega_t,\Eta_t,\mathfrak G_t)
\]

其中 \(A_t\subseteq Tok^+\) 是活跃实例集合。

多实例选择：

\[
Pick:\mathcal P(Tok^+)\setminus\{\varnothing\}\to Tok^+
\]

若 \(Pick(A_t)=\tau^+\)，单步执行得到 \(\tau'^+\)，则：

\[
A_{t+1}=(A_t\setminus\{\tau^+\})\cup\{\tau'^+\}
\]

`Pick` 需要满足资源、公平性、优先级、deadline 和租约约束。

## 4.10 并发、幂等与原子提交

真实系统中 terminal event、approval callback、timer、tool callback 可能并发到达。为避免状态损坏，PSOP-EG 运行时必须定义：

- snapshot version；
- compare-and-swap commit；
- idempotency key；
- event ordering；
- duplicate callback handling；
- lease / lock；
- conflict resolution；
- retry semantics。

原子提交可表示为：

\[
Commit:Store\times iid\times version\times Event\times Snapshot\to Store\cup\{conflict\}
\]

若版本冲突，Runner 必须重新读取最新 snapshot 并重新计算 enabled nodes，不得基于旧状态继续提交。

## 4.11 Replay Determinism

PSOP-EG 不要求外部世界可逆，也不要求 LLM 再次输出相同内容；它要求**正式状态可回放**。

给定 event log 和 artifact refs，存在：

\[
Replay(Store,iid,rid)\to \tau_T^+
\]

且满足：

\[
Replay(Store,iid,rid)=Snapshot_T(iid)
\]

若不能满足，说明缺少 replay metadata、merge 非确定、artifact 不可用、policy version 缺失或外部副作用未记录。

## 4.12 动态诱导执行图

给定初始配置 \(C_0\)，定义运行时可达图：

\[
\mathbb G_{\mathcal G,C_0}=(V,E)
\]

其中：

\[
V=\{C\mid C_0\to^* C\}
\]

\[
E=\{(C,n,\beta,o,C')\mid C\xrightarrow{n,\beta,o}C'\}
\]

这才是 execution graph 的动态语义。编辑器中的节点依赖图只是保守近似；运行时图由状态、信念、经验和策略共同诱导。

---

# 5. 状态感知、主动探测与推理加速

## 5.1 状态感知的必要性

PSOP 面对的现实环境通常是部分可观测的。系统不能直接知道：

- 设备是否真的修好；
- 照片是否覆盖关键部件；
- 用户是否准确描述问题；
- 工具返回是否可信；
- 审批是否适用于当前风险等级；
- 某条历史经验是否适用于当前站点；
- 某个失败是否可重试。

因此，PSOP-EG 需要显式维护状态信念，并把 belief 纳入 guard、requirement、scheduler 和 prompt projection。

## 5.2 状态命题与证据

令关键状态命题集合为：

\[
Q=\{q_1,q_2,\dots,q_k\}
\]

例如：

- \(q_1\)：照片覆盖关键部件；
- \(q_2\)：设备已断电；
- \(q_3\)：工单确属当前设备；
- \(q_4\)：维修动作为高风险；
- \(q_5\)：用户已确认完成；
- \(q_6\)：审批覆盖当前动作。

Belief 可以表达为：

\[
b(q_i=true)=p_i
\]

要求节点可声明 confidence threshold：

\[
Req_n(\tau^+,\beta)=satisfied \Rightarrow b(q_i=true)\ge p_i^{min}
\]

但对终止类事实，belief 不应替代硬证据。例如关闭工单不能只依赖“模型认为大概率修好了”，还需要正式完成证据。

## 5.3 被动数据采集

被动观察包括：

- terminal input；
- 用户消息；
- 图片、音频、视频上传；
- 设备日志流；
- 外部 API 回调；
- 审批结果；
- timer 到期；
- tool error；
- human note；
- telemetry。

被动观察进入系统后，必须先成为 observation event，再通过 merge 或 belief update 影响状态。

## 5.4 主动环境交互

主动交互包括：

- 要求补拍照片；
- 询问澄清问题；
- 查询历史工单；
- 调用诊断 API；
- 运行安全检查脚本；
- 请求人工复核；
- 调用多模态模型识别现场；
- 执行低风险试探动作；
- 请求审批。

这些动作应建模为 `sense`、`human`、`tool`、`approval` 或 `llm` 节点，不应成为图外隐式副作用。

## 5.5 信息增益

定义 entropy：

\[
H(b)=-\sum_{\omega\in\Omega}b(\omega)\log b(\omega)
\]

执行主动探测 \(u\) 的预期信息增益：

\[
IG(u;b)=H(b)-\mathbb E_{o\sim O_u}[H(Update(b,o,u))]
\]

风险和成本约束下的探测策略：

\[
u^*=\arg\max_{u\in Act}\left(IG(u;b)-\lambda Cost(u)-\mu Risk(u)\right)
\]

当 \(H(b)\) 较高，或关键命题 confidence 未达标时，Runner 应优先执行高信息增益的探测动作。

## 5.6 推理加速

状态感知机制可以加速推理：

1. **减少 prompt 噪音**：只投影当前高概率状态相关的 facts / trace / experience；
2. **减少无效分支**：低概率或不满足证据要求的路径不进入 Ready；
3. **降低工具成本**：先做便宜探测，再执行昂贵工具；
4. **提高缓存命中**：相似 belief state 可以复用相似 prompt summary、tool result 或经验；
5. **避免重复询问**：当关键状态已收敛，不再重复要求用户提供同一证据；
6. **提高安全性**：高风险低置信状态自动进入 probe / approval / escalation。

## 5.7 Belief 校准

若 belief 由模型输出，必须进行校准。可记录：

- predicted confidence；
- observed correctness；
- calibration curve；
- estimator version；
- domain tag；
- out-of-distribution flag。

若某个 estimator 在某类场景中校准误差过高，则该 belief 不得作为高风险节点 requirement 的唯一依据。

---

# 6. 经验闭环与 Tester 驱动演化

## 6.1 Tester Case

定义 tester case：

\[
case=(id,kind,x,env,oracle,expect,forbid,weight,tags)
\]

其中：

- `kind`：positive / negative / reverse / regression / counterfactual；
- \(x\)：输入；
- `env`：模拟环境、外部状态或 mock 工具；
- `oracle`：判定函数、judge 或人工标注；
- `expect`：期望状态、事件、证据、输出或路径属性；
- `forbid`：禁止动作、状态、输出或风险；
- `weight`：用例权重；
- `tags`：业务、风险、场景、版本标签。

## 6.2 正向、负向、反向测试

### 正向测试

正向测试验证：

- 合法输入能启动；
- 合法证据能推进；
- 成功路径可达；
- 成本和步数在阈值内；
- 完成证据满足要求。

### 负向测试

负向测试验证：

- 缺少证据时不能成功；
- 越权操作不能执行；
- 高风险动作必须审批；
- 错误设备或错误图片不能被接受；
- 模型幻觉不能进入 facts；
- 反例命中时必须探测、拒绝、升级或失败。

### 反向测试

反向测试从坏状态、失败终点或 forbidden output 出发，反推可能导致它的前驱条件。

设坏状态集合为 \(Bad\)，定义后向可达：

\[
Pre(B)=\{\tau\mid \exists n,\beta,o,\tau'.\ \tau\xrightarrow{n,\beta,o}\tau'\land \tau'\in B\}
\]

\[
Pre^*(Bad)=\bigcup_{k\ge0}Pre^k(Bad)
\]

反向测试目标是发现：

\[
Reach(Init)\cap Pre^*(Bad)\neq\varnothing
\]

若存在交集，则说明某些 guard、requirement、invariant、probe policy 或 scheduler 需要修订。

## 6.3 测试结果

测试执行结果：

\[
z=(case,trace,verdict,diagnosis,patch\_hint,metrics)
\]

其中：

- `verdict`：pass / fail / flaky / inconclusive；
- `diagnosis`：失败原因；
- `patch_hint`：修复建议；
- `metrics`：成本、步数、belief error、risk violation、latency 等。

常见 diagnosis：

- guard 过宽；
- guard 过窄；
- requirement 缺失；
- belief 误估；
- prompt projection 缺证据；
- scheduler 选错；
- tool mock 不一致；
- merge 写错字段；
- invariant 未覆盖；
- replay metadata 缺失；
- world model 预测误导。

## 6.4 Learn 与 Revision Proposal

学习函数：

\[
Learn:\mathfrak G_t\times Z^*\to (\mathcal E_{t+1},Proposal^*)
\]

它更新 experience ledger，并生成 proposal，但不直接发布新 graph。

Proposal 定义为：

\[
p=(id,base\_rid,change\_set,evidence,affected\_nodes,expected\_benefit,risk,tests,status)
\]

change_set 可包含：

- 新增 guard 条件；
- 修改 requirement；
- 新增 sense 节点；
- 调整 scheduler weight；
- 新增 invariant；
- 修改 prompt projection；
- 新增 recovery path；
- 更新 prior；
- 更新 tester case；
- 标记经验失效。

发布新 revision：

\[
Publish(Proposal,EvalReport,Approval)\to \mathcal G^{r+1}\cup\{rejected\}
\]

## 6.5 经验更新约束

经验闭环必须满足：

### Append-only

经验和 trace 不能物理删除，只能追加 tombstone、supersede 或 invalidation event。

### Safety non-regression

核心安全不变量不得被静默削弱：

\[
Inv_{core}^{r}\subseteq Inv_{core}^{r+1}
\]

若必须变更，需要显式设计评审和 migration note。

### Counterexample retention

反例必须保留复现 trace、环境假设、失败诊断和修复状态。

### Holdout validation

发布前必须通过 holdout tester：

\[
PassRate(C^+_{holdout})\ge p^+
\]

\[
ViolationRate(C^-_{holdout})\le p^-
\]

\[
InvariantViolation(C_{all})=0
\]

## 6.6 经验污染防护

Experience retrieval 可能带来错误迁移。系统必须防止：

- 相似但不同设备的经验误用；
- 已被反例否定的经验继续作为强先验；
- 旧版本工具行为被套用到新版本；
- 模型总结的经验绕过正式证据；
- 经验命中导致 prompt bias；
- 高风险场景中经验替代审批。

因此，每条经验都应带：

- applicability；
- confidence；
- source trace；
- contradicted_by；
- expiry；
- reviewer；
- risk_class；
- allowed_use：prior / prompt_hint / requirement / invariant / forbidden。

---

# 7. 静态分析、验证与发布门禁

## 7.1 编译器

源定义：

\[
Spec=(Schema,NodeDecls,HaltDecls,PolicyDecls,BeliefDecls,ExperienceDecls,InvariantDecls,TestDecls)
\]

编译器：

\[
Compile(Spec)=(\mathcal G^r,\mathbb D_{\mathcal G},Report)
\]

其中静态依赖图：

\[
n\leadsto m\iff W_n\cap R_m\neq\varnothing
\]

\[
\mathbb D_{\mathcal G}=(N,\leadsto)
\]

\(\mathbb D_{\mathcal G}\) 是编译期保守依赖图，不是运行时可达图。

## 7.2 Schema 与类型检查

编译器必须检查：

- Token schema 完整；
- 每个节点的读写字段存在；
- merge 输出满足 schema；
- Project 输出满足模型或工具输入 schema；
- ObsMap 输出满足 observation schema；
- terminal state 有必需证据字段；
- belief 字段不混入 facts；
- artifact ref 带 hash / provenance。

## 7.3 可启动性

若存在入口节点 \(n_0\)，使得：

\[
\forall x\in Input_{valid},\ \exists \beta\in enum_{n_0}(Init(x)),\ G_{n_0}(Init(x),\beta)=true
\]

则 graph 对合法输入可启动。

## 7.4 选择点分析

若存在抽象状态 \(\hat\tau^+\)：

\[
|\widehat{Ready}(\hat\tau^+)|\ge2
\]

则是 choice point。Choice point 不一定是错误，但必须有明确调度策略，且高风险 choice point 需要测试覆盖。

## 7.5 潜在死锁

若存在 \(\hat\tau^+\)：

\[
\widehat{Ready}(\hat\tau^+)=\varnothing
\land \neg\hat h_{succ}
\land \neg\hat h_{fail}
\land \neg\hat h_{wait}
\land \neg\hat h_{escalate}
\]

则报告潜在 deadlock。

## 7.6 Liveness

仅有 safety 不够。系统还需避免无限空转：

\[
running\Rightarrow \Diamond(success\lor failure\lor waiting\lor escalated\lor cancelled)
\]

工程上可通过以下方式近似保证：

- step budget；
- retry budget；
- probe budget；
- deadline；
- wait timeout；
- escalation policy；
- no-progress detector；
- repeated-state detector。

## 7.7 读写冲突与并发

两个节点 \(n,m\) 若满足：

\[
W_n\cap(R_m\cup W_m)\neq\varnothing
\quad\text{or}\quad
W_m\cap(R_n\cup W_n)\neq\varnothing
\]

则存在潜在冲突。并行执行前需要满足：

- 写集不冲突；
- actor 副作用不冲突；
- artifact refs 独立；
- commit 支持版本冲突检测；
- merge 可交换或有确定顺序。

## 7.8 Prompt Budget 可满足性

对 LLM 节点 \(n\)：

\[
TokCount_n(\tau^+,\beta)>B_n
\]

若在可能状态中成立，则报告：

- projection 过宽；
- trace summary 机制缺失；
- experience retrieval 未过滤；
- compaction 不足；
- budget 设置不合理；
- prompt 中包含可由 artifact ref 替代的大对象。

## 7.9 Belief 可观测性

若高风险节点 requirement 依赖命题 \(q\)，但没有任何观察或探测动作能显著降低 \(q\) 的不确定性，则报告 observability gap。

形式上，如果：

\[
\forall u\in Act,\ IG(u;b_q)\approx0
\]

且 \(q\) 是高风险 requirement，则必须改为人工确认、外部权威事实源、拒绝执行或重新设计节点。

## 7.10 Active Sensing 覆盖

对关键命题集合 \(Q_{key}\)，应存在探测策略使得：

\[
\mathbb E[H(b_{t+k})]\le\epsilon
\]

若不存在，说明状态不确定性无法在合理成本内收敛。

## 7.11 Invariant Preservation

对每个 invariant \(\iota\in Inv\) 和节点 \(n\)，检查：

\[
\iota(\tau^+)=true\land \tau^+\xrightarrow{n}\tau'^+\Rightarrow \iota(\tau'^+)=true
\]

若无法证明，则要求测试覆盖或 runtime guardrail。

## 7.12 Safety Theorem

若：

1. 初始状态满足不变量：\(Init(x)\models Inv\)；
2. 每个节点 transition 保持不变量；
3. bad states 均被 invariant 排除：\(Inv\Rightarrow \tau\notin Bad\)；

则：

\[
Reach(Init)\cap Bad=\varnothing
\]

该结论可以作为 PSOP-EG 安全分析的基本定理。

## 7.13 Replay Gate

发布前必须检查：

- event 是否 append-only；
- actor input / output hash 是否记录；
- model version 是否记录；
- policy version 是否记录；
- external callback 是否有 idempotency key；
- artifacts 是否可解析；
- replay 输出是否等于最终 snapshot。

## 7.14 发布门禁

一个 revision 可发布，当且仅当满足：

\[
CompileStatus=pass
\]

\[
ReplayGate=pass
\]

\[
PassRate(C^+)\ge p^+
\]

\[
ViolationRate(C^-)\le p^-
\]

\[
ReverseCounterexampleOpen=0
\]

\[
InvariantViolation=0
\]

\[
HumanApproval=granted\quad\text{if risk class requires}
\]

---

# 8. 数学说明：变换幺半群、不变量、等变性与固定点

## 8.1 转换系统

PSOP-EG 的运行语义首先是一个 labeled transition system：

\[
\mathcal T_{\mathcal G}=(S,\Lambda,\to,s_0)
\]

其中：

- \(S\)：配置空间；
- \(\Lambda\)：标签集合 \((n,\beta,o)\)；
- \(\to\subseteq S\times\Lambda\times S\)：小步转移；
- \(s_0\)：初始配置。

这比静态流程图更准确，因为 PSOP 的执行边由状态动态诱导。

在 Runtime evaluation 节点上，小步转移的下一 phase 由 EG transition 函数决定：

```text
next_phase = transition(node_id, normalized_decision)
```

其中 `normalized_decision` 由 observation 的业务判断得到，`next_phase` 不从模型自填字段取得。若 transition 不存在或目标 phase 不在节点集合中，该小步转移非法，Runtime 必须产生 recoverable failure，而不能先提交成功输出再失败。

## 8.2 节点作为部分变换

忽略外部世界和 nondeterminism 后，每个节点实例可近似为 Token 空间上的部分变换：

\[
f_{n,\beta,o}:Tok^+\rightharpoonup Tok^+
\]

其定义域是满足 guard、requirement 和 observation schema 的状态集合。

所有节点实例生成一个 partial transformation monoid：

\[
\mathsf M_{\mathcal G}=\langle f_{n,\beta,o}\rangle
\]

幺元是 identity transition，组合是连续执行。

这比“群”更合适，因为大多数执行不可逆：trace append、预算消耗、外部副作用、审批、工单关闭都不能简单逆转。

## 8.3 不变量

不变量是谓词：

\[
\iota:Tok^+\to\{true,false\}
\]

若对所有 \(f\in\mathsf M_{\mathcal G}\)：

\[
\iota(\tau)=true\land f(\tau)\downarrow\Rightarrow \iota(f(\tau))=true
\]

则 \(\iota\) 是前向不变量。

典型不变量：

- schema invariant；
- append-only trace；
- high-risk requires approval；
- success requires completion evidence；
- facts require provenance；
- belief cannot overwrite fact；
- artifact ref requires hash；
- wait requires wait reason；
- terminal state requires terminal event。

## 8.4 Bad State Reachability

安全问题可写为：

\[
Reach(Init)\cap Bad=\varnothing
\]

其中 Bad 可包含：

- 无证据成功；
- 越权工具执行；
- 高风险无审批；
- 把模型猜测写入 fact；
- trace 缺失；
- replay 不确定；
- 没有等待理由的停滞；
- 已知反例重复发生。

## 8.5 吸收态与恢复态

某些状态具有吸收性。例如 terminal success：

\[
stat=success\Rightarrow \forall f\in\mathsf M_{\mathcal G},\ f(\tau)=\tau\quad\text{or}\quad f\text{ undefined}
\]

某些坏状态不能被普通节点“洗白”，必须通过 recovery / human review：

\[
HighRiskFlag(\tau)=true\land \neg RecoveryEvidence(\tau)\Rightarrow HighRiskFlag(f(\tau))=true
\]

这比环理想类比更直接，也更适合实现。

## 8.6 相似 Skill 的等变性

设 \(\Gamma\) 是可逆场景变换集合，例如设备型号映射、语言映射、站点配置映射、工具 API 版本映射。

若 \(\gamma\in\Gamma\) 作用于 Token、节点和 observation，并满足：

\[
G_{\gamma n}(\gamma\tau,\gamma\beta)=G_n(\tau,\beta)
\]

\[
Req_{\gamma n}(\gamma\tau,\gamma\beta)=Req_n(\tau,\beta)
\]

\[
Merge_{\gamma n}(\gamma\tau,\gamma\beta,\gamma o)=\gamma Merge_n(\tau,\beta,o)
\]

则称该节点 contract 对 \(\Gamma\) 等变。

直观含义：先把任务映射到相似场景再执行，与先执行再映射结果，结构上一致。

## 8.7 Invariant Core

对相似 skill 族，存在跨场景稳定的不变量核心：

\[
CoreInv=\bigcap_{\gamma\in\Gamma}\gamma(Inv)
\]

典型 core invariants：

- 证据不足不能 success；
- 高风险必须审批；
- trace append-only；
- wait 必须有等待理由；
- facts 必须有 provenance；
- 模型预测不能直接写 fact；
- terminal 必须可 replay。

这些不变量构成 PSOP-EG 指导相似 skill 的稳定核。

## 8.8 Learning Fixed Point

定义测试和运行数据分布 \(D\) 下的更新算子：

\[
L_D(\mathfrak G_t)=Learn(\mathfrak G_t,Z_D)
\]

若存在 \(\mathfrak G^*\)：

\[
L_D(\mathfrak G^*)\equiv \mathfrak G^*
\]

则称其为测试分布 \(D\) 下的固定点。

工程上要求近似固定点：

\[
Dist(\mathfrak G_{t+1},\mathfrak G_t)\le\delta
\]

且：

\[
PassRate(C^+)\ge p^+
\]

\[
ViolationRate(C^-)\le p^-
\]

\[
InvariantViolation=0
\]

\[
BeliefError\le\epsilon
\]

这意味着 graph 在当前任务族上基本稳定，新经验主要改变 prior 和索引，而不频繁改变核心结构。

---

# 9. 场景级世界模型

## 9.1 定义

场景级世界模型不是 PSOP-EG 本身，而是可被 PSOP-EG 调用的预测模型：

\[
W_\phi:(\tau_t^+,a_t,context)\to Distribution(o_{t+1},\Delta\tau,risk,cost,terminal,b_{t+1})
\]

它预测：

- 下一步 observation；
- 可能的 Token delta；
- 风险；
- 成本；
- 是否 terminal；
- belief 如何变化。

## 9.2 训练数据

PSOP 天然产生训练世界模型的数据：

\[
(\tau_t^+,a_t,o_{t+1},\tau_{t+1}^+,risk,cost,terminal,verdict)
\]

来源包括：

- 真实运行 trace；
- replay；
- positive tester；
- negative tester；
- reverse tester；
- human review；
- tool mock；
- environment simulator。

## 9.3 世界模型的用途

世界模型可用于：

1. **主动探测选择**：预测哪个探测动作最能降低不确定性；
2. **调度加速**：预测节点成功率、成本和风险；
3. **测试生成**：生成高风险、边界、长尾和反事实 case；
4. **离线策略评估**：在模拟环境中评估 scheduler；
5. **失败预警**：预测 run 是否将进入 deadlock、failure 或 escalation；
6. **经验泛化**：把相似场景的 trace 总结成 transferable prior。

## 9.4 安全边界

世界模型必须满足：

- 只能写入 belief、prediction、proposal 或 test case；
- 不能直接写 facts；
- 不能绕过 guard、requirement、approval；
- 不能替代真实完成证据；
- 高风险预测必须可解释并带 provenance；
- 预测错误必须进入 evaluation metrics。

正式规则：

\[
WorldModelOutput\not\Rightarrow Fact
\]

除非经过真实观察、人工确认或权威工具验证。

## 9.5 世界模型 API

建议定义以下接口：

```text
predict_next(token, action, context) -> Prediction
estimate_risk(token, action, context) -> RiskEstimate
estimate_info_gain(token, probe_action, context) -> InfoGainEstimate
generate_counterfactual(token, bad_state, constraints) -> TestCase[]
simulate_rollout(token, policy, horizon) -> SimTrace[]
calibrate(predictions, realized_events) -> CalibrationReport
```

## 9.6 训练目标

可组合多个目标：

\[
\mathcal L=\mathcal L_{obs}+\alpha\mathcal L_{delta}+\beta\mathcal L_{risk}+\gamma\mathcal L_{terminal}+\rho\mathcal L_{calib}
\]

其中：

- \(\mathcal L_{obs}\)：下一 observation 预测；
- \(\mathcal L_{delta}\)：Token delta 预测；
- \(\mathcal L_{risk}\)：风险预测；
- \(\mathcal L_{terminal}\)：终止状态预测；
- \(\mathcal L_{calib}\)：置信度校准。

## 9.7 与 PSOP-EG 的关系

PSOP-EG 是治理型 reasoner；世界模型是预测型 reasoner。二者关系：

\[
Observation\to Belief\to GovernedAction
\]

世界模型提供预测和模拟，PSOP-EG 决定哪些预测可用、如何受约束、何时需要真实证据、何时升级人工。

---

# 10. 设备维保参考实例

## 10.1 场景

输入是一张设备维修工单。目标是指导工程师完成核验、诊断、维修计划、审批、执行和关闭。

节点集合示例：

\[
N=\{n_0,n_1,\dots,n_{12}\}
\]

- \(n_0\)：`bootstrap_workorder`；
- \(n_1\)：`retrieve_prior_experience`；
- \(n_2\)：`request_machine_photo`；
- \(n_3\)：`verify_photo_coverage`；
- \(n_4\)：`query_service_history`；
- \(n_5\)：`diagnose_fault_code`；
- \(n_6\)：`active_probe_missing_state`；
- \(n_7\)：`plan_repair_llm`；
- \(n_8\)：`risk_classify_repair`；
- \(n_9\)：`request_human_approval`；
- \(n_{10}\)：`invoke_external_repair_skill`；
- \(n_{11}\)：`verify_completion_evidence`；
- \(n_{12}\)：`close_or_fail_ticket`。

## 10.2 初始化

\[
Init(x)=\tau_0^+=(\tau_0,b_0,xref_0)
\]

其中：

- \(\tau_0\)：包含工单、设备 ID、站点、工程师、权限、故障描述、初始预算；
- \(b_0\)：由设备型号、故障码、历史工单生成的初始 belief；
- \(xref_0\)：初始命中的经验引用。

## 10.3 经验检索节点

`retrieve_prior_experience` 的 guard：

\[
G_{n_1}(\tau^+,\star)=\tau.reg.prior\_loaded=false
\]

merge 写入：

- `mem.experience_summary`；
- `reg.prior_loaded=true`；
- `xref.matched_cases`；
- belief prior update。

## 10.4 照片核验节点

`verify_photo_coverage` 的 requirement：

\[
Req_{n_3}(\tau^+,photo)=satisfied
\]

当且仅当：

- 存在 photo artifact ref；
- artifact hash 有效；
- photo provenance 合法；
- 设备 ID 与工单匹配或待核验；
- prompt budget 足够。

LLM 或视觉模型输出 observation：

```json
{
  "coverage_ok": true,
  "visible_parts": ["panel", "serial", "fault_indicator"],
  "missing_parts": [],
  "confidence": 0.91,
  "risk_flags": []
}
```

merge 只写入验证结果和证据引用，不直接关闭工单。

## 10.5 主动探测节点

若：

\[
H(b)>\epsilon
\]

或：

\[
b(q_{photo\_coverage}=true)<p_{min}
\]

则 `active_probe_missing_state` enabled。它可选择：

- 请求补拍；
- 查询历史；
- 询问工程师；
- 调用诊断工具；
- 请求人工复核。

调度器基于信息增益选择动作。

## 10.6 维修计划节点

`plan_repair_llm` 的 guard：

\[
G_{n_7}(\tau^+,\star)=
reg.photo\_verified=true
\land
(reg.history\_loaded=true\lor reg.diagnosis\_done=true)
\land
reg.repair\_plan\_ready=false
\]

requirement：

\[
Req_{n_7}=satisfied
\]

需要：

- 关键状态 confidence 达标；
- 没有命中 blocking negative case；
- prompt budget 满足；
- 工具/模型权限满足。

## 10.7 审批不变量

若维修动作高风险：

\[
HighRisk(\tau^+)\Rightarrow ctrl.approval\_granted=true
\]

否则 `invoke_external_repair_skill` 不得 ready。

## 10.8 关闭工单不变量

关闭工单必须满足：

\[
CloseTicket(\tau^+)\Rightarrow CompletionEvidence(\tau^+)\land UserOrEngineerConfirm(\tau^+)\land Replayable(\tau^+)
\]

模型生成的“看起来完成”不能替代完成证据。

## 10.9 Tester 示例

正向 case：

- 清晰照片；
- 故障码与历史相符；
- 审批通过；
- 期望成功关闭；
- 检查平均步数和成本。

负向 case：

- 照片缺关键部件；
- 文本声称已修好；
- 禁止直接关闭；
- 期望请求补拍或人工复核。

反向 case：

- 目标坏状态：无完成证据但 success；
- 反推可能路径；
- 发现 `close_or_fail_ticket` guard 缺失 `CompletionEvidence`；
- 生成 proposal：补充 requirement 和 negative test。

## 10.10 世界模型在该场景中的使用

世界模型可以预测：

- 要求补拍是否可能获得有效照片；
- 某故障码对应哪类维修路径成功率高；
- 当前 run 是否可能进入 deadlock；
- 某工具调用是否高延迟或易失败；
- 哪些反事实 case 应加入 regression。

但世界模型预测不得直接关闭工单，也不得跳过审批。

---

# 11. 工程映射与 Runner Step 伪代码

## 11.1 数据表建议

建议至少包含：

- `eg_revision`：graph revision；
- `node_contract`：节点定义；
- `run`：运行实例；
- `run_snapshot`：Session Token snapshot；
- `run_event`：append-only trace event；
- `artifact`：证据和大对象引用；
- `belief_record`：状态信念记录；
- `experience_entry`：经验账本；
- `test_case`：tester case；
- `test_run`：测试结果；
- `revision_proposal`：图更新提案；
- `metric`：评估指标；
- `world_model_prediction`：世界模型预测和校准数据。

## 11.2 Runner Step 伪代码

```python
def runner_step(run_id: str) -> StepResult:
    # 1. Load canonical state
    snapshot = store.load_latest_snapshot(run_id)
    graph = store.load_graph_revision(snapshot.rid)

    # 2. Acquire lease and sync external events
    with store.lease(run_id, snapshot.version) as lease:
        token = sync_external_events(snapshot.token, graph)

        # 3. Retrieve experience and update belief
        xref = retrieve_experience(token, graph)
        token = token.with_experience_refs(xref)
        observations = collect_passive_observations(token, graph)
        token = estimate_belief(token, observations, graph)

        # 4. Compute enabled and ready node instances
        enabled = compute_enabled(token, graph)
        ready = [x for x in enabled if requirements_satisfied(token, x, graph)]

        # 5. If no ready node, try to satisfy missing requirements
        if not ready:
            helper = choose_probe_or_wait_or_escalate(token, enabled, graph)
            if helper is None:
                return mark_deadlock_or_wait(token, graph, lease)
            ready = [helper]

        # 6. Select next step
        node_inst = select_next(token, ready, graph)

        # 7. Project input and enforce budget / guardrails
        view = project_input(token, node_inst, graph)
        view = compact_if_needed(view, token, node_inst, graph)
        guardrails.check(token, node_inst, view, graph)

        # 8. Execute actor and normalize observation
        raw = execute_actor(node_inst, view)
        obs = normalize_observation(raw, token, node_inst, graph)

        # 9. Update belief, merge formal state, emit experience delta
        token = update_belief(token, obs, node_inst, graph)
        token = merge_observation(token, obs, node_inst, graph)
        exp_delta = emit_experience_delta(token, obs, node_inst, graph)

        # 10. Append event and commit atomically
        event = build_event(token, node_inst, view, obs, exp_delta, graph)
        new_snapshot = append_event_and_snapshot(token, event)
        store.commit(run_id, lease.version, event, new_snapshot, exp_delta)

    return StepResult(snapshot=new_snapshot, event=event)
```

## 11.3 YAML 风格节点示例

```yaml
id: close_or_fail_ticket
kind: terminal
read:
  - facts.completion_evidence
  - facts.engineer_confirmation
  - ctrl.approval_granted
  - belief.device_repaired
  - xref.blocking_negative_cases
write:
  - stat
  - facts.ticket_close_result
  - trace

guard: |
  reg.repair_attempted == true

requirements:
  - facts.completion_evidence.exists == true
  - facts.engineer_confirmation.exists == true
  - xref.blocking_negative_cases.empty == true
  - if risk.high == true then ctrl.approval_granted == true
  - belief.device_repaired.confidence >= 0.85

actor:
  type: tool
  name: ticketing.close_or_fail

merge:
  schema: CloseTicketObservation
  writes:
    success:
      stat: success
      facts.ticket_close_result: $.result
    failure:
      stat: failure
      facts.ticket_close_result: $.error

invariants:
  - success_requires_completion_evidence
  - high_risk_requires_approval
  - terminal_event_required
```

## 11.4 MVP 落地优先级

建议按以下阶段落地：

### Phase 1：状态主权和 guarded rewrite

- Session Token snapshot；
- append-only events；
- node guard；
- actor / observation / merge 分离；
- success / failure / waiting / deadlock；
- replay metadata。

### Phase 2：requirement 和 safety invariant

- evidence requirement；
- approval gate；
- risk class；
- prompt budget；
- static report；
- negative tester。

### Phase 3：belief 和 active sensing

- belief record；
- confidence threshold；
- sense node；
- info gain heuristic；
- prompt pruning；
- belief calibration。

### Phase 4：experience ledger 和 revision proposal

- positive / negative / reverse tester；
- experience entry；
- proposal queue；
- regression gate；
- publish / rollback。

### Phase 5：场景级世界模型

- next observation prediction；
- risk prediction；
- test generation；
- simulation rollout；
- calibration；
- offline policy evaluation。

---

# 12. 结语

v5.1 将 PSOP Execution Graph 定义为一种 **状态主权明确、部分可观测、测试驱动演化、可审计回放的智能体执行图**。

它的核心不在于把 agent 画成图，而在于把以下对象放到同一个形式系统中：

1. Session Token：单次运行的正式状态；
2. Node Contract：可执行动作的证据、权限、风险和状态重写契约；
3. Belief State：对不可完全观测外部世界的状态估计；
4. Experience Ledger：跨运行、跨测试、跨版本的先验经验与反例；
5. Guarded Rewrite：运行时推理和执行的基本语义；
6. Invariant / Reachability / Liveness：安全与可推进性的分析对象；
7. Tester Loop：graph 演化的受控学习机制；
8. World Model：可选的预测、仿真和测试生成模块。

因此，PSOP-EG 可以概括为：

> **PSOP Execution Graph 是 Runner / Agent Harness 内的 governed reasoner。它以 Session Token 维护正式事务状态，以节点 contract 约束动作，以 belief 管理外部不确定性，以 experience ledger 沉淀先验和反例，并通过 tester 与 revision proposal 形成可审计、可验证、可持续演化的智能体执行系统。**
