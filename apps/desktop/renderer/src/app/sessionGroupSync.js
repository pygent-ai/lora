// Share ordering between explicit workbench refreshes and background list refreshes.
export function createSessionGroupSync(api, onGroups) {
  let revision = 0;

  async function refresh(options = {}) {
    const requestRevision = ++revision;
    const response = await api.listSessionGroups(options);
    if (requestRevision === revision && !options.signal?.aborted) {
      onGroups(response.groups || []);
    }
    return response;
  }

  return { refresh, start: (options) => startBackgroundRefresh(refresh, options) };
}

export function startBackgroundRefresh(refresh, { window, document, intervalMs = 2000 }) {
  let stopped = false;
  let pending = false;
  let timer;
  const controller = new AbortController();

  async function poll() {
    if (stopped || pending) return;
    window.clearTimeout(timer);
    pending = true;
    try {
      if (document.visibilityState !== "hidden") {
        await refresh({ signal: controller.signal });
      }
    } catch {
      // Background failures retry without replacing the active chat's error state.
    } finally {
      pending = false;
      if (!stopped) timer = window.setTimeout(poll, intervalMs);
    }
  }

  timer = window.setTimeout(poll, intervalMs);
  window.addEventListener("focus", poll);
  document.addEventListener("visibilitychange", poll);
  return () => {
    stopped = true;
    controller.abort();
    window.clearTimeout(timer);
    window.removeEventListener("focus", poll);
    document.removeEventListener("visibilitychange", poll);
  };
}
