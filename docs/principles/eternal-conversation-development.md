# 永续会话 Agent 第三原则：开发契约

> 本文固定永续会话特性的开发契约。该能力必须以通用 Harness 挂载到存量 Agent，不依赖宿主 Agent 的框架、模型、进程形态或内部实现；记忆提取、检索测试与构建以 memory-cli skill 及其稳定契约为核心。

memory-cli 是永续会话的记忆子系统核心，不是宿主 Agent 框架。宿主前台 Agent 只依赖 Harness 暴露的记忆使用能力，不需要采用 memory-cli 的内部数据结构、后台 Agent 实现或运行方式。

## 总体权限边界

- **前台 Agent** 只负责使用记忆完成当前任务。
- **后台 1 号 Agent** 负责提取和维护检索 UT、生成 Memory Snapshot 与历史承接建议，并可以直接写入本次任务隔离的 Pending 缓冲区。
- **后台 2 号 Agent** 负责把已经发布的 Pending UT 构建进正式检索系统，并提交经过 Built-only 验证的构建结果。
- **Harness** 负责记录、触发、调度、校验、原子激活、注入和历史替换。

后台 1 号 Agent 可以直接修改任务隔离的 Pending 缓冲区，但该缓冲区在任务完成前不属于正式状态，也不能被前台检索。后台 Agent 不能直接修改已发布记忆、Working Memory、前台上下文或历史覆盖边界。正式激活权和历史替换权唯一归 Harness 所有。

# 第一部分：前台 Agent

## 前台边界

宿主前台 Agent 不负责长期记忆的提取、整理、精炼、提交或历史替换，只需要使用 Harness 提供的记忆完成当前任务。

## 前台上下文组成

前台 Agent 的有序消息／提示词上下文依次由四部分组成：

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
S_s^{(c)}
\oplus
W_{>c}
\]

其中：

- \(P_{system}\) 是宿主 Agent 的 System Prompt；
- \(I_{memory\text{-}access}\) 是固定的记忆访问指导；
- \(S_s^{(c)}\) 是 revision 为 \(s\)、覆盖到历史 cursor \(c\) 的动态 Memory Snapshot；
- \(W_{>c}\) 是最近一次成功历史替换 cursor \(c\) 之后持续增长的 Working Memory。

四部分必须保持独立的来源、生命周期和控制权，不能合并成一个含义不清的 Memory Prompt。宿主模型调用还可以包含工具定义、能力定义和请求级配置；这些内容属于模型请求信封，不属于上述有序消息／提示词上下文，也不属于 Working Memory，但只要它们实际对模型可见，就属于 Harness 可观察性契约必须原样记录，或者通过受 Harness 永久保存的不可变版本与稳定引用实现精确恢复的模型输入。

### 1. System Prompt

System Prompt 由宿主 Agent 所有，包含宿主原有的身份、能力、任务规则和安全约束。

Harness 必须保留 System Prompt 的原始语义。永续会话能力通过独立的记忆上下文挂载，不得借记忆注入覆盖或悄然改写宿主的系统规则。

### 2. Memory Access Instruction

Memory Access Instruction 是固定的前台记忆使用指导，由 Harness 提供并随永续会话功能版本管理。

它只描述前台 Agent 如何使用记忆，包括：

- 当前 Memory Snapshot 不足时应查询历史记忆；
- 涉及过去的对话、决定、偏好、承诺、任务过程或历史细节时应查询记忆；
- 使用由 `memory-cli` skill 提供的稳定接口搜索、查看和展开经过整理的长期记忆；
- 通过 Harness 提供的 Raw History 位置或只读接口访问其可观察范围内的完整原始历史；
- 前台 Agent 可以绕过 `memory-cli`，使用自身已有的文件读取、文本搜索或其他只读工具直接检索 Raw History；
- 无法从已有记忆确认历史事实时不得凭空补全，应继续查询证据。

Memory Access Instruction 不包含具体记忆内容，也不描述后台如何提取、整理、存储或替换记忆。

```xml
<memory-access-instruction>
  当当前 Memory Snapshot 不足以支持任务，或任务涉及过去的对话、
  决定、偏好、承诺、执行过程和历史细节时，查询长期记忆。

  使用已挂载的记忆访问接口搜索和展开相关记忆。
  Harness 可观察到的完整原始历史由 Harness 永久保存，并通过稳定位置或只读接口提供。
  需要详细证据时，可以使用 Agent 自身已有的读取和搜索工具直接检索 Raw History，
  不要求所有历史访问都经过 memory-cli。

  <raw-history>
    完整历史存放位置：{raw_history_location}
    访问方式：{raw_history_access_method}
  </raw-history>
</memory-access-instruction>
```

Memory Access Instruction 的规则模板保持固定，`raw_history_location` 和 `raw_history_access_method` 由 Harness 在组装上下文时绑定为当前会话的实际值。memory-cli skill 可以通过 CLI、Tool、Plugin、API 或 Harness 适配器向宿主暴露，但前台获得的记忆使用语义和稳定命令契约必须一致。

