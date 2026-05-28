# Lora 项目开发指南

本文是基于当前仓库代码、测试、示例和既有 `doc_design` 文档整理的开发指南。目标是让新开发者可以直接理解项目边界、调用关系、每个公开接口和主要内部方法的职责，并能在不破坏 session、trace、case run 证据链的前提下继续开发。

## 1. 项目定位

Lora 是一个面向 Agent 自优化的本地运行框架。它不是单纯的聊天入口，而是围绕一次 Agent 执行建立可复现、可回放、可评估、可分析的闭环：

```text
case.yaml / chat input
  -> CLI 解析参数和配置
  -> SessionManager 创建或加载 session
  -> CaseManager 加载 case 并准备 workspace
  -> AgentRuntimeAdapter 驱动 Agent 执行
  -> LoraAgent 组合 prompt、调用模型和工具
  -> ToolInterceptor 记录工具调用
  -> EventStore 记录完整事件流
  -> Evaluator 生成 metrics/verdict
  -> analyze/optimize 输出失败归因
```

核心设计原则：

- `session_id` 表示一段可恢复的 Agent 上下文。
- `case_run_id` 表示一次具体执行的证据索引。
- `events.jsonl` 是事实来源，`messages.jsonl`、`tool_calls.jsonl`、`tool_results.jsonl`、`file_events.jsonl` 是投影。
- runtime 负责保留部分失败轨迹，evaluation 负责判定，analysis 负责归因。
- 所有路径在 schema 层尽量规范化为绝对路径。

## 2. 目录结构

```text
src/lora/
  __init__.py                 包级导出
  __main__.py                 python -m lora 入口
  cli.py                      CLI 编排层
  config.py                   配置加载和 YAML 子集解析
  schema/
    __init__.py               schema 导出
    models.py                 公共 dataclass 模型
  session.py                  session 和 case run 生命周期
  case.py                     case 加载、校验、workspace 准备
  runtime.py                  Agent 统一运行适配器
  agent.py                    默认 LoraAgent、prompt、工具注册
  tools.py                    工具拦截、文件读取去重、文件状态
  trace.py                    事件存储和 JSONL 投影
  evaluation.py               deterministic oracle 和指标

tests/
  unit/                       单元测试，覆盖 schema/config/session/case/runtime/tools/trace/evaluation
  scenario/                   CLI 与 trace 端到端场景

examples/
  react_agent_demo.py         pygent + DeepSeek ReAct demo

doc_design/
  development-guide.md        本文
  *.md                        已有架构设计文档
```

运行时会产生两套 session 目录：

```text
.lora/sessions/{session_id}/...
sessions/{session_id}/session.json
```

`.lora/sessions` 是 Lora 的结构化运行证据目录；`sessions` 用于兼容 pygent 风格的 session 落盘。

## 3. 关键运行产物

一次 case run 的主目录：

```text
.lora/sessions/{session_id}/cases/{case_id}/runs/{case_run_id}/
  case.yaml
  run_config.json
  run_metadata.json
  events.jsonl
  messages.jsonl
  tool_calls.jsonl
  tool_results.jsonl
  file_events.jsonl
  result.json
  metrics.json
  verdict.json
  analysis.json
  workspace/before_hashes.json
```

常见文件职责：

| 文件 | 生产者 | 说明 |
| --- | --- | --- |
| `session.json` | `SessionManager.save/create` | 可恢复的 Agent history 和 metadata |
| `metadata.json` | `SessionManager.create` | session 静态元信息 |
| `logs/session-events.jsonl` | `SessionManager` | session 级生命周期事件 |
| `run_config.json` | `SessionManager.start_case_run` | 本次 run 的配置快照 |
| `run_metadata.json` | `SessionManager.start_case_run/finish_case_run` | run 状态、开始和结束时间 |
| `events.jsonl` | `EventStore` | append-only 完整事件流 |
| `messages.jsonl` | `EventStore` | conversation 事件投影 |
| `tool_calls.jsonl` | `EventStore` | `tool.call` 事件投影 |
| `tool_results.jsonl` | `EventStore` | `tool.result` 事件投影 |
| `file_events.jsonl` | `EventStore` | `file.*` 事件投影 |
| `metrics.json` | `Evaluator` | 指标汇总 |
| `verdict.json` | `Evaluator` | pass/fail/error 判定与失败详情 |
| `analysis.json` | CLI analyze | 规则化 root cause |

## 4. CLI 调用链

入口在 `src/lora/__main__.py`：

```python
from .cli import main
raise SystemExit(main())
```

`src/lora/cli.py` 是最上层编排，不承载底层业务逻辑，只负责：

1. 构建 argparse 命令。
2. 调用 `load_run_config` 合并配置。
3. 创建 `SessionManager`、`CaseManager`、`AgentRuntimeAdapter`、`Evaluator`。
4. 将结果以 JSON 输出。
5. 在 CLI 边界捕获异常，返回退出码 `2`。

### 4.1 全局参数

| 参数 | 作用 |
| --- | --- |
| `--workspace-root` | 指定 workspace，默认当前目录或 `LORA_WORKSPACE_ROOT` |
| `--config` | 指定 `lora.yaml` |
| `--model` | 覆盖模型配置 |
| `--max-steps` | 覆盖 Agent 最大循环步数 |

### 4.2 session 命令

```bash
lora session create --case <case_id> [--mode e2e]
lora session show <session_id>
lora session resume <session_id>
```

调用关系：

```text
_session_create
  -> _manager
  -> load_run_config
  -> SessionManager.create
  -> SessionRef.to_dict

_session_show / resume
  -> _manager
  -> SessionManager.show
```

### 4.3 case 命令

```bash
lora case run <case_file> [--session <session_id>]
lora case analyze <session_id> <case_run_id>
lora case replay <session_id> <case_run_id>
```

`case run` 调用链：

```text
_case_run
  -> load_run_config(case_file/session/model/max_steps)
  -> SessionManager(config)
  -> CaseManager(workspace_root).load(case_file)
  -> _load_case_session 支持 case.session.mode=new/resume/shared/fork
  -> SessionManager.start_case_run
  -> copy case.yaml to run_dir
  -> CaseManager.prepare_workspace
  -> AgentRuntimeAdapter.run_case
  -> Evaluator.evaluate
  -> write result.json
  -> SessionManager.finish_case_run
```

`case replay` 调用链：

```text
_case_replay
  -> _find_case_run
  -> EventStore(ref).list_by_run
  -> event.to_dict
```

`case analyze` 调用链：

