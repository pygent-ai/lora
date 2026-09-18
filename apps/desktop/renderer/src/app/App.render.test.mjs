import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { after, before } from "node:test";
import { fileURLToPath } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { activityHeaderText } from "./runTiming.js";
import { settingsPayload } from "../shared/api/client.js";

const desktopRoot = fileURLToPath(new URL("../../../", import.meta.url));
let appModule;
let vite;

test("completion, replay, and history replacement share the durable run duration", () => {
  const timing = {
    case_run_id: "run-1", started_at: "2026-09-04T10:00:00Z",
    finished_at: "2026-09-04T10:00:17Z", status: "passed",
  };
  const event = { kind: "execution.completed", data: { run_timing: timing } };
  const live = appModule.projectLiveAssistantEvent({
    role: "assistant", content: "Done", status: "running", startedAt: 1, sections: [],
  }, event, 9999999999999);
  const [, restored] = appModule.historyToMessages([
    { role: "user", content: "Do this", run_timing: timing },
    { role: "assistant", content: "Done", run_timing: timing },
  ]);
  for (const message of [live, restored, appModule.projectLiveAssistantEvent(live, event, 1)]) {
    assert.equal(activityHeaderText(message, 0), "Processed for 17s");
    assert.equal(message.caseRunId, "run-1");
  }
});

before(async () => {
  vite = await createServer({
    appType: "custom",
    logLevel: "silent",
    root: desktopRoot,
    server: { middlewareMode: true },
  });
  appModule = await vite.ssrLoadModule("/renderer/src/app/App.jsx");
});

after(async () => {
  await vite?.close();
});

test("running chat allows editing and offers steering", () => {
  const html = renderToStaticMarkup(React.createElement(appModule.ChatPane, {
    activeSession: { session_id: "session-1", scope_id: "conversation" },
    messages: [{ id: "user-1", role: "user", content: "hello" }],
    settings: {}, status: "Running", running: true, steeringReady: true, approvals: [],
  }));
  const textarea = html.match(/<textarea[^>]*>/)[0];
  assert.doesNotMatch(textarea, /disabled/);
  assert.match(html, /aria-label="追加指令"/);
  assert.match(html, /下一处理边界生效/);
});

test("reconnecting sessions allow drafting but cannot steer until connected", () => {
  const html = renderToStaticMarkup(React.createElement(appModule.ChatPane, {
    activeSession: { session_id: "old-session", scope_id: "conversation" },
    messages: [{ id: "user-1", role: "user", content: "old task" }],
    settings: {}, status: "Reconnecting", running: true, steeringReady: false, approvals: [],
  }));
  assert.doesNotMatch(html.match(/<textarea[^>]*>/)[0], /disabled/);
  assert.match(html, /aria-label="正在连接"[^>]*disabled/);
  assert.doesNotMatch(html, /aria-label="追加指令"/);
});

test("steering projection deduplicates receipts and restored history", () => {
  const original = [{ id: "assistant-1", role: "assistant", content: "working" }];
  const updated = appModule.insertSteeringMessage(original, "assistant-1", "input-1", "focus on tests");
  assert.equal(updated[0].content, "focus on tests");
  assert.equal(updated[1], original[0]);
  assert.equal(appModule.insertSteeringMessage(updated, "assistant-1", "input-1", "focus on tests"), updated);
  const restored = appModule.historyToMessages([
    { role: "user", content: "focus on tests", kind: "lora.user.steering", data: { input_id: "input-1" } },
  ]);
  assert.equal(appModule.insertSteeringMessage(restored, "assistant-1", "input-1", "focus on tests"), restored);
});

test("settings displays the saved tool permission mode", () => {
  for (const enabled of [true, false]) {
    const html = renderToStaticMarkup(React.createElement(appModule.SettingsPanel, {
      settings: { approvals_enabled: enabled }, disabled: false,
      onClose() {}, onSave() {},
    }));
    assert.match(html, /工具权限/);
    assert.match(html, new RegExp(`value="${enabled ? "approval" : "full-access"}" selected=""`));
    assert.match(html, /Save and Reload/);
  }
});

