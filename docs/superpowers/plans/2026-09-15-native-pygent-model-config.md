# Native Pygent Model Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Lora's route/fallback model configuration with native Pygent models and model groups, while fixing a conversation to one group and allowing idle-time preferred-model changes that retain fallback.

**Architecture:** The user YAML owns the exact `models` and `model_groups` mapping parsed by `pygent.ModelConfig.from_mapping()`. `RunConfig` carries that immutable native config plus an agent default group; each Session persists its fixed group and preferred child, and runtime admission selects a Pygent dynamic profile whose order is preferred-first followed by the configured remainder. Settings and discovery expose safe native metadata only, while the desktop edits a complete draft and uses explicit Session APIs for model selection.

**Tech Stack:** Python 3.13, Pygent 0.3.15, FastAPI/Pydantic, pytest, React 19, Electron/Vite, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-15-native-pygent-model-config-design.md`

## Global Constraints

- Persist `models` and `model_groups` in the exact mapping accepted by `pygent.ModelConfig.from_mapping()`; do not introduce a Lora connection or model-group schema.
- Reject legacy `agents[].model_request.routes` and `fallback`; do not migrate or execute them.
- Keep an unconfigured installation bootable for Settings, but reject chat execution until a valid native model config and group exist.
- Store only credential environment-variable references in YAML and API responses; never return, log, or persist credential values in session/run data.
- A Session chooses its group only at creation. It may change its preferred child only while idle, and fallback remains enabled in configured group order.
- Use Pygent provider clients, protocol adapters, catalogs, `ModelGroup`, dynamic profiles, and `ExecutionOptions.model_calls` directly.
- Preserve unrelated user settings during atomic `config.yaml` replacement.
- Use `.venv\Scripts\python.exe -m pytest` for Python verification while the packaged `lora-api.exe` is running and locking the environment.

---

### Task 1: Native Config Domain and Loader

**Files:**
- Modify: `src/lora/schema/models.py`
- Modify: `src/lora/schema/__init__.py`
- Modify: `src/lora/config/loader.py`
- Modify: `src/lora/config/editor.py`
- Modify: `src/lora/config/__init__.py`
- Modify: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `ResolvedAgentConfig(alias: str, default_model_group: str, retry: ModelRetryConfig)`.
- Produces: `RunConfig.model_config_mapping: dict[str, Any]`, `RunConfig.model_config: ModelConfig | None`, `RunConfig.model_configuration_status: Literal["configured", "unconfigured", "legacy"]`, and `RunConfig.model_configuration_error: str | None`.
- Produces: `replace_user_model_config(user_lora_root, *, model_config, agents) -> Path`, which validates before atomically preserving unrelated YAML keys.
- Consumes: Pygent `ModelConfig.from_mapping()` as the only model/group parser.

- [ ] **Step 1: Replace route-loader tests with native and unconfigured-state failures**

```python
def test_native_pygent_model_config_is_loaded_without_route_translation(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".lora").mkdir()
    (tmp_path / ".lora" / "config.yaml").write_text(NATIVE_CONFIG, encoding="utf-8")
    config = load_run_config(workspace_root=tmp_path / "workspace")
    assert tuple(config.model_config.models) == ("main", "backup")
    assert tuple(entry.name for entry in config.model_config.model_groups["coding"].models) == ("main", "backup")
    assert config.resolved_agent.default_model_group == "coding"


def test_legacy_routes_boot_as_explicitly_unconfigured(tmp_path, monkeypatch):
    write_user_config(tmp_path, LEGACY_ROUTE_CONFIG)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = load_run_config(workspace_root=tmp_path / "workspace")
    assert config.model_config is None
    assert config.model_configuration_status == "legacy"
    assert "legacy_model_configuration" in config.model_configuration_error
```

- [ ] **Step 2: Run the focused loader tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_config.py -q`

Expected: FAIL because `RunConfig` still exposes routes and the loader still synthesizes a default DeepSeek route.

- [ ] **Step 3: Replace the route dataclasses and parse the native subtree**

