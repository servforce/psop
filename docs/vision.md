# PSOP Vision

## 1. 产品愿景

PSOP 的目标不是再做一个“会调用 LLM 的工作流系统”，而是构建一套围绕 `Skills -> EG -> Runtime` 主线的、可编译、可执行、可回放、可观测的 Agent Runtime 与控制平面。

它需要同时满足三件事：

- 用户可以在 `Web IDE` 中构建和发布 `Skills`。
- 系统可以把已发布 `Skills` 自动编译为符合 `PSOP-EG` 形式定义的 `EG`。
- `Runtime Kernel` 可以围绕该形式定义推进执行，并通过 `Replay + OpenTelemetry` 提供运行中与运行后的观测能力。

## 2. v1 面向的人和场景

- 面向需要构建 `Skills` 并将其正式运行的平台研发团队。
- 面向需要在私有环境中托管推理、工具调用、MCP 接入和可观测链路的企业团队。
- 面向仍处在设备侧尚未完全就绪阶段，但已经需要通过 `Web IDE` 模拟真实输入输出闭环的产品孵化阶段。

## 3. v1 成功标准

- `Web IDE` 可以创建、编辑、发布 `Skills`。
- `Skill` 发布后可以自动编译出符合 `PSOP_execution_graph_formal_v5.md` 定义的 `EG Compile Artifact`。
- 用户可以通过 `Gateway` 发起某个 `Skill Invocation`，系统能够加载其对应 `EG` 并完成执行。
- `Runtime Kernel` 能以 `Session Token` 为唯一正式状态，稳定推进 skill 对应 `EG` 的执行、工具调用与结果归并。
- `Web IDE` 能同时观测运行中的 `Skills` 和已执行完成的 `Skills`。
- `Replay + OpenTelemetry` 能完整串联 `skill_id / skill_version_id / compile_request_id / invocation_id / run_id / trace_id`。

## 4. 明确的非目标

- v1 不追求公有云 SaaS 优先，不以多租户商业控制面为首要目标。
- v1 不在核心设计阶段展开用户、权限、租户、复杂审批流等外围平台能力。
- v1 不把 DeerFlow 或任何第三方 harness 直接当作正式状态内核。
- v1 不把 MCP 当作状态同步协议，也不允许业务代码绕开网关直接连接生产能力。
- v1 不以“一 run 一进程”或“一 run 一容器”作为默认成本模型。

## 5. 设计信条

- 先把对象边界做对：用户定义 `Skills`，系统编译并执行 `EG`。
- 先把形式定义对齐，再谈实现便利。
- 先把回放与观测链路接上，再谈生产可用。
