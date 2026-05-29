# PSOP前端详细设计v1

## 1. 文档说明

### 1.1 文档定位

本文档是 `PSOP WEB IDE` 的唯一有效详细设计基线，用于指导前端开发、前后端联调和运行时可观测界面的落地实现。本文档回答的是“前端系统应该如何实现”，而不是产品愿景或抽象架构原则。

### 1.2 事实源

- [PSOP-Whitepaper-v3.md](./PSOP-Whitepaper-v3.md)
- [PSOP_execution_graph_formal_v5.md](./PSOP_execution_graph_formal_v5.md)
- [PSOP概要设计v1.md](./PSOP概要设计v1.md)

### 1.3 设计范围

- 当前范围只覆盖 `WEB IDE + Control Plane Console`。
- 前端必须覆盖 `运行前 / 运行时 / 运行后` 三阶段闭环。
- 当前阶段不覆盖租户、用户、权限、组织管理、端侧执行器产品形态。

### 1.4 目标读者

- 前端开发工程师
- 服务端与运行时开发工程师
- 产品与交互设计协作者

## 2. 设计目标与非目标

### 2.1 设计目标

- 支撑 `Skills -> Publish -> Auto Compile -> Invocation -> Runtime -> Replay / OTel` 主链路。
- 让用户在一个统一的 `WEB IDE` 中完成 skill 构建、发布诊断、运行发起、实时观测和运行回放。
- 页面路由、对象模型、状态同步方式直接映射服务端核心对象，避免前端出现“页面概念”和“系统概念”脱节。
- 让前端结构足够清晰，开发可以按页面域和状态域并行推进。

### 2.2 非目标

- 不设计执行端 App、桌面端客户端、移动端独立应用。
- 不在 v1 引入 React、Vue 或大型状态管理框架。
- 不在 v1 设计多租户导航、用户空间、权限中心。
- 不在 v1 把前端做成低代码平台；当前重点是把运行闭环做扎实。

## 3. 共享约束

- 用户在 `WEB IDE` 中定义的是 `Skills`，不是 `EG source`。
- `Skill` 发布后系统自动编译得到 `EG Compile Artifact`。
- 前端展示的编译结果必须围绕 formal v5 的对象组织，例如节点、边、guard、actor、merge、trace。
- 运行时真正执行的是 `compile artifact`，不是编辑中的草稿内容。
- 所有运行态页面必须能追溯到以下对象之一：`skill_id`、`skill_version_id`、`compile_request_id`、`compile_artifact_id`、`invocation_id`、`run_id`、`trace_id`。
- 所有实时页面都优先使用 `WebSocket`，失败时回退到轮询。

## 4. 技术栈

| 维度 | 选型 | 说明 |
| --- | --- | --- |
| 运行形态 | `static/admin/` 单页控制台 | 与当前项目脚手架一致，部署简单 |
| 视图层 | `Alpine.js v3` | 轻量、学习成本低，适合控制台场景 |
| 样式层 | `Tailwind CSS v4` | 统一设计 token 与布局能力 |
| EG 可视化 | `bpmn-js Viewer` | 将 formal-v5 EG artifact 在前端转换为只读 BPMN 2.0 XML 后渲染静态结构预览 |
| 脚本组织 | `ES Modules` | 保持浏览器原生模块边界，避免过早复杂化 |
| 构建工具 | 本地 `Node.js 20+` | 仅用于 CSS 与静态资源构建 |
| 通信 | `fetch` + `WebSocket` | REST 承载控制面，WS 承载实时流 |
| 测试 | `Jest` + DOM 测试 | 覆盖路由、store、service、关键组件 |

## 5. 信息架构与导航原则

### 5.1 信息架构原则

- 以 `Skill 生命周期` 组织主导航，而不是以底层技术模块组织页面。
- 页面默认先展示业务对象，再展开技术细节；例如先看 skill 发布状态，再深入到 compile diagnostics。
- 每个对象都必须支持深链访问，便于排障和协作。
- 运行中的页面强调实时性，已完成页面强调可回放和可检索。

### 5.2 布局原则

