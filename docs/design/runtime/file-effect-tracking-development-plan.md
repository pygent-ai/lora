# File Effect Tracking 实现方案

## 1. 背景与目标

当前 Lora 已经能通过 `EventStore` 记录 `tool.call`、`tool.result` 和部分 `file.read` 事件，并且 `EventStore` 已支持把 `file.read/file.write/file.edit/file.delete` 投影到 run/session 级 `file_events.jsonl`。

`pygent-ai==0.1.9` 下默认 `LoraAgent` 注册的工具白名单为：

- `bash`
- `read`
- `write`
- `edit`
- `glob`
- `grep`

文件变化不能只靠工具名或工具参数推断。模型可以通过 `bash` 执行脚本、重定向、删除、批量替换等命令，造成新增、修改、删除；同时，`write/edit/read` 的参数又能提供明确声明意图。如果二者都直接写事件，会对同一路径重复记录。

本方案引入 workspace 文件效果跟踪：在每次工具调用前后对 workspace 做快照，计算净变化，并与工具参数声明合并为一组去重后的 file effect。

第一版目标：

1. 默认 `LoraAgent` 的真实工具调用路径开启 workspace 文件效果跟踪。
2. 记录 workspace 内文件新增、编辑、删除的净效果：`file.write`、`file.edit`、`file.delete`。
3. 对 `read/write/edit` 的明确工具参数生成声明效果，并与 snapshot diff 合并。
4. 对常见 `bash` 读命令做 best-effort `file.read` inferred 记录，不承诺完整文件访问审计。
5. 工具抛错时仍记录已经发生的 workspace 文件净变化。
6. 不改变现有 `tool.call`、`tool.result`、`messages.jsonl` 语义。

非目标：

1. 不记录 A -> B -> A 这类最终无净变化的中间过程。
2. 不引入 OS 级审计，例如 strace、eBPF、ETW 或 USN Journal。
3. 不记录 workspace 外文件变化。
4. 不新增配置开关；`ToolInterceptor` 保留兼容默认值，但 `LoraAgent` 默认开启 tracking。

## 2. 当前状态

当前代码状态：

- `src/lora/trace.py` 的 `EventStore` 已支持所有 `file.*` 事件类型，并会把它们投影到 `file_events.jsonl`。
- `src/lora/tools.py` 中已有 `FileStateTracker`，用于读取去重和部分 `file.read` 事件记录。
- `src/lora/tools.py` 已实现 `FileEffectTracker`，支持 workspace 快照、工具参数声明效果、snapshot diff、效果合并和去重写入。
- `ToolInterceptor` 兼容旧的 `ToolInterceptor(store)` 调用，也支持 `workspace_root` 和 `track_file_effects=True` 开启文件效果跟踪。
- `src/lora/agent.py` 中 `LoraAgent.stream()` 已使用 `ToolInterceptor(EventStore(self.case_run_ref), workspace_root=self.workspace_root, track_file_effects=True)`，真实 agent 工具调用默认开启 tracking。

已有回归测试：

- `tests.unit.test_tools.FileEffectTrackerSpecTests`
- `tests.scenario.test_file_effect_tracking_flow.FileEffectTrackingScenarioTests`

这些测试当前应保持通过，用于防止文件效果跟踪语义回退。

## 3. 实现设计

### 3.1 数据模型

在 `src/lora/tools.py` 中新增内部 dataclass。

```python
@dataclass(slots=True)
class FileSnapshot:
    path: str
    exists: bool
    kind: Literal["file", "dir", "other", "missing"]
    size: int | None = None
    mtime_ns: int | None = None
    content_hash: str | None = None
```

```python
@dataclass(slots=True)
class FileEffect:
    type: Literal["file.read", "file.write", "file.edit", "file.delete"]
    path: str
    tool_call_id: str
    tool_name: str
    detected_by: list[Literal["tool_args", "snapshot_diff", "bash_command_parse"]]
    confidence: Literal["declared", "observed", "inferred"]
    before_hash: str | None = None
    after_hash: str | None = None
    before_exists: bool | None = None
    after_exists: bool | None = None

    def key(self) -> tuple[Any, ...]:
        ...
```

