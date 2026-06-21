export function activityRows(message) {
  if (Array.isArray(message.rows)) {
    return message.rows;
  }
  const rows = [];
  const thinking = Array.isArray(message.thinking) ? message.thinking : [];
  for (const item of thinking) {
    rows.push({ type: "thinking", content: item });
  }
  const toolCalls = Array.isArray(message.toolCalls) ? message.toolCalls : [];
  if (toolCalls.length > 0) {
    rows.push({ type: "tools", calls: toolCalls });
  }
  return rows;
}

export function activityHeaderText(message, rows, now) {
  if (message.status !== "running") {
    return message.title || "Processed";
  }

  const headerSource = message.headerSource || {};
  if (headerSource.type === "thinking" || headerSource.type === "description") {
    const text = String(headerSource.text || "").trim();
    if (text) {
      return text;
    }
  }

  const description = latestVisibleToolDescriptionFromRows(rows);
  if (description) {
    return description;
  }

  return `Thinking for ${formatProcessedDuration((now - (message.startedAt || now)) / 1000)}`;
}

export function activityFallbackDetailText(message) {
  if (message.status === "running") {
    return "";
  }
  return String(message.content || message.title || "Thinking").trim();
}

export function appendLiveThinkingContent(content, delta) {
  const base = String(content || "") === "Thinking" ? "" : String(content || "");
  return `${base}${delta}`;
}

export function activityLiveThinkingText(message) {
  return visibleThinkingText(message?.content === "Thinking" ? "" : message?.content);
}

export function thinkingHeaderSource(text) {
  const value = visibleThinkingText(text);
  return value ? { type: "thinking", text: value } : { type: "timer" };
}

export function toolHeaderSource(calls) {
  const description = latestVisibleToolDescription(calls);
  return description ? { type: "description", text: description } : { type: "timer" };
}

export function latestVisibleToolDescriptionFromRows(rows) {
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const row = rows[index];
    if (row?.type !== "tools") {
      continue;
    }
    const description = latestVisibleToolDescription(row.calls || []);
    if (description) {
      return description;
    }
    return "";
  }
  return "";
}

export function latestVisibleToolDescription(calls) {
  const safeCalls = Array.isArray(calls) ? calls : [];
  const runningIndex = findLastIndex(safeCalls, (call) => call.status === "running");
  if (runningIndex >= 0) {
    return String(safeCalls[runningIndex]?.description || "").trim();
  }
  for (let index = safeCalls.length - 1; index >= 0; index -= 1) {
    const description = String(safeCalls[index]?.description || "").trim();
    if (description) {
      return description;
    }
  }
  return "";
}

export function visibleThinkingText(text) {
  const value = String(text || "").trim();
  return isRawToolCallTranscript(value) ? "" : value;
}

export function formatProcessedDuration(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < 60) {
    return `${value}s`;
  }
  const minutes = Math.floor(value / 60);
  const remainder = value % 60;
  return remainder > 0 ? `${minutes}min${remainder}s` : `${minutes}min`;
}

function isRawToolCallTranscript(value) {
  return /^Tool call:\s*[^\n]*(?:\n[\s\S]*)?$/i.test(value);
}

function findLastIndex(items, predicate) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (predicate(items[index], index)) {
      return index;
    }
  }
  return -1;
}
