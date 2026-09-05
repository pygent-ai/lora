import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { after, before } from "node:test";
import { fileURLToPath } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { activityHeaderText } from "./runTiming.js";

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

test("trace panel provides expand-all and collapse-all controls", () => {
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

  assert.match(html, />Expand all</);
  assert.match(html, />Collapse all</);
  assert.match(html, /aria-label="Expand model.request"/);
  assert.match(html, /aria-label="Filter events by prefix"/);
  assert.match(html, />model</);
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

test("model group validation requires unique routes and an active fallback", () => {
  const valid = {
    profile: "production",
    modelRoutes: [
      { id: "primary", provider: "openai", model_name: "main", base_url: "https://main.test", api_key_env: "MAIN_KEY" },
      { id: "backup", provider: "openai", model_name: "backup", base_url: "https://backup.test", api_key_env: "BACKUP_KEY" },
    ],
    fallback: ["primary", "backup"],
  };

  assert.equal(appModule.modelGroupValidationError(valid), "");
  assert.match(appModule.modelGroupValidationError({ ...valid, fallback: [] }), /at least one route/i);
  assert.match(appModule.modelGroupValidationError({ ...valid, modelRoutes: [valid.modelRoutes[0], valid.modelRoutes[0]] }), /unique/i);
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
  assert.doesNotMatch(css, /@media\s*\(max-width:/);
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
