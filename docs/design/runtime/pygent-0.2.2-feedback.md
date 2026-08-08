# 给 Pygent 0.2.2 的工程化建议

以下建议来自 Lora 将 FastAPI 多会话 Agent、模型流、工具审计和文件副作用迁移到
Pygent 0.2.2 的实际过程。每项都包含遇到的场景、影响和建议方向。

## P0：让 Tool executor 获得请求级 portable Context

### 场景

Lora 的工具 executor 需要当前 `session_id`、`case_run_id`、`turn_id`、workspace 和权限事实，
用于 trace、文件效果和授权。`ToolCallLayer` 的授权 Module 可以读取 Agent `Context`，但
`ToolExecutor.execute(spec, call, ToolExecutionContext)` 的 `ToolExecutionContext` 只有 deadline
和事件回调，拿不到调用它的 portable `Context`。

### 影响

部署只能为每个 Agent turn 创建一套带请求状态的 executor registry，或者维护 execution ID
到请求状态的外部映射。这削弱了 ToolSpec/registry 的部署复用，也容易把请求状态放入
`trusted_live_resource_attributes`。

### 建议

给 `ToolExecutionContext` 增加只读的 portable request facts，例如：

```python
ToolExecutionContext(
    deadline=...,
    metadata=context.metadata,
    execution_id=...,
    trace_id=...,
    emit=...,
)
```

也可以允许 `ToolCallLayer` 配置一个 portable `execution_context_builder`，明确选择哪些 Context
字段可以进入 executor，而不是传递完整 prompt/history。

## P0：ModelCallLayer 最终结果应保留 usage 与 provider identity

### 场景

`ModelCallLayer` 的 effect value 已包含 `usage` 和 `provider_request_id`，但
`_message_from_effect()` 返回的 `AIMessage` 没有把这些信息放入 metadata。父 Agent 直接调用
child 时只能拿到文本和 tool calls；若要记录 token usage，只能重新订阅 execution journal。

### 影响

Agent 内部预算、trace 和计费逻辑依赖外部事件旁路；direct 与 managed 调用容易出现不同
行为，恢复后的 effect 结果也不方便重建 usage。

### 建议

将 usage、route/provider request ID 放入稳定的 `AIMessage.metadata`，或返回一个公开、可移植的
`ModelCallResult(message, usage, provider_request_id)`。事件继续用于流式观察，最终结果负责完整性。

## P1：提供 managed Child 的局部流式调用接口

### 场景

Module 内调用 child 的标准方式是 `await child(message, context)`。它能保持 lineage，但父 Module
无法在本地消费 child 的 delta；调用 `child.stream()` 又会尝试创建新 root 并被拒绝。

### 影响

Lora 必须在 root 的外部订阅整个 execution journal，再按 `module_path` 过滤主模型与压缩模型
事件。Agent 本身无法自然地把 child delta 转换为自己的领域事件，CLI/API 需要重复桥接逻辑。

### 建议

增加 scoped child handle，例如：

```python
async with self.stream_child(self.model, message, context) as child:
    async for event in child:
        ...
    output, context = await child.result()
```

它必须复用当前 ExecutionScope，不创建第二个 root，并保留 child lineage、deadline 和取消传播。

## P1：Binding 支持按业务 key 的并发策略

### 场景

API 可以限制全局 runnable Agent 数，但同一 session 的两个 turn 不能并发写 session history。
`ExecutionOptions.identity=session_id` 只提供身份，不提供串行保证。

### 影响

应用仍需维护 `dict[session_id, Lock]`，并处理锁回收、取消和 shutdown；如果在 root 内等待应用锁，
还会无效占用 runnable execution slot。

### 建议

增加 `concurrency_key` 或 keyed capacity：

```python
ExecutionOptions(concurrency_key=session_id)
Binding(max_runnable_per_key=1)
```

排队应发生在取得 runnable lease 之前，并公开 keyed queue 的拒绝与等待指标。

## P1：公开 Runtime 容量和排队指标

### 场景

评估 Runtime 是否改善服务性能，需要观察 admitted、queued、runnable、resource wait、deadline 和
取消数量。目前公共 API 主要提供单个 ExecutionHandle，没有稳定的 Runtime/Binding metrics
snapshot。

### 影响

应用只能通过事件推断或依赖私有状态，难以建立 Prometheus 指标、容量告警和压测报告。

### 建议

提供只读、可移植快照：

```python
snapshot = runtime.binding_snapshot(binding)
# live, runnable, queued, rejected, model_waiters, tool_waiters,
# queue_wait_ms histogram/counters
```

并为 admission、lease wait、resource wait 定义标准事件。

## P1：ToolCallLayer 支持领域结果映射

### 场景

Lora 工具的 Python 调用成功，但可能返回 `{status: "error", error: ...}` 领域结果。
`LocalToolExecutor` 会把这个对象视为成功的 ToolTask output；若抛 `ToolExecutionError`，又难以保留
应用内部的 trace tool_call_id 和结构化详情。

### 影响

Runtime 的“执行成功”和应用的“工具失败”可能不一致，应用必须在 ToolMessage 后再次转换。

### 建议

允许 executor 返回公开 `ToolResult`，或给 ToolCallLayer 增加 `result_adapter`，把领域 envelope
映射为 succeeded/failed/rejected，同时保留 output、error details 和应用 correlation metadata。

## P2：为动态 Agent graph 提供 plan cache 与请求资源注入

### 场景

工具集合、workspace executor 和 trace sink 按 turn 构建，但 Model/Tool 声明结构经常相同。
当前安全做法是每个 turn 构建 Module graph 并 bind，Runtime 重新编译相同 plan。

### 影响

虽然实测开销很小，但高 QPS 服务会重复 freeze/compile；为了复用 graph 而把请求对象塞进共享
live resource 又会引入并发安全问题。

### 建议

分离 `CompiledModuleDefinition` 与 request-scoped resource binding：相同 graph hash 复用 plan，
每次 execution 显式注入符合声明的资源槽位，并在启动时校验资源类型/能力。

## P2：同步发布 PyPI 版本与 Git tag

Lora 升级时 Git 已有 `v0.2.2`，但 PyPI 尚无法解析 `pygent-ai==0.2.2`，只能在 uv sources 中锁定
Git tag/commit。建议 release pipeline 在 tag 后自动发布并验证 PyPI artifact，使版本声明与锁文件
不需要双重来源。

## 已验证且值得保留的设计

- `Module` child lineage、ExecutionHandle、Binding 容量和 Runtime shutdown 的职责划分清晰。
- ToolDefinition、ToolSpec、executor registry 的三层分离适合服务部署。
- Tool authorization fail-closed、严格 JSON 和 definition freeze 很好地暴露了隐式可变状态。
- `ToolCallLayer` 的有序结果与结构化并行执行能替代大量应用自建 task/semaphore 代码。