```text
_case_analyze
  -> _find_case_run
  -> read verdict.json or re-run Evaluator
  -> EventStore.list_by_run
  -> _root_causes
  -> write analysis.json
  -> EventStore.append("analysis.created")
```

### 4.4 optimize 命令

```bash
lora optimize <case_file>
```

调用链：

```text
_optimize
  -> _case_run
  -> if run.status == "passed": return analysis=None
  -> else _case_analyze
```

当前 optimize 是最小闭环：run + evaluate + rule-based analyze。还没有自动生成测试和自动修复。

### 4.5 chat 命令

```bash
lora chat
lora chat --message "hello"
lora chat --session <session_id> --message "continue"
lora chat --new
```

调用链：

```text
_chat
  -> load_run_config
  -> SessionManager(config)
  -> resolve session_id from --session/config or create chat session
  -> SessionManager.load
  -> SessionManager.start_case_run(session_id, "chat")
  -> EventStore.append("chat.started")
  -> AgentRuntimeAdapter.run_turn
  -> EventStore.append("chat.finished")
  -> SessionManager.finish_case_run
```

交互模式会循环读取 stdin，每个非空输入生成一个 `turn-{index:04d}`。

## 5. 配置模块 `config.py`

### 5.1 `load_run_config(...) -> RunConfig`

签名：

```python
def load_run_config(
    *,
    workspace_root: str | Path | None = None,
    config_file: str | Path | None = None,
    session_id: str | None = None,
    case_file: str | Path | None = None,
    model: str | None = None,
    max_steps: int | None = None,
) -> RunConfig
```

职责：

- 确定 `workspace_root`：参数优先，其次 `LORA_WORKSPACE_ROOT`，最后 `Path.cwd()`。
- 读取 `lora.yaml` 或指定 `config_file`。
- 确定 `lora_root`：配置 `lora_root` 优先，其次 `LORA_ROOT`，默认 `.lora`。
- 确定 `session_id`：参数优先，其次 `LORA_SESSION_ID`，最后配置 `session_id`。
- 确定 `model`：参数优先，其次 `LORA_MODEL`，最后配置 `model` 或 `runtime.model`。
- 确定 `max_steps`：参数优先，其次 `LORA_MAX_STEPS`，然后配置 `max_steps` 或 `runtime.max_steps`，默认 `8`。
- 返回 `RunConfig`，由 schema 层做路径绝对化和合法性检查。

注意：

- 当前 YAML 解析器是项目内实现的子集解析器，不支持完整 YAML 特性。
- `case_file` 只在参数非空时写入 `RunConfig.case_file`。

### 5.2 `load_mapping_file(path) -> dict[str, Any]`

读取 UTF-8 文本并调用 `_parse_yaml_subset`。主要用于 `lora.yaml` 和 `case.yaml`。

### 5.3 YAML 子集解析方法

| 方法 | 职责 |
| --- | --- |
| `_read_config(path)` | 安全读取配置文件，缺失时返回 `{}`，读失败包装为 `ValueError` |
| `_dig(data, dotted_key)` | 按点号路径读取嵌套配置，如 `runtime.model` |
| `_parse_yaml_subset(text)` | 去注释、去空行，解析顶层 mapping |
| `_parse_block(lines, index, indent)` | 根据当前行判断解析 map 或 list |
| `_parse_map(lines, index, indent)` | 解析相同缩进层级的 `key: value` 或嵌套块 |
| `_parse_list(lines, index, indent)` | 解析 `- item` 列表，支持列表项为 scalar 或 mapping |
| `_split_key_value(text)` | 拆分并校验 `key: value` |
| `_indent(line)` | 计算前导空格数 |
| `_parse_scalar(value)` | 将 `true/false/null/int/float/quoted string` 转成 Python 值 |

开发注意：

- 这个解析器不支持多行字符串、锚点、复杂引号转义、inline list/map。
- 如果 case schema 复杂化，优先引入标准 YAML 库，而不是继续扩展大量边界条件。

## 6. 公共模型 `schema/models.py`

schema 是跨模块契约层。除 CLI 外，各模块应尽量通过这些 dataclass 传递结构化数据。

### 6.1 辅助函数

| 方法 | 职责 |
| --- | --- |
| `_abs_path(value)` | `expanduser().resolve()`，返回绝对路径字符串 |
| `_require(value, field_name)` | 校验非空字符串，否则抛 `ValueError` |

### 6.2 `RunConfig`

字段：

```python
workspace_root: str
lora_root: str
session_id: str | None = None
case_file: str | None = None
model: str | None = None
max_steps: int = 8
```

方法：

- `__post_init__`：规范化 `workspace_root`、`lora_root`、`case_file`；校验 `max_steps > 0`。
- `to_dict()`：返回 `asdict(self)`。
- `from_dict(data)`：用 dict 反序列化。

使用位置：

- `config.load_run_config` 生产。
- `SessionManager` 持有。
- `AgentRuntimeAdapter`、`LoraAgent` 使用 `model/max_steps/workspace_root`。
- `run_config.json` 持久化。

### 6.3 `SessionRef`

字段：

```python
session_id: str
session_dir: str
workspace_root: str
```

方法：

- `__post_init__`：校验 `session_id`，规范化路径。
- `to_dict()` / `from_dict(data)`：序列化和反序列化。

使用位置：

- `SessionManager.create`、`SessionManager.fork` 返回。
- CLI 输出。

### 6.4 `CaseRunRef`

字段：

```python
session_id: str
case_id: str
case_run_id: str
run_dir: str
```

方法：

- `__post_init__`：校验三个 id，规范化 `run_dir`。
- `to_dict()` / `from_dict(data)`。

使用位置：

- `SessionManager.start_case_run` 返回。
- `EventStore` 初始化。
- `ToolContext`、`AgentRuntimeAdapter`、`Evaluator`、`CaseManager.prepare_workspace` 依赖。

### 6.5 `CaseRunResult`

字段：

```python
session_id: str
case_id: str
case_run_id: str
status: Literal["passed", "failed", "error", "skipped"]
final_answer: str = ""
error: str | None = None
event_count: int = 0
message_count: int = 0
```

方法：

- `__post_init__`：校验 id 和 status 枚举。
- `to_dict()` / `from_dict(data)`。

使用位置：

- `AgentRuntimeAdapter.run_case` 返回。
- CLI `_case_run` 写入 `result.json`。

### 6.6 `WorkspaceRef`

字段：

```python
workspace_root: str
case_run_id: str
baseline_path: str | None = None
```

