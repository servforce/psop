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

## 6. 菜单与页面树

| 菜单 | 主路由 | 主要对象 | 主要用途 |
| --- | --- | --- | --- |
| `Overview` | `/admin` | `skill_id`, `run_id` | 查看系统总览、最近发布、最近运行、异常摘要 |
| `Skills` | `/admin/skills` | `skill_id`, `skill_version_id` | 创建、编辑、查看 skill 与版本 |
| `Publish & Diagnostics` | `/admin/compiler` | `compile_request_id`, `compile_artifact_id` | 查看发布记录、编译任务、诊断与 artifact |
| `Invocations / Runs` | `/admin/invocations` | `invocation_id`, `run_id` | 发起调用、跟踪运行中的 skill |
| `Replay` | `/admin/replay` | `run_id`, `trace_id` | 回放已运行完成的 skill |
| `Observability` | `/admin/observability` | `trace_id`, `run_id` | 查看 OTel trace、metrics、logs、慢调用 |
| `Gateway Console` | `/admin/gateway` | `mcp_server_id`, `provider_id` | 管理 terminal、MCP、LLM gateway 配置 |

### 6.1 详情页与子路由

```text
/admin
/admin/skills
/admin/skills/:skillId
/admin/skills/:skillId/versions/:skillVersionId
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
  - skill source 编辑区
  - 草稿校验结果
  - 版本侧栏
  - 最近发布记录
- 关键动作：
  - 保存草稿
  - 克隆为新草稿版本
  - 触发发布
  - 查看当前 skill 对应的最新 `run`
- 依赖数据：
  - `GET /api/skills/{skill_id}`
  - `PATCH /api/skills/{skill_id}`
  - `POST /api/skills/{skill_id}/versions`
  - `POST /api/compiler/publish`
- 状态要求：
  - 编辑区必须提示“当前运行时不会执行未发布草稿”
  - 发布按钮只针对明确版本生效

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

### 7.3 `Publish & Diagnostics`

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
  - graph 结构视图
  - capability binding 摘要
  - 静态分析摘要
- 关键动作：
  - 打开对应 skill version
  - 发起 invocation
  - 对比同一 skill 的上一版 artifact

### 7.4 `Invocations / Runs`

#### 7.4.1 `Invocation List` `/admin/invocations`

- 页面目标：从 gateway 视角看“谁调用了哪个 skill，现在执行到哪里”。
- 核心区块：
  - invocation 列表
  - 运行状态聚合卡
  - 快速发起 invocation 表单
- 关键动作：
  - 发起调用
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
  - gateway 输入封装
- 关键动作：
  - 跳转 live run
  - 查看 terminal 输入输出
  - 复制 invocation payload

#### 7.4.3 `Run Live` `/admin/runs/:runId/live`

- 页面目标：实时观察运行中的 skill，并在需要时通过 gateway 注入输入。
- 核心区块：
  - run 状态头
  - node execution timeline
  - session token 摘要
  - terminal I/O 面板
  - trace event stream
  - node / actor inspector
- 关键动作：
  - 注入 terminal input
  - 暂停自动滚动
  - 按 event type 过滤 trace
  - 打开 OTel trace
- 状态要求：
  - 主数据流优先来自 `/ws/runs/{run_id}`
  - WS 断开时自动回退轮询
  - 结束后自动提示进入 replay

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
  - session token snapshot 轨迹
  - trace 事件序列
  - terminal transcript
  - artifact 与 version 摘要
- 关键动作：
  - 跳转指定 trace event
  - 导出 replay URL
  - 打开对应 compile artifact

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

- `Skills -> Publish & Diagnostics`：从 skill 或 version 跳到 compile request / artifact。
- `Publish & Diagnostics -> Invocations / Runs`：从 artifact 跳到以该 artifact 为基础的 run。
- `Invocations / Runs -> Replay`：运行结束后进入 replay。
- `Replay -> Observability`：从 trace 跳到平台侧观测。

## 9. 前端状态模型、轮询与 WebSocket 策略

### 9.1 状态域划分

| Store | 责任 |
| --- | --- |
| `appStore` | 全局环境、路由、通知、对象跳转 |
| `skillsStore` | skill 列表、详情、版本与草稿编辑状态 |
| `compilerStore` | publish request、compile request、diagnostics、artifact |
| `invocationStore` | invocation 列表、详情、创建表单 |
| `runStore` | live run、terminal I/O、session token 摘要 |
| `replayStore` | replay timeline、snapshot、trace detail |
| `observabilityStore` | 聚合指标、异常 trace、趋势图数据 |
| `gatewayStore` | terminal / MCP / inference gateway 配置与健康 |

### 9.2 同步策略

| 场景 | 默认方式 | 退化方式 |
| --- | --- | --- |
| compile request 进行中 | 轮询 3 秒 | 手动刷新 |
| live run 页面 | `WS` 实时订阅 | 轮询 2 秒 |
| invocation 列表 | 轮询 5 秒 | 手动刷新 |
| replay 页面 | 一次性拉取 | 手动刷新 |
| observability 聚合指标 | 轮询 10 秒 | 手动刷新 |

### 9.3 事件归并原则

- `WS event` 先落到对应 store，再由 store 驱动视图更新。
- `trace_event` 与 `terminal_event` 需要按 `seq_no` 排序并去重。
- 页面内不得直接拼接实时事件逻辑，所有事件归并都在 store 层完成。

## 10. 服务层组织与后端对象映射

### 10.1 页面与对象映射

| 页面 | 主对象 | 关键后端接口 |
| --- | --- | --- |
| `Skills List` | `skill_id` | `/api/skills/*` |
| `Skill Editor` | `skill_id`, `skill_version_id` | `/api/skills/*`, `/api/compiler/publish` |
| `Compile Request Detail` | `compile_request_id` | `/api/compiler/*` |
| `Compile Artifact Detail` | `compile_artifact_id` | `/api/compiler/*`, `/api/runs/*` |
| `Invocation Detail` | `invocation_id` | `/api/gateway/invocations/*` |
| `Run Live` | `run_id` | `/api/runs/*`, `/api/terminal/*`, `/ws/runs/{run_id}` |
| `Run Replay` | `run_id`, `trace_id` | `/api/replay/*` |
| `Observability` | `trace_id`, `run_id` | `/api/system/*`, `/api/runtime/*` |
| `Gateway Console` | `mcp_server_id`, `provider_id` | `/api/gateway/mcp/*`, `/api/gateway/inference/*` |

### 10.2 DTO 使用边界

- 前端只消费服务端定义的 DTO，不自行拼装临时协议。
- `RunDetailDTO`、`SessionTokenSnapshotDTO`、`TraceEventDTO`、`ReplayTimelineDTO` 直接驱动运行态与回放态页面。
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
- store 测试：覆盖 live run 事件归并、compile diagnostics 排序、replay timeline 构建。
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
3. 随后落 `Invocation -> Run Live -> Terminal I/O` 的运行时链路。
4. 最后补 `Replay / OTel / Gateway Console` 的运行后与平台视角。

### 12.3 完成定义

- `/admin/*` 路由树完整可访问。
- `Skills`、`Publish & Diagnostics`、`Invocations / Runs`、`Replay` 四大主链路页面可联通。
- 实时页面能通过 `WS` 更新，断线后能自动退化到轮询。
- 前端对象和服务端对象一一对齐，不存在前端专属的隐式状态机。
- 读完本文档后，前端团队无需再补关键页面、路由或状态管理决策即可开工。