```python
@dataclass(slots=True)
class ModelRetryConfig:
    max_attempts_per_model: int = 2
    attempt_idle_timeout_seconds: float = 60.0
    backoff_initial: float = 0.5
    backoff_maximum: float = 4.0
    backoff_multiplier: float = 2.0


@dataclass(slots=True)
class ResolvedAgentConfig:
    alias: str
    default_model_group: str
    retry: ModelRetryConfig = field(default_factory=ModelRetryConfig)


def _parse_model_config(data: dict[str, Any]) -> tuple[dict[str, Any], ModelConfig | None, str, str | None]:
    native = {key: data[key] for key in ("models", "model_groups") if key in data}
    if _contains_legacy_model_fields(data):
        return native, None, "legacy", "legacy_model_configuration: configure native models and model_groups"
    if not native:
        return {}, None, "unconfigured", "model_configuration_required"
    return native, ModelConfig.from_mapping(native), "configured", None
```

`RunConfig.to_dict()` must serialize `model_config_mapping`, omit the derived `model_config` object, and never include resolved credential values. `RunConfig.from_dict()` reparses `model_config_mapping` with `ModelConfig.from_mapping()`.

Update `_validate_config_shape()` to allow top-level `models` and `model_groups`, and restrict `agents[].model_request` to `default_model_group`, `retry`, and `context_window`. Detect `routes`, `fallback`, or `profile` before strict validation so they yield the structured legacy diagnostic instead of aborting Settings startup. Remove `_resolve_model_routes()` and the synthetic DeepSeek default; `_default_config()` contains no model credentials or endpoint.

- [ ] **Step 4: Add atomic replacement tests and implementation**

```python
def test_replace_user_model_config_preserves_non_model_settings(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("runtime:\n  approvals:\n    enabled: false\n", encoding="utf-8")
    replace_user_model_config(tmp_path, model_config=NATIVE_MAPPING, agents=AGENT_DEFAULTS)
    saved = parse_yaml_subset(path.read_text(encoding="utf-8"))
    assert saved["runtime"]["approvals"]["enabled"] is False
    assert saved["models"] == NATIVE_MAPPING["models"]
    assert saved["model_groups"] == NATIVE_MAPPING["model_groups"]
    assert "routes" not in path.read_text(encoding="utf-8")
```

Implement `replace_user_model_config()` by validating `ModelConfig.from_mapping(model_config)` and every `agents[].model_request.default_model_group` before replacing the three top-level model keys through the existing `_write_config()` temporary-file path.

- [ ] **Step 5: Run config tests and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_config.py -q`

Expected: PASS.

```powershell
git add src/lora/schema src/lora/config tests/unit/test_config.py
git commit -m "feat: load native pygent model configuration"
```

### Task 2: Pygent-Native Client, Adapter, Catalog, and Discovery Services

**Files:**
- Create: `src/lora/runtime/model_configuration.py`
- Modify: `src/lora/runtime/__init__.py`
- Create: `tests/unit/test_model_configuration.py`

**Interfaces:**
- Produces: `CredentialEnvironment(user_lora_root: Path, transient: Mapping[str, str] = {})`, a read-only mapping that resolves env-file, process-environment, and keyring values without copying secrets into config objects.
- Produces: `build_model_invoker(config: ModelConfig, *, credential_environ: Mapping[str, str]) -> LoraModelInvoker`.
- Produces: `preferred_models(config: ModelConfig, group_name: str, preferred_model_key: str) -> tuple[ModelEntry, ...]`.
- Produces: `preferred_profile_name(model_key: str) -> str` returning `preferred:<model_key>`.
- Produces: `discover_models(*, protocol: str, connection: ModelConnection, timeout: float = 10.0) -> tuple[ModelInfo, ...]`.
- Produces: `builtin_model_catalogs() -> dict[str, Any]` serialized from Pygent `ProviderCatalog`, `ModelCapabilityCatalog`, and `CapabilityPresetCatalog`.

- [ ] **Step 1: Write protocol factory and ordering tests**

```python
def test_preferred_models_moves_only_selected_child_to_front():
    config = ModelConfig.from_mapping(native_mapping(group=("a", "b", "c")))
    assert tuple(item.name for item in preferred_models(config, "coding", "b")) == ("b", "a", "c")