`FileEffect.key()` 用于 append 去重，至少包含：

- `tool_call_id`
- normalized `path`
- `type`
- `before_hash`
- `after_hash`
- `before_exists`
- `after_exists`

如果需要跨 run 去重，key 中可以额外包含 `store.case_run_ref.case_run_id`。

### 3.2 `FileEffectTracker`

在 `src/lora/tools.py` 中新增：

```python
class FileEffectTracker:
    def __init__(self, workspace_root: str | Path, store: EventStore):
        ...

    def snapshot_workspace(self) -> dict[str, FileSnapshot]:
        ...

    def declared_effects(
        self,
        tool_name: str,
        args: dict[str, Any],
        tool_call_id: str,
    ) -> list[FileEffect]:
        ...

    def observed_effects(
        self,
        before: dict[str, FileSnapshot],
        after: dict[str, FileSnapshot],
        *,
        tool_name: str,
        tool_call_id: str,
    ) -> list[FileEffect]:
        ...

    def merge_effects(
        self,
        declared: list[FileEffect],
        observed: list[FileEffect],
    ) -> list[FileEffect]:
        ...

    def append_effects(
        self,
        effects: list[FileEffect],
        *,
        turn_id: str | None,
    ) -> None:
        ...
```

`snapshot_workspace()`：

- 遍历 `workspace_root` 下文件。
- 忽略 `.git`、`.lora`、`.venv`、`__pycache__`、`.pytest_cache`、`sessions`。
- 第一版直接计算文件内容 SHA-256，优先保证正确性。
- 只需要记录文件；目录本身变化不产生 file effect。

`observed_effects()`：

- before 不存在、after 存在：生成 `file.write`。
- before 存在、after 不存在：生成 `file.delete`。
- before/after 都存在且 `content_hash` 不同：生成 `file.edit`。
- before/after 都不存在或 hash 相同：不生成事件。
- 输出按 normalized path 稳定排序，确保测试和 trace 可复现。

`declared_effects()`：

- `read`：从 `file_path`、`path` 或 `relative_path` 解析目标路径，生成 `file.read`，`detected_by=["tool_args"]`，`confidence="declared"`。
- `write`：解析目标路径，生成写入声明；最终类型可先用 `file.write`，如果 observed 为 `file.edit`，合并时以 observed 为准。
- `edit`：解析目标路径，生成 `file.edit` 声明。
- `bash`：只解析常见读命令，例如 `cat path`、`type path`、`Get-Content path`、`grep pattern path`，生成 `file.read`，`detected_by=["bash_command_parse"]`，`confidence="inferred"`。
- 声明路径必须 resolve 后位于 `workspace_root` 内；路径逃逸时抛 `ValueError`。

`merge_effects()`：

- 以 normalized path 合并 declared 和 observed。
- 同一路径存在 observed effect 时，以 observed 的 `type/hash/existence/confidence` 为准。
- 如果 declared 与 observed 合并，`detected_by` 保持稳定顺序：`["tool_args", "snapshot_diff"]`。
- 同一路径只有 declared read 或 bash inferred read 时保留该 `file.read`。
- 同一次工具调用内，同一路径最多输出一条写入类 effect。

`append_effects()`：

- 对每个 effect 调用 `EventStore.append(effect.type, actor="tool", payload=..., turn_id=turn_id)`。
- payload 必须包含 `path`、`tool_call_id`、`tool_name`、`detected_by`、`confidence`、`before_exists`、`after_exists`、`before_hash`、`after_hash`。
- 使用 `FileEffect.key()` 在当前 tracker 实例内避免重复 append。

### 3.3 `ToolInterceptor` 集成

扩展初始化接口：

```python
class ToolInterceptor:
    def __init__(
        self,
        store: EventStore,
        *,
        workspace_root: str | Path | None = None,
        track_file_effects: bool = False,
    ):
        ...
```