前台拥有两条互补的历史访问路径：

```text
整理后的长期记忆 ──► memory-cli skill 暴露的稳定接口
可观察范围内的完整原始历史 ──► Agent 自身的读取、搜索或其他只读工具
```

`memory-cli` 用于快速获得经过整理且可验证检索的长期记忆；Raw History 用于查看详细过程、核对证据、恢复未被记忆保留的细节，以及纠正错误或不完整的记忆。

### 3. Memory Snapshot

Memory Snapshot 是动态的有限常驻连续状态，也是生成时刻已经结算历史的状态截面；它不是 Long-term Memory 的单独投影。后台 1 号 Agent 以上一版 Snapshot 与本次冻结的 Working Memory 作为连续历史输入，在同一次历史理解中并行生成新的 UT 变更与 Snapshot 候选。当前已发布 Long-term Memory 和有效 UT 用于 UT 的冲突检查、合并与替代，不作为拼接到 Snapshot 中的第三段历史。Snapshot 的语义内容由后台 1 号 Agent 提出，只有经过 Harness 的代码校验并正式提交后才能注入前台 Agent。

Memory Snapshot 向前台 Agent 提供当前最重要且无需主动检索即可使用的连续状态，包括：

- 对多数未来行为持续重要的身份、关系和稳定偏好；
- 最近已经发生并完成长期记忆结算的内容；
- Agent 当前正在处理的事项；
- 已经完成的工作和形成的结果；
- 接下来需要继续完成的事项；
- 当前仍然有效的目标、决定、约束和承诺。

```xml
  <memory-snapshot revision="..." covered-through="...">
  <resident-memory>...</resident-memory>
  <recent-context>...</recent-context>
  <current-state>...</current-state>
  <completed>...</completed>
  <next-actions>...</next-actions>
  <constraints>...</constraints>
</memory-snapshot>
```

Snapshot 必须携带可识别的记忆版本和历史覆盖边界。前台 Agent 只消费 Snapshot，不能直接修改或宣称 Snapshot 已经更新。

Snapshot 只陈述其 `covered-through` cursor 之前形成的历史状态，不宣称自己比后续上下文更新。当前 Working Memory 中 cursor 更晚的消息、工具结果或状态变化与 Snapshot 冲突时，前台 Agent 必须把 Snapshot 视为较早的历史基线，以较新的 Working Memory 为准；需要核实时继续查询 Long-term Memory 或 Raw History。下一次后台整理再把这些变化归并进新的 Snapshot。

### 4. Working Memory

Working Memory 是除 System Prompt、Memory Access Instruction 和 Memory Snapshot 之外，自最近一次成功历史替换 cursor 之后持续增长的有序前台执行上下文。

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

当前用户消息和当前任务产生的执行记录按真实发生顺序持续追加到 Working Memory。宿主适配器必须满足下述可观察性契约；仅记录用户输入和 Agent 输出、却遗漏已经向模型暴露并可能影响未来行为的工具结果或显式状态，不构成完整的永续会话接入。

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

Harness 必须记录前台 Agent 可观察到的所有新上下文，并维护 Raw History 与当前 Working Memory。

每个上下文事件必须具有：

- 稳定位置或递增序号；
- 所属会话；
- 消息角色和事件类型；
- 原始发生顺序；
- 工具调用与工具结果的关联关系；
- 可用于审计和重新整理的原始内容。

### 宿主适配器的可观察性契约

宿主适配器必须向 Harness 暴露所有可能影响前台 Agent 未来行为的可观察事件，至少包括：

- 每次模型调用实际可见的输入上下文，或者能够精确恢复该上下文、且其完整原文由 Harness 永久保存的不可变版本与稳定引用；
- 模型输出；
- Tool Call、Tool Result 与环境反馈；
- 宿主显式维护并会影响后续行为的任务状态；
- 上下文投影、替换和任务边界事件。

凡是已经进入前台模型可见输入或由宿主明确暴露给 Harness 的行为相关内容，都属于可观察历史，必须由 Harness 原样记录并持久化。模型内部推理和模型供应商没有暴露的隐藏状态不属于永续会话的保证范围。宿主无法暴露某类行为相关事件时，适配器必须明确声明可观察性缺口；该缺口中的信息不属于 \(H_t\) 和 \(E_t\) 的完整性保证，不能被默认为已经受到记忆保护。

稳定版本与引用只是原文的存储寻址方式，不能替代原文持久化。只有当引用指向的完整原文不可变、处于 Harness 的永久保存范围内，并且能够精确恢复当次模型实际可见输入时，才等价于原样记录；否则 Harness 必须直接保存完整原文。

### Raw History Store

Harness 必须将自身可观察到的全部原始历史写入追加式 Raw History Store，包括 User Message、Assistant Message、Tool Call、Tool Result、环境反馈和宿主暴露的其他执行事件。

Raw History Store 必须：