@pytest.mark.parametrize("protocol", [
    "openai_chat_completions", "openai_responses", "anthropic_messages", "gemini_generate_content",
])
def test_build_model_invoker_uses_each_native_protocol(protocol):
    invoker = build_model_invoker(
        ModelConfig.from_mapping(native_mapping(protocol=protocol)),
        credential_environ={"TEST_KEY": "secret"},
    )
    assert protocol in invoker.adapters
    assert "a" in invoker.clients
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_model_configuration.py -q`

Expected: FAIL because `lora.runtime.model_configuration` does not exist.

- [ ] **Step 3: Implement native factories without URL/provider guessing**

```python
CLIENTS = {
    "openai_chat_completions": OpenAICompatibleClient,
    "openai_responses": OpenAIResponsesClient,
    "anthropic_messages": AnthropicMessagesClient,
    "gemini_generate_content": GeminiGenerateContentClient,
}

ADAPTER_FACTORIES = (
    openai_compatible_adapters,
    openai_responses_adapters,
    anthropic_messages_adapters,
    gemini_generate_content_adapters,
)


def preferred_models(config, group_name, preferred_model_key):
    group = config.model_groups[group_name]
    by_name = {entry.name: entry for entry in group.models}
    if preferred_model_key not in by_name:
        raise ValueError(f"model {preferred_model_key!r} is not in group {group_name!r}")
    return (by_name[preferred_model_key], *(entry for entry in group.models if entry.name != preferred_model_key))
```

Resolve `connection.credential` with Pygent's `CredentialRef.resolve(credential_environ)`, where `CredentialEnvironment.__getitem__()` delegates to Lora's existing `lookup_credential()` so user-file, environment, and keyring sources remain supported. For `proxy`, inject an `httpx.AsyncClient(proxy=connection.proxy, verify=connection.verify_ssl)` and wrap the native client in a small ownership adapter whose `aclose()` closes both the Pygent client and injected HTTP client; otherwise pass `verify_ssl` directly to the native Pygent client. Merge the four native adapter maps and reject unsupported protocols by exact protocol value.

- [ ] **Step 4: Add discovery lifecycle tests and implementation**

```python
@pytest.mark.asyncio
async def test_discovery_uses_native_catalog_and_closes_client(monkeypatch):
    client = SimpleNamespace(models=SimpleNamespace(list=AsyncMock(return_value=(ModelInfo("m1"),))), aclose=AsyncMock())
    monkeypatch.setitem(CLIENTS, "openai_chat_completions", lambda **kwargs: client)
    result = await discover_models(protocol="openai_chat_completions", connection=connection(), timeout=3)
    assert result == (ModelInfo("m1"),)
    client.models.list.assert_awaited_once_with(timeout=3)
    client.aclose.assert_awaited_once()
```

The same test file must cover validation failure, timeout, cancellation, a protocol without `models`, proxy construction, missing credentials, and closure in `finally`.

- [ ] **Step 5: Run tests and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_model_configuration.py tests/unit/test_model_invoker.py -q`

Expected: PASS.

```powershell
git add src/lora/runtime/model_configuration.py src/lora/runtime/__init__.py tests/unit/test_model_configuration.py
git commit -m "feat: build model clients from pygent configuration"
```

### Task 3: Session Model Selection Persistence

**Files:**
- Modify: `src/lora/schema/models.py`
- Modify: `src/lora/sessions/manager.py`
- Modify: `tests/unit/test_session_manager.py`

**Interfaces:**
- Produces: `AgentSession.model_group_name: str` and `AgentSession.selected_model_key: str`.
- Changes: `SessionManager.create(case_id, mode="e2e", *, model_group_name: str | None = None) -> SessionRef`.
- Produces: `SessionManager.set_selected_model(session_id: str, model_key: str) -> AgentSession`.
- Produces: `SessionManager.model_selection(session_id: str) -> tuple[str, str]`.

- [ ] **Step 1: Write creation, persistence, fork, and validation tests**

```python
def test_session_persists_fixed_group_and_preferred_model(native_config):
    manager = SessionManager(native_config)
    ref = manager.create("chat", mode="chat", model_group_name="coding")
    assert manager.model_selection(ref.session_id) == ("coding", "main")
    changed = manager.set_selected_model(ref.session_id, "backup")
    assert (changed.model_group_name, changed.selected_model_key) == ("coding", "backup")
    assert SessionManager(native_config).model_selection(ref.session_id) == ("coding", "backup")


def test_session_rejects_model_outside_fixed_group(native_config):
    manager = SessionManager(native_config)
    ref = manager.create("chat", mode="chat", model_group_name="coding")
    with pytest.raises(ValueError, match="is not in fixed model group"):
        manager.set_selected_model(ref.session_id, "vision-only")
```

