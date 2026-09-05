export const DEFAULT_PANEL_WIDTHS = { history: 272, trace: 420 };
export const PANEL_LIMITS = { history: [200, 480], trace: [260, 720] };

export function normalizePanelWidths(value = {}) {
  return Object.fromEntries(Object.entries(DEFAULT_PANEL_WIDTHS).map(([key, fallback]) => {
    const [min, max] = PANEL_LIMITS[key];
    return [key, Number.isFinite(value?.[key]) ? Math.max(min, Math.min(max, value[key])) : fallback];
  }));
}

export function fitPanelWidths(value, layout, availableWidth) {
  const widths = normalizePanelWidths(value);
  let history = layout.historyCollapsed ? 68 : widths.history;
  let trace = layout.traceCollapsed ? 56 : widths.trace;
  const historyMin = layout.historyCollapsed ? 68 : PANEL_LIMITS.history[0];
  const traceMin = layout.traceCollapsed ? 56 : PANEL_LIMITS.trace[0];
  const excess = Math.max(0, history + trace + 320 - availableWidth);
  const flexible = history - historyMin + trace - traceMin;
  if (excess && flexible) {
    const ratio = Math.min(1, excess / flexible);
    history -= (history - historyMin) * ratio;
    trace -= (trace - traceMin) * ratio;
  }
  return { history: Math.round(history), trace: Math.round(trace) };
}
