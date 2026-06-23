# PSOP AHPU外部项目调研与端侧推理可行性预研

## 1. 文档定位

本文围绕 `AHPU` 这一概念，基于官方 GitHub 一手资料，回答以下问题：

1. `AHPU` 是否可以先定义为软件形态的 `Agent Harness Process Unit`；
2. CPU 类比下的指令层、高速缓存层、系统总线层、I/O 层、存储层、执行单元层该如何映射到 PSOP；
3. 未来是否值得把 AHPU 固化为端侧推理设备、板卡、SoC 甚至芯片；
4. 若走向硬件化，哪些层适合被固化，哪些层不适合。

本文检索基线日期为：`2026-04-20`。

本文是预研与建议文档，不构成当前版本的硬件承诺。

---

## 2. 研究问题与双层结论

## 2.1 近期结论：先把 AHPU 定义为软件形态

当前最合理的定义是：

> **AHPU = Agent Harness Process Unit**  
> 一个以 `Session Token` 为正式状态对象、以 `Runtime Kernel` 为状态主权者、以 `Lead Agent / Capability Host / Memory / Multimodal I/O` 为执行资源、以可恢复执行和可观测 trace 为基本能力的软件运行单元。

换言之，AHPU 第一阶段应先是**软件抽象与运行时分层**，而不是芯片名称。

## 2.2 远期结论：可硬化，但不应一开始硬化完整 agent runtime

如果未来进入硬件化，建议只硬化其中**稳定、热点、可形式化**的子层，例如：

- 多模态预处理
- KV / cache 管理
- 低比特推理
- 固定 capability dispatch 热路径
- secure I/O
- DMA / stream path

不建议一开始就尝试硬化以下层：

- 高层策略推理
- 开放式规划
- 动态工具发现
- 业务语义 merge 规则
- 审批与组织策略

因此，远期路线更像：

```text
软件 AHPU
-> 端侧推理板卡 / 模组 / 协处理器
-> 专用 SoC 子系统
-> 再决定是否值得更强专用化
```

而不是：

```text
直接发明一颗完整替代通用系统软件的“Agent CPU”
```

---

## 3. 资料范围与筛选标准

本文只使用官方 GitHub 仓库与仓库直接链接的官方文档做主证据，主要分为五组：

### 3.1 模型导出 / 端侧推理

- `pytorch/executorch`
- `ggml-org/llama.cpp`

### 3.2 模型优化 / 量化 / 训练到服务

- `pytorch/ao`
- `apache/tvm`

### 3.3 服务侧推理运行时

- `vllm-project/vllm`

### 3.4 开源硬件 / SoC 框架 / 模拟器

- `nvdla/hw`
- `ucb-bar/chipyard`
- `gem5/gem5`

### 3.5 可信启动与设备信任根

- `lowRISC/opentitan`

这些项目分别回答不同问题：

- ExecuTorch：边端 AOT 编译、轻量 runtime、后端分区
- TorchAO：量化、QAT、训练到服务的一体化优化
- TVM：IR、跨层编译、图与算子联合优化
- llama.cpp：低依赖、本地/边端 C/C++ 推理 runtime
- vLLM：服务侧大模型推理热点与内存/调度优化
- NVDLA：开源推理加速器的硬件形态
- Chipyard：如果真做 SoC，应如何组合 CPU、加速器、仿真和实现流
- gem5：如何在流片前做体系结构级验证
- OpenTitan：如果 AHPU 变成设备，可信启动与信任根怎么处理

---

## 4. 外部项目调研

## 4.1 ExecuTorch：边端 AOT 部署路径

ExecuTorch 官方将自己定义为：

- PyTorch's unified solution for deploying AI models on-device
- from smartphones to microcontrollers
- AOT compilation
- export -> compile -> execute
- one export, multiple backends

并强调：

- `torch.export()` 导出
- 分区到不同后端
- `.pte` 作为部署产物
- 轻量 C++ runtime 在设备侧执行

来源：

- https://github.com/pytorch/executorch
- https://github.com/pytorch/executorch/blob/main/README.md

对 PSOP AHPU 的启发：