Also assert: unknown creation group is rejected; initial child is group entry zero; fork copies group and child; older `session.json` without the new fields resolves the current agent default once and is saved on the next normal `save()`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_session_manager.py -q`

Expected: FAIL because Session has no model selection fields.

- [ ] **Step 3: Implement selection resolution and atomic updates**

```python
def _selection_for_group(self, group_name: str | None) -> tuple[str, str]:
    resolved = self.config.resolved_agent
    native = self.config.model_config
    if resolved is None or native is None:
        raise RuntimeError("model_configuration_required")
    selected_group = group_name or resolved.default_model_group
    group = native.model_groups.get(selected_group)
    if group is None:
        raise ValueError(f"unknown model group {selected_group!r}")
    return selected_group, group.models[0].name
```

Persist both fields in `session.json` and mirrored non-secret values in `metadata.json` so list responses do not need to load full histories. `set_selected_model()` loads, validates membership against `self.config.model_config.model_groups[session.model_group_name]`, saves with `write_json_atomic`, and never accepts a group argument.

- [ ] **Step 4: Run tests and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_session_manager.py tests/unit/test_schema.py -q`

Expected: PASS.

```powershell
git add src/lora/schema/models.py src/lora/sessions/manager.py tests/unit/test_session_manager.py tests/unit/test_schema.py
git commit -m "feat: persist session model selection"
```

### Task 4: Runtime Dynamic Profiles and Admission

**Files:**
- Modify: `src/lora/runtime/agent/core.py`
- Modify: `src/lora/runtime/service.py`
- Modify: `src/lora/orchestration/session_turns.py`
- Modify: `src/lora/orchestration/runtime_keys.py`
- Modify: `tests/unit/test_model_usage_defaults.py`
- Modify: `tests/unit/test_runtime_service.py`
- Modify: `tests/unit/test_runtime_pool.py`

**Interfaces:**
- Consumes: Task 2 `build_model_invoker`, `preferred_models`, and `preferred_profile_name`.
- Consumes: Task 3 `SessionManager.model_selection()`.
- Changes: `LoraAgent.__init__(..., model_group_name: str | None = None)` resolves the explicit Session group or the agent default.
- Changes: `LoraAgent.new_model_layer()` declares `ModelGroup.deferred(name=f"lora:{group_name}")` and `ModelCallPolicy(allow_profile_override=True)` for managed execution; direct background execution uses the same native group as a concrete `ModelGroup`.
- Changes: `RuntimeService.new_agent(*, interactive_approvals: bool, model_group_name: str, config: RunConfig | None = None)` includes the group name in its definition cache key.
- Changes: `RuntimeService.bind(module, agent)` publishes one immutable profile per group child.
- Changes: every Session execution passes `ExecutionOptions(model_calls={requirement_name: {"profile": preferred_profile_name(selected_model_key)}})`.

- [ ] **Step 1: Write managed profile ordering and admission tests**

```python
@pytest.mark.asyncio
async def test_bind_publishes_one_preferred_first_profile_per_child(native_agent):
    await service.bind(native_agent, native_agent)
    calls = handle.ensure_profile.await_args_list
    assert [call.kwargs["profile"] for call in calls] == ["preferred:a", "preferred:b", "preferred:c"]
    assert [entry.name for entry in calls[1].kwargs["models"]] == ["b", "a", "c"]


@pytest.mark.asyncio
async def test_start_turn_admits_persisted_session_preference(native_session):
    await service.start_turn(manager=manager, message="hello", run_ref=run_ref, turn_id="turn-1", interactive_approvals=True)
    options = service._start_agent_execution.await_args.kwargs["execution"]
    assert options.model_calls == {"lora:coding": {"profile": "preferred:backup"}}
```

