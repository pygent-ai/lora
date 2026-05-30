# Lora

Lora 是一个面向本地 Agent 自优化实验的运行框架。当前代码重点覆盖四条链路：

- 配置链：CLI、环境变量和 `lora.yaml` 合并为 `RunConfig`。
- 上下文链：`SessionManager` 管理可恢复 session 和对话历史。
- 证据链：`EventStore` 写入 append-only trace，并投影消息、工具调用和文件效果。
- 评估链：`CaseManager`、`AgentRuntimeAdapter` 和 `Evaluator` 执行 case 并生成 metrics/verdict。

## 安装与测试

```powershell
uv sync
uv run python -m unittest discover -s tests
```

如果直接使用本地源码运行 CLI：

```powershell
$env:PYTHONPATH = "src"
uv run python -m lora --workspace-root . chat --message "hello"
```

也可以通过项目脚本运行：

```powershell
uv run lora --workspace-root . chat --message "hello"
```

## 常用命令

创建和查看 session：

```powershell
uv run lora session create --case demo
uv run lora session show <session_id>
```

运行、回放和分析 case：

```powershell
uv run lora case run path\to\case.yaml
uv run lora case replay <session_id> <case_run_id>
uv run lora case analyze <session_id> <case_run_id>
```

聊天入口：

```powershell
uv run lora chat
uv run lora chat --message "你好"
uv run lora chat --session <session_id> --message "继续刚才的话题"
```

## 当前 Agent 行为

默认 `AgentRuntimeAdapter` 会创建 `LoraAgent`。`LoraAgent` 会加载工作区 `.env`，读取已解析的 agent profile，并兼容 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_BASE_URL`。没有可用 API key 时不会发起外部模型调用，而是返回包含 agent alias 的固定提示，方便本地测试 CLI、session、trace 和评估链路。

真实工具调用使用 `pygent.toolkits` 中的白名单工具：

- `bash`
- `read`
- `write`
- `edit`
- `glob`
- `grep`

工具调用会经过 `ToolInterceptor` 记录 `tool.call`、`tool.result`，并在默认 `LoraAgent` 路径中开启 workspace 文件效果跟踪，写入 `file.read/file.write/file.edit/file.delete` 事件和 `file_events.jsonl` 投影。

## 运行产物

Lora 的结构化运行证据默认写入：

```text
.lora/
  sessions/
    {session_id}/
      session.json
      logs/
      context/
      cases/{case_id}/runs/{case_run_id}/
        case.yaml
        run_config.json
        run_metadata.json
        events.jsonl
        messages.jsonl
        tool_calls.jsonl
        tool_results.jsonl
        file_events.jsonl
        metrics.json
        verdict.json
```

其中 `case.yaml`、`metrics.json` 和 `verdict.json` 是 `lora case run` 的评估产物。`lora chat` 每次对话也会创建 `chat` 类型的 case run，但默认只写入 `events.jsonl`、`messages.jsonl`、`run_config.json` 和 `run_metadata.json`；实际调用工具时还会生成 `tool_calls.jsonl`、`tool_results.jsonl` 和 `file_events.jsonl`。

同时会在 `sessions/{session_id}/session.json` 写入一份兼容 pygent 风格的 session 落盘。

## 已落地能力与后续规划

- Agent profile/alias 已支持：可通过全局 `--agent` 选择 `lora.yaml` 中的 `agents[].alias`，并解析 `model_request.model_name`、`api_key_env`、`api_key` 和 `base_url`。运行产物只记录 `agent_alias`、`model_name`、`api_key_source`、`base_url` 等脱敏元数据。
- `lora regression run` 已支持读取 `.lora/regression.json`，按 manifest 执行 case suite，并生成 `.lora/regressions/{regression_run_id}/result.json`、`case_runs.jsonl` 和 regression 事件；未配置 manifest 时返回 `skipped`。
- `lora test generate/register` 已支持从失败 run 生成 deterministic regression case，并注册到 `.lora/regression.json`。
- `lora repair plan/apply/gate` 已支持确定性修复计划、人工 diff attempt 捕获和命令/regression gate；失败归因已由独立 `FailureAnalyzer` 负责。
- workspace setup 已支持 `copy_fixture`、`write_file`、`mkdir`、`delete_path` 和 gated `run_command`，并会写入 `workspace/setup_actions.jsonl`；setup 路径必须位于 `workspace_root` 内。
- 仍在规划中的能力包括完整 YAML 支持、repair agent 自动生成 patch、多 session 对比/非确定性检测，以及面向不同 Agent backend 的 adapter registry。

更多设计细节见 `doc/` 和 `doc_design/`。
