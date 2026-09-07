import test from "node:test";
import assert from "node:assert/strict";

import { createApiClient, parseSseEvents, settingsPayload } from "./client.js";

test("permission settings preserve false and omit unspecified modes", () => {
  assert.equal(settingsPayload({ approvalsEnabled: false }).approvals_enabled, false);
  assert.equal(settingsPayload({ approvalsEnabled: true }).approvals_enabled, true);
  assert.equal(Object.hasOwn(settingsPayload({ workspaceRoot: "workspace" }), "approvals_enabled"), false);
});

test("session errors preserve HTTP status for missing-session recovery", async () => {
  const client = createApiClient({
    fetchImpl: async () => new Response(JSON.stringify({ detail: "Session is unavailable" }), { status: 404 }),
  });

  await assert.rejects(client.getSession("missing-session"), (error) => {
    assert.equal(error.status, 404);
    assert.match(error.message, /Session is unavailable/);
    return true;
  });
});

test("session network failures remain distinguishable from missing sessions", async () => {
  const failure = new TypeError("Failed to fetch");
  const client = createApiClient({ fetchImpl: async () => { throw failure; } });

  await assert.rejects(client.getSession("session-1"), (error) => {
    assert.equal(error, failure);
    assert.equal(error.status, undefined);
    return true;
  });
});

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

test("api client creates and loads a conversation-scoped chat", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    },
  });

  await client.createSession({ scopeId: "conversation" });
  await client.getSession("chat one", { scopeId: "conversation" });

  assert.equal(JSON.parse(calls[0].init.body).scope_id, "conversation");
  assert.equal(calls[1].url, "http://127.0.0.1:8765/sessions/chat%20one?scope_id=conversation");
});

test("api client removes a project from the sidebar by scope", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return new Response('{"deleted":true}', {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  await client.removeProject("project:C:/Projects/other");

  assert.equal(calls[0].url, "http://127.0.0.1:8765/projects?scope_id=project%3AC%3A%2FProjects%2Fother");
  assert.equal(calls[0].init.method, "DELETE");
});

test("api client lists and opens project files by scope", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (url) => {
      calls.push(url);
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    },
  });

  await client.listWorkspaceEntries("project:C:/Projects/lora", "src/lora");
  await client.readWorkspaceFile("project:C:/Projects/lora", "README.md");

  assert.equal(calls[0], "http://127.0.0.1:8765/workspace/entries?scope_id=project%3AC%3A%2FProjects%2Flora&path=src%2Flora");
  assert.equal(calls[1], "http://127.0.0.1:8765/workspace/file?scope_id=project%3AC%3A%2FProjects%2Flora&path=README.md");
});

test("api client executes and resets a scoped PowerShell session", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    },
  });

  await client.executeTerminalCommand("project:C:/Projects/lora", "Get-Location");
  await client.resetTerminal("project:C:/Projects/lora");

  assert.equal(calls[0].url, "http://127.0.0.1:8765/terminal/execute");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    scope_id: "project:C:/Projects/lora",
    command: "Get-Location",
  });
  assert.equal(calls[1].url, "http://127.0.0.1:8765/terminal/reset");
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

test("api client sends a Pygent model group with ordered fallback routes", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    },
  });

  await client.updateSettings({
    workspaceRoot: "E:/Projects/lora",
    agent: "dev",
    profile: "production",
    modelRoutes: [
      { id: "primary", provider: "openai", model_name: "gpt-main", base_url: "https://main.test/v1", api_key_env: "MAIN_KEY", api_key: "" },
      { id: "backup", provider: "openai", model_name: "gpt-backup", base_url: "https://backup.test/v1", api_key_env: "BACKUP_KEY", api_key: "backup-secret" },
    ],
    fallback: ["primary", "backup"],
    retry: {
      max_attempts_per_route: "3",
      attempt_idle_timeout_seconds: "45",
      backoff_initial: "0",
      backoff_maximum: "3",
      backoff_multiplier: "2",
    },
  });

  assert.deepEqual(JSON.parse(calls[0].init.body).model_group, {
    profile: "production",
    routes: [
      { id: "primary", provider: "openai", model_name: "gpt-main", base_url: "https://main.test/v1", api_key_env: "MAIN_KEY" },
      { id: "backup", provider: "openai", model_name: "gpt-backup", base_url: "https://backup.test/v1", api_key_env: "BACKUP_KEY", api_key: "backup-secret" },
    ],
    fallback: ["primary", "backup"],
    retry: {
      max_attempts_per_route: 3,
      attempt_idle_timeout_seconds: 45,
      backoff_initial: 0,
      backoff_maximum: 3,
      backoff_multiplier: 2,
    },
  });
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
            'event: execution.event\ndata: {"execution_id":"exec1","sequence":3,"kind":"execution.completed","data":{}}\n\n',
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

  assert.deepEqual(seen, ["lora.chat.started", "model.text.delta", "execution.completed"]);
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

test("streamChat reconnects after premature EOF even after a long running task", async () => {
  const originalNow = Date.now;
  let now = 0;
  Date.now = () => now;
  const calls = [];
  const states = [];
  try {
    const client = createApiClient({ fetchImpl: async (_url, init) => {
      calls.push(JSON.parse(init.body));
      if (calls.length === 1) {
        now = 120_000;
        return new Response('event: execution.event\ndata: {"execution_id":"exec-long","sequence":10,"kind":"lora.chat.started","data":{}}\n\n');
      }
      return new Response('event: execution.event\ndata: {"execution_id":"exec-long","sequence":11,"kind":"execution.completed","data":{}}\n\n');
    }});
    await client.streamChat({message: "work"}, {onConnectionState: state => states.push(state)});
    assert.equal(calls.length, 2);
    assert.equal(calls[1].execution_id, "exec-long");
    assert.equal(calls[1].after_sequence, 10);
    assert.equal(calls[1].message, null);
    assert.ok(states.includes("reconnecting"));
  } finally { Date.now = originalNow; }
});

test("streamChat rejects EOF without an execution id or terminal event", async () => {
  const client = createApiClient({fetchImpl: async () => new Response("")});
  await assert.rejects(client.streamChat({message: "work"}), /before the task finished/);
});