- 永久保留 Harness 可观察到的完整原始内容，不因记忆整理和历史替换而删除；
- 保持事件顺序、角色、类型和调用关联；
- 提供稳定的位置、文件布局或只读访问接口；
- 支持按会话、事件范围、关键词和内容进行读取或搜索；
- 允许前台 Agent 使用自身已有工具直接访问；
- 允许后台 Agent 在 Harness 授权的冻结范围内访问；
- 作为记忆、Snapshot、替换提案和纠错操作的最终证据来源。

Raw History Store 对应第一原则中的独立按需证据层 \(E_t\)。它不属于 Long-term Memory，也不通过 `memory-cli` 才能访问；它是独立、可持续增长且默认不注入前台上下文的证据层，不计入有限常驻行为状态 \(M_t\) 的大小。前台只有在 Snapshot 和 Long-term Memory 不足时才按需访问它。Harness 可以为不同宿主提供文件、目录、API、Tool 或其他只读适配方式，但必须在 Memory Access Instruction 的 Raw History 信息段中向前台披露实际访问位置和方法。

Working Memory 是 Raw History 中尚未被已发布 Long-term Memory、Memory Snapshot 与历史覆盖 cursor 组成的正式状态可靠承接的活动区间。历史替换只改变前台模型投影，不改变 Raw History。

## 2. 记忆整理触发

Harness 负责根据确定性策略决定何时标记待压缩上下文并创建后台记忆整理任务。

触发信号可以来自：

- 前台 Agent 一次执行完成；
- Working Memory 达到配置阈值；
- 前台上下文接近可用预算；
- 会话进入空闲状态；
- 用户显式要求整理记忆；
- 已积压的 Working Memory 需要合并处理。

用户消息数量也可以作为上下文长度标志，例如 Working Memory 中超过五个 User Message 时触发。具体阈值属于 Harness 配置，不构成固定框架依赖。

触发时，Harness 从当前历史覆盖 cursor 之后开始，标记一段连续前缀或全部 Working Memory 作为待压缩范围。该标记只创建冻结范围和整理任务，不直接改变正式记忆或前台上下文。未被标记的 Working Memory，以及标记后新增的上下文，继续按真实顺序累计记录。

## 3. 处理范围冻结

Harness 在调度后台 Agent 前，必须冻结本次任务能够读取和建议替换的连续 Working Memory 前缀，以及任务所基于的 Long-term Memory 和 Memory Snapshot 版本。

