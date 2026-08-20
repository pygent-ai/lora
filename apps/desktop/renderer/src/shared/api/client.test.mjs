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
        json: async () => ({ agent: "dev", profile: "production" }),
      };
    },
  });

  const response = await client.updateSettings({
    workspaceRoot: "E:/Projects/lora",
    agent: "dev",
    maxSteps: 7,
    contextWindow: "64000",
    apiKey: "secret-from-ui",
  });

  assert.deepEqual(response, { agent: "dev", profile: "production" });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/settings");
  assert.equal(calls[0].init.method, "PATCH");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    workspace_root: "E:/Projects/lora",
    agent_alias: "dev",
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
    agent: "default",
    maxSteps: -1,
    contextWindow: "",
    apiKey: "",
  });

  assert.deepEqual(JSON.parse(calls[0].init.body), {
    workspace_root: "E:/Projects/lora",
    agent_alias: "default",
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

test("api client delivers runtime approval decisions", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return new Response(JSON.stringify({ delivered: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  await client.deliverApproval("run:call/1", true, "approved in test");

  assert.equal(calls[0].url, "http://127.0.0.1:8765/chat/approvals/run%3Acall%2F1");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    approved: true,
    comment: "approved in test",
  });
});

test("parseSseEvents decodes named events and JSON payloads", () => {
  const events = parseSseEvents(
    [
      "event: execution.event",
      'data: {"execution_id":"exec1","sequence":1,"kind":"lora.chat.started","data":{"session_id":"s1"}}',
      "",
      ": keep-alive",
      "",
      "event: execution.event",
      'data: {"execution_id":"exec1","sequence":2,"kind":"model.text.delta","data":{"text":"hello"}}',
      "",
      "",
    ].join("\n"),
  );

  assert.deepEqual(events, [
    {
      event: "execution.event",
      data: { execution_id: "exec1", sequence: 1, kind: "lora.chat.started", data: { session_id: "s1" } },
    },
    {
      event: "execution.event",
      data: { execution_id: "exec1", sequence: 2, kind: "model.text.delta", data: { text: "hello" } },
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
            "event: execution.event\n",
            'data: {"execution_id":"exec1","sequence":1,"kind":"lora.chat.started","data":{}}\n\n',
            "event: execution.event\n",
            'data: {"execution_id":"exec1","sequence":2,"kind":"model.text.delta","data":{"text":"hello"}}\n\n',
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
          seen.push(event.data.kind);
          if (seen.length === 1) {
            throw new Error("render failed");
          }
        },
      },
    );
  } finally {
    console.error = previousConsoleError;
  }

  assert.deepEqual(seen, ["lora.chat.started", "model.text.delta"]);
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
                    "event: execution.event\n",
                    'data: {"execution_id":"exec1","sequence":1,"kind":"lora.chat.started","data":{"session_id":"s1"}}\n\n',
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
          "event: execution.event\n",
          'data: {"execution_id":"exec1","sequence":2,"kind":"model.text.delta","data":{"text":"hello"}}\n\n',
          "event: execution.event\n",
          'data: {"execution_id":"exec1","sequence":3,"kind":"execution.completed","data":{}}\n\n',
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
        seen.push(event.data.kind);
      },
    },
  );

  assert.deepEqual(seen, ["lora.chat.started", "model.text.delta", "execution.completed"]);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].execution_id, "exec1");
  assert.equal(calls[1].after_sequence, 1);
});