兼容要求：

- `ToolInterceptor(store)` 行为保持不变。
- 当 `track_file_effects=True` 且 `workspace_root` 非空时，创建 `FileEffectTracker`。
- 当 `track_file_effects=True` 但 `workspace_root is None` 时，应抛 `ValueError`，避免静默失效。

`call_tool()` 写入顺序必须为：

```text
append tool.call -> get tool_call_id
if tracking: before = snapshot_workspace()
if tracking: declared = declared_effects(name, args, tool_call_id)
execute tool
if tracking: after = snapshot_workspace()
if tracking: observed = observed_effects(before, after, tool_name=name, tool_call_id=tool_call_id)
if tracking: append merge_effects(declared, observed)
append tool.result
return ToolResult
```

异常路径必须保留同样的 file effect 写入：

```text
tool raises
  -> after = snapshot_workspace()
  -> observed = observed_effects(...)
  -> append merge_effects(declared, observed)
  -> append tool.result status=error
  -> return ToolResult(status="error")
```

### 3.4 `LoraAgent` 默认开启

在 `src/lora/agent.py` 的 `LoraAgent.stream()` 中，将当前 interceptor 初始化：

```python
interceptor = ToolInterceptor(EventStore(self.case_run_ref))
```

调整为：

```python
interceptor = ToolInterceptor(
    EventStore(self.case_run_ref),
    workspace_root=self.workspace_root,
    track_file_effects=True,
)
```

这样真实 agent 工具调用会默认记录 workspace 文件净效果，而单元测试或其他调用方仍可通过 `ToolInterceptor(store)` 保持旧行为。

## 4. 事件格式与兼容性

file effect 事件 payload 统一为：

```json
{
  "path": "E:\\Projects\\lora\\demo.txt",
  "tool_call_id": "evt_...",
  "tool_name": "bash",
  "detected_by": ["snapshot_diff"],
  "confidence": "observed",
  "before_hash": "...",
  "after_hash": "...",
  "before_exists": true,
  "after_exists": true
}
```

兼容性约束：

- 不新增投影文件，继续使用现有 `file_events.jsonl`。
- 不改变 `tool.call`、`tool.result`、`conversation.*`、`messages.jsonl` 的语义。
- 不改变 `FileStateTracker.read_text_file()` 行为。
- 未开启 `track_file_effects` 时，`ToolInterceptor` 的旧行为不变。

## 5. 测试与验收

必须通过的测试：

```powershell
uv run python -m unittest tests.unit.test_tools.ToolTests
uv run python -m unittest tests.unit.test_tools.FileEffectTrackerSpecTests
uv run python -m unittest tests.scenario.test_file_effect_tracking_flow
uv run python -m unittest discover -s tests
```

验收标准：

1. `from lora.tools import FileEffectTracker` 可导入。
2. `ToolInterceptor(store)` 兼容旧调用。
3. `ToolInterceptor(store, workspace_root=workspace, track_file_effects=True)` 支持新调用。
4. 任意工具调用造成 workspace 内文件新增时，写入一条 `file.write`。
5. 任意工具调用造成 workspace 内文件内容变化时，写入一条 `file.edit`。
6. 任意工具调用造成 workspace 内文件删除时，写入一条 `file.delete`。
7. 同一次工具调用内，同一路径最终最多一条 file effect。
8. 声明意图和实际 diff 指向同一路径时合并，不重复记录。
9. 工具异常时，已发生的净文件变化仍写入 trace，且在 `tool.result status=error` 前写入。
10. workspace 外文件变化不写入 file events；声明路径逃逸时拒绝。
11. 忽略目录中的变化不写入 file events。
12. `bash` 常见读命令可生成 inferred `file.read`，但仅作为 best-effort。
13. 真实 `LoraAgent` 默认工具调用路径会启用 tracking，并能在 run/session `file_events.jsonl` 中看到 file effects。
