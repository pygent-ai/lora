# Pygent 工具路径兼容性需求

## 背景

在会话 `chat-chat-20260530-154636-187539` 中，用户要求 agent 检查项目文档和代码实现的不一致。该任务需要大量读取文件，但实际工具调用中 `bash` 被调用 74 次，`read` 只被调用 7 次。

这不是因为 `read` 未注册，也不是因为系统提示词鼓励用 `bash cat`。实际渲染给模型的工具提示包含：

- `Use read with offset/limit instead of dumping whole files through bash cat.`
- `Use Windows absolute paths for read/write/edit file_path values; use bash working_directory for shell commands rooted in the workspace.`
- `Use bash as a fallback for verification or composed shell commands...`

会话中 agent 一开始确实尝试了 `read`，但连续遇到路径解析失败：

```text
read file_path=/root/lora/README.md
=> 错误：文件不存在 E:\root\lora\README.md

bash pwd
=> /e/Projects/lora

read file_path=/e/Projects/lora/README.md
=> 错误：文件不存在 E:\e\Projects\lora\README.md
```

随后 agent 判断 `read` 的路径解析不可靠，改用 `bash cat`、`cat -n`、`sed -n`、`grep` 等命令继续工作。这个行为符合当前提示词里的 fallback 规则，但不是期望的工具使用模式。

## 问题摘要

在 Windows 主机上，`bash` 工具暴露的是 Git Bash/MSYS 风格路径，例如 `/e/Projects/lora`；而 pygent 的文件类工具按 Windows `Path` 规则解析路径，把 `/e/Projects/lora` 错误转换成 `E:\e\Projects\lora`。

这会造成三个后果：

1. `read`、`glob`、`grep` 等结构化工具对 agent 刚从 `bash pwd` 或 shell 输出中获得的路径不可用。
2. 模型在一次会话中很快学到“bash 能成功，结构化文件工具会失败”，从而持续使用 `bash` 读文件。
3. `write` 这类会创建父目录的工具存在更高风险：如果传入 `/e/...`，可能写入错误位置，而不是目标 workspace。

## 已验证的工具行为

验证环境：

- workspace root: `E:\Projects\lora`
- bash 工作目录显示: `/e/Projects/lora`
- 工具来源: `pygent.toolkits.FileToolkits` 与 `pygent.toolkits.BashToolkits`

| 工具 | Windows 路径 `E:\Projects\lora` | MSYS 路径 `/e/Projects/lora` | 相对路径 `.` / `README.md` | 结论 |
| --- | --- | --- | --- | --- |
| `read` | 成功读取 | 失败，解析为 `E:\e\Projects\lora\...` | 拒绝相对文件路径 | 受影响 |
| `glob` | 成功 | 失败，解析为 `E:\e\Projects\lora` | 成功，按 workspace 解析 | 受影响 |
| `grep` | 成功 | 失败，解析为 `E:\e\Projects\lora` | 成功，按 workspace 解析 | 受影响 |
| `bash.working_directory` | 成功 | 失败，解析为 `E:\e\Projects\lora` | 成功，按 workspace 解析 | 受影响 |
| `write` | schema 要求绝对路径 | 同类路径解析风险，且可能创建错误目录 | 拒绝相对路径 | 高风险，需修复并测试 |
| `edit` | schema 要求绝对路径 | 同类路径解析风险 | 拒绝相对路径 | 需修复并测试 |

## 当前 Schema 问题

`read` 的 schema：

```json
{
  "name": "read",
  "description": "Read a file by absolute path, with optional line ranges for text files and page ranges for PDFs.",
  "parameters": {
    "properties": {
      "file_path": {
        "type": "string",
        "description": "要读取的文件绝对路径。"
      }
    },
    "required": ["file_path"]
  }
}
```

`write` 和 `edit` 的 schema 明确要求 absolute path，但没有说明 Windows、MSYS、WSL、POSIX 风格路径的兼容规则。

`glob` 和 `grep` 的 `path` 字段允许相对路径，但没有说明：

- 默认相对哪个根目录解析。
- Windows 下是否接受 `/e/...`。
- 返回的路径应该是 Windows 风格、MSYS 风格，还是与输入风格一致。

