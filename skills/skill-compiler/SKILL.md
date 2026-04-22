# Skill Compiler

`skill-compiler` 用于把一个标准 Skill 目录编译成可被 `Run Server` 直接消费的最小运行产物，并输出结构化构建报告。

当前版本聚焦“最小编译闭环”，目标是把 Skill 从“源码目录”推进到“可执行契约产物”。

## Purpose

输入一个单 Skill 目录，检查其是否满足最小 Skill 标准，并生成：

- 最小运行产物
- 构建报告

## When To Use

在以下场景使用本 Skill：

- 需要验证某个 Skill 是否满足最小结构标准
- 需要把 Skill 源码目录转换成可执行运行产物
- 需要为后续 Run Server 执行链路准备可消费输入
- 需要输出构建报告，说明通过项、错误项、警告项和未消费扩展内容

## Input

输入对象是一个单 Skill 目录：

`skills/<skill-code>/`

该目录必须至少包含：

- `SKILL.md`
- `skill.yaml`
- `prompts/`
- `references/`
- `examples/`
- `tests/`

允许该目录存在自定义扩展内容。当前版本会识别并记录扩展内容，但默认不参与正式编译。

## Output

编译成功时，输出到目标 Skill 目录下的 `build/` 目录：

- `build/manifest.json`
- `build/skill-package.json`
- `build/execution-graph.json`
- `build/build-report.json`

各文件最小契约如下：

- `manifest.json`
  - `skill_code`
  - `skill_version`
  - `build_version`
  - `schema_version`
  - `entry_step_id`
  - `generated_at`
  - `graph_hash_algo`
  - `graph_hash`
  - `compat.run_server_min_version`
- `skill-package.json`
  - `name`
  - `purpose`
  - `inputs_contract`
  - `outputs_contract`
  - `global_constraints`
  - `references_index`
- `execution-graph.json`
  - `graph_version`
  - `entry_step_id`
  - `steps`
  - `transitions`
- `build-report.json`
  - `status`
  - `errors`
  - `warnings`
  - `notes`
  - `unconsumed_extensions`

### Execution Graph Strict Contract (v1)

每个 step 最小字段：

- `id`
- `title`
- `type`
- `required_inputs`
- `output_schema`
- `executor`
- `timeout_sec`
- `completion_condition`

每个 transition 最小字段：

- `from_step_id`
- `on_status`
- `to_step_id`
- `priority`

固定枚举：

- `step.type`：`collect_input | task`
- `step.status`：`succeeded | failed | waiting_input | timed_out`
- `transition.on_status`：`succeeded | failed | waiting_input | timed_out`

`required_inputs` 必须是对象数组，每个对象最小字段：

- `key`
- `source_path`（仅支持 `scene.<field>` 或 `context.<field>`）
- `value_type`（`string | number | boolean | object | array`）
- `required`（`true | false`）
- `missing_message`

`required_inputs` 可选字段：

- `rules.min_length`
- `rules.max_length`
- `rules.enum`

`executor` 结构：

- 当 `step.type = collect_input`
  - `kind = collect_input`
  - `collect_mode = merge_scene_input`
- 当 `step.type = task`
  - `kind = llm`
  - `instruction`
  - `model_profile`
  - `temperature`
  - `max_tokens`

`completion_condition` 最小字段：

- `mode`（`input_ready | executor_success`）
- `success_status`（`succeeded`）
- `failure_status`（`failed | timed_out`）

`transitions` 匹配规则：

1. 先筛选 `from_step_id == current_step_id` 且 `on_status == step.status`
2. 按 `priority` 升序排序
3. 若优先级相同，按声明顺序取第一个
4. 命中后跳转到 `to_step_id`

无 transition 命中时：

- 当前 step 状态为 `succeeded` 且没有任何后继定义，可终态成功
- 其他情况视为契约错误

`graph_hash` 规则：

- `graph_hash_algo = sha256`
- 对 `execution-graph.json` 做 canonical JSON 后计算哈希
- canonical JSON：UTF-8、递归按 key 排序、无额外空白

## Workflow

### Step 1. Check Directory Structure

检查目标目录是否存在，并确认最小标准文件和目录是否齐全。

### Step 2. Read And Validate `skill.yaml`

解析 `skill.yaml`，检查关键字段、枚举字段与结构化契约字段。

### Step 3. Read Supporting Files

读取 `SKILL.md`、`prompts/`、`references/`、`examples/`、`tests/`，确认说明、样例和验证资产具备最小完整性。

### Step 4. Generate Runtime Artifacts

基于 `skill.yaml` 和源码目录内容执行 `scripts/compile.py`，生成满足契约的最小运行产物：

- `manifest.json`
- `skill-package.json`
- `execution-graph.json`

### Step 5. Generate Build Report

输出构建报告，明确区分：

- `errors`
- `warnings`
- `notes`
- `unconsumed_extensions`

## Run Server Compatibility

`skill-compiler` 产物必须可被 `Run Server` 直接消费，当前阶段要求：

1. `Run Server` 可通过 `manifest.json` 完成兼容性、哈希算法和入口校验
2. `Run Server` 可通过 `execution-graph.json` 按 step/transition 规则推进
3. `collect_input` 与 `task` 两类 step 均可基于 `executor` 结构执行
4. 若产物缺少契约字段或结构非法，`Run Server` 明确拒绝并返回定位信息

## Failure Policy

### Hard Fail

以下问题直接导致编译失败，不生成运行产物：

- 缺少 `SKILL.md`
- 缺少 `skill.yaml`
- 缺少必需目录：`prompts/`、`references/`、`examples/`、`tests/`
- `skill.yaml` 无法解析
- `skill.yaml` 缺少关键字段（如 `name`、`code`、`purpose`、`inputs`、`outputs`、`workflow_steps`）
- 任一编译产物缺少契约必需字段
- `transition.on_status` 不在固定枚举内
- `required_inputs` 结构不满足协议
- `executor` 结构不满足协议

### Warning Only

以下问题不会阻断最小编译，但会写入警告：

- `SKILL.md` 与 `skill.yaml` 存在轻微不一致
- `references/` 只有占位内容
- `examples/` 内容过于简单
- `tests/checklist.md` 过于粗略
- 存在当前版本未消费的扩展文件或目录

## Hard Rules

- 只处理单 Skill 目录，不处理整个仓库级编译
- 当前版本不调用外部网络服务
- 当前版本先按最小标准生成运行产物
- 自定义扩展内容默认只记录，不参与正式编译
- 构建报告必须明确区分错误、警告、说明和未消费扩展内容

## Success Criteria

`skill-compiler` 在当前阶段被视为成功，至少需要做到：

- 能校验最小 Skill 结构
- 能解析 `skill.yaml`
- 能校验关键枚举与结构化契约
- 能生成最小运行产物
- 能生成结构化构建报告
- 能记录未消费的扩展内容

## Execution

当前仓库提供可执行编译脚本：

- `scripts/compile.py`

示例：

```bash
python3 skills/skill-compiler/scripts/compile.py --skill-dir skills/skill-creator
```

执行成功后，目标 Skill 目录下应生成：

- `build/manifest.json`
- `build/skill-package.json`
- `build/execution-graph.json`
- `build/build-report.json`
