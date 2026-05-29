# 01 Agent Profile and Alias Management

> 状态：已实现。本文保留为实现记录和后续扩展参考；下方“原始缺口”描述的是实现前状态。

## 背景

文档中已经描述了 `agent.default_alias`、`agents[].alias` 和 `agents[].model_request` 的配置形态，但当前代码仍只有默认 `LoraAgent`。模型请求配置主要来自 `--model`、`LORA_MODEL`、`DEEPSEEK_MODEL`、`DEEPSEEK_API_KEY` 和 `DEEPSEEK_BASE_URL`。

目标是把“选择哪个 agent”和“这个 agent 如何发起模型请求”变成结构化配置，同时保证 API key 不落盘。

## 已实现状态

- CLI 已提供全局 `--agent` 参数。
- `RunConfig` 已包含 `agent_alias`、`model_name`、`api_key_source`、`base_url` 等可落盘脱敏字段。
- `load_run_config()` 已解析 `agent.default_alias`、`agents[]`、模型优先级和 API key 来源。
- `LoraAgent` 已接收解析后的 `ResolvedAgentConfig`，并保留 DeepSeek 兼容 fallback。
- `run_config.json`、`model.request` 和 `model.response` 已记录安全 agent 元数据，不落盘原始 API key。

## 原始缺口

- CLI 没有 `--agent` 或 `--agent-alias` 参数。
- `RunConfig` 没有 `agent_alias`、`model_name`、`api_key_source` 等脱敏元数据字段。
- `load_run_config()` 不解析 `agent.default_alias` 和 `agents[]`。
- `LoraAgent` 直接读取 `DEEPSEEK_API_KEY`，没有接收已解析的 agent profile。
- `model.request`、`model.response` 和 `run_config.json` 只记录 agent 类名，没有记录 alias、模型来源和 API key 来源。

## 实现方法

1. 扩展 schema：
   - 新增 `ResolvedAgentConfig` dataclass，字段包含 `alias`、`model_name`、`api_key`、`api_key_source`、`base_url`。
   - 扩展 `RunConfig`，新增可落盘字段：`agent_alias`、`model_name`、`api_key_source`、`base_url`。不要把原始 `api_key` 放进 `RunConfig`。

2. 扩展配置解析：
   - `load_run_config()` 增加 `agent_alias` 参数。
   - 读取优先级：CLI 参数 > `agent.default_alias` > 内置 `default`。
   - 从 `agents[]` 中按 alias 找 profile；找不到时在创建 session/run 前报错。
   - 模型优先级：`--model` > `agents[].model_request.model_name` > `LORA_MODEL` > `DEEPSEEK_MODEL` > 默认模型。
   - API key 优先级：`model_request.api_key_env` 指向的环境变量 > `model_request.api_key` > `DEEPSEEK_API_KEY`。
   - `api_key_source` 只记录来源描述，例如 `env:DEEPSEEK_API_KEY`、`config:model_request.api_key`、`missing`。

3. 扩展 CLI：
   - 在全局参数中加入 `--agent`，映射到 `agent_alias`。
   - `chat`、`case run`、`optimize` 都沿用全局参数。

4. 调整 runtime/agent：
   - `AgentRuntimeAdapter` 创建 `LoraAgent` 时传入 `ResolvedAgentConfig` 或可解析它的 provider。
   - `LoraAgent` 不再直接绑定单一 `DEEPSEEK_API_KEY`；保留 DeepSeek 兼容 fallback。
   - fallback 文案包含 agent alias，例如缺少 `dev` 的 API key。

5. 调整事件和落盘：
   - `run_config.json` 记录 `agent_alias`、`model_name`、`api_key_source`、`base_url`。
   - `model.request` 和 `model.response` payload 记录同样的脱敏字段。
   - 确保任何产物和 trace 都不包含原始 API key。

## 验收标准

- `lora chat --agent dev --message hello` 能选择 `agents[].alias == dev` 的 profile。
- `lora case run case.yaml --agent fast-check` 能把 alias 写入 `run_config.json` 和 `model.request`。
- `--model` 能覆盖 agent profile 中的 `model_request.model_name`。
- 当 alias 不存在时，CLI 返回退出码 `2`，并给出可读错误。
- 当 API key 缺失时，agent 不发起外部请求，fallback 文案包含缺失的 alias。
- `run_config.json`、`events.jsonl`、`messages.jsonl`、`tool_*.jsonl` 中不出现原始 API key。

## 测试建议

- `tests/unit/test_config.py`：覆盖 agent alias 解析、模型优先级、API key source 脱敏。
- `tests/unit/test_schema.py`：覆盖 `RunConfig` 新字段 round-trip。
- `tests/unit/test_runtime_adapter.py`：覆盖默认 agent 接收解析后的 profile。
- `tests/scenario/test_cli_flow.py`：覆盖 `chat --agent` 和 `case run --agent` 的落盘产物。
