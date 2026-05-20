# Skills Management MVP 设计说明

- 日期：2026-04-24
- 状态：Skills 管理子域设计基线，后续编译与运行链路以根目录详细设计为准
- 范围：`Skills 创建 -> GitLab 初始化 -> Source 编辑 -> Publish 冻结 -> PostgreSQL 入库`
- 对齐事实源：
  - `docs/PSOP概要设计v1.md`
  - `docs/PSOP前端详细设计v1.md`
  - `docs/PSOP服务端详细设计v1.md`
  - `docs/PSOP_execution_graph_formal_v5.md`

## 1. 背景与目标

当前仓库已经完成 PSOP 的概要设计、前端详细设计、服务端详细设计与 formal v5 形式定义。为了支持 `Publish -> Compile -> Invocation -> Runtime -> Replay` 主链路，首先需要明确 `Skills Management MVP` 作为 Skills 管理子域的边界，把 `Skill` 作为正式对象在 `WEB IDE`、`GitLab`、`PostgreSQL` 三者之间真正打通。

本文档只定义 Skills 管理子域的闭环；EG 编译、Runtime 推进、Agent Harness、Replay 与 OTel 的工程规则以 `docs/PSOP服务端详细设计v1.md` 和 `docs/PSOP前端详细设计v1.md` 为准。Skills 子域必须提供以下能力，作为后续链路的输入：

1. 用户可在 `WEB IDE` 创建一个正式 `skill`
2. 系统自动在 GitLab `skills` group 下创建显示名为 `skill.name`、路径为 `skill.key` 的 skill project
3. project 根目录自动初始化 `README.md`、`SKILL.md`，并可选生成只读 `skill.yaml` 编译视图
4. 用户可在 `WEB IDE` 查看并编辑 skill source，并通过表单维护结构化 manifest draft
5. 编辑内容真实提交到 GitLab
6. 平台将 skill 元数据、draft version、publish 记录正式写入 PostgreSQL
7. 发布时冻结到明确 `commit SHA`，形成可供后续编译链路消费的 published version

## 2. 范围与非目标

### 2.1 本次范围

- `Skills List` 与 `Skill Detail / Editor` 两个核心页面
- 服务端统一 GitLab Token 的真实仓库读写
- PostgreSQL 中 `skill_definition`、`skill_version`、`skill_publish_record` 三张核心表
- draft 编辑、source 保存、publish 冻结 `commit SHA`
- 发布后形成可追溯的 published version

### 2.2 本文不展开的后续链路

- `EG Compile Artifact` 生成规则、产物结构与诊断策略
- `Invocation / Run / Replay` 的 API、状态机与页面联动
- `RuntimeKernel` 如何推进 formal v5 EG
- `AgentModule / Agent Harness` 与 LLM/tool 的执行职责
- 多租户、用户身份、权限体系
- 多人协同编辑、复杂 diff/merge
- 多 skill 共用一个 GitLab project
- GitLab OAuth 或按用户身份写仓库

上述后续链路不是当前文件的事实源；实现时必须回到根目录详细设计文档获取最新约束。

## 3. 核心设计结论

### 3.1 事实源原则

- `GitLab` 是 `skill source` 的正式事实源
- `PostgreSQL` 保存平台元数据、仓库绑定、冻结 revision、manifest snapshot 编译视图与发布记录
- `WEB IDE` 是 `Skill` 的管理与编辑入口，但不拥有正式 source 主副本

### 3.2 仓库组织原则

- 一个 `skill` 对应一个 GitLab project
- 所有 skill project 固定创建在 GitLab `skills` group 下
- GitLab project 显示名使用 `skill.name`
- GitLab project 路径使用 `skill.key`，保证稳定唯一且适合 URL/脚本引用
- 默认分支固定由平台创建并跟踪，MVP 默认值为 `main`

### 3.3 技术边界原则

- GitLab 认证采用服务端统一 Token
- `draft` 始终表示当前可继续编辑的工作态
- `published version` 必须冻结到明确的 `source_commit_sha`
- 发布冻结 revision，并把当前 draft `manifest_snapshot` 复制为 published snapshot；其中 `prompt_material` 包含发布时的核心 source 正文

## 4. Skill 形式定义与物理承载

服务端详细设计定义了正式 `Skill` 至少包含：

- `identity`
- `repository binding`
- `source revision`
- `interface contract`
- `capability declarations`
- `compile config`
- `runtime policy`
- `publish metadata`

本次 MVP 将上述正式 `Skill` 拆分为“GitLab 用户源码层 + PostgreSQL 结构化契约层”共同承载：