- 左侧固定主菜单。
- 顶部固定全局搜索、对象跳转和环境状态。
- 主工作区承载列表、详情、运行态和图形化信息。
- 右侧使用抽屉承载上下文详情，例如 `trace event inspector`、`node details`、`gateway policy details`。
- `WEB IDE` 默认采用 dark-first 的 `PSOP Studio` 工作台视觉，参考 VSCode 的固定分区和高信息密度布局。
- 主色采用无青绿色调的 neutral gray 深色背景、细分割线和 orange 主操作色；`Skills / Publish / Runtime / Replay` 等工作域通过语义图标、sky 信息色、orange 行动色和局部高亮区分。
- 页面主体应保持单层全高面板，避免卡片套卡片；列表、详情、图预览和 JSON 文本均在同一工作区内通过细线分隔。

## 6. 菜单与页面树

| 菜单 | 主路由 | 主要对象 | 主要用途 |
| --- | --- | --- | --- |
| `Skills` | `/admin/skills` | `skill_id`, `skill_version_id`, `compile_request_id`, `invocation_id` | 创建、编辑、发布、编译并运行 skill |
| `智能体` | `/admin/agent-prompts` | `agent_prompt_definition_id`, `agent_prompt_version_id`, `usage_key` | 管理 Agent Prompt Pack、版本、发布与启用绑定 |
| `Replay` | `/admin/replay` | `run_id`, `trace_id` | 回放已运行完成的 skill |

`编译` 与 `运行` 不再作为左侧一级菜单暴露。二者属于具体 Skill 的生命周期能力，必须收敛到 `Skill Detail` 的 table 页中；`/admin/compiler`、`/admin/invocations` 等路径仅保留为深链、兼容与排障入口。

### 6.1 详情页与子路由

```text
/admin
/admin/skills
/admin/skills/:skillId
/admin/skills/:skillId/versions/:skillVersionId
/admin/agent-prompts
/admin/agent-prompts/:definitionId
/admin/compiler
/admin/compiler/requests/:compileRequestId
/admin/compiler/artifacts/:compileArtifactId
/admin/invocations
/admin/invocations/:invocationId
/admin/runs/:runId/live
/admin/replay
/admin/replay/runs/:runId
/admin/replay/traces/:traceId
/admin/observability
/admin/gateway
/admin/gateway/mcp
/admin/gateway/inference
/admin/gateway/terminal
```

## 7. 页面详细设计

### 7.1 `Overview`

- 页面目标：在一个页面内看到“最近有哪些 skills 被发布、哪些 runs 正在执行、哪里正在失败”。
- 核心区块：
  - 系统健康卡片
  - 最近发布的 skills
  - 最近编译任务与失败诊断
  - 运行中 invocations / runs
  - 最近完成 runs 的回放入口
  - OTel 异常摘要
- 关键动作：
  - 进入指定 `skill`
  - 进入指定 `compile request`
  - 进入指定 `run live`
  - 进入指定 `replay`
- 依赖数据：
  - `GET /api/system/summary`
  - `GET /api/skills`
  - `GET /api/compiler/requests`
  - `GET /api/runs`
- 状态要求：
  - 空态时显示“尚无 skill / 尚无运行”
  - 加载态使用 skeleton
  - 异常态显示可重试卡片和 `trace_id`

### 7.2 `Skills`

#### 7.2.1 `Skills List` `/admin/skills`

- 页面目标：管理 skill 列表并进入具体 skill。
- 核心区块：
  - skill 列表
  - 状态筛选器：`draft / published / archived`
  - 搜索框：按 `key` / `name`
  - 快速创建入口
- 关键动作：
  - 创建 skill
  - 进入 skill 详情
  - 查看最近发布状态
- 依赖数据：
  - `GET /api/skills`
  - `POST /api/skills`

#### 7.2.2 `Skill Editor` `/admin/skills/:skillId`

- 页面目标：编辑 skill 草稿，并准备发布。
- 核心区块：
  - 基本信息面板
  - skill source 编辑区，默认聚焦用户可维护的 `SKILL.md`、`README.md`、示例、脚本和引用资料
  - 结构化运行配置表单，用于维护输入输出、能力声明、预算、超时、重试等 manifest draft 字段
  - 草稿校验结果
  - 版本侧栏
  - 最近发布记录
  - 编译 table 页：展示当前 skill 的 compile request、状态、冻结 commit、artifact 与诊断入口
  - 运行 table 页：基于当前 skill 发起 invocation，并展示该 skill 的运行记录
