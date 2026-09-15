from __future__ import annotations


def native_model_config_yaml(
    *,
    alias: str = "default",
    model_key: str = "primary",
    model_id: str = "test-model",
    base_url: str = "https://example.test/v1",
    credential_env: str = "LORA_TEST_API_KEY",
    eternal_conversation: bool = False,
) -> str:
    enabled = "true" if eternal_conversation else "false"
    return f"""models:
  {model_key}:
    provider: test
    model_id: {model_id}
    protocol: openai_chat_completions
    connection:
      base_url: {base_url}
      credential:
        env: {credential_env}
      verify_ssl: true
    provider_options: {{}}
    capabilities:
      modalities:
        input: [text]
        output: [text]
      streaming:
        output: [text]
      tools:
        call: true
        choice: [auto]
        parallel: false
      structured_output:
        json_object: true
        json_schema: false
      reasoning:
        supported: false
        controllable: false
      limits:
        context_tokens: 1000
        max_output_tokens: 100
model_groups:
  coding:
    models: [{model_key}]
agent:
  default_alias: {alias}
agents:
  - alias: {alias}
    model_request:
      default_model_group: coding
runtime:
  approvals:
    enabled: false
eternal_conversation:
  enabled: {enabled}
"""