- [ ] **Step 2: Run runtime tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_model_usage_defaults.py tests/unit/test_runtime_service.py tests/unit/test_runtime_pool.py -q`

Expected: FAIL because binding publishes only one legacy profile and admission has no model-call selection.

- [ ] **Step 3: Replace route-based agent assembly with native config**

```python
def new_model_layer(self) -> ModelCallLayer:
    native_group = self.config.model_config.model_groups[self.model_group_name]
    group = ModelGroup.deferred(name=f"lora:{self.model_group_name}") if self.managed_model else ModelGroup(
        name=f"lora:{self.model_group_name}", models=native_group.models,
    )
    retry = self.resolved_agent.retry
    return ModelCallLayer(
        model_group=group,
        policy=ModelCallPolicy(allow_profile_override=self.managed_model),
        retry_policy=RetryPolicy(
            max_attempts_per_model=retry.max_attempts_per_model,
            retry_on=MODEL_RETRYABLE_ERROR_KINDS,
            attempt_idle_timeout_seconds=retry.attempt_idle_timeout_seconds,
            backoff=ExponentialBackoff(
                initial=retry.backoff_initial,
                maximum=retry.backoff_maximum,
                multiplier=retry.backoff_multiplier,
            ),
        ),
        generation=GenerationConfig(tool_choice="auto"),
        tools=self.tool_definitions,
        invoker=None if self.managed_model else self.llm,
    )
```

Delete `_preferred_model_route`, `_actual_model_route`, `_resolved_routes`, URL-based DeepSeek options, fabricated capability presets, and the forced OpenAI adapter. Trace payloads must take actual native `model_key`, `provider`, `model_id`, and fixed group from admitted Pygent events.

- [ ] **Step 4: Publish profiles and admit the Session snapshot**

At `start_turn()` and `execute_case()`, load the Session selection before creating the Agent or constructing `ExecutionOptions`. Use the fixed group in the deferred requirement name, publish every child profile using `preferred_models()`, and pass only the selected profile in `model_calls`. Hash the native model specs, native connections, group order, and profile order into the existing resolver revision. Update direct/background memory Agent construction to use its configured default group with a concrete native group and the same invoker factory.

- [ ] **Step 5: Verify runtime behavior and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_model_usage_defaults.py tests/unit/test_runtime_service.py tests/unit/test_runtime_pool.py tests/unit/test_model_invoker.py -q`

Expected: PASS.

```powershell
git add src/lora/runtime/agent/core.py src/lora/runtime/service.py src/lora/orchestration tests/unit/test_model_usage_defaults.py tests/unit/test_runtime_service.py tests/unit/test_runtime_pool.py
git commit -m "feat: select session models with pygent profiles"
```

### Task 5: Native Settings, Catalog, and Discovery API

**Files:**
- Modify: `src/lora_api/models/requests.py`
- Modify: `src/lora_api/models/responses.py`
- Modify: `src/lora_api/routers/settings.py`
- Modify: `src/lora_api/services/project_service.py`
- Modify: `tests/unit/test_lora_api_settings.py`
- Modify: `tests/unit/test_secrets.py`

**Interfaces:**
- Produces request: `UpdateSettingsRequest.model_config: dict[str, Any] | None`, `default_model_group: str | None`, and `credential_values: dict[str, str]`.
- Produces request: `DiscoverModelsRequest(protocol: str, connection: dict[str, Any], credential_value: str | None, timeout_seconds: float = 10.0)`.
- Produces response: `RuntimeConfigResponse` fields `model_configuration_status`, `model_configuration_error`, `models`, `model_groups`, `default_model_group`, and native `retry.max_attempts_per_model`.
- Produces endpoints: `GET /settings/model-catalogs` and `POST /settings/models/discover`.

- [ ] **Step 1: Replace settings tests with complete native payload and secrecy assertions**

```python
def test_update_settings_replaces_native_config_and_never_returns_credentials(context):
    request = UpdateSettingsRequest(model_config=NATIVE_MAPPING, default_model_group="coding", credential_values={"MAIN_KEY": "secret"})
    response = asyncio.run(update_settings(request, context=context))
    assert response.model_groups["coding"]["models"] == ["main", "backup"]
    assert response.default_model_group == "coding"
    assert "secret" not in response.model_dump_json()


def test_settings_remain_available_for_legacy_config(legacy_context):
    response = get_settings(context=legacy_context)
    assert response.model_configuration_status == "legacy"
    assert response.models == {}
```

