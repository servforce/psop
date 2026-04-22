# Skill Compiler Acceptance Checklist

## Input Validation

- [ ] 能接收一个单 Skill 目录作为输入
- [ ] 能检查最小标准文件是否存在
- [ ] 能检查最小标准目录是否存在
- [ ] 能识别并记录扩展内容

## Contract Validation

- [ ] 能解析 `skill.yaml`
- [ ] 能检查关键字段是否存在
- [ ] 当 `skill.yaml` 无法解析时，明确失败
- [ ] 当关键字段缺失时，明确失败

## Build Output

- [ ] 成功时能生成 `build/manifest.json`
- [ ] 成功时能生成 `build/skill-package.json`
- [ ] 成功时能生成 `build/execution-graph.json`
- [ ] 成功时能生成 `build/build-report.json`
- [ ] 可通过 `python3 skills/skill-compiler/scripts/compile.py --skill-dir <skill-dir>` 完成编译

## Artifact Contract

- [ ] `manifest.json` 包含运行包身份与兼容字段
- [ ] `manifest.json` 包含 `graph_hash_algo` 且值为 `sha256`
- [ ] `skill-package.json` 包含输入、输出、约束与 references 索引字段
- [ ] `execution-graph.json` 包含入口、steps、transitions 字段
- [ ] step 节点包含最小执行字段（如 `required_inputs`、`executor`、`completion_condition`）
- [ ] transition 节点包含 `from_step_id`、`on_status`、`to_step_id`、`priority`
- [ ] `step.type` 仅使用 `collect_input | task`
- [ ] `transition.on_status` 仅使用 `succeeded | failed | waiting_input | timed_out`
- [ ] `required_inputs` 为对象数组且字段满足协议
- [ ] `required_inputs.source_path` 仅使用 `scene.` 或 `context.` 前缀
- [ ] `executor` 在 `collect_input` 与 `task` 两种 step 下均满足固定结构
- [ ] `completion_condition` 包含 `mode`、`success_status`、`failure_status`
- [ ] `build-report.json` 包含 `errors/warnings/notes/unconsumed_extensions`

## Failure Policy

- [ ] 缺少核心文件时直接失败
- [ ] 缺少核心目录时直接失败
- [ ] `transition.on_status` 非法时直接失败
- [ ] `required_inputs` 非法时直接失败
- [ ] `executor` 非法时直接失败
- [ ] 轻微内容不足时给出警告而非直接失败
- [ ] 对未消费扩展内容给出记录说明

## Minimum Goal

- [ ] 能完成最小编译闭环
- [ ] 能为后续 Run Server 消费提供最小运行产物草稿
- [ ] 能输出结构化构建报告
- [ ] 产物字段契约足以支持 Run Server 按执行图直接推进
- [ ] transition 匹配可按 `from_step_id + on_status + priority` 确定唯一结果
