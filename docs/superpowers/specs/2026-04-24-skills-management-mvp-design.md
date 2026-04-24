# Skills Management MVP 设计说明

- 日期：2026-04-24
- 状态：已达成设计共识，待进入实现规划
- 范围：`Skills 创建 -> GitLab 初始化 -> Source 编辑 -> Publish 冻结 -> PostgreSQL 入库`
- 对齐事实源：
  - `docs/PSOP概要设计v1.md`
  - `docs/PSOP前端详细设计v1.md`
  - `docs/PSOP服务端详细设计v1.md`
  - `docs/PSOP_execution_graph_formal_v5.md`

## 1. 背景与目标

当前仓库已经完成 PSOP 的概要设计、前端详细设计、服务端详细设计与 formal v5 形式定义，但正式工程代码仍停留在脚手架阶段。为了支持后续 `Publish -> Compile -> Invocation -> Runtime -> Replay` 主链路，首先需要落地 `Skills Management MVP`，把 `Skill` 作为正式对象在 `WEB IDE`、`GitLab`、`PostgreSQL` 三者之间真正打通。

本次交付目标不是实现编译和运行，而是实现以下闭环：

1. 用户可在 `WEB IDE` 创建一个正式 `skill`
2. 系统自动在 GitLab `skills` group 下创建显示名为 `skill.name`、路径为 `skill.key` 的 skill project
3. project 根目录自动初始化 `README.md`、`SKILL.md`、`skill.yaml`
4. 用户可在 `WEB IDE` 查看并编辑 skill source
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

### 2.2 本次不做

- `EG Compile Artifact` 生成
- `Invocation / Run / Replay`
- 多租户、用户身份、权限体系
- 多人协同编辑、复杂 diff/merge
- 多 skill 共用一个 GitLab project
- GitLab OAuth 或按用户身份写仓库

## 3. 核心设计结论

### 3.1 事实源原则

- `GitLab` 是 `skill source` 的正式事实源
- `PostgreSQL` 只保存平台元数据、仓库绑定、冻结 revision、索引快照与发布审计
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
- 发布不复制 source 内容，只冻结 revision 并生成审计记录

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

本次 MVP 将上述正式 `Skill` 拆分为“GitLab 源码层 + PostgreSQL 元数据层”共同承载：

| 正式字段 | 存放位置 | 说明 |
| --- | --- | --- |
| `identity` | `skill.yaml` + `skill_definition` 镜像字段 | GitLab 为正文，数据库保留检索字段 |
| `repository binding` | `skill_definition` | 包括 `gitlab_project_id`、`repository_url`、`default_branch`、`manifest_path` |
| `source revision` | `skill_version` | `draft` 跟踪 `source_ref`，`published` 必须记录 `source_commit_sha` |
| `interface contract` | `skill.yaml` | 由 Web IDE 编辑并写入 GitLab |
| `capability declarations` | `skill.yaml` | 由 Web IDE 编辑并写入 GitLab |
| `compile config` | `skill.yaml` | 为后续 `SkillCompiler` 预留 |
| `runtime policy` | `skill.yaml` | 为后续 Runtime 预留 |
| `publish metadata` | `skill_publish_record` | 发布说明、状态、冻结 commit、发布时间 |

结论：MVP 中“正式 Skill 视图”由 `GitLab source + DB metadata` 共同构成，不将数据库变成 source 的替代事实源。

## 5. GitLab 中的 Skill Source 结构

每个 GitLab skill project 的根目录至少包含：

- `README.md`
- `SKILL.md`
- `skill.yaml`

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
   - 写入满足本次 MVP 形式定义的最小结构

### 5.2 `skill.yaml` 最小契约