方法：

- `__post_init__`：规范化路径，校验 `case_run_id`。
- `to_dict()`。

使用位置：

- `CaseManager.prepare_workspace` 返回，表示 workspace 准备和 baseline 文件位置。

### 6.7 `EvaluationResult`

字段：

```python
status: Literal["passed", "failed", "error"]
metrics: dict[str, Any] = {}
verdict: dict[str, Any] = {}
```

方法：

- `__post_init__`：校验 status、metrics、verdict 类型。
- `to_dict()`。

使用位置：

- `Evaluator.evaluate` 返回。
- CLI 根据 evaluation 覆盖非 error 的 runtime status。

### 6.8 `ContextEvent`

字段：

```python
id: str
session_id: str
case_id: str | None
case_run_id: str | None
turn_id: str | None
type: str
timestamp: str
actor: Literal["user", "assistant", "tool", "system"]
payload: dict[str, Any] = {}
```

方法：

- `__post_init__`：校验 id、session_id、type、timestamp、actor、payload。
- `to_dict()` / `from_dict(data)`。

使用位置：

- `EventStore.append/append_event/list_events`。
- 所有 trace 回放和投影。

事件命名约定：

- conversation：`conversation.user_message`、`conversation.assistant_message`、`conversation.tool_message`
- model：`model.request`、`model.response`
- tool：`tool.call`、`tool.result`
- file：`file.read`、`file.write`、`file.edit`、`file.delete`
- context：`context.projection_created`、`context.checkpoint`
- runtime：`runtime.error`
- case：`case.started`、`case.finished`
- prompt：`prompt.rendered`
- analysis/test/repair/regression：`analysis.created`、`test.generated`、`repair.*`、`regression.*`

`trace.py` 中的 `DESIGN_EVENT_TYPES` 对齐设计文档的必备事件清单，`LORA_EVENT_TYPES` 额外包含 chat 和 runtime error 等项目内事件。

### 6.9 `CaseDefinition`

字段：

```python
id: str
title: str
type: str
session: dict[str, Any] = {}
workspace: dict[str, Any] = {}
input: dict[str, Any] = {}
expect: dict[str, Any] = {}
metrics: dict[str, Any] = {}
```

方法：

- `__post_init__`：校验 `id/title/type` 和所有 dict section。
- `to_dict()`。
- `from_dict(data)`：提供默认 `title=id`、`type=e2e`，并复制各 section。

使用位置：

- `CaseManager.load` 生产。
- `AgentRuntimeAdapter.run_case` 读取 input。
- `Evaluator.evaluate` 读取 expect/metrics。

### 6.10 `SessionSpec`

字段：

```python
case_id: str
mode: str = "new"
session_id: str | None = None
source_session_id: str | None = None
```

方法：

- `__post_init__`：校验 `case_id` 和 `mode in {"new", "resume", "fork", "shared"}`。

使用位置：

- `SessionManager.load_or_create`。

### 6.11 `AgentSession`

字段：

```python
session_id: str
workspace_root: str
session_dir: str
created_at: str
updated_at: str
system_prompt: str = ""
history: list[dict[str, Any]] = []
metadata: dict[str, Any] = {}
```

方法：

- `__post_init__`：校验 `session_id`，规范化路径。
- `to_dict()`：额外添加 `version: "1.0"`。
- `from_dict(data)`：兼容缺失 `session_dir/system_prompt/history/metadata`。

使用位置：

- `SessionManager.create/load/save`。
- `RuntimeContext` 包装。
- 运行结束后持久化 history 和 metadata。

## 7. Session 模块 `session.py`

`SessionManager` 是 session 和 run 的生命周期管理器。它只操作本地文件系统，不直接运行 Agent。

### 7.1 初始化

```python
manager = SessionManager(config)
```

字段：

- `config`: `RunConfig`
- `workspace_root`: `Path(config.workspace_root)`
- `lora_root`: `Path(config.lora_root)`
- `sessions_root`: `{lora_root}/sessions`

### 7.2 `create(case_id, mode="e2e") -> SessionRef`

职责：

- 生成 session id：`{mode}-{slug(case_id)}-{yyyyMMdd-HHmmss}-{digest}`。
- 创建目录：`context/`、`state/`、`logs/`、`cases/`。
- 写 `.lora/sessions/{session_id}/session.json`。
- 写 `.lora/sessions/{session_id}/metadata.json`。
- 写兼容路径 `sessions/{session_id}/session.json`。
- 返回 `SessionRef`。

失败条件：

- session 目录已存在时 `mkdir(exist_ok=False)` 会失败。

### 7.3 `load(session_id) -> AgentSession`

职责：

- 定位 `.lora/sessions/{session_id}/session.json`。
- 文件不存在则抛 `FileNotFoundError`。
- 读取 JSON，并强制补入当前 `session_dir`。
- 返回 `AgentSession`。

### 7.4 `load_or_create(spec: SessionSpec) -> AgentSession`

行为：

- `resume/shared`：必须提供 `spec.session_id`，直接 `load`。
- `fork`：必须提供 `spec.source_session_id`，先 `fork` 再 `load`。
- `new`：创建新 session 后加载。

### 7.5 `fork(source_session_id) -> SessionRef`

职责：

- 校验源 session 目录存在。
- 使用源 `metadata.json` 的 `case_id` 创建新 fork session。
- 复制源 session 的 `session.json`、`context/`、`state/`。
- 修正新 session 的 `session_id/session_dir/updated_at`。
- 在 metadata 中写入 `forked_from`。

注意：

- 当前没有复制 `cases/` 和 `logs/`，fork 只继承上下文和状态。

### 7.6 `start_case_run(session_id, case_id, run_config=None) -> CaseRunRef`

职责：

- 确认 session 可加载。
- 生成 `case_run_id`：`run-{yyyyMMdd-HHmmss-ffffff}-{digest}`。
- 创建 run 目录：`.lora/sessions/{session_id}/cases/{case_id}/runs/{case_run_id}`。
- 写 `run_config.json`。
- 写 `run_metadata.json`，初始 `status=running`。
- 写 session 级事件 `case.started`。
- 返回 `CaseRunRef`。

### 7.7 `finish_case_run(case_run_ref, status) -> None`

职责：

- 校验 status 属于 `passed/failed/error/skipped`。
- 更新 `run_metadata.json` 的 `status/finished_at`。
- 更新 session `updated_at` 和 metadata：
  - `active_case_id`
  - `last_case_run_id`
  - `last_case_run_status`