- 关键动作：
  - 保存草稿
  - 保存结构化配置
  - 克隆为新草稿版本
  - 触发发布
  - 在发布抽屉内查看服务端真实阶段进度
  - 查看当前 skill 的编译历史与 EG artifact
  - 基于当前 skill 发起运行并进入 live run
- 依赖数据：
  - `GET /api/skills/{skill_id}`
  - `PATCH /api/skills/{skill_id}`
  - `POST /api/skills/{skill_id}/versions`
  - `POST /api/skills/{skill_id}/publish`
  - `GET /api/compiler/requests`
- 状态要求：
  - 编辑区必须提示“当前运行时不会执行未发布草稿”
  - `skill.yaml` 如存在，只作为系统生成的只读 manifest snapshot 预览，不作为普通编辑入口
  - 前端不能要求用户通过手写 YAML 来完成机器契约配置；必须优先提供表单化字段和系统默认值
  - 发布按钮只针对明确版本生效
  - 发布启动后必须展示关联的 `compile_request` 阶段时间线，直到 `published / failed` 终态；发布后的编译任务和 artifact 继续在当前 Skill 的 `编译` table 页呈现

#### 7.2.3 `Skill Version Detail` `/admin/skills/:skillId/versions/:skillVersionId`

- 页面目标：查看一个已冻结版本的结构、发布记录与 artifact 关系。
- 核心区块：
  - 版本摘要
  - 发布记录
  - 编译结果摘要
  - 运行引用列表
- 关键动作：
  - 打开 compile diagnostics
  - 打开 artifact
  - 查看被哪些 invocations / runs 使用

### 7.3 `Compile / Artifact`

该能力在导航上归属于 `Skill Detail -> 编译` table 页。`/admin/compiler` 与 artifact 详情页保留用于深链、排障和从日志/回放跳转。

#### 7.3.1 `Compiler Queue` `/admin/compiler`

- 页面目标：统一查看所有 publish 和 compile 任务。
- 核心区块：
  - compile request 列表
  - 状态筛选：`pending / running / succeeded / failed`
  - severity 摘要
  - artifact 生成情况
- 关键动作：
  - 查看 request 详情
  - 重新触发 compile
  - 按 skill/version 跳转

#### 7.3.2 `Compile Request Detail` `/admin/compiler/requests/:compileRequestId`

- 页面目标：定位某次 publish / compile 的成功或失败原因。
- 核心区块：
  - request 元数据
  - diagnostics 列表
  - source 与 artifact diff
  - formal v5 结构化摘要
- 关键动作：
  - 展开 diagnostic 定位到 source 段落
  - 打开 compile artifact
  - 复制 request id / artifact id
- 依赖数据：
  - `GET /api/compiler/requests/{compile_request_id}`
  - `GET /api/compiler/requests/{compile_request_id}/diagnostics`

#### 7.3.3 `Compile Artifact Detail` `/admin/compiler/artifacts/:compileArtifactId`

- 页面目标：查看最终可执行 `EG Artifact` 的结构与元数据。
- 核心区块：
  - artifact 摘要
  - graph 结构视图：前端将 formal-v5 EG JSON 转换为只读 BPMN 2.0 XML，并使用本地 `bpmn-js` Viewer 渲染
  - JSON 文本视图：展示完整 EG artifact，支持复制
  - 节点详情区：点击图中节点后展示该节点的 `kind / actor / guard / projection / merge`
  - capability binding 摘要
  - 静态分析摘要
- 关键动作：
  - 打开对应 skill version
  - 发起 invocation
  - 对比同一 skill 的上一版 artifact
- 语义约束：
  - BPMN 图只是 artifact 的静态结构预览，不是 formal-v5 的运行时语义来源。
  - 实际可执行性仍由 `Session Token`、节点 `guard`、`Runtime Kernel` 调度和 trace 决定。
  - BPMN XML 只在前端运行时生成，不写回服务端，不替代 `EG Compile Artifact`。

### 7.4 `Invocations / Runs`

该能力在导航上归属于 `Skill Detail -> 运行` table 页。`/admin/invocations` 保留为兼容入口，发起运行的默认入口应使用具体 Skill 详情页，避免用户在脱离 Skill 上下文的页面里选择错误对象。

