# 永续会话 Agent 第三原则：开发契约

> 本文固定永续会话特性的开发契约。该能力必须以通用 Harness 挂载到存量 Agent，不依赖宿主 Agent 的框架、模型、进程形态或内部实现。

## 总体权限边界

- **前台 Agent** 只负责使用记忆完成当前任务。
- **后台 Agent** 只负责提出记忆内容和历史承接范围。
- **Harness** 负责记录、触发、调度、校验、提交、注入和历史替换。

后台 Agent 的输出始终是非可信候选数据，不能直接修改正式记忆、Working Memory、前台上下文或历史覆盖边界。历史替换权唯一归 Harness 所有。

# 第一部分：前台 Agent

## 前台边界

宿主前台 Agent 不负责长期记忆的提取、整理、精炼、提交或历史替换，只需要使用 Harness 提供的记忆完成当前任务。

## 前台上下文组成

前台 Agent 的模型可见上下文依次由四部分组成：

```text
1. System Prompt
2. Memory Access Instruction
3. Memory Snapshot
4. Working Memory
```

形式化表示为：

\[
C_t
=
P_{system}
\oplus
I_{memory\text{-}access}
\oplus
S_{memory}^{(v)}
\oplus
W_{>v}
\]

其中：

- \(P_{system}\) 是宿主 Agent 的 System Prompt；
- \(I_{memory\text{-}access}\) 是固定的记忆访问指导；
- \(S_{memory}^{(v)}\) 是 Harness 控制的动态 Memory Snapshot；
- \(W_{>v}\) 是最近一次成功历史替换边界之后持续增长的 Working Memory。

四部分必须保持独立的来源、生命周期和控制权，不能合并成一个含义不清的 Memory Prompt。

### 1. System Prompt

System Prompt 由宿主 Agent 所有，包含宿主原有的身份、能力、任务规则和安全约束。

Harness 必须保留 System Prompt 的原始语义。永续会话能力通过独立的记忆上下文挂载，不得借记忆注入覆盖或悄然改写宿主的系统规则。

### 2. Memory Access Instruction

Memory Access Instruction 是固定的前台记忆使用指导，由 Harness 提供并随永续会话功能版本管理。

它只描述前台 Agent 如何使用记忆，包括：

- 当前 Memory Snapshot 不足时应查询历史记忆；
- 涉及过去的对话、决定、偏好、承诺、任务过程或历史细节时应查询记忆；
- 使用稳定接口搜索、查看和展开长期记忆；
- 通过 Harness 提供的位置或接口访问详细原始历史；
- 无法从已有记忆确认历史事实时不得凭空补全，应继续查询证据。

Memory Access Instruction 不包含具体记忆内容，也不描述后台如何提取、整理、存储或替换记忆。

```xml
<memory-access-instruction>
  当当前 Memory Snapshot 不足以支持任务，或任务涉及过去的对话、
  决定、偏好、承诺、执行过程和历史细节时，查询长期记忆。

  使用已挂载的记忆访问接口搜索和展开相关记忆。
  详细原始历史可通过 Harness 提供的证据访问位置或接口查看。
</memory-access-instruction>
```

具体接入可以使用 CLI、Tool、Skill、Plugin、API 或 Harness 自动检索，但前台获得的使用语义必须一致。

### 3. Memory Snapshot

Memory Snapshot 是动态的长期记忆投影。其语义内容由后台 Agent 提出，只有经过 Harness 的代码校验并正式提交后才能注入前台 Agent。

Memory Snapshot 向前台 Agent 提供当前最重要且无需主动检索即可使用的连续状态，包括：

- 最近已经发生并完成长期记忆结算的内容；
- Agent 当前正在处理的事项；
- 已经完成的工作和形成的结果；
- 接下来需要继续完成的事项；
- 当前仍然有效的目标、决定、约束和承诺。

