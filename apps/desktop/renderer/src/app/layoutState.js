export const COMPACT_LAYOUT_QUERY = "(max-width: 1280px)";

export function createInitialLayoutState({ compact = false } = {}) {
  return {
    historyCollapsed: compact,
    traceCollapsed: compact,
  };
}

export function adaptLayoutToCompactViewport(state, compact) {
  return compact
    ? { ...state, historyCollapsed: true, traceCollapsed: true }
    : state;
}

export function toggleHistory(state, { compact = false } = {}) {
  const historyCollapsed = !state.historyCollapsed;
  return {
    ...state,
    historyCollapsed,
    traceCollapsed: compact && !historyCollapsed ? true : state.traceCollapsed,
  };
}

export function toggleTrace(state, { compact = false } = {}) {
  const traceCollapsed = !state.traceCollapsed;
  return {
    ...state,
    historyCollapsed: compact && !traceCollapsed ? true : state.historyCollapsed,
    traceCollapsed,
  };
}