- AHPU 如果进入端侧，必须存在类似 `PSOP IR / AHPU IR -> AOT artifact -> edge runtime` 的链路；
- “一份导出，多种后端”非常适合将来把相同 agent 子图下沉到不同 NPU / GPU / CPU；
- 这说明 AHPU 的第一性问题不是“先造硬件”，而是先定义可分区、可编译、可部署的中间表示。

## 4.2 TorchAO：训练到服务的模型优化层

TorchAO 官方把自己定义为：

- PyTorch-Native Training-to-Serving Model Optimization
- quantization
- QAT
- float8 training
- integrates with vLLM and ExecuTorch

来源：

- https://github.com/pytorch/ao
- https://github.com/pytorch/ao/blob/main/README.md

对 PSOP AHPU 的启发：

- 若 AHPU 要做端侧，不可能只谈 runtime，不谈量化与内存；
- AHPU 的“高速缓存层”和“执行单元层”最终会被模型压缩、权重量化、activation 量化直接影响；
- TorchAO 与 ExecuTorch、vLLM 的联动说明：**模型优化层、推理 runtime 层、硬件后端层必须联动设计**。

## 4.3 TVM：跨层编译框架

TVM 官方将自己定义为：

- open machine learning compilation framework
- Python-first customization
- universal deployment
- TensorIR + Relax 的 cross-level design
- foundation infra for vertical compilers for domains, such as LLMs

来源：

- https://github.com/apache/tvm
- https://github.com/apache/tvm/blob/main/README.md

对 PSOP AHPU 的启发：

- AHPU 如果要成为“类 CPU 架构”，不能停留在口头类比，必须有中间表示与编译链；
- TVM 的价值不在于它能直接运行 agent，而在于它说明：
  - 图级表示
  - 张量级表示
  - Python-first 编译扩展
  - 最小可部署模块

  这些都应该进入 AHPU 的设计方法论。

## 4.4 llama.cpp：本地/边端推理 runtime

`llama.cpp` 的核心特点是：

- plain C/C++ implementation
- minimal setup
- wide hardware coverage
- 多种低比特量化
- 本地 OpenAI-compatible server
- CPU+GPU hybrid inference

来源：

- https://github.com/ggml-org/llama.cpp
- https://github.com/ggml-org/llama.cpp/blob/master/README.md

对 PSOP AHPU 的启发：

- 如果 PSOP 要做端侧 agent 设备，`llama.cpp` 类 runtime 是很现实的参考；
- 它证明了“低依赖、跨平台、低比特、本地 API server”是现实可行的；
- 但它主要解决的是 model runtime，不解决 harness state、approval、trace、policy 等更高层问题。

因此，`llama.cpp` 更像 AHPU 中的 **Reasoning Unit backend**，而不是 AHPU 全部。

## 4.5 vLLM：服务侧热点优化

vLLM 官方强调：

- fast LLM inference and serving
- PagedAttention
- continuous batching
- prefix caching
- structured outputs
- tool calling and reasoning parsers

来源：

- https://github.com/vllm-project/vllm
- https://github.com/vllm-project/vllm/blob/main/README.md

对 PSOP AHPU 的启发：

- vLLM 不适合直接当边端 runtime 主体，但非常适合作为“服务侧 AHPU”热点参考；
- `PagedAttention`、continuous batching、prefix caching 这些思想，能帮助定义 AHPU 的缓存层与内存层；
- 如果未来 AHPU 形成“云边协同”，云端那一侧更像 vLLM，边端那一侧更像 ExecuTorch / llama.cpp。

## 4.6 NVDLA：开源推理加速器原型

NVDLA 官方将自己定义为：

- open architecture for deep learning inference accelerators
- modular architecture
- scalable
- configurable
- simplify integration and portability

来源：

- https://github.com/nvdla/hw
- https://github.com/nvdla/hw/blob/nvdlav1/README.md

对 PSOP AHPU 的启发：

- 真正可硬化的部分，应是推理加速器、内存访问与数据搬运等稳定子层；
- NVDLA 恰恰说明“推理硬件”本身是可以模块化、可配置、可移植的；
- 但它是 DLA，不是 agent runtime，也不负责 memory policy、tool selection、formal state merge。

