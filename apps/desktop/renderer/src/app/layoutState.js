export function createInitialLayoutState() {
  return {
    historyCollapsed: false,
    traceCollapsed: false,
  };
}

export function toggleHistory(state) {
  return {
    ...state,
    historyCollapsed: !state.historyCollapsed,
  };
}

export function toggleTrace(state) {
  return {
    ...state,
    traceCollapsed: !state.traceCollapsed,
  };
}