test("desktop app renders its initial workbench", async () => {
  const html = renderToStaticMarkup(React.createElement(appModule.App));

  assert.match(html, /class="app-shell/);
  assert.match(html, /aria-label="Workbench"/);
  assert.match(html, /aria-label="Open project"/);
});

test("trace event updates scroll the inspector to the latest item", () => {
  const traceList = { scrollTop: 24, scrollHeight: 960 };

  appModule.scrollTraceToLatest(traceList);

  assert.equal(traceList.scrollTop, 960);
  assert.doesNotThrow(() => appModule.scrollTraceToLatest(null));
});

test("trace events are collapsed by default and expanded rows show untruncated content", () => {
  const content = `<user-context>\n  <user-message>${"x".repeat(220)}FULL_END</user-message>\n</user-context>`;
  const event = {
    id: "event-1",
    type: "conversation.user_message",
    payload: { content, identity: "default" },
  };

  const collapsed = renderToStaticMarkup(
    React.createElement(appModule.TraceEventRow, { event, tab: "Events" }),
  );
  const expanded = renderToStaticMarkup(
    React.createElement(appModule.TraceEventRow, { event, tab: "Events", expanded: true }),
  );

  assert.match(collapsed, /aria-expanded="false"/);
  assert.doesNotMatch(collapsed, /FULL_END/);
  assert.match(expanded, /aria-expanded="true"/);
  assert.match(expanded, /FULL_END/);
  assert.doesNotMatch(expanded, /Payload/);
  assert.doesNotMatch(expanded, /identity/);
  assert.equal(expanded.match(/FULL_END/g)?.length, 1);
});

test("trace events without content expand to their complete payload", () => {
  const event = {
    id: "event-2",
    type: "model.request",
    payload: { model: "primary", messages: [{ role: "user", content: "hello" }] },
  };
  const expanded = renderToStaticMarkup(
    React.createElement(appModule.TraceEventRow, { event, tab: "Events", expanded: true }),
  );

  assert.match(expanded, /&quot;model&quot;: &quot;primary&quot;/);
  assert.match(expanded, /&quot;messages&quot;/);
});

test("trace panel starts with an overview and retains raw-event navigation", () => {
  const html = renderToStaticMarkup(
    React.createElement(appModule.TracePanel, {
      activeSession: { session_id: "session-1", last_case_run_id: "run-1" },
      collapsed: false,
      contextSnapshots: [],
      events: [{ id: "event-1", type: "model.request", payload: { content: "request" } }],
      settings: {},
      onToggle() {},
    }),
  );

  assert.match(html, /当前任务/);
  assert.match(html, /原始事件/);
  assert.match(html, /工具调用/);
  assert.match(html, /aria-label="Trace tabs"/);
  assert.doesNotMatch(html, /aria-label="Expand model.request"/);
});

test("trace event prefix filters are derived from the first type segment", () => {
  const events = [
    { type: "conversation.user_message" },
    { type: "model.request" },
    { type: "model.response" },
    { type: "context.checkpoint" },
    { type: "execution" },
  ];

  assert.deepEqual(appModule.eventPrefixGroups(events), [
    { prefix: "context", count: 1 },
    { prefix: "conversation", count: 1 },
    { prefix: "execution", count: 1 },
    { prefix: "model", count: 2 },
  ]);
  assert.deepEqual(
    appModule.filterTraceEventsByPrefix(events, "model").map((event) => event.type),
    ["model.request", "model.response"],
  );
  assert.equal(appModule.filterTraceEventsByPrefix(events, "all"), events);
});

test("trace expansion set toggles one event without mutating the current set", () => {
  const current = new Set(["event-1"]);
  const removed = appModule.toggleSetValue(current, "event-1");
  const added = appModule.toggleSetValue(current, "event-2");

  assert.deepEqual([...current], ["event-1"]);
  assert.deepEqual([...removed], []);
  assert.deepEqual([...added], ["event-1", "event-2"]);
});

test("conversation updates scroll the transcript to the latest message", () => {
  const transcript = { scrollTop: 120, scrollHeight: 1_280 };

  appModule.scrollTranscriptToLatest(transcript);

  assert.equal(transcript.scrollTop, 1_280);
  assert.doesNotThrow(() => appModule.scrollTranscriptToLatest(null));
});

test("trace tools retain the call name when a result omits it", () => {
  const events = [
    { id: "call-1", type: "tool.call", payload: { tool_name: "read", args: { path: "src/app.js" } } },
    { id: "result-1", type: "tool.result", payload: { tool_call_id: "call-1", status: "success", result: "ok" } },
  ];

  const [tool] = appModule.traceToolEvents(events);

  assert.equal(tool.payload.tool_name, "read");
  assert.equal(appModule.eventTitle(tool), "Read");
  assert.match(appModule.eventSummary(tool), /src\/app\.js/);
});

test("trace files prioritize the action and path", () => {
  const event = { type: "file.read", payload: { path: "E:\\Projects\\lora\\src\\app.js", before_hash: null } };

  assert.equal(appModule.eventTitle(event), "Read file");
  assert.equal(appModule.eventSummary(event), "E:\\Projects\\lora\\src\\app.js");
});

test("conversation tool messages show each actual result without the duplicated payload", () => {
  const event = {
    type: "conversation.tool_message",
    payload: {
      content: "",
      role: "tool",
      results: [
        {
          call_id: "call-1",
          name: "bash",
          status: "succeeded",
          output: JSON.stringify({
            status: "success",
            result: JSON.stringify({ status: "success", result: "actual stdout", error: null }),
            error: null,
            tool_call_id: "call-1",
          }),
        },
      ],
    },
  };

  assert.equal(appModule.eventSummary(event), "bash  success");
  assert.equal(appModule.traceEventDetails(event, "Events"), "bash  ·  success  ·  call-1\nactual stdout");
});

test("trace config formats model routes without object coercion", () => {
  assert.equal(
    appModule.formatConfigValue("routes", [
      { id: "primary", provider: "openai", model_name: "deepseek-v4-flash" },
      { id: "backup", provider: "openai", model_name: "gpt-5" },
    ]),
    "primary  openai / deepseek-v4-flash\nbackup  openai / gpt-5",
  );
  assert.equal(appModule.formatConfigValue("max_steps", 0), "0");
  assert.equal(appModule.formatConfigValue("fallback", ["primary", "backup"]), "primary → backup");
});

test("context snapshots group model steps by run and compression version", () => {
  const snapshots = [
    { snapshot_id: "a", case_run_id: "run-1", compression_version: 0 },
    { snapshot_id: "b", case_run_id: "run-1", compression_version: 0 },
    { snapshot_id: "c", case_run_id: "run-1", compression_version: 1 },
    { snapshot_id: "d", case_run_id: "run-2", compression_version: 0 },
  ];

  const runs = appModule.contextSnapshotRuns(snapshots);

  assert.equal(runs.length, 2);
  assert.deepEqual(runs[0].versions.map((item) => item.version), [0, 1]);
  assert.deepEqual(runs[0].versions[0].snapshots.map((item) => item.snapshot_id), ["a", "b"]);
  assert.equal(runs[1].versions[0].snapshots[0].snapshot_id, "d");
});

test("context snapshot phase distinguishes requests and responses", () => {
  assert.equal(appModule.contextSnapshotPhase({ phase: "request" }), "Request");
  assert.equal(appModule.contextSnapshotPhase({ phase: "response" }), "Response");
  assert.equal(appModule.contextSnapshotPhase({}), "Request");
});

test("context snapshot merge removes replayed live events", () => {
  assert.deepEqual(
    appModule.mergeContextSnapshots(
      [{ snapshot_id: "a" }],
      [{ snapshot_id: "a" }, { snapshot_id: "b" }],
    ).map((item) => item.snapshot_id),
    ["a", "b"],
  );
});

test("context inspector renders original and compressed versions as variable rows", () => {
  const html = renderToStaticMarkup(
    React.createElement(appModule.ContextInspector, {
      snapshots: [
        {
          snapshot_id: "original",
          case_run_id: "run-1",
          compression_version: 0,
          projection_revision: 3,
          system_prompt: "You are Lora.",
          message_count: 1,
          tool_count: 0,
          messages: [{ role: "user", content: "Inspect the project." }],
        },
        {
          snapshot_id: "compressed",
          case_run_id: "run-1",
          compression_version: 1,
          phase: "response",
          projection_revision: 4,
          system_prompt: "You are Lora.",
          message_count: 2,
          tool_count: 1,
          messages: [
            { role: "assistant", content: "Compressed summary." },
            { role: "tool", content: "read result" },
          ],
        },
      ],
    }),
  );

  assert.match(html, />v0</);
  assert.match(html, />Original</);
  assert.match(html, />v1</);
  assert.match(html, />Compressed</);
  assert.match(html, /Step 1\/1 · Response/);
  assert.match(html, /context-variable-role">system</);
  assert.match(html, /context-variable-role">assistant</);
  assert.match(html, /context-variable-role">tool</);
});

test("native model validation requires groups to reference configured children", () => {
  const model = {
    connection: "shared", model_id: "main", protocol: "openai_chat_completions",
    capabilities: { limits: { context_tokens: 1000, max_output_tokens: 100 } },
  };
  const valid = {
    connections: { shared: { provider: "openai", credential: { env: "MAIN_KEY" }, protocols: { openai_chat_completions: { base_url: "https://main.test" } } } },
    models: { primary: model, backup: { ...model, model_id: "backup" } },
    modelGroups: { coding: { models: ["primary", "backup"] } },
    defaultModelGroup: "coding",
  };

  assert.equal(appModule.modelGroupValidationError(valid), "");
  assert.match(appModule.modelGroupValidationError({ ...valid, modelGroups: { coding: { models: ["missing"] } } }), /不存在的模型 missing/);
  assert.match(appModule.modelGroupValidationError({ ...valid, defaultModelGroup: "missing" }), /默认模型组/);
});

test("new chat stays enabled while another session is running", () => {
  const html = renderToStaticMarkup(
    React.createElement(appModule.SessionSidebar, {
      collapsed: false,
      settings: { workspace_root: "", agent: "default", routes: [] },
      projects: [],
      sessionGroups: [],
      activeScopeId: "",
      activeSessionId: "running-session",
      running: true,
      onCreateSession() {},
      onDeleteSession() {},
      onSelectSession() {},
      onOpenSettings() {},
      onToggle() {},
    }),
  );

  assert.match(html, /class="icon-button header-action"[^>]*title="New chat"/);
  assert.doesNotMatch(html, /title="New chat"[^>]*disabled/);
});

test("live tool calls appear before results and update the same inspector row", () => {
  const call = { type: "model.tool_call.completed", payload: { call_id: "live-1", name: "bash", arguments: { command: "long-command" } } };
  const started = { type: "tool.started", payload: { call_id: "live-1" } };
  const [pending] = appModule.traceToolEvents([call, started]);
  assert.equal(pending.payload.status, "running");
  assert.equal(pending.payload.has_result, false);
  assert.equal(pending.payload.tool_name, "bash");
  assert.match(pending.payload.arguments, /long-command/);
  const result = { type: "lora.runtime.message", payload: { role: "tool", tool_call_id: "live-1", content: "command output" } };
  const done = { type: "tool.completed", payload: { call_id: "live-1" } };
  const tools = appModule.traceToolEvents([call, started, result, done]);
  assert.equal(tools.length, 1);
  assert.equal(tools[0].id, pending.id);
  assert.equal(tools[0].payload.status, "success");
  assert.match(tools[0].payload.result, /command output/);
  const replayed = appModule.traceToolEvents([call, result, done, call, started]);
  assert.equal(replayed.length, 1);
  assert.equal(replayed[0].payload.status, "success");
});

test("detached trace results retain running state, task ID and partial output until a final result", () => {
  const events = [
    { id: "call-bg", type: "tool.call", payload: { tool_call_id: "bg", tool_name: "bash" } },
    { id: "result-bg", type: "tool.result", payload: {
      tool_call_id: "bg", status: "running", framework_status: "detached",
      task: { task_id: "task-bg" }, result: "partial stdout", next_action: "query task",
    } },
  ];
  const [running] = appModule.traceToolEvents(events);
  assert.equal(running.payload.status, "running");
  assert.match(running.payload.result, /task-bg/);
  assert.match(running.payload.result, /partial stdout/);
  for (const status of ["success", "error"]) {
    const tools = appModule.traceToolEvents([...events, {
      id: "final-bg", type: "tool.result", payload: { tool_call_id: "bg", status, result: "final output" },
    }]);
    assert.equal(tools.length, 1);
    assert.equal(tools[0].payload.status, status);
    assert.equal(tools[0].payload.result, "final output");
  }
});

test("background chat results remain running through turn completion and history replay", () => {
  const payload = {
    tool_call_id: "bg", status: "running", framework_status: "detached",
    task: { task_id: "task-bg" }, result: "partial stdout", next_action: "query task",
  };
  const toolMessage = { role: "tool", tool_call_id: "bg", content: JSON.stringify(payload) };
  const initial = { role: "assistant", content: "", status: "running", sections: [] };
  const live = appModule.projectLiveAssistantEvent(initial, { kind: "tool.result", data: payload });
  const runtime = appModule.projectLiveAssistantEvent(initial, { kind: "lora.runtime.message", data: toolMessage });
  const [, replay] = appModule.historyToMessages([
    { role: "user", content: "Start background work" },
    { role: "assistant", content: "", tool_calls: [{ id: "bg", function: { name: "bash", arguments: "{}" } }] },
    toolMessage,
    { role: "assistant", content: "Task started" },
  ]);
  for (const message of [live, runtime, replay]) {
    const completed = appModule.projectLiveAssistantEvent(message, { kind: "execution.completed", data: {} });
    assert.equal(completed.sections[0].status, "running");
    const call = completed.sections[0].calls[0];
    assert.equal(call.status, "running");
    assert.match(call.result, /task-bg/);
    assert.match(call.result, /partial stdout/);
    for (const status of ["success", "error"]) {
      const final = appModule.projectLiveAssistantEvent(completed, {
        kind: "tool.result", data: { tool_call_id: "bg", status, result: "final output" },
      });
      assert.equal(final.sections[0].calls.length, 1);
      assert.equal(final.sections[0].calls[0].status, status);
      assert.equal(final.sections[0].calls[0].result, "final output");
      assert.equal(final.sections[0].status, "done");
    }
  }
  const [trace] = appModule.traceToolEvents([{ type: "lora.runtime.message", payload: toolMessage }]);
  assert.equal(trace.payload.status, "running");
});

test("live tools report failure and cancellation while parallel calls remain running", () => {
  for (const type of ["tool.failed", "tool.cancelled"]) {
    const tools = appModule.traceToolEvents([
      { type: "tool.started", payload: { call_id: "a", name: "read" } },
      { type: "model.tool_call.completed", payload: { call_id: "b", name: "bash", arguments: {} } },
      { type, payload: { call_id: "a", error_kind: "Stopped" } },
    ]);
    assert.equal(tools.length, 2);
    assert.equal(tools[0].payload.status, "error");
    assert.equal(tools[0].payload.result, "Stopped");
    assert.equal(tools[1].payload.status, "running");
  }
});

test("legacy model tool IDs pair calls with their results", () => {
  const tools = appModule.traceToolEvents([
    { id: "evt-call", type: "tool.call", payload: { model_tool_call_id: "model-1", tool_name: "read", args: { path: "README.md" } } },
    { id: "evt-result", type: "tool.result", payload: { model_tool_call_id: "model-1", tool_call_id: "legacy-event-id", result: "actual file content" } },
  ]);
  assert.equal(tools.length, 1);
  assert.equal(tools[0].payload.status, "success");
  assert.equal(tools[0].payload.result, "actual file content");
  assert.match(appModule.traceEventDetails(tools[0], "Tools"), /actual file content/);
});

test("project groups create chats directly and omit session counts", () => {
  const html = renderToStaticMarkup(
    React.createElement(appModule.SessionSidebar, {
      collapsed: false,
      settings: { workspace_root: "C:/Projects/lora", agent: "default", routes: [] },
      projects: [{ workspace_root: "C:/Projects/lora" }, { workspace_root: "C:/Projects/other" }],
      sessionGroups: [
        { scope: { scope_id: "project:C:/Projects/lora", label: "lora", workspace_root: "C:/Projects/lora" },
          sessions: [{ session_id: "chat-1", title: "First chat" }] },
        { scope: { scope_id: "project:C:/Projects/other", label: "other", workspace_root: "C:/Projects/other" }, sessions: [] },
        { scope: { scope_id: "conversation", label: "Chat", workspace_root: null }, sessions: [] },
      ],
      activeScopeId: "project:C:/Projects/lora",
      activeSessionId: "chat-1",
      onCreateSession() {}, onDeleteSession() {}, onSelectSession() {},
      onChooseProject() {}, onOpenSettings() {}, onToggle() {},
    }),
  );

  assert.match(html, /title="Open project"/);
  assert.match(html, /aria-label="New chat in lora"/);
  assert.match(html, /aria-label="New chat in Chat"/);
  assert.match(html, /aria-label="Remove project lora"/);
  assert.match(html, /aria-label="Remove project other"/);
  assert.doesNotMatch(html, /aria-label="Remove project Chat"/);
  assert.doesNotMatch(html, /group-count/);
  assert.doesNotMatch(html, />Choose Project</);
});

test("switching workspace clears an unchanged agent override", () => {
  const draft = {
    workspaceRoot: "E:/Projects/other",
    agent: "dev",
  };

  assert.deepEqual(
    appModule.settingsForSave(draft, { workspace_root: "E:/Projects/lora", agent: "dev" }),
    { workspaceRoot: "E:/Projects/other", agent: "" },
  );
  assert.equal(
    appModule.settingsForSave({ ...draft, agent: "other" }, { workspace_root: "E:/Projects/lora", agent: "dev" }).agent,
    "other",
  );
});

test("composer sends on Enter and keeps Shift+Enter for a newline", () => {
  assert.equal(appModule.shouldSubmitComposer({ key: "Enter", shiftKey: false, nativeEvent: {} }), true);
  assert.equal(appModule.shouldSubmitComposer({ key: "Enter", shiftKey: true, nativeEvent: {} }), false);
  assert.equal(appModule.shouldSubmitComposer({ key: "Enter", shiftKey: false, nativeEvent: { isComposing: true } }), false);
  assert.equal(appModule.shouldSubmitComposer({ key: "a", shiftKey: false, nativeEvent: {} }), false);
});

test("initial workbench load retries transient fetch failures", async () => {
  let calls = 0;
  const result = await appModule.initializeWorkbench(
    async () => {
      calls += 1;
      if (calls < 3) {
        throw new TypeError("Failed to fetch");
      }
      return "ready";
    },
    { attempts: 3, retryDelay: async () => {} },
  );

  assert.equal(result, "ready");
  assert.equal(calls, 3);
});

test("initial workbench load does not hide configuration errors", async () => {
  let calls = 0;
  await assert.rejects(
    appModule.initializeWorkbench(
      async () => {
        calls += 1;
        throw new Error("Agent alias is not configured");
      },
      { attempts: 3, retryDelay: async () => {} },
    ),
    /Agent alias is not configured/,
  );
  assert.equal(calls, 1);
});

test("session history survives switching away while another session is running", () => {
  const persisted = [{ id: "persisted", role: "assistant", content: "Saved answer" }];
  const live = [
    { id: "user-live", role: "user", content: "Keep working" },
    { id: "assistant-live", role: "assistant", content: "Still working", status: "running" },
  ];

  assert.equal(appModule.selectSessionMessages(live, persisted), live);
  assert.equal(appModule.selectSessionMessages([], persisted), persisted);
  assert.equal(appModule.selectSessionMessages(undefined, persisted), persisted);
});

test("session live events survive switching away while another session is running", () => {
  const cache = new Map();
  const first = { id: "execution-1", type: "model.started" };
  const second = { id: "execution-2", type: "tool.started" };

  let live = appModule.appendSessionLiveTraceEvent(cache, "session-1", [], first);
  live = appModule.appendSessionLiveTraceEvent(cache, "session-1", live, second);
  live = appModule.appendSessionLiveTraceEvent(cache, "session-1", live, second);

  assert.deepEqual(cache.get("session-1"), [first, second]);
  assert.equal(cache.has("session-2"), false);
});

test("initial workbench selects the first session from the active project only", () => {
  const groups = [
    {
      scope: { scope_id: "project:E:/Projects/pygent" },
      sessions: [{ session_id: "pygent-session" }],
    },
    {
      scope: { scope_id: "project:E:/Projects/lora" },
      sessions: [{ session_id: "lora-session" }],
    },
  ];

  assert.equal(
    appModule.firstSessionIdInScope(groups, "project:E:/Projects/lora"),
    "lora-session",
  );
  assert.equal(appModule.firstSessionIdInScope(groups, "project:E:/Projects/missing"), "");
});

test("history, chat, and trace share one non-overlay grid", async () => {
  const css = await readFile(new URL("./app.css", import.meta.url), "utf8");

  assert.match(
    css,
    /\.app-shell\s*{[^}]*grid-template-columns:\s*var\(--history-width\) minmax\(0, 1fr\) var\(--trace-width\)/,
  );
  assert.doesNotMatch(
    css,
    /\.app-shell:not\(\.history-collapsed\) \.history\s*{[^}]*position:\s*fixed/,
  );
  assert.doesNotMatch(css, /\.trace\s*{[^}]*position:\s*fixed/);
  assert.doesNotMatch(css, /\.workbench\s*{/);
});

test("layout mode is represented by the same state that drives panel toggles", () => {
  assert.equal(
    appModule.appLayoutClassName({ compact: true, historyCollapsed: true, traceCollapsed: false }),
    "app-shell compact-layout history-collapsed",
  );
  assert.equal(
    appModule.appLayoutClassName({ compact: false, historyCollapsed: false, traceCollapsed: true }),
    "app-shell trace-collapsed",
  );
});

test("native Pygent events project to the same completed turn as persisted history", () => {
  const toolResult = JSON.stringify({
    status: "success",
    result: "package.json",
    error: null,
    tool_call_id: "call-1",
  });
  const events = [
    { kind: "model.reasoning.delta", data: { text: "Inspecting the workspace." } },
    {
      kind: "model.tool_call.completed",
      data: { call_id: "call-1", name: "read", arguments: { path: "package.json" } },
    },
    { kind: "tool.completed", data: { call_id: "call-1" } },
    {
      kind: "lora.runtime.message",
      data: {
        role: "tool",
        content: toolResult,
        payload: { role: "tool", tool_call_id: "call-1", name: "read" },
      },
    },
    { kind: "model.text.delta", data: { text: "This is " } },
    { kind: "model.text.delta", data: { text: "the answer." } },
    { kind: "execution.completed", data: {} },
  ];
  const initial = {
    id: "live-assistant",
    role: "assistant",
    content: "",
    status: "running",
    startedAt: 1,
    endedAt: null,
    sections: [],
  };
  const live = events.reduce(
    (message, event) => appModule.projectLiveAssistantEvent(message, event, 2),
    initial,
  );
  const [, history] = appModule.historyToMessages([
    { role: "user", content: "Inspect this project." },
    {
      role: "assistant",
      content: "",
      reasoning_content: "Inspecting the workspace.",
      tool_calls: [{ id: "call-1", function: { name: "read", arguments: { path: "package.json" } } }],
    },
    {
      role: "tool",
      content: toolResult,
      tool_call_id: "call-1",
      name: "read",
    },
    { role: "assistant", content: "This is the answer." },
  ]);

  assert.deepEqual(turnView(live), turnView(history));
  assert.equal(live.content, "This is the answer.");
  assert.equal(live.sections.some((section) => section.title === "Assistant content"), false);
});

test("assistant text becomes activity only when the same model turn proceeds to a tool call", () => {
  const initial = { role: "assistant", content: "", status: "running", sections: [] };
  const withText = appModule.projectLiveAssistantEvent(initial, {
    kind: "model.text.delta",
    data: { text: "I will inspect first." },
  });
  const withTool = appModule.projectLiveAssistantEvent(withText, {
    kind: "model.tool_call.completed",
    data: { call_id: "call-2", name: "glob", arguments: { pattern: "**/*.py" } },
  });

  assert.equal(withText.content, "I will inspect first.");
  assert.equal(withTool.content, "");
  assert.equal(withTool.sections[0].title, "Assistant content");
  assert.equal(withTool.sections[1].calls[0].id, "call-2");
});

test("sequential tool calls merge when no assistant text separates them", () => {
  const initial = { role: "assistant", content: "", status: "running", sections: [] };
  const events = [
    { kind: "model.tool_call.completed", data: { call_id: "call-a", name: "read", arguments: {} } },
    { kind: "tool.completed", data: { call_id: "call-a" } },
    { kind: "model.tool_call.completed", data: { call_id: "call-b", name: "glob", arguments: {} } },
    { kind: "tool.completed", data: { call_id: "call-b" } },
  ];
  const live = events.reduce(
    (message, event) => appModule.projectLiveAssistantEvent(message, event, 2),
    initial,
  );
  const [, history] = appModule.historyToMessages([
    { role: "user", content: "Inspect." },
    { role: "assistant", content: "", tool_calls: [{ id: "call-a", function: { name: "read", arguments: {} } }] },
    { role: "tool", content: "result-a", tool_call_id: "call-a", name: "read" },
    { role: "assistant", content: "", tool_calls: [{ id: "call-b", function: { name: "glob", arguments: {} } }] },
    { role: "tool", content: "result-b", tool_call_id: "call-b", name: "glob" },
  ]);

  for (const message of [live, history]) {
    const toolSections = message.sections.filter((section) => section.type === "tools");
    assert.equal(toolSections.length, 1);
    assert.deepEqual(toolSections[0].calls.map((call) => call.id), ["call-a", "call-b"]);
    assert.equal(toolSections[0].status, "done");
  }
});

test("assistant text keeps sequential tool calls in separate groups", () => {
  const initial = { role: "assistant", content: "", status: "running", sections: [] };
  const events = [
    { kind: "model.tool_call.completed", data: { call_id: "call-a", name: "read", arguments: {} } },
    { kind: "tool.completed", data: { call_id: "call-a" } },
    { kind: "model.text.delta", data: { text: "Checking another source." } },
    { kind: "model.tool_call.completed", data: { call_id: "call-b", name: "glob", arguments: {} } },
  ];
  const live = events.reduce(
    (current, event) => appModule.projectLiveAssistantEvent(current, event, 2),
    initial,
  );
  const [, history] = appModule.historyToMessages([
    { role: "user", content: "Inspect." },
    { role: "assistant", content: "", tool_calls: [{ id: "call-a", function: { name: "read", arguments: {} } }] },
    { role: "tool", content: "result-a", tool_call_id: "call-a", name: "read" },
    { role: "assistant", content: "Checking another source." },
    { role: "assistant", content: "", tool_calls: [{ id: "call-b", function: { name: "glob", arguments: {} } }] },
  ]);

  for (const message of [live, history]) {
    assert.deepEqual(message.sections.map((section) => section.type), ["tools", "text", "tools"]);
    assert.equal(message.content, "");
  }
});

test("hidden reasoning does not split otherwise adjacent tool calls", () => {
  const [, message] = appModule.historyToMessages([
    { role: "user", content: "Inspect." },
    { role: "assistant", content: "", tool_calls: [{ id: "call-a", function: { name: "read", arguments: {} } }] },
    { role: "tool", content: "result-a", tool_call_id: "call-a", name: "read" },
    { role: "assistant", content: "", reasoning_content: "Need another source." },
    { role: "assistant", content: "", tool_calls: [{ id: "call-b", function: { name: "glob", arguments: {} } }] },
  ]);

  assert.equal(message.sections.filter((section) => section.type === "tools").length, 1);
  assert.deepEqual(
    message.sections.find((section) => section.type === "tools").calls.map((call) => call.id),
    ["call-a", "call-b"],
  );
});

test("a tool failure stays local when the agent execution recovers", () => {
  const initial = { role: "assistant", content: "", status: "running", sections: [] };
  const withTool = appModule.projectLiveAssistantEvent(initial, {
    kind: "model.tool_call.completed",
    data: { call_id: "call-3", name: "bash", arguments: { command: "exit 1" } },
  });
  const failed = appModule.projectLiveAssistantEvent(withTool, {
    kind: "tool.failed",
    data: { call_id: "call-3", error_kind: "executor_error" },
  });
  const completed = appModule.projectLiveAssistantEvent(failed, {
    kind: "execution.completed",
    data: {},
  });

  assert.equal(failed.status, "running");
  assert.equal(failed.sections[0].status, "done");
  assert.equal(failed.sections[0].calls[0].status, "error");
  assert.equal(failed.sections[0].calls[0].result, "executor_error");
  assert.equal(completed.status, "success");
});

test("chat tool cards appear on tool start and retain results through completion and replay", () => {
  const start = { kind: "tool.started", data: { call_id: "slow", name: "bash", arguments: { command: "slow-command" } } };
  const initial = { role: "assistant", content: "", status: "running", sections: [] };
  const pending = appModule.projectLiveAssistantEvent(initial, start);
  assert.equal(pending.sections[0].calls[0].status, "running");
  assert.equal(pending.sections[0].calls[0].name, "bash");
  const result = appModule.projectLiveAssistantEvent(pending, {
    kind: "lora.runtime.message", data: { role: "tool", tool_call_id: "slow", content: "finished output" },
  });
  const completed = appModule.projectLiveAssistantEvent(result, { kind: "tool.completed", data: { call_id: "slow" } });
  const replayed = appModule.projectLiveAssistantEvent(completed, start);
  assert.equal(replayed.sections[0].calls.length, 1);
  assert.equal(replayed.sections[0].calls[0].status, "success");
  assert.equal(replayed.sections[0].calls[0].result, "finished output");
});

test("assistant runtime calls create chat cards before tool results without model deltas", () => {
  const projected = appModule.projectLiveAssistantEvent({ content: "", sections: [] }, {
    kind: "lora.runtime.message", data: { role: "assistant", tool_calls: [
      { id: "one", function: { name: "read", arguments: { path: "a" } } },
      { id: "two", function: { name: "read", arguments: { path: "b" } } },
    ] },
  });
  assert.equal(projected.sections[0].calls.length, 2);
  assert.ok(projected.sections[0].calls.every((call) => call.status === "running"));
  const failed = appModule.projectLiveAssistantEvent(projected, { kind: "tool.failed", data: { call_id: "one", error_kind: "timeout" } });
  assert.equal(failed.sections[0].calls[0].status, "error");
  assert.equal(failed.sections[0].calls[1].status, "running");
});

test("only an execution failure marks the whole assistant turn as failed", () => {
  const initial = { role: "assistant", content: "", status: "running", sections: [] };
  const failed = appModule.projectLiveAssistantEvent(
    initial,
    { kind: "execution.failed", data: { error: "agent exhausted its recovery steps" } },
    2,
  );

  assert.equal(failed.status, "error");
  assert.equal(failed.endedAt, 2);
  assert.equal(failed.content, "Error: agent exhausted its recovery steps");
});

test("persisted tool errors do not mark a recovered historical turn as failed", () => {
  const toolResult = JSON.stringify({
    status: "error",
    result: null,
    error: "filesystem_error",
    tool_call_id: "call-4",
  });
  const [, history] = appModule.historyToMessages([
    { role: "user", content: "Inspect the project." },
    {
      role: "assistant",
      content: "",
      tool_calls: [{ id: "call-4", function: { name: "read", arguments: { path: "missing.py" } } }],
    },
    { role: "tool", content: toolResult, tool_call_id: "call-4", name: "read" },
    { role: "assistant", content: "I recovered using another file." },
  ]);

  assert.equal(history.status, "success");
  assert.equal(history.content, "I recovered using another file.");
  assert.equal(history.sections[0].calls[0].status, "error");
});

test("completed turn durations survive history refreshes and subsequent turns", () => {
  const firstTiming = { case_run_id: "first", started_at: "2026-09-07T00:00:01Z", finished_at: "2026-09-07T00:00:16Z", status: "passed" };
  const secondTiming = { case_run_id: "second", started_at: "2026-09-07T00:00:20Z", finished_at: "2026-09-07T00:00:27Z", status: "passed" };
  const firstHistory = [
    { role: "user", content: "<user-message>First question</user-message>", run_timing: firstTiming },
    { role: "assistant", content: "First answer" },
  ];
  const refreshed = appModule.historyToMessages(firstHistory);
  assert.equal(activityHeaderText(refreshed[1], 90000), "Processed for 15s");

  const secondHistory = [
    ...firstHistory,
    { role: "user", content: "Second question", run_timing: secondTiming },
    { role: "assistant", content: "Second answer" },
  ];
  const reloaded = appModule.historyToMessages(secondHistory);
  assert.equal(activityHeaderText(reloaded[1], 90000), "Processed for 15s");
  assert.equal(activityHeaderText(reloaded[3], 90000), "Processed for 7s");
});

test("missing or mismatched historical timing does not become a fabricated zero duration", () => {
  const history = [
    { role: "user", content: "Question" },
    { role: "assistant", tool_calls: [{ id: "call", function: { name: "read", arguments: {} } }] },
    { role: "assistant", content: "Answer" },
  ];
  const unrelated = [
    { role: "user", content: "Different question" },
    { role: "assistant", startedAt: 1000, endedAt: 16000 },
  ];
  for (const timings of [undefined, unrelated]) {
    const [, message] = appModule.historyToMessages(history, timings);
    assert.equal(message.startedAt, undefined);
    assert.equal(message.endedAt, undefined);
    assert.equal(activityHeaderText(message, 90000), "Processed");
  }
  assert.equal(activityHeaderText({ status: "running", startedAt: 1000 }, 6000), "Processing for 5s");
  assert.equal(activityHeaderText({ status: "error" }, 6000), "Failed");
});

test("thinking streams in one preview and completes when the answer or tools begin", () => {
  const sections = [{ type: "text", title: "Thinking", content: "First line\nSecond line" }];
  const message = { status: "running", sections, content: "" };
  const state = appModule.thinkingActivityState(message);
  assert.equal(state.running, true);
  const html = renderToStaticMarkup(React.createElement(appModule.ThinkingActivity, state));
  assert.match(html, /thinking-preview[^>]*>First line Second line</);
  assert.match(html, /Thinking/);
  assert.doesNotMatch(html, /<details[^>]* open/);
  assert.equal(appModule.thinkingActivityState({ ...message, content: "Answer" }).running, false);
  assert.equal(appModule.thinkingActivityState({ ...message, sections: [...sections, { type: "tools" }] }).running, false);
  const completed = appModule.thinkingActivityState({ ...message, status: "success" });
  const finished = renderToStaticMarkup(React.createElement(appModule.ThinkingActivity, completed));
  assert.match(finished, /Thinking complete/);
  assert.doesNotMatch(finished, /thinking-preview/);
  assert.match(finished, /First line/);
  assert.match(finished, /Second line/);
});

test("reasoning stays below processing activity and tool output", () => {
  const html = renderToStaticMarkup(React.createElement(appModule.AssistantActivity, {
    message: {
      status: "running", content: "", startedAt: Date.now(),
      sections: [
        { type: "text", title: "Assistant content", content: "Earlier activity" },
        { type: "tools", status: "done", calls: [] },
        { type: "text", title: "Thinking", content: "Latest reasoning" },
      ],
    },
  }));
  assert.ok(html.indexOf("Processing for") < html.indexOf("Earlier activity"));
  assert.ok(html.indexOf("Earlier activity") < html.indexOf('class="thinking-activity"'));
  assert.ok(html.indexOf('class="tool-group"') < html.indexOf('class="thinking-activity"'));
  assert.match(html, /Thinking/);
  assert.doesNotMatch(html, /Waiting for model output/);
});

test("finished reasoning stays at the bottom while processing continues", () => {
  const html = renderToStaticMarkup(React.createElement(appModule.AssistantActivity, {
    message: {
      status: "running", content: "Answer", startedAt: Date.now(),
      sections: [{ type: "text", title: "Thinking", content: "Preserved reasoning" }],
    },
  }));
  assert.match(html, /Thinking complete/);
  assert.ok(html.indexOf('class="activity-head"') < html.indexOf('class="thinking-activity"'));
  assert.ok(html.indexOf('class="activity-detail"') < html.indexOf('class="thinking-activity"'));
});

test("collapsed processing keeps only the processed header at the top", () => {
  const html = renderToStaticMarkup(React.createElement(appModule.AssistantActivity, {
    message: {
      status: "success", content: "Answer", startedAt: 1000, endedAt: 16000,
      sections: [{ type: "text", title: "Thinking", content: "Preserved reasoning" }],
    },
  }));
  assert.match(html, /Processed for 15s/);
  assert.doesNotMatch(html, /Thinking complete/);
  assert.doesNotMatch(html, /Preserved reasoning/);
  assert.doesNotMatch(html, /class="activity-detail"/);
});

test("history preserves reasoning from the final assistant message", () => {
  const [, message] = appModule.historyToMessages([
    { role: "user", content: "Question" },
    { role: "assistant", content: "Answer", metadata: { reasoning_content: "Full final reasoning" } },
  ]);
  assert.equal(message.content, "Answer");
  assert.equal(appModule.thinkingActivityState(message).content, "Full final reasoning");
  assert.equal(appModule.thinkingActivityState(message).running, false);
});

test("model-id changes remain saveable with an existing credential reference", () => {
  const model = {
    connection: "primary", model_id: "pool_0021", protocol: "openai_chat_completions",
    provider_options: {}, capabilities: { limits: { context_tokens: 1000, max_output_tokens: 100 } },
  };
  const connections = { primary: { provider: "openai", credential: { env: "MODEL_API_KEY" }, credential_source: "user-file:MODEL_API_KEY", protocols: { openai_chat_completions: { base_url: "https://example.test/v1" } } } };
  const settings = { connections, models: { primary: model }, model_groups: { coding: { models: ["primary"] } }, default_model_group: "coding" };
  for (const saving of [false, true]) {
    const html = renderToStaticMarkup(React.createElement(appModule.SettingsPanel, {
      settings, disabled: saving, onClose() {}, onSave() {},
    }));
    const save = html.match(/<button[^>]*aria-label="Save and Reload"[^>]*>/)[0];
    assert.equal(save.includes("disabled"), saving);
    assert.equal(html.includes("正在保存…"), saving);
  }
  const draft = { connections, models: { primary: model }, modelGroups: { coding: { models: ["primary"] } }, defaultModelGroup: "coding", credentialValues: {} };
  assert.equal(appModule.modelGroupValidationError(draft), "");
  const payload = settingsPayload(draft);
  assert.equal(payload.model_config.models.primary.model_id, "pool_0021");
  assert.equal(JSON.stringify(payload).includes("saved-secret"), false);
  assert.notEqual(appModule.modelGroupValidationError({
    ...draft, models: { primary: { ...model, model_id: "" } },
  }), "");
});

test("settings separate native model identity from connection fields", () => {
  const model = {
    connection: "openai-main", model_id: "gpt-test", protocol: "openai_responses",
    provider_options: {}, capabilities: { limits: { context_tokens: 1000, max_output_tokens: 100 } },
  };
  const connections = { "openai-main": { provider: "openai", credential: { env: "OPENAI_API_KEY" }, protocols: { openai_responses: { base_url: "https://api.openai.com/v1" } }, verify_ssl: true } };
  const html = renderToStaticMarkup(React.createElement(appModule.SettingsPanel, {
    settings: { connections, models: { primary: model }, model_groups: { coding: { models: ["primary"] } }, default_model_group: "coding" },
    disabled: false, onClose() {}, onSave() {},
  }));

  assert.match(html, /模型信息/);
  assert.match(html, /决定调用谁、使用哪种 API 格式；不属于连接/);
  assert.match(html, /1 · 服务连接/);
  assert.match(html, /连接名称/);
  assert.match(html, /供应商/);
  assert.match(html, /Pygent 内置/);
  assert.match(html, /自定义供应商/);
  assert.doesNotMatch(html, /自定义 Provider ID/);
  assert.match(html, /接口地址/);
  assert.match(html, /添加接口/);
  assert.ok(html.indexOf("Base URL") < html.indexOf("接口协议"));
  assert.equal(appModule.providerSelectionValue("openai", null), "openai");
  assert.equal(appModule.providerSelectionValue("company-gateway", { providers: { openai: {} } }), "__custom__");
  assert.deepEqual(appModule.renamedConnectionValue({ provider: "gateway" }, "gateway", "office-gateway", { providers: { openai: {} } }), { provider: "office-gateway" });
  assert.deepEqual(appModule.renamedConnectionValue({ provider: "openai" }, "gateway", "office-gateway", { providers: { openai: {} } }), { provider: "openai" });
  const catalogs = { providers: {
    openai: { protocols: { openai_responses: {}, openai_chat_completions: {} } },
    anthropic: { protocols: { anthropic_messages: {} } },
  } };
  assert.deepEqual(appModule.protocolChoicesForConnection("openai", catalogs), ["openai_responses", "openai_chat_completions"]);
  assert.deepEqual(appModule.protocolChoicesForConnection("company-gateway", catalogs), ["openai_responses", "openai_chat_completions", "anthropic_messages", "gemini_generate_content"]);
  assert.match(html, /2 · 模型目录/);
  assert.match(html, /openai-main · openai/);
  assert.match(html, /OpenAI Responses/);
  assert.match(html, /本地名称/);
  assert.match(html, /仅供 Lora 的模型组引用/);
  assert.doesNotMatch(html, /逗号分隔/);
  assert.match(html, /type="checkbox" checked=""/);
});

test("existing idle chat exposes only child models from its fixed group", () => {
  const html = renderToStaticMarkup(React.createElement(appModule.ChatPane, {
    activeSession: {
      session_id: "s1", title: "Chat", model_group_name: "coding", selected_model_key: "backup",
      selectable_models: [
        { model_key: "main", provider: "openai", model_id: "gpt-main" },
        { model_key: "backup", provider: "anthropic", model_id: "claude-backup" },
      ],
    },
    messages: [], settings: { workspace_root: "", model_groups: { coding: { models: ["main", "backup"] }, vision: { models: ["vision-only"] } } },
    status: "Ready", running: false, approvals: [], projects: [], api: {},
    onSendMessage() {}, onSteering() {}, onApproval() {}, onChangePermissions() {}, onChangeModel() {},
  }));
  assert.match(html, /coding/);
  assert.match(html, /backup/);
  assert.doesNotMatch(html, /vision-only/);
  assert.doesNotMatch(html, /aria-label="模型组"/);
});

test("automation triggers render as system-origin cards with the raw instruction", () => {
  const [message] = appModule.historyToMessages([{
    role: "user",
    kind: "lora.automation.trigger",
    content: "<automation-trigger><instructions>escaped</instructions></automation-trigger>",
    data: { origin: "automation", raw_content: "检查构建状态" },
  }]);

  assert.equal(message.role, "automation");
  assert.equal(message.content, "检查构建状态");
});

test("reopening an active session replays only its current turn without duplicating partial output", () => {
  const restored = appModule.messagesForRecovery({
    run_history_start_index: 2,
    history: [
      { role: "user", content: "Previous" }, { role: "assistant", content: "Completed" },
      { role: "user", content: "Current" }, { role: "assistant", content: "Partial output" },
    ],
  }, "resumed");
  assert.deepEqual(restored.map((message) => message.content), ["Previous", "Completed", "Current", ""]);
  const replayed = appModule.projectLiveAssistantEvent(restored[3], {
    kind: "model.text.delta", data: { text: "Partial output" },
  });
  assert.equal(replayed.content, "Partial output");
  assert.throws(() => appModule.messagesForRecovery({ history: [], run_history_start_index: 1 }, "bad"));
});

function turnView(message) {
  return {
    role: message.role,
    content: message.content,
    status: message.status,
    sections: message.sections.map((section) => ({
      type: section.type,
      title: section.title,
      content: section.content,
      status: section.status,
      calls: section.calls?.map((call) => ({
        id: call.id,
        name: call.name,
        arguments: call.arguments,
        result: call.result,
        status: call.status,
      })),
    })),
  };
}

test("essential connection and model fields remain visible before optional details", () => {
  const settings = {
    connections: { service: { provider: "openai", credential: { env: "API_KEY" }, credential_source: "missing", protocols: { openai_responses: { base_url: "https://example.test/v1" } } } },
    models: { primary: { connection: "service", protocol: "openai_responses", model_id: "example", capabilities: { limits: { context_tokens: 1000 } } } },
    model_groups: { coding: { models: ["primary"] } }, default_model_group: "coding",
  };
  const html = renderToStaticMarkup(React.createElement(appModule.SettingsPanel, { settings, disabled: false, onClose() {}, onSave() {} }));
  const connection = html.slice(html.indexOf('connection-route-card'), html.indexOf('aria-label="Native Pygent models"'));
  const model = html.slice(html.indexOf('aria-label="Native Pygent models"'), html.indexOf('aria-label="Model groups"'));
  for (const field of ['连接名称', 'API Key', 'Base URL']) assert.ok(connection.indexOf(field) >= 0 && connection.indexOf(field) < connection.indexOf('<details'));
  for (const field of ['使用连接', '服务商模型 ID', '所属模型组']) assert.ok(model.indexOf(field) >= 0 && model.indexOf(field) < model.indexOf('<details'));
  assert.match(html, /连接未验证|请填写 API Key/);
  assert.match(html.match(/<button[^>]*aria-label="Save and Reload"[^>]*>/)[0], /disabled/);
  assert.match(html, /默认模型组 · /);
});
