# PSOP 前端详细设计 v1

## 1. 文档说明

本文档是当前 `static/` 前端代码实现的详细设计基线。它描述已经落地的静态 Web 控制台、页面片段、全局状态、路由和后端接口映射。

当前前端不是 `static/admin/` 目录下的独立应用，而是以 `static/index.html` 为入口的静态 Alpine 控制台。`/admin/*` 路由由静态宿主回退到 `index.html`。

## 2. 当前范围

当前前端已覆盖：

- Dashboard 汇总视图。
- PSkills 列表、创建、删除、详情。
- PSkill source 和 repository file 编辑。
- Materials 上传、分析、派生资产预览、素材生成 PSkill draft。
- 发布抽屉、编译进度 SSE、编译列表、artifact 详情。
- EG JSON 视图和 bpmn-js 静态图预览。
- Agent Prompt Pack 列表、详情、版本、文件、校验、发布、激活。
- Platform Agents、Agent Runs、Skill Packages、Tools、Memory。
- Runtime job 任务页和统计。
- Invocation 列表、Run Live、Terminal transcript、多模态输入、WebSocket 增量提示。
- Skill Test Scenario、timeline、asset、run、review、evaluate、fork。
- Replay run 列表和 run replay 视图。
- Run Evaluation Reports、Findings。
- Governance Proposals、Experiments。
- Platform Tool Authorizations。
- Platform Observability 工作台。

当前前端未实现为一级页面或正式功能：

- MCP Gateway Console。
- Inference provider/route 配置页。
- 租户、用户、权限、组织管理。

## 3. 技术栈

| 维度 | 当前实现 |
| --- | --- |
| 入口 | `static/index.html` |
| 视图层 | Alpine.js 3.15.3，本地 `node_modules` 加载 |
| 样式 | Tailwind CSS 4.1.18，经 PostCSS 编译到 `static/css/style.compiled.css` |
| 图标 | 本地 Material Symbols 字体，`static/css/material-symbols.css` |
| EG 图预览 | bpmn-js 16.5.0 |
| 图表依赖 | lightweight-charts、Plotly.js 当前安装但非所有页面必用 |
| 测试 | Jest，覆盖 `static/js/utils/**` 和页面片段守卫 |
| 构建 | 无 Vite/webpack；Node 仅用于 CSS 构建、静态 dev server 和 Jest |

## 4. 静态目录结构

```text
static/
  index.html                  App Shell 和所有脚本/样式引用
  css/
    style.css                 Tailwind 输入
    style.compiled.css        Tailwind 编译产物
    material-symbols.css      本地图标字体声明
  fonts/
    material-symbols-outlined.ttf
  js/
    runtime-config.js         默认运行时配置；dev server 会动态覆盖
    app.js                    全局 helper、路由、初始状态
    app/
      core.js                 boot、页面片段加载、基础 UI 行为
      dashboard.js            Dashboard 汇总
      skill-detail.js         PSkills、source、repository、materials
      compiler.js             compile request、artifact、BPMN
      agent-prompts.js        Agent Prompt Pack
      skill-test.js           测试场景、timeline、review
      tasks.js                runtime jobs
      evaluations.js          Run evaluations、findings
      governance.js           Governance proposals、experiments、tool authorization helper
      platform-agents.js      Agent definitions、AgentRun 详情
      platform.js             Skill packages、tools、memory
      observability.js        OTel / runtime / agent / tool 查询工作台
      runtime.js              invocation、run live、terminal、replay
      formatters.js           展示格式化
    utils/
      *.js                    浏览器 helper
      *.node.cjs              Jest / Node 复用入口
      __tests__/*.test.js
  pages/
    *.html                    页面片段，不包含完整 HTML 文档
  scripts/
    build-css.cjs
    dev-server.cjs
  package.json
  tailwind.config.js
  postcss.config.js
```

资源路径以静态根为基准：

- CSS：`/css/style.compiled.css`、`/css/material-symbols.css`
- JS：`/js/app.js`、`/js/app/*.js`
- Page fragments：`/pages/*.html`
- npm runtime：`/node_modules/...`

不存在 `/assets/*` 目录映射。

## 5. App Shell

`static/index.html` 当前布局：

- 左侧 sidebar：桌面端显示，可折叠。
- 顶部 header：显示当前路由标题。
- 主内容区：多个固定 `div` 容器按 route name `x-show` 切换。
- 全局 modal/drawer 容器：创建 Skill、发布 Skill、删除 Skill。

