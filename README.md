# Lora

Lora 是基于 Pygent 0.2.16 的本地 Agent 开发与评测工具。API、CLI、case runner 共用 workspace 级 `LoraRuntimeService`，执行、并发、持久化、模型路由、工具任务和审批均由 Pygent Runtime 管理。

本版本直接采用 Pygent 0.2.16 的 Execution schema v1，不读取或迁移旧 Runtime journal；默认数据库使用新的 `*-v1.sqlite3` 路径。

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

如果 `~/.lora/config.yaml` 尚不存在，Lora 会兼容读取项目 `lora.yaml` 中原有的 `agent/agents` 配置；创建用户配置后，项目中的模型配置不再生效。

## 项目配置

工具批准策略也属于用户级配置，可放在 `~/.lora/config.yaml` 的 `runtime.approvals` 下，对所有项目生效。`preauthorized_tools` 中列出的工具会自动放行；`enabled: false` 会放行所有高风险工具，请谨慎使用。

项目 `lora.yaml` 保存 durability、capacity、MCP、delegation 等项目行为；未配置用户级批准策略时，仍兼容项目中的 `runtime.approvals`：

```yaml
runtime:
  durability:
    mode: preferred
    history_path: .lora/runtime/executions-v1.sqlite3
  capacity:
    scope: runtime_instance
    coordinator_path: .lora/runtime/capacity-v1.sqlite3
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
```

## 使用

```powershell
uv run lora chat --message "分析当前项目"
uv run lora --agent dev case run cases/example.yaml
uv run lora-api --workspace-root E:\Projects\lora
npm run dev
```

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
