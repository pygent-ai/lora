# Native Pygent model configuration design

## Status

Approved direction: replace Lora's route-based model configuration with Pygent's native model configuration. Do not migrate or execute the legacy `routes` and `fallback` format. The user's existing configuration was copied verbatim to `C:\Users\Administrator\.lora\config.pre-model-connections-20260915-190310.yaml` before this design was written.

## Goals

- Store model definitions and model groups in the exact mapping accepted by `pygent.ModelConfig.from_mapping()`.
- Let a user work with multiple provider connections and select credential-visible models from each connection.
- Let a user configure multiple named Pygent `ModelGroup` values and choose one when creating a conversation.
- Keep a conversation's model group fixed while allowing its preferred child model to change between turns. The preferred model runs first and the remaining group members retain their configured fallback order.
- Use Pygent's provider, capability, connection, client, adapter, model-catalog, and model-group types instead of parallel Lora equivalents.
- Reuse existing credential references without reading or displaying secret values.

## Non-goals

- No automatic conversion of legacy routes.
- No Lora-specific named-connection schema or connection-to-model compiler.
- No secret export, secret reveal, or secret copy into `config.yaml`.
- No automatic model choice based on capabilities.
- No live paid model invocation as part of configuration.

## Authoritative configuration

The user configuration keeps Lora's non-model settings, but its model subtree is a native Pygent mapping:

```yaml
connections:
  deepseek-main:
    provider: deepseek
    credential: {env: DEEPSEEK_API_KEY}
    protocols:
      openai_chat_completions: {base_url: https://api.deepseek.com}
    verify_ssl: true
  private-main:
    provider: deepseek
    credential: {env: DEEPSEEK_API_KEY_2}
    protocols:
      openai_chat_completions: {base_url: http://113.46.219.251:8080/v1}
    verify_ssl: true

models:
  deepseek_primary:
    connection: deepseek-main
    model_id: deepseek-v4-flash
    protocol: openai_chat_completions
    provider_options: {}
    capabilities:
      modalities: {input: [text], output: [text]}
      streaming: {output: [text]}
      tools:
        call: true
        choice: [none, auto, required, named]
        parallel: true
      structured_output: {json_object: true, json_schema: false}
      reasoning: {supported: true, controllable: true}
      limits: {context_tokens: 1000000, max_output_tokens: 384000}

  private_secondary:
    connection: private-main
    model_id: private-model-id
    protocol: openai_chat_completions
    provider_options: {}
    capabilities:
      modalities: {input: [text], output: [text]}
      streaming: {output: [text]}
      tools:
        call: true
        choice: [none, auto, required, named]
        parallel: true
      structured_output: {json_object: true, json_schema: false}
      reasoning: {supported: true, controllable: true}
      limits: {context_tokens: null, max_output_tokens: null}

model_groups:
  coding:
    models: [deepseek_primary, private_secondary]

agent:
  default_alias: dev

agents:
  - alias: dev
    model_request:
      default_model_group: coding
      context_window: 1000000
      retry:
        max_attempts_per_model: 2
        attempt_idle_timeout_seconds: 60
        backoff_initial: 0.5
        backoff_maximum: 4
        backoff_multiplier: 2
```

The `connections`, `models`, and `model_groups` values are passed unchanged to `ModelConfig.from_mapping()`. Pygent validates unknown fields, URLs, credentials, capabilities, duplicate entries, and group references. Lora validates only its surrounding application settings and that every agent's default references an existing model group.

Pygent 0.3.16 defines reusable top-level `ConnectionConfig` values. Multiple models reference one connection by key and independently select one of its protocol endpoints. Lora persists this contract directly and does not introduce a parallel connection abstraction.

## Cutover and unconfigured state

Legacy `agents[].model_request.routes` or `fallback` fields are never converted or used for execution. Their presence produces a structured `legacy_model_configuration` diagnostic that points to the saved backup and asks the user to configure models again.

The local API must still start when no valid model configuration exists. In this state:

- non-model settings and the Settings screen remain available;
- chat execution is disabled with a clear `model_configuration_required` message;
- saving a valid native Pygent configuration replaces the obsolete model fields while preserving unrelated user settings;
- the existing credential store remains untouched.

No default route is silently synthesized. A missing user config starts in the same explicit unconfigured state.

## Credential handling

Connection forms select a credential environment-variable name. The settings response may report only the name and a boolean/source label such as `user-file`, `environment`, `keyring`, or `missing`; it never returns the value.

When the user keeps `DEEPSEEK_API_KEY` or `DEEPSEEK_API_KEY_2`, the existing credential store supplies the secret at the deployment boundary. An optional new secret entered while saving is written through the existing credential service and removed from the configuration payload before persistence. Blank secret inputs preserve existing credentials.

## Provider and model discovery

The backend exposes serialized data from `ProviderCatalog.builtin()`, `ModelCapabilityCatalog.builtin()`, and `CapabilityPresetCatalog.builtin()`. The Settings UI uses these values for provider labels, supported protocols, default base URLs, credential names, provider-options forms, and complete capability records.

For a connection draft, the backend constructs the native Pygent client selected by protocol and resolves only the referenced credential. If that client exposes the optional `models: ModelCatalog` capability, the backend calls `await client.models.list(timeout=...)` and returns normalized `ModelInfo` records. It always closes the client.

If live listing is unsupported or fails, the response contains a structured per-connection error. The UI then offers models from Pygent's bundled capability catalog filtered by provider and protocol, plus an explicit custom model ID path. A discovery failure never deletes an already configured model.

