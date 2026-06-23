# PSOP Execution Graph 正式版
## 面向 Agent Harness 的 Session Token 形式化定义

---

## 摘要

本文给出 PSOP Execution Graph（简称 **PSOP-EG**）的一版正式化定义。与旧稿中以 Place / Marking / Token Flow 为核心的建模方式不同，本文将 PSOP-EG 重新界定为一个**面向 Agent Harness 的控制核（control kernel）**：静态上，它是由有限节点、类型系统、停机条件与策略注解构成的编译对象；动态上，它以 **Session Token / Instance Token** 作为一等运行时对象，通过 Guard 判定节点可执行性，通过调度器在多个候选节点中选步，通过节点执行结果对 Session Token 做受控重写，从而推进现实事务。

为了与现代大模型系统保持概念兼容，本文进一步引入三层 token 结构：**Session Token、Prompt View、Model Tokens**。其中 Session Token 是事务实例的语义状态，Prompt View 是某个节点在当前时刻向模型暴露的上下文投影，Model Tokens 则是该视图经过编码后得到的词法 token 序列。由此，PSOP-EG 既可以保持“统一实例状态驱动执行”的语义一致性，又能自然接入 Agent Harness 中的上下文编译、压缩、工具执行、审批与 tracing 机制。

全文分为五章。第一章说明设计立场与概念边界；第二章给出核心对象与形式定义；第三章给出运行语义；第四章给出编译器、静态分析与 Harness Runtime 的定义；第五章给出设备维保场景下的参考实例，用于说明本形式系统如何落到工程实现。

---

## 目录

