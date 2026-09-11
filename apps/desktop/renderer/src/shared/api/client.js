const DEFAULT_BASE_URL = "http://127.0.0.1:8765";
const STREAM_RESUME_TIMEOUT_MS = 55_000;
const STREAM_RESUME_RETRY_DELAY_MS = 1_000;

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
      const error = new Error(await responseErrorText(response));
      error.status = response.status;
      throw error;
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
    listAutomations: (status = "", options = {}) =>
      jsonRequest(`/automations${status ? `?status=${encodeURIComponent(status)}` : ""}`, options),
    createAutomation: (request, options = {}) => jsonRequest("/automations", {
      ...options, method: "POST", body: request,
    }),
    updateAutomation: (automationId, request, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}`, {
        ...options, method: "PATCH", body: request,
      }),
    deleteAutomation: (automationId, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}`, {
        ...options, method: "DELETE",
      }),
    pauseAutomation: (automationId, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}/pause`, {
        ...options, method: "POST",
      }),
    resumeAutomation: (automationId, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}/resume`, {
        ...options, method: "POST",
      }),
    runAutomation: (automationId, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}/run`, {
        ...options, method: "POST",
      }),
    listAutomationRuns: (automationId, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}/runs`, options),
    removeProject: (scopeId, options = {}) =>
      jsonRequest(`/projects?scope_id=${encodeURIComponent(scopeId)}`, {
        ...options,
        method: "DELETE",
      }),
    listWorkspaceEntries: (scopeId, path = "", options = {}) =>
      jsonRequest(`/workspace/entries?scope_id=${encodeURIComponent(scopeId)}&path=${encodeURIComponent(path)}`, options),
    readWorkspaceFile: (scopeId, path, options = {}) =>
      jsonRequest(`/workspace/file?scope_id=${encodeURIComponent(scopeId)}&path=${encodeURIComponent(path)}`, options),
    executeTerminalCommand: (scopeId, command, options = {}) =>
      jsonRequest("/terminal/execute", {
        ...options,
        method: "POST",
        body: { scope_id: scopeId, command },
      }),
    resetTerminal: (scopeId, options = {}) =>
      jsonRequest("/terminal/reset", {
        ...options,
        method: "POST",
        body: { scope_id: scopeId },
      }),
    listSessions: (options = {}) => jsonRequest("/sessions", options),
    listSessionGroups: (options = {}) => jsonRequest("/sessions/groups", options),
    createSession: (request = {}, options = {}) =>
      jsonRequest("/sessions", {
        ...options,
        method: "POST",
        body: {
          case_id: request.caseId || "chat",
          mode: request.mode || "chat",
          scope_id: request.scopeId || undefined,
        },
      }),
    getSession: (sessionId, { scopeId, ...options } = {}) =>
      jsonRequest(`/sessions/${encodeURIComponent(sessionId)}${scopeQuery(scopeId)}`, options),
    deleteSession: (sessionId, { scopeId, ...options } = {}) =>
      jsonRequest(`/sessions/${encodeURIComponent(sessionId)}${scopeQuery(scopeId)}`, {
        ...options,
        method: "DELETE",
      }),
    getTraceEvents: (sessionId, caseRunId, options = {}) =>
      jsonRequest(
        `/traces/${encodeURIComponent(sessionId)}/${encodeURIComponent(caseRunId)}`,
        options,
      ),
    getToolResult: (toolCallId, options = {}) =>
      jsonRequest(`/tool-results/${encodeURIComponent(toolCallId)}`, options),
    deliverApproval: (approvalId, approved, comment = "", options = {}) =>
      jsonRequest(`/chat/approvals/${encodeURIComponent(approvalId)}`, {
        ...options,
        method: "POST",
        body: { approved, comment },
      }),
    streamChat: (request, handlers = {}) =>
      streamChatTurn({
        baseUrl,
        fetchImpl,
        request,
        onEvent: handlers.onEvent,
        onConnectionState: handlers.onConnectionState,
        signal: handlers.signal,
      }),
  };
}

export function settingsPayload(settings) {
  const contextWindow = settingsNumber(settings.contextWindow);
  return compactObject({
    workspace_root: settingsString(settings.workspaceRoot),
    agent_alias: settingsString(settings.agent),
    max_steps: Number.isFinite(settings.maxSteps) ? settings.maxSteps : undefined,
    context_window: Object.hasOwn(settings, "contextWindow") ? contextWindow ?? null : undefined,
    api_key: cleanString(settings.apiKey),
    approvals_enabled: typeof settings.approvalsEnabled === "boolean" ? settings.approvalsEnabled : undefined,
    model_group: modelGroupPayload(settings),
  });
}

