# Lora 开发指南

## 运行边界

```text
API / CLI / case runner
        -> LoraRuntimeService
        -> Pygent LocalRuntime + Binding
        -> LoraAgent(Module)
```

`LoraRuntimeService` 是 workspace 级 Runtime、SQLite history、model deployments、capacity、tool registry、MCP 和 durable task manager 的唯一所有者。`LoraAgent` 是原生 Pygent Module，不存在旧 Agent adapter。

## 配置

模型配置只有一种形式，并统一位于用户级 `~/.lora/config.yaml`：

```yaml
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
```

所有项目共用用户级 `agent/agents`。项目 `lora.yaml` 中的同名配置只在用户配置不存在时作为迁移兼容。旧单模型字段、`runtime.model`、顶层 `model/base_url` 与 CLI `--model` 均不支持。

## Agent Module

`LoraAgent` 组合以下业务 Module：

- `DynamicPromptModule`
- `ContextCompressionModule`
- `SkillReminderModule`
- `ToolAuditModule`
- `PersistedDiffModule`
- Pygent `ReActLayer`、`ModelCallLayer`、`ToolCallLayer`

Session、prompt、技能选择、文件快照和 diff 是 Lora 业务语义；admission、并发、取消、retry/fallback、journal、审批 waiter、durable ToolTask 和 fencing 是 Pygent Runtime 语义。

## 事件与恢复

API 不维护独立内存回放协议，也不把 Pygent 事件翻译成另一套状态机。传输信封字段为：

```text
execution_id, sequence, kind, module_path, trace_id, data
```

恢复直接查询 Pygent SQLite history。业务 case run 只记录 `runtime_execution_id`，供审计和定位使用。

## 工具与审批

工具必须声明 `ToolSpec.side_effect`、`idempotency`、`resource_key` 与 `sandbox_profile`。WRITE、EXTERNAL、后台委派等高风险操作进入 `LoraToolAuthorization`，通过 Pygent `wait_external()` 等待统一审批 API。

MCP 工具在 Runtime 初始化时发现并注册；工具名冲突直接阻止启动，optional server 失败只警告，required server 失败阻止启动。

## 验证

```powershell
uv run pytest tests -q
npm --prefix apps/desktop test
npm --prefix apps/desktop run build
uv run python -m compileall -q src
```

开发新能力时优先增加 Pygent Module 或 ToolSpec，不重新引入 Agent 适配器、事件映射器或单模型兼容字段。