- 保存 session。
- 写 session 级事件 `case.finished`。

### 7.8 `show(session_id) -> dict[str, Any]`

返回：

```python
{"session": session.to_dict(), "metadata": metadata}
```

### 7.9 `save(session) -> None`

职责：

- 更新时间。
- 调用 `_save_session` 同步写 `.lora/.../session.json` 和 `sessions/.../session.json`。

### 7.10 内部方法

| 方法 | 职责 |
| --- | --- |
| `_save_session(session)` | 写两个 session 持久化位置 |
| `_session_dir(session_id)` | 返回 `.lora/sessions/{session_id}` |
| `_pygent_session_path(session_id)` | 返回 `workspace_root/sessions/{session_id}/session.json` |
| `_new_session_id(case_id, mode)` | 基于时间和 hash 生成 session id |
| `_new_case_run_id(session_id, case_id)` | 基于时间和 hash 生成 run id |
| `_append_session_event(session_id, event_type, payload)` | 写 `logs/session-events.jsonl` |
| `_write_json(path, data)` | 创建父目录并格式化写 JSON |
| `_read_json(path)` | 读取 JSON |
| `_now()` | 返回 UTC ISO 时间 |
| `_slug(value)` | 将 case id 转成 URL/path 友好的小写短名 |

开发注意：

- 不要绕过 `start_case_run/finish_case_run` 手工创建 run，否则 metadata 和 session event 会缺失。
- 对 session history 的修改应通过 `SessionManager.save` 落盘。

## 8. Case 模块 `case.py`

`CaseManager` 负责将 YAML case 变成 `CaseDefinition`，并为 run 准备 workspace。

### 8.1 初始化

```python
case_manager = CaseManager(workspace_root)
```

`workspace_root` 会被 `expanduser().resolve()`。

### 8.2 `load(path) -> CaseDefinition`

调用链：

```text
load
  -> Path(path).resolve
  -> load_mapping_file
  -> CaseDefinition.from_dict
  -> validate
```

职责：

- 读取 case YAML。
- 应用 schema 默认值。
- 做业务校验。
- 返回 `CaseDefinition`。

### 8.3 `validate(case) -> None`

校验规则：

- `input` 必须定义 `messages`、`content` 或 `prompt`。
- 如果存在 `input.messages`：
  - 必须是非空 list。
  - 每项必须是 mapping。
  - `role` 只能是 `user` 或 `assistant`。
  - 必须有 `content`。
- `expect.tool_calls.required` 中每项必须有 `name`。
- `expect.files.unchanged` 如果存在，必须是 list。

### 8.4 `prepare_workspace(case, case_run_ref) -> WorkspaceRef`

职责：

- 读取 `case.workspace.setup`，默认空 list。
- 当前只支持 setup step：`type: copy_fixture`。
- 执行 fixture 拷贝。
- 对 `expect.files.unchanged` 中的文件写入 baseline hash。
- 返回 `WorkspaceRef`。

支持的 setup：

```yaml
workspace:
  setup:
    - type: copy_fixture
      from: fixtures/case-a
      to: work/case-a
```

### 8.5 `case_hash(case) -> str`

将 `case.to_dict()` 按 key 排序序列化为 JSON，并返回 SHA-256。用于未来 regression、缓存、稳定标识。

### 8.6 内部方法

| 方法 | 职责 |
| --- | --- |
| `_copy_fixture(step)` | 将 `from` 拷贝到 `to`，目录会先删除目标再复制 |
| `_write_baseline(case, case_run_ref)` | 写 `workspace/before_hashes.json` |
| `_required_tools(case)` | 返回 `expect.tool_calls.required` |
| `_resolve_under_root(root, value)` | 将相对路径解析到 workspace 下；当前不拒绝绝对路径 |
| `_file_snapshot(path)` | 返回 `{exists, content_hash, size}` |

开发注意：

- 新增 workspace setup 类型时，只扩展 `prepare_workspace` 和测试，不要把准备逻辑放进 CLI。
- 如果未来要限制绝对路径逃逸，需要加强 `_resolve_under_root`。

## 9. Trace 模块 `trace.py`

`EventStore` 是 case run 的事实存储。它写入主事件流，并根据事件类型同步写投影文件。

### 9.1 初始化

```python
store = EventStore(case_run_ref)
```

初始化路径：

- `events_path = run_dir/events.jsonl`
- `messages_path = run_dir/messages.jsonl`
- `tool_calls_path = run_dir/tool_calls.jsonl`
- `tool_results_path = run_dir/tool_results.jsonl`
- `file_events_path = run_dir/file_events.jsonl`

### 9.2 `append(event, *, actor=None, payload=None, turn_id=None) -> str`

支持两种调用：

```python
store.append(context_event)
store.append("tool.call", actor="assistant", payload={...}, turn_id="turn-0001")
```

职责：

- 将字符串事件转换为 `ContextEvent`。
- 校验 `ContextEvent` 的 run scope。
- 写入事件流和相关投影。
- 返回 event id。

### 9.3 `append_event(event) -> str`

`append` 的显式别名，只接受 `ContextEvent`。

### 9.4 `append_error(error, event_type="error", actor="system", payload=None, turn_id=None) -> str`

职责：

- 将异常或字符串转成标准 error payload。
- 写入指定事件类型，默认 `error`。

payload 包含：

```python
{"error": "...", "error_type": "..."}
```

### 9.5 查询方法

| 方法 | 返回 |
| --- | --- |
| `list_by_run(session_id=None, case_run_id=None)` | 当前或指定 run 的事件 |
| `list_by_session(session_id=None)` | 当前或指定 session 的事件 |
| `list_by_case(case_id=None)` | 当前或指定 case 的事件 |
| `list_by_turn(turn_id)` | 指定 turn 的事件 |
| `list_events()` | 当前 `events.jsonl` 全部事件 |
| `iter_jsonl(path)` | 静态生成器，逐行读取 JSONL；缺失文件返回空迭代 |

### 9.6 内部写入和投影

| 方法 | 职责 |
| --- | --- |
| `_append_event(event)` | 写主事件，并根据 type 写投影 |
| `_coerce_event(...)` | 字符串事件转 `ContextEvent` |
| `_validate_event_scope(event)` | 确认 event 的 session/case/run 与当前 store 一致 |
| `_append_jsonl(path, data)` | append 写 JSONL |
| `_tool_call_record(event)` | 从 `tool.call` 生成投影行 |
| `_tool_result_record(event)` | 从 `tool.result` 生成投影行 |
| `_file_event_record(event)` | 从 `file.*` 生成投影行 |