Only protocols with an installed Pygent adapter can be saved as executable Lora models. Catalog-only media protocols remain visible as unsupported information and cannot be added to a text-agent group.

## Settings experience

The model section has three ordered steps:

1. **Connections**: add a provider/protocol/base URL/credential reference, then load models. These are editor drafts derived into native model entries, not a persisted Lora schema.
2. **Models**: select one or more models per connection, assign stable unique model keys, review capabilities, and optionally edit custom capabilities/provider options.
3. **Model groups**: create multiple named groups and order selected model keys. Drag or arrow controls change Pygent fallback order. Each agent selects one default group for new conversations.

Saving sends one complete replacement of `models`, `model_groups`, and the agent-to-group references. The backend validates the native subtree with `ModelConfig.from_mapping()` before atomically rewriting `config.yaml`. Partial invalid state remains only in the browser draft.

The new-conversation action includes a required model-group selector, defaulted from the selected agent. The created Session persists both `model_group_name` and `selected_model_key`; the initial selected model is the first entry in the configured group. Different conversations may use different groups concurrently.

An idle conversation exposes a child-model selector containing only members of its fixed group. Switching it updates `selected_model_key` atomically and affects the next turn. Switching is rejected while that conversation has an active execution, and the group selector is not shown after creation.

The general Settings summary displays the configured groups and their ordered `model_key / provider / model_id` entries. Conversation chrome displays its fixed group and current preferred model. It no longer displays routes or a separate fallback list.

## Runtime assembly

`RunConfig` holds the parsed Pygent `ModelConfig` and the agent's default Pygent `ModelGroup`, rather than Lora `ModelRouteConfig` objects. A Session selection resolves the fixed group and preferred model for an execution. Retry fields use Pygent terminology (`max_attempts_per_model`).

At the deployment boundary Lora:

- resolves each referenced `ConnectionConfig.credential` through the existing credential sources;
- creates one native Pygent client per model key using its protocol and connection;
- creates one adapter per used protocol;
- constructs `DefaultModelInvoker`/the existing event-projection subclass with those maps;
- binds the Agent to a deferred Pygent model-group requirement named for the Session's fixed native group;
- publishes one immutable dynamic profile per child model. A profile contains the selected child first, followed by every other group member in the group's configured order;
- selects the Session's current child-model profile with Pygent `ModelCallOptions` at execution admission, so an in-flight execution cannot change order;
- closes all clients through the invoker lifecycle.

The runtime no longer fabricates a capability preset, guesses DeepSeek from a URL, forces `openai_chat_completions`, or translates fallback IDs. Trace and usage events use Pygent `model_key`, `provider`, `model_id`, and group name directly.

Changing the preferred child does not create a different Lora model group and does not disable fallback. For a configured group `[A, B, C]`, selecting `B` produces the Pygent execution order `[B, A, C]`; selecting `C` produces `[C, A, B]`. Pygent's normal continuation rule remains authoritative inside an admitted execution.

## API contracts

- `GET /settings` returns configuration status, native safe model entries, native groups, active agent/group, retry settings, and credential presence metadata.
- `PATCH /settings` accepts a complete native model-config replacement plus agent group selections and optional transient credential values.
- `GET /settings/model-catalogs` returns the bundled Pygent provider/capability catalogs in JSON-safe form.
- `POST /settings/models/discover` accepts one connection draft and returns native `ModelInfo` values or a structured discovery error.
- Session creation accepts `model_group_name`; omission uses the selected agent's default group.
- Session detail returns immutable `model_group_name`, current `selected_model_key`, and the group's selectable model summaries.
- A dedicated Session update operation changes only `selected_model_key` and requires the Session to be idle.

The OpenAPI contract and API documentation are regenerated or updated with these shapes. Raw credentials are forbidden in all response models, logs, and validation errors.

## Errors and concurrency

- Native Pygent validation errors become HTTP 422 responses with the failing configuration path where available.
- Missing credentials do not prevent saving a model, but mark it unconfigured and prevent chat execution through that model group.
- Discovery authentication, timeout, TLS, transport, and invalid-response errors remain scoped to the connection being tested.
- A Session model-group change is rejected after creation. A child-model change is rejected if the key is outside the fixed group or the Session is running.
- Removing a group referenced by any Session, or removing a model currently selected by a Session, is rejected. Adding group members or changing fallback order affects only later execution admissions.
- Settings writes continue to use an atomic temporary-file replacement.
- Runtime reload occurs only after the new file validates. If reload fails, the API reports the failure and retains the valid saved configuration for diagnosis; it does not restore legacy routes.

## Testing

Implementation follows test-first changes for:

- strict acceptance of native Pygent config and rejection of legacy routes/fallback;
- explicit unconfigured startup and disabled chat behavior;
- preservation of unrelated settings and existing credential references;
- Pygent catalog serialization and protocol-specific live model discovery using local HTTP fixtures;
- client closure on discovery success, validation failure, timeout, and cancellation;
- native group ordering as runtime fallback ordering;
- multiple groups and different connections in one configuration;
- different conversations selecting different fixed groups;
- idle child-model switching and selected-first fallback order (`[A, B, C]` to `[B, A, C]`);
- rejection of group switching, out-of-group child selection, running-session switching, and removal of referenced configuration;
- persistence and recovery of the fixed group and selected child model;
- settings request/response secrecy;
- desktop connection/model/group editing, validation, and payload generation;
- existing API, CLI, runtime, scenario, and desktop regression suites.

No external provider credential or paid model call is required for automated verification.
