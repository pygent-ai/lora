# Pygent 工具结构化错误需求

## 背景

`pygent-ai==0.1.10` 已经修复 Windows 上 Git Bash/MSYS 路径兼容问题。`read`、`glob`、`grep`、`bash.working_directory`、`write`、`edit` 现在都能正确接受 `/e/Projects/...` 这类路径。

剩余问题是：多个工具遇到路径不存在、参数非法、目标文件不存在等工具级错误时，仍然把错误写进普通文本结果，并让上层看到 `status=success`、`error=None`。

这会误导 agent：

- 模型难以区分“工具成功返回一段文本”和“工具调用失败”。
- 上层 trace、evaluation、repair 流程无法可靠统计工具失败。
- Lora 的 `ToolInterceptor` 只能根据异常判断失败；如果 pygent 工具把错误吞成字符串，interceptor 会记录成功。

## 已验证现象

验证环境：

- `pygent-ai==0.1.10`
- workspace root: `E:\Projects\lora`
- Lora 调用路径：`ToolInterceptor.call_tool(...)`

复测结果：

| 工具 | 输入 | 当前外层状态 | 当前结果 | 期望 |
| --- | --- | --- | --- | --- |
| `read` | 缺失文件 `/e/Projects/lora/NO_SUCH_FILE.md` | `status=success`, `error=None` | `错误：文件不存在 E:\Projects\lora\NO_SUCH_FILE.md` | 工具级 `error` |
| `glob` | 缺失目录 `/e/Projects/lora/NO_SUCH_DIR` | `status=success`, `error=None` | `error: path does not exist E:\Projects\lora\NO_SUCH_DIR` | 工具级 `error` |
| `grep` | 缺失目录 `/e/Projects/lora/NO_SUCH_DIR` | `status=success`, `error=None` | `错误：路径不存在 E:\Projects\lora\NO_SUCH_DIR` | 工具级 `error` |
| `edit` | 缺失文件 `/e/Projects/lora/NO_SUCH_FILE.md` | `status=success`, `error=None` | `错误：文件不存在或不是文件 E:\Projects\lora\NO_SUCH_FILE.md` | 工具级 `error` |
| `bash` | 缺失 `working_directory` | `status=success`, `error=None` | `error: working directory does not exist...` | 工具级 `error` |
| `bash` | `command="exit 7"` | `status=success`, `error=None` | `exit_code: 7` | 语义需明确，建议不归为工具级错误 |

## 问题定义

需要区分两类失败：

1. **工具级错误**：工具无法按请求执行，例如路径不存在、路径不是文件、参数非法、路径越界、权限不足、工作目录不存在、内部异常。这类错误应被结构化标记为失败。
2. **被执行程序的业务退出码**：例如 `bash(command="exit 7")` 成功启动 shell，shell 内命令返回非零退出码。这类结果可以保持为正常工具结果，但必须结构化包含 `exit_code`，让上层可以自行判断。

当前 pygent 文件工具把第一类错误变成普通字符串，导致上层无法可靠识别。

## 需求目标

pygent 工具层需要提供结构化错误语义，使 Lora 或其他调用方可以无歧义地判断工具调用是否失败。

最低目标：

- 工具级错误不应只作为普通文本返回。
- 上层适配后，tool result 的外层状态应为 `error`。
- 错误类型、错误消息、原始输入和规范化路径应尽可能结构化保留。

## 功能需求

### 1. 工具级错误必须结构化

当工具遇到以下情况时，应返回结构化错误或抛出明确异常：

- 文件不存在。
- 路径不存在。
- 目标不是文件。
- 目标不是目录。
- `working_directory` 不存在。
- 相对路径被拒绝。
- 路径越过 workspace 安全边界。
- 权限不足。
- 参数类型或参数组合非法。
- 读写编码失败。

建议错误形态：

```json
{
  "ok": false,
  "error": {
    "type": "FileNotFoundError",
    "message": "File does not exist",
    "path": "E:\\Projects\\lora\\NO_SUCH_FILE.md",
    "input_path": "/e/Projects/lora/NO_SUCH_FILE.md"
  }
}
```

如果 pygent 当前工具框架更适合抛异常，也可以抛出自定义异常，例如：

```text
ToolExecutionError(
  error_type="FileNotFoundError",
  message="File does not exist",
  details={...}
)
```

关键是调用方不能只能通过解析 result 字符串判断失败。

### 2. 上层可映射为 `status=error`

pygent 应保证 Lora 这类调用方能可靠映射：

