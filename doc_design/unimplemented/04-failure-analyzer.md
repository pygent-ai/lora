# 04 Failure Analyzer

> 状态：已实现。本文保留为实现记录和后续扩展参考；下方“原始缺口”描述的是实现前状态。

## 背景

当前 `lora case analyze` 已经调用独立 `FailureAnalyzer`。CLI 只负责读取输入、调用 analyzer、写入 `analysis.json` 和 `analysis.created` 事件。

目标是让 CLI 只负责读取输入、调用 analyzer、写入结果；归因规则由独立模块维护和测试。

## 已实现状态

- 已新增 `src/lora/analysis.py`。
- `FailureAnalyzer` 已覆盖 runtime error、tool error、missing tool call、answer assertion、file mutation、turn/tool budget 和 unknown failure。
- `_case_analyze()` 已调用 `FailureAnalyzer().analyze(...)`。
- `repair` 和 `test_generation` 可以复用 `analysis.json` 中的 root cause 和 recommended tests。

## 原始缺口

- 没有 `src/lora/analysis.py` 或类似模块。
- `_root_causes()` 在 CLI 中，难以复用和扩展。
- root cause 类型较少，只区分运行错误和断言失败。
- 没有针对 tool failure、missing tool call、file mutation、max_turns/max_tool_calls 的细分归因。

## 实现方法

1. 新增模块：

```python
class FailureAnalyzer:
    def analyze(
        self,
        *,
        verdict: dict[str, Any],
        events: list[ContextEvent],
        run_dir: Path,
    ) -> AnalysisResult:
        ...
```

2. 定义输出 schema：

```json
{
  "status": "failed",
  "root_causes": [
    {
      "type": "ASSERTION_FAILED",
      "summary": "...",
      "evidence": ["..."],
      "suspected_modules": ["evaluation", "runtime"],
      "recommended_tests": [
        {"kind": "unit", "description": "..."}
      ]
    }
  ]
}
```

3. 规则拆分：
   - `runtime.error` -> `RUNTIME_ERROR`
   - `tool.result status=error` -> `TOOL_EXECUTION_ERROR`
   - `tool.required` failure -> `MISSING_TOOL_CALL`
   - `answer.contains` failure -> `ANSWER_ASSERTION_FAILED`
   - `files.unchanged` failure -> `UNEXPECTED_FILE_MUTATION`
   - `metrics.max_turns` / `metrics.max_tool_calls` -> `BUDGET_EXCEEDED`

4. CLI 调整：
   - `_case_analyze()` 读取 verdict/events 后调用 `FailureAnalyzer().analyze(...)`。
   - 删除或保留 `_root_causes()` 作为薄 wrapper，最终迁移测试到 analyzer。

5. 可扩展性：
   - 每条规则写成私有方法或策略列表。
   - 规则输出保持稳定，便于 repair/test generation 使用。

## 验收标准

- `lora case analyze` 的 JSON 结构保持兼容，仍写 `analysis.json`。
- CLI 中不再维护主要 root cause 判断逻辑。
- 不同 verdict failure 类型能产出不同 root cause type。
- tool error 的 evidence 包含对应 `tool.result` 错误 payload。
- file mutation 的 evidence 包含 changed file path。
- 原有 analyze 场景测试继续通过。

## 测试建议

- `tests/unit/test_analysis.py`：逐类覆盖 root cause 规则。
- `tests/scenario/test_cli_flow.py`：保持 `case analyze` 端到端产物校验。
- 为 `FailureAnalyzer` 添加空 verdict、passed verdict、error verdict 的边界测试。
