# Expected Output

若输入目录满足最小标准，则应在“输入 Skill 目录”下生成：

```text
skills/<input-skill-code>/build/
├─ manifest.json
├─ skill-package.json
├─ execution-graph.json
└─ build-report.json
```

可通过以下命令执行生成：

```bash
python3 skills/skill-compiler/scripts/compile.py --skill-dir skills/<input-skill-code>
```

期望行为包括：

- `manifest.json`
  标识被编译 Skill 的基础身份与兼容信息，至少包含：
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
  汇总该 Skill 的基本运行契约，至少包含：
  - `name`
  - `purpose`
  - `inputs_contract`
  - `outputs_contract`
  - `global_constraints`
  - `references_index`
- `execution-graph.json`
  根据 `workflow_steps` 生成最小执行图契约，至少包含：
  - `graph_version`
  - `entry_step_id`
  - `steps`
  - `transitions`
  - 且 `steps`、`transitions` 的子结构满足严格协议
- `build-report.json`
  记录编译结果、错误、警告、说明与未消费扩展内容，至少包含：
  - `status`
  - `errors`
  - `warnings`
  - `notes`
  - `unconsumed_extensions`

最小结构示例（示意）：

```json
{
  "manifest.json": {
    "skill_code": "skill-creator",
    "skill_version": "0.1.0",
    "build_version": "0.1.0+build.1",
    "schema_version": "psop-skill-build/v1",
    "entry_step_id": "collect_requirements",
    "generated_at": "2026-04-01T15:30:00Z",
    "graph_hash_algo": "sha256",
    "graph_hash": "6a6f0d...",
    "compat": {
      "run_server_min_version": "0.1.0"
    }
  }
}
```

```json
{
  "execution-graph.json": {
    "graph_version": "v1",
    "entry_step_id": "collect_requirements",
    "steps": [
      {
        "id": "collect_requirements",
        "title": "收集输入",
        "type": "collect_input",
        "required_inputs": [
          {
            "key": "skill_name",
            "source_path": "scene.skill_name",
            "value_type": "string",
            "required": true,
            "rules": {
              "min_length": 2,
              "max_length": 64
            },
            "missing_message": "请提供 skill 名称"
          }
        ],
        "output_schema": {
          "type": "object"
        },
        "executor": {
          "kind": "collect_input",
          "collect_mode": "merge_scene_input"
        },
        "timeout_sec": 300,
        "completion_condition": {
          "mode": "input_ready",
          "success_status": "succeeded",
          "failure_status": "failed"
        }
      },
      {
        "id": "compile_skill",
        "title": "生成编译产物",
        "type": "task",
        "required_inputs": [],
        "output_schema": {
          "type": "object"
        },
        "executor": {
          "kind": "llm",
          "instruction": "基于输入生成产物并校验契约",
          "model_profile": "default",
          "temperature": 0.2,
          "max_tokens": 2000
        },
        "timeout_sec": 600,
        "completion_condition": {
          "mode": "executor_success",
          "success_status": "succeeded",
          "failure_status": "timed_out"
        }
      }
    ],
    "transitions": [
      {
        "from_step_id": "collect_requirements",
        "on_status": "succeeded",
        "to_step_id": "compile_skill",
        "priority": 10
      }
    ]
  }
}
```

如果输入目录不满足最小标准，则不生成前三个运行产物，只输出失败编译报告或明确失败结论。
