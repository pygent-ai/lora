# 永续会话 Agent 第二原则：概念架构

> 本文只约束永续会话的概念架构，不依赖任何 Agent 框架、模型、数据库、消息队列或具体实现。

## 第二原则

> **前台行动，后台记忆；双通道使用，快照式交接，历史可追溯。**

永续会话必须将当前任务执行与长期记忆维护分离：

- **前台 Agent** 面向用户，在 Working Memory 上理解请求、执行任务和交付结果；
- **后台记忆 Agent** 面向历史，负责将 Working Memory 中具有未来价值的信息整理为 Long-term Memory 候选，并向 Harness 提交提案；
- **Harness** 独占正式状态提交权，负责校验后台提案、原子发布 Long-term Memory，并组装前台 Agent 的当前上下文。

这种分离是职责边界，不要求前台和后台必须使用不同模型、不同进程或不同服务。

## 两类记忆的定义

### Working Memory

> **Working Memory 是尚未被后台记忆 Agent 整理，并由 Harness 已发布的 Long-term Memory、Memory Snapshot 与历史覆盖 cursor 组成的正式状态可靠承接的当前上下文。**

它随前台任务即时增长，包含最近一次由 Harness 成功原子发布 Long-term Memory 状态、Memory Snapshot 并推进历史替换 cursor 之后产生的消息、行动、工具结果和状态变化。Long-term Memory 变更可以为空；只要正式状态尚未成功发布并推进 cursor，Working Memory 的起点就不变。后台任务完成、提交提案或更新检索表示但没有推进 cursor 时，也不能改变 Working Memory 的起点。它不等于前台 Agent 的全部模型上下文，因为模型上下文还会包含从 Long-term Memory 注入或披露的内容。

### Long-term Memory

> **Long-term Memory 是经后台记忆 Agent 整理、由 Harness 校验并正式发布、可跨任务持续使用的记忆。**

后台记忆 Agent 是 Long-term Memory 的语义维护者，不是 Long-term Memory 本身，也不拥有正式状态提交权。Long-term Memory 可以采用任意存储或检索实现，但只有已经由 Harness 完成原子发布的内容才能供前台使用。

二者的关系是：

```text
Working Memory
      │
      │ 后台提取、整理、精炼
      ▼
后台记忆 Agent
      │
      │ 提交候选提案
      ▼
Harness
      │
      │ 校验并原子发布
      ▼
Long-term Memory
```

## 概念组成

```text
完整历史／证据 ──► 后台记忆 Agent ──提案──► Harness ──原子发布──► Long-term Memory
       ▲                                                               │
       │                    ┌───────────────────────┴────────────────────┐
       │                    ▼                                            ▼
       │              常驻记忆上下文                               按需披露记忆
       │                    │                                            │
       │                    └──────────► 上下文组装器 ◄──────────────────┘
       │                                         ▲
       │                                         │
       │                                  Working Memory
       │                                         │
       │                                         ▼
       └───────────────────────────────────── 前台 Agent
```

该架构包含以下概念角色：

1. **前台 Agent**：处理当前任务，不承担长期记忆库的整理与维护。
2. **后台记忆 Agent**：整理被冻结的 Working Memory，并向 Harness 产出 Long-term Memory 的候选变更。
3. **Working Memory**：保存尚未被 Harness 已发布的 Long-term Memory、Memory Snapshot 与历史覆盖 cursor 组成的正式状态可靠承接的当前上下文。
4. **Long-term Memory**：保存已经由后台整理并由 Harness 发布、可跨任务使用的持久记忆。
5. **完整历史／证据层**：记为 \(E_t\)，独立保存系统可观察到的原始交互，为追溯、纠错和重新提取提供按需证据；它不自动进入前台上下文，也不属于 Long-term Memory。
6. **Harness／上下文组装器**：校验并原子发布正式状态，按照确定性规则为前台 Agent 生成当前上下文。
7. **记忆披露接口**：允许前台 Agent 根据当前任务发现、检索并展开非必要常驻的 Long-term Memory。

## 记忆使用的双通道

### 常驻注入

每个任务开始时，由上下文组装器自动提供对多数未来行为持续重要且不应依赖主动检索的信息，例如：

- 身份、关系和稳定偏好；
- 长期目标与硬性约束；
- 当前任务状态和未完成承诺；
- 记忆能力的存在及其使用边界。