- [ ] **Step 2: Run focused API settings tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_lora_api_settings.py tests/unit/test_secrets.py -q`

Expected: FAIL because request/response models still expose route fields.

- [ ] **Step 3: Implement native settings replacement**

Validate the complete subtree with `ModelConfig.from_mapping()`, ensure the selected agent default exists, save credential values with `set_user_credential()` keyed only by credential reference names present in the submitted model config, call `replace_user_model_config()`, reload, then return only safe connection metadata:

```python
{"base_url": connection.base_url, "credential": {"env": env_name}, "credential_source": credential_source, "verify_ssl": connection.verify_ssl, "proxy": connection.proxy}
```

Map Pygent validation failures to HTTP 422 with `{"code": "invalid_model_configuration", "path": path, "message": message}`. A missing credential is a saved `missing` status, not a settings-validation error.

- [ ] **Step 4: Implement Pygent catalog and discovery endpoints**

`GET /settings/model-catalogs` returns JSON-safe Pygent provider, capability, and preset values. `POST /settings/models/discover` builds `ModelConnection.from_mapping()`, temporarily supplies `credential_value` only to the client factory, calls Task 2 `discover_models()`, and translates auth/timeout/TLS/transport/invalid-response errors into scoped response codes without echoing the credential.

- [ ] **Step 5: Run API tests and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_lora_api_settings.py tests/unit/test_secrets.py tests/unit/test_lora_api_app.py -q`

Expected: PASS.

```powershell
git add src/lora_api src/lora/config tests/unit/test_lora_api_settings.py tests/unit/test_secrets.py tests/unit/test_lora_api_app.py
git commit -m "feat: expose native pygent model settings"
```

### Task 6: Session Creation and Preferred-Model API

**Files:**
- Modify: `src/lora/orchestration/session_execution.py`
- Modify: `src/lora_api/models/requests.py`
- Modify: `src/lora_api/models/responses.py`
- Modify: `src/lora_api/routers/sessions.py`
- Modify: `src/lora_api/services/session_service.py`
- Modify: `tests/unit/test_lora_api_session_groups.py`
- Modify: `tests/unit/test_session_execution_coordinator.py`

**Interfaces:**
- Changes: `CreateSessionRequest.model_group_name: str | None`.
- Produces: `UpdateSessionModelRequest(selected_model_key: str)`.
- Produces: `PATCH /sessions/{session_id}/model?scope_id=...`.
- Produces: `SessionExecutionCoordinator.session_busy(manager, session_id) -> bool`.
- Adds to session responses: `model_group_name`, `selected_model_key`, and `selectable_models: list[ModelSummaryResponse]`.

- [ ] **Step 1: Write API tests for fixed groups and guarded child switching**

```python
def test_create_session_chooses_group_and_returns_children(context):
    created = asyncio.run(create_session(CreateSessionRequest(model_group_name="coding"), context=context))
    detail = get_session(created.session_id, context=context)
    assert detail.session.model_group_name == "coding"
    assert detail.session.selected_model_key == "main"
    assert [item.model_key for item in detail.selectable_models] == ["main", "backup"]


def test_idle_session_can_change_child_but_not_group(context):
    created = create_native_session(context, "coding")
    changed = asyncio.run(update_session_model(created.session_id, UpdateSessionModelRequest(selected_model_key="backup"), context=context))
    assert changed.selected_model_key == "backup"
```

Also cover different sessions using different groups, unknown group, out-of-group child, active execution conflict (HTTP 409), and the absence of any group-changing update field.

- [ ] **Step 2: Run focused Session API tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_lora_api_session_groups.py tests/unit/test_session_execution_coordinator.py -q`

Expected: FAIL because Session responses and the model update endpoint do not exist.

- [ ] **Step 3: Implement coordinator busy-state and Session API**

`session_busy()` checks `_managed_runs` under the coordinator lock for a non-terminal turn with the exact `SessionExecutionKey`. The router rejects changes while busy, delegates membership validation and atomic persistence to `SessionManager.set_selected_model()`, and returns HTTP 422 for invalid membership and HTTP 409 for active execution.

- [ ] **Step 4: Guard settings deletion against persisted Session references**

Before Task 5's config replacement, enumerate all known Session scopes from `build_session_scopes()`. Reject removal of any persisted `model_group_name`, or removal of any persisted `selected_model_key`, with HTTP 409. Permit additions and group reordering; runtime admission reads the new order only for later turns.

- [ ] **Step 5: Run tests and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_lora_api_session_groups.py tests/unit/test_session_execution_coordinator.py tests/unit/test_lora_api_settings.py -q`

Expected: PASS.

```powershell
git add src/lora/orchestration/session_execution.py src/lora_api tests/unit/test_lora_api_session_groups.py tests/unit/test_session_execution_coordinator.py tests/unit/test_lora_api_settings.py
git commit -m "feat: manage fixed session model groups"
```

### Task 7: Desktop Native Model Configuration