#### 7.4.1 `Invocation List` `/admin/invocations`

- 页面目标：从 gateway 视角看“谁选择了哪个 skill，真实终端连接是否建立，现在执行到哪个现实步骤”。
- 核心区块：
  - invocation 列表
  - 运行状态聚合卡
  - 快速发起 invocation 表单
- 关键动作：
  - 建立某个 skill 的真实终端协作连接
  - 进入 invocation 详情
  - 打开 live run 页面
- 依赖数据：
  - `GET /api/gateway/invocations`
  - `POST /api/gateway/invocations`

#### 7.4.2 `Invocation Detail` `/admin/invocations/:invocationId`

- 页面目标：查看一次调用是如何映射成 run 的。
- 核心区块：
  - 调用请求摘要
  - 绑定的 skill version / compile artifact
  - run 状态
  - terminal context、设备/环境能力和 run binding 摘要
- 关键动作：
  - 跳转 live run
  - 查看 terminal 输入输出
  - 复制 invocation payload

#### 7.4.3 `Run Live` `/admin/runs/:runId/live`

- 页面目标：围绕当前现实步骤实时协作执行 skill，并在 Runtime 等待时提交文本、图片、视频、音频或设备反馈等现场证据。
- 核心区块：
  - run 状态头，展示 `status`、`runtime_phase`、`terminal_session_id`
  - 当前任务摘要、适用边界、安全提醒和准备事项
  - 当前现实步骤：`current_step`、步骤标题、步骤目标、当前指令
  - 等待上下文：`wait_reason`、`expected_inputs`、`checkpoint_id`、最近 evaluation decision
  - 多模态输入区：一个输入事件内的文本框与 `attachments[]` 队列，支持同时选择图片、视频、音频；自然语言说明统一作为文本输入提交
  - binding summary
  - node execution timeline
  - session token 摘要
  - terminal transcript / I/O 面板
  - trace event stream
  - node / actor inspector
- 关键动作：
  - 通过 `/api/terminal/sessions/{run_id}/events` 注入当前 checkpoint 所需的 terminal input / evidence；纯文本走 JSON，含附件时构造 `multipart/form-data`，其中 `event` JSON 只保存 `text`、`source`、`external_event_id` 等事件级字段，文件使用重复的 `files` 字段提交，`parts[]` 由服务端返回
  - 暂停自动滚动
  - 按 event type 过滤 trace
  - 打开 OTel trace
- 状态要求：
  - 主数据流优先来自 `/ws/runs/{run_id}`
  - WS 断开时自动回退轮询
  - 前端重连后按 REST 拉取缺失的 terminal / trace / snapshot seq，并在 store 层排序去重
  - WS 发送 terminal input 只作为后续低延迟优化；服务端仍必须先落成 `terminal_event` 后再广播
  - 页面不得把 terminal output 都当作最终结果；只有 run 终态和 final verification 成立后才展示为最终完成
  - `waiting_input` 时必须突出当前等待原因与期望输入类型，避免退化成普通聊天框
  - 乐观消息、REST transcript 与 WS 增量事件均按 `event.parts[]` 渲染，同一条气泡内可同时展示文本、图片缩略图、音频播放器和视频播放器；媒体读取使用 `/events/{event_id}/parts/{part_id}/content`
  - 结束后自动提示进入 replay

### 7.4.4 `Skill Test Scenario` `/admin/skills/:skillId/tests/new` 与 `/admin/skills/:skillId/tests/:scenarioId`

- 页面目标：围绕当前 skill 管理黑盒时序测试场景，而不是交互式调试入口。测试场景描述“在什么时间向智能体输入什么事实，以及在某个时间点以前应看到什么文本输出”。
- 导航归属：`Skill Detail -> 测试` tab；场景列表在 tab 内展示，新建和编辑使用深链。
- 列表核心区块：
  - scenario 列表：名称、最近运行状态、最近 run、语义评估摘要、更新时间
  - 新建场景入口、运行场景入口、最近运行 review 入口
