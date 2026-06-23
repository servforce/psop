# Guides

本目录保存面向接入方、使用方和运维方的可执行手册。这里的文档应尽量写清楚前置条件、接口、步骤、错误处理和验证方式。

## 当前文档

- [terminal-integration-v1.md](terminal-integration-v1.md)
  - 终端接入开发者手册，覆盖 Invocation、Run、Terminal Session、事件追加、文件上传、WebSocket、幂等与断线恢复。

## 维护原则

- 手册内容必须与当前公开 API、配置和运行行为一致。
- 如果架构设计尚未落地，不要在接入手册中写成可用能力。
- 示例命令应可复制执行，并明确依赖的环境变量。