| 正式字段 | 存放位置 | 说明 |
| --- | --- | --- |
| `identity` | `skill_definition` + draft/published `manifest_snapshot` | 数据库是结构化身份事实源，GitLab 文件可展示镜像信息 |
| `repository binding` | `skill_definition` | 包括 `gitlab_project_id`、`repository_url`、`default_branch`、`manifest_path` |
| `source revision` | `skill_version` | `draft` 跟踪 `source_ref`，`published` 必须记录 `source_commit_sha` |
| `interface contract` | draft/published `skill_version.manifest_snapshot` | 由 Web IDE 表单维护，发布时冻结 |
| `capability declarations` | draft/published `skill_version.manifest_snapshot` | 由 Web IDE 表单和系统默认规则维护 |
| `compile config` | draft/published `skill_version.manifest_snapshot` | 作为 `SkillCompiler` 的正式输入，具体规则见服务端详细设计 |
| `runtime policy` | draft/published `skill_version.manifest_snapshot` | 作为 Runtime artifact policy 的来源，具体规则见服务端详细设计 |
| `publish metadata` | `skill_publish_record` | 发布说明、状态、冻结 commit、发布时间 |

结论：MVP 中“正式 Skill 视图”由 `GitLab user source + DB manifest` 共同构成。用户主要维护 `SKILL.md` 等源文件，编译视图由 PostgreSQL 中 draft/published `skill_version.manifest_snapshot` 承载。

### 4.1 用户源文件与系统 manifest 边界

- `SKILL.md` 是用户和 Agent 面向的执行说明正文，描述目标、步骤、约束、示例与注意事项。
- `README.md` 是用户可读说明与仓库展示入口。
- draft `skill_version.manifest_snapshot` 是系统结构化配置草稿和编译视图，来源于 Web IDE 表单、Skill 基础信息、系统默认规则以及当前 `SKILL.md` / `README.md` 正文。
- published `skill_version.manifest_snapshot` 是发布时冻结的机器契约，必须与 `source_commit_sha` 一起进入 `skill_version`。
- `manifest_snapshot.prompt_material` 保存当前草稿的 `SKILL.md` 与 `README.md` 正文；修改 `SKILL.md` 会重建 snapshot 中的 prompt material，从而让 snapshot 与当前草稿内容保持同构。
- draft snapshot 是临时草稿投影，不维护单独审计或变更记录；只有发布时复制出的 published snapshot 才会真正影响编译。
- `skill.yaml` 如保留在 GitLab 中，只作为 PSOP 生成的只读序列化编译视图，用于预览、离线复现和代码审查；普通用户不需要手工维护。
- 编译准确性由当前 source 到 snapshot 的确定性投影、结构化字段校验、默认值填充、compile diagnostics、artifact 冻结和 Replay 关联保障。

## 5. GitLab 中的 Skill Source 结构

每个 GitLab skill project 的根目录至少包含：

- `README.md`
- `SKILL.md`
- 可选的系统生成 `skill.yaml`

按需扩展的目录包括：

- `references/`
- `scripts/`
- `tools/`

后续如需加入 `examples/`、`tests/`、`prompts/`，不破坏本次结构基线。

### 5.1 初始化生成规则

创建 skill 时，平台自动在新建 GitLab project 根目录生成：

1. `README.md`
   - 说明 skill 名称、用途、仓库由 PSOP 管理
2. `SKILL.md`
   - 提供 agent/协作者可读的执行说明占位内容
3. `skill.yaml`
   - 可选写入满足本次 MVP 形式定义的只读 manifest snapshot；该文件由系统生成，不作为用户编辑入口

### 5.2 `manifest` 最小契约

draft/published `skill_version.manifest_snapshot` 是结构化正式入口。序列化为 `skill.yaml` 时，MVP 最小格式如下：

```yaml
skill:
  identity:
    key: equipment-diagnosis
    name: Equipment Diagnosis
    description: Diagnose equipment issues from operator input.
  interface_contract:
    invocation_mode: terminal
    entry: default
    inputs:
      - name: user_input
        type: text
        required: true
        description: User request entered from WEB IDE.
    outputs:
      - name: final_response
        type: text
        description: Final response returned to the caller.
  capabilities:
    terminal:
      enabled: true
    llm:
      route_key: default
      required: true
    mcp_tools: []
    sandbox:
      required: false
  compile_config:
    formal_revision: psop-eg-formal/v5
    target: eg.compile.artifact
    validation_rules: []
  runtime_policy:
    timeout_seconds: 300
    retry:
      max_attempts: 0
    budget:
      max_llm_calls: 8
      max_tool_calls: 8
    concurrency:
      mode: single
    isolation:
      level: default
  prompt_material:
    readme: |
      # Equipment Diagnosis
      ...
    skill_md: |
      # Equipment Diagnosis
      ...
```