```text
memory_job
  conversation_id
  base_memory_revision
  base_snapshot_revision
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
- 为后台 1 号 Agent 创建任务隔离的 Pending 缓冲区；
- 为后台 2 号 Agent 冻结由稳定 UT ID 和精确内容组成的构建批次；
- 将基于旧版本产生的提案标记为过期并拒绝直接提交。

调度协议不得要求后台 Agent 与宿主前台 Agent 使用相同框架、模型、进程或部署方式。

## 5. Pending 缓冲区与统一提案接收

后台 1 号 Agent 负责语义判断，并可以在本次任务隔离的 Pending 缓冲区中直接新增或修改 UT。修改已经处于 Built 状态的 UT 时，修改后的 UT 在缓冲区中重新标记为 Pending。缓冲区中的逐步写入不对前台可见，也不改变正式 Long-term Memory。

后台 1 号 Agent 完成全部 UT 处理和 Memory Snapshot 生成后，向 Harness 提交统一结果，包括：

- 本次 Pending 缓冲区及其完整变更集；
- 新增、更新或替代的 UT；
- 建议发布的 Memory Snapshot；
- 建议由新记忆承接的 Working Memory 范围；
- 每项候选记忆对应的原始证据。

Harness 不自行总结历史，也不执行后台 Agent 任意生成的外部写入命令。Harness 只把任务隔离的 Pending 缓冲区和统一提案作为待验证数据处理；只有完整任务成功结束并通过 Harness 校验后，缓冲区才有资格与 Snapshot、历史覆盖 cursor 一起原子激活。

## 6. 合法性校验与替换裁决

Harness 必须通过确定性代码决定哪些提案可以提交，以及哪些 Working Memory 可以实际退出前台上下文。

代码校验至少覆盖：

- **结构校验**：字段、类型、大小和操作种类合法；
- **身份与权限校验**：提案属于正确的用户、会话、任务和记忆命名空间；
- **范围校验**：建议替换范围位于被冻结范围内；
- **版本校验**：提案基于当前有效的 Long-term Memory revision 和 Memory Snapshot revision；
- **证据校验**：引用存在、未被篡改并位于授权历史范围内；
- **覆盖校验**：替换范围连续，不能跳过未承接历史形成空洞；
- **完整性校验**：Memory Snapshot 和记忆变更满足正式发布契约；
- **UT 变更契约校验**：后台 1 号 Agent 已提交冲突处理声明、证据引用和新增、修改或替代关系；UT 的字段、状态、稳定 ID、引用关系及检索测试满足可机械验证的正式契约；
- **语义承接声明校验**：后台 1 号 Agent 必须明确声明 Pending UT 变更、关联记忆内容和新的 Memory Snapshot 已经承接被冻结范围中仍会影响未来行为的内容；当 Pending UT 变更为空时，必须明确声明该范围没有需要新增或修改的长期记忆；
- **确定性证据校验**：Harness 只校验上述声明存在、引用证据有效、UT 契约通过、版本一致且覆盖连续，不重新总结历史，也不自行判断应该记住什么；

后台 1 号 Agent 对应该记住什么、记忆内容、冲突是否已在语义上正确消解、语义完整性和空 UT 结论负责；Harness 负责代码层面的合法性、一致性、证据引用有效性、UT 契约、版本、连续覆盖与原子提交裁决。Harness 只验证后台 1 号的语义声明是否存在，以及其证据引用和操作是否满足可执行契约；Harness 不判断应该记住什么，不重新判断冲突是否在语义上正确消解，也不取代后台 1 号进行语义总结。

## 7. 后台 1 号提案的原子提交与替换发布

后台 1 号 Agent 完成提取和修改 UT，只表示 Pending 候选已经准备完毕；它本身不构成正式发布。通过校验的提案必须由 Harness 原子提交。一次成功提交同时完成：

1. 当存在语义记忆变更时发布新的 Long-term Memory 版本；没有语义记忆变更时继续使用当前版本；
2. 将本次任务隔离的 Pending 缓冲区激活为正式可检索的 Pending UT；缓冲区可以为空；
3. 发布新的 Memory Snapshot；
4. 保存记忆与原始证据的关联；
5. 推进历史覆盖边界；
6. 使已被可靠承接的 Working Memory 获得退出前台投影的资格。

设 \(r\) 为 Long-term Memory revision，\(s\) 为 Memory Snapshot revision，\(c\) 为已结算历史 cursor。形式化表示为：

\[
(L_r,S_s^{(c)},W_{>c})
\longrightarrow
(L_{r'},S_{s+1}^{(c')},W_{>c'})
\]

其中 \(c'\) 是本次实际提交的覆盖终点，不要求等于 \(c+1\)。当提案包含新增或修改的 UT 时，\(r'=r+1\)；当 Pending UT 变更为空时，\(r'=r\)。记忆 revision、Snapshot revision 和历史 cursor 是三个独立状态量。后台 1 号提案被 Harness 原子激活后，新增或修改的 UT 成为正式的 Pending Long-term Memory，并推进语义 Long-term Memory revision。后台 2 号只把同一语义从 Pending 检索表示迁移为 Built 检索表示，不推进 Long-term Memory revision、Snapshot revision 或历史 cursor；Harness 使用内部构建产物标识或内容指纹管理该迁移。

任何一步失败时：

- 旧 Long-term Memory 和旧 Memory Snapshot 继续有效；
- 历史覆盖边界不推进；
- Working Memory 不退出；
- 前台 Agent 仍可使用上一个有效快照和完整未结算尾部。

记忆替换不由时间单独触发，而由经过验证的 Long-term Memory 状态、Memory Snapshot 与历史覆盖 cursor 原子发布成功触发；不要求每次都产生新的 Long-term Memory revision。替换不得中途改写正在运行的前台 Agent 上下文，只在后续上下文投影中生效。

## 8. 前台上下文投影

Harness 在每次调用宿主前台 Agent 前生成新的模型可见上下文：

\[
C_t
=
P_{system}
\oplus
I_{memory\text{-}access}
\oplus
S_s^{(c)}
\oplus
W_{>c}
\]

上下文投影必须：

- 保留宿主 System Prompt 的语义；
- 提供固定的 Memory Access Instruction；
- 只读取最新已提交的 Memory Snapshot；
- 不读取后台任务的半成品；
- 只移除历史覆盖边界之前、已被可靠承接的 Working Memory；
- 保留尚未结算以及后台处理期间新增的 Working Memory 尾部；
- 在 Memory Access Instruction 中披露 Raw History 的稳定位置或只读访问方式；
- 不修改或删除 Raw History。

替换前：

```text
System Prompt
Memory Access Instruction
Memory Snapshot revision s，覆盖到 cursor c
Working Memory: event 101 ... event 180
```

后台只处理 `event 101 ... event 160`，且 Harness 成功提交后：

```text
System Prompt
Memory Access Instruction
Memory Snapshot revision s+1，覆盖到 cursor 160
Working Memory: event 161 ... event 180
```

## Harness 状态边界

Harness 至少必须管理：

```text
当前 Long-term Memory revision
当前 Memory Snapshot revision
已结算到的历史 cursor
当前 Working Memory 范围
Raw History Store 的位置、格式与最新事件 cursor
正在运行的后台任务及其冻结范围
任务隔离的 Pending 缓冲区
已经发布的 Pending UT 池
当前 Built 检索产物标识或内容指纹
后台 2 号冻结的 UT 构建批次
待验证的记忆提案
最后一次成功提交
```

Harness 必须防止同一历史范围被冲突提交、旧提案覆盖新 Snapshot、新消息被旧任务误删，以及未承接历史被跳过。

## Harness 核心约束

> **Harness 负责永久保存自身可观察到的所有 Raw History，并向前台提供可由自身工具直接搜索的只读历史访问能力；同时负责触发整理、冻结范围、创建隔离缓冲区、调度后台、接收提案、代码校验、原子激活和生成前台上下文投影。后台 1 号 Agent 可以直接写入任务隔离的 Pending 缓冲区，但未经 Harness 校验并与 Snapshot、历史覆盖 cursor 一起成功激活，不得改变正式状态。**

# 第三部分：后台记忆 Agent

## 后台 Agent 边界

后台记忆能力由两个彼此独立的 Agent 角色组成：

- **后台 1 号 Agent：记忆提取 Agent**，负责根据旧 Snapshot 与冻结的 Working Memory 提取和维护检索 UT，在任务隔离的 Pending 缓冲区中直接写入变更，并生成 Memory Snapshot 和历史承接范围；
- **后台 2 号 Agent：记忆构建 Agent**，负责冻结一批已经发布的 Pending UT，将其构建进正式检索系统，并产出经过 Built-only 验证的构建结果。

两个后台 Agent 均与宿主前台 Agent 解耦，不要求使用相同框架、模型、进程、工具协议或部署方式。后台 1 号 Agent 对任务隔离缓冲区拥有直接写入权，两个后台 Agent 都没有正式状态激活权和历史替换权；正式状态切换必须由 Harness 完成。

## memory-cli 的两种工作模式

memory-cli 必须明确区分静态构建模式和动态构建模式。模式由记忆项目配置显式声明，不能根据当前是否存在 Pending UT 临时推断。

```text
Static Mode
  所有正常参与检索的 UT 都已经完成构建
  search 只查询正式检索系统
  UT 只作为验证契约，不作为运行时搜索语料