- 场景编辑器核心区块：
  - 右侧基础信息：场景名称、描述；目标运行产物默认使用 latest published ready artifact，不在普通表单中暴露版本/artifact 选择
  - 主体时间轴：输入分组包含 GPS、三轴定位、文本、图片、音频、视频信道；输出分组包含单一阶段文本信道；底部时间行以分钟配置总时长，默认 30 分钟
  - 事件创建：用户点击某个信道的时间位置即可新增事件；拖动事件可改变 `at_ms`，右侧属性面板编辑内容
  - 多模态输入：同一个 input event 可在事件属性中配置文本 part 和多个图片、音频、视频资源 part；资源可直接上传并绑定，也可选择已有场景资源
  - 传感器输入：GPS 事件编辑 `{ latitude, longitude, altitude?, accuracy_m?, timestamp? }`，三轴定位事件编辑 `{ x, y, z, roll?, pitch?, yaw?, timestamp? }`
  - 阶段输出：`expected.semantic` 的每个事件代表一个现实任务阶段，只配置阶段时间点与 `expectation`；判断语义为“该阶段时间点以前已经满足”
  - 高级 JSON：保留 `timeline` 与 `judge_policy` 的 JSON 编辑入口，但默认流程不要求用户手写 JSON
- 状态要求：
  - `timeline.schema_version` 固定为 `psop-skill-test-timeline/v1`
  - 输入事件保存 `id`、`lane_id`、`at_ms`、`event_kind`、`mime_type`、`payload_inline` 与可选 `parts[]`；Skill Test 内部 `parts[]` 字段包含 `part_id`、`kind=text|image|video|audio`、`mime_type`、`text?`、`asset_id?`，自然语言说明使用 text part；sensor lane 的 `payload_inline` 必须是结构化对象
  - 输出期望事件保存 `id`、`lane_id="expected.semantic"`、`at_ms`、`expectation`，其中 `id` 即阶段 id
  - 场景资源上传进入对象存储，并以 `skill_test_asset.artifact_object_id` 被 timeline event 的 `parts[].asset_id` 引用
  - 新建场景时如果存在本地暂存资源，前端先创建 scenario，再上传资源并 patch timeline 中的临时 `parts[].asset_id`
  - 顶层 `asset_id` 只用于读取历史单资源事件；前端新建和编辑流程统一写入 `parts[]`

### 7.4.5 `Skill Test Scenario Review` `/admin/skills/:skillId/tests/:scenarioId/runs/:scenarioRunId/review`

- 页面目标：回看一次黑盒时序测试的真实执行，并把预设 timeline、真实 replay、driver events 与语义评估结果叠加展示。
- 核心区块：
  - 预设 timeline：展示各信道输入事件和阶段输出期望，标识 scheduled/sent/passed/failed/inconclusive
  - Review 时间：拖动时间轴游标后，右侧 transcript 只展示该切面以前发生的真实 terminal events
  - 真实 transcript：以 terminal/chat 风格展示真实 input/output，并支持根据 Judge 证据引用高亮
  - 阶段详情：点击阶段输出事件后展示阶段 id、阶段时间、阶段期望、真实输出、Judge 结果与人工判定占位
  - 评估结果：展示每条阶段期望的状态、置信度、理由、证据引用和 raw response 摘要
  - Fork 操作：当前切面可 `Fork Scenario` 或 `Fork Debug`；选中阶段事件时优先使用该阶段 `stage_outputs[].cursor`，Fork Scenario 的时间轴总时长默认保持原测试场景不变，并保留切面时间点及以前的 timeline 事件，也可打开真实 Run Replay
- 状态要求：
  - Review 优先消费 `/api/skill-test-scenario-runs/{scenario_run_id}/review`，并使用其中的 `stage_outputs[]` 驱动阶段详情
  - Review transcript 遍历 terminal event 的 `parts[]`，把一次现场提交展示为一条多模态输入，不把同一批证据拆成多条独立消息
  - 游标以 `time_ms`、`terminal_seq`、`snapshot_seq` 三元组表示，保证 fork 使用精确 Session Token Snapshot 与 terminal prefix
  - Review 不模拟运行结果，只基于已持久化的 replay timeline、terminal events、trace events、snapshots 与 evaluation records

### 7.5 `Replay`

#### 7.5.1 `Replay Index` `/admin/replay`

- 页面目标：按 skill、时间、状态检索历史运行。
- 核心区块：
  - run 检索表单
  - 最近完成 runs 列表
  - 失败 runs 快速入口