左侧一级菜单：

| 菜单 | 路由 | 对象 |
| --- | --- | --- |
| `Dashboard` | `/admin/dashboard` | 全局指标和闭环概览 |
| `Skills` | `/admin/skills` | PSkill 生命周期 |
| `Prompt Packs` | `/admin/agent-prompts` | Agent Prompt Pack |
| `Agents` | `/admin/platform/agents` | Agent 定义、版本和绑定 |
| `Agent Runs` | `/admin/platform/agent-runs` | AgentRun 运行事实 |
| `Skill Packages` | `/admin/platform/skills` | Skills 包 |
| `任务` | `/admin/tasks` | Runtime jobs |
| `Evaluations` | `/admin/evaluations` | RunEvaluation reports / findings |
| `Governance` | `/admin/governance/proposals` | 治理提案和实验 |
| `工具授权` | `/admin/platform/tool-authorizations` | 高副作用工具授权 |
| `Tools` | `/admin/platform/tools` | ToolPolicy 与工具调用 |
| `Memory` | `/admin/platform/memory` | Agent memory |
| `Observability` | `/admin/platform/observability` | OTel / runtime / agent 查询 |

编译、运行、测试和 Replay 通过 PSkill 详情内动作、列表动作或 deep link 进入，不在当前 sidebar 中作为一级菜单展示。

## 6. 路由表

当前浏览器路由由 `static/js/app.js` 的 `resolveAdminRoute()` 解析。

| Path | route.name | 说明 |
| --- | --- | --- |
| `/`、`/admin`、`/admin/dashboard` | `dashboard` | Dashboard |
| `/admin/skills` | `skills-list` | PSkill 列表 |
| `/admin/tasks` | `tasks-list` | Runtime job 任务页 |
| `/admin/skills/:skillId` | `skill-detail` | PSkill 详情 |
| `/admin/skills/:skillId/runs/:runId/live` | `skill-run-live` | PSkill 下 run live |
| `/admin/skills/:skillId/runs/:runId/live/replay` | `skill-run-live` + `view=replay` | PSkill 下 run replay 视图 |
| `/admin/skills/:skillId/runs/:runId/replay` | `skill-run-live` + `view=replay` | 兼容 replay path |
| `/admin/skills/:skillId/debug/runs/:runId/live` | `skill-debug-live` | 调试 run live |
| `/admin/skills/:skillId/tests/new` | `skill-test-scenario-new` | 新建测试场景 |
| `/admin/skills/:skillId/tests/:scenarioId` | `skill-test-scenario` | 测试场景详情 |
| `/admin/skills/:skillId/tests/:scenarioId/runs/:scenarioRunId/review` | `skill-test-scenario-review` | 测试运行 review |
| `/admin/skills/:skillId/compiler/artifacts/:artifactId` | `skill-compiler-artifact` | Skill 上下文 artifact 详情 |
| `/admin/compiler` | `compiler-list` | 编译请求列表深链 |
| `/admin/compiler/artifacts/:artifactId` | `compiler-artifact` | Artifact 详情 |
| `/admin/agent-prompts` | `agent-prompts-list` | Prompt Pack 列表 |
| `/admin/agent-prompts/:definitionId` | `agent-prompt-detail` | Prompt Pack 详情 |
| `/admin/invocations` | `invocations-list` | Invocation 列表深链 |
| `/admin/runs/:runId/live` | `run-live` | Run live |
| `/admin/runs/:runId/live/replay` | `run-live` + `view=replay` | Run replay 视图 |
| `/admin/replay` | `replay-list` | Replay run 列表 |
| `/admin/replay/runs/:runId` | `run-live` + `view=replay` | Replay detail |
| `/admin/evaluations` | `evaluation-reports` | RunEvaluation reports |
| `/admin/evaluations/:evaluationId` | `evaluation-report` | RunEvaluation report |
| `/admin/evaluations/findings` | `evaluation-findings` | RunEvaluation findings |
| `/admin/governance`、`/admin/governance/proposals` | `governance-proposals` | Governance proposals |
| `/admin/governance/proposals/:proposalId` | `governance-proposal` | Governance proposal |
| `/admin/governance/experiments` | `governance-experiments` | Governance experiments |
| `/admin/platform/tool-authorizations` | `tool-authorizations` | Tool authorizations |
| `/admin/platform/agents` | `platform-agents` | Agents |
| `/admin/platform/agents/:agentKey` | `platform-agent` | Agent detail |
| `/admin/platform/agent-runs` | `platform-agent-runs` | AgentRuns |
| `/admin/platform/agent-runs/:agentRunId` | `platform-agent-run` | AgentRun detail |
| `/admin/platform/skills` | `platform-skills` | Skill packages |
| `/admin/platform/skills/:packageName` | `platform-skill` | Skill package detail |
| `/admin/platform/tools` | `platform-tools` | Tools |
| `/admin/platform/tools/:toolName` | `platform-tool` | Tool detail |
| `/admin/platform/memory` | `platform-memory` | Memory entries |
| `/admin/platform/memory/:memoryId` | `platform-memory-entry` | Memory entry |
| `/admin/platform/observability` | `platform-observability` | Observability 工作台 |

