import assert from "node:assert/strict";
import test from "node:test";

import {
  activityHeaderText,
  formatProcessedDuration,
  thinkingHeaderSource,
  toolHeaderSource,
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
