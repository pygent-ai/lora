export async function refreshActiveSession({ api, sessionId, scopeId, signal, isCurrent, onResume, onSnapshot }) {
  const current = () => !signal.aborted && isCurrent();
  if (!current()) return;
  const detail = await api.getSession(sessionId, { scopeId, signal });
  if (!current()) return;
  if (detail.runtime_execution_id) {
    onResume(detail);
    return;
  }
  const runId = detail.session.last_case_run_id;
  const trace = runId
    ? await api.getTraceEvents(sessionId, runId, { signal })
    : { events: [], context_snapshots: [] };
  if (current()) onSnapshot(detail, trace);
}