常驻注入必须保持有限。它是动态组装出的稳定上下文，不等于把全部记忆固定拼接给前台 Agent。

### 按需披露

长尾、低频和细节性记忆不默认占用工作上下文，而通过逐步披露供前台 Agent 使用：

```text
知道存在 → 查询相关记忆 → 查看结果摘要 → 展开具体记忆或原始证据
```

前台 Agent 必须知道记忆系统存在、何时应查询以及可以查询什么。只有存储和检索能力、却没有可发现性，不构成有效的记忆披露。

## 任务边界与后台处理

Harness 持续记录前台上下文。上下文长度、用户消息数量、上下文预算、任务状态或其他确定性标志达到触发条件后，Harness 可以从当前 Working Memory 的起点开始，标记一段连续前缀或全部 Working Memory，冻结为本次后台处理范围。未被标记的上下文以及冻结后新增的上下文继续累计，不属于本次替换范围。

后台记忆 Agent 以上一版 Snapshot 与被冻结范围作为连续历史输入进行处理，至少包括：

- 提取仍可能影响未来行为的信息；
- 合并重复信息；
- 识别冲突、变更、过期和不确定信息；
- 精炼已有记忆；
- 维护记忆与原始证据之间的关联；
- 生成 Long-term Memory 变更与 Memory Snapshot 候选，并向 Harness 提交提案；当冻结范围没有需要长期保留的信息时，Long-term Memory 变更可以为空。

触发条件不依赖特定框架事件。“超过五个用户消息”可以作为一种配置策略，但不是固定架构要求。触发只冻结和调度一个范围；只有后台记忆能力完成记忆整理与 Memory Snapshot 候选、Harness 校验并原子发布后，该范围才能退出前台上下文。

## 快照式交接

后台记忆处理不能成为下一个任务的必然硬阻塞点，也不能在尚未完成并发布时提前丢弃历史。已经由 Harness 发布、能够可靠承接 Working Memory 的 Long-term Memory，可以在后续检索优化或内部构建尚未完成时继续生效。

设：

- \(L_r\) 为 revision 为 \(r\) 的最新已发布 Long-term Memory；
- \(S_s^{(c)}\) 为 revision 为 \(s\)、已经可靠承接到历史 cursor \(c\) 的 Memory Snapshot；
- \(W_{>c}\) 为 cursor \(c\) 之后、尚未被已发布正式状态可靠承接的 Working Memory；
- \(q\) 为下一个任务。

下一个任务的初始上下文应满足：

\[
C_{initial}
=
P_{system}
\oplus
I_{memory\text{-}access}
\oplus
S_s^{(c)}
\oplus
W_{>c}
\]

其中 Memory Snapshot 是有限常驻连续状态，不是 Long-term Memory 的单独投影。后台记忆能力以旧 Snapshot 与本次冻结的 Working Memory 作为连续历史输入，在同一次历史理解中并行生成 Long-term Memory 候选变更与新 Snapshot 候选。当前已发布 Long-term Memory 和有效 UT 用于检查、合并和消解 UT 冲突，不作为拼接到 Snapshot 中的第三段历史。