```json
{
  "status": "error",
  "error": "File does not exist: E:\\Projects\\lora\\NO_SUCH_FILE.md",
  "error_type": "FileNotFoundError",
  "result": null
}
```

如果采用返回对象而非异常，工具框架需要提供统一判断方法，例如：

```python
is_tool_error(result) -> bool
tool_error_to_message(result) -> str
```

### 3. 成功结果不得混入错误前缀

成功结果不应使用下面这类字符串协议：

```text
错误：...
error: ...
```

这些文本可以出现在 `error.message` 中，但不应作为 `result` 的唯一载体。

### 4. `bash` 需要单独定义语义

`bash` 有两层结果：

1. 工具是否成功启动并管理 shell 进程。
2. shell 内命令的 `exit_code`。

建议：

- `working_directory` 不存在、命令参数非法、无法启动 shell、超时、后台启动失败等，属于工具级错误，应映射为 `status=error`。
- shell 成功启动后，命令返回非零 `exit_code`，可保持 `status=success`，但结果必须结构化包含 `exit_code`、`stdout`、`stderr` 或合并输出。
- 如果当前为了兼容仍返回文本 `exit_code: ... output: ...`，至少不要把 `working_directory` 不存在伪装成同类成功文本。

### 5. Trace 与文件影响记录应使用真实状态

工具级错误应写入 trace：

- `tool.result.status = "error"`
- `tool.result.error_type`
- `tool.result.error`

文件影响记录规则：

- `read` 文件不存在时，不应记录成功的 `file.read`。
- `edit/write` 在路径校验失败时，不应记录写入类 effect。
- `write/edit` 如果部分执行后失败，应保留实际 observed effect，但 tool result 仍应是 error。

## 受影响工具范围

必须覆盖：

- `read`
- `glob`
- `grep`
- `edit`
- `write`
- `bash.working_directory`

建议审计所有 pygent 工具里返回自然语言错误的路径，尤其是包含以下文本前缀的返回：

- `错误：`
- `error:`
- `Error:`
- `failed`
- `not found`

## 验收用例

### read

- `read(file_path="/e/Projects/lora/NO_SUCH_FILE.md")` 返回工具级 error。
- 上层 tool result 为 `status=error`。
- `error_type` 为 `FileNotFoundError` 或等价类型。
- `result` 不应是 `"错误：文件不存在 ..."`。

### glob

- `glob(path="/e/Projects/lora/NO_SUCH_DIR", pattern="*.md")` 返回工具级 error。
- 上层 tool result 为 `status=error`。
- 错误 details 中包含原始 `path` 和 resolved path。

### grep

- `grep(path="/e/Projects/lora/NO_SUCH_DIR", pattern="x")` 返回工具级 error。
- 上层 tool result 为 `status=error`。

### edit

- `edit(file_path="/e/Projects/lora/NO_SUCH_FILE.md", old_string="x", new_string="y")` 返回工具级 error。
- 不应创建文件。
- 不应记录 `file.edit` 或 `file.write` 的 declared success。

### write

- 对路径越界、父路径是文件、权限不足等情况返回工具级 error。
- 成功写入时仍返回正常成功结果。
- 失败时不得用普通文本承载错误。

### bash

- `bash(command="pwd", working_directory="/e/Projects/lora/NO_SUCH_DIR")` 返回工具级 error。
- `bash(command="exit 7", working_directory="/e/Projects/lora")` 可以保持工具成功，但结果中必须能结构化读取 `exit_code=7`。

## 非目标

- 不要求把所有 bash 非零退出码都改成工具级 error。
- 不要求改变普通命令输出格式，除非为了表达 `exit_code`、`stdout`、`stderr` 的结构化结果。
- 不要求 Lora 通过字符串匹配补救 pygent 的错误语义；修复应优先发生在 pygent 工具层或 pygent 的工具调用协议层。

## 推荐实现顺序

1. 在 pygent 工具框架中定义统一工具错误对象或异常类型。
2. 修改 `read/glob/grep/edit/write/bash.working_directory` 的错误路径，使其返回或抛出结构化错误。
3. 调整 OpenAI/function calling 适配层，让结构化工具错误映射为 `status=error`。
4. 保留 `bash` 命令进程退出码语义，并为非零退出码提供结构化字段。
5. 增加 Windows 路径和缺失路径的回归测试。
6. 用 Lora 的 `ToolInterceptor.call_tool(...)` 复测，确认不再出现错误文本加 `status=success`。

## 成功标准

当文件不存在或目录不存在时，agent 看到的是明确的工具失败，而不是一段普通文本。这样模型可以正确调整下一步行动，trace/evaluation/repair 也能可靠统计工具错误。

