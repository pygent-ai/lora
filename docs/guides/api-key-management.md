# API Key 管理

Lora 只接受 Pygent 原生 `connection.credential.env` 引用，不接受在配置文件中直接写入 API key。

## 支持的凭证来源

按优先级依次为：

1. `~/.lora/credentials.env`
2. 进程环境变量
3. OS 凭据库中的同名条目（安装 `keyring` 后可用）

用户凭据文件优先，确保 CLI、Desktop 和不同项目读取同一个用户 API key。运行产物只记录 `api_key_source`，不会记录原始 key。

## 用户模型配置

模型和凭据引用统一写入 `~/.lora/config.yaml`：

```yaml
models:
  deepseek-chat:
    provider: deepseek
    model_id: deepseek-chat
    protocol: openai_chat_completions
    connection:
      base_url: https://api.deepseek.com
      credential:
        env: DEEPSEEK_API_KEY
      verify_ssl: true
    # provider_options 和 capabilities 见 user-config.yaml.example
```

需要认证的 connection 必须通过 `credential.env` 指定凭证变量；本地免认证服务使用
`credential: {none: true}`。未知配置字段会直接导致配置加载失败。

## CLI 管理

```powershell
uv run lora credentials set DEEPSEEK_API_KEY
uv run lora credentials list
uv run lora credentials validate
uv run lora credentials delete DEEPSEEK_API_KEY
```

`set` 默认写入唯一的用户凭据文件 `~/.lora/credentials.env`。

```dotenv
DEEPSEEK_API_KEY=replace-with-real-key
```

## 安全约束

- 不要提交 `credentials.env`。
- 不要在配置、日志、测试快照或错误消息中输出原始 key。
- 测试应使用临时 `user_lora_root` 和伪造凭证。
- 调试凭证时使用 `lora credentials validate`，不要打印解析后的 key。

## 常见问题

当凭据状态为 `missing` 时，依次检查：

1. 当前 agent alias、模型组和子模型是否正确。
2. `~/.lora/config.yaml` 中 `connection.credential.env` 指向的变量名是否正确。
3. `~/.lora/credentials.env`、进程环境或系统 keyring 是否提供该变量。
4. 使用 OS 凭据库时，`keyring` 后端是否可用。
