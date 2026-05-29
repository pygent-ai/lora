# 05 Test Generation and Regression Registration

> 状态：已实现。本文保留为实现记录和后续扩展参考；下方“原始缺口”描述的是实现前状态。

## 背景

事件类型中已经预留 `test.generated`，自优化设计文档中也规划了根据失败分析生成复现测试和维护 regression suite。当前代码已经实现 deterministic test generation 和 regression manifest 注册。

目标是先实现 deterministic 的“测试建议和注册”流程，再考虑模型生成测试。

## 已实现状态

- CLI 已提供 `lora test generate <session_id> <case_run_id>` 和 `lora test register <case_file>`。
- 已新增 `TestGenerator` 和 `RegressionRegistrar`。
- failed/error run 可生成 `.lora/sessions/{session_id}/generated_tests/{case_run_id}/generated_case.yaml` 和 `metadata.json`。
- `test.generated` 事件已写入原始 case run trace。
- `.lora/regression.json` 可通过注册 API 创建、去重和稳定排序。

## 原始缺口

- 没有 `lora test generate` 或类似命令。
- 没有 `TestGenerator` 模块。
- `analysis.json` 中的 `recommended_tests` 只是建议，没有落成 case 文件。
- `.lora/regression.json` 没有注册 API。
- `test.generated` 事件没有实际写入路径。

## 实现方法

1. 新增 CLI：

```powershell
lora test generate <session_id> <case_run_id>
lora test register <case_file>
```

2. 新增产物目录：

```text
.lora/sessions/{session_id}/generated_tests/{case_run_id}/
  generated_case.yaml
  metadata.json
```

3. `test generate` 第一版：
   - 读取原始 `case.yaml`、`verdict.json`、`analysis.json`。
   - 生成一个最小复现 case：
     - 复制原始 input。
     - 复制或收紧失败断言。
     - 保留必要 workspace setup。
   - 不调用模型，避免生成不可控测试。

4. `test register`：
   - 创建或更新 `.lora/regression.json`。
   - 用 normalized path 注册 case，避免重复。
   - 保留 manifest 排序稳定。

5. 事件记录：
   - 生成 case 后写 `test.generated`。
   - payload 包含 source `session_id`、source `case_run_id`、generated path、registered 状态。

6. 后续扩展：
   - 接入 `FailureAnalyzer` 的 root cause 类型，为不同失败类型生成不同模板。
   - 接入模型生成更细的 UT/ST，但必须落盘并经过人工或 gate 校验。

## 验收标准

- 对 failed run 执行 `lora test generate` 会生成 `generated_case.yaml`。
- generated case 能被 `lora case run` 正常加载和执行。
- `test.generated` 事件包含 source run 和 generated path。
- `lora test register generated_case.yaml` 能创建或更新 `.lora/regression.json`。
- 重复注册同一 case 不产生重复条目。
- 对 passed run 执行 generate 时返回 `skipped` 或可读错误。

## 测试建议

- `tests/unit/test_test_generation.py`：覆盖 case 生成、manifest 去重和稳定排序。
- `tests/scenario/test_generated_test_flow.py`：从失败 run 生成 case，再注册 regression manifest。
- 和 regression runner 集成后，增加 generated case 被 regression 执行的场景。
