---
name: job-system-design
description: "用于为 PSOP 设计或实现基于数据库的后台任务系统、worker 池、调度器、租约、重试、恢复和后台执行拓扑。"
allowed-tools: []
---

# Job System Design

本 skill 用于 PSOP 的后台任务系统设计与实现，尤其适用于以下场景：

- 新增 `runtime_job` 类型
- 调整 worker / scheduler / sandbox 分工
- 设计 claim / lease / retry / dead-letter
- 排查任务恢复、重复执行、幂等、延迟调度问题

## 1. 目标边界

默认目标不是“尽快把任务跑起来”，而是构建一个：

- 以数据库为事实源
- 可恢复
- 可审计
- 可观测
- 可控扩展

的任务系统。

## 2. 设计原则

- API 进程不直接执行长耗时后台任务。
- producer 写 job，consumer 领 job，scheduler 做恢复和补偿。
- claim 必须原子化，不能靠“先查后改”的竞态逻辑。
- 每个 job 类型都要声明：
  - 幂等键
  - 最大重试次数
  - 重试退避策略
  - 超时策略
  - 失败去向
- job payload 优先保存引用，不在数据库中塞超大对象。
- 所有任务都应具备 trace / log / metric 观测点。

## 3. 推荐工作流

1. 明确 producer、consumer、外部依赖和完成定义。
2. 画出 job 生命周期：
   - `pending`
   - `claimed`
   - `running`
   - `retryable_failed`
   - `dead_letter`
   - `succeeded`
3. 定义 claim / lease / renew / release / recover 规则。
4. 明确哪些错误可重试，哪些需要死信或审批。
5. 设计幂等边界：
   - 重复领取是否安全
   - 重复回调是否安全
   - 失败后重放是否安全
6. 明确观测字段：
   - `job_id`
   - `run_id`
   - `trace_id`
   - `worker_id`
   - `attempt`

## 4. PSOP 特殊要求

- `Runtime Kernel` 仍然是正式状态主权者；worker 只产生 observation/result。
- 高风险任务通过 `Sandbox Manager` 升级隔离，而不是把所有任务默认丢进 sandbox。
- 与 `MCP Gateway`、`LLM Inference Gateway` 相关的 job，必须保留 provider/tool 侧审计信息。
- 恢复逻辑要优先保证“不会错写正式状态”，再考虑吞吐。

## 5. 交付检查

- 是否有状态机说明。
- 是否说明了 claim 的原子性。
- 是否说明 lease 过期后的恢复策略。
- 是否说明重试与死信边界。
- 是否说明幂等键和观测字段。
- 是否说明 API / worker / scheduler / sandbox 的职责分层。
