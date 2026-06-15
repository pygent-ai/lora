# API Key 管理

本文档说明 Lora 如何安全存放、加载和配置模型 API key，供日常使用和后续开发参考。

## 设计原则

1. **密钥与源码分离**：不要把真实 API key 写进可提交的 `lora.yaml` 或开发仓库目录。
2. **配置只引用变量名**：`lora.yaml` 通过 `api_key_env` 声明需要哪个环境变量，不保存明文密钥。
3. **用户级一份、项目级可覆盖**：默认放在 `~/.lora/credentials.env`，需要时可用项目级 `.env.local` 覆盖。
4. **运行产物脱敏**：`run_config.json`、事件流和 session 文件只记录 `api_key_source`，不记录原始 key。

## 凭证存放位置

| 层级 | 路径 | 适用场景 | 是否提交到 Git |
| --- | --- | --- | --- |
| 进程环境变量 | `$env:DEEPSEEK_API_KEY` / `export DEEPSEEK_API_KEY=...` | CI/CD、临时覆盖 | 否 |
| 用户级凭证文件 | `~/.lora/credentials.env` | 日常开发（推荐） | 否 |
| OS 凭据库 | Windows Credential Manager / macOS Keychain | 不想落盘明文文件时 | 否 |
| 项目级覆盖 | `<workspace>/.env.local` | 某项目需要不同 key | 否 |
| 遗留工作区 `.env` | `<workspace>/.env` | 旧项目迁移期兼容 | 否 |
| 明文配置字段 | `lora.yaml` 的 `api_key:` | 已废弃，会触发警告 | 禁止 |

## 加载顺序

`load_run_config()` 会按以下顺序把凭证文件加载进进程环境，且**不会覆盖已经存在的环境变量**：

```text
1. 进程环境变量（最高优先级，不会被文件覆盖）
2. ~/.lora/credentials.env
3. <workspace>/.env.local
4. <workspace>/.env（遗留，会打印 DeprecationWarning）
```

随后根据当前 agent profile 解析 API key：

```text
1. model_request.api_key_env 指向的变量
2. OS 凭据库中的同名变量（安装 keyring 后可用）
3. lora.yaml 明文 api_key（废弃，触发 DeprecationWarning）
4. 回退到 DEEPSEEK_API_KEY
```

解析结果会写入 `ResolvedAgentConfig.api_key`（仅内存）和 `api_key_source`（可落盘的安全元数据）。

## 推荐配置

### 1. 复制配置模板

```powershell
Copy-Item lora.yaml.example lora.yaml
```

`lora.yaml` 只保留 agent 配置，不写密钥：

```yaml
agent:
  default_alias: dev

agents:
  - alias: dev
    model_request:
      api_key_env: DEEPSEEK_API_KEY
      model_name: deepseek-v4-flash
      base_url: https://api.deepseek.com
```

### 2. 保存 API key

**推荐：用户级凭证文件**

```powershell
uv run lora credentials set DEEPSEEK_API_KEY
```

命令会把密钥写入 `%USERPROFILE%\.lora\credentials.env`（Unix 上权限为 `600`）。

**可选：OS 凭据库**

```powershell
uv add keyring
uv run lora credentials set DEEPSEEK_API_KEY --keyring
```

**可选：项目级覆盖**

在目标工作区创建 `.env.local`：

```text
DEEPSEEK_API_KEY=sk-project-specific
```

### 3. 验证配置

```powershell
uv run lora credentials validate
uv run lora --agent dev chat -m "hello"
```

`validate` 成功时返回：

```json
{
  "agent_alias": "dev",
  "api_key_env": "DEEPSEEK_API_KEY",
  "api_key_source": "env:DEEPSEEK_API_KEY",
  "status": "ok"
}
```

## CLI 命令