## 4.7 Chipyard：SoC 级集成框架

Chipyard 官方强调：

- agile development of Chisel-based systems-on-chip
- produce RISC-V SoC
- custom accelerators
- includes NVDLA
- simulation, FPGA, VLSI flows

来源：

- https://github.com/ucb-bar/chipyard
- https://github.com/ucb-bar/chipyard/blob/main/README.md

对 PSOP AHPU 的启发：

- 如果未来真要做专用 SoC，不能只谈 accelerator，还必须谈：
  - CPU 主核
  - memory system
  - peripheral
  - accelerator integration
  - simulation / FPGA / VLSI flow
- Chipyard 适合作为“长期 AHPU SoC 原型平台”，而不是当下软件架构参考。

## 4.8 gem5：体系结构级验证

gem5 官方把自己定义为：

- modular platform for computer-system architecture research
- system-level architecture and processor microarchitecture
- evaluate hardware designs and system optimizations

来源：

- https://github.com/gem5/gem5
- https://github.com/gem5/gem5/blob/stable/README.md

对 PSOP AHPU 的启发：

- 如果未来要论证 AHPU 芯片化是否值得，必须先做体系结构仿真；
- gem5 很适合用于验证：
  - memory hierarchy
  - bus contention
  - accelerator attachment
  - cache hit/miss 对延迟的影响

## 4.9 OpenTitan：可信启动与信任根

OpenTitan 官方定位是：

- open source silicon Root of Trust
- transparent, trustworthy, and secure

来源：

- https://github.com/lowRISC/opentitan
- https://github.com/lowRISC/opentitan/blob/master/README.md

对 PSOP AHPU 的启发：

- 如果未来 AHPU 成为端侧设备或协处理器，就不能只谈算力，还必须谈：
  - secure boot
  - key management
  - attestation
  - trusted execution boundary

换言之，AHPU 一旦进入硬件层，安全设计必须从 Day 1 进入视野。

---

## 5. 近期软件 AHPU 设计

## 5.1 定义

建议把近期的软件 AHPU 定义为：

> 一个围绕 `Session Token`、`Prompt View`、`Capability Host`、`Memory`、`Multimodal I/O` 和 `Trace` 运转的 agent execution unit。  
> 它不替代 `Runtime Kernel`，而是为 `Runtime Kernel` 提供一组类似 CPU 执行资源的抽象。

## 5.2 指令层

建议先定义逻辑指令，而不是物理 ISA。可先形成 `PSOP IR / AHPU IR` 级别的操作类：

| 指令类 | 作用 | 对应 PSOP 语义 |
| --- | --- | --- |
| `PROJECT_CONTEXT` | 把 `Session Token` 投影为当前执行所需视图 | `Prompt Projection` |
| `SELECT_NODE` | 在候选节点中择优 | `Sel` / agent advice |
| `RETRIEVE_MEMORY` | 在已绑定记忆域内检索 | `MemoryScopeBinding` |
| `CALL_CAPABILITY` | 调 MCP / tool / skill / code | `Actor` |
| `MERGE_OBS` | 将观察结果并回正式状态 | `Merge` |
| `WAIT_EVENT` | 合法挂起等待输入/回调/审批 | `Waiting` |
| `CHECKPOINT` | 写入可恢复推进点 | `Token Version + Trace` |
| `EMIT_TRACE` | 输出结构化运行事件 | `TraceEvent` |
| `ENFORCE_POLICY` | 执行预算/权限/审批校验 | `Guardrails` |

这些逻辑指令未来可以继续向下映射到：

- Python runtime
- Rust/C++ runtime
- Edge runtime backend
- NPU / accelerator dispatch

## 5.3 高速缓存层

建议的软件 AHPU 缓存层包括：

1. `working memory cache`
   - 当前 run 的热事实、寄存器、最近 trace 摘要
2. `prompt view cache`
   - 常用 projection 结果，避免重复上下文拼装
