# Lora Principles

本目录存放 Lora 特性的原则，用于固定特性目标与概念架构，防止开发过程中发生方向漂移。

第一原则固定特性目标，第二原则固定与框架和实现无关的概念架构；第三原则固定当前开发契约。第三原则可以绑定记忆子系统的核心能力，但不得绑定或侵入宿主 Agent 的框架、模型、进程形态和内部实现。具体方案必须服务于前两条原则，不能反过来悄然改变特性目标和架构边界。

## Normative Principles

- [永续会话 Agent 第一原则](eternal-conversation-agent.md)：固定永续会话特性的目标与边界。
- [永续会话 Agent 第二原则](eternal-conversation-architecture.md)：固定前台执行、后台整理、Working Memory、Long-term Memory、双通道使用和快照式交接的概念架构。
- [永续会话 Agent 第三原则](eternal-conversation-development.md)：固定可外挂 Harness、前台 Agent 上下文、双后台记忆 Agent，以及以 memory-cli skill 为核心的记忆构建开发契约。

## 解释性总览

- [永续会话直观总览](../guides/eternal-conversation-overview.md)：说明必要性、关键技术、整体结构以及上下文从冻结到无感替换的完整变化过程；该文档用于理解，不新增规范性原则。