`static/js/utils/router.js` 和 `router.node.cjs` 用于复用与测试；实际页面当前以 `app.js` 中的全局 helper 为准。

## 7. 页面片段

当前页面片段：

| 文件 | 容器 | 说明 |
| --- | --- | --- |
| `dashboard.html` | `dashboard-page` | Dashboard |
| `skills-list.html` | `skills-list-page` | PSkill 列表 |
| `skill-detail.html` | `skill-detail-page` | PSkill 详情、source、repository、materials、publish、compile、runs、tests |
| `create-skill-modal.html` | `create-skill-modal-page` | 创建 PSkill |
| `delete-skill-modal.html` | `delete-skill-modal-page` | 删除 PSkill |
| `publish-skill-drawer.html` | `publish-skill-drawer-page` | 发布与 compile progress |
| `compiler-list.html` | `compiler-list-page` | 编译请求列表 |
| `compiler-artifact-detail.html` | `compiler-artifact-page` | Artifact JSON/BPMN/节点详情 |
| `agent-prompts-list.html` | `agent-prompts-list-page` | Prompt Pack 列表 |
| `agent-prompt-detail.html` | `agent-prompt-detail-page` | Prompt Pack 详情 |
| `tasks.html` | `tasks-page` | Runtime job 任务页 |
| `invocations-list.html` | `invocations-list-page` | Invocation 列表 |
| `run-live.html` | `run-live-page` | Run live/replay、terminal transcript |
| `skill-test-scenario-detail.html` | `skill-test-scenario-page` | 测试场景编辑 |
| `skill-test-scenario-review.html` | `skill-test-scenario-review-page` | 测试运行 review |
| `replay-list.html` | `replay-list-page` | Replay run 列表 |
| `evaluation-reports.html` | `evaluation-reports-page` | RunEvaluation reports |
| `evaluation-findings.html` | `evaluation-findings-page` | RunEvaluation findings |
| `governance-proposals.html` | `governance-proposals-page` | Governance proposals |
| `governance-experiments.html` | `governance-experiments-page` | Governance experiments |
| `tool-authorizations.html` | `tool-authorizations-page` | Platform tool authorizations |
| `platform-agents.html` | `platform-agents-page` | Agents |
| `platform-agent-runs.html` | `platform-agent-runs-page` | AgentRuns |
| `platform-skills.html` | `platform-skills-page` | Skill packages |
| `platform-tools.html` | `platform-tools-page` | Tools |
| `platform-memory.html` | `platform-memory-page` | Memory entries |
| `platform-observability.html` | `platform-observability-page` | Observability 工作台 |

页面片段必须保持片段形态，不包含 `<!doctype>`、`<html>`、`<head>`、`<body>` 或脚本标签。

## 8. 状态模型

当前状态集中在 Alpine 组件 `skillsConsole()` 的单一对象中，由 `createInitialState()` 初始化，再通过 `window.PSOPConsole*Methods` mixin 组合。

主要状态域：

