import test from "node:test";
import assert from "node:assert/strict";

import { createApiClient, parseSseEvents } from "./client.js";

test("api client updates settings with backend snake_case fields", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => ({ agent: "dev", model: "updated-model" }),
      };
    },
  });

  const response = await client.updateSettings({
    workspaceRoot: "E:/Projects/lora",
    configPath: "",
    agent: "dev",
    model: "updated-model",
    maxSteps: 7,
    apiKey: "secret-from-ui",
  });

  assert.deepEqual(response, { agent: "dev", model: "updated-model" });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/settings");
  assert.equal(calls[0].init.method, "PATCH");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    workspace_root: "E:/Projects/lora",
    config_path: "",
    agent_alias: "dev",
    model: "updated-model",
    max_steps: 7,
    api_key: "secret-from-ui",
  });
});

test("api client sends blank runtime fields so settings can clear overrides", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => ({}),
      };
    },
  });

  await client.updateSettings({
    workspaceRoot: "E:/Projects/lora",
    configPath: "",
    agent: "default",
    model: "",
    maxSteps: -1,
    apiKey: "",
  });

  assert.deepEqual(JSON.parse(calls[0].init.body), {
    workspace_root: "E:/Projects/lora",
    config_path: "",
    agent_alias: "default",
    model: "",
    max_steps: -1,
  });
});

test("api client lists session groups for directory-scoped sidebar", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => ({
          active_scope_id: "project:E:/Projects/lora",
          groups: [],
        }),
      };
    },
  });

  const response = await client.listSessionGroups();

  assert.equal(calls[0].url, "http://127.0.0.1:8765/sessions/groups");
  assert.deepEqual(response, {
    active_scope_id: "project:E:/Projects/lora",
    groups: [],
  });
});

test("parseSseEvents decodes named events and JSON payloads", () => {
  const events = parseSseEvents(
    [
      "event: chat.started",
      'data: {"type":"chat.started","payload":{"session_id":"s1"}}',
      "",
      ": keep-alive",
      "",
      "event: assistant.delta",
      'data: {"type":"assistant.delta","payload":{"delta":"hello"}}',
      "",
      "",
    ].join("\n"),
  );

  assert.deepEqual(events, [
    {
      event: "chat.started",
      data: { type: "chat.started", payload: { session_id: "s1" } },
    },
    {
      event: "assistant.delta",
      data: { type: "assistant.delta", payload: { delta: "hello" } },
    },
  ]);
});