3. `capability metadata cache`
   - 当前能力目录、策略、schema、endpoint 元信息
4. `multimodal buffer`
   - 当前图片、音频片段、视频帧、OCR / ASR 中间结果
5. `kv/cache handle`
   - 推理 backend 暴露的 KV cache / prefix cache 句柄

这里可以明显借鉴：

- vLLM 的 `PagedAttention / prefix caching`
- Daytona 的 snapshots
- ExecuTorch 的 memory planning

## 5.4 系统总线层

建议先定义逻辑总线，而不是物理总线：

| 总线 | 作用 |
| --- | --- |
| `control bus` | `Runtime Kernel` 与 AHPU 调度、状态切换、审批控制 |
| `memory bus` | token snapshot、memory index、KV/cache、artifact metadata |
| `capability bus` | MCP / tool / skill / sandbox 调度 |
| `trace bus` | 运行事件、指标、审计输出 |
| `stream bus` | 音频、视频、图像、实时数据流 |

这样做的好处是，未来无论底层是单机、边端设备、还是专用 SoC，都可以保持同一套上层结构语言。

## 5.5 I/O 层

AHPU 的 I/O 层建议统一定义为：

- 文本 I/O
- 音频 I/O
- 视频 / 图像 I/O
- MCP / API I/O
- device callback
- approval input
- timer / event input

这与 PSOP 现有的统一 terminal protocol 方向是一致的，也便于未来把某些输入前处理下沉到边端硬件。

## 5.6 存储层

AHPU 的存储层建议分成：

- `token snapshot store`
- `trace store`
- `memory index`
- `artifact / object store`
- `model weights store`
- `sandbox snapshot store`

其中：

- 前四项仍以软件形态为主
- `model weights store` 更接近推理 runtime 层
- `sandbox snapshot store` 则与执行环境恢复密切相关

## 5.7 执行单元层

建议的软件 AHPU 执行单元包括：

| 执行单元 | 职责 |
| --- | --- |
| `reasoning unit` | 调用 LLM backend，执行节点级推理 |
| `tool unit` | 调用 tool / MCP / code / skill |
| `multimodal preprocess unit` | OCR、ASR、帧抽取、压缩与格式转换 |
| `policy / approval unit` | 审批、权限、预算、风险门控 |
| `context compile unit` | projection、裁剪、摘要、压缩 |

其中，未来最值得首先下沉到专用运行时甚至硬件的，通常不是 `reasoning policy` 本身，而是：

- preprocess
- cache management
- dispatch
- low-bit inference

---

## 6. 远期芯片 AHPU 设计判断

## 6.1 哪些层值得硬化

结合 ExecuTorch、TorchAO、vLLM、NVDLA、Chipyard，当前最有价值的硬化方向是：

1. **多模态预处理**
   - 音频特征提取
   - 图像 resize / normalize
   - 视频帧采样
   - OCR / ASR 前流水

2. **KV / cache 管理**
   - prefix cache
   - KV layout
   - hot/cold cache move

3. **低比特推理**
   - int4 / int8 / fp8 / mxfp4 类热点推理

4. **固定 capability dispatch**
   - 某些稳定、高频、结构固定的本地工具调用

5. **secure I/O**
   - 传感器到推理单元的数据可信链路

6. **DMA / stream path**
   - 视频流、音频流、共享 buffer 的高速搬运

这些层共同特征是：

- 热点明确
- 形式稳定
- 容易被 benchmark
- 与业务语义相对解耦

## 6.2 哪些层不宜硬化

不建议硬化：

- 高层策略推理
- 开放式规划
- 动态工具发现
- 动态 skill 选择
- 业务语义 merge 规则
- 审批规则与组织政策

原因在于这些层：

- 变化频率高
- 依赖业务语义
- 依赖产品策略
- 很难形成长期稳定指令集

如果过早硬化，结果往往是硬件把软件创新空间锁死。

## 6.3 推荐架构路径

建议把长期架构路线写成：

```text
PSOP IR / AHPU IR
-> 编译 / 量化 / 分区
-> ExecuTorch / TVM / llama.cpp / vLLM backend
-> NPU / GPU / CPU / DSP
-> 边端板卡 / 模组 / 协处理器
-> 再评估专用 SoC
```