### 5.3 结构约束

- manifest 顶层固定为 `skill`
- `identity.key` 必须与平台中的 `skill_definition.key` 一致
- `identity.name` 必须与 GitLab project 显示名一致
- `compile_config.formal_revision` 初始固定为 `psop-eg-formal/v5`
- `manifest_path` 在本次 MVP 可继续记录为仓库根目录 `skill.yaml`，但语义是系统生成副本路径，不是用户维护入口

## 6. PostgreSQL 数据模型

### 6.1 `skill_definition`

职责：承载一个 `skill` 的稳定身份与 GitLab 绑定关系。

建议字段：

- `id UUID`
- `key`
- `name`
- `description`
- `status`，枚举：`active | archived`
- `gitlab_group_path`，固定为 `skills`
- `gitlab_project_id`
- `repository_url`
- `default_branch`
- `manifest_path`
- `latest_draft_version_id`
- `latest_published_version_id`
- `created_at`
- `updated_at`

约束：

- `key` 全局唯一
- `gitlab_project_id` 唯一
- `manifest_path` 初始可固定为 `skill.yaml`，表示系统生成副本路径
- 当前草稿机器契约事实源保存在 draft `skill_version.manifest_snapshot`，由服务端默认值和前端表单维护

### 6.2 `skill_version`

职责：表示 skill 的一个草稿态或冻结态版本。

建议字段：

- `id UUID`
- `skill_definition_id`
- `version_no`
- `status`，枚举：`draft | published | archived`
- `source_ref`
- `source_commit_sha`
- `manifest_snapshot JSONB`
- `runtime_policy_snapshot JSONB`
- `created_at`
- `updated_at`

规则：

- `draft` 允许只有 `source_ref`
- `published` 必须带 `source_commit_sha`
- `manifest_snapshot` 来源于 draft `skill_version.manifest_snapshot`，发布时复制为 frozen snapshot，是后续编译入口的机器契约事实源；其中 `prompt_material` 会随 `SKILL.md` / `README.md` 等核心源文件变更而重建
- `runtime_policy_snapshot` 来源于 `manifest_snapshot.skill.runtime_policy`，用于后续运行态展示与编译输入缓存

### 6.3 `skill_publish_record`

职责：记录一次正式发布行为。

建议字段：

- `id UUID`
- `skill_definition_id`
- `skill_version_id`
- `publish_reason`
- `publish_status`，枚举：`requested | compiling | published | failed`
- `published_commit_sha`
- `release_ref`
- `published_at`
- `created_at`

规则：

- 每次 publish 必须对应一个明确的 `published_commit_sha`
- `release_ref` 在 MVP 中默认与默认分支一致，仅作为审计字段保留

## 7. 核心业务流设计

### 7.1 Create Skill

目标：在 `WEB IDE` 中创建正式 skill，并同步建立 GitLab 仓库和数据库元数据。

流程：

1. 前端提交 `key`、`name`、`description`
2. 服务端校验：
   - `skill.key` 未被占用
   - GitLab `skills` group 下不存在同路径 project
3. 服务端调用 GitLab API：
   - 在 `skills` group 下创建 project
   - 使用 `name = skill.name`
   - 使用 `path = skill.key`
   - 创建默认分支 `main`
  - 初始化 `README.md`、`SKILL.md`
  - 可选初始化系统生成的只读 `skill.yaml`
4. GitLab 初始化成功后，服务端写 PostgreSQL：
   - `skill_definition`
   - 首个 `draft skill_version`
   - 初始 draft `manifest_snapshot`
5. 返回完整 skill 详情

一致性原则：

- GitLab 创建或初始化失败时，不写数据库
- 先 GitLab、后数据库，避免出现“DB 有 skill，GitLab 无仓库”的伪状态

### 7.2 Edit Skill Metadata

可编辑元数据包括：

- `name`
- `description`

处理规则：

- `description` 更新数据库即可
- `name` 更新时，同时同步 GitLab project 显示名与 draft `manifest_snapshot.skill.identity.name`
- `key` 在 MVP 中创建后不可修改，避免 project path、绑定关系与后续发布历史失稳

### 7.3 Edit Skill Source

可编辑文件范围：

- `README.md`
- `SKILL.md`
- 示例、脚本、引用资料等用户源文件

系统生成文件：

