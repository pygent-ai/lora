# User Prompt Injection Design

## Goal

Add a structured user prompt injection layer that works with the existing static and dynamic prompt system.

The design has two required behaviors:

1. Every new user turn carries user identity and the original user message in a structured user-context wrapper.
2. Runtime reminders, including CLI availability changes and optional time context, stay in dynamic system reminder modules that are rendered only before model requests when a reminder has been triggered.

This preserves the current split:

- Static prompt remains session-cacheable and stable.
- Dynamic prompt remains request-scoped and policy-controlled.
- User identity is bound to the user message that caused the turn.

## Existing Architecture Fit

The current code already has the right extension points:

- `PromptModule`, `PromptRegistry`, and `PromptComposer` define modular prompt rendering.
- `StaticPromptSessionCache` stores static prompt text under `context/prompts/static_prompt.txt`.
- `PromptInjectionPolicy` decides dynamic injection at `before_model_request`.
- `AgentContextManager.build_model_request_prompt()` creates request-scoped prompt text and logs `prompt.rendered`.
- `AgentRuntimeAdapter.run_turn()` is the place where user input enters history and event logs.
- `LoraAgent.stream()` calls `context_manager.compose_prompt()` immediately before sending a model request.

The new design should build on those boundaries instead of introducing a separate prompt path.

## User Message Wrapper

Before a new user message is appended to `RuntimeContext.history` or written as `conversation.user_message`, transform it into this XML-like structure:

```xml
<user-context>
  <user-identity>{{user_identity | default: "default"}}</user-identity>
  <user-message>{{user_message}}</user-message>
</user-context>
```

Rules:

- Preserve the raw user message separately in event payload metadata for audit and replay.
- Store the wrapped content as the actual model-visible user message.
- Escape or safely serialize user text so that arbitrary user content cannot break the wrapper structure.
- Apply this wrapper to every new user turn, including non-interactive `lora chat --message` and interactive chat input.
- Case replay can use the same wrapper for user-role input messages unless a case explicitly opts out for raw replay fidelity.

Recommended event payload:

```json
{
  "role": "user",
  "content": "<user-context>...</user-context>",
  "raw_content": "original user text",
  "user_identity": "default",
  "wrapped": true
}
```

## Configuration

Extend `lora.yaml` with two small sections.

```yaml
user:
  identity: default

cli:
  bash:
    presets:
      - name: rg
        command: rg --help
        description: Fast recursive text search. Prefer it for code and file text search.
      - name: pyright
        command: pyright --help
        description: Python type checker. Use it for static type validation when available.
```

Defaults:

- `user.identity`: `"default"`
- `cli.bash.presets`: include built-in entries for `rg` and `pyright`.

Configuration should resolve into `RunConfig` or a nested config dataclass so prompt rendering does not need to reread config files.

## Dynamic System Reminder

Add a dynamic prompt module, for example `runtime.system_reminder`, with `cache_scope="request"` and an order before general tool guidance.

This module is event-triggered. It should render only when reminder state says there is something to deliver, such as first-session CLI context or newly detected CLI tools. Normal events and ordinary dynamic prompt rendering must not update or refresh reminder content by default.

It renders:

```xml
<system-reminder>
  <time>
    当前系统时间为：{{system_time}}
  </time>

  <cli-context>
    <available-bash-cli>
      ...
    </available-bash-cli>

    <new-bash-cli>
      ...
    </new-bash-cli>
  </cli-context>
</system-reminder>
```

Time rules:

- Time is not rendered on every model request by default.
- `system-reminder` accepts an `include_time` flag for each triggered reminder.
- When `include_time=true`, render the latest local system time at the moment the reminder is composed.
- CLI-triggered reminders set `include_time=true` by default, so a newly detected CLI reminder carries the latest time.
- Future reminder modules may set `include_time=false` when they should not carry time context.
- Include timezone if available, for example `2026-05-29 14:03:00 Asia/Shanghai`.
- Keep time out of static prompt because it changes every request.