投影规则：

- `event.type.startswith("conversation.")` -> `messages.jsonl`
- `event.type == "tool.call"` -> `tool_calls.jsonl`
- `event.type == "tool.result"` -> `tool_results.jsonl`
- `event.type.startswith("file.")` -> `file_events.jsonl`

开发注意：

- 新事件类型应优先写入 `events.jsonl`，投影只是加速读取和分析。
- append-only 是当前系统可回放性的基础，不要覆写已有 JSONL。

## 10. Tools 模块 `tools.py`

该模块负责工具调用包裹、文件读取去重和文件状态维护。

### 10.1 常量

| 常量 | 用途 |
| --- | --- |
| `FILE_UNCHANGED_STUB` | 完全重复读取同一文件版本时返回的固定提示 |
| `FILE_RANGE_CONTAINED_STUB` | 请求范围已被此前读取覆盖时返回 |
| `FILE_RANGE_OVERLAP_STUB` | 请求范围部分重叠时返回 |

### 10.2 `ReadRange`

字段：

```python
unit: Literal["line", "full"] = "full"
start: int = 1
end: int | Literal["EOF"] = "EOF"
```

方法：

- `covers(other)`：当前 range 是否完全覆盖另一个 range。`full` 覆盖所有。
- `overlaps(other)`：两个 range 是否重叠。只要任一为 `full` 就视为重叠。

### 10.3 `ReadDedupDecision`

字段：

```python
action: Literal["read", "stub", "partial"]
reason: Literal["none", "exact", "contained", "overlap"]
content: str | None = None
previous_event_id: str | None = None
```

表示一次读取是否应该真实读取、返回 stub，或未来支持只返回未读部分。

### 10.4 `ToolContext`

字段：

```python
case_run_ref: CaseRunRef
turn_id: str | None = None
```

用于把工具调用关联到当前 run 和 turn。

### 10.5 `ToolResult`

字段：

```python
status: Literal["success", "error", "stubbed", "partial"]
result: Any = None
error: str | None = None
tool_call_id: str | None = None
```

方法：

- `to_dict()`：序列化工具结果。

### 10.6 `FileStateTracker`

初始化：

```python
tracker = FileStateTracker(session_state_dir)
```

路径：

- `file_state_path = session_state_dir/file_state.json`
- `read_state_path = session_state_dir/read_state.json`

#### `record_read(path, content_hash, read_range, event_id) -> None`

职责：

- 将路径规范化为绝对路径。
- 在 `read_state.json` 中记录相同 content hash 下的读取 range。
- 在 `file_state.json` 中记录文件存在、hash、最后读取事件和读取 ranges。

#### `should_stub_read(path, content_hash, read_range, dedup_level="contained") -> ReadDedupDecision`

行为：

- 只比较相同路径、相同 `content_hash` 的历史读取。
- exact：同 unit/start/end，返回 `stub/exact`。
- contained：历史 range 覆盖当前 range，返回 `stub/contained`。
- overlap：当 `dedup_level == "overlap"` 且范围重叠，返回 `partial/overlap`。
- 否则返回 `read/none`。

#### `read_text_file(path, *, offset=None, limit=None, dedup_level="contained", event_store=None, turn_id=None) -> dict`

调用链：

```text
read_text_file
  -> read full text
  -> sha256 content
  -> _line_range
  -> should_stub_read
  -> if stub: return stub payload
  -> _select_lines
  -> EventStore.append("file.read") if store exists
  -> record_read
  -> return content/hash/range
```

返回示例：

```python
{"status": "success", "content": "...", "content_hash": "...", "range": {...}}
{"status": "stubbed", "content": "...", "dedup": "contained"}
```

注意：

- 即使只读一个范围，当前实现也先读取完整文件计算 hash。
- `dedup_level="contained"` 不会返回 partial，只会对被覆盖的范围 stub。

#### 内部方法

| 方法 | 职责 |
| --- | --- |
| `_read_json(path)` | 文件不存在返回 `{}` |
| `_write_json(path, data)` | 创建父目录并格式化写 JSON |

### 10.7 `ToolInterceptor`

初始化：

```python
interceptor = ToolInterceptor(EventStore(case_run_ref))
```

#### `call_tool(name, args, ctx, tool) -> ToolResult`

调用链：

```text
call_tool
  -> EventStore.append("tool.call", actor="assistant")
  -> tool(**args)
  -> on exception: EventStore.append("tool.result", status="error")
  -> on success: infer status from result.status if stubbed/partial
  -> EventStore.append("tool.result", actor="tool")
  -> return ToolResult
```

开发注意：

- 所有模型触发的工具调用都应该经过 `ToolInterceptor`。
- 真正的工具函数只负责业务结果，记录、错误包装、trace 写入都在 interceptor 做。

### 10.8 其他内部方法

| 方法 | 职责 |
| --- | --- |
| `_normalize(path)` | 规范化为绝对路径字符串 |
| `_line_range(content, offset, limit)` | 根据行偏移和限制生成 `ReadRange` |
| `_select_lines(content, read_range)` | 从文本中选择 range 内容 |
| `_end_value(value)` | 将 `EOF` 视为极大值用于比较 |
| `_now()` | UTC ISO 时间 |

## 11. Runtime 模块 `runtime.py`

runtime 的目标是让不同 Agent 实现都能接入统一执行、事件记录和 session 保存流程。

### 11.1 `RuntimeMessage`

字段：

```python
role: str
content: str
type: str | None = None
payload: dict[str, Any] | None = None
```

表示标准化后的 Agent 输出。

### 11.2 `RuntimeContext`

初始化：

```python
context = RuntimeContext(session)
```

字段：

- `session`: `AgentSession`
- `session_id`: `session.session_id`
- `system_prompt`: `session.system_prompt`
- `history`: `session.history`

方法：

- `add_message(message)`：将 dict 或对象标准化后 append 到 history。
- `last_message`：返回最后一条 history，空时返回 `None`。

### 11.3 `EchoAgent`

方法：

```python
async def stream(self, context: RuntimeContext, max_steps: int = 8)
```

行为：

- 找到最后一条 user message。
- yield 一条 assistant message：`Echo: {content}`。

当前默认 `AgentRuntimeAdapter` 实际会创建 `LoraAgent`，但测试里也使用 Echo 风格和自定义 agent 验证适配能力。

### 11.4 `AgentRuntimeAdapter`

初始化：

```python
adapter = AgentRuntimeAdapter(agent=None, config=config, session_manager=manager)
```

