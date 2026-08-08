# Pygent 0.2.2 Agent Runtime 改造评估

## 改造后的执行边界

FastAPI 不再直接用裸 `asyncio.Task` 执行 Agent。服务为每个聊天 turn 创建一个
`ManagedChatTurn`，并把它绑定到进程级共享的 `LocalRuntime`：

```text
FastAPI SSE
  -> shared LocalRuntime + Binding
    -> LoraAgent (one root per chat turn)
        -> ReActLayer
          -> DynamicPromptModule -> ContextCompressionModule -> ModelCallLayer
          -> ToolCallLayer -> ToolAuditModule -> SkillReminderModule -> PersistedDiffModule
```

默认容量策略位于 `lora_api/services/managed_chat_runtime.py`：

| 资源 | 默认值 | 作用 |
|---|---:|---|
| live agent executions | 32 | 限制运行中和排队中的 Agent 总量 |
| runnable agent executions | 4 | 限制同时占用执行槽的 Agent |
| admission queue | 64 | 满载时提供有界背压 |
| model concurrency | 4 | 限制跨 Agent 的模型调用 |
| tool concurrency | 8 | 限制跨 Agent 的工具调用 |
| execution deadline | 30 分钟 | 避免失控 turn 永久占用服务资源 |

客户端断开超过恢复窗口后，服务调用 Pygent `ExecutionHandle.cancel()`；FastAPI
lifespan 关闭时调用 `LocalRuntime.close(cancel=True)`。完成事件会返回
`runtime_execution_id` 和 `runtime_trace_id`。

## 得到的实质收益

- Agent admission、排队、并发上限、deadline、取消和 shutdown 由一个控制面负责。
- 不同 session 可在 Pygent 容量上限内并发；同一 session 在进入 Runtime 前串行排队，避免并发写坏历史。
- 主模型与上下文压缩模型是 execution graph 中的真实 child module；模型流式事件来自
  Pygent execution journal。
- 工具通过正式的 `ToolSpec`、授权 Module、`ToolCallLayer`、ToolTask 和 ToolResult 链路执行；
  Lora 的 trace、workspace 安全和文件效果逻辑作为部署 executor 保留。
- API SSE 只负责投影 execution events，不再拥有模型调用的生命周期。

这不会缩短模型本身的推理时间。性能收益主要体现为负载下避免过量并发、连接耗尽和
无界排队，从而改善稳定性、内存上界和尾延迟。

## 本机验证结果

在 2026-08-07 的本地开发环境中：

- Pygent 空 execution：中位额外开销约 `0.306 ms`，P95 约 `0.650 ms`。
- 8 个 50 ms 模拟 Agent、`max_runnable=2`：实测峰值并发严格为 2，总耗时约
  `0.246 s`（接近四批执行）。
- 真实 managed API 多工具请求：37.56 秒，13 次工具结果，无错误，产生可关联的
  execution ID 和 trace ID。
- ToolCallLayer 改造后的真实请求：`glob` 确认 `pyproject.toml` 后返回 `lora`，耗时约
  14.6 秒；一次供应商瞬时失败也验证了错误会作为 `chat.error` 暴露，而不会伪装成空成功。
- 主测试集：`229 passed, 13 subtests passed`。

这些数字是开发机样本，不代表生产 SLA；正式比较应固定模型、输入、并发阶梯和上游
限流条件，分别观察吞吐、P50/P95/P99、排队时间、错误率和峰值内存。

## 开发成本判断

首次接入并不“免费”：需要建立服务 Runtime 生命周期、Agent root、模型 child、事件桥接
和取消测试。本次新增的 Runtime 服务实现约 170 行，并修改了 Agent/API 边界。

后续新增 Agent 类型时，开发成本会下降，前提是遵守统一入口：

1. 把一次 Agent 工作声明为 Pygent `Agent` root。
2. 把模型调用声明为 child `ModelCallLayer`。
3. 外部工具通过 `ToolCallLayer` 或 `tool_permit()` 进入容量控制。
4. FastAPI 只启动 execution、订阅事件和取消 handle。

这样无需为每个 Agent 重写 semaphore、任务表、排队、deadline、取消传播和 shutdown
逻辑。若 Agent 只有单用户、低并发且没有长任务，这部分治理的收益有限；对多会话桌面端、
API 服务或未来的子 Agent 并行执行，收益明显。

## 当前限制

- `ToolInterceptor` 仍是 Lora 的工具审计和文件效果业务边界，但执行身份、授权、任务状态、
  timeout、容量和事件已由 `ToolCallLayer` 管理。
- 当前容量值是代码级默认值，生产化前应进入配置文件并增加运行指标端点。
- `identity=session_id` 只用于追踪；当前由 API registry 在进入 Runtime 前实现 session 级互斥。
  若 Pygent 后续提供 keyed capacity，可移除这层应用队列并统一等待指标与过载策略。
