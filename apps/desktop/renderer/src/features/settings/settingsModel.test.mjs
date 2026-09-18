import test from "node:test";
import assert from "node:assert/strict";
import { addModelToGroup, applyBuiltinProvider, emptyNativeConnection, modelDiscoveryRequest, settingsToDraft, modelGroupValidationError } from "./settingsModel.js";
import { settingsPayload } from "../../shared/api/client.js";

test("quick setup preserves native connection-model-group structure", () => {
  const draft = settingsToDraft({ connections: { service: emptyNativeConnection() } });
  const next = addModelToGroup(draft, { connectionKey: "service", modelId: " example-model ", groupName: "" });
  assert.equal(modelGroupValidationError(next), "");
  assert.equal(next.connections, draft.connections);
  assert.equal(next.models["model-1"].connection, "service");
  assert.equal(next.models["model-1"].model_id, "example-model");
  assert.deepEqual(next.modelGroups.default.models, ["model-1"]);
  assert.equal(next.defaultModelGroup, "default");
  assert.deepEqual(draft.models, {});
  const saved = settingsPayload(next).model_config;
  assert.equal(saved.models["model-1"].connection, "service");
  assert.equal(Object.hasOwn(saved.models["model-1"], "credential"), false);
});

test("quick setup appends a fallback without changing existing priorities or credentials", () => {
  const draft = settingsToDraft({ connections: { service: emptyNativeConnection() }, models: { "model-1": { model_id: "existing" } }, model_groups: { coding: { models: ["model-1"] } }, default_model_group: "coding" });
  draft.credentialValues = { OPENAI_API_KEY: "test-only-value" };
  const next = addModelToGroup(draft, { connectionKey: "service", modelId: "backup", groupName: "coding" });
  assert.deepEqual(next.modelGroups.coding.models, ["model-1", "model-2"]);
  assert.equal(next.models["model-1"], draft.models["model-1"]);
  assert.equal(next.credentialValues, draft.credentialValues);
  assert.equal(next.defaultModelGroup, "coding");
});

test("quick setup avoids group collisions and uses catalog capabilities", () => {
  const draft = settingsToDraft({ connections: { service: emptyNativeConnection() }, model_groups: { default: { models: ["existing"] } }, default_model_group: "default" });
  const capabilities = { limits: { context_tokens: 42 } };
  const next = addModelToGroup(draft, { connectionKey: "service", modelId: "known", groupName: "" }, { model_capabilities: [{ provider: "openai", protocol: "openai_responses", model_id: "known", capabilities }] });
  assert.deepEqual(next.modelGroups["default-2"].models, ["model-1"]);
  assert.equal(next.defaultModelGroup, "default");
  assert.deepEqual(next.models["model-1"].capabilities, capabilities);
  assert.notEqual(next.models["model-1"].capabilities, capabilities);
});

test("quick setup rejects missing references and blank model IDs", () => {
  const draft = settingsToDraft({ connections: { service: emptyNativeConnection() } });
  for (const options of [{ connectionKey: "missing", modelId: "a" }, { connectionKey: "service", modelId: " " }, { connectionKey: "service", modelId: "a", groupName: "deleted" }]) {
    assert.throws(() => addModelToGroup(draft, options));
  }
});

test("built-in provider applies Pygent endpoints and carries the entered key", () => {
  const draft = settingsToDraft({
    connections: { custom: { provider: "custom", credential: { env: "CUSTOM_KEY" }, protocols: { openai_chat_completions: { base_url: "https://custom.test/v1" } } } },
    models: { main: { connection: "custom", protocol: "openai_chat_completions", model_id: "model" } },
  });
  draft.credentialValues.CUSTOM_KEY = "temporary-secret";
  const catalog = {
    default_protocol: "anthropic_messages",
    protocols: { anthropic_messages: { base_url: "https://api.anthropic.com", api_key_env: "ANTHROPIC_API_KEY" } },
  };
  const next = applyBuiltinProvider(draft, "custom", "anthropic", catalog);
  assert.deepEqual(next.connections.custom.protocols, { anthropic_messages: { base_url: "https://api.anthropic.com" } });
  assert.equal(next.connections.custom.provider, "anthropic");
  assert.deepEqual(next.connections.custom.credential, { env: "ANTHROPIC_API_KEY" });
  assert.equal(next.credentialValues.ANTHROPIC_API_KEY, "temporary-secret");
  assert.equal(next.models.main.protocol, "anthropic_messages");
  assert.equal(draft.models.main.protocol, "openai_chat_completions");
});

test("switching connection protocol keeps addresses, credentials and model references", async () => {
  const { changeConnectionProtocol } = await import("./settingsModel.js");
  const draft = settingsToDraft({ connections: { service: emptyNativeConnection() }, models: { main: { connection: "service", protocol: "openai_responses", model_id: "example" }, other: { connection: "other", protocol: "openai_responses" } } });
  const next = changeConnectionProtocol(draft, "service", "openai_responses", "anthropic_messages");
  assert.equal(next.connections.service.protocols.anthropic_messages.base_url, draft.connections.service.protocols.openai_responses.base_url);
  assert.equal(next.connections.service.credential, draft.connections.service.credential);
  assert.equal(next.models.main.protocol, "anthropic_messages");
  assert.equal(next.models.other, draft.models.other);
  assert.equal(next.modelGroups, draft.modelGroups);
  assert.equal(changeConnectionProtocol(draft, "service", "openai_responses", "openai_chat_completions"), draft);
  assert.equal(draft.models.main.protocol, "openai_responses");
});

test("model discovery uses the selected connection and unsaved credential", () => {
  const draft = settingsToDraft({ connections: { service: { ...emptyNativeConnection(), credential_source: "missing" } } });
  draft.credentialValues.OPENAI_API_KEY = "temporary-secret";
  const request = modelDiscoveryRequest(draft, "service");
  assert.equal(request.protocol, "openai_responses");
  assert.equal(request.credential_value, "temporary-secret");
  assert.equal(request.connection.credential.env, "OPENAI_API_KEY");
  assert.equal(Object.hasOwn(request.connection, "credential_source"), false);
  assert.equal(modelDiscoveryRequest(draft, "missing"), null);
});