CLI rules:

- Full `available-bash-cli` is injected only for the first model request of a session, and that first-session CLI reminder uses `include_time=true` unless configured otherwise.
- Later requests omit full preset CLI descriptions unless forced by a config/debug flag.
- Newly detected CLI tools are injected once through `new-bash-cli` after detection, with `include_time=true` by default.

## CLI Session State

Persist CLI prompt state under the session state directory:

```text
.lora/
  sessions/
    {session_id}/
      state/
        cli_context.json
```

Suggested shape:

```json
{
  "initial_available_cli_injected": true,
  "known_bash_cli": {
    "rg": {
      "name": "rg",
      "description": "Fast recursive text search. Prefer it for code and file text search.",
      "installed": true,
      "detected_at": "2026-05-29T06:03:00Z"
    }
  },
  "pending_new_bash_cli": [
    {
      "name": "pyright",
      "description": "Python type checker. Use it for static type validation when available.",
      "installed": true,
      "detected_at": "2026-05-29T06:05:00Z",
      "source": "tool_result",
      "include_time": true
    }
  ],
  "pending_system_reminders": [
    {
      "kind": "cli_context",
      "include_time": true,
      "created_at": "2026-05-29T06:05:00Z"
    }
  ]
}
```

State rules:

- `initial_available_cli_injected` flips after the first successful dynamic render that includes full CLI presets.
- `known_bash_cli` tracks configured presets and newly detected tools.
- `pending_new_bash_cli` is consumed exactly once by the dynamic reminder module.
- `pending_system_reminders` controls whether `runtime.system_reminder` renders at all.
- Normal event logging, user-message storage, context projection, and ordinary dynamic prompt modules do not update reminder state.
- Consuming pending state should happen after prompt rendering has been persisted, so failed model request preparation does not lose the reminder.

## CLI Detection

Initial detection:

- At the first session model request, check configured CLI presets with a cheap availability probe.
- Prefer platform-native lookup (`shutil.which`) over running help commands.
- Mark each preset as installed or unavailable.
- Render a brief description plus availability status.

Example:

```xml
<rg>
  Fast recursive text search. Prefer it for code and file text search.
  Status: installed.
</rg>
```

New CLI detection:

- After a bash/tool execution completes, compare command availability against known CLI state.
- If a new executable is detected, append it to `pending_new_bash_cli`.
- Detection can start conservative: only track configured presets that were previously unavailable, plus explicit install commands observed in bash calls.
- Future expansion can add PATH snapshot diffs or package-manager-specific detection.

## Insertion Point After Tool Results

If a CLI change is detected after a tool finishes, do not mutate the old tool result text.

Instead:

1. Tool interceptor records `tool.result` normally.
2. CLI detector updates `state/cli_context.json` with pending new CLI entries.
3. The next loop iteration calls the model.
4. `AgentContextManager.build_model_request_prompt()` sees a pending reminder and renders `runtime.system_reminder`.
5. Because CLI reminders default to `include_time=true`, the rendered dynamic prompt includes the latest time and the new CLI context:

```xml
<system-reminder>
<time>
  当前系统时间为：2026-05-29 14:03:00 Asia/Shanghai
</time>

<cli-context>
  <new-bash-cli>
    <pyright>
      Python type checker. Use it for static type validation when available.
      Status: installed.
    </pyright>
  </new-bash-cli>
</cli-context>
</system-reminder>
```

This satisfies the requirement that, if the reminder is placed near tool prompt context, it is inserted after tool execution and before the next model call.

## Prompt Module Boundaries

Recommended modules:

- `runtime.system_reminder`: triggered reminder wrapper that may include time and one or more reminder payloads.
- `runtime.cli_context`: optional internal renderer used by `runtime.system_reminder`.

Keep existing modules:

