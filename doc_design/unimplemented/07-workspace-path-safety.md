# 07 Workspace Path Safety for Case Setup

> 状态：已实现。本文保留为实现记录和后续扩展参考；下方“原始缺口”描述的是实现前状态。

## 背景

开发指南已经指出：Agent 工具会限制 workspace，case setup 路径也需要同样约束。当前 `CaseManager` 已通过 `_resolve_workspace_path()` 检查最终路径必须位于 workspace root 下。

目标是防止 case setup 通过绝对路径或 `..` 修改 workspace 外的文件。

## 已实现状态

- `_resolve_workspace_path()` 已执行 `relative_to(workspace_root)` 检查。
- `copy_fixture.from`、`copy_fixture.to`、新增 setup action 路径和 `expect.files.unchanged` 都已限制在 workspace 内。
- 路径逃逸会在 agent 执行前抛出可读 `ValueError`。
- 已覆盖相对路径、绝对路径和 `..` 逃逸测试。

## 原始缺口

- `_resolve_under_root(root, value)` 没有 `relative_to(root)` 校验。
- `copy_fixture.from` 可以指向 workspace 外部文件。
- `copy_fixture.to` 可以写到 workspace 外部路径。
- `expect.files.unchanged` 的路径也需要同样的 workspace 约束。

## 实现方法

1. 新增安全路径函数：

```python
def _resolve_workspace_path(root: Path, value: Any, *, field_name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field_name} must stay within workspace_root: {value}") from exc
    return resolved
```

2. 替换调用点：
   - `_copy_fixture()` 的 `from` 和 `to`。
   - `_write_baseline()` 中 `expect.files.unchanged` 的路径。
   - 后续新增 workspace setup 类型统一复用该函数。

3. 错误语义：
   - 路径为空：`ValueError`。
   - 路径逃逸：`ValueError`，错误消息包含字段名。
   - source 不存在：仍保持 `FileNotFoundError`。

4. 文档同步：
   - 在 case schema 文档中明确 workspace setup 路径必须位于 `workspace_root` 内。
   - 如果未来需要允许外部只读 fixtures，应增加显式 allowlist，不要默认允许。

## 验收标准

- `workspace.setup[].from: ../outside.txt` 被拒绝。
- `workspace.setup[].to: ../outside.txt` 被拒绝。
- 绝对路径指向 workspace 外部时被拒绝。
- workspace 内的相对路径和绝对路径仍可正常使用。
- `expect.files.unchanged` 指向 workspace 外部时被拒绝。
- 错误发生在 agent 执行前，不产生误导性的 passed run。

## 测试建议

- `tests/unit/test_case_manager.py`：覆盖 `from`、`to`、`expect.files.unchanged` 的路径逃逸。
- `tests/scenario/test_cli_flow.py`：覆盖 CLI 返回退出码 `2` 和可读错误。
- 在 Windows 路径和 POSIX 风格 `..` 上都加测试样例。
