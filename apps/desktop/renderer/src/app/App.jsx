import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  FolderCode,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  SendHorizontal,
  Settings as SettingsIcon,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";

import { createApiClient } from "../shared/api/client.js";
import {
  activityHeaderText,
  activityRows,
  formatProcessedDuration,
  thinkingHeaderSource,
  toolHeaderSource,
} from "./activityModel.js";
import { createInitialLayoutState, toggleHistory, toggleTrace } from "./layoutState.js";
import { parseInlineMarkdown, parseMarkdownBlocks } from "./markdown.js";

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
  context_window: null,
};

const TRACE_TABS = ["Events", "Tools", "Files", "Config"];
const TOOL_ARGUMENT_PREVIEW_LIMIT = 4_000;
const TOOL_RESULT_PREVIEW_LIMIT = 6_000;

export function App() {
  const api = useMemo(() => createApiClient(), []);
  const [layout, setLayout] = useState(createInitialLayoutState);
  const [settings, setSettings] = useState(EMPTY_SETTINGS);
  const [projects, setProjects] = useState([]);
  const [sessionGroups, setSessionGroups] = useState([]);
  const [activeScopeId, setActiveScopeId] = useState("");
  const [activeSession, setActiveSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [activityCollapseToken, setActivityCollapseToken] = useState(0);
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
      setActivityCollapseToken((value) => value + 1);
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
      const activityId = `activity-${Date.now()}`;
      let streamSessionId = activeSession?.session_id || null;
      let finalStatus = "Ready";
      setMessages((items) => [
        ...items,
        { id: `user-${Date.now()}`, role: "user", content: message },
        {
          id: activityId,
          role: "activity",
          title: "Thinking",
          content: "Thinking",
          status: "running",
          startedAt: Date.now(),
        },
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
                appendAssistantDelta(setMessages, activityId, assistantId, String(data.payload?.delta || ""));
              } else if (eventType === "runtime.message") {
                appendRuntimeActivity(setMessages, activityId, assistantId, data);
              } else if (eventType === "chat.completed") {
                finalStatus = String(data.payload?.status || "Done");
                finalizeRuntimeActivity(setMessages, activityId, finalStatus);
                setActivityCollapseToken((value) => value + 1);
                const finalAnswer = data.payload?.final_answer;
                if (typeof finalAnswer === "string" && finalAnswer.trim()) {
                  replaceAssistantMessage(setMessages, assistantId, finalAnswer);
                }
              } else if (eventType === "chat.error") {
                finalStatus = "Error";
                finalizeRuntimeActivity(setMessages, activityId, finalStatus);
                setActivityCollapseToken((value) => value + 1);
                replaceAssistantMessage(setMessages, assistantId, `Error: ${data.payload?.error || "chat failed"}`);
              } else if (eventType === "chat.cancelled") {
                finalStatus = "Skipped";
                finalizeRuntimeActivity(setMessages, activityId, finalStatus);
                setActivityCollapseToken((value) => value + 1);
                replaceAssistantMessage(
                  setMessages,
                  assistantId,
                  data.payload?.reason || "Chat cancelled after reconnect timeout.",
                );
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
        finalizeRuntimeActivity(setMessages, activityId, "Error");
        setActivityCollapseToken((value) => value + 1);
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
        activityCollapseToken={activityCollapseToken}
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
  const [acknowledgedStatuses, setAcknowledgedStatuses] = useState({});

  function toggleGroup(scopeId) {
    setCollapsedGroups((current) => ({ ...current, [scopeId]: !current[scopeId] }));
  }

  function acknowledgeSessionStatus(session) {
    const statusKind = sessionStatusKind(session.last_case_run_status);
    if (statusKind === "running") {
      return;
    }
    setAcknowledgedStatuses((current) => ({
      ...current,
      [session.session_id]: sessionStatusIdentity(session),
    }));
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
            {collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
          </button>
        </div>

        <button className="primary-action" disabled={running} title="New chat" type="button" onClick={onCreateSession}>
          <MessageSquarePlus aria-hidden="true" />
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
                  {scope.workspace_root && <FolderCode aria-hidden="true" />}
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
                          scope={scope}
                          session={session}
                        statusAcknowledged={acknowledgedStatuses[session.session_id] === sessionStatusIdentity(session)}
                        onAcknowledgeStatus={acknowledgeSessionStatus}
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
            <SlidersHorizontal aria-hidden="true" />
            <span className="plain-action-label">Choose Project</span>
          </button>
          <button className="plain-action" title="Settings" type="button" onClick={onOpenSettings}>
            <SettingsIcon aria-hidden="true" />
            <span className="plain-action-label">Settings</span>
          </button>
        </div>
      </div>
    </aside>
  );
}

function SessionRow({
  active,
  scope,
  session,
  statusAcknowledged,
  onAcknowledgeStatus,
  onDeleteSession,
  onSelectSession,
}) {
  const title = cleanSessionTitle(session.title) || session.session_id || "Untitled chat";
  const statusKind = sessionStatusKind(session.last_case_run_status);
  const showStatus = statusKind === "running" || !statusAcknowledged;
  const canDelete = statusKind !== "running";

  function selectSession() {
    onAcknowledgeStatus(session);
    onSelectSession(session.session_id, scope);
  }

  return (
    <div
      className={active ? "session-row active" : "session-row"}
      role="button"
      tabIndex={0}
      onClick={selectSession}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectSession();
        }
      }}
    >
      <div className="session-rail" />
      <div className="session-copy">
        <div className="session-title" title={title}>
          {title}
        </div>
      </div>
      <div className="session-row-actions">
        {showStatus && <span className={`session-indicator ${statusKind}`} aria-label={sessionStatusLabel(statusKind)} />}
        {canDelete && (
          <button
            className="session-delete"
            title="Delete session"
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onDeleteSession(session.session_id, scope);
            }}
          >
            <Trash2 aria-hidden="true" />
          </button>
        )}
      </div>
    </div>
  );
}