`bash.working_directory` 的 schema 写着 relative paths are resolved from workspace root，但没有说明它是否接受 bash 自身输出的 `/e/...` 路径。

## 需求目标

pygent 文件类工具需要在 Windows 上统一支持以下路径输入：

1. Windows 绝对路径：`E:\Projects\lora\README.md`
2. Windows slash 路径：`E:/Projects/lora/README.md`
3. Git Bash/MSYS 路径：`/e/Projects/lora/README.md`
4. workspace 相对路径：按各工具现有策略支持或明确拒绝
5. `~` 用户目录路径：如果现有工具支持，应保持兼容

工具返回错误时，需要让上层 agent 能明确识别这是失败，而不是一次成功的普通文本结果。

## 功能需求

### 1. 增加统一路径规范化层

在 pygent 工具层增加共享路径规范化函数，所有接受路径的工具都通过同一入口解析路径。

建议支持：

```text
/e/Projects/lora/README.md -> E:\Projects\lora\README.md
/c/Users/name/file.txt     -> C:\Users\name\file.txt
E:/Projects/lora/file.txt  -> E:\Projects\lora\file.txt
```

规则：

- 仅在 Windows 平台启用 MSYS drive path 转换。
- `/[a-zA-Z]/...` 应被识别为 drive path，而不是拼接到当前盘符下。
- 普通 POSIX 绝对路径在非 Windows 平台保持原有语义。
- 相对路径的支持策略按工具区分，但必须文档化并在 schema 里说明。

### 2. 统一所有路径字段

至少覆盖这些字段：

- `read.file_path`
- `write.file_path`
- `edit.file_path`
- `glob.path`
- `grep.path`
- `bash.working_directory`

如果 pygent 还有其他文件工具，也需要审计所有 path-like 参数，例如 `path`、`file`、`file_path`、`dir`、`directory`、`cwd`、`working_directory`、`target`、`source`、`destination`。

### 3. 修正工具错误返回

当前 `read` 路径不存在时，工具结果外层表现为成功，内容里包含：

```text
错误：文件不存在 E:\e\Projects\lora\README.md
```

这会误导上层 agent。需求：

- 路径不存在、路径非法、路径逃逸、权限不足等情况应返回结构化错误。
- 上层适配成 tool result 时，`status` 应为 `error`，`error` 字段应包含错误摘要。
- 普通文本结果中不应只用自然语言错误承载失败状态。

### 4. Schema 描述需要能指导模型

更新 schema description，使模型知道 Windows 环境下可以直接使用从 bash 得到的路径。

建议描述：

```text
Accepts Windows absolute paths such as E:\Projects\repo\file.txt, Windows slash paths such as E:/Projects/repo/file.txt, and on Windows Git Bash/MSYS drive paths such as /e/Projects/repo/file.txt. Relative paths, when allowed by this tool, resolve from workspace_root.
```

对 `read/write/edit`：

- 如果继续拒绝相对路径，要明确写“relative paths are rejected”。
- 如果未来支持相对路径，要明确写“relative paths resolve from workspace_root”。

对 `glob/grep/bash.working_directory`：

- 明确 `.`、相对目录、Windows 绝对路径、MSYS 路径的解析规则。

### 5. 避免写入错误位置

`write` 是最高风险工具。修复前，如果传入 `/e/Projects/lora/out.txt`，实现可能会创建或尝试创建 `E:\e\Projects\lora\out.txt`。

需求：

- `write` 和 `edit` 必须先完成路径规范化和 workspace 安全检查，再执行任何文件修改。
- 对明显疑似 MSYS 路径的输入，不能按 `E:\e\...` 解释。
- 如果无法安全解析，应失败，不得猜测写入。

### 6. 保持 workspace 安全边界

路径兼容不应绕过 workspace 限制。

需求：

- 对受 workspace 限制的工具，规范化后的 resolved path 必须仍在 workspace root 内。
- `read` 如果允许读取 workspace 外部路径，应保持现有配置开关或权限策略。
- 错误消息应同时包含原始输入和规范化后的候选路径，便于排查，但避免泄露敏感内容。

