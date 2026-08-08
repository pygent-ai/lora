# API Key 管理

Lora 只接受 `api_key_env` 引用，不接受在 `lora.yaml` 中直接写入 API key。

## 支持的凭证来源

按优先级依次为：

1. 进程环境变量
2. OS 凭据库中的同名条目（安装 `keyring` 后可用）
3. `~/.lora/credentials.env`
4. `<workspace>/.env.local`

凭证文件只补充尚未存在的环境变量，因此进程环境变量始终优先。运行产物只记录 `api_key_source`，不会记录原始 key。

## 模型配置

```yaml
agents:
  - alias: default
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

每个 route 必须通过 `api_key_env` 指定凭证变量。未知配置字段会直接导致配置加载失败。

## CLI 管理

```powershell
uv run lora credentials set DEEPSEEK_API_KEY
uv run lora credentials list
uv run lora credentials validate
uv run lora credentials delete DEEPSEEK_API_KEY
```

`set` 默认写入 `~/.lora/credentials.env`。若项目需要独立凭证，可在 workspace 创建 `.env.local`：

```dotenv
DEEPSEEK_API_KEY=replace-with-real-key
```

## 安全约束

- 不要提交 `credentials.env` 或 `.env.local`。
- 不要在配置、日志、测试快照或错误消息中输出原始 key。
- 测试应使用临时 `user_lora_root` 和伪造凭证。
- 调试凭证时使用 `lora credentials validate`，不要打印解析后的 key。

## 常见问题

当 `api_key_source` 为 `missing` 时，依次检查：

1. 当前 agent alias 和 route 是否正确。
2. `api_key_env` 指向的变量名是否正确。
3. 进程环境、`~/.lora/credentials.env` 或 workspace `.env.local` 是否提供该变量。
4. 使用 OS 凭据库时，`keyring` 后端是否可用。
