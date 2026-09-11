# Pygent 0.3.9 Runtime 与持久化资源问题报告

> 报告日期：2026-09-08
> 适用版本：Pygent 0.3.9，提交 `9aeb637ae345f0bad8ab3a0fb9416c2ea91ae84b`
> 证据等级：本地 mock 服务的合成集成测试，不等同于真实模型供应商压测

> **后续更新：** benchmark 已补充显式工具授权和 executor step 计数，并用相同正确负载对 Pygent 0.3.9 与 0.3.10 各重跑 200/1,000 并发三轮。0.3.10 在 1,000 并发 preferred 下相对 0.3.9 的 wall/P95 改善 6.2%、CPU 改善 10.1%，但物理写入仍约 719.6 MiB，写放大问题没有实质消失。完整数据见 [Agent framework resource benchmark](./agent-framework-resource-benchmark.md#pygent-0310-corrected-ab)。本报告以下 0.3.9 数据保留为“工具被拒绝”路径的历史诊断，不再作为成功工具执行排名。

## 结论摘要

Pygent 0.3.9 的直接调用和无持久化 Runtime 路径没有发现异常磁盘开销；当前最明确的问题集中在 `SQLiteHistoryStore + DurabilityMode.PREFERRED` 的高并发写入成本。

在一次 1,000 Agent 同时提交、每个 Agent 进行 4 次模型往返和 3 次工具调用尝试的测试中，Runtime preferred 路径产生了 **604.42 MiB 进程物理写入**。最终 SQLite 文件为 **64.65 MiB**，主要逻辑 JSON 文本约 **47.31 MiB**：

- 物理写入 / 最终数据库大小：**9.35 倍**；
- 物理写入 / 主要逻辑文本：**12.77 倍**；
- 每个执行写入 **64 条事件**，1,000 个执行共 **64,000 条事件**；
- 相对同版本 direct 路径的三次测试中位数，preferred 的 wall time 为 **3.06 倍**，CPU time 为 **2.34 倍**；
- 从 200 并发扩大到 1,000 并发时，请求量增加 5 倍，preferred 物理写入从 79.586 MiB 增至 605.848 MiB，即 **7.61 倍**。

这说明高并发 durable journal 存在明显的写放大和事务成本，优先建议从事件粒度、重复载荷、SQLite group commit、WAL/checkpoint 策略和可观测指标五方面优化。

同时，本次复核发现测试适配器漏配了 `ToolCallLayer.authorization`。Pygent 按设计 fail-closed，3,000 次工具调用全部产生 `tool.rejected(reason_code="authorization_not_configured")`，并未真正执行工具。因此：

1. **604.42 MiB 写入是有效的 Pygent durable rejected-tool 流程数据**，足以证明写放大问题；
2. 原跨框架排名不能再称为“等价工具执行对比”，Pygent 排名应暂停使用；
3. 真正成功执行工具会增加授权、执行和 effect 事件，当前写入值很可能是目标工作负载的下界，准确数值需修复适配器后重测；
4. 漏配授权属于 Lora benchmark 适配器问题，不是 Pygent 的执行错误；Pygent 可改善配置校验与开发体验，帮助应用更早发现这种误用。

## 测试环境与方法

| 项目 | 值 |
|---|---|
| 操作系统 | Windows 11 `10.0.26200` |
| CPU | Intel Core i7-1360P，12 核 / 16 逻辑处理器 |
| 内存 | 16 GiB |
| Python | 3.13.11 |
| SQLite | 3.51.0 |
| Pygent | 0.3.9 / `9aeb637a` |
| 模型端点 | 本地 OpenAI-compatible mock，非真实供应商 |
| 工作负载 | 1,000 请求、并发 1,000；每请求 4 次模型往返、3 次工具调用尝试 |
| Runtime | `LocalRuntime(history=SQLiteHistoryStore(...))` |
| Durability | `DurabilityPolicy(DurabilityMode.PREFERRED)` |
| 重复性 | 正式对比三次独立进程；另做一次保留 SQLite 文件的诊断运行 |

诊断运行结果：

| 指标 | 值 |
|---|---:|
| 完成结果 | 1,000 / 1,000（仅代表最终文本通过） |
| Runtime 执行时间 | 18.269 s |
| 进程 wall time | 19.354 s |
| CPU time | 19.063 s |
| 峰值 RSS | 200.91 MiB |
| 物理读取 | 331.05 MiB |
| 物理写入 | 604.42 MiB |
| 峰值线程 | 23 |

正式三次独立进程测试的中位数：

| 模式 | Run time | CPU time | 峰值 RSS | 物理写入 |
|---|---:|---:|---:|---:|
| Pygent direct | 7.867 s | 10.062 s | 200.3 MiB | 0.139 MiB |
| Runtime disabled | 11.016 s | 13.844 s | 223.9 MiB | 0.140 MiB |
| Runtime preferred | 24.037 s | 23.594 s | 201.0 MiB | 605.848 MiB |

direct 与 Runtime 并非完全相同的控制路径，这组比值用于度量 Runtime 管理成本，不应解释为单一函数的纯开销。

## 问题清单

| ID | 优先级 | 结论 | 归属 |
|---|---|---|---|
| PYG-RUNTIME-001 | P1 | preferred durability 在高并发下物理写入放大明显 | Pygent |
| PYG-RUNTIME-002 | P1 | 事件数量多，完整模型请求和工具 schema 被重复持久化 | Pygent |
| PYG-RUNTIME-003 | P1 | 单 SQLite writer、多个状态/effect 事务与 WAL FULL 组合造成事务压力 | Pygent，部分为源码推断，需 trace 验证占比 |
| PYG-RUNTIME-004 | P2 | 缺少面向 durability 成本的一等指标，应用难以定位写放大 | Pygent |
| PYG-RUNTIME-005 | P2 | 工具有定义但未配置授权时缺少 admission 期诊断 | Pygent 开发体验建议，不是执行正确性缺陷 |
| BENCH-001 | P0 | benchmark 将被拒绝的工具调用误判为成功工具流程 | Lora benchmark，非 Pygent |

## PYG-RUNTIME-001：SQLite durable history 写放大

### 证据

保留的数据库大小为 67,784,704 bytes（64.65 MiB）。按字段长度统计的主要逻辑文本如下：

| 数据 | 行数 | 文本体积 |
|---|---:|---:|
| `events.event_json` | 64,000 | 43,329,340 bytes |
| execution 输入、输出和 model call JSON | 1,000 | 3,886,780 bytes |
| effect 请求、spec 和结果 | 4,000 | 2,008,000 bytes |
| input receive 请求和 batch | 5,000 | 389,000 bytes |
| 合计 | — | 49,613,120 bytes（47.31 MiB） |

相同进程的物理写入为 604.42 MiB。该指标包含 SQLite 主库、WAL、checkpoint 和页级重复写入等进程 I/O，不等价于数据库净增长；但 12.77 倍于主要逻辑文本、9.35 倍于最终数据库，已经高到会影响 SSD 写入、CPU 和高并发吞吐。

### 源码链路

- `src/pygent/runtime/_history_store.py:66`：事件 batch 默认上限为 64；
- `src/pygent/runtime/_history_store.py:109`：启用 WAL；
- `src/pygent/runtime/_history_store.py:383`：单事件 writer 刷新队列；
- `src/pygent/runtime/_history_store.py:442`：每批次使用 `BEGIN IMMEDIATE`；
- `src/pygent/runtime/_history_store.py:445`：以 `executemany` 写入事件；
- `src/pygent/runtime/_history_store.py:544`：其他事务队列也单独开启 `BEGIN IMMEDIATE`。

保留数据库实测 PRAGMA 为 `journal_mode=wal`、`synchronous=FULL`、`wal_autocheckpoint=1000`、`page_size=4096`。

### 建议

1. 使用自适应 group commit：并发升高时扩大 batch，按最大等待时间与最大事件数共同触发，不只依赖固定 64 条。
2. 合并 execution 生命周期、input receive、effect begin/complete 和 terminal projection 中可以原子提交的写操作，减少事务次数。
3. 对 `PREFERRED` 提供显式、文档化的 SQLite 策略选项，例如可选择 `synchronous=NORMAL`、延迟 checkpoint 或固定恢复窗口；默认语义不能被静默弱化。
4. 评估 append-friendly 的全局递增主键或 rowid 布局，并保留 `(execution_id, event_index)` 唯一约束，减少大量随机 execution ID 交错写入导致的 B-tree 页扰动。
5. 用 SQLite trace/profile、WAL 文件峰值和 checkpoint 计时进一步拆分：事件写、状态投影、effect、fsync、checkpoint 各占多少。

## PYG-RUNTIME-002：事件粒度与重复载荷偏大

该简单执行产生 64 条事件，分布如下：

| 事件 | 每次执行 | 总数 |
|---|---:|---:|
| `span.started` / `span.completed` | 8 / 8 | 8,000 / 8,000 |
| 每类 model lifecycle 事件 | 4 | 每类 4,000 |
| 每类 tool-call stream 事件 | 3 | 每类 3,000 |
| `tool.requested` / `tool.rejected` | 3 / 3 | 3,000 / 3,000 |
| execution lifecycle 事件 | 4 | 每类 1,000 |
| `model.text.delta` | 1 | 1,000 |

其中 `model.request.prepared` 每次模型调用都保存完整请求，包含 messages、generation 参数和 tools schema。本次很小的 mock 用例中，单条 event JSON 已达到 3,190 bytes；真实 Agent 的系统提示、历史消息和工具 schema 更大，重复持久化成本会更明显。

建议将数据分为三个层次：

- 恢复和 fencing 必需的 durable facts；
- 客户端断线重放需要的业务事件；
- 调试/trace 观测事件。

允许 Binding 显式选择 event persistence policy。静态工具 schema、模型 profile 和重复 prompt 前缀可用 digest/reference 去重；终态后可在不破坏 replay cursor 契约的前提下进行压缩或投影归档。

## PYG-RUNTIME-003：串行写入与事务乘法

Pygent 已把事件批处理到单 writer，这有利于正确性和避免 SQLite 多 writer 冲突。但当前执行还会分别写 execution 状态、事件、effect begin/complete、input receive、fence 和 terminal 数据。高并发下这些路径最终竞争同一个 `_write_lock`，配合 `synchronous=FULL` 和自动 checkpoint，可能形成事务排队及重复页写。

这是基于源码和 I/O 结果的高可信推断，尚未通过 SQLite trace 精确分摊各事务的耗时与写入占比。建议不要只扩大 event batch；应同时测量并减少每个执行的 commit 数。

## PYG-RUNTIME-004：缺少 durability 成本指标

建议 Runtime 原生暴露下列指标，否则应用只能看到总 CPU/I/O，无法判断是模型、事件序列化、writer 排队还是 checkpoint：

- `events_persisted_total`、`event_payload_bytes_total`、events/execution；
- batch size 分布、batch fill ratio、writer queue depth 和 queue wait；
- SQLite transaction count、commit latency、busy time；
- WAL bytes、checkpoint bytes/time/count、fsync time（平台允许时）；
- effect、input、execution projection 各自的写入行数和字节数；
- terminal result 返回前等待 durability commit 的时间。

这些指标应能按 Binding、durability mode 和 execution 汇总，但避免默认添加高基数 execution ID 标签。

## PYG-RUNTIME-005：授权误配置应更早暴露

`ToolCallLayer` 在未设置 `authorization` 和 `authorization_adapter` 时返回 `authorization_not_configured`，这是正确的 fail-closed 行为。本次错误在应用层：模型收到一个 tool-role 结果后继续生成最终文本，benchmark 又只校验了最终文本，导致整个执行表面上“成功”。

建议增加至少一种开发期保护：

- Binding/admission 时发现 Agent 暴露工具但没有授权器，发出明确 warning，严格模式可直接拒绝 admission；
- 提供语义明确的内置授权器，例如 `DenyAllAuthorization` 和只允许 `PURE + IDEMPOTENT` 的 `AllowPureToolsAuthorization`；
- 在 execution outcome 或 summary 中提供 rejected tool count，便于调用方设置“只要预期工具被拒绝就失败”的策略；
- 文档示例强调“注册工具不等于授权工具”。

这项建议的目标是减少错误集成，不是改变默认安全策略，也不建议默认允许工具。

## 已确认不是当前问题的内容

### 0.3.9 已消除旧版 cancellation 轮询热点

此前 0.3.8 的 preferred profile 中观察到每 execution 约 50 ms 的 SQLite cancellation polling；0.3.9 已改为通知式等待。相同 200 并发测试中，cancellation query 从 94,844 次降为 0，preferred wall time、P95 和 CPU 分别约下降 60.6%、61.2% 和 59.2%。因此报告不再把 cancellation polling 列为 0.3.9 缺陷。

### 工具拒绝不是 Pygent bug

`src/pygent/tool/layer.py:299` 明确返回 `authorization_not_configured`。Pygent 没有绕过授权、也没有执行未授权副作用。应修复的是 benchmark 的授权配置和断言。

## 建议验收目标

在保持现有 replay、idempotency、effect fencing、取消、deadline 和 fail-closed 语义不回退的前提下，建议用同一机器、同一 mock、修正后的真实工具执行 workload 验收：

1. 1,000 并发，连续 3 个新进程运行，全部 1,000/1,000 完成，且每次准确成功执行 3 个工具；
2. 相对 0.3.9 基线，preferred 物理写入至少降低 **50%**；
3. preferred 的 wall time 和 CPU time 至少降低 **30%**；
4. 请求量从 200 增长到 1,000 时，物理写入增长不超过 **6 倍**，接近线性；
5. 提供 logical payload bytes、transaction count、WAL/checkpoint bytes，使物理写入放大可直接解释；
6. 测试进程崩溃、重启回放、重复 idempotency key、effect outcome unknown、取消和 shutdown，确保性能优化没有削弱 durable contract。

这些是问题修复目标，不是通用硬性 SLA；最终阈值应由 Pygent 团队根据 REQUIRED/PREFERRED 的公开语义分别确定。

## 修复 benchmark 后的重测要求

Lora benchmark 需要先完成以下修正，再恢复跨框架排名：

1. 给 Pygent `ToolCallLayer` 配置只允许本测试纯函数工具的显式授权器；
2. 工具 executor 增加独立成功计数，断言每次请求恰好执行 3 次；
3. 解析 `ToolResult`，任何 `rejected`、`failed` 或缺失都使请求失败，不能只检查最终模型文本；
4. direct、runtime-disabled、runtime-preferred 使用同一个工具与授权语义；
5. 先跑 200 并发，再跑 1,000 并发，每项至少 3 个新进程；
6. 同时保留 SQLite 主库、WAL 峰值、事件统计和进程 I/O，避免只看最终 DB 文件。

在此之前，现有 Pygent 行可用于分析“当前错误配置下 Runtime 的资源成本”，不能用于发布最终框架排名。

## 复现入口与相关文档

- Pygent 仓库：[pygent-ai/pygent](https://github.com/pygent-ai/pygent)
- Execution contract：[docs/EXECUTION.md](https://github.com/pygent-ai/pygent/blob/main/docs/EXECUTION.md)
- Runtime SDK：[docs/runtime/SDK.md](https://github.com/pygent-ai/pygent/blob/main/docs/runtime/SDK.md)
- Lora benchmark 说明：[agent-framework-resource-benchmark.md](./agent-framework-resource-benchmark.md)
- benchmark 适配器：`.tmp/framework-bench/python_adapter.py`
- 诊断结果：`.tmp/framework-bench/results/preferred-1000-write-probe-20260908/run-1/result.json`
- 保留数据库：`.tmp/framework-bench/probes/preferred-1000-20260908-1/executions.sqlite3`

核心复现配置如下：

```python
history = SQLiteHistoryStore(history_dir / "executions.sqlite3")
runtime = LocalRuntime(history=history)
binding = runtime.create_binding(
    # 省略与问题无关的容量参数
    durability=DurabilityPolicy(DurabilityMode.PREFERRED),
)
bound = runtime.bind(agent, binding=binding)
handle = await bound.start(request, options=options)
answer, context = await handle.result()
```

建议上游定位时为 SQLite 增加 trace/profile，并分别记录 events、execution projection、effects、input receive、WAL checkpoint 的事务数和耗时。

## 证据限制

- 这是本地 mock 合成集成测试，不代表真实模型网络延迟、token 生成或供应商限流下的端到端体验；
- 诊断运行只做了一次，正式性能表的中位数来自三次独立进程；
- Windows 进程物理 I/O 包含数据库、WAL 和 checkpoint 等所有写入，不能直接当作数据库净增长；
- 工具授权漏配使“成功工具执行”条件不成立，跨框架排名和功能等价性结论已失效；
- 串行事务对 604.42 MiB 的具体贡献尚未由 SQLite trace 分解，因此 PYG-RUNTIME-003 的机制占比仍需验证；
- 本报告没有使用真实 API key，日志和文档中不包含密钥。

## 推荐提交给 Pygent 的 Issue

可以拆成三个上游 Issue：

1. **SQLiteHistoryStore shows 9.35x physical-write amplification at 1,000 concurrent preferred executions**
2. **Durable journal repeats full model requests/tool schemas and emits 64 events for a small execution**
3. **Warn at admission when ToolCallLayer exposes tools without an authorization provider**

前两个是资源与持久化设计问题；第三个是防误用的开发体验改进。`BENCH-001` 应在 Lora 仓库内部修复，不应作为 Pygent bug 提交。