function modelGroupPayload(settings) {
  if (!Array.isArray(settings.modelRoutes)) {
    return undefined;
  }
  return {
    profile: settingsString(settings.profile) || "default",
    routes: settings.modelRoutes.map((route) => compactObject({
      id: settingsString(route.id),
      provider: settingsString(route.provider),
      model_name: settingsString(route.model_name),
      base_url: settingsString(route.base_url),
      api_key_env: settingsString(route.api_key_env),
      api_key: cleanString(route.api_key),
    })),
    fallback: Array.isArray(settings.fallback) ? settings.fallback.map(settingsString) : [],
    retry: {
      max_attempts_per_route: settingsNumber(settings.retry?.max_attempts_per_route),
      attempt_idle_timeout_seconds: settingsNumber(settings.retry?.attempt_idle_timeout_seconds),
      backoff_initial: settingsNonNegativeNumber(settings.retry?.backoff_initial),
      backoff_maximum: settingsNonNegativeNumber(settings.retry?.backoff_maximum),
      backoff_multiplier: settingsNumber(settings.retry?.backoff_multiplier),
    },
  };
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

async function streamChatTurn({ baseUrl, fetchImpl, request, onEvent, onConnectionState, signal }) {
  let executionId = request.executionId || null;
  let afterSequence = Number.isFinite(request.afterSequence) ? request.afterSequence : null;
  let disconnectedAt = null;
  let terminal = false;
  let attempt = 0;

  while (true) {
    try {
      await streamChatAttempt({
        baseUrl,
        fetchImpl,
        request: { ...request, executionId, afterSequence },
        onEvent: (event) => {
          const data = event?.data || {};
          disconnectedAt = null;
          attempt = 0;
          onConnectionState?.("connected");
          if (data.execution_id) {
            executionId = data.execution_id;
          }
          if (Number.isFinite(data.sequence)) {
            afterSequence = Math.max(afterSequence ?? -1, data.sequence);
          }
          terminal ||= ["execution.completed", "execution.failed", "execution.cancelled", "execution.deadline_exceeded", "lora.transport.error"].includes(data.kind);
          emitStreamEvent(event, onEvent);
        },
        signal,
      });
      if (terminal) return;
      throw new Error("Execution stream ended before the task finished");
    } catch (err) {
      if (terminal) return;
      if (signal?.aborted || isAbortError(err) || !executionId) {
        throw err;
      }
      disconnectedAt ??= Date.now();
      if (Date.now() - disconnectedAt >= STREAM_RESUME_TIMEOUT_MS) {
        throw err;
      }
      onConnectionState?.("reconnecting");
      attempt += 1;
      await delay(Math.min(STREAM_RESUME_RETRY_DELAY_MS * attempt, 5_000), signal);
    }
  }
}

async function streamChatAttempt({ baseUrl, fetchImpl, request, onEvent, signal }) {
  const response = await fetchImpl(`${baseUrl}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: request.executionId ? null : request.message,
      session_id: request.sessionId || null,
      scope_id: request.scopeId || null,
      case_id: request.caseId || "chat",
      turn_id: request.turnId || null,
      execution_id: request.executionId || null,
      after_sequence: Number.isFinite(request.afterSequence) ? request.afterSequence : null,
    }),
    signal,
  });
  if (!response.ok) {
    throw new Error(await responseErrorText(response));
  }
  if (!response.body?.getReader) {
    const text = await response.text();
    for (const event of parseSseEvents(text)) {
      emitStreamEvent(event, onEvent);
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

function delay(ms, signal) {
  if (signal?.aborted) {
    return Promise.reject(abortError());
  }
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timeout);
        reject(abortError());
      },
      { once: true },
    );
  });
}

function isAbortError(err) {
  return err instanceof DOMException && err.name === "AbortError";
}

function abortError() {
  return new DOMException("The operation was aborted.", "AbortError");
}

function emitCompleteSseBlocks(buffer, onEvent) {
  const blocks = buffer.split(/\r?\n\r?\n/);
  const pending = blocks.pop() || "";
  for (const block of blocks) {
    const event = parseSseBlock(block);
    if (event !== null) {
      emitStreamEvent(event, onEvent);
    }
  }
  return pending;
}

function emitStreamEvent(event, onEvent) {
  if (!onEvent) {
    return;
  }
  try {
    onEvent(event);
  } catch (err) {
    console.error("SSE event handler failed", err, event);
  }
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

function scopeQuery(scopeId) {
  return scopeId ? `?scope_id=${encodeURIComponent(scopeId)}` : "";
}

function settingsValue(value) {
  return typeof value === "string" ? value.trim() : value;
}

function settingsString(value) {
  const clean = settingsValue(value);
  return typeof clean === "string" ? clean : undefined;
}

function settingsNumber(value) {
  if (value === null || value === undefined || value === "") {
    return undefined;
  }
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number > 0 ? number : undefined;
}

function settingsNonNegativeNumber(value) {
  if (value === null || value === undefined || value === "") {
    return undefined;
  }
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number >= 0 ? number : undefined;
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
