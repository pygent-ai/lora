# User Prompt Injection Design

> Updated after the prompt routing refactor. The earlier design used request-scoped `runtime.system_reminder`; that module no longer exists. Runtime reminders now live in user/tool messages, while request-scoped system guidance uses `request_system` prompt modules.

## Goal

Provide a structured user/tool message injection layer that coexists with the current system prompt composition model:

1. Every new user turn carries user identity and the original user message in a structured `<user-context>` wrapper.
2. Initial CLI and skill availability context is appended to the user message inside one `<system-reminder>`.
3. New CLI or skill discoveries after tool execution are appended to the tool message inside one `<system-reminder>`.
4. Reminder content is not registered as a system prompt module and does not enter static prompt cache or request-system prompt.

## Current Prompt Split

The model-visible request has three relevant surfaces:

| Surface | Current source | Notes |
| --- | --- | --- |
| Static system prompt | `phase="static"` modules | Session-cacheable; stored in `context/prompts/static_prompt.txt` |
| Request system prompt | `phase="request_system"` modules | Re-rendered before each model request; appended directly after the static system prompt |
| Conversation messages | runtime history | User/tool messages may include `<system-reminder>` |

Request system modules currently include:

- `tool.available`
- `system.tool_result_reminders`
- `system.token_budget`

The following reminder modules are intentionally absent:

- `runtime.system_reminder`
- `runtime.skill_reminder`

## User Message Wrapper

Before a new user message becomes model-visible, wrap it as:

```xml
<user-context>
  <user-identity>{{user_identity | default: "default"}}</user-identity>
  <user-message>{{user_message}}</user-message>
</user-context>
```

Rules:

- Preserve raw user text separately in event payload metadata for audit and replay.
- Store wrapped content as the model-visible user message.
- Append initial reminder content after the wrapped user context when available.

Example with initial reminders:

```xml
<user-context>
  <user-identity>default</user-identity>
  <user-message>帮我检查项目</user-message>
</user-context>

<system-reminder>
  <cli-context>
    <available-bash-cli>
      ...
    </available-bash-cli>
  </cli-context>
  <skills-context>
    <available-skills>
      ...
    </available-skills>
  </skills-context>
</system-reminder>
```

## Initial Reminder Rules

Initial reminders are produced by the agent/context manager and appended by:

- `AgentRuntimeAdapter.run_turn()` for normal chat turns.
- `run_case()` for the first case input user message.

Rules:

- Render the initial CLI list only when the session state says it has not been shown.
- Render the initial skill list by scanning available skills and consuming the reminder state.
- If both CLI and skill context exist, use one outer `<system-reminder>` containing both `<cli-context>` and `<skills-context>`.
- Do not add a second reminder module to the system prompt.
- Do not include reminder content in `prompt.rendered` system prompt text.

## Tool-Side Reminder Rules

After a tool result is produced, runtime may detect newly available CLI tools or newly created/changed skills.

Rules:

- New discoveries are appended to the tool message, not to the next system prompt.
- Use the same outer `<system-reminder>` wrapper as initial reminders.
- Include only newly discovered entries, not the full initial list.
- Consume pending reminder state immediately after rendering so each discovery is shown once.

Example:

```xml
<tool-result>
  ...
</tool-result>

<system-reminder>
  <cli-context>
    <new-bash-cli>
      ...
    </new-bash-cli>
  </cli-context>
  <skills-context>
    <new-skills>
      ...
    </new-skills>
  </skills-context>
</system-reminder>
```

## Relationship To System Prompt Modules

The message injection layer must stay separate from system prompt module composition.

| Content | Location |
| --- | --- |
| Identity, policy, coding rules, path rules | static system prompt |
| Available model tools | `tool.available` request-system module |
| Tool result handling guidance | `system.tool_result_reminders` request-system module |
| Context budget guidance | `system.token_budget` request-system module |
| Initial CLI/skill availability | user message `<system-reminder>` |
| New CLI/skill discoveries | tool message `<system-reminder>` |

This avoids two common bugs:

- Rendering reminders into a prompt artifact but not sending them to the model.
- Sending the same reminder twice through both user/tool messages and system prompt modules.

## Persistence And Audit

System prompt audit:

- `prompt.rendered` records the complete system prompt actually sent to the model.
- `request_system_module_ids` lists request-scoped system modules.
- `dynamic_module_ids` is a legacy placeholder and should stay empty.
- `session.json.system_prompt` stores the latest complete system prompt.

Message audit:

- User message history stores the model-visible wrapped content, including initial `<system-reminder>` when present.
- Tool message history stores the tool result plus any tool-side `<system-reminder>`.
- Raw user text should remain available in event metadata where applicable.

## Test Coverage

Unit tests should assert:

- `wrap_user_message()` returns the expected `<user-context>` structure and escapes special characters.
- Initial skill/CLI reminders appear in the first user message and not in the rendered system prompt.
- Tool-side new skill/CLI reminders appear in tool messages and are consumed once.
- The prompt registry does not include `runtime.system_reminder` or `runtime.skill_reminder`.
- Request-system modules are named `system.tool_result_reminders` and `system.token_budget`.

Runtime and scenario tests should assert:

- `AgentRuntimeAdapter.run_turn()` stores wrapped user content and appends initial reminders.
- `run_case()` appends initial reminders to the first case user message.
- `prompt.rendered` includes `request_system_module_ids`.
- `session.json.system_prompt` contains the complete system prompt without any synthetic boundary marker.
- Static prompt cache does not contain `<system-reminder>`, `<cli-context>`, or `<skills-context>`.

## Implementation Order

The current implementation should follow this order when changed again:

1. Update tests for the model-visible message location first.
2. Keep reminder state and renderers in the agent/context manager layer.
3. Keep request-scoped system guidance in `phase="request_system"` modules.
4. Write `prompt.rendered` and session prompt only from the actual system prompt passed to the model.
5. Verify no removed runtime reminder modules are registered or referenced by active code.