Dynamic Mode
  UT 具有 Pending 或 Built 两种构建状态
  Pending UT 通过字符或关键词匹配提供即时检索
  Built UT 通过正式检索系统提供检索
  search 统一合并两条路径的结果
```

概念配置可以表示为：

```json
{
  "mode": "static"
}
```

或：

```json
{
  "mode": "dynamic"
}
```

静态模式保持传统 memory-cli 的运行时数据与测试数据分离；动态模式允许已经由 Harness 激活的 Pending UT 作为构建完成前的即时检索规则。两种模式使用相同的稳定外部命令，模式差异只存在于内部检索与构建流程。

## 动态模式的双层检索

动态模式下，Long-term Memory 同时包含两个正式可检索层：

```text
Pending UT 层
  后台 1 号 Agent 已完成提取或修改
  已经由 Harness 与 Snapshot、cursor 一起原子激活
  尚未构建进正式检索系统
  通过 UT queries 的字符或关键词匹配检索

Built UT 层
  后台 2 号 Agent 已完成构建
  已经通过 Built-only UT 验证并由 Harness 原子切换
  通过正式检索系统检索
```

Pending 和 Built 都属于已经发布的 Long-term Memory。Pending 不是后台半成品；任务隔离且尚未由 Harness 激活的 Pending 缓冲区才是后台中间结果。

两层在设计语义上只有检索表示、检索成本和性能差异，不允许主动引入记忆内容、可用事实或预期未来行为效果差异。面对同一有效查询，两层的理论目标是向前台提供语义等价的答案承载内容：

\[
E_{behavior}\left(R_{pending}(q)\right)
=
E_{behavior}\left(R_{built}(q)\right)
\]

Pending 使用较慢的轻量匹配立即提供可靠记忆；Built 在不改变语义的前提下，把同一批 UT 的检索行为构建进更稳定、高效的检索系统。上式是算法的理论目标，不要求把 Pending/Built 差分验证作为状态切换门槛，也不承诺实际检索结果逐项完全相同。实际效果偏移属于检索算法需要持续测试和优化的质量问题；只要 Built 候选通过对应 UT 的 Built-only 验证，就可以完成状态切换。

Pending 与 Built 的实际结果允许存在偏移，但该偏移不得改变已发布记忆的目标语义，且对未来行为造成的偏差必须受第一原则的 \(\epsilon\) 约束。Built-only UT 是状态切换门槛；实际偏移通过后续检索质量评估和算法优化持续收敛，不要求增加 Pending/Built 差分验证门槛。

## UT 数据契约

动态模式中的每项 UT 必须具有稳定 UT ID、关联记忆内容、检索输入、结果断言和构建状态。构建状态只有两个值：

```text
pending
built
```

构建任务是否正在运行属于后台任务信息，不属于 UT 状态，不得为此增加第三个 UT 状态。

```yaml
retrieval_unit:
  id: ut-123
  memory_id: memory-123
  build_state: pending
  content: 用户决定使用某项方案
  priority: 80
  queries:
    - 用户最终决定使用什么方案
    - 已确认的方案
  must_include:
    - 某项方案
  evidence_refs:
    - raw-history:event-160
