import assert from "node:assert/strict";
import test from "node:test";

import {
  activityFallbackDetailText,
  activityHeaderText,
  activityLiveThinkingText,
  appendLiveThinkingContent,
  formatProcessedDuration,
  thinkingHeaderSource,
  toolHeaderSource,
  visibleThinkingText,
} from "./activityModel.js";

test("activity header shows live thinking text over the timer", () => {
  const now = Date.UTC(2026, 5, 20, 12, 0, 10);
  const message = {
    status: "running",
    startedAt: now - 10_000,
    headerSource: thinkingHeaderSource("I am checking the current transcript structure."),
  };

  assert.equal(activityHeaderText(message, [], now), "I am checking the current transcript structure.");
});

test("activity header returns to timer when a newer tool call has no description", () => {
  const now = Date.UTC(2026, 5, 20, 12, 1, 12);
  const calls = [{ id: "call_1", name: "read", description: "", status: "running" }];
  const message = {
    status: "running",
    startedAt: now - 72_000,
    headerSource: toolHeaderSource(calls),
  };

  assert.equal(activityHeaderText(message, [{ type: "tools", calls }], now), "Thinking for 1min12s");
});

test("activity header ignores raw tool call transcripts in thinking text", () => {
  const now = Date.UTC(2026, 5, 20, 12, 1, 12);
  const message = {
    status: "running",
    startedAt: now - 72_000,
    headerSource: thinkingHeaderSource("Tool call: tool\n{}"),
  };

  assert.equal(activityHeaderText(message, [], now), "Thinking for 1min12s");
});

test("running activity does not duplicate live header text as fallback detail", () => {
  const message = {
    status: "running",
    content: "Let me now examine the actual source code to compare with the docs.",
    headerSource: thinkingHeaderSource("Let me now examine the actual source code to compare with the docs."),
  };

  assert.equal(activityFallbackDetailText(message), "");
});

test("assistant delta is accumulated as live activity thinking content", () => {
  const first = appendLiveThinkingContent("Thinking", "Let me now examine");
  const second = appendLiveThinkingContent(first, " the actual source code.");

  assert.equal(second, "Let me now examine the actual source code.");
});

test("live activity thinking text uses the activity content source", () => {
  const message = {
    status: "running",
    content: "Now let me also check the config loader.",
  };

  assert.equal(activityLiveThinkingText(message), "Now let me also check the config loader.");
});

test("live activity thinking text filters raw tool call content", () => {
  const message = {
    status: "running",
    content: "Tool call: tool\n{}",
  };

  assert.equal(activityLiveThinkingText(message), "");
});

test("completed activity can still show fallback detail text", () => {
  const message = {
    status: "success",
    title: "Processed for 20s",
    content: "Previous thinking",
  };

  assert.equal(activityFallbackDetailText(message), "Previous thinking");
});

test("visible thinking text filters raw tool call transcripts", () => {
  assert.equal(visibleThinkingText("Tool call: tool\n{}"), "");
  assert.equal(visibleThinkingText("I am checking the current transcript structure."), "I am checking the current transcript structure.");
});

test("raw tool call transcript does not override a new tool timer", () => {
  const now = Date.UTC(2026, 5, 20, 12, 1, 12);
  const calls = [{ id: "call_1", name: "tool", description: "", status: "running" }];
  const message = {
    status: "running",
    startedAt: now - 72_000,
    headerSource: toolHeaderSource(calls),
  };

  assert.equal(activityHeaderText(message, [{ type: "tools", calls }], now), "Thinking for 1min12s");
});

test("activity header uses the latest running tool description", () => {
  const now = Date.UTC(2026, 5, 20, 12, 0, 20);
  const calls = [
    { id: "call_1", name: "read", description: "Reading App.jsx", status: "success" },
    { id: "call_2", name: "rg", description: "Searching activity rendering", status: "running" },
  ];
  const message = {
    status: "running",
    startedAt: now - 20_000,
    headerSource: toolHeaderSource(calls),
  };

  assert.equal(activityHeaderText(message, [{ type: "tools", calls }], now), "Searching activity rendering");
});

test("activity header shows processed title after completion", () => {
  const now = Date.UTC(2026, 5, 20, 12, 0, 20);
  const message = {
    status: "success",
    title: "Processed for 20s",
    headerSource: thinkingHeaderSource("old thinking"),
  };

  assert.equal(activityHeaderText(message, [], now), "Processed for 20s");
});

test("processed duration uses compact min and second format", () => {
  assert.equal(formatProcessedDuration(8), "8s");
  assert.equal(formatProcessedDuration(72), "1min12s");
  assert.equal(formatProcessedDuration(120), "2min");
});