function Workbench({
  activeSession,
  messages,
  activityCollapseToken,
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
        activityCollapseToken={activityCollapseToken}
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

function ChatPane({ activeSession, messages, activityCollapseToken, settings, status, running, onSendMessage }) {
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
            {" / "}
            context {formatContextWindow(settings.context_window)}
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
          <MessageRow key={message.id} message={message} activityCollapseToken={activityCollapseToken} />
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
            <SendHorizontal aria-hidden="true" />
            {running ? "Running" : "Send"}
          </button>
        </div>
      </footer>
    </section>
  );
}

function MessageRow({ message, activityCollapseToken }) {
  if (message.role === "activity") {
    return <ActivityMessage message={message} collapseToken={activityCollapseToken} />;
  }

  if (message.role === "assistant" && !message.content) {
    return null;
  }

  return (
    <article className={`message ${message.role}`}>
      {message.role !== "user" && <div className="avatar">L</div>}
      <div className="bubble">
        {message.role === "assistant" ? <MarkdownContent content={message.content} /> : message.content}
      </div>
    </article>
  );
}

function MarkdownContent({ content }) {
  const blocks = useMemo(() => parseMarkdownBlocks(content), [content]);

  return (
    <div className="markdown-content">
      {blocks.map((block, index) => (
        <MarkdownBlock key={index} block={block} />
      ))}
    </div>
  );
}

function MarkdownBlock({ block }) {
  if (block.type === "heading") {
    const HeadingTag = `h${block.level}`;
    return <HeadingTag>{renderInlineMarkdown(block.text)}</HeadingTag>;
  }

  if (block.type === "code") {
    return (
      <pre className="markdown-code">
        {block.language && <span className="markdown-code-language">{block.language}</span>}
        <code>{block.text}</code>
      </pre>
    );
  }

  if (block.type === "quote") {
    return <blockquote>{renderInlineMarkdown(block.text)}</blockquote>;
  }

  if (block.type === "list") {
    const ListTag = block.ordered ? "ol" : "ul";
    return (
      <ListTag>
        {block.items.map((item, index) => (
          <li key={index}>{renderInlineMarkdown(item)}</li>
        ))}
      </ListTag>
    );
  }

  if (block.type === "table") {
    return (
      <div className="markdown-table-wrap">
        <table>
          <thead>
            <tr>
              {block.header.map((cell, index) => (
                <th key={index} style={tableCellStyle(block.alignments[index])}>
                  {renderInlineMarkdown(cell)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} style={tableCellStyle(block.alignments[cellIndex])}>
                    {renderInlineMarkdown(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (block.type === "rule") {
    return <hr />;
  }

  return <p>{renderInlineMarkdown(block.text)}</p>;
}

function tableCellStyle(alignment) {
  return alignment ? { textAlign: alignment } : undefined;
}

function renderInlineMarkdown(text) {
  return parseInlineMarkdown(text).map((segment, index) => renderInlineSegment(segment, index));
}

function renderInlineSegment(segment, key) {
  if (segment.type === "strong") {
    return <strong key={key}>{segment.children.map((child, index) => renderInlineSegment(child, index))}</strong>;
  }
  if (segment.type === "em") {
    return <em key={key}>{segment.children.map((child, index) => renderInlineSegment(child, index))}</em>;
  }
  if (segment.type === "code") {
    return <code key={key}>{segment.text}</code>;
  }
  if (segment.type === "link") {
    return (
      <a key={key} href={segment.href} target="_blank" rel="noreferrer">
        {segment.children.map((child, index) => renderInlineSegment(child, index))}
      </a>
    );
  }
  return <React.Fragment key={key}>{segment.text}</React.Fragment>;
}

function ActivityMessage({ message, collapseToken }) {
  const isRunning = message.status === "running";
  const [expanded, setExpanded] = useState(isRunning);
  const [now, setNow] = useState(Date.now());
  const content = String(message.content || "");
  const rows = activityRows(message);
  const header = activityHeaderText(message, rows, now);

  useEffect(() => {
    setExpanded(message.status === "running");
  }, [collapseToken, message.id, message.status]);

  useEffect(() => {
    if (!isRunning) {
      return undefined;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [isRunning]);

  return (
    <div className={expanded ? "activity expanded" : "activity"}>
      <button
        className="activity-head"
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="activity-title">
          <span>{header}</span>
          <span className="activity-toggle">{expanded ? "v" : ">"}</span>
        </span>
      </button>
      <div className="activity-divider" aria-hidden="true" />
      {expanded && (
        <div className="activity-detail">
          {rows.map((row, index) =>
            row.type === "tools" ? (
              <ToolGroup calls={row.calls || []} key={`tools-${index}`} />
            ) : (
              <pre className="activity-block" key={`thinking-${index}`}>
                {row.content}
              </pre>
            ),
          )}
          {!rows.length && <pre className="activity-block">{content || message.title || "Thinking"}</pre>}
        </div>
      )}
    </div>
  );
}

function ToolGroup({ calls }) {
  const safeCalls = Array.isArray(calls) ? calls : [];
  return (
    <div className="tool-group">
      {safeCalls.map((call, index) => (
        <ToolCallRow call={call} key={call.id || `${call.name}-${index}`} />
      ))}
    </div>
  );
}

function ToolCallRow({ call }) {
  const [expanded, setExpanded] = useState(false);
  const hasResult = Boolean(call.result);
  const tone = activityTone(call.status);
  return (
    <div className="tool-call-row">
      <button
        className="tool-call-toggle"
        type="button"
        disabled={!hasResult}
        aria-expanded={hasResult ? expanded : undefined}
        onClick={() => {
          if (hasResult) {
            setExpanded((value) => !value);
          }
        }}
      >
        <span className="tool-call-name">{toolActionLabel(call.name)}</span>
        <span className={`tool-call-status ${tone}`}>{toolStatusLabel(call.status)}</span>
      </button>
      {expanded && hasResult && <pre className="tool-call-result">{call.result}</pre>}
    </div>
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
          {collapsed ? <PanelRightOpen aria-hidden="true" /> : <PanelRightClose aria-hidden="true" />}
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
                <strong>{eventTitle(event)}</strong>
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
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close settings" title="Close settings">
            <X aria-hidden="true" />
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
          <label>
            <span>Context window</span>
            <input
              min="1"
              placeholder="Unset"
              type="number"
              value={draft.contextWindow}
              onChange={(event) => setField("contextWindow", event.target.value)}
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
  const rendered = [];
  let index = 0;
  while (index < history.length) {
    const message = history[index] || {};
    const role = String(message.role || "assistant");
    if (role !== "user") {
      rendered.push(...renderTurnSegment([message], `history-${index}`));
      index += 1;
      continue;
    }
    const content = cleanContent(String(message.content || ""));
    if (content) {
      rendered.push({ id: `history-${index}`, role: "user", content });
    }
    const segment = [];
    index += 1;
    while (index < history.length && String(history[index]?.role || "") !== "user") {
      segment.push(history[index]);
      index += 1;
    }
    rendered.push(...renderTurnSegment(segment, `history-${index}`));
  }
  return rendered.filter(Boolean);
}

function renderTurnSegment(segment, idPrefix) {
  let finalAssistantIndex = -1;
  segment.forEach((message, index) => {
    if (String(message?.role || "") === "assistant" && cleanContent(String(message?.content || "")).trim()) {
      finalAssistantIndex = index;
    }
  });

  let rows = [];
  let activityToneValue = "success";
  const rendered = [];
  segment.forEach((message, index) => {
    const role = String(message?.role || "");
    const content = cleanContent(String(message?.content || ""));
    const toolCalls = toolCallsFromMessage(message);
    if (role === "assistant") {
      if (index === finalAssistantIndex && content) {
        if (toolCalls.length > 0) {
          rows = appendToolCallsToRows(rows, toolCalls.map(toolCallState));
        }
        rendered.push({ id: `${idPrefix}-assistant-${index}`, role: "assistant", content });
        return;
      }
      if (content) {
        rows = [...rows, { type: "thinking", content }];
      }
      rows = appendToolCallsToRows(rows, toolCalls.map(toolCallState));
      return;
    }
    if (role === "tool") {
      const activity = toolResultActivity(message);
      rows = applyToolResultToRows(rows, activity);
      if (activity.status === "error") {
        activityToneValue = "error";
      }
    }
  });
  if (rows.length > 0) {
    rendered.unshift({
      id: `${idPrefix}-activity`,
      role: "activity",
      title: "Processed for 0s",
      content: activityContentFromRows(rows),
      rows,
      status: activityToneValue,
    });
  }
  return rendered;
}

function apiEventToTraceEvent(event) {
  return {
    id: `${event.type}-${Date.now()}-${Math.random()}`,
    type: event.type || "runtime.event",
    timestamp: "",
    actor: "runtime",
    payload: tracePreviewPayload(event.payload || {}),
  };
}

function tracePreviewPayload(payload) {
  if (!payload || typeof payload !== "object") {
    return {};
  }
  const preview = { ...payload };
  if (typeof preview.content === "string") {
    preview.content = limitText(preview.content, TOOL_RESULT_PREVIEW_LIMIT);
  }
  if (preview.payload && typeof preview.payload === "object") {
    preview.payload = { ...preview.payload };
    if (typeof preview.payload.content === "string") {
      preview.payload.content = limitText(preview.payload.content, TOOL_RESULT_PREVIEW_LIMIT);
    }
  }
  return preview;
}

function appendAssistantDelta(setMessages, activityId, assistantId, delta) {
  setMessages((items) =>
    items.map((item) => {
      if (item.id === activityId) {
        const content = `${item.content === "Thinking" ? "" : item.content || ""}${delta}`;
        return { ...item, content, headerSource: thinkingHeaderSource(content) };
      }
      if (item.id === assistantId) {
        return { ...item, content: `${item.content}${delta}` };
      }
      return item;
    }),
  );
}

function appendRuntimeActivity(setMessages, activityId, assistantId, event) {
  const update = runtimeActivityUpdateFromEvent(event);
  if (!update.thinking.length && !update.calls.length && !update.results.length) {
    return;
  }
  setMessages((items) =>
    items.map((item) => {
      if (item.id === assistantId && event.payload?.role === "assistant") {
        return { ...item, content: "" };
      }
      if (item.id !== activityId) {
        return item;
      }
      const assistantThinking = items.find((candidate) => candidate.id === assistantId)?.content || "";
      const thinkingDetail =
        event.payload?.role === "assistant" && assistantThinking.trim() ? [`Thinking\n${assistantThinking}`] : [];
      let rows = activityRows(item);
      for (const value of [...thinkingDetail.map((item) => item.replace(/^Thinking\n/, "")), ...update.thinking]) {
        if (value.trim()) {
          rows = [...rows, { type: "thinking", content: value }];
        }
      }
      rows = appendToolCallsToRows(rows, update.calls);
      for (const result of update.results) {
        rows = applyToolResultToRows(rows, result);
      }
      const flatToolCalls = rows.flatMap((row) => (row.type === "tools" ? row.calls || [] : []));
      const content = activityContentFromRows(rows) || item.content;
      const hasError = flatToolCalls.some((call) => call.status === "error") || item.status === "error";
      const latestToolGroup = [...rows].reverse().find((row) => row.type === "tools" && row.calls?.length);
      const nextTitle = latestToolGroup ? activityToolBatchTitle(latestToolGroup.calls || []) : item.title;
      const headerSource =
        update.calls.length > 0
          ? toolHeaderSource(update.calls)
          : thinkingDetail.length || update.thinking.length
            ? thinkingHeaderSource([...thinkingDetail, ...update.thinking].at(-1))
            : item.headerSource;
      return {
        ...item,
        title: nextTitle || item.title,
        content,
        headerSource,
        rows,
        status: hasError ? "error" : "running",
      };
    }),
  );
}

function finalizeRuntimeActivity(setMessages, activityId, status) {
  setMessages((items) =>
    items.map((item) => {
      if (item.id !== activityId) {
        return item;
      }
      return {
        ...item,
        title: `Processed for ${formatProcessedDuration((Date.now() - (item.startedAt || Date.now())) / 1000)}`,
        status: activityTone(status) === "error" ? "error" : "success",
      };
    }),
  );
}

function replaceAssistantMessage(setMessages, assistantId, content) {
  setMessages((items) => items.map((item) => (item.id === assistantId ? { ...item, content } : item)));
}

function traceTabEvents(tab, events) {
  if (tab === "Tools") {
    return traceToolEvents(events);
  }
  if (tab === "Files") {
    return events.filter((event) => {
      const type = String(event.type || "");
      return type.startsWith("file.") || type === "diff.created";
    });
  }
  return events;
}

function traceToolEvents(events) {
  const items = [];
  const byCallId = new Map();

  function upsert(callId, patch) {
    const key = callId || `tool-${items.length}`;
    let item = callId ? byCallId.get(callId) : null;
    if (!item) {
      item = {
        id: key,
        type: "tool.call",
        payload: {
          tool_call_id: callId,
          tool_name: callId || "tool",
          trace_tool: true,
          has_call: false,
          has_result: false,
          status: "running",
        },
      };
      items.push(item);
      if (callId) {
        byCallId.set(callId, item);
      }
    }
    item.payload = { ...item.payload, ...patch };
    return item;
  }

  for (const event of events) {
    const type = String(event.type || "");
    const payload = event.payload || {};

    if (type === "tool.call") {
      const callId = String(payload.tool_call_id || payload.id || event.id || "");
      upsert(callId, {
        tool_call_id: callId,
        tool_name: String(payload.tool_name || payload.name || "tool"),
        arguments: stringifyToolArgs(payload.args),
        call_event: event,
        has_call: true,
        status: "running",
      });
      continue;
    }

    if (type === "tool.result") {
      const callId = String(payload.tool_call_id || "");
      const status = String(payload.status || "success") === "error" || payload.error ? "error" : "success";
      upsert(callId, {
        tool_call_id: callId,
        result_event: event,
        has_result: true,
        result: stringifyDetail(payload.error || payload.result || payload.content || ""),
        status,
      });
      continue;
    }

    if (type === "runtime.message") {
      if (payload.role === "assistant") {
        for (const call of toolCallsFromMessage(payload)) {
          const state = toolCallState(call);
          upsert(state.id, {
            tool_call_id: state.id,
            tool_name: state.name,
            arguments: state.arguments,
            call_event: event,
            has_call: true,
            status: "running",
          });
        }
      } else if (payload.role === "tool") {
        const result = toolResultActivity(payload);
        if (result.toolCallId) {
          upsert(result.toolCallId, {
            tool_call_id: result.toolCallId,
            result_event: event,
            has_result: true,
            result: result.content,
            status: result.status === "error" ? "error" : "success",
          });
        }
      }
    }
  }

  return items;
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
    ["context_window", settings.context_window],
    ["compression_trigger", compressionTriggerLabel(settings.context_window)],
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
    contextWindow: Number.isFinite(settings.context_window) ? String(settings.context_window) : "",
    apiKey: "",
  };
}

function formatContextWindow(value) {
  return Number.isFinite(value) ? `${value} tokens` : "unset";
}

function compressionTriggerLabel(contextWindow) {
  return Number.isFinite(contextWindow) ? `${Math.floor(contextWindow * 0.9)} tokens` : "disabled";
}

function cleanContent(content) {
  const match = content.match(/<user-message>([\s\S]*?)<\/user-message>/);
  return (match ? match[1] : content).trim();
}

function cleanSessionTitle(title) {
  return String(title || "").replace(/\s+/g, " ").trim();
}

function runtimeActivityUpdateFromEvent(event) {
  const payload = event.payload || {};
  if (payload.role === "assistant") {
    return {
      thinking: [],
      calls: toolCallsFromMessage(payload).map(toolCallState),
      results: [],
    };
  }
  if (payload.role === "tool") {
    return {
      thinking: [],
      calls: [],
      results: [toolResultActivity(payload)],
    };
  }
  return { thinking: [], calls: [], results: [] };
}

function toolCallsFromMessage(message) {
  const payload = message?.payload && typeof message.payload === "object" ? message.payload : {};
  const rawToolCalls = message?.tool_calls || payload.tool_calls || [];
  return Array.isArray(rawToolCalls) ? rawToolCalls.filter((toolCall) => toolCall && typeof toolCall === "object") : [];
}

function toolResultActivity(message) {
  const payload = message?.payload && typeof message.payload === "object" ? message.payload : {};
  const parsed = parseJsonObject(message?.content);
  const status = String(parsed.status || "result");
  const toolCallId = String(message?.tool_call_id || payload.tool_call_id || parsed.tool_call_id || "tool");
  let detail = Object.prototype.hasOwnProperty.call(parsed, "result") ? parsed.result : message?.content || "";
  if (parsed.error) {
    detail = parsed.error;
  }
  const tone = status === "error" || parsed.error ? "error" : "success";
  return {
    title: `Tool result: ${toolCallId}`,
    toolCallId,
    content: stringifyDetail(detail),
    status: tone,
  };
}

function toolCallState(toolCall) {
  const id = toolCallId(toolCall);
  const argumentsText = toolCallArguments(toolCall);
  return {
    id,
    name: toolCallName(toolCall),
    description: toolCallDescription(argumentsText),
    arguments: argumentsText,
    result: "",
    status: "running",
  };
}

function mergeToolCalls(current, nextCalls) {
  const merged = Array.isArray(current) ? [...current] : [];
  const safeNextCalls = Array.isArray(nextCalls) ? nextCalls : [];
  for (const call of safeNextCalls) {
    const index = call.id ? merged.findIndex((item) => item.id === call.id) : -1;
    if (index >= 0) {
      merged[index] = { ...merged[index], ...call, result: merged[index].result || call.result || "" };
    } else {
      merged.push(call);
    }
  }
  return merged;
}

function applyToolResult(current, result) {
  const merged = Array.isArray(current) ? [...current] : [];
  const index = result.toolCallId ? merged.findIndex((item) => item.id === result.toolCallId) : -1;
  const patch = {
    result: result.content,
    status: result.status,
  };
  if (index >= 0) {
    merged[index] = { ...merged[index], ...patch };
    return merged;
  }
  const fallbackIndex = findLastRunningToolCallIndex(merged);
  if (fallbackIndex >= 0) {
    merged[fallbackIndex] = { ...merged[fallbackIndex], ...patch };
    return merged;
  }
  return merged;
}

function findLastRunningToolCallIndex(toolCalls) {
  const safeToolCalls = Array.isArray(toolCalls) ? toolCalls : [];
  for (let index = safeToolCalls.length - 1; index >= 0; index -= 1) {
    if (safeToolCalls[index]?.status === "running") {
      return index;
    }
  }
  return -1;
}

function appendToolCallsToRows(rows, calls) {
  const safeCalls = Array.isArray(calls) ? calls : [];
  if (!safeCalls.length) {
    return rows;
  }
  const nextRows = Array.isArray(rows) ? [...rows] : [];
  const last = nextRows[nextRows.length - 1];
  if (last?.type === "tools") {
    nextRows[nextRows.length - 1] = { ...last, calls: mergeToolCalls(last.calls || [], safeCalls) };
    return nextRows;
  }
  nextRows.push({ type: "tools", calls: safeCalls });
  return nextRows;
}

function applyToolResultToRows(rows, result) {
  if (!rows.length) {
    return rows;
  }
  const nextRows = rows.map((row) =>
    row.type === "tools" ? { ...row, calls: [...(row.calls || [])] } : row,
  );
  if (result.toolCallId) {
    for (let rowIndex = nextRows.length - 1; rowIndex >= 0; rowIndex -= 1) {
      const row = nextRows[rowIndex];
      if (row.type !== "tools") {
        continue;
      }
      if ((row.calls || []).some((call) => call.id === result.toolCallId)) {
        nextRows[rowIndex] = { ...row, calls: applyToolResult(row.calls || [], result) };
        return nextRows;
      }
    }
  }
  for (let rowIndex = nextRows.length - 1; rowIndex >= 0; rowIndex -= 1) {
    const row = nextRows[rowIndex];
    if (row.type !== "tools") {
      continue;
    }
    if ((row.calls || []).some((call) => call.status === "running")) {
      nextRows[rowIndex] = { ...row, calls: applyToolResult(row.calls || [], result) };
      return nextRows;
    }
  }
  return appendToolCallsToRows(nextRows, applyToolResult([], result));
}

function activityContentFromRows(rows) {
  const safeRows = Array.isArray(rows) ? rows : [];
  return safeRows
    .map((row) => {
      if (row.type === "thinking") {
        return `Thinking\n${row.content}`;
      }
      const calls = Array.isArray(row.calls) ? row.calls : [];
      return calls
        .map((call) => {
          const lines = [toolCallDetail(call)];
          if (call.result) {
            lines.push(`Tool result: ${call.id || call.name}\n${call.result}`);
          }
          return lines.join("\n");
        })
        .join("\n\n");
    })
    .filter(Boolean)
    .join("\n\n");
}

function toolActionLabel(name) {
  const normalized = String(name || "").trim().toLowerCase().replace(/-/g, "_");
  const aliases = {
    read: "Read",
    write: "Write",
    edit: "Edit",
    bash: "Bash",
    shell: "Bash",
    grep: "Grep",
    glob: "Glob",
    delete: "Delete",
  };
  if (aliases[normalized]) {
    return aliases[normalized];
  }
  return normalized ? normalized.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase()) : "Tool";
}

function toolStatusLabel(status) {
  const normalized = String(status || "running").trim().toLowerCase();
  if (normalized.includes("error") || normalized.includes("fail")) {
    return "Error";
  }
  if (normalized.includes("success") || normalized.includes("done") || normalized.includes("pass")) {
    return "Done";
  }
  return "Running";
}

function shortToolTarget(name, args) {
  const parsed = parseJsonObject(args);
  const normalized = String(name || "").trim().toLowerCase().replace(/-/g, "_");
  if (normalized === "bash" || normalized === "shell") {
    return compactText(String(parsed.command || parsed.cmd || ""));
  }
  if (normalized === "grep") {
    return String(parsed.pattern || parsed.query || parsed.regex || "").trim();
  }
  if (normalized === "glob") {
    return String(parsed.pattern || parsed.glob_pattern || parsed.glob || parsed.include || "").trim();
  }
  const path = String(parsed.path || parsed.file_path || parsed.file || parsed.target || parsed.filename || "").trim();
  return path ? shortPathLabel(path) : "";
}

function compactText(value) {
  const compact = String(value || "").replace(/\s+/g, " ").trim();
  return compact.length <= 48 ? compact : `${compact.slice(0, 45)}...`;
}

function shortPathLabel(value) {
  const parts = String(value || "").replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.length >= 2 ? parts.slice(-2).join("/") : parts[0] || value;
}

function toolCallId(toolCall) {
  return String(toolCall?.id || toolCall?.tool_call_id || "");
}

function toolCallName(toolCall) {
  const fn = toolCall?.function && typeof toolCall.function === "object" ? toolCall.function : {};
  return String(fn.name || toolCall?.name || toolCall?.tool_name || "tool");
}

function toolCallArguments(toolCall) {
  const fn = toolCall?.function && typeof toolCall.function === "object" ? toolCall.function : {};
  const args = Object.prototype.hasOwnProperty.call(fn, "arguments") ? fn.arguments : toolCall?.arguments;
  if (args === undefined || args === null) {
    return "";
  }
  return limitText(typeof args === "string" ? args : safeJsonStringify(args), TOOL_ARGUMENT_PREVIEW_LIMIT);
}

function toolCallDetail(toolCall) {
  return `Tool call: ${toolCallName(toolCall)}\n${toolCallArguments(toolCall)}`.trim();
}

function toolCallDescription(argumentsText) {
  const parsed = parseJsonObject(argumentsText);
  return String(parsed.description || "").trim();
}

function parseJsonObject(value) {
  try {
    const parsed = JSON.parse(String(value || ""));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function stringifyDetail(value) {
  return limitText(typeof value === "string" ? value : safeJsonStringify(value), TOOL_RESULT_PREVIEW_LIMIT);
}

function activityTone(status) {
  const value = String(status || "").toLowerCase();
  if (value.includes("error") || value.includes("fail")) {
    return "error";
  }
  if (value.includes("run") || value.includes("live")) {
    return "warning";
  }
  if (value.includes("success") || value.includes("done") || value.includes("pass")) {
    return "success";
  }
  return "ready";
}

function activityToolBatchTitle(calls) {
  const safeCalls = Array.isArray(calls) ? calls : [];
  for (const call of safeCalls) {
    if (call.description) {
      return call.description;
    }
  }
  return "Acting";
}

function eventSummary(event) {
  const payload = event.payload || {};
  if (payload.trace_tool) {
    const status = String(payload.status || "running");
    const parts = [status];
    if (payload.has_call) {
      parts.push("Call");
    }
    if (payload.has_result) {
      parts.push("Result");
    }
    const callId = shortId(payload.tool_call_id || "");
    if (callId) {
      parts.push(callId);
    }
    const target = shortToolTarget(payload.tool_name, payload.arguments);
    if (target) {
      parts.push(target);
    }
    return parts.join("  ");
  }
  if (payload.delta) {
    return payload.delta;
  }
  if (payload.error) {
    return payload.error;
  }
  if (payload.content) {
    return String(payload.content).slice(0, 180);
  }
  return limitText(safeJsonStringify(payload), 180);
}

function eventTitle(event) {
  const payload = event.payload || {};
  if (payload.trace_tool) {
    return toolActionLabel(payload.tool_name);
  }
  return event.type || "event";
}

function eventTone(event) {
  const payload = event.payload || {};
  if (payload.trace_tool) {
    return activityTone(payload.status);
  }
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

function sessionStatusLabel(status) {
  const kind = sessionStatusKind(status);
  if (kind === "running") {
    return "Running";
  }
  if (kind === "error") {
    return "Error";
  }
  return "Done";
}

function sessionStatusIdentity(session) {
  return [
    session.last_case_run_status || sessionStatusKind(session.last_case_run_status),
    session.updated_at || session.created_at || "",
  ].join(":");
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

function stringifyToolArgs(value) {
  if (value === undefined || value === null) {
    return "";
  }
  return limitText(typeof value === "string" ? value : safeJsonStringify(value), TOOL_ARGUMENT_PREVIEW_LIMIT);
}

function safeJsonStringify(value) {
  try {
    return JSON.stringify(value, (_key, item) => (typeof item === "bigint" ? item.toString() : item), 2);
  } catch {
    return String(value);
  }
}

function limitText(value, limit) {
  const text = String(value || "");
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit)}\n\n[truncated ${text.length - limit} chars]`;
}