`skill.yaml` 是结构化正式入口，MVP 最小格式如下：

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
```

### 5.3 结构约束

- `skill.yaml` 顶层固定为 `skill`
- `identity.key` 必须与平台中的 `skill_definition.key` 一致
- `identity.name` 必须与 GitLab project 显示名一致
- `compile_config.formal_revision` 初始固定为 `psop-eg-formal/v5`
- `manifest_path` 在本次 MVP 固定为仓库根目录 `skill.yaml`

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
- `manifest_path` 初始固定为 `skill.yaml`

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
- `manifest_snapshot` 来源于 GitLab 中 `skill.yaml` 的解析结果，仅用于索引、展示和后续编译入口，不替代 GitLab 正文
- `runtime_policy_snapshot` 来源于 `skill.yaml.runtime_policy` 的解析结果，用于后续运行态展示与编译输入缓存

### 6.3 `skill_publish_record`

职责：记录一次正式发布行为。

建议字段：

- `id UUID`
- `skill_definition_id`
- `skill_version_id`
- `publish_reason`
- `publish_status`，枚举：`requested | published | failed`
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
   - 初始化 `README.md`、`SKILL.md`、`skill.yaml`
4. GitLab 初始化成功后，服务端写 PostgreSQL：
   - `skill_definition`
   - 首个 `draft skill_version`
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
- `name` 更新时，同时同步 GitLab project 显示名与 `skill.yaml.identity.name`
- `key` 在 MVP 中创建后不可修改，避免 project path、绑定关系与后续发布历史失稳

### 7.3 Edit Skill Source

可编辑文件范围：

- `README.md`
- `SKILL.md`
- `skill.yaml`

流程：

1. 前端获取当前 draft source 与 `head_commit_sha`
2. 用户在 `WEB IDE` 中编辑三个文件
3. 保存时前端提交：
   - 三个文件内容
   - `base_commit_sha`
4. 服务端提交前校验 GitLab 分支头是否仍等于 `base_commit_sha`
5. 一致时，服务端通过统一 Token 将变更提交到 GitLab 默认分支
6. GitLab 返回新的 `head_commit_sha`
7. 服务端更新当前 draft version：
   - `source_ref = default_branch`
   - `manifest_snapshot`
   - `runtime_policy_snapshot`
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
3. 解析 `skill.yaml` 并生成最新 `manifest_snapshot`
4. 创建新的 `published skill_version`
   - `status = published`
   - `source_ref = default_branch`
   - `source_commit_sha = head commit`
   - `manifest_snapshot = latest parsed manifest`
   - `runtime_policy_snapshot = latest parsed runtime policy`
5. 写入 `skill_publish_record`
6. 更新 `skill_definition.latest_published_version_id`
7. 保留原有 `draft skill_version` 继续跟踪默认分支，供后续继续编辑

发布规则：

- 发布冻结当前 head commit，不重复提交 source
- published version 一经生成不可编辑
- 后续修改继续发生在 draft 上，再次 publish 生成新的 published version

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
- `skill_yaml_content`
- `source_ref`
- `head_commit_sha`

### 8.6 `PUT /api/skills/{skill_id}/source`

用途：保存当前 draft source。

请求：

```json
{
  "base_commit_sha": "abc123",
  "readme_content": "...",
  "skill_md_content": "...",
  "skill_yaml_content": "..."
}
```

行为：

- 基于 `base_commit_sha` 做分支头并发检查
- GitLab 提交成功后刷新 draft version 的 `manifest_snapshot` 与 `runtime_policy_snapshot`

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
- Source 编辑区，三个 tab：
  - `README.md`
  - `SKILL.md`
  - `skill.yaml`
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
3. GitLab project 根目录自动包含 `README.md`、`SKILL.md`、`skill.yaml`
4. skill 元数据、draft version、publish record 会写入 PostgreSQL
5. 用户可在 `WEB IDE` 查看并编辑三份 source 文件
6. 保存会真实提交到 GitLab 默认分支
7. publish 会冻结到明确 `commit SHA`
8. 平台会形成可追溯的 published version
9. 后续编译链路可直接基于该 published version 接入