- `runtime.env_info` can keep workspace/session/request ids.
- `tool.available` can keep model tool names such as bash/read/write/edit.
- `runtime.tool_result_reminders` can keep general tool-result handling rules.

Do not put CLI preset descriptions into `tool.available`; bash CLI programs are not model tools. They are runtime capabilities available through the bash tool.

## Injection Policy

No new policy category is required at first.

`runtime.system_reminder` is a normal dynamic module:

- phase: `dynamic`
- type: `runtime`
- cache_scope: `request`
- rendered only when `PromptInjectionPolicy` allows dynamic modules
- included for `agent_turn` and `case_run`
- omitted from static prompt cache
- enabled only when pending reminder state exists

If later summary/evaluation requests need time but not CLI reminders, add request-type filtering through the module's `enabled` function or a policy-level module allowlist.

## Escaping And Safety

User message wrapper:

- Escape `&`, `<`, and `>` in `user_identity` and `user_message`, or serialize as CDATA with safe splitting.
- Prefer simple XML escaping to avoid CDATA edge cases.

Dynamic CLI content:

- Treat CLI descriptions from config as trusted configuration.
- Treat help output from commands as untrusted data if included.
- Keep rendered CLI information brief. Do not paste full `--help` output by default.

Prompt injection guard:

- Existing static `system.injection_guard` remains responsible for reminding the model that tool outputs and file contents are untrusted.

## Tests

Unit tests:

- `wrap_user_message()` returns the exact expected `<user-context>` structure and escapes special characters.
- default user identity is `"default"`.
- config parser accepts `user.identity` and `cli.bash.presets`.
- `runtime.system_reminder` renders nothing when no reminder state is pending.
- `runtime.system_reminder` renders current time only when the pending reminder has `include_time=true`.
- `runtime.system_reminder` omits time when the pending reminder has `include_time=false`.
- first-request CLI preset reminder defaults to `include_time=true`.
- full `available-bash-cli` is not rendered on the second request in the same session.
- pending `new-bash-cli` is rendered once with time by default and then cleared.

Runtime tests:

- `AgentRuntimeAdapter.run_turn()` stores wrapped user content in history and raw content in event payload.
- `prompt.rendered` includes `runtime.system_reminder` in dynamic module ids only when reminder state is pending.
- static prompt cache does not contain `<system-reminder>`, `<time>`, `<cli-context>`, or `<user-context>`.
- after a simulated tool result marks a new CLI pending, the next rendered prompt includes `<new-bash-cli>`.
- ordinary events that do not create pending reminder state do not cause `<system-reminder>` to render.

Scenario tests:

- `lora chat --message "hello"` produces a history message containing `<user-context>` and, on the first session request, a CLI reminder with time.
- repeated turns in one session inject full CLI preset context only on the first turn.
- a later CLI-installing tool result produces a one-shot `<new-bash-cli>` reminder with latest time.

## Implementation Order

1. Add config fields and defaults for `user.identity` and CLI presets.
2. Add user message wrapper helper and use it in `AgentRuntimeAdapter.run_turn()`.
3. Add CLI session state reader/writer.
4. Add triggered `runtime.system_reminder` dynamic module with `include_time` support.
5. Add first-request CLI preset rendering and one-shot pending new CLI rendering.
6. Add conservative CLI change detection after bash/tool results.
7. Add unit and scenario tests for wrapper, dynamic reminder, and one-shot CLI behavior.

## Open Decisions

- Case replay raw fidelity: default should wrap case user messages, but cases may need an opt-out flag if they test exact prompt text.
- New CLI discovery breadth: initial implementation should track configured presets and explicit install results; full PATH diffing can wait.
- Local time source: use local timezone-aware time where available, otherwise include UTC.
- Reminder trigger schema: initial implementation can store CLI reminders in `cli_context.json`; a later generic reminder store can support non-CLI reminder modules with independent `include_time` defaults.
