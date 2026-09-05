import test from "node:test";
import assert from "node:assert/strict";
import { loadWorkbenchPreferences, saveWorkbenchPreferences, sessionDateLabel } from "./workbenchPreferences.js";

test("preferences tolerate unavailable or corrupted storage and preserve other fields", () => {
  const original = globalThis.window;
  try {
    let data = "not-json";
    globalThis.window = { localStorage: { getItem: () => data, setItem: (_key, value) => { data = value; } } };
    assert.deepEqual(loadWorkbenchPreferences(), {});
    data = 'null';
    assert.deepEqual(loadWorkbenchPreferences(), {});
    data = '{"other":true}';
    saveWorkbenchPreferences({ layout: { historyCollapsed: false, traceCollapsed: true } });
    assert.equal(loadWorkbenchPreferences().other, true);
    assert.equal(loadWorkbenchPreferences().layout.traceCollapsed, true);
    globalThis.window = { get localStorage() { throw new Error("blocked"); } };
    assert.deepEqual(loadWorkbenchPreferences(), {});
    assert.doesNotThrow(() => saveWorkbenchPreferences({ layout: {} }));
  } finally {
    if (original === undefined) delete globalThis.window;
    else globalThis.window = original;
  }
});

test("history dates handle absent and invalid API timestamps", () => {
  assert.equal(sessionDateLabel({}), "");
  assert.equal(sessionDateLabel({ updated_at: "invalid" }), "");
  assert.ok(sessionDateLabel({ created_at: "2026-09-05T12:00:00Z" }));
});
