const DEFAULT_BASE_URL = "http://127.0.0.1:8765";

export function createApiClient(options = {}) {
  const baseUrl = normalizeBaseUrl(options.baseUrl || defaultBaseUrl());
  const fetchImpl = options.fetchImpl || globalThis.fetch?.bind(globalThis);
  if (typeof fetchImpl !== "function") {
    throw new Error("Fetch API is not available");
  }

  async function jsonRequest(path, { method = "GET", body, signal } = {}) {
    const response = await fetchImpl(`${baseUrl}${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
    if (!response.ok) {
      throw new Error(await responseErrorText(response));
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  return {
    baseUrl,
    getHealth: (options = {}) => jsonRequest("/health", options),
    getSettings: (options = {}) => jsonRequest("/settings", options),
    updateSettings: (settings, options = {}) =>
      jsonRequest("/settings", {
        ...options,
        method: "PATCH",
        body: settingsPayload(settings),
      }),
    listProjects: (options = {}) => jsonRequest("/projects", options),
    listSessions: (options = {}) => jsonRequest("/sessions", options),
    listSessionGroups: (options = {}) => jsonRequest("/sessions/groups", options),
    createSession: (request = {}, options = {}) =>
      jsonRequest("/sessions", {
        ...options,
        method: "POST",
        body: {
          case_id: request.caseId || "chat",
          mode: request.mode || "chat",
        },
      }),
    getSession: (sessionId, options = {}) => jsonRequest(`/sessions/${encodeURIComponent(sessionId)}`, options),
    deleteSession: (sessionId, options = {}) =>
      jsonRequest(`/sessions/${encodeURIComponent(sessionId)}`, { ...options, method: "DELETE" }),
    getTraceEvents: (sessionId, caseRunId, options = {}) =>
      jsonRequest(
        `/traces/${encodeURIComponent(sessionId)}/${encodeURIComponent(caseRunId)}`,
        options,
      ),
    streamChat: (request, handlers = {}) =>
      streamChatTurn({
        baseUrl,
        fetchImpl,
        request,
        onEvent: handlers.onEvent,
        signal: handlers.signal,
      }),
  };
}

export function settingsPayload(settings) {
  return compactObject({
    workspace_root: settingsString(settings.workspaceRoot),
    config_path: settingsString(settings.configPath),
    agent_alias: settingsString(settings.agent),
    model: settingsString(settings.model),
    max_steps: Number.isFinite(settings.maxSteps) ? settings.maxSteps : undefined,
    api_key: cleanString(settings.apiKey),
  });
}

export function parseSseEvents(text) {
  const events = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const event = parseSseBlock(block);
    if (event !== null) {
      events.push(event);
    }
  }
  return events;
}

async function streamChatTurn({ baseUrl, fetchImpl, request, onEvent, signal }) {
  const response = await fetchImpl(`${baseUrl}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: request.message,
      session_id: request.sessionId || null,
      case_id: request.caseId || "chat",
      turn_id: request.turnId || null,
    }),
    signal,
  });
  if (!response.ok) {
    throw new Error(await responseErrorText(response));
  }
  if (!response.body?.getReader) {
    const text = await response.text();
    for (const event of parseSseEvents(text)) {
      onEvent?.(event);
    }
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const nextBuffer = emitCompleteSseBlocks(buffer, onEvent);
    buffer = nextBuffer;
  }
  buffer += decoder.decode();
  emitCompleteSseBlocks(`${buffer}\n\n`, onEvent);
}

function emitCompleteSseBlocks(buffer, onEvent) {
  const blocks = buffer.split(/\r?\n\r?\n/);
  const pending = blocks.pop() || "";
  for (const block of blocks) {
    const event = parseSseBlock(block);
    if (event !== null) {
      onEvent?.(event);
    }
  }
  return pending;
}

function parseSseBlock(block) {
  const lines = block.split(/\r?\n/);
  let eventName = "message";
  const dataLines = [];
  for (const line of lines) {
    if (!line || line.startsWith(":")) {
      continue;
    }
    const index = line.indexOf(":");
    const field = index === -1 ? line : line.slice(0, index);
    const rawValue = index === -1 ? "" : line.slice(index + 1);
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;
    if (field === "event") {
      eventName = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  const dataText = dataLines.join("\n");
  return {
    event: eventName,
    data: JSON.parse(dataText),
  };
}

function defaultBaseUrl() {
  const windowBaseUrl = globalThis.window?.__LORA_API_BASE_URL__;
  if (typeof windowBaseUrl === "string" && windowBaseUrl.trim()) {
    return windowBaseUrl;
  }
  const viteBaseUrl = import.meta.env?.VITE_LORA_API_BASE_URL;
  return typeof viteBaseUrl === "string" && viteBaseUrl.trim() ? viteBaseUrl : DEFAULT_BASE_URL;
}

function normalizeBaseUrl(value) {
  return value.replace(/\/+$/, "");
}

function settingsValue(value) {
  return typeof value === "string" ? value.trim() : value;
}

function settingsString(value) {
  const clean = settingsValue(value);
  return typeof clean === "string" ? clean : undefined;
}

function cleanString(value) {
  const clean = settingsValue(value);
  return typeof clean === "string" && clean ? clean : undefined;
}

function compactObject(value) {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined));
}

async function responseErrorText(response) {
  const fallback = `Request failed with ${response.status}`;
  try {
    const text = await response.text();
    return text || fallback;
  } catch {
    return fallback;
  }
}