行为：

- 如果 `agent is None`，延迟导入并创建 `LoraAgent(config)`。
- 保存 `config` 和 `session_manager`。

#### `run_turn(session, user_input, case_run_ref, turn_id=None) -> dict`

用于 chat 单轮。

调用链：

```text
run_turn
  -> EventStore(case_run_ref)
  -> RuntimeContext(session)
  -> resolve turn_id
  -> _start_agent_run(agent, case_run_ref, turn_id)
  -> context.add_message(user)
  -> store.append("conversation.user_message")
  -> async for raw_output in _stream_agent
       -> _normalize_output
       -> context.add_message
       -> store.append(event_type)
       -> collect assistant final_parts
  -> except: status="error"; store.append("runtime.error")
  -> finally: update session.history/metadata; SessionManager.save
  -> return dict
```

返回字段：

```python
session_id
case_id
case_run_id
turn_id
status
final_answer
error
message_count
```

#### `run_case(session, case, case_run_ref) -> CaseRunResult`

用于 case run。

调用链：

```text
run_case
  -> EventStore.append("case.started")
  -> _start_agent_run(agent, case_run_ref, "turn-0001")
  -> _case_messages(case)
  -> append each user message to context and events
  -> append model.request
  -> _stream_agent(context, latest_user_content)
  -> normalize and record assistant/tool outputs
  -> except: status="error"; append runtime.error
  -> finally:
       session.history = context.history 或 original_history + 本轮 history
       update session.metadata
       session_manager.save
       append model.response
       append case.finished
       append context.checkpoint
  -> count events
  -> return CaseRunResult
```

注意：

- runtime status 只表示执行是否出错；case 断言失败由 `Evaluator` 后续覆盖为 failed。
- 即使 agent 抛异常，partial assistant output 也会保留。
- `case.session.carry_context: false` 时，Agent 本轮看不到旧 history；运行结束后，本轮增量会追加回原 session history。

#### `_stream_agent(context, latest_user_input)`

兼容两类 Agent：

1. 暴露 `stream(context, max_steps=...)` 或 `stream(latest_user_input, max_steps=...)`。
2. 暴露 `run(latest_user_input, max_steps=...)`，可以是 sync 或 async。

返回逻辑：

- async iterator：逐条 yield。
- sync iterator：逐条 yield。
- run 返回值：包装为 assistant message。

### 11.5 Runtime 辅助方法

| 方法 | 职责 |
| --- | --- |
| `_case_messages(case)` | 从 `input.messages` 或 `input.content/prompt` 提取 user messages |
| `_latest_user_content(case)` | 返回最后一条 user content |
| `_normalize_output(output)` | 将 dict、`RuntimeMessage` 或对象转成 `RuntimeMessage` |
| `_message_to_history(message)` | 将输入统一成 history dict |
| `_event_type_for_role(role)` | role 到 conversation event type |
| `_actor_for_role(role)` | role 到 event actor |
| `_value(value)` | 兼容 pygent `.data` 包装 |
| `_stringify(value)` | 将非字符串内容 JSON 化或转字符串 |
| `_start_agent_run(agent, case_run_ref, turn_id)` | 如果 agent 有 `start_run`，执行它 |

开发注意：

- 新 Agent 最少实现 `stream` 或 `run`。
- 如果需要工具、prompt、trace 初始化，推荐实现 `start_run(case_run_ref, turn_id)`。
- Agent 输出的 dict 最好包含 `role/content/type/payload`，否则 runtime 会按 role 默认推导事件类型。

## 12. Agent 模块 `agent.py`

`agent.py` 包含当前默认 Agent、prompt 模块系统和三个工具。

### 12.1 `PromptModule`

字段：

```python
id: str
type: Literal["system", "project", "runtime", "tool", "memory"]
cache_scope: Literal["global", "project", "session", "turn"]
order: int
render: Callable[[PromptRenderContext], str | None]
```

属性：

- `version_hash`：基于 `id/type/cache_scope/order` 的短 hash 字符串。

注意：

- 当前 `version_hash` 使用 Python 内置 `hash`，跨进程不保证稳定；如果 prompt cache 需要强一致，应改为 SHA-256。

### 12.2 `PromptRenderContext`

字段：

```python
session_id: str
workspace_root: Path
session_dir: Path
turn_id: str | None
projection: dict[str, Any]
tool_names: list[str]
```

用于 prompt module 渲染。

### 12.3 `PromptRegistry`

初始化时注册默认模块：

| id | type | cache_scope | order | 内容 |
| --- | --- | --- | --- | --- |
| `system.identity` | system | global | 10 | Lora 身份和回答语言 |
| `system.tool_policy` | system | global | 20 | 工具调用和 prompt injection 提醒 |
| `runtime.boundary_note` | runtime | turn | 100 | UTC 时间、workspace、session、turn |
| `project.file_status` | project | session | 110 | 文件读取状态 |
| `tool.available` | tool | turn | 120 | 当前工具名 |
| `memory.recent_projection` | memory | turn | 130 | 最近历史摘要 |

方法：

- `resolve()`：按 `order` 排序返回模块列表。

### 12.4 `PromptComposer`

初始化：

```python
composer = PromptComposer(registry=None)
```

#### `compose(ctx) -> tuple[str, list[dict[str, Any]]]`

行为：

- 从 registry 获取模块。
- `cache_scope == "global"` 的模块先渲染。
- 如果存在动态模块，插入边界常量 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`。
- 再渲染非 global 模块。
- 返回 prompt 文本和 rendered module 元数据。

module metadata 包含：

```python
id
type
cache_scope
version_hash
```

### 12.5 `AgentContextManager`

初始化：

```python
manager = AgentContextManager(session_dir=..., workspace_root=..., store=...)
```

字段：

- `session_dir`
- `workspace_root`
- `store`
- `file_state = FileStateTracker(session_dir / "state")`
- `prompt_composer = PromptComposer()`

#### `projection(history, limit=8) -> dict`

返回：

```python
{
  "recent_messages": [{"role": ..., "content": first_1200_chars}, ...],
  "file_status": self.file_status(),
}
```

#### `compose_prompt(runtime_context, turn_id, tool_names) -> str`

调用链：

```text
compose_prompt
  -> AgentContextManager.projection
  -> if store exists: append context.projection_created
  -> PromptRenderContext
  -> PromptComposer.compose
  -> if store exists: append prompt.rendered
  -> return prompt
