# Lora

Lora 是基于 Pygent 0.3.19 的本地 Agent 开发与评测工具。前台推理由原生 `PygentAgent` 驱动，上下文窗口压缩由原生 compressor `Module` 承担；API、CLI、case runner 共用 workspace 级 `LoraRuntimeService`，执行、并发、持久化、模型路由、工具任务和审批均由 Pygent Runtime 管理。

Runtime journal 直接使用当前 PyPI Pygent 管理的 SQLite schema 和配置路径。

模型部署使用 `model-deployments-v2.sqlite3`，按当前配置重新建立，不读取或迁移旧 `model-deployments-v1.sqlite3`。聊天记录和 execution journal 保留；旧模型部署绑定的未完成 execution 不支持跨版本恢复。

## 安装

```powershell
uv sync
npm install
```

## 用户模型配置

模型配置属于当前操作系统用户，统一保存在 `~/.lora/config.yaml`。所有项目和会话共用这套配置。首次迁移可以复制仓库示例：

```powershell
New-Item -ItemType Directory -Force "$HOME\.lora"
Copy-Item .\user-config.yaml.example "$HOME\.lora\config.yaml"
```

完整格式见 [`user-config.yaml.example`](user-config.yaml.example)：顶层 `models`
使用 Pygent 原生 `ModelConfig`（每个模型自带 connection、protocol、credential 和
capabilities），`model_groups` 按顺序声明子模型及回退链。新建对话时选择一个模型组，
对话创建后模型组固定；空闲时可以在该组内切换首选子模型，其余子模型继续作为回退。

API key 保存在同一用户目录下的凭据文件：

```powershell
uv run lora credentials set DEEPSEEK_API_KEY
uv run lora credentials validate
```

如果尚未建立有效的模型配置，系统会显示“需要配置模型”，不会猜测默认模型或 API key。

## 用户配置

运行数据统一保存在用户目录：项目使用 `~/.lora/projects/<项目路径哈希>/`，无项目聊天使用 `~/.lora/conversations/`。项目目录内不自动创建 `.lora`。每个数据目录包含 `sessions/`、`runtime/` 和回归结果；会话元数据记录原始工作区路径。项目专属 skills、`repair.json` 和 `regression.json` 也放在对应数据目录，用户通用 skills 放在 `~/.lora/skills/`。

项目路径哈希取规范化绝对路径的 SHA-256 前 24 位，因此同名项目互相隔离，移动项目会得到新的数据目录。`lora_root` 不再是用户配置项。运行时数据库的相对路径基于该项目或聊天的数据目录解析，绝对路径按配置使用。

`~/.lora/config.yaml` 同时承载模型、运行时和工具审批策略（`runtime.approvals`）配置，对所有项目共用。`preauthorized_tools` 中列出的工具会自动放行；`runtime.approvals.enabled: false` 会放行所有高风险工具，请谨慎使用。

在前端 Settings 中，将「Tool permissions / 工具权限」选择为「Full access / 完全访问」，点击「Save and Reload」即可保存，对所有工作区的后续运行生效；正在运行的任务保留原权限。选择「Require approval / 逐次审批」可恢复审批。

也可以直接在该配置文件中设置以下内容，然后重启 Lora 服务：

```yaml
runtime:
  approvals:
    enabled: false
```

如果已有 `runtime` 或 `approvals` 配置，请合并到已有节点中。此开关仅控制 Lora 的工具审批，不会提升操作系统权限。恢复逐次审批时改回 `enabled: true`。

```yaml
runtime:
  durability:
    mode: preferred
    history_path: runtime/executions-v1.sqlite3
  capacity:
    scope: runtime_instance
    coordinator_path: runtime/capacity-v1.sqlite3
  approvals:
    enabled: true
    timeout_seconds: 300
    preauthorized_tools: []

mcp:
  servers: []

delegation:
  allowed_agents: []
  max_depth: 4
  max_parallel: 4
  background_enabled: true

eternal_conversation:
  enabled: false
  extractor_agent_alias: dev
  builder_agent_alias: fast-check
  dynamic_memory_cli_path: ~/.lora/skills/dynamic-memory-cli/scripts/dynamic_memory_cli.py
```

## 使用

```powershell
uv run lora session run --new --message "分析当前项目"
uv run lora --agent dev case run cases/example.yaml
uv run lora-api --workspace-root E:\Projects\lora
npm run dev
```

## Windows 便携版

```powershell
npm run desktop:portable
```

产物位于 `apps/desktop/release/Lora-Desktop-<版本>-portable.exe`，目标 Windows x64 机器无需预装 Python、Node.js 或项目依赖，复制后双击即可使用。便携版不会修改系统 `PATH`；需要终端中的 `lora` 命令时请使用安装版。

`npm run dev` 会启动 Vite 和 Electron；Electron 会自动启动并在退出时关闭本地 `lora-api`。只调试浏览器 renderer 时可运行 `npm --prefix apps/desktop run dev:renderer`。

直接运行 Pygent ReAct 示例：

```powershell
uv run python examples/react_agent_demo.py
```

## Execution 事件协议

`POST /chat/stream` 输出 Pygent execution journal 的稳定薄封装，不再映射旧 chat SSE 事件：

```json
{
  "execution_id": "exec-...",
  "sequence": 12,
  "kind": "model.text.delta",
  "module_path": "lora.react.model",
  "trace_id": "trace-...",
  "data": {"text": "partial text"}
}
```

断线恢复使用 `execution_id` 和 `after_sequence`。审批事件、工具事件、模型事件与 execution 终态共享同一序列。

## 验证

```powershell
uv run pytest tests -q
npm --prefix apps/desktop test
npm --prefix apps/desktop run build
```

更多说明见 [CLI](docs/cli/lora-session.md)、[本地 API](docs/api/local-service.md) 和[开发指南](docs/guides/development-guide.md)。

运行时依赖锁定为官方 PyPI 发布的 `pygent-ai==0.3.19`（对应 [官方 v0.3.19](https://github.com/pygent-ai/pygent/releases/tag/v0.3.19)），`uv.lock` 记录下载地址和校验值，不使用 `../pygent` 本地源码覆盖。模型配置直接采用 Pygent 的 `connections`、`models`、`model_groups` 三层契约：先定义可复用 Connection，再由模型引用并选择该连接提供的协议。

重试直接使用 Pygent 0.3.19 的 `RetryPolicy.max_attempts_per_model`；旧 `routes` / `fallback` 以及 0.3.15 的模型内嵌 connection 结构不会继续执行。

### Bash 后台任务

`bash` 的调用参数 `timeout` 现在以**秒**为单位，表示前台等待时间，默认 600 秒。等待到期返回任务引用，原命令继续运行；`timeout=0` 或 `is_background=true` 立即返回。命令仍遵守 Lora 的工具审批策略。Agent 可用 `tool_task_get(task_id)` 查询输出与最终结果，用 `tool_task_stop(task_id)` 请求停止。

前台回合结束后，后台 Bash 由 Pygent Runtime 持有。Lora 在任务终态补记审计和工作区文件差异；运行时关闭会取消并清理后台命令。输出使用既有截断与落盘机制。任务引用也可通过 `/runtime/tasks/{task_id}` 查询或取消。重启只能读取已保存结果；未确认完成的任务在原 owner 租约失效后成为 `unknown`，不自动重跑或接管旧进程。

升级前请先结束旧版本的活动执行。Bash 工具契约已变为 `standard.shell.bash@3.1.0`，旧版未完成执行的 ExecutionPlan 不保证兼容，不能把继续已有会话等同于恢复旧执行。