1. [引言与设计定位](#1-引言与设计定位)  
2. [核心对象与形式系统](#2-核心对象与形式系统)  
3. [运行语义](#3-运行语义)  
4. [编译器、静态分析与 Harness Runtime](#4-编译器静态分析与-harness-runtime)  
5. [设备维保场景下的参考实例](#5-设备维保场景下的参考实例)  

---

# 1. 引言与设计定位

## 1.1 问题背景

PSOP-EG 试图回答的不是“如何在封闭符号系统中做纯计算”，而是“如何在开放现实环境中，让智能体围绕某个事务实例持续推进执行”。这类系统通常具有以下共同特征：

1. 一个事务往往由若干**固定步骤**组成，例如采集外部信息、核验状态、调用代码、调用外部工具、调用大模型、调用第三方 skill、等待审批、结束归档等；
2. 步骤是否可执行，不只由静态流程边决定，更取决于当前上下文中已经累积的事实、历史、寄存器、外部反馈与控制状态；
3. 系统在执行过程中需要保留可追溯的历史，能够在必要时做回放、审计、解释与再调度；
4. 系统并非始终处于纯内部计算状态，它可能等待用户输入、等待工程师上传照片、等待外部系统回调、等待审批或定时器到期；
5. 某个时刻可能有多个步骤同时可执行，也可能没有任何步骤可执行；这两类情况都需要成为语义与静态分析的一部分。

如果仍然沿用“Token 在 Place 间流动”的经典工作流视角，那么会遇到三个明显问题：

- **问题一：实例语义被稀释。** 现实系统中最重要的对象往往不是某个轻量 token，而是“一个正在运行的事务实例”。它有自己的目标、事实、暂存器、记忆、trace、预算、等待原因与状态机；
- **问题二：边主导的流程图过强。** 节点之间的后继关系常常并不是预先手工连好的，而是由“当前状态能激活哪些节点”动态诱导出来的；
- **问题三：难以对接现代 Agent Runtime。** 在今天的 Agent Harness 中，真正驱动系统的通常是 session state、context compilation、prompt budget、tool host、approval 和 trace bus，而不是静态 place/arc 图本身。

因此，PSOP-EG 需要从“CPN 风格的 token-flow 模型”转向“面向 Agent Harness 的 session-driven guarded rewrite system”。

## 1.2 设计立场

本文采取如下设计立场：

**（1）节点是静态的，边是派生的。**  
PSOP-EG 中的节点集合由设计者静态给定；节点之间不存在必须预先声明的固定连接线。某个节点是否能在另一个节点之后执行，不由图上的显式边决定，而由前一步执行后得到的新 Session Token 是否使后者 guard 成立决定。

**（2）Token 是实例，而不是流动物。**  
每个运行中的事务实例都对应一个 Session Token。执行器推进任务的方式不是“让 token 在 place 之间流动”，而是“对某个 Session Token 反复执行 guarded rewrite”。

**（3）Guard 作用于统一状态。**  
节点 guard 不应依赖外部隐式连线，而应只依赖当前 Session Token（必要时先通过 ingress/sync 把外部世界投影到 Token 中），从而保证可解释性、可检查性与可测试性。

**（4）PSOP-EG 是 Agent Harness 的控制核，而不是 Harness 全部。**  
PSOP-EG 负责节点、guard、enabledness、状态重写、调度与终止语义；完整的 Harness Runtime 还需要负责状态持久化、上下文编译、prompt 压缩、工具宿主、审批、守护策略、trace bus 与评估闭环。

## 1.3 与 LLM Token 的关系

为了避免概念混淆，本文明确区分三层对象：

\[
\text{Session Token}
\xrightarrow{\text{Project / Render}}
\text{Prompt View}
\xrightarrow{\text{Encode}}
\text{Model Tokens}
\]

- **Session Token**：运行实例的语义状态；
- **Prompt View**：某个节点在当前时刻暴露给模型的上下文视图；
- **Model Tokens**：Prompt View 经过 tokenizer / encoder 后得到的词法 token 序列。

因此，本文允许在术语上让 PSOP 的 token 向 LLM 语境“靠一靠”，但不把两者直接等同：Session Token 是**事务实例对象**，Model Tokens 是**模型输入的词法单元**。二者之间通过上下文投影与编码链条连接。

## 1.4 本文贡献

相对于旧稿，本文完成了以下替换：

1. 以 **Session Token** 取代 Place/Marking 作为运行时本体；
2. 以 **Guarded Rewrite** 取代 Token Flow 作为基础运行语义；
3. 以 **动态诱导图** 取代固定弧连接作为“execution graph”的主要语义来源；
4. 以 **Harness Runtime** 取代抽象执行器的狭义表述，使其更贴近现代 Agent 系统；
5. 把 **Prompt Projection / Budget / Compaction** 正式纳入模型定义，使其能够与 LLM 执行环境自然对齐。

---

# 2. 核心对象与形式系统

## 2.1 基本集合与符号约定

为避免歧义，本文使用以下基本集合：

- \(Input\)：外部输入空间；
- \(InstId\)：实例标识空间；
- \(GraphId\)：图定义标识与版本空间；
- \(Goal\)：目标与子目标描述空间；
- \(Meta\)：实例元信息空间；
- \(Facts\)：已知事实与产物空间；
- \(Reg\)：工作寄存器空间；
- \(Mem\)：持久记忆/摘要空间；
- \(Trace\)：执行轨迹空间；
- \(Ctrl\)：控制字段空间；
- \(Stat\)：实例状态空间；
- \(\Omega\)：外部世界状态空间；
- \(\Eta\)：Harness 内部句柄与会话资源空间；
- \(Msg\)：消息对象空间；
- \(V\)：模型词表；
- \(Obs\)：节点执行产生的观察结果空间。

其中 \(Stat\) 至少包含以下原子状态：

\[
Stat \supseteq \{running, waiting, success, failure, deadlock\}
\]

## 2.2 Session Token

**定义 2.1（Session Token）**  
PSOP-EG 的一等运行时对象定义为：

\[
Tok = InstId \times GraphId \times Goal \times Meta \times Facts \times Reg \times Mem \times Trace \times Ctrl \times Stat
\]

记一个具体 Session Token 为：

\[
\tau=(iid,gid,goal,m,f,r,mem,h,c,s) \in Tok
\]

其分量含义如下：

- \(iid\)：实例唯一标识；
- \(gid\)：所属 graph/skill 的定义版本；
- \(goal\)：当前主目标与子目标栈；
- \(m\)：相对稳定的元信息，如工单、设备、工程师、权限、策略参数；
- \(f\)：当前已知事实与产物，如照片、日志、工况、API 返回、用户回复、审批结论；
- \(r\)：工作寄存器，用于保存执行中的中间结构；
- \(mem\)：长时记忆、摘要、检索索引或压缩后的历史视图；
- \(h\)：append-only trace；
- \(c\)：控制字段，如预算、重试、等待原因、截止时间、租约、审批状态、并发锁；
- \(s\)：当前实例状态。

> 在本文中，一个 Session Token 就是一个运行中的 graph instance / agent session。

这一定义直接取代旧稿中的 \((M,\kappa,\xi)\) 三分式本体。Marking 不再是首要对象；真正的一等对象是 Session Token。

## 2.3 Trace 与事件

**定义 2.2（Trace）**  
执行轨迹定义为事件序列：

\[
Trace = Event^*
\]

单个事件记为：

\[
e=(nid,kind,\beta,status,obs\_ref,\Delta,cost,ts_b,ts_e,summary)
\]

其中：

- \(nid\)：节点标识；
- \(kind\)：节点类型；
- \(\beta\)：本次执行所采用的局部绑定；
- \(status\)：success / failure / timeout / retryable / rejected 等；
- \(obs\_ref\)：观察结果引用或摘要；
- \(\Delta\)：对 Session Token 的关键增量摘要；
- \(cost\)：资源成本，如 token 使用量、调用时长、费用估算；
- \(ts_b, ts_e\)：开始与结束时间；
- \(summary\)：供审计、回放与推理使用的事件摘要。

Trace 必须满足 append-only 约束：既有事件不得就地篡改，只能通过追加新事件表达新的结论或补充信息。

## 2.4 外部世界与 Harness 句柄

虽然 Session Token 是运行时本体，但系统仍运行在开放环境中。因此引入：

\[
\omega \in \Omega
\]

表示当前外部世界状态，例如：

- 外部系统真实状态；
- 当前时间；
- 用户是否已回复；
- 工程师是否已上传照片；
- 某个第三方 skill 或工具是否可用。

同时引入 Harness 内部句柄：

\[
\eta \in \Eta
\]

用于表示运行时并不直接写入 Token 的宿主资源，例如：

- 会话连接；
- 工具 host 句柄；
- 缓存与租约；
- 审批上下文；
- 沙箱执行环境；
- 遥测与 tracing channel。

## 2.5 节点定义

设 \(N\) 为非空有限集合，称为节点集合。每个节点 \(n\in N\) 定义为：

\[
n=(id_n,kind_n,Bind_n,enum_n,R_n,W_n,g_n,\Pi_n,a_n,m_n,\pi_n)
\]

其中：

### 2.5.1 节点类型

\[
kind_n \in K
\]

可取：

\[
K=\{start,input,code,llm,tool,skill,timer,approval,terminal\}
\]

它们分别表示：

- `start`：启动/初始化节点；
- `input`：接收外部输入或回调；
- `code`：执行确定性代码；
- `llm`：调用大模型推理；
- `tool`：调用 API、数据库、MCP 或工具；
- `skill`：调用外部 skill（例如 Claude skill）；
- `timer`：由时间条件触发；
- `approval`：审批/人工确认节点；
- `terminal`：显式终结节点。

### 2.5.2 局部绑定空间

某些节点需要从统一 Token 中选择一个局部对象来执行，因此定义：

\[
\beta \in Bind_n
\]

以及绑定枚举函数：

\[
enum_n : Tok \to \mathcal P(Bind_n)
\]

若某节点不需要绑定，则令：

\[
Bind_n=\{\star\}, \qquad enum_n(\tau)=\{\star\}
\]

### 2.5.3 读写足迹

- \(R_n \subseteq Fields(Tok)\)：节点可能读取的 Token 字段集合；
- \(W_n \subseteq Fields(Tok)\)：节点可能写入的 Token 字段集合。

它们用于静态分析、冲突检测、增量重算与并行可行性判断。

### 2.5.4 Guard

**定义 2.3（Guard）**  
每个节点具有纯 guard：

\[
g_n : Tok \times Bind_n \to \{true,false\}
\]

Guard 必须满足：

1. 纯函数；
2. 总定义；
3. 在图定义中静态给定；
4. 只依赖 Token 中可获得的信息。

这意味着外部世界的变化若要影响 guard，必须先经由 ingress / sync 写入 Token，再参与判断。

### 2.5.5 Prompt Projection

为了让 PSOP-EG 与 LLM runtime 对齐，对每个需要模型参与的节点引入上下文投影函数：

\[
\Pi_n : Tok \times Bind_n \to Msg^*
\]

其输出是某个节点在当前实例状态下对模型暴露的 Prompt View。它不要求等于整个 Session Token，而通常只投影“当前节点真正需要的信息子集”。

进一步地，令：

\[
Encode_n : Msg^* \to V^*
\]

则该节点一次模型调用的 token 用量可写为：

\[
TokCount_n(\tau,\beta)=\left|Encode_n(\Pi_n(\tau,\beta))\right|
\]

若节点 \(n\) 的上下文预算为 \(B_n\in\mathbb N\)，则期望满足：

\[
TokCount_n(\tau,\beta) \le B_n
\]

否则 Harness Runtime 必须在节点执行前先做压缩、裁剪或摘要更新。

### 2.5.6 Actor / 执行关系

**定义 2.4（Actor）**  
节点的实际执行语义定义为一个关系：

\[
a_n \subseteq Tok \times Bind_n \times \Omega \times \Eta \times Obs \times \Omega \times \Eta
\]

记

\[
(\tau,\beta,\omega,\eta,o,\omega',\eta') \in a_n
\]

表示：在当前 Token、绑定、外部世界与 Harness 句柄下，执行节点 \(n\) 可能产生观察结果 \(o\)，并使外部世界与句柄分别演化到 \(\omega'\) 与 \(\eta'\)。

把执行语义定义为关系而不是纯函数，是为了显式容纳现实系统中的非确定性：

- LLM 输出并不唯一；
- 外部 API 可能成功、失败或超时；
- 人工审批可能同意或拒绝；
- 用户可能给出多种不同输入；
- 第三方 skill 可能返回不同置信度与建议。

### 2.5.7 Merge / Token 更新函数

节点执行之后，需要把观察结果并入 Session Token。定义：

\[
m_n : Tok \times Bind_n \times Obs \to Tok
\]

要求：

\[
\forall f\notin W_n,\quad m_n(\tau,\beta,o).f = \tau.f
\]

即节点只能写入自己声明过的字段；其他字段保持不变。

### 2.5.8 节点策略注解

\(\pi_n\) 是编译期或调度期注解，可包含：

- 优先级；
- 成本估计；
- 最大重试次数；
- 超时；
- 是否允许 LLM 择优；
- 是否要求审批；
- 预算阈值 \(B_n\)；
- 是否允许与其他节点并行。

## 2.6 Graph 定义

**定义 2.5（PSOP Execution Graph）**  
一个编译后的 PSOP Execution Graph 定义为：

\[
\mathcal G=(\Sigma,N,Init,H,I,Pol)
\]

其中：

- \(\Sigma\)：类型系统与字段 schema；
- \(N\)：有限节点集合；
- \(Init : Input \to Tok\)：初始 Session Token 构造器；
- \(H=(h_{succ},h_{fail},h_{wait})\)：成功、失败、等待谓词；
- \(I=\{\iota_1,\dots,\iota_k\}\)：Token 不变量集合；
- \(Pol\)：全局策略与默认调度注解。

其中：

\[
h_{succ},h_{fail},h_{wait}: Tok \to \{true,false\}
\]

分别表示：成功终止条件、失败终止条件与正常等待条件。

为保证可启动性，通常要求存在入口节点 \(n_0\in N\)，使得：

\[
\forall x\in Input,\ \exists \beta\in enum_{n_0}(Init(x)),\ g_{n_0}(Init(x),\beta)=true
\]

当 \(Bind_{n_0}=\{\star\}\) 时，上式简化为无条件入口 guard。

## 2.7 Harness Runtime 的抽象定义

PSOP-EG 本身并不是整个 Agent Harness。完整运行时应定义为：

\[
\mathcal H=(\mathcal G,Store,Sync,Pick,Sel,Comp,ToolHost,Approval,Guardrails,TraceBus,EvalLoop)
\]

其中：

- `Store`：Session Token 的持久化与版本化存储；
- `Sync`：把外部世界变化写入 Token 的同步函数；
- `Pick`：多实例调度器，在活跃实例中选一个待推进实例；
- `Sel`：实例内调度器，在 enabled 节点中选一个待执行节点；
- `Comp`：上下文压缩、摘要与 budget 控制组件；
- `ToolHost`：工具、MCP、代码与外部 skill 的宿主；
- `Approval`：审批与人机边界；
- `Guardrails`：策略、安全与权限检查；
- `TraceBus`：事件总线、遥测与 tracing；
- `EvalLoop`：离线/在线评估与改进闭环。

因此，PSOP-EG 的正确定位是：

> **PSOP-EG 是 Harness Runtime 的控制核；Harness Runtime 是 PSOP-EG 的执行宿主。**

---

# 3. 运行语义

## 3.1 运行配置与同步步骤

单实例运行配置定义为：

\[
C=(\tau,\omega,\eta)
\]

其中：

- \(\tau\)：当前 Session Token；
- \(\omega\)：当前外部世界状态；
- \(\eta\)：当前 Harness 句柄。

为了保持 guard 只依赖 Token，运行时在每个调度周期开始前允许做一次同步：

\[
Sync : Tok \times \Omega \times \Eta \to Tok
\]

记：

\[
\tau^{\sharp}=Sync(\tau,\omega,\eta)
\]

`Sync` 的作用是把新的外部输入、回调、定时器事实、审批结果等物化到 Token 中。此后，enabledness 计算一律基于 \(\tau^{\sharp}\) 进行，从而保持 guard 纯粹性。

## 3.2 可执行节点实例集合

先定义节点实例空间：

\[
Inst = \bigsqcup_{n\in N}(\{n\}\times Bind_n)
\]

**定义 3.1（Enabled）**  
给定 Session Token \(\tau\)，当前可执行节点实例集合定义为：

\[
Enabled_{\mathcal G}(\tau)=\{(n,\beta)\in \mathrm{Inst} \mid \beta\in \mathrm{enum}_n(\tau) \land g_n(\tau,\beta)=\mathrm{true}\}
\]

这一定义体现了本文的核心主张：

- 节点之间没有固定“流向边”；
- 是否可执行由 Token 诱导；
- 节点执行后的新 Token 决定下一批 enabled nodes。

## 3.3 调度器

若当前存在多个可执行节点实例，则需要一个实例内调度器：

\[
Sel : Tok \times \bigl(\mathcal P(Inst)\setminus\{\varnothing\}\bigr) \to Inst
\]

满足：

\[
Sel(\tau,E)\in E
\]

`Sel` 可以是：

- 静态优先级规则；
- 启发式成本规则；
- LLM 择优；
- 人工仲裁；
- 混合策略。

当多个节点同时 enabled 时，这不是语义错误，而是显式的 choice point。系统必须依赖 `Sel` 来决定下一步执行谁。

## 3.4 Prompt View 与预算控制

对于 LLM 节点 \(n\)，若当前选中了节点实例 \((n,\beta)\)，则其模型输入由：

\[
\Pi_n(\tau,\beta)
\]

给出。若：

\[
TokCount_n(\tau,\beta) > B_n
\]

则 Harness Runtime 必须先应用压缩器：

\[
Comp_n : Tok \to Tok
\]

使得：

\[
TokCount_n(Comp_n(\tau),\beta) \le B_n
\]

然后再执行节点。这样，Session Token 与 Model Tokens 的关系就被正式纳入运行语义，而不是作为实现细节被隐含掉。

## 3.5 小步转移

**定义 3.2（单步转移）**  
设当前配置为 \(C=(\tau,\omega,\eta)\)。若某个节点实例 \((n,\beta)\in Enabled_{\mathcal G}(\tau)\)，且：

\[
(\tau,\beta,\omega,\eta,o,\omega',\eta')\in a_n
\]

令：

\[
\bar\tau = m_n(\tau,\beta,o)
\]

再由系统统一追加事件：

\[
\tau' = append(\bar\tau,e)
\]

其中 \(e\) 是本次执行事件，则定义一步转移：

\[
(\tau,\omega,\eta) \xrightarrow{n,\beta,o} (\tau',\omega',\eta')
\]

其中：

\[
e=(id_n,kind_n,\beta,status,o,\Delta,cost,ts_b,ts_e,summary)
\]

需要注意的是：

1. `guard` 只负责判定，不做副作用；
2. `actor` 负责真实执行，包括 LLM 调用、工具调用、外部 skill、代码与审批；
3. `merge` 负责把观察结果吸收到 Token；
4. `trace` 由系统追加，而不是由节点私自维护；
5. LLM 节点的 prompt projection 与 budget 控制，是执行前语义的一部分。

## 3.6 状态分类：Running / Waiting / Deadlock

定义配置状态函数：

\[
Status(C)=
\begin{cases}
Success,& h_{succ}(\tau)=true\\
Failure,& h_{fail}(\tau)=true\\
Waiting,& h_{wait}(\tau)=true \land Enabled_{\mathcal G}(\tau)=\varnothing\\
Deadlock,& h_{succ}(\tau)=h_{fail}(\tau)=h_{wait}(\tau)=false \land Enabled_{\mathcal G}(\tau)=\varnothing\\
Running,& \text{otherwise}
\end{cases}
\]

其中：

- `Running`：至少仍有内部可推进路径；
- `Waiting`：当前没有内部节点可执行，但系统明确处于合法等待；
- `Deadlock`：既不成功、也不失败、也不属于正常等待，但确实无节点可执行。

这种区分对工程系统非常关键。没有节点可执行并不必然意味着异常；只有当系统无法给出合法等待理由时，才应判为 deadlock。

## 3.7 全局多实例语义

现代 Agent Harness 通常同时维护多个事务实例。因此定义全局运行状态：

\[
\mathcal R=(A,\omega,\eta)
\]

其中 \(A\subseteq Tok\) 是当前所有活跃 Session Token 的集合。

定义多实例选择器：

\[
Pick : \mathcal P(Tok)\setminus\{\varnothing\} \to Tok
\]

当 \(A\neq\varnothing\) 时，Harness Runtime 先用 `Pick` 选择一个实例 \(\tau\in A\)，再计算其 enabled 节点集合，并通过 `Sel` 选择一个节点推进。若单实例执行后得到新 Token \(\tau'\)，则全局状态演化为：

\[
(A,\omega,\eta)
\xrightarrow{\tau,n,\beta,o}
((A\setminus\{\tau\})\cup\{\tau'\},\omega',\eta')
\]

由此可见，真正的执行器行为并不是“让很多 token 在图里乱跑”，而是：

1. 在活跃实例集合中选一个 Session Token；
2. 计算它当前的 enabled nodes；
3. 选一步执行；
4. 用新的 Session Token 覆盖旧实例状态。

## 3.8 动态诱导出的执行图

虽然名字仍然叫 Execution Graph，但其运行时“图”应定义为可达配置图，而不是预先手工连线的静态边图。

给定初始配置 \(C_0\)，定义其诱导图：

\[
\mathbb G_{\mathcal G,C_0}=(V,E)
\]

其中：

\[
V=\{C\mid C_0\to^* C\}
\]

是全部可达配置；

\[
E=\{(C,n,\beta,o,C')\mid C\xrightarrow{n,\beta,o} C'\}
\]

是全部可能发生的小步转移。

因此，“Graph”不是设计者在编辑器中画出的连接线总和，而是运行语义诱导出的可达状态图。

---

# 4. 编译器、静态分析与 Harness Runtime

## 4.1 源定义层与编译层

为了支持工程实现，引入源定义层：

\[
Spec=(Schema,NodeDecls,HaltDecls,PolicyDecls)
\]

其中：

- `Schema`：Session Token 的字段 schema 与类型约束；
- `NodeDecls`：节点声明，包括 guard、projection、actor、merge 与注解；
- `HaltDecls`：成功、失败、等待条件；
- `PolicyDecls`：默认调度、重试、审批与 budget 策略。

编译器定义为：

\[
Compile(Spec)=(\mathcal G,\mathbb D_{\mathcal G},Report)
\]

其中：

- \(\mathcal G\)：编译后的正式 PSOP-EG；
- \(\mathbb D_{\mathcal G}\)：静态依赖图；
- `Report`：静态分析诊断结果。

## 4.2 静态依赖图

由于运行时没有固定边，编译器可以从读写足迹派生一个保守的依赖图，用于分析和可视化。

定义：

\[
n \leadsto m \iff W_n \cap R_m \neq \varnothing
\]

据此得到静态依赖图：

\[
\mathbb D_{\mathcal G}=(N,\leadsto)
\]

它的含义是：节点 \(n\) 的写入可能影响节点 \(m\) 的 guard、projection 或行为。需要强调：

- \(\mathbb D_{\mathcal G}\) 是**编译期依赖图**；
- \(\mathbb G_{\mathcal G,C_0}\) 是**运行时诱导图**；
- 二者都可以叫 graph，但语义不同。

## 4.3 静态分析问题

### 4.3.1 可启动性

给定入口节点 \(n_0\)，若：

\[
\forall x\in Input,\ \exists \beta\in enum_{n_0}(Init(x)),\ g_{n_0}(Init(x),\beta)=true
\]

则称图在合法输入上可启动。

### 4.3.2 选择点（Choice Point）

若存在某个抽象状态 \(\hat\tau\)，使得：

\[
|\widehat{Enabled}(\hat\tau)|\ge 2
\]

则该状态是 choice point。它不是错误，但意味着：

- 必须依赖 `Sel`；
- 应分析不同选择对成本、正确性、可解释性与时延的影响。

### 4.3.3 潜在死锁

若存在某个抽象状态 \(\hat\tau\)，满足：

\[
\widehat{Enabled}(\hat\tau)=\varnothing
\land \hat h_{succ}(\hat\tau)=false
\land \hat h_{fail}(\hat\tau)=false
\land \hat h_{wait}(\hat\tau)=false
\]

则它是潜在 deadlock 状态。

### 4.3.4 读写冲突与并行性

若两个节点 \(n,m\) 满足：

\[
W_n \cap (R_m \cup W_m) \neq \varnothing
\quad\text{或}\quad
W_m \cap (R_n \cup W_n) \neq \varnothing
\]

则它们存在潜在读写冲突。反之，若读写集合相互独立，且策略允许，则可考虑并行执行或批处理执行。

### 4.3.5 Prompt Budget 可满足性

对任意 LLM 节点 \(n\)，编译器可对抽象状态空间做上界估计。若存在可能状态使：

\[
TokCount_n(\tau,\beta)>B_n
\]

则编译器应至少发出以下一种诊断：

1. 缺少 compaction 规则；
2. projection 字段集过大；
3. budget 设置过低；
4. 历史增长不受控，trace summary 机制缺失。

### 4.3.6 审批与权限覆盖

若某类高风险节点要求显式审批或权限断言，则编译器应检查其前置控制条件是否已覆盖。例如：

- 外部执行类 skill；
- 写生产系统的工具；
- 涉及金额、工单关闭或安全相关的操作。

这类检查虽然未必全部写成数学判定式，但应成为 `Report` 的一部分。

## 4.4 Harness Runtime 的职责细化

在运行层，Harness Runtime 的职责可展开为：

1. 维护活跃 Session Token 集合及其版本；
2. 在每个调度周期执行 `Sync`，把外部事件写入 Token；
3. 计算 `Enabled` 集合并做选择；
4. 对 LLM 节点执行 projection、budget 检查、compaction 与推理；
5. 对 tool / skill / code 节点进行安全执行；
6. 对 approval 节点挂起、恢复与确认；
7. 统一追加 trace 与遥测；
8. 在 `Success / Failure / Waiting / Deadlock` 之间切换实例状态；
9. 维持跨实例公平性、资源预算与重试策略；
10. 将运行数据反馈给评估与优化闭环。

## 4.5 最终定位

综上，本文建议将系统分成三层：

### （1）定义层

\[
Spec \leadsto \mathcal G
\]

设计者在这一层定义节点、Token schema、halt 条件、projection、预算与审批策略。

### （2）运行层

\[
\mathcal H(\mathcal G)
\]

Harness Runtime 解释并执行图，同时管理上下文编译、预算、工具、审批、持久化与 tracing。

### （3）实例层

\[
\tau_0 \to \tau_1 \to \tau_2 \to \cdots
\]

每个事务实例在运行时表现为一条 Session Token 的演化链。

因此，PSOP-EG 的一句话定义可以表述为：

> **PSOP Execution Graph 是 Agent Harness 的控制核；其一等运行时对象是 Session Token。每次节点执行，都是在 guard 约束下，把 Session Token 投影成预算受限的 Prompt View 或工具输入，获得观察结果后再 merge 回 Session Token 的一次受控状态重写。**

---

# 5. 设备维保场景下的参考实例

## 5.1 场景说明

考虑一个设备维保场景。系统接收一张维修工单，目标是指导工程师完成一次现场核验与维修推进。图中可能包含以下固定步骤：

1. 读取工单与设备信息；
2. 请求工程师拍照上传机器状态；
3. 用视觉/多模态 LLM 核验照片是否满足要求；
4. 查询设备历史工单与故障码；
5. 运行规则代码做故障初判；
6. 调用 LLM 生成维修计划；
7. 调用外部 Claude skill 生成详细维修动作或检查清单；
8. 必要时请求人工审批；
9. 执行维修步骤并更新结果；
10. 关闭工单或进入失败流程。

这类流程非常适合作为 PSOP-EG 的目标场景，因为：

- 步骤是固定的；
- 节点间并无单一线性路径；
- 多个节点可能同时具备执行条件；
- 外部输入（照片、审批、系统回调）会持续改变运行状态；
- LLM 节点和外部 skill 节点都是流程内的普通节点，而不是特殊外挂。

## 5.2 初始 Session Token

令输入 \(x\in Input\) 为一张维修工单，初始 Token 可定义为：

\[
Init(x)=\tau_0=(iid,gid,goal,m,f,r,mem,h,c,s)
\]

其中：

- \(goal\)：`完成设备状态核验并推进维修`；
- \(m\)：包含工单号、设备 ID、工程师 ID、站点信息、权限；
- \(f\)：初始包含工单文本、设备型号、已知故障码；
- \(r\)：全部工作寄存器置空；
- \(mem\)：为空或包含长期摘要模板；
- \(h=\langle\rangle\)：空 trace；
- \(c\)：默认预算、重试计数、等待原因置空；
- \(s=running\)。

## 5.3 节点集合示例

设节点集合至少包含：

\[
N=\{n_0,n_1,n_2,n_3,n_4,n_5,n_6,n_7,n_8,n_9\}
\]

分别表示：

- \(n_0\)：`bootstrap_workorder`；
- \(n_1\)：`request_machine_photo`；
- \(n_2\)：`verify_photo_llm`；
- \(n_3\)：`query_service_history`；
- \(n_4\)：`diagnose_fault_code`；
- \(n_5\)：`plan_repair_llm`；
- \(n_6\)：`invoke_claude_skill`；
- \(n_7\)：`request_human_approval`；
- \(n_8\)：`close_ticket`；
- \(n_9\)：`fail_ticket`。

### 5.3.1 启动节点

\(n_0\) 是无条件入口节点：

\[
Bind_{n_0}=\{\star\}, \qquad g_{n_0}(\tau,\star)=true
\]

它的作用是初始化寄存器与工作集，例如：

- 把工单中的关键字段标准化写入 \(r\)；
- 在 \(f\) 中标记 `ticket_loaded=true`；
- 在 \(c\) 中初始化预算和重试参数。

### 5.3.2 请求上传照片

\(n_1\) 的 guard 可写为：

\[
g_{n_1}(\tau,\star)=
(\tau.f.photos=\varnothing)
\land
(\tau.c.awaiting\_photo=false)
\]

执行后，系统向工程师发送“请拍照上传设备状态”的请求，并在 \(c\) 中写入：

\[
\tau.c.awaiting\_photo := true
\]

此时若没有其他节点 enabled，而 \(h_{wait}(\tau)=true\)，系统进入 Waiting，而不是 Deadlock。

### 5.3.3 多模态照片核验节点

\(n_2\) 是一个 LLM 节点，其 guard 为：

\[
g_{n_2}(\tau,\star)=
(\tau.f.photos\neq\varnothing)
\land
(\tau.r.photo\_verified=false)
\]

其 Prompt Projection 可以写为：

\[
\Pi_{n_2}(\tau,\star)=
\langle
system:\ \text{照片核验规则与输出 schema},
user:\ \text{工单摘要 + 设备型号 + 照片引用}
\rangle
\]

若模型返回“照片合格、可见序列号与关键部件”，则 `merge` 把结果写入：

\[
\tau.r.photo\_verified := true,
\qquad
\tau.f.photo\_assessment := \text{模型输出摘要}
\]

### 5.3.4 查询历史与规则诊断节点

\(n_3\) 与 \(n_4\) 可以同时具备可执行性：

\[
g_{n_3}(\tau,\star)=
(\tau.m.machine\_id\neq\varnothing)
\land
(\tau.r.history\_loaded=false)
\]

\[
g_{n_4}(\tau,\star)=
(\tau.f.fault\_code\neq\varnothing)
\land
(\tau.r.rule\_diagnosis\_done=false)
\]

此时 `Enabled` 集合可能同时包含 \((n_3,\star)\) 与 \((n_4,\star)\)，形成 choice point。执行器既可以按优先级顺序执行，也可以在读写不冲突时并行执行。

### 5.3.5 维修计划节点

\(n_5\) 是一个 LLM 计划节点，其 guard 可以写成：

\[
g_{n_5}(\tau,\star)=
(\tau.r.photo\_verified=true)
\land
\bigl((\tau.r.history\_loaded=true) \lor (\tau.r.rule\_diagnosis\_done=true)\bigr)
\land
(\tau.r.repair\_plan\_ready=false)
\]

它从 Token 中选取：

- 已验证照片摘要；
- 历史工单信息；
- 故障码诊断结果；
- 安全注意事项；
- 维修约束条件；

构成 Prompt View，然后生成维修计划并写入 \(r.repair\_plan\) 与 \(f.repair\_plan\_summary\)。

### 5.3.6 外部 Skill 节点

\(n_6\) 表示调用外部 Claude skill 生成更细粒度的维修步骤。其 guard 可写为：

\[
g_{n_6}(\tau,\star)=
(\tau.r.repair\_plan\_ready=true)
\land
(\tau.c.approval\_granted=true)
\land
(\tau.r.skill\_invoked=false)
\]

这里的 `approval_granted` 可以由人工审批节点 \(n_7\) 或策略默认值提供。这说明“调用外部 skill”不是图外行为，而是图中的普通节点，只是它在 actor 层依赖不同宿主。

### 5.3.7 结束与失败节点

\(n_8\) 和 \(n_9\) 分别由成功与失败条件驱动：

\[
g_{n_8}(\tau,\star)=
(\tau.r.repair\_completed=true)
\land
(\tau.r.ticket\_closable=true)
\]

\[
g_{n_9}(\tau,\star)=
(\tau.c.retry\_budget=0)
\lor
(\tau.r.unrecoverable\_fault=true)
\]

它们写入最终状态：

\[
\tau.s := success
\quad\text{或}\quad
\tau.s := failure
\]

## 5.4 一条可能的执行历史

给定初始 Token \(\tau_0\)，系统可能经历如下演化：

\[
\tau_0 \xrightarrow{n_0} \tau_1
\xrightarrow{n_1} \tau_2
\xrightarrow{Sync} \tau_2^{\sharp}
\xrightarrow{n_2} \tau_3
\xrightarrow{n_3} \tau_4
\xrightarrow{n_4} \tau_5
\xrightarrow{n_5} \tau_6
\xrightarrow{n_7} \tau_7
\xrightarrow{n_6} \tau_8
\xrightarrow{n_8} \tau_9
\]

其中：

1. \(n_0\) 初始化工单上下文；
2. \(n_1\) 请求照片；
3. `Sync` 把工程师新上传的照片写入 Token；
4. \(n_2\) 核验照片；
5. \(n_3\) 查询设备历史；
6. \(n_4\) 运行规则诊断；
7. \(n_5\) 生成维修计划；
8. \(n_7\) 申请并获得审批；
9. \(n_6\) 调用外部 skill 输出详细步骤；
10. \(n_8\) 关闭工单。

若在某一时刻工程师迟迟未上传照片，则系统可能停在：

\[
Status(C)=Waiting
\]

若系统既无等待理由又无任何 enabled 节点，则应判为：

\[
Status(C)=Deadlock
\]

## 5.5 Trace 示例

一次成功执行的 trace 可以表示为：

\[
\langle
(bootstrap\_workorder,start,\star,success,o_0,\Delta_0,c_0,t_0,t_1,s_0),
(request\_machine\_photo,input,\star,success,o_1,\Delta_1,c_1,t_1,t_2,s_1),
(verify\_photo\_llm,llm,\star,success,o_2,\Delta_2,c_2,t_2,t_3,s_2),
(query\_service\_history,tool,\star,success,o_3,\Delta_3,c_3,t_3,t_4,s_3),
(diagnose\_fault\_code,code,\star,success,o_4,\Delta_4,c_4,t_4,t_5,s_4),
(plan\_repair\_llm,llm,\star,success,o_5,\Delta_5,c_5,t_5,t_6,s_5),
(invoke\_claude\_skill,skill,\star,success,o_6,\Delta_6,c_6,t_6,t_7,s_6),
(close\_ticket,terminal,\star,success,o_7,\Delta_7,c_7,t_7,t_8,s_7)
\rangle
\]

由此可见，trace 不只是“走过了哪些节点”的记录，也是一种完整的运行叙事：每个步骤做了什么、消耗了多少、写回了哪些关键状态、为什么进入下一步，都应能通过 trace 回放出来。

## 5.6 工程映射建议

把上述定义落到工程系统时，可以采用如下映射：

- Session Token：数据库中的实例主记录 + 结构化事实表 + trace 索引；
- Prompt View：节点级 context compiler 的输出；
- LLM 节点：调用模型 API，输入为 Prompt View，输出为结构化 `Obs`；
- Tool 节点：调用内部 API / MCP / 数据库；
- Skill 节点：调用外部 Claude skill 或其他代理能力；
- Sync：接入消息队列、回调、定时器、人工审批结果；
- TraceBus：记录事件、耗时、token 用量、错误码、置信度与人工介入点。

采用这一映射后，PSOP-EG 不再是一个只停留在理论层面的“图”，而是可以直接对接到现代 Agent Harness 的运行内核。

---

# 结语

本文给出的正式版 PSOP-EG，不再把 Place、Arc、Marking 视为理论中心，而是把 **Session Token** 作为一等运行时对象，把 **Node Guard** 作为可执行性判断，把 **Prompt Projection / Token Budget** 作为 LLM 时代不可缺失的上下文机制，把 **Harness Runtime** 作为统一执行宿主，把 **动态诱导图** 作为 execution graph 的真正语义来源。

因此，PSOP-EG 应被理解为：

> 一个由编译器生成、由 Harness Runtime 解释、并以 Session Token 为一等运行时对象推进事务的 guarded rewrite system。

这个定义既保留了你原来想要的“固定节点 + 统一状态 + 编译器 + 执行器”的结构，也把它自然接到了现代 Agent Harness、长会话执行、工具调用、审批、预算与 trace 这套语汇上。