```

`prompt.rendered` payload 包含：

- `module_ids`
- `modules`
- `prompt_hash`
- `dynamic_inputs`

#### `file_status() -> list[dict]`

读取 `state/file_state.json`，输出：

```python
{"path": ..., "status": "read" | "deleted", "content_hash": ...}
```

### 12.6 内置工具

#### `ListFilesTool`

继承 `pygent.module.tool.BaseTool`。

方法：

- `forward(relative_path=".") -> dict`：
  - 解析 workspace 内安全路径。
  - 路径不存在抛 `FileNotFoundError`。
  - 非目录抛 `NotADirectoryError`。
  - 返回 `{path, entries}`。
- `_safe_path(relative_path) -> Path`：
  - 确保目标不逃逸 `workspace_root`。

#### `ReadTextFileTool`

方法：

- `forward(relative_path, offset=None, limit=None) -> dict`：
  - 解析安全路径。
  - 调用 `AgentContextManager.file_state.read_text_file`。
  - 使用 `dedup_level="contained"`。
  - 传入 event store 和 turn id。
- `_safe_path(relative_path) -> Path`：
  - 同样限制 workspace 内。

#### `AddNumbersTool`

方法：

- `forward(a, b) -> float`：返回 `a + b`。

### 12.7 `LoraAgent`

初始化：

```python
agent = LoraAgent(config)
```

职责：

- 保存 `RunConfig`。
- 加载 workspace `.env`。
- 读取 `DEEPSEEK_API_KEY`。
- 确定模型：
  - `config.model`
  - `DEEPSEEK_MODEL`
  - 默认 `deepseek-chat`
- 确定 base URL：`DEEPSEEK_BASE_URL` 或 `https://api.deepseek.com`。
- 如果有 API key，创建 `AsyncRequestsClient`；否则 `llm=None`。
- 初始化工具管理器。

#### `start_run(case_run_ref, turn_id) -> None`

职责：

- 保存当前 run 和 turn。
- 根据 run 目录向上查找 session 目录。
- 创建 `AgentContextManager`。
- 重建 `ToolManager` 和 `_tools`。
- 注册：
  - `list_files`
  - `read_text_file`
  - `add_numbers`

#### `tools_param() -> list[dict]`

将 pygent tool manager 的 OpenAI function schema 包装为：

```python
{"type": "function", "function": function}
```

#### `stream(context, max_steps=8) -> AsyncIterator[dict]`

调用链：

```text
stream
  -> require start_run already called
  -> AgentContextManager.compose_prompt
  -> if llm is None:
       yield assistant fallback message
       return
  -> BaseContext(system_prompt)
  -> convert runtime history to pygent messages
  -> ToolInterceptor(EventStore)
  -> for max_steps:
       stream llm chunks
       collect assistant text
       yield assistant message
       inspect tool_calls from last_message
       if no tool_calls: return
       for each tool_call:
         validate tool name
         ToolInterceptor.call_tool
         add ToolMessage to pygent context
         yield tool message
  -> raise RuntimeError when max_steps exhausted
```

无 API key 时返回固定提示：

```text
Lora agent is wired into chat, but DEEPSEEK_API_KEY is not configured for a model call.
```

#### `_register_tool(tool) -> None`

同时注册到 `ToolManager` 和本地 `_tools` dict。

### 12.8 Agent 辅助方法

| 方法 | 职责 |
| --- | --- |
| `_render_file_status(ctx)` | 渲染文件读取状态 prompt section |
| `_render_projection(ctx)` | 渲染最近历史 prompt section |
| `_to_pygent_message(message)` | runtime history dict 转 pygent `UserMessage/AssistantMessage/ToolMessage` |
| `_message_tool_calls(message)` | 兼容 pygent `.data` 结构读取 tool calls |
| `_message_content(message)` | 兼容 pygent `.data` 结构读取 content |
| `_hash_text(text)` | SHA-256 prompt hash |
| `_load_env_file(path)` | 简易 `.env` loader，不覆盖已有环境变量 |
| `_session_dir_for_run(run_dir)` | 从 run_dir 向上查找包含 `session.json` 的 session 根目录 |

开发注意：

- 新工具必须继承 `BaseTool`，并通过 `_register_tool` 注册。
- 工具 `forward` 不要自己写 trace，统一由 `ToolInterceptor` 记录。
- 文件工具必须使用 `_safe_path`，避免模型读取 workspace 外路径。

## 13. Evaluation 模块 `evaluation.py`

`Evaluator` 是 deterministic oracle。它不运行 Agent，只读取 case 和 run 产物判定结果。

### 13.1 `Evaluator.evaluate(case, case_run_ref) -> EvaluationResult`

调用链：

```text
evaluate
  -> EventStore(case_run_ref).list_by_run()
  -> collect runtime.error/error events
  -> _final_answer(events)
  -> _tool_call_names(run_dir)
  -> check expect.answer.contains
  -> check expect.tool_calls.required
  -> _file_failures
  -> check metrics.max_turns
  -> check metrics.max_tool_calls
  -> build metrics
  -> status = error if errors else failed if failures else passed
  -> write metrics.json/verdict.json
  -> return EvaluationResult
```

支持的断言：

```yaml
expect:
  answer:
    contains:
      - token
  tool_calls:
    required:
      - name: read_text_file
  files:
    unchanged:
      - README.md
metrics:
  max_turns: 8
  max_tool_calls: 6
```

metrics 输出：

```python
event_count
message_count
tool_call_count
turn_count
final_answer_length
```

verdict 输出：

```python
status
final_answer
failures
errors
```

### 13.2 内部方法

| 方法 | 职责 |
| --- | --- |
| `_final_answer(events)` | 拼接所有 assistant message content |
| `_tool_call_names(run_dir)` | 从 `tool_calls.jsonl` 提取 tool name 序列 |
| `_file_failures(case, run_dir)` | 对比 baseline 和当前文件快照 |
| `_file_snapshot(path)` | 返回 `{exists, content_hash, size}` |
| `_write_json(path, data)` | 写格式化 JSON |

开发注意：

- `runtime.error` 会让 status 直接变成 `error`。
- evaluation 不应修改 session history。
- 新增 expect 类型时，应同步增加单元测试和场景测试。

## 14. 包导出

`src/lora/__init__.py` 导出：

```python
CaseManager
Evaluator
FileStateTracker
SessionManager
ToolInterceptor
load_run_config
```

`src/lora/schema/__init__.py` 导出：

```python
AgentSession
CaseDefinition
CaseRunRef
CaseRunResult
ContextEvent
EvaluationResult
RunConfig
SessionRef
SessionSpec
WorkspaceRef
```

