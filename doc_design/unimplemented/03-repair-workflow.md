# 03 Repair Workflow

> 状态：已实现。本文保留为实现记录和后续扩展参考；下方“原始缺口”描述的是实现前状态。

## 背景

文档中规划了 repair plan、patch attempt 和 gate。当前代码已经实现 `lora repair plan/apply/gate`、repair 产物目录，以及 `repair.started`、`repair.patch_created`、`repair.finished` 事件写入。

目标是实现最小 repair 闭环：基于一次失败 case run 生成修复计划，允许产生 patch attempt，并通过 tests/regression gate 判断是否通过。

## 已实现状态

- CLI 已提供 `lora repair plan <session_id> <case_run_id>`、`lora repair apply <repair_plan_path>`、`lora repair gate <repair_attempt_id>`。
- 已定义 `RepairPlan` 和 gate result 产物。
- repair 产物写入 `.lora/sessions/{session_id}/repairs/{repair_id}/`。
- repair attempt 会捕获当前 git diff，并关联原始失败 case run。
- gate 支持配置命令和 regression manifest fallback。

## 原始缺口

- 没有 `lora repair ...` CLI。
- 没有 `RepairPlan`、`RepairAttempt` 或 gate result schema。
- 没有 repair 产物目录。
- 没有把 repair attempt 和原始失败 case run 建立关联。
- 没有自动或人工 patch 的执行边界。

## 实现方法

1. 新增 CLI 命令：

```powershell
lora repair plan <session_id> <case_run_id>
lora repair apply <repair_plan_path>
lora repair gate <repair_attempt_id>
```

2. 定义产物目录：

```text
.lora/sessions/{session_id}/repairs/{repair_id}/
  repair_plan.json
  attempts/{attempt_id}/
    patch.diff
    gate_result.json
    metadata.json
```

3. `repair plan`：
   - 读取失败 run 的 `verdict.json`、`analysis.json` 和 `events.jsonl`。
   - 输出规则化计划，包含 suspected files、failure summary、recommended checks。
   - 第一版可以不自动调用模型，只生成 deterministic plan。

4. `repair apply`：
   - 第一版支持人工 patch：如果用户已经修改工作区，记录当前 diff 为 `patch.diff`。
   - 后续再接入 patch agent。
   - 写入 `repair.patch_created` 事件。

5. `repair gate`：
   - 运行最相关测试，至少支持 manifest 中配置的命令。
   - 如果 regression runner 已实现，则优先运行 regression suite。
   - 输出 `gate_result.json`，包含命令、退出码、stdout/stderr 摘要和最终 status。

6. 事件记录：
   - plan 开始写 `repair.started`。
   - patch 产生写 `repair.patch_created`。
   - gate 完成写 `repair.finished`。

## 验收标准

- `lora repair plan <session_id> <case_run_id>` 能为 failed/error run 生成 `repair_plan.json`。
- 对 passed run 执行 repair plan 时返回可读错误或 `skipped`。
- repair plan 中包含原始 `session_id`、`case_run_id`、verdict failures/errors 和 recommended checks。
- `repair apply` 能保存当前 git diff 到 `patch.diff`。
- `repair gate` 能执行配置的测试命令并写入 `gate_result.json`。
- repair 相关事件能在 trace 或 session logs 中查询。

## 测试建议

- `tests/unit/test_repair.py`：覆盖 plan schema、passed run 跳过、gate status 计算。
- `tests/scenario/test_repair_flow.py`：构造失败 case，生成 plan，保存 patch，运行 gate。
- 在测试中避免真实模型调用，使用 deterministic failed case 和本地命令。