```xml
<memory-snapshot revision="..." covered-through="...">
  <recent-context>...</recent-context>
  <current-state>...</current-state>
  <completed>...</completed>
  <next-actions>...</next-actions>
  <constraints>...</constraints>
</memory-snapshot>
```

Snapshot 必须携带可识别的记忆版本和历史覆盖边界。前台 Agent 只消费 Snapshot，不能直接修改或宣称 Snapshot 已经更新。

### 4. Working Memory

Working Memory 是除 System Prompt、Memory Access Instruction 和 Memory Snapshot 之外，自最近一次成功历史替换边界之后持续增长的有序前台执行上下文。

Working Memory 可以包含：

- User Message；
- Assistant Message；
- Tool Call；
- Tool Result；
- 环境反馈；
- 宿主 Agent 暴露的其他上下文事件。

Working Memory 不包含：

- System Prompt；
- Memory Access Instruction；
- Memory Snapshot；
- 宿主模型请求中的工具定义或能力定义。

当前用户消息和当前任务产生的执行记录按真实发生顺序持续追加到 Working Memory。宿主 Agent 只能暴露部分执行轨迹时，最低限度必须记录用户输入和 Agent 输出。

```text
System Prompt
Memory Access Instruction
Memory Snapshot
Working Memory
  ├── 历史 User Message
  ├── 历史 Assistant Message
  ├── 历史 Tool Call
  ├── 历史 Tool Result
  ├── 当前 User Message
  ├── 当前 Assistant Message
  ├── 当前 Tool Call
  └── 当前 Tool Result
```

# 第二部分：Harness

## Harness 边界

Harness 是挂载在用户与宿主前台 Agent 之间的通用会话控制层。它不承担语义性的记忆提取，但拥有正式状态、前台上下文投影和历史替换的唯一控制权。

Harness 必须提供以下八项功能。

## 1. 上下文记录

Harness 必须记录前台 Agent 可观察到的新上下文，并维护完整原始历史与当前 Working Memory。

每个上下文事件必须具有：

- 稳定位置或递增序号；
- 所属会话；
- 消息角色和事件类型；
- 原始发生顺序；
- 工具调用与工具结果的关联关系；
- 可用于审计和重新整理的原始内容。

完整原始历史作为证据永久保留。Working Memory 是其中尚未被已提交 Memory Snapshot 可靠承接的活动区间。

## 2. 记忆整理触发

Harness 负责根据确定性策略决定何时创建后台记忆整理任务。

触发信号可以来自：

- 前台 Agent 一次执行完成；
- Working Memory 达到配置阈值；
- 前台上下文接近可用预算；
- 会话进入空闲状态；
- 用户显式要求整理记忆；
- 已积压的 Working Memory 需要合并处理。

触发只创建整理任务，不直接改变正式记忆或前台上下文。

## 3. 处理范围冻结

Harness 在调度后台 Agent 前，必须冻结本次任务能够读取和建议替换的 Working Memory 范围，以及任务所基于的 Long-term Memory 版本。

```text
memory_job
  conversation_id
  base_memory_revision
  from_cursor
  to_cursor
```

后台运行期间新增的上下文继续进入 Working Memory，不属于本次冻结范围，也不能被本次提案替换。

## 4. 后台 Agent 调度

Harness 负责创建、启动、监控和结束后台记忆任务，包括：

- 向后台 Agent 提供冻结的 Working Memory 范围；
- 提供任务所基于的 Long-term Memory 版本；
- 提供受控的原始证据访问能力；
- 防止同一范围被重复或冲突处理；
- 控制任务并发与执行顺序；
- 处理超时、失败、取消和重试；
- 将基于旧版本产生的提案标记为过期并拒绝直接提交。

调度协议不得要求后台 Agent 与宿主前台 Agent 使用相同框架、模型、进程或部署方式。

## 5. 记忆与替换提案接收

后台 Agent 负责语义判断，并向 Harness 提交候选结果，包括：