| 状态域 | 字段示例 | 所属文件 |
| --- | --- | --- |
| App / Route | `apiBaseUrl`、`route`、`loadingPage`、`notice`、`centerToast` | `app.js`、`core.js` |
| Dashboard | `dashboardMetrics`、`dashboardFilters` | `dashboard.js` |
| Skills | `skills`、`currentSkill`、`sourceLoadedSkillId`、`repositoryEntries` | `skill-detail.js` |
| Materials | `rawMaterials`、`rawMaterialDetail`、`rawMaterialAnalysis`、upload/generate modal state | `skill-detail.js` |
| Publish / Compiler | `publishProgress`、`compilerRequests`、`compilerArtifact`、BPMN viewer state | `compiler.js` |
| Agent Prompts | `agentPrompts`、`agentPromptDetail`、`agentPromptBindings` | `agent-prompts.js` |
| Tasks | `tasks`、`taskStats`、`taskFilters`、`taskPollTimer` | `tasks.js` |
| Runtime | `invocations`、`liveRun`、`terminalEvents`、`runWs`、`liveRunPollTimer` | `runtime.js` |
| Skill Tests | `skillTestCases`、`skillTestCase`、`skillTestRuns`、`skillTestReview`、timeline state | `skill-test.js` |
| Evaluations | `currentEvaluation`、`evaluationFindings`、`evaluationActivityWs` | `evaluations.js` |
| Governance | `governanceProposals`、`governanceExperimentRows`、`governanceProposalActivityWs` | `governance.js` |
| Platform Agents | `platformAgents`、`platformAgentRuns`、`platformAgentToolAuthorizations` | `platform-agents.js` |
| Platform Skills / Tools / Memory | `skillPackages`、`platformTools`、`memoryEntries` | `platform.js` |
| Observability | `observabilityMetrics`、`observabilityRunEvents`、`observabilityRunTraces` | `observability.js` |

当前不是多 store 架构；文档和后续改动不应假设存在独立 `appStore/runStore/gatewayStore`。

## 9. 后端接口映射

| 页面/能力 | 当前调用接口 |
| --- | --- |
| PSkills List | `GET /api/v1/pskills`、`POST /api/v1/pskills` |
| PSkill Detail | `GET/PATCH/DELETE /api/v1/pskills/{skill_id}` |
| Source Editor | `GET/PUT /api/v1/pskills/{skill_id}/source` |
| Repository Browser | `/api/v1/pskills/{skill_id}/repository/tree`、`/repository/files`、`/repository/folders` |
| Materials | `/api/v1/pskills/{skill_id}/materials*` |
| Publish Drawer | `POST /api/v1/pskills/{skill_id}/publish`、`GET /api/v1/compiler/requests/{id}/events`、`/progress` |
| Compiler List | `GET /api/v1/compiler/requests` |
| Artifact Detail | `GET/PUT /api/v1/compiler/artifacts/{id}` |
| Agent Prompts | `/api/v1/agent-prompts*`、`/api/v1/agent-prompt-bindings*` |
| Tasks | `GET /api/v1/runtime/jobs`、`GET /api/v1/runtime/jobs/stats` |
| Invocations | `GET/POST /api/v1/gateway/invocations` |
| Run Live | `GET /api/v1/runs/{run_id}`、`POST /api/v1/runs/{run_id}/cancel`、`/snapshots`、`/traces`、`/bindings`、`/events` |
| Terminal WS | `/ws/runs/{run_id}` |
| Replay | `GET /api/v1/replay/runs`、`GET /api/v1/replay/runs/{run_id}` |
| Skill Tests | `/api/v1/pskills/{skill_id}/test-scenarios*`、`/api/v1/skill-test-scenario-runs*` |
| Inference Models | `GET /api/v1/gateway/inference/models` |
| Evaluations | `/api/v1/evaluations*` |
| Governance | `/api/v1/governance/proposals*`、`/api/v1/governance/experiments*` |
| Tool Authorizations | `/api/v1/tool-authorizations*`、`/api/v1/runs/{run_id}/tool-authorizations`、`/api/v1/agent-runs/{agent_run_id}/tool-authorizations` |
| Platform Agents | `/api/v1/agents*`、`/api/v1/agent-runs*` |
| Platform Skill Packages | `/api/v1/skills*` |
| Platform Tools | `/api/v1/tools*` |
| Platform Memory | `/api/v1/memory*` |
| Platform Observability | `/api/v1/observability/*`；metrics 面板展示 Runtime、Agent、Evaluation、Governance 与 OTel 状态 |

当前前端不应调用以下未实现接口：

- `/api/v1/system/summary`
- `/api/v1/system/config`
- `/api/v1/gateway/mcp/*`
- `/api/v1/gateway/inference/providers`
- `/api/v1/gateway/inference/routes`
- `/api/v1/runtime/workers`
- `/api/v1/runtime/sandboxes`
- `/api/v1/replay/traces/{trace_id}`
## 10. 同步策略

