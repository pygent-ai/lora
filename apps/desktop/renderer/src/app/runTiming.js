// Both live events and history carry the same durable run_timing contract.
export function runTimingFields(timing) {
  if (!timing || typeof timing !== "object") return {};
  const startedAt = parseTimestamp(timing.started_at);
  const endedAt = parseTimestamp(timing.finished_at);
  return {
    caseRunId: timing.case_run_id,
    ...(startedAt !== undefined ? { startedAt } : {}),
    ...(endedAt !== undefined && (startedAt === undefined || endedAt >= startedAt) ? { endedAt } : {}),
    ...(timing.status === "running" ? { status: "running" } : {}),
    ...(timing.status === "passed" ? { status: "success" } : {}),
    ...(["failed", "error", "skipped"].includes(timing.status) ? { status: "error" } : {}),
  };
}

function parseTimestamp(value) {
  const timestamp = typeof value === "string" && value ? Date.parse(value) : NaN;
  return Number.isFinite(timestamp) ? timestamp : undefined;
}

export function activityHeaderText(message, now) {
  const running = message.status === "running";
  const label = running ? "Processing" : message.status === "error" ? "Failed" : "Processed";
  const end = running ? now : message.endedAt;
  if (!Number.isFinite(message.startedAt) || !Number.isFinite(end) || end < message.startedAt) {
    return label;
  }
  const duration = formatProcessedDuration((end - message.startedAt) / 1000);
  return `${label} ${message.status === "error" ? "after" : "for"} ${duration}`;
}

function formatProcessedDuration(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  if (value < 60) return `${value}s`;
  const minutes = Math.floor(value / 60);
  const remainder = value % 60;
  return remainder > 0 ? `${minutes}min${remainder}s` : `${minutes}min`;
}
