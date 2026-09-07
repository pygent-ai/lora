# Lora

Lora 是基于 Pygent 0.3.6 的本地 Agent 开发与评测工具。前台推理由原生 `PygentAgent` 驱动，上下文窗口压缩由原生 compressor `Module` 承担；API、CLI、case runner 共用 workspace 级 `LoraRuntimeService`，执行、并发、持久化、模型路由、工具任务和审批均由 Pygent Runtime 管理。

本版本直接采用 Pygent 0.3.3 的 Execution schema v1，不读取或迁移旧 Runtime journal；默认数据库使用 `*-v1.sqlite3` 路径。若 `preferred`/`disabled` 持久化发现该路径中的 Pygent 内部 SQLite schema 不兼容，Lora 会保留旧库并切换到带 `-schema-v7` 后缀的新 journal；`required` 模式仍会明确失败。

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

```yaml
agent:
  default_alias: dev

agents:
  - alias: dev
    model_request:
      profile: default
      routes:
        - id: primary
          provider: openai
          model_name: deepseek-v4-flash
          base_url: https://api.deepseek.com
          api_key_env: DEEPSEEK_API_KEY
      fallback: [primary]
```

API key 保存在同一用户目录下的凭据文件：

```powershell
uv run lora credentials set DEEPSEEK_API_KEY
uv run lora credentials validate
```

如果 `~/.lora/config.yaml` 尚未建立，系统将使用内置默认配置（含内置模型别名与持久化策略）。

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
uv run lora chat --message "分析当前项目"
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

更多说明见 [CLI](docs/cli/lora-chat.md)、[本地 API](docs/api/local-service.md) 和[开发指南](docs/guides/development-guide.md)。

本地开发依赖通过 `tool.uv.sources` 使用相邻目录 `../pygent` 的源码；`uv sync` 会安装该版本，包含执行输入取消固定字节大小上限的修复。
