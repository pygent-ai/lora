# Lora

Lora 是基于 Pygent 0.2.6 的本地 Agent 开发与评测工具。API、CLI、case runner 共用 workspace 级 `LoraRuntimeService`，执行、并发、持久化、模型路由、工具任务和审批均由 Pygent Runtime 管理。

本版本直接采用 Pygent 0.2.6 的 Execution schema v1，不读取或迁移旧 Runtime journal；默认数据库使用新的 `*-v1.sqlite3` 路径。

## 安装

```powershell
uv sync
npm install
```

## 配置

Lora 只支持 routes-based 模型配置，不再接受旧的 `model_name/base_url/api_key_env` 单模型配置或 `--model` 覆盖。

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
      retry:
        max_attempts_per_route: 2
        attempt_timeout_seconds: 60
        backoff_initial: 0.5
        backoff_maximum: 4
        backoff_multiplier: 2

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

API key 建议写入用户凭据文件：

```powershell
uv run lora credentials set DEEPSEEK_API_KEY
uv run lora credentials validate
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
