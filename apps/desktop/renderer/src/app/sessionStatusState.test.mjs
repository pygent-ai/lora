import assert from "node:assert/strict";
import test from "node:test";

import {
  ACKNOWLEDGED_SESSION_STATUSES_KEY,
  acknowledgeStoredSessionStatus,
  loadAcknowledgedSessionStatuses,
  sessionStatusIdentity,
} from "./sessionStatusState.js";

test("acknowledged session status survives a renderer restart", () => {
  const storage = createMemoryStorage();
  const session = {
    session_id: "session-1",
    last_case_run_status: "completed",
    updated_at: "2026-08-21T10:00:00Z",
  };

  const acknowledged = acknowledgeStoredSessionStatus({}, session, storage);
  const restored = loadAcknowledgedSessionStatuses(storage);

  assert.deepEqual(restored, acknowledged);
  assert.equal(restored[session.session_id], sessionStatusIdentity(session));
});

test("a later run is not hidden by an older acknowledgement", () => {
  const storage = createMemoryStorage();
  const completed = {
    session_id: "session-1",
    last_case_run_status: "completed",
    updated_at: "2026-08-21T10:00:00Z",
  };
  const laterRun = { ...completed, updated_at: "2026-08-21T10:05:00Z" };

  acknowledgeStoredSessionStatus({}, completed, storage);
  const restored = loadAcknowledgedSessionStatuses(storage);

  assert.notEqual(restored[completed.session_id], sessionStatusIdentity(laterRun));
});

test("running sessions are not acknowledged and corrupt storage is ignored", () => {
  const storage = createMemoryStorage();
  storage.setItem(ACKNOWLEDGED_SESSION_STATUSES_KEY, "not json");

  assert.deepEqual(loadAcknowledgedSessionStatuses(storage), {});
  assert.deepEqual(
    acknowledgeStoredSessionStatus({}, { session_id: "session-1", last_case_run_status: "running" }, storage),
    {},
  );
});

function createMemoryStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
  };
}
