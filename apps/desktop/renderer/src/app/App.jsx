import React, { useCallback, useEffect, useMemo, useState } from "react";

import { createApiClient } from "../shared/api/client.js";
import { createInitialLayoutState, toggleHistory, toggleTrace } from "./layoutState.js";

const EMPTY_SETTINGS = {
  workspace_root: "",
  lora_root: "",
  agent: "default",
  model: "",
  api_key_env: "DEEPSEEK_API_KEY",
  api_key_source: "missing",
  user_lora_root: "",
  base_url: "",
  max_steps: -1,
};

const TRACE_TABS = ["Events", "Tools", "Files", "Config"];

export function App() {
  const api = useMemo(() => createApiClient(), []);
  const [layout, setLayout] = useState(createInitialLayoutState);
  const [settings, setSettings] = useState(EMPTY_SETTINGS);
  const [projects, setProjects] = useState([]);
  const [sessionGroups, setSessionGroups] = useState([]);
  const [activeScopeId, setActiveScopeId] = useState("");
  const [activeSession, setActiveSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [traceEvents, setTraceEvents] = useState([]);
  const [liveEvents, setLiveEvents] = useState([]);
  const [status, setStatus] = useState("Loading");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const loadTrace = useCallback(
    async (session) => {
      if (!session?.last_case_run_id) {
        setTraceEvents([]);
        return;
      }
      const response = await api.getTraceEvents(session.session_id, session.last_case_run_id);
      setTraceEvents(response.events || []);
    },
    [api],
  );

  const loadSession = useCallback(
    async (sessionId) => {
      const detail = await api.getSession(sessionId);
      setActiveSession(detail.session);
      setMessages(historyToMessages(detail.history || []));
      setLiveEvents([]);
      await loadTrace(detail.session);
    },
    [api, loadTrace],
  );

  const refreshWorkbench = useCallback(
    async ({ selectSessionId, selectFirst = false, preserveSessionId = "" } = {}) => {
      setError("");
      const [nextSettings, nextProjects, nextSessionGroups] = await Promise.all([
        api.getSettings(),
        api.listProjects(),
        api.listSessionGroups(),
      ]);
      const groups = nextSessionGroups.groups || [];
      const sessionList = flattenSessionGroups(groups);
      setSettings(nextSettings);
      setProjects(nextProjects.projects || []);
      setSessionGroups(groups);
      setActiveScopeId(nextSessionGroups.active_scope_id || scopeIdFromWorkspace(nextSettings.workspace_root));
      const targetSessionId =
        selectSessionId ||
        (selectFirst ? sessionList[0]?.session_id : preserveSessionId);
      if (targetSessionId && sessionList.some((session) => session.session_id === targetSessionId)) {
        await loadSession(targetSessionId);
      } else {
        setActiveSession(null);
        setMessages([]);
        setTraceEvents([]);
        setLiveEvents([]);
      }
      setStatus("Ready");
    },
    [api, loadSession],
  );

  useEffect(() => {
    refreshWorkbench({ selectFirst: true }).catch((err) => {
      setStatus("Offline");
      setError(readableError(err));
    });
  }, [refreshWorkbench]);

  const handleCreateSession = useCallback(async () => {
    try {
      setError("");
      const session = await api.createSession({ caseId: "chat", mode: "chat" });
      await refreshWorkbench({ selectSessionId: session.session_id });
      setNotice("New chat created");
    } catch (err) {
      setError(readableError(err));
    }
  }, [api, refreshWorkbench]);

  const handleDeleteSession = useCallback(
    async (sessionId, scope) => {
      try {
        setError("");
        if (scope?.workspace_root && scope.workspace_root !== settings.workspace_root) {
          await api.updateSettings({ workspaceRoot: scope.workspace_root });
        }
        await api.deleteSession(sessionId);
        await refreshWorkbench({ selectFirst: true });
        setNotice("Session deleted");
      } catch (err) {
        setError(readableError(err));
      }
    },
    [api, refreshWorkbench, settings.workspace_root],
  );

  const handleSelectSession = useCallback(
    async (sessionId, scope) => {
      try {
        setError("");
        if (scope?.workspace_root && scope.workspace_root !== settings.workspace_root) {
          await api.updateSettings({ workspaceRoot: scope.workspace_root });
          await refreshWorkbench({ selectSessionId: sessionId });
          return;
        }
        await loadSession(sessionId);
      } catch (err) {
        setError(readableError(err));
      }
    },
    [api, loadSession, refreshWorkbench, settings.workspace_root],
  );

  const handleSendMessage = useCallback(
    async (message) => {
      if (!message.trim() || running) {
        return;
      }
      setRunning(true);
      setStatus("Running");
      setError("");
      setNotice("");
      setTraceEvents([]);
      setLiveEvents([]);
      const assistantId = `assistant-${Date.now()}`;
      let streamSessionId = activeSession?.session_id || null;
      let finalStatus = "Ready";
      setMessages((items) => [
        ...items,
        { id: `user-${Date.now()}`, role: "user", content: message },
        { id: assistantId, role: "assistant", content: "" },
      ]);

      try {
        await api.streamChat(
          { message, sessionId: streamSessionId, caseId: "chat" },
          {
            onEvent: ({ data }) => {
              const eventType = data.type || "";
              if (data.session_id && !streamSessionId) {
                streamSessionId = data.session_id;
              }
              setLiveEvents((items) => [...items, apiEventToTraceEvent(data)]);
              if (eventType === "assistant.delta") {
                appendAssistantDelta(setMessages, assistantId, String(data.payload?.delta || ""));
              } else if (eventType === "chat.completed") {
                finalStatus = String(data.payload?.status || "Done");
                const finalAnswer = data.payload?.final_answer;
                if (typeof finalAnswer === "string" && finalAnswer.trim()) {
                  replaceAssistantMessage(setMessages, assistantId, finalAnswer);
                }
              } else if (eventType === "chat.error") {
                finalStatus = "Error";
                replaceAssistantMessage(setMessages, assistantId, `Error: ${data.payload?.error || "chat failed"}`);
              }
            },
          },
        );
        if (streamSessionId) {
          await refreshWorkbench({ selectSessionId: streamSessionId });
        } else {
          await refreshWorkbench({ selectFirst: true });
        }
        setStatus(finalStatus);
      } catch (err) {
        setStatus("Error");
        setError(readableError(err));
        replaceAssistantMessage(setMessages, assistantId, `Error: ${readableError(err)}`);
      } finally {
        setRunning(false);
      }
    },
    [activeSession?.session_id, api, refreshWorkbench, running],
  );

  const handleSaveSettings = useCallback(
    async (draft) => {
      try {
        setError("");
        setStatus("Reloading");
        const nextSettings = await api.updateSettings(draft);
        setSettings(nextSettings);
        setSettingsOpen(false);
        setActiveSession(null);
        setMessages([]);
        setTraceEvents([]);
        setLiveEvents([]);
        await refreshWorkbench({ selectFirst: true });
        setNotice("Settings saved and runtime reloaded");
      } catch (err) {
        setStatus("Error");
        setError(readableError(err));
      }
    },
    [api, refreshWorkbench],
  );

  const appClassName = [
    "app-shell",
    layout.historyCollapsed ? "history-collapsed" : "",
    layout.traceCollapsed ? "trace-collapsed" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <main className={appClassName}>
      <SessionSidebar
        collapsed={layout.historyCollapsed}
        settings={settings}
        projects={projects}
        sessionGroups={sessionGroups}
        activeScopeId={activeScopeId}
        activeSessionId={activeSession?.session_id || ""}
        running={running}
        onCreateSession={handleCreateSession}
        onDeleteSession={handleDeleteSession}
        onSelectSession={handleSelectSession}
        onOpenSettings={() => setSettingsOpen(true)}
        onToggle={() => setLayout(toggleHistory)}
      />
      <Workbench
        activeSession={activeSession}
        messages={messages}
        settings={settings}
        status={status}
        running={running}
        traceCollapsed={layout.traceCollapsed}
        traceEvents={[...traceEvents, ...liveEvents]}
        onSendMessage={handleSendMessage}
        onToggleTrace={() => setLayout(toggleTrace)}
      />
      {(error || notice) && (
        <div className={error ? "toast error" : "toast"} role="status">
          {error || notice}
        </div>
      )}
      {settingsOpen && (
        <SettingsPanel
          settings={settings}
          disabled={running}
          onClose={() => setSettingsOpen(false)}
          onSave={handleSaveSettings}
        />
      )}
    </main>
  );
}

function SessionSidebar({
  collapsed,
  settings,
  projects,
  sessionGroups,
  activeScopeId,
  activeSessionId,
  running,
  onCreateSession,
  onDeleteSession,
  onSelectSession,
  onOpenSettings,
  onToggle,
}) {
  const [collapsedGroups, setCollapsedGroups] = useState({});

  function toggleGroup(scopeId) {
    setCollapsedGroups((current) => ({ ...current, [scopeId]: !current[scopeId] }));
  }

  return (
    <aside className="history" aria-label="Session history">
      <div className="history-shell">
        <div className="history-top">
          <div className="brand">
            <h1 className="brand-title">{collapsed ? "L" : "Lora"}</h1>
            <p className="brand-path" title={settings.workspace_root}>
              {shortPath(settings.workspace_root) || "No workspace"}
            </p>
          </div>
          <button
            className="icon-button rail-button"
            title={collapsed ? "Expand history" : "Collapse history"}
            type="button"
            onClick={onToggle}
          >
            <span className="rail-glyph chevron">{collapsed ? ">" : "<"}</span>
          </button>
        </div>

        <button className="primary-action" disabled={running} title="New chat" type="button" onClick={onCreateSession}>
          <span className="rail-glyph plus">+</span>
          <span className="action-label">New Chat</span>
        </button>

        <div className="section-label">Sessions</div>
        <div className="session-groups">
          {sessionGroups.length === 0 && <div className="empty-state">No chats yet</div>}
          {sessionGroups.map((group) => {
            const scope = group.scope || {};
            const isCollapsed = collapsedGroups[scope.scope_id] ?? group.collapsed;
            return (
              <section className="session-group" key={scope.scope_id || scope.label}>
                <button
                  className={scope.scope_id === activeScopeId ? "session-group-header active" : "session-group-header"}
                  title={scope.tooltip || scope.label}
                  type="button"
                  onClick={() => toggleGroup(scope.scope_id)}
                >
                  <span
                    aria-hidden="true"
                    className={isCollapsed ? "group-arrow collapsed" : "group-arrow"}
                  />
                  <span
                    aria-hidden="true"
                    className={scope.workspace_root ? "group-icon project" : "group-icon conversation"}
                  />
                  <span className="group-label">{scope.label || "Workspace"}</span>
                  <span className="group-count">{group.sessions?.length || 0}</span>
                </button>
                {!isCollapsed && (
                  <div className="session-list">
                    {(group.sessions || []).length === 0 && <div className="empty-state compact">No chats yet</div>}
                    {(group.sessions || []).map((session) => (
                      <SessionRow
                        active={session.session_id === activeSessionId}
                        key={session.session_id}
                        running={running}
                        scope={scope}
                        session={session}
                        onDeleteSession={onDeleteSession}
                        onSelectSession={onSelectSession}
                      />
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </div>

        <div className="history-bottom">
          <div className="runtime-card">
            <strong>{settings.agent || "default"}</strong>
            <span>{settings.model || "default model"}</span>
            <span>{projects.length ? `${projects.length} workspace` : "active workspace only"}</span>
          </div>
          <button className="plain-action" title="Switch workspace" type="button" onClick={onOpenSettings}>
            <span className="rail-glyph small">[]</span>
            <span className="plain-action-label">Choose Project</span>
          </button>
          <button className="plain-action" title="Settings" type="button" onClick={onOpenSettings}>
            <span className="rail-glyph small">*</span>
            <span className="plain-action-label">Settings</span>
          </button>
        </div>
      </div>
    </aside>
  );
}

function SessionRow({ active, running, scope, session, onDeleteSession, onSelectSession }) {
  return (
    <div
      className={active ? "session-row active" : "session-row"}
      role="button"
      tabIndex={0}
      onClick={() => onSelectSession(session.session_id, scope)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelectSession(session.session_id, scope);
        }
      }}
    >
      <div className="session-rail" />
      <div className="session-copy">
        <div className="session-title">{session.title}</div>
        <div className="session-meta">{sessionMeta(session)}</div>
      </div>
      <span className={`status-dot ${statusTone(session.last_case_run_status)}`} />
      <button
        className="session-delete"
        disabled={running}
        title="Delete session"
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onDeleteSession(session.session_id, scope);
        }}
      >
        x
      </button>
    </div>
  );
}

function Workbench({
  activeSession,
  messages,
  settings,
  status,
  running,
  traceCollapsed,
  traceEvents,
  onSendMessage,
  onToggleTrace,
}) {
  return (
    <section className="workbench" aria-label="Workbench">
      <ChatPane
        activeSession={activeSession}
        messages={messages}
        settings={settings}
        status={status}
        running={running}
        onSendMessage={onSendMessage}
      />
      <TracePanel
        collapsed={traceCollapsed}
        events={traceEvents}
        settings={settings}
        activeSession={activeSession}
        onToggle={onToggleTrace}
      />
    </section>
  );
}

function ChatPane({ activeSession, messages, settings, status, running, onSendMessage }) {
  const [draft, setDraft] = useState("");

  function submit() {
    const text = draft.trim();
    if (!text) {
      return;
    }
    setDraft("");
    onSendMessage(text);
  }

  return (
    <section className="chat" aria-label="Chat">
      <header className="chat-header">
        <div className="chat-title">
          <h2>{activeSession?.title || "Select or create a chat session"}</h2>
          <p>
            {settings.agent || "default"} / {settings.model || "default model"} / max_steps {settings.max_steps}
          </p>
        </div>
        <div className={`status-pill ${statusTone(status)}`}>{statusLabel(status)}</div>
      </header>

      <div className="transcript">
        {messages.length === 0 && (
          <div className="welcome">
            <h3>Lora Workbench</h3>
            <p>Start a chat, inspect runtime events, and tune the local runtime from Settings.</p>
          </div>
        )}
        {messages.map((message) => (
          <MessageRow key={message.id} message={message} />
        ))}
      </div>

      <footer className="composer">
        <div className="composer-box">
          <textarea
            disabled={running}
            placeholder={running ? "Lora is running..." : "Message Lora..."}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                submit();
              }
            }}
          />
          <button className="send" disabled={running || !draft.trim()} type="button" onClick={submit}>
            {running ? "Running" : "Send"}
          </button>
        </div>
      </footer>
    </section>
  );
}

function MessageRow({ message }) {
  if (message.role === "tool") {
    return (
      <div className="activity">
        <div className="activity-head">
          <span>{message.title || "Runtime activity"}</span>
          <span className="activity-state">{message.status || "Done"}</span>
        </div>
        <pre className="activity-detail">{message.content}</pre>
      </div>
    );
  }

  return (
    <article className={`message ${message.role}`}>
      {message.role !== "user" && <div className="avatar">L</div>}
      <div className="bubble">{message.content || (message.role === "assistant" ? "Thinking..." : "")}</div>
    </article>
  );
}

function TracePanel({ collapsed, events, settings, activeSession, onToggle }) {
  const [tab, setTab] = useState("Events");
  const visibleEvents = traceTabEvents(tab, events);

  return (
    <aside className="trace" aria-label="Trace inspector">
      <header className="trace-header">
        <div className="trace-title">
          <h3>Trace</h3>
          <p>
            Session: {shortId(activeSession?.session_id) || "none"} / Run:{" "}
            {shortId(activeSession?.last_case_run_id) || "ready"}
          </p>
        </div>
        <button
          className="icon-button trace-toggle"
          title={collapsed ? "Expand trace" : "Collapse trace"}
          type="button"
          onClick={onToggle}
        >
          <span className="rail-glyph chevron">{collapsed ? "<" : ">"}</span>
        </button>
        <div className="vertical-label">TRACE</div>
      </header>

      <nav className="trace-tabs" aria-label="Trace tabs">
        {TRACE_TABS.map((item) => (
          <button className={item === tab ? "tab active" : "tab"} key={item} type="button" onClick={() => setTab(item)}>
            {item}
          </button>
        ))}
      </nav>

      {tab === "Config" ? (
        <div className="config-list">
          {configRows(settings).map(([key, value]) => (
            <div className="config-row" key={key}>
              <strong>{key}</strong>
              <span>{String(value || "-")}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="trace-list">
          {visibleEvents.length === 0 && <div className="empty-state">No {tab.toLowerCase()} yet</div>}
          {visibleEvents.map((event, index) => (
            <div className="trace-event" key={`${event.type}-${event.id || index}`}>
              <div className={`status-dot ${eventTone(event)}`} />
              <div>
                <strong>{event.type || "event"}</strong>
                <span>{eventSummary(event)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}

function SettingsPanel({ settings, disabled, onClose, onSave }) {
  const [draft, setDraft] = useState(() => settingsToDraft(settings));

  useEffect(() => {
    setDraft(settingsToDraft(settings));
  }, [settings]);

  function setField(field, value) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  return (
    <div className="settings-backdrop" role="presentation">
      <section className="settings-panel" aria-label="Settings">
        <header className="settings-header">
          <div>
            <h2>Settings</h2>
            <p>Save credentials and reload the local runtime.</p>
          </div>
          <button className="icon-button" type="button" onClick={onClose}>
            x
          </button>
        </header>

        <div className="settings-form">
          <label>
            <span>Workspace</span>
            <input value={draft.workspaceRoot} onChange={(event) => setField("workspaceRoot", event.target.value)} />
          </label>
          <label>
            <span>Config path</span>
            <input value={draft.configPath} onChange={(event) => setField("configPath", event.target.value)} />
          </label>
          <label>
            <span>Agent</span>
            <input value={draft.agent} onChange={(event) => setField("agent", event.target.value)} />
          </label>
          <label>
            <span>Model</span>
            <input value={draft.model} onChange={(event) => setField("model", event.target.value)} />
          </label>
          <label>
            <span>Max steps</span>
            <input
              type="number"
              value={draft.maxSteps}
              onChange={(event) => setField("maxSteps", Number(event.target.value))}
            />
          </label>
          <div className="readonly-field">
            <span>API key env</span>
            <strong>{settings.api_key_env || "DEEPSEEK_API_KEY"}</strong>
          </div>
          <div className="readonly-field">
            <span>API key status</span>
            <strong>{settings.api_key_source === "missing" ? "Not configured" : `Configured (${settings.api_key_source})`}</strong>
          </div>
          <label>
            <span>API key</span>
            <input
              type="password"
              autoComplete="off"
              placeholder="Leave blank to keep current key"
              value={draft.apiKey}
              onChange={(event) => setField("apiKey", event.target.value)}
            />
          </label>
        </div>

        <footer className="settings-actions">
          <button className="plain-action" disabled={disabled} type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="send" disabled={disabled} type="button" onClick={() => onSave(draft)}>
            Save and Reload
          </button>
        </footer>
      </section>
    </div>
  );
}

function historyToMessages(history) {
  return history
    .map((message, index) => {
      const role = String(message.role || "assistant");
      const content = cleanContent(String(message.content || ""));
      if (!content) {
        return null;
      }
      if (role === "tool") {
        return {
          id: `history-tool-${index}`,
          role: "tool",
          title: toolTitle(message),
          content,
          status: "Done",
        };
      }
      return {
        id: `history-${index}`,
        role: role === "user" ? "user" : "assistant",
        content,
      };
    })
    .filter(Boolean);
}

function apiEventToTraceEvent(event) {
  return {
    id: `${event.type}-${Date.now()}-${Math.random()}`,
    type: event.type || "runtime.event",
    timestamp: "",
    actor: "runtime",
    payload: event.payload || {},
  };
}

function appendAssistantDelta(setMessages, assistantId, delta) {
  setMessages((items) =>
    items.map((item) => (item.id === assistantId ? { ...item, content: `${item.content}${delta}` } : item)),
  );
}

function replaceAssistantMessage(setMessages, assistantId, content) {
  setMessages((items) => items.map((item) => (item.id === assistantId ? { ...item, content } : item)));
}

function traceTabEvents(tab, events) {
  if (tab === "Tools") {
    return events.filter((event) => String(event.type || "").startsWith("tool."));
  }
  if (tab === "Files") {
    return events.filter((event) => {
      const type = String(event.type || "");
      return type.startsWith("file.") || type === "diff.created";
    });
  }
  return events;
}

function configRows(settings) {
  return [
    ["workspace", settings.workspace_root],
    ["lora_root", settings.lora_root],
    ["agent", settings.agent],
    ["model", settings.model],
    ["api_key_env", settings.api_key_env],
    ["api_key_source", settings.api_key_source],
    ["base_url", settings.base_url],
    ["max_steps", settings.max_steps],
    ["user_lora_root", settings.user_lora_root],
  ];
}

function flattenSessionGroups(groups) {
  return groups.flatMap((group) => group.sessions || []);
}

function scopeIdFromWorkspace(workspaceRoot) {
  return workspaceRoot ? `project:${workspaceRoot}` : "";
}

function settingsToDraft(settings) {
  return {
    workspaceRoot: settings.workspace_root || "",
    configPath: "",
    agent: settings.agent || "",
    model: settings.model || "",
    maxSteps: Number.isFinite(settings.max_steps) ? settings.max_steps : -1,
    apiKey: "",
  };
}

function cleanContent(content) {
  const match = content.match(/<user-message>([\s\S]*?)<\/user-message>/);
  return (match ? match[1] : content).trim();
}

function toolTitle(message) {
  return message.name || message.tool_name || message.tool_call_id || "Tool result";
}

function eventSummary(event) {
  const payload = event.payload || {};
  if (payload.delta) {
    return payload.delta;
  }
  if (payload.error) {
    return payload.error;
  }
  if (payload.content) {
    return String(payload.content).slice(0, 180);
  }
  return JSON.stringify(payload).slice(0, 180);
}

function eventTone(event) {
  const payload = event.payload || {};
  if (payload.error || payload.status === "error") {
    return "error";
  }
  if (String(event.type || "").includes("warning")) {
    return "warning";
  }
  return "success";
}

function statusTone(status) {
  const value = String(status || "").toLowerCase();
  if (value.includes("run") || value.includes("load")) {
    return "warning";
  }
  if (value.includes("error") || value.includes("fail")) {
    return "error";
  }
  return "success";
}

function statusLabel(status) {
  const value = String(status || "Ready");
  if (value === "passed") {
    return "Done";
  }
  return value;
}

function sessionMeta(session) {
  const stamp = session.updated_at || session.created_at || "";
  const status = session.last_case_run_status || "ready";
  return `${stamp.slice(0, 19)} / ${status}`;
}

function shortPath(path) {
  if (!path) {
    return "";
  }
  return path.length <= 36 ? path : `...${path.slice(-33)}`;
}

function shortId(value) {
  if (!value) {
    return "";
  }
  return value.length <= 16 ? value : `${value.slice(0, 8)}...${value.slice(-6)}`;
}

function readableError(err) {
  return err instanceof Error ? err.message : String(err);
}