| 场景 | 当前方式 |
| --- | --- |
| Publish progress | SSE：`/api/v1/compiler/requests/{id}/events`，断线可读 `/progress` |
| Run Live run event | WebSocket `/ws/runs/{run_id}` 接收 `terminal.event.appended`，增量更新 transcript、Run Events 和 Replay timeline，REST 补齐 |
| Run Live run trace | WebSocket `/ws/runs/{run_id}` 接收 `trace.event.appended`，增量更新 Replay timeline、EG Node Path 和 trace 列表，REST 补齐 |
| Run Live snapshot | WebSocket `/ws/runs/{run_id}` 接收 `session_token.snapshot.appended`，增量更新 Replay snapshot 比较视图，REST 补齐 |
| Run Live 状态 | WebSocket `/ws/runs/{run_id}` 接收 `run.updated` 增量更新状态栏和 Replay run metadata；REST 刷新 run、run events、run traces、bindings 补齐 |
| Run Live binding | WebSocket `/ws/runs/{run_id}` 接收 `binding.updated`，增量更新 Binding 列表和 Replay binding evidence，REST 补齐 |
| Tasks | 轮询 runtime jobs 和 stats |
| Skill Test Review | REST 拉取 review DTO，必要时轮询运行状态 |
| Replay | REST 一次性拉取 replay detail；deep link 支持 `event_id`、`trace_id`、`seq_no`、`snapshot_seq` 定位证据 |
| Evaluation / Governance activity | WebSocket 活动快照 + REST 补齐 |
| Tool Authorizations | WebSocket `/ws/tool-authorizations` 接收授权变更，REST 补齐 |
| Observability | REST 查询 dashboard、metrics、run events、run traces、agent/tool/model facts |

WebSocket 不是状态源。断线、刷新或 seq 不连续时必须通过 REST 从 `latest_seq + 1` 补齐 run events。

## 11. Terminal UI

Run Live 当前负责：

- 展示 run 状态、phase、等待原因、期望输入、binding summary。
- 展示 terminal transcript。
- 支持 JSON text input。
- 支持 multipart text + image/audio/video 输入。
- 媒体 part 通过 `/runs/{run_id}/events/{event_id}/parts/{part_id}/content` 读取。
- 将 runtime output、input、错误提示和 replay 视图区分展示。

输入规则与 [PSOP终端接入说明v1.md](./PSOP终端接入说明v1.md) 保持一致：前端不构造 `parts[]`，只提交 `text` 和文件字段。

## 12. EG 图预览

Artifact 详情使用 `static/js/utils/eg-bpmn.js` 将 formal-v5 artifact 转为只读 BPMN XML，再由本地 bpmn-js viewer 渲染。

约束：

- BPMN 图仅用于结构预览。
- BPMN 不写回服务端。
- 实际可执行语义以 artifact JSON、Session Token、guard、merge 和 Runtime trace 为准。

## 13. 构建与本地运行

从根目录：

```bash
scripts/dev/build-web.sh
scripts/dev/test-web.sh
scripts/dev/run-web.sh
```

从 `static/`：

```bash
npm ci
npm run build:css
npm test
npm run dev
```

`static/scripts/dev-server.cjs` 行为：

- 默认监听 `0.0.0.0:4173`。
- 普通文件按静态根读取。
- 无扩展名路径 fallback 到 `index.html`，支持 `/admin/*`。
- 动态生成 `/js/runtime-config.js`，推导 `window.__PSOP_API_BASE_URL`。

## 14. 测试覆盖

当前 Jest 重点覆盖：

- router path 解析。
- skill key 生成。
- runtime event helper。
- EG -> BPMN 转换。
- 页面片段守卫。
- terminal media 渲染 helper。
- button tooltip 和 danger action 行为。
- skill test timeline normalize/layout。
- materials 与 tasks helper。

## 15. 设计约束

- 保持静态轻栈，不引入大型框架或打包器。
- 依赖必须通过 `static/package.json` 和 lockfile 管理。
- 所有运行时依赖本地加载，不使用 CDN。
- 页面主体维持单层全高面板和细线分区。
- 页面与后端对象保持同构：`skill_id`、`compile_request_id`、`compile_artifact_id`、`invocation_id`、`run_id`、`scenario_run_id` 必须可追溯。
- 不为未实现后端接口预留可点击入口。