#### 7.5.2 `Run Replay` `/admin/replay/runs/:runId`

- 页面目标：按时间线回放一个 run 的完整执行过程。
- 核心区块：
  - 时间轴
  - binding resolved / updated 记录
  - session token snapshot 轨迹
  - trace 事件序列
  - terminal transcript
  - artifact 与 version 摘要
- 关键动作：
  - 跳转指定 trace event
  - 导出 replay URL
  - 打开对应 compile artifact
- 状态要求：
  - Replay transcript 与 Live Run 使用同一套 `event.parts[]` 渲染逻辑，保证历史回放中的多模态输入仍保持同事件整体语义和媒体预览能力

#### 7.5.3 `Trace Detail` `/admin/replay/traces/:traceId`

- 页面目标：对某一条 trace 进行精细排障。
- 核心区块：
  - span tree
  - event payload
  - 对应 node / actor
  - 相关 metrics / logs

### 7.6 `Observability`

- 页面目标：从运行平台视角看系统健康，而不是从单个 run 视角看细节。
- 核心区块：
  - compile 成功率
  - invocation / run 吞吐
  - 平均执行时长
  - gateway 调用延迟
  - inference 延迟与错误率
  - 最近异常 trace
- 关键动作：
  - 由慢调用跳转到 run
  - 由异常 trace 跳转到 replay
  - 按 skill key 过滤

### 7.7 `Gateway Console`

- 页面目标：集中查看 terminal、MCP、LLM inference 三类 gateway 的配置与健康状态。
- 子页：
  - `/admin/gateway/terminal`
  - `/admin/gateway/mcp`
  - `/admin/gateway/inference`
- 核心区块：
  - 连接状态
  - provider / server 列表
  - policy 摘要
  - 最近错误
- 关键动作：
  - 启停某个 MCP server
  - 查看 discover 出来的 tools
  - 配置模型路由与 fallback

### 7.8 `Agent Prompts`

- 页面目标：把平台级智能体提示词从业务代码中移出，作为可审阅、可发布、可启用和可追溯的 Prompt Pack 资产管理。
- 导航归属：左侧一级菜单 `智能体`；该页面管理平台级 Agent Prompt Pack，不放入单个 Skill Detail。
- 列表页 `/admin/agent-prompts`：
  - 展示 Prompt Pack key、agent id、scenario、active version、content hash、usage binding 与更新时间。
  - 默认进入时触发后端 seed，确保 repo-backed 初始包可见。
- 详情页 `/admin/agent-prompts/:definitionId`：
  - 左栏：版本列表，区分 `draft / published / archived`，标记 active version。
  - 中栏：文件编辑区，至少支持 `agent.yaml`、`system.md`、`user_template.md/json`、`output_schema.json`。
  - 右栏：摘要、usage binding、校验结果、发布和启用动作。
- 交互约束：
  - `draft` 可编辑，`published` 不可编辑；修改 published 内容必须先创建新 draft。
  - `publish` 前必须执行服务端校验；`activate` 只允许选择 published version。
  - 页面采用单层全高面板和细线分区，不使用面板套面板。

### 7.9 Issue #1 最小闭环页面要求

issue #1 的前端最小可验收闭环必须优先打通以下页面路径：

```text
Skills Detail -> 编译 table -> 运行 table -> Run Live -> Run Replay
```

- `Skills Detail`：发布动作启动后展示 `publish_record`、`compile_request` 与阶段时间线；编译中通过 SSE 接收 `publish.progress / publish.terminal`，断线后轮询 `/progress`，成功后在 `编译` table 页展示 request/artifact，在 `运行` table 页提供“发起运行”入口；用户主要编辑 `SKILL.md` 与结构化配置表单，`skill.yaml` 仅作为系统生成快照预览。
- `编译 table`：展示当前 skill 的 compile request 列表、diagnostics 摘要和 artifact 入口；artifact 详情继续支持 EG JSON 与 BPMN 静态结构预览。
- `运行 table`：提供一个最小运行入口，默认绑定当前 skill，提交 terminal context 后创建 invocation；用户的现场反馈在 Run Live 中通过 terminal event 注入。
- `Run Live`：展示 run 状态、当前 phase、当前现实步骤、等待原因、期望输入、binding summary、terminal transcript、最新中间指令、trace event stream；WebSocket 未完成前允许以 2 秒轮询 `/api/runs/{run_id}`、terminal events 与 trace events。
- `Run Replay`：运行完成后展示 timeline，至少包含 invocation、binding resolved、Runtime 中间指令、terminal input/evidence、wait checkpoint、evidence evaluation、LLM request/response 摘要、内置 tool call/result、final verification 与 final response。
- 顶部和面包屑必须始终暴露 `skill_id`、`compile_request_id`、`compile_artifact_id`、`invocation_id`、`run_id` 中的关键跳转关系，方便排障。

