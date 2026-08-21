export const ACKNOWLEDGED_SESSION_STATUSES_KEY = "lora.desktop.acknowledged-session-statuses.v1";

export function sessionStatusIdentity(session) {
  return [
    session?.last_case_run_status || sessionStatusKind(session?.last_case_run_status),
    session?.updated_at || session?.created_at || "",
  ].join(":");
}

export function loadAcknowledgedSessionStatuses(storage = browserStorage()) {
  if (!storage) {
    return {};
  }

  try {
    const parsed = JSON.parse(storage.getItem(ACKNOWLEDGED_SESSION_STATUSES_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).filter(
        ([sessionId, identity]) => typeof sessionId === "string" && typeof identity === "string",
      ),
    );
  } catch {
    return {};
  }
}

export function acknowledgeStoredSessionStatus(current, session, storage = browserStorage()) {
  const sessionId = String(session?.session_id || "");
  if (!sessionId || sessionStatusKind(session?.last_case_run_status) === "running") {
    return current;
  }

  const next = {
    ...current,
    [sessionId]: sessionStatusIdentity(session),
  };
  persistAcknowledgedSessionStatuses(next, storage);
  return next;
}

export function persistAcknowledgedSessionStatuses(statuses, storage = browserStorage()) {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(ACKNOWLEDGED_SESSION_STATUSES_KEY, JSON.stringify(statuses));
  } catch {
    // Read markers are optional UI state; storage failures must not block navigation.
  }
}

function browserStorage() {
  try {
    return globalThis.window?.localStorage || null;
  } catch {
    return null;
  }
}

function sessionStatusKind(status) {
  const value = String(status || "").toLowerCase();
  if (value.includes("run")) {
    return "running";
  }
  if (value.includes("error") || value.includes("fail")) {
    return "error";
  }
  return "success";
}
