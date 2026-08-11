import assert from "node:assert/strict";
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
