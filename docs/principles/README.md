# Lora Principles

本目录存放 Lora 特性的原则，用于固定特性目标与概念架构，防止开发过程中发生方向漂移。

这里不绑定具体框架、数据库、模型或实现。具体方案必须服务于这些原则，不能反过来悄然改变特性目标和架构边界。

## Normative Principles

- [永续会话 Agent 第一原则](eternal-conversation-agent.md)：固定永续会话特性的目标与边界。
- [永续会话 Agent 第二原则](eternal-conversation-architecture.md)：固定前台执行、后台整理、Working Memory、Long-term Memory、双通道使用和快照式交接的概念架构。
- [永续会话 Agent 第三原则](eternal-conversation-development.md)：固定可外挂 Harness 的开发契约；当前已确认前台 Agent 的上下文组成与控制边界。
