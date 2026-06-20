import test from "node:test";
import assert from "node:assert/strict";

import { createInitialLayoutState, toggleHistory, toggleTrace } from "./layoutState.js";

test("desktop workbench layout state starts with history and trace expanded", () => {
  assert.deepEqual(createInitialLayoutState(), {
    historyCollapsed: false,
    traceCollapsed: false,
  });
});

test("desktop workbench layout state toggles history and trace independently", () => {
  const initial = createInitialLayoutState();
  const historyCollapsed = toggleHistory(initial);
  const traceCollapsed = toggleTrace(historyCollapsed);

  assert.deepEqual(historyCollapsed, {
    historyCollapsed: true,
    traceCollapsed: false,
  });
  assert.deepEqual(traceCollapsed, {
    historyCollapsed: true,
    traceCollapsed: true,
  });
  assert.deepEqual(toggleHistory(traceCollapsed), {
    historyCollapsed: false,
    traceCollapsed: true,
  });
});