**Files:**
- Modify: `apps/desktop/renderer/src/shared/api/client.js`
- Modify: `apps/desktop/renderer/src/shared/api/client.test.mjs`
- Modify: `apps/desktop/renderer/src/app/App.jsx`
- Modify: `apps/desktop/renderer/src/app/App.render.test.mjs`
- Modify: `apps/desktop/renderer/src/app/app.css`

**Interfaces:**
- Changes: `settingsPayload()` submits `model_config`, `default_model_group`, and `credential_values`.
- Produces client methods: `getModelCatalogs()`, `discoverModels(request)`, and `updateSessionModel(sessionId, selectedModelKey, {scopeId})`.
- Produces draft shape: `{ models: Record<string, NativeModelDraft>, modelGroups: Record<string, {models: string[]}>, defaultModelGroup: string, credentialValues: Record<string, string> }`.

- [ ] **Step 1: Replace desktop payload and validation tests**

```javascript
test("settings payload preserves the exact native pygent subtree", () => {
  const payload = settingsPayload(nativeDraft);
  assert.deepEqual(payload.model_config, {
    models: nativeDraft.models,
    model_groups: nativeDraft.modelGroups,
  });
  assert.equal(payload.default_model_group, "coding");
  assert.equal(JSON.stringify(payload).includes("saved-secret"), false);
});

test("native validation requires each group model to exist", () => {
  assert.match(modelConfigValidationError(invalidDraft), /unknown model/i);
});
```

- [ ] **Step 2: Run desktop tests and verify RED**

Run: `npm --prefix apps/desktop test -- --test-name-pattern="settings|model"`

Expected: FAIL because the client and UI still emit routes/fallback.

- [ ] **Step 3: Implement the three-step Settings editor**

Replace the route editor with:

1. connection fields embedded in each model draft (`base_url`, credential env/none, TLS, optional proxy);
2. model fields (`model_key`, `provider`, `model_id`, `protocol`, provider options, full native capabilities), with provider/capability presets populated from `getModelCatalogs()` and optional discovery populated from `discoverModels()`;
3. multiple named groups with ordered model keys and one Agent default group.

Renaming a model key updates group references in the browser draft. Removing a referenced model requires removing it from all draft groups first. Saving remains disabled until native-shaped client validation succeeds; server 422 paths display beside the matching field.

- [ ] **Step 4: Update Settings summary and styles**

Render configured groups and their ordered `model_key · provider / model_id` rows; show `未配置模型` with the backend diagnostic when status is `unconfigured` or `legacy`. Remove all route/fallback labels and helpers (`emptyModelRoute`, `nextRouteId`, `fallbackDisplayRoutes`, `primaryModel`).

- [ ] **Step 5: Run desktop tests and commit**

Run: `npm --prefix apps/desktop test`

Expected: all desktop tests PASS.

```powershell
git add apps/desktop/renderer/src/shared/api apps/desktop/renderer/src/app
git commit -m "feat: configure native pygent models in desktop"
```

### Task 8: Desktop Conversation Group and Child Selectors

**Files:**
- Modify: `apps/desktop/renderer/src/app/App.jsx`
- Modify: `apps/desktop/renderer/src/app/App.render.test.mjs`
- Modify: `apps/desktop/renderer/src/shared/api/client.js`
- Modify: `apps/desktop/renderer/src/shared/api/client.test.mjs`
- Modify: `apps/desktop/renderer/src/app/app.css`

**Interfaces:**
- Consumes: Task 6 Session response fields and `updateSessionModel()`.
- Changes: new-chat flows pass `modelGroupName`; existing-chat composer changes only `selectedModelKey`.
- Displays: fixed group name and current preferred model in conversation chrome.

- [ ] **Step 1: Write UI and API-client behavior tests**

```javascript
test("new conversation sends the chosen model group", async () => {
  await client.createSession({ caseId: "chat", modelGroupName: "coding" });
  assert.equal(JSON.parse(calls[0].init.body).model_group_name, "coding");
});

test("existing idle chat shows only child models from its fixed group", () => {
  const html = renderToStaticMarkup(React.createElement(ChatPane, {
    activeSession: sessionWithModels, running: false, onChangeModel() {}, messages: [], settings, status: "Ready", approvals: [], api,
  }));
  assert.match(html, /coding/);
  assert.match(html, /backup/);
  assert.doesNotMatch(html, /vision-only/);
});
```

