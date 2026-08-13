# Pygent execution-scoped context extension feedback

> 状态更新（Pygent 0.2.12）：Pygent 已提供受约束、可注册 codec 的 `Context`
> 子类支持。Lora 已采用该能力统一 portable 模型投影与完整会话历史，因此本文描述的
> `attach_runtime_context()` workaround 已被移除。`SessionManager` 等 live resource 仍不应
> 进入 Context；相关 execution-scoped resource 建议依然适用于需要复用 graph 且不能在
> 构造期注入的 live dependency。

## 当前适配结果

Lora 现在直接使用 `LoraContext(Context)` 承载 `session_id`、case/run/turn
identity、完整历史、模型投影状态和待提交文件副作用。`RuntimeService` 只在执行入口
构造该 context；同一个 `LoraAgent` Module graph 可以跨 turn、跨 run 复用。

原来的 `_LoraRunServices`、Agent 上的 run-bound 引用和 observer 隐藏队列已经删除。
`EventStore`、prompt context view、`DiffTool` 等对象在使用点根据 portable identity
重建。数据库连接、锁、客户端等 live resource 仍不进入 codec；未来若出现不可重建的
请求级 live resource，本文建议的正式 execution-scoped resource API 仍然适用。

本文记录 Lora 集成 Pygent 0.2.11 时遇到的上下文扩展问题，并提出一个兼顾
portable context、持久化恢复和下游框架开发体验的改进方向。

## 摘要

Pygent 当前的 `Context` 是不可变、可移植的模型请求快照，并明确禁止继承：

```python
@dataclass(frozen=True, slots=True)
class Context:
    system_prompt: str = ""
    messages: tuple[Message, ...] = ()
    tools: tuple[ToolDefinition, ...] = ()
    metadata: JsonObjectInput = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError(
            "Context cannot be subclassed; put portable request facts in metadata"
        )
```

我们认同以下约束：

- 模型上下文应保持 immutable；
- 可恢复执行所需的数据应可序列化；
- 下游不能通过子类把锁、连接、manager 等 process-local 对象悄悄带入持久化状态；
- `metadata` 应只保存 portable request facts。

问题不在于 Pygent 拒绝任意继承，而在于 `Context` 之外没有正式的
execution-scoped 扩展通道。需要 session、持久化服务或领域状态的下游 Module，
只能把 live state 挂在 Module graph、Agent 或外部映射上。

我们希望 Pygent 保持 `Context` 的现有边界，同时提供类型安全、生命周期明确的
execution-scoped resource API。

## Lora 实际需要管理的两类上下文

Lora 同时存在两类生命周期和语义不同的状态。

### 1. 模型请求上下文

Pygent `Context` 表示当前模型调用可见的快照：

- system prompt；
- 当前有效 messages；
- 当前可见 tools；
- portable metadata。

它会在 pipeline 中通过 `replace()` 生成新值，也可能在上下文压缩后只包含摘要和
近期消息。它适合作为模型输入和可恢复执行数据，不应承担应用服务容器的职责。

### 2. Lora 执行状态

Lora 还需要一个覆盖整个 turn 的可变执行状态：

- `AgentSession` 和完整、未投影的持久化 history；
- session status，例如 `compressing`、`compacted`、`compression_failed`；
- `SessionManager`，用于结束时保存 session；
- 当前 case run、turn 和 trace 归属；
- prompt projection 和 compression 所需的 history cutoff；
- 本次运行是否已经切换到 compacted model context；
- 只在当前进程有效的 observer、store 或其他领域服务。

这些对象不全是 portable 的，也不应被放入 `Context.metadata`。但 prompt、compression、
tool audit 等 Module 在执行过程中必须访问它们。

## 当前 Lora workaround

Lora 目前为每个 turn 创建一个新 `LoraAgent`，组装 Module graph，然后在启动执行前
补挂运行状态：

```python
runtime_context = LoraContext(...)
agent.attach_runtime_context(runtime_context, manager)

handle = await bound.start(turn_message, pygent_context, execution=options)
```

`attach_runtime_context()` 实际把对象写入整个 graph 共享的 services：

```python
class _LoraRunServices:
    def __init__(self, *, agent, context_manager, observer):
        self.agent = agent
        self.context_manager = context_manager
        self.observer = observer
        self.runtime_context: LoraContext | None = None
        self.session_manager: SessionManager | None = None
        self.model_context_compacted = False
```

内层 Module 随后从共享 services 获取状态：

