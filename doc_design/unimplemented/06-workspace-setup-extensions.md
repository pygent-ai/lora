# 06 Workspace Setup Extensions

> 状态：已实现。本文保留为实现记录和后续扩展参考；下方“原始缺口”描述的是实现前状态。

## 背景

当前 `CaseManager.prepare_workspace()` 已支持多种 workspace setup action，并会记录 action 级别产物。

目标是为常见 case 准备动作提供稳定 schema，并确保所有动作可复现、可回放、可审计。

## 已实现状态

- 已支持 `copy_fixture`、`write_file`、`mkdir`、`delete_path` 和 gated `run_command`。
- setup action 会写入 `{run_dir}/workspace/setup_actions.jsonl`。
- `run_command` 默认禁用，必须显式设置 `workspace.allow_commands: true`。
- 所有路径解析复用 workspace 安全路径检查。

## 原始缺口

- 只支持 `copy_fixture`。
- 没有 setup action 级别的产物记录。
- 没有 `write_file`、`delete_path`、`mkdir`、`run_command` 等常用动作。
- 没有明确哪些 setup 动作允许修改 workspace。

## 实现方法

1. 定义 setup schema：

```yaml
workspace:
  setup:
    - type: copy_fixture
      from: examples/fixture.txt
      to: sandbox/fixture.txt
    - type: write_file
      path: sandbox/input.txt
      content: hello
    - type: mkdir
      path: sandbox/out
    - type: delete_path
      path: sandbox/stale.txt
```

2. 第一批支持类型：
   - `copy_fixture`：保留现有行为。
   - `write_file`：写 UTF-8 文本。
   - `mkdir`：创建目录。
   - `delete_path`：删除 workspace 内文件或目录。

3. 第二批可选类型：
   - `run_command`：默认不启用，除非 case 显式 `allow_commands: true`。
   - 命令执行必须记录 stdout/stderr/exit code。

4. 产物记录：

```text
{run_dir}/workspace/setup_actions.jsonl
```

每条 action 记录：

```json
{
  "type": "write_file",
  "path": "...",
  "status": "success",
  "started_at": "...",
  "finished_at": "..."
}
```

5. 实现结构：
   - 在 `CaseManager.prepare_workspace()` 中分发到私有方法。
   - 每个 action 先做路径安全检查。
   - setup 完成后再写 unchanged baseline。

## 验收标准

- `copy_fixture` 原有行为和测试保持不变。
- `write_file` 能创建目标文件并记录 setup action。
- `mkdir` 能创建目录并记录 setup action。
- `delete_path` 能删除文件/目录并记录 setup action。
- 未知 setup type 返回可读 `ValueError`。
- setup action 失败时，case run 不继续执行 agent，并保留失败原因。

## 测试建议

- `tests/unit/test_case_manager.py`：覆盖每个 setup type。
- `tests/scenario/test_cli_flow.py`：覆盖带多 setup action 的 case run。
- 增加失败场景：未知 type、路径逃逸、缺失 source。
