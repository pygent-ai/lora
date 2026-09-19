export const SESSION_HISTORY_LIMIT = 200;
export const TRACE_EVENT_LIMIT = 500;
export const CONTEXT_SNAPSHOT_LIMIT = 50;

export async function refreshActiveSession({ api, sessionId, scopeId, signal, isCurrent, onResume, onSnapshot }) {
  const current = () => !signal.aborted && isCurrent();
  if (!current()) return;
  const detail = await api.getSession(sessionId, {
    scopeId,
    historyLimit: SESSION_HISTORY_LIMIT,
    signal,
  });
  if (!current()) return;
  if (detail.runtime_execution_id) {
    onResume(detail);
    return;
  }
  const runId = detail.session.last_case_run_id;
  const trace = runId
    ? await api.getTraceEvents(sessionId, runId, {
        eventLimit: TRACE_EVENT_LIMIT,
        contextSnapshotLimit: CONTEXT_SNAPSHOT_LIMIT,
        signal,
      })
    : { events: [], events_total: 0, events_truncated: false, context_snapshots: [], context_snapshots_total: 0, context_snapshots_truncated: false };
  if (current()) onSnapshot(detail, trace);
}