```powershell
# 列出已配置的变量名（不显示值）
uv run lora credentials list

# 交互式写入用户凭证文件
uv run lora credentials set DEEPSEEK_API_KEY

# 非交互写入（注意 shell 历史）
uv run lora credentials set DEEPSEEK_API_KEY --value sk-...

# 写入 OS 凭据库
uv run lora credentials set DEEPSEEK_API_KEY --keyring

# 删除用户凭证文件中的变量
uv run lora credentials delete DEEPSEEK_API_KEY

# 删除 OS 凭据库中的变量
uv run lora credentials delete DEEPSEEK_API_KEY --keyring

# 检查当前 agent profile 是否能解析到 key
uv run lora credentials validate
uv run lora --agent dev credentials validate
```

## GUI 配置

在 Lora GUI 中打开 **Settings**，可以看到：

- **API key env**：当前 agent profile 使用的环境变量名，例如 `DEEPSEEK_API_KEY`
- **API key status**：当前解析状态，例如 `Configured (env:DEEPSEEK_API_KEY)`
- **API key**：密码输入框，留空表示保留现有密钥，填写后会写入 `~/.lora/credentials.env`

GUI 不会把 API key 写入项目状态文件或 `lora.yaml`。

## 多 Provider / 多 Agent

为不同 agent 使用不同变量名：

```yaml
agents:
  - alias: deepseek
    model_request:
      api_key_env: DEEPSEEK_API_KEY
      model_name: deepseek-v4-flash
  - alias: openai
    model_request:
      api_key_env: OPENAI_API_KEY
      model_name: gpt-4o
```

分别保存：

```powershell
uv run lora credentials set DEEPSEEK_API_KEY
uv run lora credentials set OPENAI_API_KEY
```

## 从开发目录迁移

如果之前把密钥放在仓库根目录的 `.env`：

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.lora"
Move-Item .env "$env:USERPROFILE\.lora\credentials.env"
uv run lora credentials validate
```

迁移后，开发仓库内不再保留任何密钥文件。

## 开发参考

### 关键模块

| 模块 | 职责 |
| --- | --- |
| `src/lora/secrets.py` | 凭证文件读写、加载链、keyring 集成 |
| `src/lora/config.py` | 启动时调用 `load_credentials()`，解析 agent profile |
| `src/lora/credentials_cli.py` | `lora credentials` 子命令 |
| `src/lora/redaction.py` | 持久化前脱敏 |
| `src/gui/widgets/settings.py` | GUI 中的 API key 配置界面 |

### 新增密钥类型时

1. 在 `lora.yaml` 增加 agent profile，设置 `api_key_env`
2. 用 `lora credentials set <ENV_NAME>` 保存密钥
3. 确认 `api_key_source` 只记录来源标签，不记录明文

### 测试约定

- 单元测试使用临时目录作为 `user_lora_root`，不要读写真实 `~/.lora/credentials.env`
- 断言运行产物时使用 `api_key_source`，不要断言原始 key
- 需要覆盖加载链时，通过 `load_credentials(user_lora_root=..., workspace_root=...)` 注入临时文件

### 安全约束

- 不要把 `credentials.env`、`.env.local`、`.env` 提交到 Git
- 不要在 `lora.yaml` 写 `api_key: sk-...`
- Agent 的 bash/read 工具可以读取工作区文件，因此不要把凭证文件放在 workspace 内
- 若必须调试密钥问题，用 `lora credentials list` 和 `validate`，不要 `print(api_key)`

## 常见问题

### `api_key_source` 为 `missing`

说明当前 agent profile 没有解析到可用 key。检查：

1. `lora credentials validate` 输出中的 `api_key_env`
2. `~/.lora/credentials.env` 是否存在对应变量
3. `lora.yaml` 中 agent alias 是否匹配

### 项目和工作区 key 不一致

在目标 workspace 下创建 `.env.local`，项目级文件会覆盖用户级默认值（但仍低于显式进程环境变量）。

### 遗留 `.env` 警告

把密钥迁到 `~/.lora/credentials.env` 后删除工作区 `.env`，警告会消失。
