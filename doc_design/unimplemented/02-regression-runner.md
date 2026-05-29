# 02 Regression Runner

> 状态：已实现。本文保留为实现记录和后续扩展参考；下方“原始缺口”描述的是实现前状态。

## 背景

CLI 已经有 `lora regression run`，事件类型也预留了 `regression.started` 和 `regression.finished`。当前代码已经会读取 `.lora/regression.json`、执行 manifest 中的 case suite，并在 manifest 缺失时返回 `skipped`。

目标是让 regression suite 可以从 manifest 读取 case 列表，逐个执行并生成可回放的汇总结果。

## 已实现状态

- `.lora/regression.json` 支持 `version`、`cases` 和 `fail_fast`。
- `lora regression run` 会执行 manifest 中的 case，并汇总 status、metrics 和 verdict。
- regression run 级别产物写入 `.lora/regressions/{regression_run_id}/`。
- 已写入 `regression.started` 和 `regression.finished` 事件。

## 原始缺口

- `.lora/regression.json` 没有 schema。
- `lora regression run` 不读取或执行 manifest 中的 case。
- 没有 regression run 级别的产物目录。
- 没有汇总每个 case run 的 status、metrics、verdict。
- 没有 `regression.started` / `regression.finished` 的实际写入路径。

## 实现方法

1. 定义 manifest schema：

```json
{
  "version": "1.0",
  "cases": [
    {"path": "cases/example.yaml", "session_mode": "new"}
  ]
}
```

2. 新增 regression 产物目录：

```text
.lora/regressions/{regression_run_id}/
  regression_config.json
  result.json
  case_runs.jsonl
```

3. 实现执行器：
   - 新增 `RegressionRunner`，接收 `RunConfig`、`SessionManager`、`CaseManager`、`AgentRuntimeAdapter`、`Evaluator`。
   - 按 manifest 顺序执行 case。
   - 每个 case 复用现有 `_case_run` 的核心逻辑，但应抽出可复用函数，避免 regression 直接调用 CLI handler。
   - 单个 case 失败不应中断整个 suite，除非 manifest 配置 `fail_fast: true`。

4. 事件记录：
   - 在每个 regression run 开始和结束时写入 `regression.started`、`regression.finished`。
   - 如果 regression 不绑定某个 case run，可创建一个特殊 case id，例如 `regression`，或把 regression 事件写入每个 case run 的 trace。优先选择独立 regression 产物目录，并在汇总中链接 case run。

5. CLI 返回：
   - 返回 `status`：全部 passed 为 `passed`；存在 failed 为 `failed`；存在 error 为 `error`。
   - 返回 `total`、`passed`、`failed`、`error`、`skipped` 和 `result_path`。

## 验收标准

- `.lora/regression.json` 不存在时仍返回 `skipped`。
- manifest 存在且包含 case 时，`lora regression run` 会执行所有 case。
- 生成 `.lora/regressions/{regression_run_id}/result.json` 和 `case_runs.jsonl`。
- 汇总结果包含每个 case 的 `session_id`、`case_run_id`、`status`、`metrics`、`verdict`。
- 任一 case failed 时 regression status 为 `failed`。
- 任一 case error 时 regression status 为 `error`，且错误详情可在对应 case run trace 中回放。

## 测试建议

- `tests/unit/test_regression.py`：覆盖 manifest 解析和汇总状态计算。
- `tests/scenario/test_cli_flow.py`：新增 regression manifest 场景，验证产物和返回 JSON。
- `tests/scenario/test_trace_event_store_flow.py`：覆盖 regression 事件类型的真实写入。