## 8. 路由组织与布局壳

### 8.1 路由组织

- 路由基线固定为 `/admin/*`。
- 使用 `History API`，由服务端或静态宿主将 `/admin/*` 回退到 `static/admin/index.html`。
- 一切详情页必须以对象 ID 作为 URL 参数，确保支持复制链接和跨人协作。

### 8.2 布局壳

```text
AppShell
  Sidebar
  Topbar
  MainContent
  ContextDrawer
  BottomEventConsole
```

### 8.3 跳转规则

- `Skill Detail -> 编译`：从当前 skill 查看 compile request / artifact。
- `Skill Detail -> 测试`：管理黑盒时序测试场景；场景运行结束后进入 scenario review。
- `Skill Detail -> 运行`：从当前 skill 发起 invocation，并跳到对应 live run。
- `Skill Detail -> 调试`：发起真实 debug invocation，并跳到 Skill Debug Live；该入口不依赖测试场景。
- `Compile Artifact -> Run`：从 artifact 跳到以该 artifact 为基础的 run。
- `Invocations / Runs -> Replay`：运行结束后进入 replay。
- `Skill Test Scenario Review -> Fork Scenario / Fork Debug`：从当前时间切面创建新测试场景或真实调试会话。
- `Replay -> Observability`：从 trace 跳到平台侧观测。

## 9. 前端状态模型、轮询与 WebSocket 策略

### 9.1 状态域划分

| Store | 责任 |
| --- | --- |
| `appStore` | 全局环境、路由、通知、对象跳转 |
| `skillsStore` | skill 列表、详情、版本与草稿编辑状态 |
| `compilerStore` | publish request、compile request、diagnostics、artifact |
| `invocationStore` | invocation 列表、详情、创建表单 |
| `runStore` | live run、当前现实步骤、等待上下文、run binding、terminal transcript、session token 摘要 |
| `skillTestStore` | scenario 列表、时间轴草稿、场景资源、场景运行、review 游标与语义评估结果 |
| `replayStore` | replay timeline、snapshot、trace detail、terminal transcript |
| `observabilityStore` | 聚合指标、异常 trace、趋势图数据 |
| `gatewayStore` | terminal / MCP / inference gateway 配置与健康 |

### 9.2 同步策略

| 场景 | 默认方式 | 退化方式 |
| --- | --- | --- |
| compile request 进行中 | SSE 实时推送 | 轮询 `/progress` / 手动刷新 |
| live run 页面 | `WS` 实时订阅 | 轮询 2 秒 |
| skill test scenario 列表/详情 | REST 按需加载 | 手动刷新 |
| skill test scenario review | 一次性拉取 review DTO | 手动刷新 / 重新评估 |
| invocation 列表 | 轮询 5 秒 | 手动刷新 |
| replay 页面 | 一次性拉取 | 手动刷新 |
| observability 聚合指标 | 轮询 10 秒 | 手动刷新 |

### 9.3 事件归并原则

- `WS event` 先落到对应 store，再由 store 驱动视图更新。
- `trace_event`、`terminal_event` 与 `session_token.snapshot.appended` 需要按 `seq_no` 排序并去重。
- `binding.resolved` 与 `binding.updated` 先更新 run binding summary，再驱动 Run Live / Replay 时间线。
- 页面内不得直接拼接实时事件逻辑，所有事件归并都在 store 层完成。
- WS 不是状态源；断线重连后通过 `/api/runs/*` 与 `/api/terminal/*` 拉取缺失事件并补齐 store。

## 10. 服务层组织与后端对象映射

### 10.1 页面与对象映射