- `skill.yaml` 如存在，默认只读展示，不进入普通保存流程

结构化配置范围：

- `interface_contract`
- `capability declarations`
- `compile_config`
- `runtime_policy`

流程：

1. 前端获取当前 draft source、`head_commit_sha` 与 draft `manifest_snapshot`。
2. 用户在 `WEB IDE` 中编辑 `SKILL.md` 等用户源文件，或通过表单调整结构化运行配置。
3. 保存用户源文件时前端提交文件内容与 `base_commit_sha`。
4. 保存结构化配置时前端提交 manifest patch；服务端校验并写入 draft `skill_version.manifest_snapshot`。
5. 服务端提交用户源文件前校验 GitLab 分支头是否仍等于 `base_commit_sha`。
6. 一致时，服务端通过统一 Token 将变更提交到 GitLab 默认分支。
7. GitLab 返回新的 `head_commit_sha`。
8. 服务端更新当前 draft version：
   - `source_ref = default_branch`
   - `manifest_snapshot = current draft manifest`
   - `runtime_policy_snapshot = current draft manifest runtime policy`
   - `updated_at`

并发规则：

- 当前分支头变化则拒绝保存
- 返回“source 已更新，请刷新后重试”
- 本次不做自动 merge 或三方 diff

### 7.4 Publish Skill

目标：将当前 draft 对应的 GitLab 分支头冻结成一个正式发布版本。

流程：

1. 用户输入 `publish_reason`
2. 服务端读取当前 draft 跟踪的默认分支头 commit
3. 校验当前 draft `manifest_snapshot`，并生成发布冻结 `manifest_snapshot`
4. 创建新的 `published skill_version`
   - `status = published`
   - `source_ref = default_branch`
   - `source_commit_sha = head commit`
   - `manifest_snapshot = frozen draft manifest_snapshot`
   - `runtime_policy_snapshot = latest parsed runtime policy`
5. 写入 `skill_publish_record`，初始 `publish_status = compiling`
6. 创建 `skill_compile_request` 并执行编译
7. 编译成功时更新 `publish_status = published`，并推进 `skill_definition.latest_published_version_id`
8. 编译失败时更新 `publish_status = failed`，保留上一版 latest published，不允许失败编译成为运行入口
9. 保留原有 `draft skill_version` 继续跟踪默认分支，供后续继续编辑
10. 如启用 GitLab manifest 副本，则将系统生成的 `skill.yaml` 写入或更新为发布时 snapshot，用作只读编译视图

发布规则：

- 发布冻结当前 head commit，不重复提交 source
- published version 一经生成不可编辑
- 后续修改继续发生在 draft 上，再次 publish 生成新的 published version
- latest published 只指向编译成功且存在 ready artifact 的版本

## 8. API 设计

### 8.1 `GET /api/skills`

用途：技能列表与检索。

返回字段至少包含：

- `skill_id`
- `key`
- `name`
- `status`
- `gitlab_project_id`
- `repository_url`
- `latest_draft_head_sha`
- `latest_published_commit_sha`
- `updated_at`

### 8.2 `POST /api/skills`

用途：创建 skill、创建 GitLab project、初始化 source、创建 draft version。

请求：

```json
{
  "key": "equipment-diagnosis",
  "name": "Equipment Diagnosis",
  "description": "Diagnose equipment issues from operator input."
}
```

响应包含：

- `skill detail`
- `draft version summary`
- `gitlab binding summary`

### 8.3 `GET /api/skills/{skill_id}`

用途：获取 skill 详情。

响应至少包含：

- `skill_definition`
- `current_draft_version`
- `latest_published_version`
- `recent_publish_records`

### 8.4 `PATCH /api/skills/{skill_id}`

用途：更新 skill 元数据。

请求允许：

- `name`
- `description`

### 8.5 `GET /api/skills/{skill_id}/source`

用途：读取当前 draft 对应的 GitLab 文件内容。

响应至少包含：

- `readme_content`
- `skill_md_content`
- `manifest_preview` 或只读 `skill_yaml_content` 预览
- `source_ref`
- `head_commit_sha`

### 8.6 `PUT /api/skills/{skill_id}/source`

用途：保存当前 draft source。

请求：

```json
{
  "base_commit_sha": "abc123",
  "readme_content": "...",
  "skill_md_content": "..."
}
```

行为：

- 基于 `base_commit_sha` 做分支头并发检查
- GitLab 提交成功后刷新 draft version 的 `source_ref` 与 `head_commit_sha`
- 结构化 manifest draft 通过独立表单保存到 draft `skill_version.manifest_snapshot`；如需更新系统生成 `skill.yaml`，由服务端根据该 snapshot 自动序列化