设 \(\Delta L\) 为后台记忆能力提出的 Long-term Memory 候选变更，\(\widehat{L}_{r'}\) 为在当前已发布状态 \(L_r\) 上应用该候选变更后、尚待 Harness 提交的候选 Long-term Memory 状态：

\[
\widehat{L}_{r'}
=
\operatorname{ApplyCandidate}(L_r,\Delta L)
\]

新的 Snapshot 候选是上一版 Snapshot 与从上一历史 cursor 开始的本次冻结 Working Memory 的连续状态投影：

\[
\widehat{S}_{s'}^{(c')}
=
\operatorname{Project}
\left(
S_s^{(c)} \oplus W_{(c,c']}
\right)
\]

\(\widehat{L}_{r'}\) 与 \(\widehat{S}_{s'}^{(c')}\) 是后台记忆能力根据同一段连续历史并行产出的统一提案组成部分，不互相充当历史输入。只有 Harness 校验并原子发布后，它们才分别成为正式的 \(L_{r'}\) 与 \(S_{s'}^{(c')}\)。当 Long-term Memory 变更为空时，\(\widehat{L}_{r'}=L_r\)，但 Snapshot revision 与历史覆盖 cursor 仍可随成功提交推进。

例如上一版 Snapshot 承接 event 50，本次在 event 89 触发处理时，新 Snapshot 是“承接到 event 50 的旧 Snapshot”与 event 51 至 event 89 的投影；event 90 之后的上下文不进入本次 Snapshot，继续保留在 Working Memory。当前任务需要更多历史信息时，前台 Agent 通过记忆披露接口查询 \(L_r\)；披露结果作为新的工具结果或上下文事件追加到 Working Memory，而不是预先假定所有相关记忆已经在任务开始前完成检索。

也就是说：

> **已结算历史由 Long-term Memory、Memory Snapshot 与历史覆盖 cursor 组成的正式状态承接；尚未完成结算的上下文继续作为 Working Memory 进入前台。**

后台处理完成后，Long-term Memory 状态、Memory Snapshot 与结算边界必须由 Harness 以原子方式发布。Long-term Memory 变更可以为空，但新的 Snapshot 必须明确新的可靠承接边界：

\[
(L_r, S_s^{(c)}, W_{>c})
\longrightarrow
(L_{r'}, S_{s'}^{(c')}, W_{>c'})
\]

只有当 Long-term Memory 状态与新的 Memory Snapshot 已由 Harness 成功发布，并确认已保留某段 Working Memory 对未来行为仍然有效的全部影响，或者确认该范围不存在需要继续保留的影响后，该部分才能退出 Working Memory。后台处理期间产生的新上下文不属于本次发布的覆盖范围，必须继续保留。

## 不可偏离的架构边界

1. 前台 Agent 不应同时承担当前任务执行和长期记忆库整理两种主要职责。
2. 后台 Agent 是 Long-term Memory 的语义维护者，只有提案权；Harness 独占正式状态提交权。二者都不能悄然改变用户目标、事实、承诺或约束。
3. Working Memory 只指尚未被 Harness 已发布的 Long-term Memory、Memory Snapshot 与历史覆盖 cursor 组成的正式状态可靠承接的当前上下文，不等于前台 Agent 的全部模型上下文。
4. Long-term Memory 替代的是前台上下文中已经可靠结算的旧 Working Memory，不是完整历史／证据层。
5. 未被 Harness 已发布的 Long-term Memory、Memory Snapshot 与历史覆盖 cursor 组成的正式状态可靠承接的 Working Memory，必须继续保留在下一个任务的上下文中。
6. 后台记忆处理失败、延迟或重试时不得造成 Working Memory 空洞，也不应无条件阻塞前台任务；后续检索优化或内部构建尚未完成时，已经发布的 Long-term Memory 必须继续可用，并保持相同的目标语义，实际行为偏差不得超过第一原则允许的 \(\epsilon\)。
7. Long-term Memory 的更新必须通过 Harness 可识别的提交边界生效；未经 Harness 发布的后台半成品不能对前台可见。
8. 常驻注入和按需披露必须同时存在；不能要求所有 Long-term Memory 常驻，也不能要求前台对所有关键记忆主动检索。
9. Working Memory 和 Long-term Memory 都必须保持到原始证据的可追溯关系，并允许纠错。
10. Working Memory 与 Long-term Memory 的区分是概念和生命周期上的，不要求使用不同的物理存储。

## 防漂移约束

永续会话架构必须遵守：

1. 当前任务执行与长期记忆维护必须保持职责分离。
2. 前台 Agent 必须自动获得始终需要知道的记忆，并能够发现和展开其他相关记忆。
3. Working Memory 与 Long-term Memory 必须清晰区分，后台 Agent 只作为 Long-term Memory 的维护者。
4. 后台记忆处理尚未完成记忆整理与 Memory Snapshot 发布时，Working Memory 必须无损进入下一个任务；发布完成后可以由目标语义一致、行为偏差不超过 \(\epsilon\) 的 Long-term Memory 与 Memory Snapshot 承接，无须等待后续检索优化或内部构建完成。
5. Long-term Memory 必须原子生效；只有已被可靠结算的内容才能退出 Working Memory。
6. 错误、冲突或过期的记忆必须能够依据原始证据得到纠正。

如果一个方案违反上述约束，或以后台处理结果直接替换尚未被可靠承接的历史，它就偏离了第二原则。