```

在动态模式的 Pending 路径中，`queries` 可以作为即时搜索的字符或关键词匹配语料；命中后返回该 UT 关联的完整 `content`，不能只返回 `must_include`。`must_include` 仍然是结果断言，不作为返回内容的替代品。

在 Built 路径中，`queries` 和 `must_include` 只作为验证契约；正常搜索必须通过已经构建的正式检索系统完成。静态模式始终采用 Built 路径的边界。

UT 不需要面向业务暴露 `v1`、`v2` 版本。为避免并发构建误提交，后台 2 号必须冻结 UT 的精确内容，Harness 可以通过完整内容比较或内部内容指纹确认构建输入没有变化。内容指纹只用于并发校验，不属于新的 UT 状态。

## 后台 1 号 Agent：记忆提取

后台 1 号 Agent 是高频运行的语义提取角色。它读取 Harness 提供的：

- 冻结的 Working Memory 范围；
- 当前已提交的 Long-term Memory；
- 当前已提交的 Memory Snapshot；
- 受控的原始证据；
- 本次任务的基础记忆版本和覆盖边界。

它必须从历史中收集并提取仍可能影响未来行为的信息，包括：

- 身份、偏好、关系和长期目标；
- 决定、约束、承诺和未完成事项；
- 已完成工作、任务结果和可复用经验；
- 关键实体、别名、日期、位置和关系；
- 状态变化、当前有效值及精确的答案承载细节；
- 未来可能使用的检索词和检索场景。

后台 1 号 Agent 在写入新 UT 前，必须主动对照当前已发布记忆和当前有效 UT 检查冲突。发现冲突时，它应在任务隔离的 Pending 缓冲区中修改已有 UT、补充必要 UT 或声明对旧 UT 的替代关系，使最终 UT 集合表达当前有效的记忆行为。

后台 1 号 Agent 可以直接写入本次任务的 Pending 缓冲区，但不能直接改写前台可见的已发布状态。已经处于 Built 状态的 UT 一旦被修改，修改后的 UT 必须重新进入 Pending 状态。旧 Built 检索产物在新 Pending 激活后必须被同一稳定 UT ID 的新内容覆盖，不能与新结果无规则并列返回。

修改已有 UT 必须有冻结历史或已授权证据支持，不能为了让新增内容通过而删除、放宽或弱化仍然有效的行为约束。后台 1 号 Agent 必须在结束前消解冲突；尚未消解的冲突不能随缓冲区一起激活。

## 后台 1 号 Agent 的处理顺序

后台 1 号 Agent 必须在同一次历史理解中按以下顺序工作：

1. 从旧 Snapshot 与冻结的 Working Memory 中提取记忆内容和候选 UT；
2. 对照当前已发布记忆和有效 UT 检查冲突；
3. 在任务隔离的 Pending 缓冲区中新增、修改或替代 UT；
4. 使用旧 Snapshot 和本次冻结的 Working Memory 生成新的 Memory Snapshot；
5. 声明新 Snapshot 与 Pending UT 建议承接的连续历史范围；
6. 向 Harness 提交完整缓冲区和统一提案。

```text
连续历史输入：旧 Snapshot + 冻结的 Working Memory
冲突检查参考：已发布 Long-term Memory + 当前有效 UT
                              │
                              ▼
                       后台 1 号 Agent
                              ├── Pending UT 缓冲区
                              ├── Memory Snapshot 候选
                              └── 历史承接范围建议
                                           │
                                           ▼
                                        Harness
```

## Memory Snapshot 生成

Memory Snapshot 候选由后台 1 号 Agent 在完成关键记忆和检索 UT 的提取后生成。旧 Snapshot 与本次冻结的 Working Memory 是生成新 UT 和新 Snapshot 的共同连续历史输入；当前已发布 Long-term Memory 与有效 UT 只用于 UT 冲突检查、合并和替代。设旧 Snapshot 已承接到 cursor \(c\)，本次冻结范围结束于 \(c'\)，\(\Delta L\) 为本次 Pending 缓冲区表达的候选语义变更，先在当前已发布状态上形成尚待 Harness 提交的候选 Long-term Memory 状态：

\[
\widehat{L}_{r'}
=
\operatorname{ApplyCandidate}(L_r,\Delta L)
\]

新 Snapshot 候选是旧 Snapshot 与 \(W_{(c,c']}\) 的连续状态投影：

\[
\widehat{S}_{s'}^{(c')}
=
\operatorname{Project}
\left(
S_s^{(c)} \oplus W_{(c,c']}
\right)
\]

\(\widehat{L}_{r'}\) 和 \(\widehat{S}_{s'}^{(c')}\) 都不是已发布状态，而是后台 1 号根据同一段连续历史并行生成的结果，二者不互相充当历史输入。后台 1 号将二者作为同一统一提案提交；只有 Harness 校验并原子激活后，它们才分别成为正式的 \(L_{r'}\) 和 \(S_{s'}^{(c')}\)。当 Pending UT 变更为空时，\(\widehat{L}_{r'}=L_r\)，但新的 Snapshot 候选仍可承接本次连续历史范围。

例如旧 Snapshot 承接 event 50、在 event 89 触发记忆整理时，新 Snapshot 由旧 Snapshot 与 event 51 至 event 89 共同生成；处理期间新增的 event 90 及以后内容不进入本次 Snapshot。

生成顺序必须为：

```text
第一阶段：提取可检索记忆
  ├── 提取关键事实
  ├── 建立或修改 UT
  ├── 检查当前记忆与有效 UT 冲突
  ├── 消解冲突和声明替代关系
  ├── 建立证据引用
  └── 写入任务隔离的 Pending 缓冲区