## 验收用例

以下用例应在 Windows 环境运行。

### read

```text
workspace_root = E:\Projects\lora
```

- `read(file_path="E:\Projects\lora\README.md", limit=2)` 成功。
- `read(file_path="E:/Projects/lora/README.md", limit=2)` 成功。
- `read(file_path="/e/Projects/lora/README.md", limit=2)` 成功。
- `read(file_path="/e/Projects/lora/missing.md")` 返回结构化错误，外层 status 为 `error`。
- 如果 `read` 仍拒绝相对路径，`read(file_path="README.md")` 返回结构化错误，schema 必须明确说明。

### glob

- `glob(path="E:\Projects\lora", pattern="README.md")` 返回 README。
- `glob(path="E:/Projects/lora", pattern="README.md")` 返回 README。
- `glob(path="/e/Projects/lora", pattern="README.md")` 返回 README。
- `glob(path=".", pattern="README.md")` 返回 README。
- 缺省 `path` 时从 workspace root 搜索。

### grep

- `grep(path="E:\Projects\lora", pattern="Lora", output_mode="content", head_limit=2)` 成功。
- `grep(path="E:/Projects/lora", pattern="Lora", output_mode="content", head_limit=2)` 成功。
- `grep(path="/e/Projects/lora", pattern="Lora", output_mode="content", head_limit=2)` 成功。
- `grep(path=".", pattern="Lora", output_mode="content", head_limit=2)` 成功。

### bash

- `bash(command="pwd && ls README.md")` 仍在 bash 环境输出 `/e/Projects/lora`。
- `bash(command="pwd", working_directory="E:\Projects\lora")` 成功。
- `bash(command="pwd", working_directory="E:/Projects/lora")` 成功。
- `bash(command="pwd", working_directory="/e/Projects/lora")` 成功。
- `bash(command="pwd", working_directory=".")` 成功。

### write

使用临时目录，例如 `E:\Projects\lora\.lora\path-compat-test\write.txt`。

- `write(file_path="E:\Projects\lora\.lora\path-compat-test\write.txt", content="ok")` 写入目标文件。
- `write(file_path="E:/Projects/lora/.lora/path-compat-test/write.txt", content="ok")` 写入同一个目标文件。
- `write(file_path="/e/Projects/lora/.lora/path-compat-test/write.txt", content="ok")` 写入同一个目标文件。
- 不应创建 `E:\e\Projects\...`。
- 对 workspace 外路径按现有安全策略允许或拒绝，但必须返回结构化状态。

### edit

使用 `write` 创建的临时文件。

- `edit(file_path="E:\Projects\lora\.lora\path-compat-test\write.txt", old_string="ok", new_string="edited")` 成功。
- `edit(file_path="E:/Projects/lora/.lora/path-compat-test/write.txt", old_string="edited", new_string="ok")` 成功。
- `edit(file_path="/e/Projects/lora/.lora/path-compat-test/write.txt", old_string="ok", new_string="edited")` 成功。
- 不应访问或创建 `E:\e\Projects\...`。

## 非目标

- 不要求 `read/write/edit` 必须支持相对路径；只要求 schema 与行为一致。
- 不要求改变 bash 输出路径风格；bash 继续输出 `/e/...` 是可以接受的。
- 不要求 Lora 在 prompt 中继续用额外规则弥补工具缺陷。修复应优先发生在 pygent 工具层。

## 推荐实现顺序

1. 在 pygent 中实现并测试共享路径规范化函数。
2. 将 `read/glob/grep/bash.working_directory` 接入该函数，先解决已验证失败。
3. 将 `write/edit` 接入该函数，并补充防止错误写入位置的测试。
4. 修改工具错误返回，使路径类错误变成结构化失败。
5. 更新所有 path-like 参数的 schema description。
6. 在 Windows CI 或本地 Windows 测试中增加上述验收用例。

## 成功标准

修复完成后，agent 从 `bash pwd` 得到 `/e/Projects/lora` 后，可以直接把 `/e/Projects/lora/README.md` 交给 `read`，并成功读取文件。模型不再因为结构化文件工具路径失败而转向大量 `bash cat`。

