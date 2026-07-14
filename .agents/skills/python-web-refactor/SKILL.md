---
name: python-web-refactor
description: "用于重构 PSOP 中基于 FastAPI 和 SQLAlchemy 的后端代码，尤其适用于包边界、领域分层、router/service/repository 分离和长期可维护性。"
allowed-tools: []
---

# Python Web Refactor

本 skill 用于 PSOP 服务端的结构化重构与长期演进，重点面向：

- `FastAPI` 路由拆分
- `SQLAlchemy` 模型与 repository 调整
- `backend/psop/` 包结构重组
- 把脚本式实现收敛为可维护的领域模块

## 1. 适用场景

- 某个模块已经同时混杂了 router、service、repository、schema、provider 调用。
- 新功能需要明确领域边界，但现有目录组织无法支撑。
- 当前实现能跑，但不利于测试、替换依赖或长期维护。

## 2. 重构目标

- 让模块边界与 `PSOP详细系统设计v1.md` 对齐。
- 让 API、领域逻辑、基础设施、任务系统、观测层职责清晰。
- 在不破坏行为的前提下，逐步提高可测试性和可读性。

## 3. 推荐结构

- `app/`
  - API 装配、配置、依赖注入。
- `domain/`
  - 领域模型、用例、服务接口。
- `runtime/`
  - `Runtime Kernel`、`Session Token`、graph 推进。
- `gateway/`
  - terminal / mcp / inference 网关。
- `harness/`
  - DeerFlow adapter、context compiler、capability binding。
- `jobs/`
  - worker、scheduler、runtime_job handlers。
- `infra/`
  - 数据库、对象存储、provider adapter、OTel。

## 4. 重构规则

- 优先按领域拆分，而不是继续堆 `utils.py`。
- router 层只做请求解析、权限检查、调用用例、返回 DTO。
- service / use case 层承载业务流程。
- repository / gateway adapter 层承载外部依赖访问。
- schema / DTO 与 ORM model 分离，不把数据库模型直接暴露给 API。
- 对运行时核心对象，优先保证边界正确，而不是追求“少几个文件”。

## 5. 验证要求

- 重构前后关键接口契约保持一致，或明确标记 breaking change。
- 至少补上受影响模块的契约测试或集成测试。
- 对运行时、任务系统、网关改动，补充必要的 trace / log 断言或检查点。

## 6. PSOP 特殊注意事项

- 不要让 refactor 破坏 `Session Token` 与 `Runtime Kernel` 的主权边界。
- 不要因为方便而让 router 或 worker 直接改正式状态表。
- 不要把 provider SDK 逻辑散落到各业务模块里，应集中到 gateway / infra adapter。