| 页面 | 主对象 | 关键后端接口 |
| --- | --- | --- |
| `Skills List` | `skill_id` | `/api/skills/*` |
| `Skill Editor` | `skill_id`, `skill_version_id` | `/api/skills/*`, `/api/skills/{skill_id}/publish`, `/api/compiler/*` |
| `Compile Request Detail` | `compile_request_id` | `/api/compiler/*` |
| `Compile Artifact Detail` | `compile_artifact_id` | `/api/compiler/*`, `/api/runs/*` |
| `Invocation Detail` | `invocation_id` | `/api/gateway/invocations/*` |
| `Run Live` | `run_id` | `/api/runs/*`, `/api/runs/{run_id}/binding-requirements`, `/api/runs/{run_id}/bindings`, `/api/terminal/*`, `/ws/runs/{run_id}` |
| `Skill Test Scenario` | `skill_id`, `scenario_id` | `/api/skills/{skill_id}/test-scenarios/*` |
| `Skill Test Scenario Review` | `skill_id`, `scenario_id`, `scenario_run_id`, `run_id` | `/api/skill-test-scenario-runs/*`, `/api/replay/*`, `/api/terminal/*` |
| `Run Replay` | `run_id`, `trace_id` | `/api/replay/*` |
| `Observability` | `trace_id`, `run_id` | `/api/system/*`, `/api/runtime/*` |
| `Gateway Console` | `mcp_server_id`, `provider_id` | `/api/gateway/mcp/*`, `/api/gateway/inference/*` |

### 10.2 DTO 使用边界

- 前端只消费服务端定义的 DTO，不自行拼装临时协议。
- `RunDetailDTO`、`RunCapabilityBindingDTO`、`SessionTokenSnapshotDTO`、`TerminalEventDTO`、`TraceEventDTO`、`ReplayTimelineDTO` 直接驱动运行态与回放态页面。
- gateway 相关页面直接映射 `McpServerDTO`、`McpToolDTO`、`InferenceProviderDTO`、`ModelRouteDTO`。

## 11. 其它非功能设计

### 11.1 可用性

- 所有对象详情页都必须提供 ID 复制与返回上一级能力。
- 实时页面必须支持“暂停滚动”“筛选事件”“复制 trace id”。
- 关键错误必须带 `trace_id` 与重试入口。

### 11.2 响应式

- v1 主要面向桌面宽屏。
- 窄屏下保留可访问性，但不追求移动端高密度操作体验。

### 11.3 测试策略

- 路由测试：覆盖 `/admin/*` 主要跳转。
- store 测试：覆盖 live run 的 terminal / trace / snapshot / binding 事件归并、compile diagnostics 排序、replay timeline 构建。
- service 测试：覆盖 DTO 映射、错误模型、WS reconnect。
- 页面测试：覆盖 `Skills`、`Compile Request Detail`、`Run Live` 三个核心页面。

## 12. 开发切片、实现顺序与完成定义

### 12.1 开发切片

1. `Slice A`：`AppShell + Router + http/ws service + appStore`
2. `Slice B`：`Skills` 页面与草稿编辑流
3. `Slice C`：`Publish & Diagnostics` 页面与 artifact 查看
4. `Slice D`：`Invocations / Runs` 与 `Run Live`
5. `Slice E`：`Replay`、`Observability`、`Gateway Console`

### 12.2 实现顺序

1. 先落布局壳、路由和 service 层，使后续页面能平行开发。
2. 再落 `Skills -> Publish -> Compile` 的运行前链路。
3. 随后落 `Invocation -> Run Binding -> Run Live -> Terminal I/O` 的运行时链路。
4. 最后补 `Replay / OTel / Gateway Console` 的运行后与平台视角。

### 12.3 完成定义

- `/admin/*` 路由树完整可访问。
- `Skills`、`Skill Detail / 编译`、`Skill Detail / 运行`、`Replay` 主链路页面可联通。
- 用户能从一个已发布 skill 发起运行，并在运行完成后跳到 Replay 查看 invocation、binding、terminal input/output、LLM、tool、final output 时间线。
- 实时页面能通过 `WS` 更新，断线后能自动退化到轮询并补齐缺失 seq。
- 前端对象和服务端对象一一对齐，不存在前端专属的隐式状态机。
- 读完本文档后，前端团队无需再补关键页面、路由或状态管理决策即可开工。
