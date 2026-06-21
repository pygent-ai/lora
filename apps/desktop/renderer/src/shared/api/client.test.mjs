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
    contextWindow: "64000",
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
    context_window: 64000,
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
    contextWindow: "",
    apiKey: "",
  });

  assert.deepEqual(JSON.parse(calls[0].init.body), {
    workspace_root: "E:/Projects/lora",
    config_path: "",
    agent_alias: "default",
    model: "",
    max_steps: -1,
    context_window: null,
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

test("api client fetches tool results by tool call id", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return {
        ok: true,
        status: 200,
        json: async () => ({ tool_call_id: "evt_1", result: "complete" }),
      };
    },
  });

  const response = await client.getToolResult("evt_1");

  assert.equal(calls[0].url, "http://127.0.0.1:8765/tool-results/evt_1");
  assert.deepEqual(response, { tool_call_id: "evt_1", result: "complete" });
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

test("streamChat keeps reading when an event handler throws", async () => {
  const previousConsoleError = console.error;
  const seen = [];
  console.error = () => {};
  try {
    const client = createApiClient({
      baseUrl: "http://127.0.0.1:8765",
      fetchImpl: async () =>
        new Response(
          [
            "event: chat.started\n",
            'data: {"type":"chat.started","payload":{}}\n\n',
            "event: assistant.delta\n",
            'data: {"type":"assistant.delta","payload":{"delta":"hello"}}\n\n',
          ].join(""),
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
    });

    await client.streamChat(
      { message: "hello" },
      {
        onEvent: (event) => {
          seen.push(event.data.type);
          if (seen.length === 1) {
            throw new Error("render failed");
          }
        },
      },
    );
  } finally {
    console.error = previousConsoleError;
  }

  assert.deepEqual(seen, ["chat.started", "assistant.delta"]);
});

test("streamChat resumes the same run after a stream read failure", async () => {
  const calls = [];
  const encoder = new TextEncoder();
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (_url, init) => {
      const body = JSON.parse(init.body);
      calls.push(body);
      if (calls.length === 1) {
        let sent = false;
        return new Response(
          new ReadableStream({
            pull(controller) {
              if (sent) {
                controller.error(new Error("socket lost"));
                return;
              }
              sent = true;
              controller.enqueue(
                encoder.encode(
                  [
                    "event: chat.started\n",
                    'data: {"type":"chat.started","session_id":"s1","case_run_id":"run1","sequence":1,"payload":{}}\n\n',
                  ].join(""),
                ),
              );
            },
          }),
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        );
      }
      return new Response(
        [
          "event: assistant.delta\n",
          'data: {"type":"assistant.delta","session_id":"s1","case_run_id":"run1","sequence":2,"payload":{"delta":"hello"}}\n\n',
          "event: chat.completed\n",
          'data: {"type":"chat.completed","session_id":"s1","case_run_id":"run1","sequence":3,"payload":{"status":"passed"}}\n\n',
        ].join(""),
        {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        },
      );
    },
  });

  const seen = [];
  await client.streamChat(
    { message: "hello", sessionId: "s1" },
    {
      onEvent: (event) => {
        seen.push(event.data.type);
      },
    },
  );

  assert.deepEqual(seen, ["chat.started", "assistant.delta", "chat.completed"]);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].resume_case_run_id, "run1");
  assert.equal(calls[1].resume_from_event, 1);
});