Also assert the selector is disabled while running and no existing-chat group selector is rendered.

- [ ] **Step 2: Run focused desktop tests and verify RED**

Run: `npm --prefix apps/desktop test -- --test-name-pattern="model group|preferred model|conversation"`

Expected: FAIL because create and update calls do not carry Session model choices.

- [ ] **Step 3: Implement creation-time group selection**

Keep `pendingNewModelGroup` in `App`, default it from `settings.default_model_group`, and render the group menu only in the empty/new-conversation state. Every sidebar/project/new-task creation path passes that value to `api.createSession()`.

- [ ] **Step 4: Implement idle preferred-child switching**

When loading Session detail, merge `detail.selectable_models` into the UI's active Session view. Render a child menu from that list; call `api.updateSessionModel()`, refresh Session detail, and update chrome on success. Disable it when `running || configuring`. Do not optimistically alter the group or current child before the server confirms the atomic update.

- [ ] **Step 5: Run desktop tests and commit**

Run: `npm --prefix apps/desktop test`

Expected: all desktop tests PASS.

```powershell
git add apps/desktop/renderer/src
git commit -m "feat: select conversation models in desktop"
```

### Task 9: Contracts, Examples, Documentation, and Full Verification

**Files:**
- Modify: `contracts/openapi/lora-api.json`
- Modify: `docs/api/local-service.md`
- Modify: `docs/guides/api-key-management.md`
- Modify: `src/lora/config/README.md`
- Modify: `src/lora/runtime/README.md`
- Modify: `src/lora/sessions/README.md`
- Modify: `lora.yaml.example`
- Modify: `user-config.yaml.example`
- Modify: `.env.example`
- Modify: tests and fixtures still referencing `routes`, `fallback`, `model_name`, or `max_attempts_per_route`

**Interfaces:**
- Documents the final native YAML, safe credential reference flow, discovery limitations, fixed Session group, preferred-child switching, and fallback semantics.
- Removes legacy route names from public contracts and active examples.

- [ ] **Step 1: Locate and replace remaining active legacy references**

Run: `rg -n "model_request\.routes|routes:|fallback:|model_name|max_attempts_per_route" src tests apps contracts lora.yaml.example user-config.yaml.example .env.example docs/api docs/guides src/lora/*/README.md`

Expected: matches identify fixtures, contract fields, and active documentation that must use native `models`, `model_groups`, `model_id`, and `max_attempts_per_model`. Historical design/feedback documents remain unchanged.

- [ ] **Step 2: Update examples and API documentation**

Use the complete native example from the approved spec, including explicit capabilities, native connection credential references, two model groups, and `default_model_group`. Document that only OpenAI-compatible and Anthropic protocols currently expose Pygent native discovery catalogs; other protocols remain manually configurable.

- [ ] **Step 3: Regenerate or update OpenAPI and assert contract endpoints**

Run: `.venv\Scripts\python.exe -c "import json; from lora_api.app import create_app; json.dump(create_app().openapi(), open('contracts/openapi/lora-api.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)"`

Then add/adjust API tests asserting `/settings/model-catalogs`, `/settings/models/discover`, `/sessions/{session_id}/model`, native settings fields, and absence of route response fields.

- [ ] **Step 4: Run static and focused verification**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_config.py tests/unit/test_model_configuration.py tests/unit/test_model_usage_defaults.py tests/unit/test_lora_api_settings.py tests/unit/test_lora_api_session_groups.py -q`

Run: `.venv\Scripts\python.exe -m pyright src/lora/config src/lora/schema src/lora/runtime/model_configuration.py src/lora/runtime/agent/core.py src/lora/runtime/service.py src/lora_api`

Run: `npm --prefix apps/desktop test`

Expected: focused Python tests PASS, Pyright reports 0 errors, and all desktop tests PASS.

- [ ] **Step 5: Run the complete regression suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Run: `git diff --check`

Expected: the complete Python suite passes (including scenario tests and subtests), and diff check reports no whitespace errors. LF-to-CRLF conversion warnings on Windows are informational.

- [ ] **Step 6: Commit final contracts and documentation**

```powershell
git add contracts docs src/lora/config/README.md src/lora/runtime/README.md src/lora/sessions/README.md lora.yaml.example user-config.yaml.example .env.example tests
git commit -m "docs: document native pygent model groups"
```