```python
class DynamicPromptModule(Module):
    async def forward(self, message, context):
        runtime_context = self.services.runtime_context
        prompt = self.services.context_manager.build_model_request_prompt(
            runtime_context=runtime_context,
            turn_id=self.services.agent.turn_id,
            tool_names=list(self.services.agent.tool_names),
        )
        return message, replace(context, system_prompt=prompt.text)
```

这套方案能工作，是因为 Lora 当前严格保证“一次 turn 创建一个 Agent 实例”。它不是
可复用 Agent graph 上安全的通用方案。

## 给下游造成的不便

### 1. 初始化协议是隐式且有顺序的

合法调用顺序变成：

```text
new agent -> assemble graph -> attach runtime context -> bind -> start
```

类型系统无法表达 `attach_runtime_context()` 是必需步骤。漏调时只能在 Module 已开始
执行后抛出运行时错误。

### 2. execution state 被错误地绑定到 Agent graph

这些状态属于一次 execution，而不是 Agent 定义。如果 Agent 或 compiled graph 被复用，
并发 execution 可能覆盖同一个 `runtime_context`，导致 session history、压缩状态或持久化
目标串线。

为了避免该风险，Lora 必须每 turn 重建 Agent graph，削弱了 graph/plan 复用价值。

### 3. Module API 看不到真实依赖

Module 的签名只显示：

```python
async def forward(self, message, context): ...
```

但它实际还依赖 Lora session、manager 和 observer。这些依赖藏在闭包或共享 services
中，使 Module 难以独立测试、复用和审查。

### 4. 下游出现两个容易混淆的 context

Lora 开发者需要同时区分：

```python
context.messages                 # 当前模型可见视图
runtime_context.history          # 完整持久化历史
context.system_prompt            # 当前不可变请求值
runtime_context.system_prompt    # Lora session 状态
```

两者不能合并，但框架没有提供正式的组合或访问方式，导致每个下游自行设计 facade 和
同步规则。

### 5. `metadata` 不能解决 live resource 问题

`metadata` 很适合保存 `session_id`、`case_run_id`、`turn_id` 等 JSON facts，但不适合保存：

- `SessionManager`；
- database connection 或 transaction；
- event observer；
- lock、semaphore；
- provider client；
- mutable domain aggregate。

把这些对象编码成 ID 再维护进程级全局映射，只是把依赖和生命周期转移到了另一个
隐式位置，并使 cleanup、retry 和 recovery 更复杂。

## 我们不建议直接开放任意 Context 继承

最直观的下游诉求可能是：

```python
class LoraContext(Context):
    session: AgentSession
    manager: SessionManager
```

但这会模糊 portable state 和 live resource 的边界，并产生以下协议问题：

- codec 如何记录和恢复任意子类；
- Pygent 内部构造新 `Context` 时是否保留子类；
- `replace()`、compaction 和 child module 调用是否保持扩展字段；
- worker 如何导入第三方 context 类型；
- 无法序列化的字段在 retry/resume 时采用什么语义。

因此，禁止任意继承是可以接受的。真正缺失的是独立于 portable `Context` 的执行资源
机制。

## 推荐方案：ExecutionScope 提供类型化 resource registry

建议让每次 root execution 拥有一个明确的 `ExecutionScope`，并让 Module 在执行时访问
它。`Context` 继续保持现有不可变、可序列化的定义。

概念 API：

```python
class ExecutionResources:
    def require[T](self, resource_type: type[T]) -> T: ...
    def get[T](self, resource_type: type[T]) -> T | None: ...


class ExecutionScope:
    execution_id: str
    request_id: str
    identity: str | None
    deadline: float | None
    resources: ExecutionResources
```

Module 可以显式接收 scope：

```python
class Module:
    async def forward(
        self,
        message: Message,
        context: Context,
        execution: ExecutionScope,
    ) -> tuple[Message, Context]: ...
```

Lora 的使用方式：

```python
scope = LoraExecutionScope(session=session, session_manager=manager)

handle = await bound.start(
    message,
    context,
    execution=ExecutionOptions(
        request_id=case_run_id,
        resources={LoraExecutionScope: scope},
    ),
)
```

Module 中的依赖变为：

```python
async def forward(self, message, context, execution):
    lora = execution.resources.require(LoraExecutionScope)
    prompt = self.prompt_service.build(
        session=lora.session,
        model_context=context,
    )
    return message, replace(context, system_prompt=prompt.text)
```

