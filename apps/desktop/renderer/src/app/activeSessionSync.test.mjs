import assert from "node:assert/strict";
import test from "node:test";
import { refreshActiveSession } from "./activeSessionSync.js";

function fixture(overrides = {}) {
  const snapshots = [];
  const resumed = [];
  const detail = { session: { session_id: "child", last_case_run_id: "new-run" }, history: [] };
  const trace = { events: [{ id: "new-event" }], context_snapshots: [{ id: "new-context" }] };
  const controller = new AbortController();
  return {
    snapshots, resumed, detail, trace, controller,
    options: {
      api: { getSession: async () => detail, getTraceEvents: async () => trace, ...overrides },
      sessionId: "child", scopeId: "conversation", signal: controller.signal,
      isCurrent: () => true,
      onResume: (value) => resumed.push(value),
      onSnapshot: (...value) => snapshots.push(value),
    },
  };
}

test("a background-completed run refreshes trace and context for the open session", async () => {
  const f = fixture();
  f.options.api.getTraceEvents = async (sessionId, runId) => {
    assert.equal(sessionId, "child");
    assert.equal(runId, "new-run");
    return f.trace;
  };
  await refreshActiveSession(f.options);
  assert.deepEqual(f.snapshots, [[f.detail, f.trace]]);
});

test("an externally started execution attaches to the existing stream", async () => {
  const f = fixture({ getTraceEvents: () => assert.fail("must use the live stream") });
  f.detail.runtime_execution_id = "external-execution";
  await refreshActiveSession(f.options);
  assert.deepEqual(f.resumed, [f.detail]);
  assert.deepEqual(f.snapshots, []);
});

test("an already streaming session does not poll or replace live data", async () => {
  const f = fixture({ getSession: () => assert.fail("already streaming") });
  f.options.isCurrent = () => false;
  await refreshActiveSession(f.options);
  assert.deepEqual(f.snapshots, []);
});

test("switching sessions during detail loading does not attach the old execution", async () => {
  const f = fixture();
  let current = true;
  f.options.isCurrent = () => current;
  f.options.api.getSession = async () => {
    current = false;
    return { ...f.detail, runtime_execution_id: "old-execution" };
  };
  await refreshActiveSession(f.options);
  assert.deepEqual(f.resumed, []);
  assert.deepEqual(f.snapshots, []);
});

test("switching sessions or aborting during trace loading discards the stale snapshot", async () => {
  for (const abort of [false, true]) {
    const f = fixture();
    let current = true;
    f.options.isCurrent = () => current;
    f.options.api.getTraceEvents = async () => {
      if (abort) f.controller.abort();
      else current = false;
      return f.trace;
    };
    await refreshActiveSession(f.options);
    assert.deepEqual(f.snapshots, []);
  }
});

test("a session without a run clears previous trace and context", async () => {
  const f = fixture({ getTraceEvents: () => assert.fail("no run") });
  f.detail.session.last_case_run_id = null;
  await refreshActiveSession(f.options);
  assert.deepEqual(f.snapshots[0][1], { events: [], context_snapshots: [] });
});