开发注意：

- 公共 API 新增后，如果希望使用方直接 `from lora import ...` 或 `from lora.schema import ...`，需要同步更新 `__all__`。

## 15. 测试结构和覆盖点

当前测试使用 `unittest`。

### 15.1 单元测试

| 文件 | 覆盖 |
| --- | --- |
| `test_config.py` | 配置合并、环境变量覆盖、YAML 子集解析 |
| `test_schema.py` | 路径规范化、ref round-trip、事件校验、case 默认值 |
| `test_session_manager.py` | 创建/加载 session、多 run、finish metadata、fork |
| `test_case_manager.py` | case 加载校验、hash、fixture copy、baseline |
| `test_runtime_adapter.py` | run_case 事件记录、失败保留、run_turn chat |
| `test_tools.py` | 文件读取去重、hash 变化、工具拦截成功/失败 |
| `test_trace.py` | 事件 append、scope 校验、投影、查询、错误事件 |
| `test_evaluation.py` | answer/tool/file 断言、失败详情 |

### 15.2 场景测试

| 文件 | 覆盖 |
| --- | --- |
| `test_cli_flow.py` | `session create/show`、`case run/replay`、`optimize`、`chat --message` |
| `test_trace_event_store_flow.py` | append-only trace 和所有投影文件 |

### 15.3 推荐测试命令

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

如果需要直接跑 CLI：

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m lora --workspace-root . chat --message "hello"
```

## 16. 常见开发任务

### 16.1 新增 CLI 命令

步骤：

1. 在 `build_parser` 中添加 subparser。
2. 将 handler 设置为私有函数，例如 `_repair_plan`。
3. handler 中只做编排和 JSON 输出，不写底层逻辑。
4. 复用 `load_run_config`、`SessionManager`、`EventStore`。
5. 增加 scenario test，验证命令退出码和产物。

### 16.2 新增 case 断言

步骤：

1. 在 case YAML 中设计 `expect` 或 `metrics` 字段。
2. 在 `CaseManager.validate` 中增加 schema 校验。
3. 在 `Evaluator.evaluate` 中增加断言逻辑。
4. verdict failure 要包含：
   - `type`
   - `message`
   - `expected`
   - 必要时包含 `actual` 和证据路径。
5. 增加 unit test 和至少一个 scenario。

### 16.3 新增工具

步骤：

1. 在 `agent.py` 新增 `BaseTool` 子类。
2. 构造函数中定义 name、description、category、parameters 描述。
3. `forward` 只实现业务逻辑。
4. 如有文件访问，使用 workspace 安全路径检查。
5. 在 `LoraAgent.start_run` 中 `_register_tool`。
6. 确认工具调用都通过 `ToolInterceptor.call_tool`。
7. 增加工具单测和 runtime 场景测试。

### 16.4 接入新的 Agent 实现

最小要求：

```python
class MyAgent:
    async def stream(self, context: RuntimeContext, max_steps: int = 8):
        yield {"role": "assistant", "content": "..."}
```

可选要求：

```python
def start_run(self, case_run_ref: CaseRunRef, turn_id: str | None) -> None:
    ...
```

如果 agent 只支持 `run(prompt, max_steps=...)`，`AgentRuntimeAdapter` 也可以兼容，但无法天然记录中间 tool message。

### 16.5 新增事件类型

步骤：

1. 确定事件 payload schema。
2. 通过 `EventStore.append("event.type", actor=..., payload=..., turn_id=...)` 写主事件。
3. 如果需要高频查询，在 `EventStore._append_event` 中增加投影文件。
4. 增加 trace unit test。

### 16.6 新增 workspace setup 类型

步骤：

1. 在 `CaseManager.validate` 中校验字段。
2. 在 `prepare_workspace` 中分发新类型。
3. 新增私有方法处理具体逻辑。
4. 产物必须记录到 run_dir，便于 replay 和分析。
5. 增加 `test_case_manager.py` 覆盖。

## 17. 现有边界和风险

| 区域 | 当前状态 | 后续建议 |
| --- | --- | --- |
| YAML 解析 | 自研子集 | case 复杂后引入 PyYAML 或 ruamel.yaml |
| `PromptModule.version_hash` | 使用 Python `hash` | 改为稳定 hash |
| 文件路径安全 | Agent 工具限制 workspace，Case fixture 解析未强制限制绝对路径 | 对 case setup 加路径逃逸检查 |
| regression | CLI 仅返回 skipped | 后续实现 manifest 和执行器 |
| analysis | 规则化 root cause | 后续抽出独立 `FailureAnalyzer` |
| repair | 未实现 | 后续增加 plan/apply/gate |
| prompt 落盘 | 只记录 prompt hash 和 module 元数据 | 如需 debug，可保存 rendered prompt 文本 |

## 18. 推荐扩展路线

短期：

1. 补 README。
2. 补齐 regression manifest 读取和执行。
3. 将 `_root_causes` 从 CLI 抽到独立 analyzer 模块。
4. 为 prompt rendered 增加文本落盘或可配置开关。

中期：

1. 支持完整 YAML。
2. 增加更多工具和文件写入追踪。
3. 实现 context checkpoint/projection 的真实恢复能力。
4. 增加模型请求和响应的详细 token/耗时指标。

长期：

1. 自动生成失败复现测试。
2. repair plan + patch attempt + gate。
3. 多 session 对比和非确定性检测。
4. 面向不同 Agent backend 的 adapter registry。

## 19. 开发守则

- 不要绕过 `SessionManager` 创建 run。
- 不要绕过 `EventStore` 写 trace。
- 不要让工具自己吞掉异常而不记录，异常应由 `ToolInterceptor` 包装。
- 不要在 evaluator 中执行 Agent 或修改 session。
- 不要把一次 run 的判断逻辑写死在 CLI，CLI 只做编排。
- 任何新增持久化文件都应放在 session/run 目录结构下，并在文档中说明生产者。
- 新接口必须补单元测试；跨模块调用链必须补 scenario test。

## 20. 一句话心智模型

开发时始终把 Lora 看成四条链同时推进：

```text
配置链：CLI/env/lora.yaml -> RunConfig
上下文链：SessionManager -> AgentSession -> RuntimeContext -> session.json
证据链：EventStore -> events.jsonl -> projections -> replay/analyze
评估链：CaseDefinition -> AgentRuntimeAdapter -> Evaluator -> verdict/analysis
```

只要这四条链不断，新增能力就能被复现、回放、评估和继续优化。