其中：

- `ExecuTorch` 负责边端 AOT artifact 与多后端部署
- `TorchAO` 负责量化与训练到服务优化
- `TVM` 负责跨层 IR 与编译框架思路
- `llama.cpp` 负责本地/低依赖 runtime
- `vLLM` 负责服务侧热点优化与 cache 设计参考
- `NVDLA` 负责可硬化加速器参考
- `Chipyard` 负责 SoC 集成与原型路线
- `gem5` 负责体系结构仿真验证
- `OpenTitan` 负责安全与信任根

## 6.4 如果未来真做芯片，推荐怎么走

建议路线：

### Phase A：软件 AHPU

- 先定义逻辑指令、缓存、总线、执行单元
- 建立 runtime metrics 和 hotspot profiling

### Phase B：可插拔 backend

- 支持 `ExecuTorch / llama.cpp / vLLM` 等 backend
- 建立云边协同 profile

### Phase C：协处理器化

- 把 preprocess、cache、low-bit inference、stream path 下沉到板卡/模组/NPU

### Phase D：SoC 原型

- 用 `Chipyard + NVDLA` 组合原型
- 用 `gem5` 做体系结构模拟
- 用 `OpenTitan` 补齐可信启动与安全边界

只有在这些阶段都跑通，才有资格讨论“完整 AHPU 芯片”。

---

## 7. 面向 PSOP 的最终判断

## 7.1 当前阶段应怎么写

PSOP 当前应把 AHPU 写成：

- 一种**软件架构抽象**
- 一组**运行时执行资源分层**
- 一套**未来可编译、可分区、可部署、可硬化**的结构语言

而不应写成：

- 当前版本必须自研芯片
- 当前版本必须定义物理 ISA
- 当前版本必须替代 Linux / CPU / GPU / NPU 生态

## 7.2 对 PSOP 正式语义的约束

无论未来 AHPU 走到哪一步，都不应破坏以下事实：

1. `Session Token` 仍是软件层正式状态主权对象；
2. `Runtime Kernel` 仍是唯一 formal commit 主体；
3. `Trace` 仍是回放与审计基线；
4. 硬件只加速稳定热点，不接管业务语义主权。

## 7.3 一句话结论

> AHPU 值得做，但当前应先做成软件抽象与运行时分层；  
> 若未来进入硬件化，首选“端侧推理板卡 / 模组 / SoC 协处理器”路线，而不是一步到位发明一颗完整替代通用系统软件的 Agent CPU。

---

## 8. 路线图建议

### Phase 1：软件 AHPU 抽象与 runtime metrics

- 定义 `PSOP IR / AHPU IR`
- 定义逻辑指令层、缓存层、总线层
- 建立性能观测：延迟、cache 命中、模型调用成本、流式 I/O 热点

### Phase 2：推理后端可插拔与端侧 profile

- 接入边端 backend 与服务侧 backend
- 对比 `ExecuTorch / llama.cpp / vLLM`
- 明确云边协同分工

### Phase 3：热点固化

- 量化、KV/cache、multimodal preprocess、stream DMA、固定 dispatch

### Phase 4：模拟器与开源硬件原型

- `gem5` 验证 memory hierarchy 与总线争用
- `Chipyard + NVDLA` 原型化
- `OpenTitan` 补齐安全链路

---

## 9. 参考来源

以下链接均为官方 GitHub 仓库或其 README 直接给出的官方文档入口，检索日期均为 `2026-04-20`：

- ExecuTorch: https://github.com/pytorch/executorch
- TorchAO: https://github.com/pytorch/ao
- Apache TVM: https://github.com/apache/tvm
- llama.cpp: https://github.com/ggml-org/llama.cpp
- vLLM: https://github.com/vllm-project/vllm
- NVDLA: https://github.com/nvdla/hw
- Chipyard: https://github.com/ucb-bar/chipyard
- gem5: https://github.com/gem5/gem5
- OpenTitan: https://github.com/lowRISC/opentitan