- 建议新增、更新、合并或失效的记忆；
- 建议发布的 Memory Snapshot；
- 建议由新记忆承接的 Working Memory 范围；
- 每项候选记忆对应的原始证据。

Harness 不自行总结历史，也不直接执行后台 Agent 给出的命令或写入操作。后台提案始终作为待验证数据处理。

## 6. 合法性校验与替换裁决

Harness 必须通过确定性代码决定哪些提案可以提交，以及哪些 Working Memory 可以实际退出前台上下文。

代码校验至少覆盖：

- **结构校验**：字段、类型、大小和操作种类合法；
- **身份与权限校验**：提案属于正确的用户、会话、任务和记忆命名空间；
- **范围校验**：建议替换范围位于被冻结范围内；
- **版本校验**：提案基于当前有效的 Long-term Memory 版本；
- **证据校验**：引用存在、未被篡改并位于授权历史范围内；
- **覆盖校验**：替换范围连续，不能跳过未承接历史形成空洞；
- **完整性校验**：Memory Snapshot 和记忆变更满足正式发布契约。

Harness 负责代码层面的合法性和一致性裁决；后台 Agent 负责建议记住什么以及建议承接哪段历史。

## 7. 原子提交与替换发布

通过校验的提案必须由 Harness 原子提交。一次成功提交同时完成：

1. 发布新的 Long-term Memory 版本；
2. 发布新的 Memory Snapshot；
3. 保存记忆与原始证据的关联；
4. 推进历史覆盖边界；
5. 使已被可靠承接的 Working Memory 获得退出前台投影的资格。

形式化表示为：

\[
(L_v,W_{>v})
\longrightarrow
(L_{v+1},W_{>v+1})
\]

任何一步失败时：

- 旧 Long-term Memory 和旧 Memory Snapshot 继续有效；
- 历史覆盖边界不推进；
- Working Memory 不退出；
- 前台 Agent 仍可使用上一个有效快照和完整未结算尾部。

记忆替换不由时间单独触发，而由经过验证的新记忆版本成功提交触发。替换不得中途改写正在运行的前台 Agent 上下文，只在后续上下文投影中生效。

## 8. 前台上下文投影

Harness 在每次调用宿主前台 Agent 前生成新的模型可见上下文：

\[
C_t
=
P_{system}
\oplus
I_{memory\text{-}access}
\oplus
S_{memory}^{(v)}
\oplus
W_{>v}
\]

上下文投影必须：

- 保留宿主 System Prompt 的语义；
- 提供固定的 Memory Access Instruction；
- 只读取最新已提交的 Memory Snapshot；
- 不读取后台任务的半成品；
- 只移除历史覆盖边界之前、已被可靠承接的 Working Memory；
- 保留尚未结算以及后台处理期间新增的 Working Memory 尾部；
- 不修改或删除完整原始历史。

替换前：

```text
System Prompt
Memory Access Instruction
Memory Snapshot v
Working Memory: event 101 ... event 180
```

后台只处理 `event 101 ... event 160`，且 Harness 成功提交后：

```text
System Prompt
Memory Access Instruction
Memory Snapshot v+1，覆盖到 event 160
Working Memory: event 161 ... event 180
```

## Harness 状态边界

Harness 至少必须管理：

```text
当前 Long-term Memory revision
当前 Memory Snapshot revision
已结算到的历史 cursor
当前 Working Memory 范围
正在运行的后台任务及其冻结范围
待验证的记忆提案
最后一次成功提交
```

Harness 必须防止同一历史范围被冲突提交、旧提案覆盖新 Snapshot、新消息被旧任务误删，以及未承接历史被跳过。

## Harness 核心约束

> **Harness 负责观察历史、触发整理、冻结范围、调度后台、接收提案、代码校验、原子提交和生成前台上下文投影。后台 Agent 负责语义提取，但未经 Harness 代码校验和成功提交的 Agent 输出不得改变正式状态。**
