import test from "node:test";
import assert from "node:assert/strict";

import {
  adaptLayoutToCompactViewport,
  createInitialLayoutState,
  toggleHistory,
  toggleTrace,
} from "./layoutState.js";

test("desktop workbench starts with navigation visible and inspector tucked away", () => {
  assert.deepEqual(createInitialLayoutState(), {
    historyCollapsed: false,
    traceCollapsed: true,
  });
});

test("desktop workbench layout state toggles history and trace independently", () => {
  const initial = createInitialLayoutState();
  const historyCollapsed = toggleHistory(initial);
  const traceCollapsed = toggleTrace(historyCollapsed);

  assert.deepEqual(historyCollapsed, {
    historyCollapsed: true,
    traceCollapsed: true,
  });
  assert.deepEqual(traceCollapsed, {
    historyCollapsed: true,
    traceCollapsed: false,
  });
  assert.deepEqual(toggleHistory(traceCollapsed), {
    historyCollapsed: false,
    traceCollapsed: false,
  });
});

test("compact workbench starts collapsed and keeps only one panel expanded", () => {
  const initial = createInitialLayoutState({ compact: true });
  const historyExpanded = toggleHistory(initial, { compact: true });
  const traceExpanded = toggleTrace(historyExpanded, { compact: true });

  assert.deepEqual(initial, {
    historyCollapsed: true,
    traceCollapsed: true,
  });
  assert.deepEqual(historyExpanded, {
    historyCollapsed: false,
    traceCollapsed: true,
  });
  assert.deepEqual(traceExpanded, {
    historyCollapsed: true,
    traceCollapsed: false,
  });
});

test("entering compact mode collapses both workbench panels", () => {
  assert.deepEqual(
    adaptLayoutToCompactViewport(createInitialLayoutState(), true),
    {
      historyCollapsed: true,
      traceCollapsed: true,
    },
  );
});