这里的 `LoraExecutionScope` 只是 process-local resource，不进入 execution journal 或
portable codec。需要恢复的事实仍通过 `Context.metadata`、`ExecutionOptions` 或应用自己的
持久化层保存。

## 生命周期和恢复语义

resource API 需要明确以下规则。

### 资源作用域

- 资源属于单次 root execution；
- child Module 默认继承同一个 scope；
- detached child/job 必须显式声明继承、复制或重新解析哪些资源；
- execution 结束后 runtime 释放对资源的引用；
- 同一 compiled graph 的并发 execution 必须获得不同的 resource set。

### 持久化边界

- resource value 默认不序列化；
- journal 只记录显式选择的 resource identity/facts，不记录 live object；
- runtime restart 后，应用通过 resolver 根据 portable identity 重建资源；
- 缺少必需 resolver/resource 时，在 execution 启动或恢复阶段 fail fast，而不是运行到
  深层 Module 才失败。

### 声明与校验

Module 最好能声明必需资源：

```python
class DynamicPromptModule(Module):
    execution_requirements = ExecutionRequirements(
        resources=(ResourceRequirement(LoraExecutionScope),)
    )
```

Binding/start 可以在执行前验证资源是否存在，从而替代 Lora 当前的 Optional 字段和
运行时守卫。

## 兼容性较低成本的备选方案

如果暂时不能修改所有 `Module.forward()` 签名，可以先在现有 execution runtime 中提供
受控访问器：

```python
class Module:
    def execution_resource[T](self, resource_type: type[T]) -> T: ...
```

或者在 bind/start 阶段注入只读 handle：

```python
class BoundExecution:
    resources: ExecutionResources
```

关键约束是：资源必须绑定到 execution，而不是写入可复用的 Module/Agent 定义对象。
实现可以内部使用 `ContextVar` 传播当前 execution handle，但不应要求下游应用直接管理
全局 `ContextVar`，也不应让资源的正确性依赖 task-local 隐式设置。

## 面向 Lora 开发者的组合 facade

即使 Pygent 提供 execution resources，Lora 仍可能为自己的 Module 提供统一 facade：

```python
@dataclass(frozen=True)
class LoraContext:
    model: pygent.Context
    execution: LoraExecutionScope
```

该 facade 可以改善领域开发体验，但它应是 Lora 层的组合视图，而不是要求 Pygent
序列化的 `Context` 子类。Pygent 提供正式 execution scope 后，适配器无需再通过
`agent.attach_runtime_context()` 或共享 mutable services 获取执行状态。

## 非目标

本建议不要求：

- 允许在 `Context.metadata` 放任意 Python 对象；
- 序列化数据库连接、锁、manager 或 provider client；
- 自动恢复所有第三方 live resource；
- 将完整应用 session history 变成模型可见 messages；
- 取消 `Context` 的 immutable、slots 或 portable codec 约束；
- 强制 Pygent 理解 Lora 的 `AgentSession`。

## 建议验收用例

1. 同一个 bound/compiled Agent graph 同时启动两个 execution，各自读取到自己的 typed
   resource，状态不串线。
2. child Module 可以访问父 execution 的继承资源。
3. detached job 没有声明资源策略时不能意外继承 process-local resource。
4. 缺少 Module 声明的必需资源时，`start()` 在执行前给出明确错误。
5. `Context` 的 codec、freeze、`replace()` 和恢复行为保持不变。
6. live resource 不出现在 context serialization 或 execution journal 中。
7. execution 成功、失败、取消和超时后都会释放 runtime 对 resource 的引用。
8. 恢复执行可以通过 portable resource identity 和应用 resolver 重建所需资源。
9. 两个并发 execution 可以共享显式声明为 workspace-scoped 的只读服务，同时隔离
   turn-scoped mutable state。

## 期望结果

如果 Pygent 提供上述能力，Lora 可以：

- 删除 `LoraAgent.attach_runtime_context()`；
- 删除 `_LoraRunServices.runtime_context: Optional` 和延迟补挂协议；
- 在 execution 启动时一次性校验 session scope；
- 安全复用 Agent graph，而不是依赖“每 turn 新建 Agent”保证隔离；
- 让 Module 的依赖更容易测试和审查；
- 清晰保留 `Pygent Context = portable model snapshot` 与
  `Lora execution scope = live domain state` 的边界。

一句话概括我们的请求：

> 请继续保持 `Context` 封闭和 portable，但为下游框架提供正式、类型安全、可声明、
> execution-scoped 的 live resource 注入与访问机制。