### 8.7 `POST /api/skills/{skill_id}/publish`

用途：冻结当前 draft head commit 并创建 published version。

请求：

```json
{
  "publish_reason": "Initial publish for MVP baseline."
}
```

响应至少包含：

- `publish_record`
- `published_version`
- `published_commit_sha`

### 8.8 `GET /api/skills/{skill_id}/publishes`

用途：查看技能发布记录。

## 9. 前端页面设计

### 9.1 `Skills List` `/admin/skills`

职责：

- 查看 skill 列表
- 创建 skill
- 按 `key/name/status` 检索
- 跳转 skill 详情页

页面区块：

- 顶部创建表单
- skill 列表
- GitLab 绑定摘要
- 最近发布状态

### 9.2 `Skill Detail / Editor` `/admin/skills/:skillId`

职责：

- 查看 skill 基本信息
- 查看 GitLab 绑定
- 编辑 source
- 查看 draft 状态
- 发起 publish

页面区块：

- 基本信息面板
- GitLab 绑定面板
- Source 编辑区，默认 tab：
  - `README.md`
  - `SKILL.md`
- 结构化配置区：
  - 输入输出契约
  - 能力声明
  - 编译配置
  - 运行策略
- 系统生成文件预览：
  - `skill.yaml`，只读
- Draft / Version 侧栏
- Publish 面板

页面提示：

- 当前编辑内容属于 `draft`
- `runtime` 不会执行未发布 draft
- published version 不可编辑

## 10. 冲突处理与补偿策略

### 10.1 Source 保存冲突

- 前端加载时保存 `head_commit_sha`
- 保存时必须携带 `base_commit_sha`
- 分支头变化则拒绝保存
- 返回明确错误与刷新提示

### 10.2 失败补偿原则

- GitLab 创建 project 失败：整个创建失败，不写数据库
- GitLab 初始化文件失败：整个创建失败，不写数据库
- GitLab 保存 source 失败：不更新 draft version 快照
- Publish 冻结 SHA 失败：不创建 published version 与 publish record
- GitLab 已成功但数据库写失败：返回错误，记录补偿日志，后续通过 reconcile 脚本修复

### 10.3 日志与审计

本次 MVP 至少记录以下审计点：

- skill 创建请求
- GitLab project 创建结果
- source 保存提交结果
- publish 冻结结果
- GitLab 成功但数据库失败的补偿告警

## 11. 测试策略

### 11.1 后端服务测试

- 创建 skill 时 GitLab project 与初始文件生成正确
- 编辑 source 时 `base_commit_sha` 冲突被正确拦截
- publish 时能冻结明确 `commit SHA`
- draft 与 published version 状态迁移正确

### 11.2 后端 API 测试

- `POST /api/skills`
- `GET /api/skills/{id}`
- `GET /api/skills/{id}/source`
- `PUT /api/skills/{id}/source`
- `POST /api/skills/{id}/publish`

### 11.3 前端页面测试

- `Skills List` 创建 skill
- `Skill Detail` 读取 source
- source 保存成功态与冲突态
- publish 成功后 UI 刷新

### 11.4 集成测试

使用 fake GitLab adapter 或 test double 跑通以下链路：

1. create skill
2. read source
3. edit source
4. publish
5. verify DB state

## 12. 实现顺序

1. 建立 PostgreSQL 模型、迁移与 repository
2. 建立 GitLab adapter 与统一配置
3. 落 `SkillsModule` 用例：
   - create skill
   - get skill detail
   - get source
   - save source
   - publish
4. 暴露 FastAPI API
5. 实现前端 `Skills List` 与 `Skill Detail / Editor`
6. 补测试、错误态与补偿日志

## 13. 完成定义

当以下条件全部满足时，本次 `Skills Management MVP` 视为完成：

1. 用户可在 `WEB IDE` 创建一个 skill
2. 系统会在 GitLab `skills` group 下自动创建显示名为 `skill.name`、路径为 `skill.key` 的 project
3. GitLab project 根目录自动包含 `README.md`、`SKILL.md`，并可选包含系统生成的只读 `skill.yaml`
4. skill 元数据、draft version、publish record 会写入 PostgreSQL
5. 用户可在 `WEB IDE` 查看并编辑用户 source 文件，并通过表单维护 manifest draft
6. 保存会真实提交到 GitLab 默认分支
7. publish 会冻结到明确 `commit SHA`
8. 平台会形成可追溯的 published version
9. 后续编译链路可直接基于该 published version 接入
