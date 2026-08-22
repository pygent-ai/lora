import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { after, before } from "node:test";
import { fileURLToPath } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const desktopRoot = fileURLToPath(new URL("../../../", import.meta.url));
let appModule;
let vite;

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

test("desktop app renders its initial workbench", async () => {
  const html = renderToStaticMarkup(React.createElement(appModule.App));

  assert.match(html, /class="app-shell/);
  assert.match(html, /aria-label="Workbench"/);
  assert.match(html, /Choose Project/);
});

test("trace event updates scroll the inspector to the latest item", () => {
  const traceList = { scrollTop: 24, scrollHeight: 960 };

  appModule.scrollTraceToLatest(traceList);

  assert.equal(traceList.scrollTop, 960);
  assert.doesNotThrow(() => appModule.scrollTraceToLatest(null));
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

test("trace config formats model routes without object coercion", () => {
  assert.equal(
    appModule.formatConfigValue("routes", [
      { id: "primary", provider: "openai", model_name: "deepseek-v4-flash" },
      { id: "backup", provider: "openai", model_name: "gpt-5" },
    ]),
    "primary  openai / deepseek-v4-flash\nbackup  openai / gpt-5",
  );
  assert.equal(appModule.formatConfigValue("max_steps", 0), "0");
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

  assert.match(html, /class="primary-action"[^>]*title="New chat"/);
  assert.doesNotMatch(html, /class="primary-action"[^>]*disabled/);
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