第二阶段：生成连续状态
  ├── 读取旧 Memory Snapshot
  ├── 使用第一阶段已提取的事实和证据
  ├── 归纳当前执行状态
  ├── 标记已完成事项
  ├── 保留未完成事项
  └── 生成新 Memory Snapshot 候选
```

Memory Snapshot 只承载前台 Agent 为继续行动必须直接知道的信息：

- 对多数未来行为持续重要的身份、关系和稳定偏好；
- 最近上下文；
- 当前状态；
- 已完成事项；
- 下一步行动；
- 当前有效的目标、约束和承诺。

其他可在未来按需查找的内容进入即时记忆层，原始细节保留在完整证据历史中。Snapshot 不能脱离已经提取出的记忆和证据重新自由总结。

### 空 UT 变更

冻结范围可能不包含任何仍会影响未来行为的新信息。后台 1 号 Agent 不得为了推进历史覆盖 cursor 而制造无价值 UT；此时可以提交空的 Pending UT 变更集，并在统一提案中明确声明本次没有需要新增、修改或替代的长期记忆。

空 UT 变更仍然必须生成新的 Memory Snapshot 候选和历史承接建议，并保留支持该判断的证据引用。Harness 校验并原子发布后，可以在不推进 Long-term Memory revision 的情况下推进 Snapshot revision 与历史覆盖 cursor。空变更只表示不需要改变 Long-term Memory，不表示可以跳过 Snapshot、证据、连续覆盖或原子发布校验。

## 增量上下文与无感替换

后台 1 号 Agent 处理冻结范围期间，Harness 的上下文缓存必须继续记录新事件，前台 Agent 不等待后台处理，也不感知缓存替换。

例如，当前状态为：

```text
Memory Snapshot：已经承接到 event 50
Working Memory：event 51 ... event 89
```

Harness 在 event 89 处触发后台任务后：

```text
后台 1 号输入：
  已发布 Snapshot 和 Long-term Memory，承接到 event 50
  冻结的 Raw History / Working Memory：event 51 ... event 89

Harness 缓存继续增长：
  旧 Snapshot + event 51 ... event 102
```

后台 1 号完成且 Harness 原子激活后：

```text
新 Snapshot：承接到 event 89
新 Pending UT：从 event 51 ... event 89 提取并正式可检索
Working Memory：event 90 ... event 102
```

替换只影响下一次前台上下文投影，不中途改写正在运行的模型调用。event 90 之后的新增上下文始终保留，不能被 event 89 的后台任务删除。

## 后台 1 号 Agent 的统一提案

后台 1 号 Agent 向 Harness 返回一个统一提案：

```yaml
memory_proposal:
  proposal_id: proposal-123
  base_memory_revision: 7
  base_snapshot_revision: 12

  source_range:
    from_cursor: 101
    to_cursor: 160

  pending_buffer_ref: pending-buffer-123
  # 本例模拟该范围没有需要新增或修改的长期记忆。
  changed_ut_ids: []

  snapshot_candidate:
    resident_memory: ...
    recent_context: ...
    current_state: ...
    completed: ...
    next_actions: ...
    constraints: ...

  replacement_candidate:
    covered_through: 160

  # 空 UT 变更仍必须引用支持该判断的冻结历史证据。
  evidence_refs:
    - raw-history:range-101-160
```

Harness 完成结构、证据、版本、连续覆盖和 UT 变更校验后，必须把 Pending 缓冲区、Memory Snapshot 和历史覆盖 cursor 一起原子激活。任何一部分失败时，缓冲区都不能对前台可见，旧 Snapshot 和 Working Memory 必须继续有效。

原子激活成功后，非空的 Pending UT 立即进入正式即时检索层，Memory Snapshot 成为新的前台 Snapshot，历史覆盖边界按已验证范围推进。此时被冻结范围已经由已发布 Long-term Memory 与 Memory Snapshot 可靠结算，可以在下一次前台上下文投影中退出 Working Memory，无须等待后台 2 号 Agent 完成构建。空 UT 变更时没有新的 Pending UT 进入检索层，但不影响经过验证的历史结算。

## memory-cli 双层检索

`memory-cli search` 必须统一查询即时记忆层与已构建记忆层：

\[
R(q)
=
\operatorname{Merge}
\left(
R_{built}(q),
R_{pending}(q)
\right)
\]

```text
memory-cli search
      ├── 正式记忆系统检索 ──► built results
      └── Pending UT queries 字符匹配 ──► pending results
                                  │
                                  ▼
                         覆盖旧结果、去重、排序
