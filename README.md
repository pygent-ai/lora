# Lora

Lora 是一个面向 Agent 自优化的本地运行与评测框架。它把一次 Agent 执行拆成可复现的 session、可回放的 trace、可判定的 case run，以及可持续沉淀的回归与修复证据，帮助开发者把“Agent 表现不稳定”变成可以观察、分析和改进的工程问题。

当前项目聚焦于本地 Agent 实验闭环：从命令行启动对话或 case，记录模型消息、工具调用和文件影响，再通过 deterministic evaluator 生成指标、判定结果和失败归因。

## 为什么需要 Lora

构建 Agent 时，真正困难的部分通常不是“调用一次模型”，而是回答这些问题：

- 这次失败能不能复现？
- Agent 到底看到了什么上下文？
- 它调用了哪些工具，改动了哪些文件？
- 失败是模型回答问题、工具调用问题、文件副作用问题，还是预算约束问题？
- 修复之后，旧问题会不会再次出现？

Lora 为这些问题提供一套本地优先的运行证据链。

## 核心能力

- **Session 管理**：创建、恢复和查看 Agent session，把对话历史和运行上下文落盘。
- **Trace 记录**：通过 append-only `events.jsonl` 记录消息、模型请求、工具调用、文件影响和运行错误。
- **Case 评测**：使用 YAML case 描述输入、期望和指标，执行后生成 `metrics.json` 与 `verdict.json`。
- **失败分析**：基于 verdict、事件流和运行产物输出 root cause，辅助定位失败来源。
- **回归管理**：从失败 run 生成 deterministic regression case，并通过 manifest 统一运行。
- **修复闭环**：支持生成修复计划、捕获人工 diff attempt，并运行 regression gate。
- **Agent profile**：通过 `lora.yaml` 和 `--agent` 选择不同 agent alias、模型、API key 来源和 base URL。
- **文件影响追踪**：记录工具对 workspace 文件的读取、写入、编辑和删除效果。

## 快速开始

安装依赖：

```powershell
uv sync
```

运行测试：

```powershell
uv run python -m unittest discover -s tests
```

直接运行源码中的 CLI：

```powershell
$env:PYTHONPATH = "src"
uv run python -m lora --workspace-root . chat --message "hello"
```

也可以使用项目脚本：

```powershell
uv run lora --workspace-root . chat --message "hello"
```

## 基本用法

创建并查看 session：

```powershell
uv run lora session create --case demo
uv run lora session show <session_id>
```

启动单轮对话：

```powershell
uv run lora chat --message "你好，帮我总结一下这个项目"
```

启动交互式对话：

```powershell
uv run lora chat
```

继续已有 session：

```powershell
uv run lora chat --session <session_id> --message "继续刚才的话题"
```

运行、回放和分析 case：

```powershell
uv run lora case run path\to\case.yaml
uv run lora case replay <session_id> <case_run_id>
uv run lora case analyze <session_id> <case_run_id>
```

运行回归套件：

```powershell
uv run lora regression run
```

从失败 run 生成并注册回归 case：

```powershell
uv run lora test generate <session_id> <case_run_id>
uv run lora test register path\to\generated-case.yaml
```

执行修复闭环：

```powershell
uv run lora repair plan <session_id> <case_run_id>
uv run lora repair apply path\to\repair-plan.json
uv run lora repair gate <repair_attempt_id>
```

## 配置 Agent

Lora 默认会读取 workspace 下的 `.env` 和 `lora.yaml`。推荐把密钥放在环境变量里，再通过 agent profile 引用。

示例 `lora.yaml`：

```yaml
agent:
  default_alias: dev

agents:
  - alias: dev
    model_request:
      api_key_env: DEEPSEEK_API_KEY
      model_name: deepseek-v4-flash
      base_url: https://api.deepseek.com
  - alias: fast-check
    model_request:
      api_key_env: DEEPSEEK_API_KEY
      model_name: deepseek-v4-flash
```

