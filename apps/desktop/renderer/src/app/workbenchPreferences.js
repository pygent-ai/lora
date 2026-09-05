const KEY = "lora.workbench.preferences.v1";

export function loadWorkbenchPreferences() {
  try {
    const value = JSON.parse(globalThis.window?.localStorage?.getItem(KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

export function saveWorkbenchPreferences(patch) {
  try {
    globalThis.window?.localStorage?.setItem(KEY, JSON.stringify({ ...loadWorkbenchPreferences(), ...patch }));
  } catch { /* Keep the workbench usable when browser storage is unavailable. */ }
}

export function sessionDateLabel(session) {
  const date = new Date(session.updated_at || session.created_at || "");
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}