```

返回结果必须至少区分 UT 的构建状态：

```json
{
  "id": "memory-123",
  "content": "...",
  "build_state": "pending"
}
```

合并必须遵守：

- 同一稳定 UT ID 或 memory ID 的 Pending 新内容覆盖旧 Built 检索产物；
- 同一记忆不能因为同时存在于两条检索路径而重复返回；
- Pending 与 Built 结果进入统一排序；
- Pending 路径必须返回 UT 关联的完整记忆内容；
- 未被 Harness 激活的任务缓冲区不能参与搜索。

## 后台 2 号 Agent：记忆构建

后台 2 号 Agent 是异步、可批量运行的记忆构建角色。它负责：

- 从已经发布的 Pending UT 池中冻结一批稳定 UT ID 和精确 UT 内容；
- 在不改变记忆语义的前提下，将冻结批次构建进正式记忆结构和正式索引；
- 在关闭对应 Pending fallback 的条件下，对 Built 候选运行冻结批次 UT；
- 运行适用于正式检索系统的回归 UT 和性能测试；
- 生成新的 Built 检索系统候选；
- 向 Harness 提交正式构建结果。

后台 2 号 Agent 不得在构建过程中自行更改已经发布的 UT、记忆事实、约束、优先级或替代关系。发现语义不一致时，本次构建必须失败并返回诊断；语义修正由后台 1 号 Agent 重新写入 Pending 缓冲区并走原子激活流程。

后台 2 号 Agent 构建的是已发布记忆的运行时结构和索引，不在普通记忆构建任务中修改 `memory-cli` 的实现代码或检索算法。检索系统本身的代码优化属于独立工程流程。

## Built-only 验证

Pending UT 本身就是即时搜索规则，因此不能通过包含 Pending fallback 的合并搜索证明 Built 候选已经正确构建。否则 Pending 结果会掩盖 Built 检索失败，形成自证循环。

后台 2 号 Agent 必须对冻结批次执行：

```text
冻结 Pending UT 批次
        ↓
生成 Built 候选
        ↓
关闭该批次的 Pending fallback
        ↓
仅通过 Built 候选运行该批次 UT
        ↓
验证通过后提交 Harness
```

形式化要求为：

\[
Test(B_{candidate}, UT_{batch}, pending\text{-}fallback=off)=pass
\]

Built-only 验证通过前，对应 UT 必须继续保持 Pending 并正常参与前台搜索。构建或验证失败只影响后台构建任务，不能使 Pending UT 退出检索。

## 两状态流转与原子迁移

动态模式下 UT 的构建状态只有：

```text
pending
   ↓ Built-only 验证通过并由 Harness 原子切换
built
```

修改已经 Built 的 UT 时，修改后的内容重新进入 Pending：

```text
built
   ↓ 后台 1 号修改并由 Harness 原子激活
pending
```

后台 2 号冻结批次后，后台 1 号仍可继续向其他任务缓冲区写入并发布新的 Pending UT。设 \(\Delta\) 为 Built-only 验证通过且内容仍未变化的冻结批次，Harness 的原子迁移为：

\[
(P,B)
\longrightarrow
(P\setminus\Delta,\operatorname{Replace}(B,\Delta))
\]

Harness 提交前必须比较当前 Pending UT 与后台 2 号冻结内容。只有稳定 UT ID 和精确内容仍然一致的项才能从 Pending 切换为 Built；已经被后台 1 号修改的项必须继续保持 Pending，旧构建结果直接作废。该校验可以比较完整内容或内部内容指纹，不要求增加面向业务的 UT 版本号。Pending 到 Built 的迁移只改变检索表示和内部构建产物，不产生新的语义 Long-term Memory revision。

Harness 只切换本次验证通过且内容未变化的批次，不能清空整个 Pending 池。迁移必须保证：

- Built 候选可用之后才切换对应 UT；
- 状态切换与正式检索结构发布原子完成；
- 构建期间新增或修改的 Pending UT 保持不变；
- 失败时原 Pending UT 继续提供即时检索；
- 迁移过程中不存在检索空窗或语义变化。

## 后台记忆 Agent 核心约束

> **后台 1 号 Agent 负责将旧 Snapshot 与冻结的 Working Memory 转换为 Pending UT、Memory Snapshot 候选和历史承接建议，并可以直接写入任务隔离的 Pending 缓冲区；后台 2 号 Agent 负责把已经发布的 Pending UT 构建进正式检索系统，并以 Built-only 方式验证。动态模式允许 Pending UT 作为即时检索规则，静态模式和 Built 路径仍把 UT 作为验证契约。UT 的构建状态只有 Pending 和 Built；正式激活、状态切换和历史替换仍由 Harness 通过代码校验和原子提交完成。**