选择 agent：

```powershell
uv run lora --agent dev chat --message "hello"
```

临时覆盖模型：

```powershell
uv run lora --agent dev --model deepseek-v4-flash chat --message "hello"
```

运行产物只会记录 `agent_alias`、`model_name`、`api_key_source`、`base_url` 等脱敏元数据，不会落盘原始 API key。

## 默认 Agent 行为

默认 `AgentRuntimeAdapter` 会创建 `LoraAgent`。`LoraAgent` 会加载工作区 `.env`，读取已解析的 agent profile，并兼容 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_BASE_URL`。

如果没有可用 API key，Lora 不会发起外部模型调用，而是返回一条包含 agent alias 的固定提示。这让本地测试可以在没有真实模型凭据的情况下覆盖 CLI、session、trace 和 evaluation 链路。

当前默认工具来自 `pygent.toolkits`，并通过白名单注册：

- `bash`
- `read`
- `write`
- `edit`
- `glob`
- `grep`

工具调用会经过 `ToolInterceptor`，统一记录 `tool.call`、`tool.result`，并在默认路径下追踪 workspace 文件影响。

## 运行产物

Lora 的结构化运行证据默认写入 `.lora/`：

```text
.lora/
  sessions/
    {session_id}/
      session.json
      logs/
      context/
      cases/
        {case_id}/
          runs/
            {case_run_id}/
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
              analysis.json
```

其中 `events.jsonl` 是完整事实来源，其他 JSONL 和 JSON 文件是面向查询、评测和分析的投影或结果。

## 项目结构

```text
src/lora/
  cli.py              CLI 编排入口
  config.py           配置加载、agent profile 解析和 YAML 子集解析
  session.py          session 与 case run 生命周期
  case.py             case 加载、校验和 workspace 准备
  runtime.py          Agent 运行适配层
  agent.py            默认 LoraAgent、prompt 和工具注册
  tools.py            工具拦截、文件读取去重和文件影响追踪
  trace.py            事件存储和 JSONL 投影
  evaluation.py       deterministic evaluator
  analysis.py         失败归因
  regression.py       回归套件执行
  test_generation.py  回归 case 生成与注册
  repair.py           修复计划、attempt 捕获和 gate
  schema/             公共 dataclass 契约

tests/
  unit/               单元测试
  scenario/           CLI 与端到端场景测试

examples/             示例脚本
doc/                  使用文档
doc_design/           设计与开发文档
```

## 当前状态

已实现能力：

- `chat`、`session`、`case`、`optimize` 基础 CLI。
- Agent profile 选择与模型请求配置解析。
- append-only trace 与消息、工具、文件事件投影。
- deterministic case evaluation 与 rule-based failure analysis。
- regression manifest 执行与失败 case 生成注册。
- repair plan、manual diff attempt 捕获和 regression gate。
- workspace setup 的受控文件操作与路径安全检查。
- prompt 渲染记录、static prompt cache 和 prompt injection policy 基础链路。

规划中的能力：

- 完整 YAML 支持。
- agent profile CRUD 与多 backend adapter registry。
- 更细粒度的 root cause 策略和历史结果对比。
- repair agent 自动生成 patch attempt。
- 多 session 对比、非确定性检测和更完整的指标体系。

## 适用场景

Lora 适合用于：

- 本地开发和调试 Agent 工程。
- 把失败对话沉淀成可重复运行的回归 case。
- 观察 Agent 的工具调用、文件影响和上下文构造。
- 为 prompt、工具、runtime adapter 或模型配置变更建立回归保护。
- 研究 Agent 自我修复、自我评测和持续优化工作流。

## 更多文档

- `doc/lora-chat.md`：`lora chat` 使用说明。
- `doc_design/development-guide.md`：内部开发指南。
- `doc_design/`：架构设计、模块拆分和后续规划。
