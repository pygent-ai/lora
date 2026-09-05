import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Folder,
  FolderCode,
  ArrowDown,
  ArrowUp,
  ChevronRight,
  Check,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Search,
  Settings as SettingsIcon,
  Trash2,
  X,
} from "lucide-react";

import { createApiClient } from "../shared/api/client.js";
import { loadWorkbenchPreferences, saveWorkbenchPreferences } from "./workbenchPreferences.js";
import { DEFAULT_PANEL_WIDTHS, PANEL_LIMITS, normalizePanelWidths, fitPanelWidths } from "./panelWidths.js";
import { projectPathKey } from "../features/projects/projectPaths.js";
import { FileExplorer } from "../features/workspace/FileExplorer.jsx";
import { PowerShellPanel } from "../features/workspace/PowerShellPanel.jsx";
import { activityHeaderText, runTimingFields } from "./runTiming.js";
import {
  adaptLayoutToCompactViewport,
  COMPACT_LAYOUT_QUERY,
  createInitialLayoutState,
  toggleHistory,
  toggleTrace,
} from "./layoutState.js";
import { parseInlineMarkdown, parseMarkdownBlocks } from "./markdown.js";
import {
  acknowledgeStoredSessionStatus,
  loadAcknowledgedSessionStatuses,
  sessionStatusIdentity,
} from "./sessionStatusState.js";

const EMPTY_SETTINGS = {
  workspace_root: "",
  lora_root: "",
  agent: "default",
  profile: "default",
  routes: [],
  fallback: [],
  retry: null,
  user_lora_root: "",
  max_steps: -1,
  context_window: null,
  context_compression_trigger_ratio: 0.9,
};

const TRACE_TABS = ["Overview", "Tools", "Changes", "Events", "Context", "Config"];
const INSPECTOR_LABELS = { Overview: "概览", Events: "事件", Context: "上下文", Tools: "工具", Changes: "文件活动", Config: "配置", Trace: "执行详情", Files: "文件", PowerShell: "终端" };
const TOOL_ARGUMENT_PREVIEW_LIMIT = 4_000;
const TOOL_RESULT_PREVIEW_LIMIT = 6_000;
const TRACE_RENDER_LIMIT = 300;

export function App() {
  const api = useMemo(() => createApiClient(), []);
  const workbenchRef = useRef(null);
  const [panelWidths, setPanelWidths] = useState(() => normalizePanelWidths(loadWorkbenchPreferences().panelWidths));
  const [workbenchWidth, setWorkbenchWidth] = useState(globalThis.window?.innerWidth || 1360);
  const [resizingPanel, setResizingPanel] = useState(false);
  useEffect(() => {
    const node = workbenchRef.current;
    const observer = new ResizeObserver(([entry]) => setWorkbenchWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!resizingPanel) saveWorkbenchPreferences({ panelWidths });
  }, [panelWidths, resizingPanel]);
  const [layout, setLayout] = useState(() => {
    const compact = compactLayoutMatches();
    const saved = loadWorkbenchPreferences().layout;
    const initial = createInitialLayoutState({ compact });
    return { ...initial, ...(!compact && saved && typeof saved.historyCollapsed === "boolean" && typeof saved.traceCollapsed === "boolean" ? saved : {}), compact };
  });
  const [settings, setSettings] = useState(EMPTY_SETTINGS);
  useEffect(() => {
    if (!layout.compact) saveWorkbenchPreferences({ layout: { historyCollapsed: layout.historyCollapsed, traceCollapsed: layout.traceCollapsed } });
  }, [layout]);
  const [projects, setProjects] = useState([]);
  const [sessionGroups, setSessionGroups] = useState([]);
  const [activeScopeId, setActiveScopeId] = useState("");
  const [activeSession, setActiveSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [activityCollapseToken, setActivityCollapseToken] = useState(0);
  const [traceEvents, setTraceEvents] = useState([]);
  const [liveEvents, setLiveEvents] = useState([]);
  const [contextSnapshots, setContextSnapshots] = useState([]);
  const [status, setStatus] = useState("Loading");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [runningSessionIds, setRunningSessionIds] = useState({});
  const [approvals, setApprovals] = useState([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const projectChooserBusyRef = useRef(false);
  const activeSessionIdRef = useRef("");
  const messagesRef = useRef([]);
  const runningSessionIdsRef = useRef({});
  const pendingSessionMessagesRef = useRef(new Map());
  const sessionLiveEventsRef = useRef(new Map());
  const sessionLoadTokenRef = useRef(0);
  const traceLoadTokenRef = useRef(0);

  const activeSessionId = activeSession?.session_id || "";
  const running = Boolean(activeSessionId && runningSessionIds[activeSessionId]);
  const hasRunningSessions = Object.keys(runningSessionIds).length > 0;
  const visibleSessionGroups = useMemo(
    () => applyRunningSessionStatus(sessionGroups, runningSessionIds),
    [sessionGroups, runningSessionIds],
  );
  const visibleTraceEvents = useMemo(() => [...traceEvents, ...liveEvents], [traceEvents, liveEvents]);

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    const matchMedia = globalThis.window?.matchMedia?.bind(globalThis.window);
    if (!matchMedia) {
      return undefined;
    }
    const compactLayout = matchMedia(COMPACT_LAYOUT_QUERY);
    const syncCompactLayout = (event) => {
      setLayout((current) => {
        const saved = loadWorkbenchPreferences().layout;
        const restored = !event.matches && current.compact && saved && typeof saved.historyCollapsed === "boolean" && typeof saved.traceCollapsed === "boolean"
          ? saved : current;
        return { ...adaptLayoutToCompactViewport(restored, event.matches), compact: event.matches };
      });
    };
    syncCompactLayout(compactLayout);
    compactLayout.addEventListener("change", syncCompactLayout);
    return () => compactLayout.removeEventListener("change", syncCompactLayout);
  }, []);

  const setSessionRunning = useCallback((sessionId, isRunning) => {
    if (!sessionId) {
      return;
    }
    const next = isRunning
      ? { ...runningSessionIdsRef.current, [sessionId]: true }
      : omitKey(runningSessionIdsRef.current, sessionId);
    runningSessionIdsRef.current = next;
    setRunningSessionIds(next);
  }, []);

  const loadTrace = useCallback(
    async (session, token = traceLoadTokenRef.current) => {
      if (!session?.last_case_run_id) {
        if (token === traceLoadTokenRef.current) {
          setTraceEvents([]);
          setContextSnapshots([]);
        }
        return;
      }
      const response = await api.getTraceEvents(session.session_id, session.last_case_run_id);
      if (token === traceLoadTokenRef.current) {
        setTraceEvents(response.events || []);
        setContextSnapshots((current) => mergeContextSnapshots(response.context_snapshots || [], current));
      }
    },
    [api],
  );

  const loadSession = useCallback(
    async (sessionId, previewSession = null) => {
      const sessionToken = sessionLoadTokenRef.current + 1;
      sessionLoadTokenRef.current = sessionToken;
      traceLoadTokenRef.current += 1;
      const traceToken = traceLoadTokenRef.current;
      if (previewSession) {
        activeSessionIdRef.current = previewSession.session_id || sessionId;
        setActiveSession(previewSession);
      }
      setStatus("Loading");
      setNotice("");
      const pendingMessages = pendingSessionMessagesRef.current.get(sessionId);
      const previewMessages = selectSessionMessages(pendingMessages, []);
      const previewLiveEvents = sessionLiveEventsRef.current.get(sessionId) || [];
      messagesRef.current = previewMessages;
      setMessages(previewMessages);
      setTraceEvents([]);
      setLiveEvents(previewLiveEvents);
      setContextSnapshots(contextSnapshotsFromEvents(previewLiveEvents));
      let detail;
      try {
        detail = await api.getSession(sessionId, { scopeId: previewSession?.scope_id });
      } catch (err) {
        if (sessionToken !== sessionLoadTokenRef.current) {
          return;
        }
        if (err?.status !== 404) {
          throw err;
        }
        activeSessionIdRef.current = "";
        messagesRef.current = [];
        setActiveSession(null);
        setMessages([]);
        setTraceEvents([]);
        setLiveEvents([]);
        setContextSnapshots([]);
        setNotice("This chat is unavailable in the current project. Select another chat or start a new one.");
        setStatus("Ready");
        return;
      }
      if (sessionToken !== sessionLoadTokenRef.current) {
        return;
      }
      const nextMessages = historyToMessages(detail.history || []);
      if (sessionToken !== sessionLoadTokenRef.current) {
        return;
      }
      const latestPendingMessages = pendingSessionMessagesRef.current.get(sessionId);
      const resolvedMessages = selectSessionMessages(latestPendingMessages, nextMessages);
      activeSessionIdRef.current = detail.session.session_id || sessionId;
      setActiveSession(detail.session);
      messagesRef.current = resolvedMessages;
      setMessages(resolvedMessages);
      setActivityCollapseToken((value) => value + 1);
      setStatus("Ready");
      window.setTimeout(() => {
        loadTrace(detail.session, traceToken).catch((err) => {
          if (traceToken === traceLoadTokenRef.current) {
            setError(readableError(err));
          }
        });
      }, 0);
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
      const activeScopeId = nextSessionGroups.active_scope_id || scopeIdFromWorkspace(nextSettings.workspace_root);
      setSettings(nextSettings);
      setProjects(nextProjects.projects || []);
      setSessionGroups(groups);
      const targetSessionId =
        selectSessionId ||
        (selectFirst ? firstSessionIdInScope(groups, activeScopeId) : preserveSessionId);
      const targetSession = sessionList.find((session) => session.session_id === targetSessionId);
      setActiveScopeId(targetSession?.scope_id || activeScopeId);
      if (targetSessionId && targetSession) {
        await loadSession(targetSessionId, targetSession);
      } else {
        sessionLoadTokenRef.current += 1;
        traceLoadTokenRef.current += 1;
        activeSessionIdRef.current = "";
        messagesRef.current = [];
        setActiveSession(null);
        setMessages([]);
        setTraceEvents([]);
        setLiveEvents([]);
        setContextSnapshots([]);
      }
      setStatus("Ready");
    },
    [api, loadSession],
  );

  useEffect(() => {
    let cancelled = false;
    setStatus("Connecting");
    initializeWorkbench(() => {
      if (cancelled) {
        return undefined;
      }
      return refreshWorkbench({ selectFirst: true });
    }).catch((err) => {
      if (!cancelled) {
        setStatus("Offline");
        setError(readableError(err));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [refreshWorkbench]);

  const handleCreateSession = useCallback(async (scope = null) => {
    try {
      setError("");
      setNotice("");
      const targetWorkspace = scope?.workspace_root || "";
      if (targetWorkspace && projectPathKey(targetWorkspace) !== projectPathKey(settings.workspace_root)) {
        setStatus("Opening project");
        await api.updateSettings({ workspaceRoot: targetWorkspace, agent: "" });
      }
      const session = await api.createSession({
        caseId: "chat",
        mode: "chat",
        scopeId: scope?.scope_id === "conversation" ? "conversation" : undefined,
      });
      await refreshWorkbench({ selectSessionId: session.session_id });
      setNotice("New chat created");
    } catch (err) {
      setError(readableError(err));
    }
  }, [api, refreshWorkbench, settings.workspace_root]);

  const handleDeleteSession = useCallback(
    async (sessionId, scope) => {
      try {
        setError("");
        if (scope?.workspace_root && scope.workspace_root !== settings.workspace_root) {
          await api.updateSettings({ workspaceRoot: scope.workspace_root });
        }
        await api.deleteSession(sessionId, { scopeId: scope?.scope_id });
        pendingSessionMessagesRef.current.delete(sessionId);
        sessionLiveEventsRef.current.delete(sessionId);
        await refreshWorkbench({ selectFirst: true });
        setNotice("Session deleted");
      } catch (err) {
        setError(readableError(err));
      }
    },
    [api, refreshWorkbench, settings.workspace_root],
  );

  const handleDeleteProject = useCallback(async (scope) => {
    if (!scope?.scope_id) return;
    try {
      setError("");
      setNotice("");
      if (scope.scope_id === scopeIdFromWorkspace(settings.workspace_root)) {
        const fallback = projects.find(
          (project) => projectPathKey(project.workspace_root) !== projectPathKey(scope.workspace_root),
        );
        if (!fallback) {
          throw new Error("Open another project before removing this one.");
        }
        setStatus("Opening project");
        await api.updateSettings({ workspaceRoot: fallback.workspace_root, agent: "" });
      }
      await api.removeProject(scope.scope_id);
      await refreshWorkbench({ selectFirst: true });
      setNotice("Project removed from sidebar");
    } catch (err) {
      setError(readableError(err));
    }
  }, [api, projects, refreshWorkbench, settings.workspace_root]);

  const handleSelectSession = useCallback(
    async (session, scope) => {
      const sessionId = typeof session === "string" ? session : session?.session_id;
      if (!sessionId) {
        return;
      }
      try {
        setError("");
        if (scope?.workspace_root && scope.workspace_root !== settings.workspace_root) {
          await api.updateSettings({ workspaceRoot: scope.workspace_root });
          await refreshWorkbench({ selectSessionId: sessionId });
          return;
        }
        await loadSession(sessionId, typeof session === "string" ? null : session);
      } catch (err) {
        setError(readableError(err));
      }
    },
    [api, loadSession, refreshWorkbench, settings.workspace_root],
  );

  const handleSendMessage = useCallback(
    async (message) => {
      const initialSessionId = activeSessionIdRef.current;
      if (!message.trim() || (initialSessionId && runningSessionIdsRef.current[initialSessionId])) {
        return;
      }
      if (initialSessionId) {
        setSessionRunning(initialSessionId, true);
      }
      setStatus("Running");
      setError("");
      setNotice("");
      setTraceEvents([]);
      setLiveEvents([]);
      if (!initialSessionId) {
        setContextSnapshots([]);
      }
      const assistantId = `assistant-${Date.now()}`;
      let streamSessionId = initialSessionId || null;
      let streamMessages = messagesRef.current;
      let streamEvents = [];
      const startedWithoutSession = !streamSessionId;
      let finalStatus = "Ready";

      const isStreamSessionVisible = () => {
        const currentSessionId = activeSessionIdRef.current;
        return currentSessionId === streamSessionId || (startedWithoutSession && !currentSessionId);
      };

      const updateCachedMessages = (updater) => {
        if (!streamSessionId) {
          return;
        }
        const current = pendingSessionMessagesRef.current.get(streamSessionId) || streamMessages;
        const next = updater(current);
        streamMessages = next;
        pendingSessionMessagesRef.current.set(streamSessionId, next);
      };

      const updateVisibleMessages = (updater) => {
        updateCachedMessages(updater);
        if (isStreamSessionVisible()) {
          setMessages(updater);
        }
      };

      const nextMessages = [
        ...messagesRef.current,
        { id: `user-${Date.now()}`, role: "user", content: message },
        {
          id: assistantId,
          role: "assistant",
          content: "",
          status: "running",
          startedAt: Date.now(),
          endedAt: null,
          sections: [],
        },
      ];
      if (initialSessionId) {
        pendingSessionMessagesRef.current.set(initialSessionId, nextMessages);
        sessionLiveEventsRef.current.set(initialSessionId, streamEvents);
      }
      streamMessages = nextMessages;
      messagesRef.current = nextMessages;
      setMessages(nextMessages);

      try {
        await api.streamChat(
          {
            message,
            sessionId: streamSessionId,
            caseId: "chat",
            scopeId: activeSession?.scope_id,
          },
          {
            onEvent: ({ data }) => {
              const eventKind = data.kind || "";
              const eventData = data.data || {};
              const eventSessionId = eventKind === "lora.chat.started" ? String(eventData.session_id || "") : "";
              if (eventSessionId && !streamSessionId) {
                streamSessionId = eventSessionId;
                setSessionRunning(eventSessionId, true);
                pendingSessionMessagesRef.current.set(eventSessionId, streamMessages);
              }
              streamEvents = appendSessionLiveTraceEvent(
                sessionLiveEventsRef.current,
                streamSessionId,
                streamEvents,
                apiEventToTraceEvent(data),
              );
              if (isStreamSessionVisible()) {
                setLiveEvents(streamEvents);
              }
              if (eventKind === "lora.context.snapshot" && isStreamSessionVisible()) {
                setContextSnapshots((current) => mergeContextSnapshots(current, [eventData]));
              }
              projectLiveExecutionEvent(updateVisibleMessages, assistantId, data);
              if (eventKind === "lora.approval.requested") {
                setApprovals((items) => [
                  ...items.filter((item) => item.approval_id !== eventData.approval_id),
                  { ...eventData, session_id: eventSessionId || streamSessionId },
                ]);
              } else if (eventKind === "execution.completed") {
                finalStatus = "Done";
                if (isStreamSessionVisible()) {
                  setActivityCollapseToken((value) => value + 1);
                }
              } else if (eventKind === "lora.transport.error" || eventKind === "execution.failed" || eventKind === "execution.deadline_exceeded") {
                finalStatus = "Error";
                if (isStreamSessionVisible()) {
                  setActivityCollapseToken((value) => value + 1);
                }
              } else if (eventKind === "execution.cancelled") {
                finalStatus = "Skipped";
                if (isStreamSessionVisible()) {
                  setActivityCollapseToken((value) => value + 1);
                }
              }
              if (["execution.completed", "execution.failed", "execution.deadline_exceeded", "execution.cancelled"].includes(eventKind)) {
                setApprovals((items) => items.filter((item) => item.session_id !== streamSessionId));
              }
            },
          },
        );
        const currentSessionId = activeSessionIdRef.current;
        if (streamSessionId) {
          setSessionRunning(streamSessionId, false);
          pendingSessionMessagesRef.current.delete(streamSessionId);
          await refreshWorkbench({
            selectSessionId: currentSessionId === streamSessionId || !currentSessionId ? streamSessionId : "",
            preserveSessionId: currentSessionId && currentSessionId !== streamSessionId ? currentSessionId : "",
          });
        } else {
          await refreshWorkbench({ selectFirst: true });
        }
        if (isStreamSessionVisible()) {
          setStatus(finalStatus);
        }
      } catch (err) {
        if (isStreamSessionVisible()) {
          setStatus("Error");
        }
        setError(readableError(err));
        projectLiveExecutionEvent(updateVisibleMessages, assistantId, {
          kind: "lora.transport.error",
          data: { error: readableError(err) },
        });
        if (isStreamSessionVisible()) {
          setActivityCollapseToken((value) => value + 1);
        }
      } finally {
        const completedSessionId = streamSessionId || initialSessionId;
        if (completedSessionId) {
          setSessionRunning(completedSessionId, false);
        }
      }
    },
    [activeSession?.scope_id, api, refreshWorkbench, setSessionRunning],
  );

  const handleSaveSettings = useCallback(
    async (draft) => {
      try {
        setError("");
        setStatus("Reloading");
        const nextSettings = await api.updateSettings(settingsForSave(draft, settings));
        setSettings(nextSettings);
        setSettingsOpen(false);
        setActiveSession(null);
        setMessages([]);
        setTraceEvents([]);
        setLiveEvents([]);
        setContextSnapshots([]);
        pendingSessionMessagesRef.current.clear();
        sessionLiveEventsRef.current.clear();
        await refreshWorkbench({ selectFirst: true });
        setNotice("Settings saved and runtime reloaded");
      } catch (err) {
        setStatus("Error");
        setError(readableError(err));
      }
    },
    [api, refreshWorkbench, settings],
  );

  const handleChooseProject = useCallback(async (newTask = false) => {
    if (projectChooserBusyRef.current) return;
    projectChooserBusyRef.current = true;
    try {
      setError("");
      setNotice("");
      const chooseDirectory = globalThis.window?.loraDesktop?.chooseProjectDirectory;
      if (typeof chooseDirectory !== "function") {
        setError("请在桌面应用中打开项目，以使用系统文件夹选择器。");
        return;
      }
      const workspaceRoot = await chooseDirectory(settings.workspace_root);
      if (!workspaceRoot || projectPathKey(workspaceRoot) === projectPathKey(settings.workspace_root)) return;
      if (Object.keys(runningSessionIdsRef.current).length) {
        setError("当前任务结束后即可切换项目。");
        return;
      }
      setStatus("Opening project");
      await api.updateSettings({ workspaceRoot, agent: "" });
      if (newTask === true) {
        const session = await api.createSession({ caseId: "chat", mode: "chat" });
        await refreshWorkbench({ selectSessionId: session.session_id });
      } else {
        await refreshWorkbench({ selectFirst: true });
      }
      setNotice("Project opened");
    } catch (err) {
      setStatus("Error");
      setError(readableError(err));
    } finally {
      projectChooserBusyRef.current = false;
    }
  }, [api, refreshWorkbench, settings.workspace_root]);

  const appClassName = appLayoutClassName(layout);
  const fittedWidths = fitPanelWidths(panelWidths, layout, workbenchWidth);
  function resizePanel(side, width) {
    const other = side === "history" ? fittedWidths.trace : fittedWidths.history;
    const [minimum, maximum] = PANEL_LIMITS[side];
    const limit = Math.max(minimum, Math.min(maximum, workbenchWidth - other - 320));
    setPanelWidths((current) => ({ ...current, [side]: Math.max(minimum, Math.min(limit, width)) }));
  }

  async function handleApproval(approval, approved) {
    try {
      await api.deliverApproval(
        approval.approval_id,
        approved,
        approved ? "Approved in Lora Desktop" : "Rejected in Lora Desktop",
      );
      setApprovals((items) => items.filter((item) => item.approval_id !== approval.approval_id));
    } catch (approvalError) {
      setApprovals((items) => items.filter((item) => item.approval_id !== approval.approval_id));
      setError(String(approvalError?.message || approvalError));
    }
  }

  return (
    <main ref={workbenchRef} aria-label="Workbench" className={`${appClassName}${resizingPanel ? " resizing-panels" : ""}`} style={{ "--history-width": `${fittedWidths.history}px`, "--trace-width": `${fittedWidths.trace}px` }}>
      {!layout.historyCollapsed && <PanelDivider side="history" width={fittedWidths.history} max={Math.max(200, Math.min(480, workbenchWidth - fittedWidths.trace - 320))} onResize={(width) => resizePanel("history", width)} onDragging={setResizingPanel} />}
      {!layout.traceCollapsed && <PanelDivider side="trace" width={fittedWidths.trace} max={Math.max(260, Math.min(720, workbenchWidth - fittedWidths.history - 320))} onResize={(width) => resizePanel("trace", width)} onDragging={setResizingPanel} />}
      <SessionSidebar
        collapsed={layout.historyCollapsed}
        settings={settings}
        projects={projects}
        sessionGroups={visibleSessionGroups}
        activeScopeId={activeScopeId}
        activeSessionId={activeSessionId}
        onCreateSession={handleCreateSession}
        onDeleteSession={handleDeleteSession}
        onDeleteProject={handleDeleteProject}
        onSelectSession={handleSelectSession}
        onChooseProject={handleChooseProject}
        onOpenSettings={() => setSettingsOpen(true)}
        onToggle={() =>
          setLayout((current) => toggleHistory(current, { compact: current.compact }))
        }
      />
      <ChatPane
        activeSession={activeSession}
        messages={messages}
        activityCollapseToken={activityCollapseToken}
        settings={settings}
        status={status}
        running={running}
        approvals={approvals.filter((item) => item.session_id === activeSessionId)}
        api={api}
        onSendMessage={handleSendMessage}
        onApproval={handleApproval}
        projects={projects}
        onSelectProject={handleCreateSession}
        onChooseProject={() => handleChooseProject(true)}
        onChangePermissions={async (approvalsEnabled) => {
          const nextSettings = await api.updateSettings({ approvalsEnabled });
          setSettings(nextSettings);
        }}
      />
      <TracePanel
        api={api}
        activeSession={activeSession}
        collapsed={layout.traceCollapsed}
        contextSnapshots={contextSnapshots}
        events={visibleTraceEvents}
        settings={settings}
        onToggle={() =>
          setLayout((current) => toggleTrace(current, { compact: current.compact }))
        }
      />
      {(error || notice) && (
        <div className={error ? "toast error" : "toast"} role="status">
          {error || notice}
        </div>
      )}
      {settingsOpen && (
        <SettingsPanel
          settings={settings}
          disabled={hasRunningSessions}
          onClose={() => setSettingsOpen(false)}
          onSave={handleSaveSettings}
        />
      )}
    </main>
  );
}

function PanelDivider({ side, width, max, onResize, onDragging }) {
  const dragRef = useRef(null);
  function stop(event) {
    if (!dragRef.current) return;
    dragRef.current = null;
    onDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  return <div className={`panel-divider panel-divider-${side}`} role="separator" aria-label={side === "history" ? "调整项目面板宽度" : "调整详情面板宽度"}
    aria-orientation="vertical" aria-valuemin={PANEL_LIMITS[side][0]} aria-valuemax={max} aria-valuenow={width} tabIndex={0}
    title="拖动调整宽度，双击恢复默认；方向键微调"
    onPointerDown={(event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.currentTarget.focus();
      dragRef.current = { x:event.clientX, width };
      event.currentTarget.setPointerCapture(event.pointerId);
      onDragging(true);
    }}
    onPointerMove={(event) => {
      if (dragRef.current) onResize(dragRef.current.width + (event.clientX - dragRef.current.x) * (side === "history" ? 1 : -1));
    }} onPointerUp={stop} onPointerCancel={stop} onLostPointerCapture={stop}
    onDoubleClick={() => onResize(DEFAULT_PANEL_WIDTHS[side])}
    onKeyDown={(event) => {
      if (event.key === "Home") { event.preventDefault(); onResize(DEFAULT_PANEL_WIDTHS[side]); }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        onResize(width + (event.key === "ArrowRight" ? 1 : -1) * (side === "history" ? 1 : -1) * (event.shiftKey ? 40 : 10));
      }
    }} />;
}

export function appLayoutClassName(layout) {
  return [
    "app-shell",
    layout.compact ? "compact-layout" : "",
    layout.historyCollapsed ? "history-collapsed" : "",
    layout.traceCollapsed ? "trace-collapsed" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

export function SessionSidebar({
  collapsed,
  settings,
  projects,
  sessionGroups,
  activeScopeId,
  activeSessionId,
  onCreateSession,
  onDeleteSession,
  onDeleteProject,
  onSelectSession,
  onChooseProject,
  onOpenSettings,
  onToggle,
}) {
  const [collapsedGroups, setCollapsedGroups] = useState({});
  const [query, setQuery] = useState("");
  const search = query.trim().toLocaleLowerCase();
  const filteredGroups = sessionGroups.map((group) => ({ ...group, sessions: (group.sessions || []).filter((session) => !search || `${session.title || ""} ${session.session_id || ""} ${group.scope?.label || ""}`.toLocaleLowerCase().includes(search)) })).filter((group) => !search || group.sessions.length);
  const [acknowledgedStatuses, setAcknowledgedStatuses] = useState(loadAcknowledgedSessionStatuses);

  function toggleGroup(scopeId) {
    setCollapsedGroups((current) => ({ ...current, [scopeId]: !current[scopeId] }));
  }

  function acknowledgeSessionStatus(session) {
    setAcknowledgedStatuses((current) => acknowledgeStoredSessionStatus(current, session));
  }

  return (
    <aside className="history" aria-label="Session history">
      <div className="history-shell">
        <div className="history-top">
          <div className="brand">
            <h1 className="brand-title">{collapsed ? "L" : "Lora"}</h1>
          </div>
          <div className="history-header-actions" aria-label="Create">
            <button className="icon-button header-action" title="New chat" aria-label="New chat in current project"
              type="button" onClick={() => onCreateSession()}><Plus aria-hidden="true" /></button>
            <button className="icon-button header-action" title="Open project" aria-label="Open project"
              aria-haspopup="dialog" type="button" onClick={onChooseProject}><Folder aria-hidden="true" /></button>
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

        {!collapsed && <>
          <button className="new-task-button" type="button" onClick={() => onCreateSession()}><Plus size={18} aria-hidden="true" />新建任务</button>
          <label className="session-search"><Search size={16} aria-hidden="true" /><input aria-label="搜索任务" placeholder="搜索任务或项目" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
        </>}
        <div className="section-label">项目与任务</div>
        <div className="session-groups">
          {sessionGroups.length === 0 && <div className="empty-state">No chats yet</div>}
          {search && filteredGroups.length === 0 && <div className="empty-state">没有匹配的任务，试试其他关键词。</div>}
          {filteredGroups.map((group) => {
            const scope = group.scope || {};
            const isCollapsed = !search && (collapsedGroups[scope.scope_id] ?? group.collapsed);
            const isCurrentProject = scope.scope_id === scopeIdFromWorkspace(settings.workspace_root);
            const canSwitchBeforeRemoving = !scope.workspace_root || projects.some(
              (project) => project.workspace_root
                && projectPathKey(project.workspace_root) !== projectPathKey(scope.workspace_root),
            );
            return (
              <section className="session-group" key={scope.scope_id || scope.label}>
                <button
                  className={`${scope.scope_id === activeScopeId ? "session-group-header active" : "session-group-header"} has-new-chat${scope.workspace_root ? " has-project-actions" : ""}`}
                  title={scope.tooltip || scope.label}
                  type="button"
                  onClick={() => toggleGroup(scope.scope_id)}
                >
                  {scope.workspace_root && <FolderCode aria-hidden="true" />}
                  <span className="group-label">{scope.label || "Workspace"}</span>
                </button>
                <div className="session-group-actions">
                  <button className="session-group-new-chat" type="button"
                      aria-label={`New chat in ${scope.label || "project"}`}
                      title={`New chat in ${scope.label || "project"}`}
                      onClick={() => onCreateSession(scope)}>
                    <Plus aria-hidden="true" />
                  </button>
                  {scope.workspace_root && (
                    <button className="session-group-delete" type="button"
                        disabled={isCurrentProject && !canSwitchBeforeRemoving}
                        aria-label={`Remove project ${scope.label || "project"}`}
                        title={isCurrentProject && !canSwitchBeforeRemoving ? "Open another project before removing this one" : "Remove project from sidebar"}
                        onClick={() => onDeleteProject(scope)}>
                      <Trash2 aria-hidden="true" />
                    </button>
                  )}
                </div>
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
            <span>{primaryModel(settings)}</span>
            <span>{projects.length ? `${projects.length} workspace` : "active workspace only"}</span>
          </div>
          <button className="plain-action" title="Settings" type="button" onClick={onOpenSettings}>
            <SettingsIcon aria-hidden="true" />
            <span className="plain-action-label">设置</span>
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
    onSelectSession(session, scope);
  }

  return (
    <div
      className={active ? "session-row active" : "session-row"}
      role="button"
      tabIndex={0}
      onClick={selectSession}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
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

function ChatPane({ activeSession, messages, activityCollapseToken, settings, status, running, approvals, api, onSendMessage, onApproval, projects = [], onSelectProject, onChooseProject, onChangePermissions }) {
  const [draft, setDraft] = useState("");
  const [configuring, setConfiguring] = useState(false);
  const [configError, setConfigError] = useState("");
  const empty = messages.length === 0;
  const selectedScope = activeSession?.scope_id || scopeIdFromWorkspace(settings.workspace_root);
  async function configure(action) {
    setConfiguring(true);
    setConfigError("");
    try { await action(); } catch (err) { setConfigError(readableError(err)); }
    finally { setConfiguring(false); }
  }
  const transcriptRef = useRef(null);
  const composerRef = useRef(null);
  const followTranscriptRef = useRef(true);
  const [awayFromLatest, setAwayFromLatest] = useState(false);
  const chatTitle = activeSession?.title || "Select or create a chat session";

  useEffect(() => {
    if (followTranscriptRef.current) scrollTranscriptToLatest(transcriptRef.current);
  }, [messages, activityCollapseToken]);
  useEffect(() => {
    followTranscriptRef.current = true;
    setAwayFromLatest(false);
    scrollTranscriptToLatest(transcriptRef.current);
  }, [activeSession?.session_id]);
  useEffect(() => {
    const input = composerRef.current;
    if (input) { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 200)}px`; }
  }, [draft]);

  function submit() {
    const text = draft.trim();
    if (!text || running || configuring) {
      return;
    }
    setDraft("");
    followTranscriptRef.current = true;
    onSendMessage(text);
  }

  return (
    <section className={`chat${empty ? " chat-empty" : ""}`} aria-label="Chat">
      <header className="chat-header">
        <div className="chat-title">
          <h2 title={chatTitle}>{chatTitle}</h2>
          <p>
            <span title={settings.workspace_root}>{activeSession?.scope_id === "conversation" ? "独立对话" : shortPath(settings.workspace_root) || "未选择项目"}</span>
            <span aria-hidden="true"> · </span>{primaryModel(settings)}
          </p>
        </div>
        <div className={`status-pill ${statusTone(status)}`}>{statusLabel(status)}</div>
      </header>

      <div className="transcript" ref={transcriptRef} onScroll={(event) => {
        const node = event.currentTarget;
        followTranscriptRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
        setAwayFromLatest(!followTranscriptRef.current);
      }}>
        {messages.map((message) => (
          <MessageRow key={message.id} message={message} activityCollapseToken={activityCollapseToken} api={api} />
        ))}
      </div>

      {approvals.map((approval) => (
        <aside className="approval-card" key={approval.approval_id} aria-live="assertive">
          <div>
            <span className="approval-kicker">Execution paused</span>
            <strong>{approval.tool_name}</strong>
            <code>{safeJsonStringify(approval.arguments || {})}</code>
          </div>
          <div className="approval-actions">
            <button className="plain-action" type="button" onClick={() => onApproval(approval, false)}>Reject</button>
            <button className="send" type="button" onClick={() => onApproval(approval, true)}>Approve once</button>
          </div>
        </aside>
      ))}

      <footer className="composer">
        {messages.length === 0 && (
          <div className="welcome">
            <span className="welcome-eyebrow">LORA WORKSPACE</span>
            <h3>从一个任务开始</h3>
            <p>描述你的目标，Lora 会结合当前项目分析、执行并整理结果。</p>
            <div className="starter-actions">{["介绍这个项目的结构", "检查当前代码中的问题", "梳理待完成的工作"].map((prompt) => <button key={prompt} type="button" onClick={() => { setDraft(prompt); composerRef.current?.focus(); }}>{prompt}<ArrowUp size={16} aria-hidden="true" /></button>)}</div>
          </div>
        )}

        {awayFromLatest && <button className="jump-latest" type="button" onClick={() => { followTranscriptRef.current = true; scrollTranscriptToLatest(transcriptRef.current); setAwayFromLatest(false); }}><ArrowDown size={14} />回到最新</button>}
        <div className="composer-surface">
        <div className="composer-box">
          <textarea
            ref={composerRef}
            aria-label="任务内容"
            disabled={running || configuring}
            placeholder={running ? "Lora 正在执行任务…" : "描述任务，或继续追问…"}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (shouldSubmitComposer(event)) {
                event.preventDefault();
                submit();
              }
            }}
          />
        </div>
        <div className="composer-context">
          {!empty ? <span className="composer-option" title={selectedScope === "conversation" ? "独立对话" : selectedScope.slice(8)}>
            <FolderCode size={14} aria-hidden="true" />
            {selectedScope === "conversation" ? "独立对话" : projects.find((project) => scopeIdFromWorkspace(project.workspace_root) === selectedScope)?.label || shortPath(selectedScope.slice(8))}
          </span> : <ComposerMenu label="任务项目" icon={<FolderCode size={14} aria-hidden="true" />} value={selectedScope} disabled={running || configuring} onChange={(value) => {
              if (value === "browse") void configure(() => onChooseProject());
              else void configure(() => onSelectProject(value === "conversation" ? { scope_id: "conversation" } : { scope_id: value, workspace_root: value.slice(8) }));
            }} options={[
              { value:"conversation", label:"独立对话" },
              ...(settings.workspace_root && !projects.some((project) => projectPathKey(project.workspace_root) === projectPathKey(settings.workspace_root)) ? [{ value:scopeIdFromWorkspace(settings.workspace_root), label:shortPath(settings.workspace_root), description:settings.workspace_root }] : []),
              ...projects.map((project) => ({ value:scopeIdFromWorkspace(project.workspace_root), label:project.label || shortPath(project.workspace_root), description:project.workspace_root })),
              { value:"browse", label:"选择其他文件夹…", action:true },
            ]} />}
          <ComposerMenu label="任务权限（全局）" title="全局权限设置，对后续新运行生效" value={settings.approvals_enabled === false ? "full" : "ask"} disabled={running || configuring} onChange={(value) => void configure(() => onChangePermissions(value === "ask"))}
            options={[{ value:"ask", label:"逐次审批" }, { value:"full", label:"完全访问" }]} />
          <div className="composer-send-actions">
            <span className="composer-hint">Enter 发送 · Shift + Enter 换行</span>
            <button className="send" aria-label={running ? "执行中" : "发送"} title={running ? "执行中" : "发送"} disabled={running || configuring || !draft.trim()} type="button" onClick={submit}>
              <ArrowUp aria-hidden="true" />
            </button>
          </div>
        </div>
        {configError && <p className="composer-config-error" role="alert">{configError}</p>}
        </div>
      </footer>
    </section>
  );
}

function ComposerMenu({ label, title, icon, value, options, disabled, onChange }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  useEffect(() => {
    if (!open) return;
    const items = menuRef.current?.querySelectorAll('[role^="menuitem"]');
    const selected = options.findIndex((option) => option.value === value);
    items?.[Math.max(0, selected)]?.focus();
    const dismiss = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  return <div className="composer-menu" ref={rootRef} onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }}>
    <button ref={triggerRef} className="composer-menu-trigger" type="button" title={title} aria-label={label} aria-haspopup="menu" aria-expanded={open} disabled={disabled}
      onClick={() => setOpen((current) => !current)} onKeyDown={(event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setOpen(true); }
      }}>
      {icon}<span>{options.find((option) => option.value === value)?.label || label}</span><ChevronRight size={12} className="composer-menu-chevron" aria-hidden="true" />
    </button>
    {open && <div ref={menuRef} className="composer-menu-popup" role="menu" aria-label={label} onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); triggerRef.current?.focus(); }
      const items = Array.from(menuRef.current.querySelectorAll('[role^="menuitem"]'));
      const index = items.indexOf(document.activeElement);
      const target = event.key === "ArrowDown" ? (index + 1) % items.length : event.key === "ArrowUp" ? (index - 1 + items.length) % items.length : event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : -1;
      if (target >= 0) { event.preventDefault(); items[target]?.focus(); }
    }}>
      <div className="composer-menu-heading">{label}</div>
      {options.map((option) => <button key={option.value} type="button" tabIndex={-1} role={option.action ? "menuitem" : "menuitemradio"} aria-checked={option.action ? undefined : value === option.value}
        className={`composer-menu-item${option.action ? " menu-action" : ""}`} title={option.description}
        onClick={() => { setOpen(false); triggerRef.current?.focus(); if (option.value !== value) onChange(option.value); }}>
        <span>{option.label}{option.description && <small>{option.description}</small>}</span>
        {value === option.value && <Check size={14} aria-hidden="true" />}
        {option.action && <Folder size={14} aria-hidden="true" />}
      </button>)}
    </div>}
  </div>;
}

function MessageRow({ message, activityCollapseToken, api }) {
  if (message.role === "activity") {
    return <ActivityMessage message={message} collapseToken={activityCollapseToken} api={api} />;
  }

  const hasAssistantActivity =
    message.role === "assistant" &&
    ((Array.isArray(message.sections) && message.sections.length > 0) || message.startedAt !== undefined);
  if (message.role === "assistant" && !message.content && !hasAssistantActivity) {
    return null;
  }

  return (
    <article className={`message ${message.role}`}>
      {message.role !== "user" && <div className="avatar">L</div>}
      <div className="bubble">
        {message.role === "assistant" ? (
          <>
            {hasAssistantActivity && (
              <AssistantActivity message={message} collapseToken={activityCollapseToken} api={api} />
            )}
            {message.content && <MarkdownContent content={message.content} />}
          </>
        ) : (
          message.content
        )}
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

function ActivityMessage({ message, collapseToken, api }) {
  return (
    <article className={`message assistant${message.status === "running" ? " is-running" : ""}`}>
      <div className="avatar">L</div>
      <div className="bubble">
        <AssistantActivity message={message} collapseToken={collapseToken} api={api} />
      </div>
    </article>
  );
}

function AssistantActivity({ message, collapseToken, api }) {
  const isRunning = message.status === "running";
  const [expanded, setExpanded] = useState(isRunning);
  const [now, setNow] = useState(Date.now());
  const sections = visibleActivitySections(message);
  const liveStatus = activityLiveStatus(message);
  const header = activityHeaderText(message, now);
  const showDetail =
    expanded && (sections.length > 0 || liveStatus || (isRunning && !hasVisibleAssistantContent(message)));

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
          <ChevronRight className="activity-chevron" aria-hidden="true" />
        </span>
      </button>
      {showDetail && (
        <div className="activity-detail">
          {sections.map((section, index) =>
            section.type === "tools" ? (
              <ToolGroup section={section} key={section.id || `tools-${index}`} api={api} />
            ) : (
              <ActivityTextSection section={section} key={section.id || `text-${index}`} />
            ),
          )}
          {liveStatus && <div className="activity-live-status">{liveStatus}</div>}
          {!sections.length && !liveStatus && isRunning && !hasVisibleAssistantContent(message) && (
            <div className="activity-muted">Waiting for model output...</div>
          )}
        </div>
      )}
    </div>
  );
}

function ActivityTextSection({ section }) {
  const content = String(section.content || "").trim();
  if (!content) {
    return null;
  }
  if (section.title === "Assistant content") {
    return (
      <section className="activity-section assistant-content-section">
        <MarkdownContent content={content} />
      </section>
    );
  }
  return (
    <section className="activity-section">
      <pre className="activity-block">{content}</pre>
    </section>
  );
}

function ToolGroup({ section, api }) {
  const safeCalls = Array.isArray(section.calls) ? section.calls : [];
  const [expanded, setExpanded] = useState(section.status === "running");

  useEffect(() => {
    if (section.status === "running") {
      setExpanded(true);
    }
  }, [section.status]);

  return (
    <section className="tool-group">
      <button
        className="tool-group-head"
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span>{toolGroupTitle(section)}</span>
        <ChevronRight className="activity-chevron" aria-hidden="true" />
      </button>
      {expanded && (
        <div className="tool-group-body">
          {safeCalls.map((call, index) => (
            <ToolCallRow call={call} key={call.id || `${call.name}-${index}`} api={api} />
          ))}
        </div>
      )}
    </section>
  );
}

function ToolCallRow({ call, api }) {
  const [expanded, setExpanded] = useState(false);
  const [fullResult, setFullResult] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const hasResult = Boolean(call.result || call.resultRef);
  const tone = activityTone(call.status);

  async function toggleExpanded() {
    if (!hasResult) {
      return;
    }
    const nextExpanded = !expanded;
    setExpanded(nextExpanded);
    if (!nextExpanded || !call.resultRef || fullResult || loading || !api?.getToolResult) {
      return;
    }
    try {
      setLoading(true);
      setLoadError("");
      const response = await api.getToolResult(call.id);
      setFullResult(stringifyFullDetail(response.error || response.result || ""));
    } catch (err) {
      setLoadError(readableError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="tool-call-row">
      <button
        className="tool-call-toggle"
        type="button"
        disabled={!hasResult}
        aria-expanded={hasResult ? expanded : undefined}
        onClick={toggleExpanded}
      >
        <span className="tool-call-name">{toolCallDisplayLabel(call)}</span>
        <span className={`tool-call-status ${tone}`}>{toolStatusLabel(call.status)}</span>
      </button>
      {expanded && hasResult && (
        <pre className="tool-call-result">
          {loading ? "Loading result..." : loadError || fullResult || call.result}
        </pre>
      )}
    </div>
  );
}

export function TracePanel({ api, collapsed, contextSnapshots, events, settings, activeSession, onToggle }) {
  const [panelMode, setPanelMode] = useState("Trace");
  const [tab, setTab] = useState("Overview");
  const [eventPrefix, setEventPrefix] = useState("all");
  const [expandedEventKeys, setExpandedEventKeys] = useState(() => new Set());
  const overviewTools = useMemo(() => traceToolEvents(events), [events]);
  const traceListRef = useRef(null);
  const tabEvents = useMemo(() => traceTabEvents(tab, events), [tab, events]);
  const prefixGroups = useMemo(
    () => (tab === "Events" ? eventPrefixGroups(tabEvents) : []),
    [tab, tabEvents],
  );
  const activePrefix = eventPrefix === "all" || prefixGroups.some((group) => group.prefix === eventPrefix)
    ? eventPrefix
    : "all";
  const visibleEvents = useMemo(
    () => filterTraceEventsByPrefix(tabEvents, activePrefix),
    [tabEvents, activePrefix],
  );
  const renderedEvents = useMemo(() => latestItems(visibleEvents, TRACE_RENDER_LIMIT), [visibleEvents]);
  const hiddenEventCount = Math.max(0, visibleEvents.length - renderedEvents.length);
  const renderedEventKeys = useMemo(
    () => renderedEvents.map((event, index) => traceEventKey(event, tab, index)),
    [renderedEvents, tab],
  );
  const expandedCount = renderedEventKeys.filter((key) => expandedEventKeys.has(key)).length;
  const projectScopeId = activeSession?.scope_id === "conversation"
    ? ""
    : activeSession?.scope_id || scopeIdFromWorkspace(settings.workspace_root);

  useEffect(() => {
    setEventPrefix("all");
    setExpandedEventKeys(new Set());
  }, [tab, activeSession?.session_id, activeSession?.last_case_run_id]);

  useEffect(() => {
    if (!collapsed && tab !== "Config") {
      scrollTraceToLatest(traceListRef.current);
    }
  }, [collapsed, renderedEvents, tab]);

  return (
    <aside className="trace" aria-label="Trace inspector">
      <header className="trace-header">
        <div className="trace-title">
          <h3>{INSPECTOR_LABELS[panelMode]}</h3>
          <p>{panelMode === "Trace"
            ? "运行概览、工具结果与原始记录"
            : projectScopeId ? settings.workspace_root : "No project selected"}</p>
        </div>
        <button
          className="icon-button trace-toggle"
          title={collapsed ? "Expand trace" : "Collapse trace"}
          type="button"
          onClick={onToggle}
        >
          {collapsed ? <PanelRightOpen aria-hidden="true" /> : <PanelRightClose aria-hidden="true" />}
        </button>
        <div className="vertical-label">详情</div>
      </header>

      <nav className="inspector-modes" aria-label="Inspector modes">
        {["Trace", "Files", "PowerShell"].map((item) => (
          <button className={item === panelMode ? "active" : ""} key={item} type="button" onClick={() => setPanelMode(item)}>
            {INSPECTOR_LABELS[item]}
          </button>
        ))}
      </nav>

      {panelMode === "Files" ? (
        <FileExplorer api={api} scopeId={projectScopeId} workspaceRoot={projectScopeId ? settings.workspace_root : ""} />
      ) : panelMode === "PowerShell" ? (
        <PowerShellPanel api={api} scopeId={projectScopeId} workspaceRoot={projectScopeId ? settings.workspace_root : ""} />
      ) : (
      <div className="trace-mode-content">
        <nav className="trace-tabs" aria-label="Trace tabs">
          {TRACE_TABS.map((item) => (
            <button className={item === tab ? "tab active" : "tab"} key={item} type="button" onClick={() => setTab(item)}>
              {INSPECTOR_LABELS[item]}
            </button>
          ))}
        </nav>

        {tab === "Overview" ? (
          <div className="run-overview">
            <span className="section-label">当前任务</span>
            <h4>{activeSession?.title || "还没有任务"}</h4>
            <p className="overview-status">{!events.length ? "执行任务后，这里会显示工具活动与文件记录。" : "执行记录已就绪，可按需查看工具结果与原始事件。"}</p>
            <div className="overview-metrics">
              <button type="button" onClick={() => setTab("Tools")}><strong>{overviewTools.length}</strong><span>工具调用</span></button>
              <button type="button" onClick={() => setTab("Changes")}><strong>{traceTabEvents("Changes", events).length}</strong><span>文件活动</span></button>
              <button type="button" onClick={() => setTab("Events")}><strong>{events.length}</strong><span>原始事件</span></button>
            </div>
            <h5>最近工具活动</h5>
            {overviewTools.slice(-6).map((event, index) => { const key = `overview-${event.id || index}`; return <TraceEventRow key={key} event={event} tab="Tools" expanded={expandedEventKeys.has(key)} onToggle={() => setExpandedEventKeys((current) => toggleSetValue(current, key))} />; })}
            {!overviewTools.length && <p className="empty-state">暂无工具调用</p>}
            <details className="overview-identifiers"><summary>运行标识</summary><p>Session: {activeSession?.session_id || "—"}</p><p>Run: {activeSession?.last_case_run_id || "—"}</p></details>
          </div>
        ) : tab === "Config" ? (
        <div className="config-list">
          {configRows(settings).map(([key, value]) => (
            <div className="config-row" key={key}>
              <strong>{key}</strong>
              <span>{formatConfigValue(key, value)}</span>
            </div>
          ))}
        </div>
      ) : tab === "Context" ? (
        <ContextInspector snapshots={contextSnapshots} />
      ) : (
        <div className="trace-event-pane">
          <div className="trace-list-controls">
            <div className="trace-list-toolbar">
              <span>{visibleEvents.length} {tab.toLowerCase()}</span>
              <div className="trace-list-actions">
                <button
                  type="button"
                  disabled={renderedEventKeys.length === 0 || expandedCount === renderedEventKeys.length}
                  onClick={() => setExpandedEventKeys(new Set(renderedEventKeys))}
                >
                  Expand all
                </button>
                <button
                  type="button"
                  disabled={expandedCount === 0}
                  onClick={() => setExpandedEventKeys(new Set())}
                >
                  Collapse all
                </button>
              </div>
            </div>
            {tab === "Events" && prefixGroups.length > 0 && (
              <div className="trace-prefix-filters" aria-label="Filter events by prefix">
                <button
                  className={activePrefix === "all" ? "active" : ""}
                  type="button"
                  aria-pressed={activePrefix === "all"}
                  onClick={() => setEventPrefix("all")}
                >
                  <span>All</span><strong>{tabEvents.length}</strong>
                </button>
                {prefixGroups.map((group) => (
                  <button
                    className={activePrefix === group.prefix ? "active" : ""}
                    key={group.prefix}
                    type="button"
                    aria-pressed={activePrefix === group.prefix}
                    onClick={() => setEventPrefix(group.prefix)}
                  >
                    <span>{group.prefix}</span><strong>{group.count}</strong>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="trace-list" ref={traceListRef}>
            {visibleEvents.length === 0 && <div className="empty-state">No {tab.toLowerCase()} yet</div>}
            {hiddenEventCount > 0 && (
              <div className="empty-state compact">Showing latest {renderedEvents.length} of {visibleEvents.length}</div>
            )}
            {renderedEvents.map((event, index) => {
              const key = renderedEventKeys[index];
              return (
                <TraceEventRow
                  event={event}
                  expanded={expandedEventKeys.has(key)}
                  tab={tab}
                  key={key}
                  onToggle={() => setExpandedEventKeys((current) => toggleSetValue(current, key))}
                />
              );
            })}
          </div>
        </div>
        )}
      </div>
      )}
    </aside>
  );
}

export function ContextInspector({ snapshots }) {
  const runs = useMemo(() => contextSnapshotRuns(snapshots), [snapshots]);
  const latestRun = runs[runs.length - 1] || null;
  const [runId, setRunId] = useState("");
  const [version, setVersion] = useState(null);
  const [snapshotId, setSnapshotId] = useState("");

  useEffect(() => {
    if (!runs.length) {
      setRunId("");
      setVersion(null);
      setSnapshotId("");
      return;
    }
    const selectedRun = runs.find((run) => run.id === runId) || latestRun;
    const selectedVersion = selectedRun.versions.find((item) => item.version === version)
      || selectedRun.versions[selectedRun.versions.length - 1];
    const selectedSnapshot = selectedVersion.snapshots.find((item) => item.snapshot_id === snapshotId)
      || selectedVersion.snapshots[selectedVersion.snapshots.length - 1];
    if (runId !== selectedRun.id) setRunId(selectedRun.id);
    if (version !== selectedVersion.version) setVersion(selectedVersion.version);
    if (snapshotId !== selectedSnapshot.snapshot_id) setSnapshotId(selectedSnapshot.snapshot_id);
  }, [runs, latestRun, runId, version, snapshotId]);

  if (!latestRun) {
    return <div className="empty-state context-empty">No model context captured yet</div>;
  }
  const selectedRun = runs.find((run) => run.id === runId) || latestRun;
  const selectedVersion = selectedRun.versions.find((item) => item.version === version)
    || selectedRun.versions[selectedRun.versions.length - 1];
  const selectedSnapshot = selectedVersion.snapshots.find((item) => item.snapshot_id === snapshotId)
    || selectedVersion.snapshots[selectedVersion.snapshots.length - 1];
  const stepIndex = selectedVersion.snapshots.findIndex((item) => item.snapshot_id === selectedSnapshot.snapshot_id);

  return (
    <div className="context-inspector">
      <div className="context-toolbar">
        <label>
          <span>Run</span>
          <select value={selectedRun.id} onChange={(event) => { setRunId(event.target.value); setVersion(null); }}>
            {runs.map((run, index) => (
              <option value={run.id} key={run.id}>{`Run ${index + 1} · ${shortId(run.id)}`}</option>
            ))}
          </select>
        </label>
        <div className="context-stepper" aria-label="Context step">
          <button type="button" disabled={stepIndex <= 0} onClick={() => setSnapshotId(selectedVersion.snapshots[stepIndex - 1].snapshot_id)}>‹</button>
          <span>Step {stepIndex + 1}/{selectedVersion.snapshots.length} · {contextSnapshotPhase(selectedSnapshot)}</span>
          <button type="button" disabled={stepIndex >= selectedVersion.snapshots.length - 1} onClick={() => setSnapshotId(selectedVersion.snapshots[stepIndex + 1].snapshot_id)}>›</button>
        </div>
      </div>
      <div className="context-version-strip" aria-label="Context versions">
        {selectedRun.versions.map((item) => (
          <button
            className={item.version === selectedVersion.version ? "context-version active" : "context-version"}
            key={item.version}
            type="button"
            onClick={() => {
              setVersion(item.version);
              setSnapshotId(item.snapshots[item.snapshots.length - 1].snapshot_id);
            }}
          >
            <strong>v{item.version}</strong>
            <span>{item.version === 0 ? "Original" : "Compressed"}</span>
          </button>
        ))}
      </div>
      <div className="context-meta">
        <span>{contextSnapshotPhase(selectedSnapshot)}</span>
        <span>{selectedSnapshot.message_count || 0} messages</span>
        <span>{selectedSnapshot.tool_count || 0} tools</span>
        <span>rev {selectedSnapshot.projection_revision || 0}</span>
      </div>
      <div className="context-variable-list">
        <ContextVariableRow index="S" role="system" value={{ role: "system", content: selectedSnapshot.system_prompt || "" }} />
        {(selectedSnapshot.messages || []).map((message, index) => (
          <ContextVariableRow
            index={String(index)}
            key={`${selectedSnapshot.snapshot_id}-${index}`}
            role={message.role || "message"}
            value={message}
          />
        ))}
      </div>
    </div>
  );
}

function ContextVariableRow({ index, role, value }) {
  const [expanded, setExpanded] = useState(false);
  const detail = safeJsonStringify(value);
  return (
    <div className={`context-variable-row role-${role}${expanded ? " expanded" : ""}`}>
      <button className="context-variable-main" type="button" aria-expanded={expanded} onClick={() => setExpanded((item) => !item)}>
        <span className="context-variable-chevron">{expanded ? "▾" : "▸"}</span>
        <span className="context-variable-index">{index}</span>
        <span className="context-variable-role">{role}</span>
        <span className="context-variable-preview">{contextMessageSummary(value) || "∅"}</span>
      </button>
      {expanded && (
        <div className="context-variable-detail">
          <button type="button" onClick={() => copyContextValue(detail)}>Copy</button>
          <pre>{detail}</pre>
        </div>
      )}
    </div>
  );
}

export function TraceEventRow({ event, expanded = false, tab, onToggle = () => {} }) {
  const details = traceEventDetails(event, tab);
  const content = (
    <>
      <span className="trace-event-heading">
        <strong className="trace-event-title">{eventTitle(event)}</strong>
        {details && <span className="trace-event-chevron" aria-hidden="true">{expanded ? "−" : "+"}</span>}
      </span>
      <span className="trace-event-summary">{eventSummary(event)}</span>
    </>
  );

  return (
    <div className={`trace-event${expanded ? " expanded" : ""}`}>
      <div className={`status-dot ${eventTone(event)}`} aria-hidden="true" />
      <div className="trace-event-content">
        {details ? (
          <button
            className="trace-event-toggle"
            type="button"
            aria-expanded={expanded}
            aria-label={`${expanded ? "Collapse" : "Expand"} ${eventTitle(event)}`}
            onClick={onToggle}
          >
            {content}
          </button>
        ) : (
          <div className="trace-event-text">{content}</div>
        )}
        {expanded && details && <pre className="trace-event-details">{details}</pre>}
      </div>
    </div>
  );
}

export function scrollTraceToLatest(element) {
  if (element) {
    element.scrollTop = element.scrollHeight;
  }
}

export function scrollTranscriptToLatest(element) {
  if (element) {
    element.scrollTop = element.scrollHeight;
  }
}

export function mergeContextSnapshots(...collections) {
  const merged = [];
  const seen = new Set();
  for (const collection of collections) {
    for (const snapshot of Array.isArray(collection) ? collection : []) {
      const id = String(snapshot?.snapshot_id || "");
      if (!id || seen.has(id)) continue;
      seen.add(id);
      merged.push(snapshot);
    }
  }
  return merged;
}

export function toggleSetValue(values, value) {
  const next = new Set(values);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

export function eventPrefixGroups(events) {
  const counts = new Map();
  for (const event of Array.isArray(events) ? events : []) {
    const prefix = eventTypePrefix(event?.type);
    counts.set(prefix, (counts.get(prefix) || 0) + 1);
  }
  return [...counts.entries()]
    .map(([prefix, count]) => ({ prefix, count }))
    .sort((left, right) => left.prefix.localeCompare(right.prefix));
}

export function filterTraceEventsByPrefix(events, prefix) {
  const items = Array.isArray(events) ? events : [];
  if (!prefix || prefix === "all") return items;
  return items.filter((event) => eventTypePrefix(event?.type) === prefix);
}

function eventTypePrefix(type) {
  const value = String(type || "event");
  return value.split(".", 1)[0] || "event";
}

function traceEventKey(event, tab, index) {
  return `${tab}:${event.type || "event"}:${event.id || index}`;
}

export function contextSnapshotsFromEvents(events) {
  return mergeContextSnapshots(
    (Array.isArray(events) ? events : [])
      .filter((event) => event?.type === "lora.context.snapshot")
      .map((event) => event.payload),
  );
}

export function contextSnapshotRuns(snapshots) {
  const runs = [];
  const byRun = new Map();
  for (const snapshot of mergeContextSnapshots(snapshots)) {
    const runId = String(snapshot.case_run_id || "unknown-run");
    let run = byRun.get(runId);
    if (!run) {
      run = { id: runId, versions: [], byVersion: new Map() };
      byRun.set(runId, run);
      runs.push(run);
    }
    const version = Math.max(0, Number(snapshot.compression_version) || 0);
    let group = run.byVersion.get(version);
    if (!group) {
      group = { version, snapshots: [] };
      run.byVersion.set(version, group);
      run.versions.push(group);
    }
    group.snapshots.push(snapshot);
  }
  return runs.map((run) => ({
    id: run.id,
    versions: run.versions.sort((left, right) => left.version - right.version),
  }));
}

export function contextSnapshotPhase(snapshot) {
  return snapshot?.phase === "response" ? "Response" : "Request";
}

function contextMessageSummary(message) {
  const content = String(message?.content || "").trim();
  if (content) return limitText(content.replace(/\s+/g, " "), 180);
  const calls = Array.isArray(message?.tool_calls) ? message.tool_calls : [];
  if (calls.length) return `calls ${calls.map((call) => call.name || "tool").join(", ")}`;
  const results = Array.isArray(message?.results) ? message.results : [];
  if (results.length) return results.map((result) => `${result.name || "tool"}: ${result.status || "result"}`).join(", ");
  return String(message?.kind || "");
}

function copyContextValue(value) {
  globalThis.navigator?.clipboard?.writeText?.(value).catch(() => {});
}

export function SettingsPanel({ settings, disabled, onClose, onSave }) {
  const [draft, setDraft] = useState(() => settingsToDraft(settings));
  const settingsRef = useRef(null);
  const validationError = modelGroupValidationError(draft);

  useEffect(() => {
    const previous = document.activeElement;
    settingsRef.current?.querySelector("button")?.focus();
    return () => previous?.focus?.();
  }, []);

  useEffect(() => {
    setDraft(settingsToDraft(settings));
  }, [settings]);

  function setField(field, value) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function setRoute(index, field, value) {
    setDraft((current) => {
      const routes = current.modelRoutes.map((route, routeIndex) =>
        routeIndex === index ? { ...route, [field]: value } : route,
      );
      const previousId = current.modelRoutes[index]?.id;
      const fallback = field === "id"
        ? current.fallback.map((routeId) => (routeId === previousId ? value : routeId))
        : current.fallback;
      return { ...current, modelRoutes: routes, fallback };
    });
  }

  function addRoute() {
    setDraft((current) => {
      const id = nextRouteId(current.modelRoutes);
      return {
        ...current,
        modelRoutes: [...current.modelRoutes, emptyModelRoute(id)],
        fallback: [...current.fallback, id],
      };
    });
  }

  function removeRoute(index) {
    setDraft((current) => {
      if (current.modelRoutes.length <= 1) return current;
      const removedId = current.modelRoutes[index]?.id;
      return {
        ...current,
        modelRoutes: current.modelRoutes.filter((_route, routeIndex) => routeIndex !== index),
        fallback: current.fallback.filter((routeId) => routeId !== removedId),
      };
    });
  }

  function toggleFallback(routeId) {
    setDraft((current) => ({
      ...current,
      fallback: current.fallback.includes(routeId)
        ? current.fallback.filter((item) => item !== routeId)
        : [...current.fallback, routeId],
    }));
  }

  function moveFallback(routeId, direction) {
    setDraft((current) => {
      const index = current.fallback.indexOf(routeId);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= current.fallback.length) return current;
      const fallback = [...current.fallback];
      [fallback[index], fallback[target]] = [fallback[target], fallback[index]];
      return { ...current, fallback };
    });
  }

  return (
    <div className="settings-backdrop" role="presentation">
      <section className="settings-panel" aria-label="Settings" role="dialog" aria-modal="true" ref={settingsRef} onKeyDown={(event) => {
        if (event.key === "Escape") { event.stopPropagation(); onClose(); }
        if (event.key === "Tab") {
          const items = [...settingsRef.current.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]')].filter((node) => node.getClientRects().length);
          const first = items[0], last = items.at(-1);
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }}>
        <header className="settings-header">
          <div>
            <h2>设置</h2>
            <p>管理模型连接、运行参数与工具权限。</p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close settings" title="Close settings">
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="settings-form">
          <details className="settings-disclosure">
          <summary>项目与 Agent <span>工作目录与配置档案</span></summary>
          <div className="settings-disclosure-body">
          <label>
            <span>Workspace</span>
            <input value={draft.workspaceRoot} onChange={(event) => setField("workspaceRoot", event.target.value)} />
          </label>
          <label>
            <span>Agent</span>
            <input value={draft.agent} onChange={(event) => setField("agent", event.target.value)} />
          </label>
          <label>
            <span>Model group profile</span>
            <input value={draft.profile} onChange={(event) => setField("profile", event.target.value)} />
          </label>
          </div>
          </details>
          <section className="model-group-editor" aria-label="Model group routes">
            <div className="model-group-heading">
              <div>
                <strong>模型连接</strong>
                <span>先配置模型与密钥；失败回退策略可在高级设置中调整。</span>
              </div>
              <button className="route-add" type="button" onClick={addRoute}>
                <Plus aria-hidden="true" /> 添加模型
              </button>
            </div>
            <div className="model-route-list">
              {draft.modelRoutes.map((route, index) => (
                <article className="model-route-card" key={`route-${index}`}>
                  <div className="model-route-title">
                    <span className="route-rank">{String(index + 1).padStart(2, "0")}</span>
                    <strong>{route.id || `route-${index + 1}`}</strong>
                    <button
                      className="route-remove"
                      disabled={draft.modelRoutes.length <= 1}
                      type="button"
                      onClick={() => removeRoute(index)}
                      aria-label={`Remove ${route.id || `route ${index + 1}`}`}
                    >
                      <Trash2 aria-hidden="true" />
                    </button>
                  </div>
                  <div className="model-route-grid">
                    <label><span>模型名称</span><input value={route.model_name} onChange={(event) => setRoute(index, "model_name", event.target.value)} /></label>
                    <label><span>服务地址 · Base URL</span><input value={route.base_url} onChange={(event) => setRoute(index, "base_url", event.target.value)} /></label>
                    <label className="route-secret">
                      <span>API 密钥</span>
                      <input type="password" autoComplete="off" placeholder={route.api_key_source === "missing" ? "尚未配置" : "留空保留已有密钥"} value={route.api_key} onChange={(event) => setRoute(index, "api_key", event.target.value)} />
                    </label>
                    <details className="route-advanced"><summary>高级连接参数</summary><div className="model-route-grid">
                      <label><span>连接标识 · Route ID</span><input value={route.id} onChange={(event) => setRoute(index, "id", event.target.value)} /></label>
                      <label><span>协议适配器 · Provider</span><input value={route.provider} onChange={(event) => setRoute(index, "provider", event.target.value)} /></label>
                      <label><span>密钥环境变量</span><input value={route.api_key_env} onChange={(event) => setRoute(index, "api_key_env", event.target.value)} /></label>
                    </div></details>
                  </div>
                </article>
              ))}
            </div>
          </section>
          <details className="settings-disclosure"><summary>高级运行设置 <span>回退顺序、重试与上下文</span></summary><div className="settings-disclosure-body">
          <section className="fallback-editor" aria-label="Fallback order">
            <div className="model-group-heading">
              <div>
                <strong>Fallback order</strong>
                <span>Top to bottom. Disabled routes remain configured but are not called.</span>
              </div>
            </div>
            {fallbackDisplayRoutes(draft.modelRoutes, draft.fallback).map((route, displayIndex) => {
              const priority = draft.fallback.indexOf(route.id);
              const enabled = priority >= 0;
              return (
                <div className={`fallback-row ${enabled ? "enabled" : ""}`} key={`fallback-${displayIndex}`}>
                  <label className="fallback-toggle">
                    <input type="checkbox" checked={enabled} onChange={() => toggleFallback(route.id)} />
                    <span>{enabled ? priority + 1 : "—"}</span>
                    <strong>{route.id || "Unnamed route"}</strong>
                    <small>{route.model_name || "No model"}</small>
                  </label>
                  <div className="fallback-order-actions">
                    <button type="button" disabled={!enabled || priority === 0} onClick={() => moveFallback(route.id, -1)} aria-label={`Move ${route.id} up`}><ArrowUp aria-hidden="true" /></button>
                    <button type="button" disabled={!enabled || priority === draft.fallback.length - 1} onClick={() => moveFallback(route.id, 1)} aria-label={`Move ${route.id} down`}><ArrowDown aria-hidden="true" /></button>
                  </div>
                </div>
              );
            })}
          </section>
          <div className="retry-grid">
            <label><span>Attempts / route</span><input min="1" type="number" value={draft.retry.max_attempts_per_route} onChange={(event) => setField("retry", { ...draft.retry, max_attempts_per_route: event.target.value })} /></label>
            <label><span>Idle timeout (seconds)</span><input min="0.1" step="0.1" type="number" value={draft.retry.attempt_idle_timeout_seconds} onChange={(event) => setField("retry", { ...draft.retry, attempt_idle_timeout_seconds: event.target.value })} /></label>
          </div>
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
          </div></details>
          <div className="settings-permissions">
          <label>
            <span>Tool permissions / 工具权限</span>
            <select
              value={draft.approvalsEnabled ? "approval" : "full-access"}
              disabled={disabled}
              aria-describedby="tool-permissions-help"
              onChange={(event) => setField("approvalsEnabled", event.target.value === "approval")}
            >
              <option value="approval">Require approval / 逐次审批</option>
              <option value="full-access">Full access / 完全访问</option>
            </select>
          </label>
          <p id="tool-permissions-help">
            完全访问会自动允许所有工具调用；逐次审批会要求确认写入和外部操作（已预授权的工具除外）。
            保存后对所有工作区的新运行生效，正在运行的任务保留原权限。
          </p>
          </div>
          {validationError && <p className="settings-validation" role="alert">{validationError}</p>}
        </div>

        <footer className="settings-actions">
          <button className="plain-action" disabled={disabled} type="button" onClick={onClose}>
            取消
          </button>
          <button className="send" aria-label="Save and Reload" disabled={disabled || Boolean(validationError)} type="button" onClick={() => onSave(draft)}>
            保存并应用
          </button>
        </footer>
      </section>
    </div>
  );
}

export function selectSessionMessages(pendingMessages, historyMessages) {
  return Array.isArray(pendingMessages) && pendingMessages.length > 0
    ? pendingMessages
    : historyMessages;
}

export function appendSessionLiveTraceEvent(cache, sessionId, currentEvents, event, limit = TRACE_RENDER_LIMIT) {
  const events = Array.isArray(currentEvents) ? currentEvents : [];
  if (event?.id && events.some((item) => item.id === event.id)) {
    return events;
  }
  const next = [...events, event];
  const limited = next.length > limit ? next.slice(-limit) : next;
  if (sessionId) {
    cache.set(sessionId, limited);
  }
  return limited;
}

export function historyToMessages(history) {
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
    rendered.push(...renderTurnSegment(segment, `history-${index}`, message.run_timing));
  }
  return rendered.filter(Boolean);
}

function renderTurnSegment(segment, idPrefix, timing = segment.find((message) => message?.run_timing)?.run_timing) {
  let lastToolCallIndex = -1;
  segment.forEach((message, index) => {
    if (String(message?.role || "") === "assistant" && toolCallsFromMessage(message).length > 0) {
      lastToolCallIndex = index;
    }
  });
  let finalAssistantIndex = -1;
  segment.forEach((message, index) => {
    if (
      index > lastToolCallIndex &&
      String(message?.role || "") === "assistant" &&
      cleanContent(String(message?.content || "")).trim() &&
      toolCallsFromMessage(message).length === 0
    ) {
      finalAssistantIndex = index;
    }
  });

  let sections = [];
  let finalContent = "";
  segment.forEach((message, index) => {
    const role = String(message?.role || "");
    const content = cleanContent(String(message?.content || ""));
    const reasoning = cleanContent(String(message?.reasoning_content || ""));
    const toolCalls = toolCallsFromMessage(message);
    if (role === "assistant") {
      if (index === finalAssistantIndex) {
        finalContent = content;
        return;
      }
      if (reasoning) {
        sections = appendTextSection(sections, "Thinking", reasoning);
      }
      if (content) {
        sections = appendTextSection(sections, "Assistant content", content);
      }
      sections = appendToolCallsSection(sections, toolCalls.map(toolCallState));
      return;
    }
    if (role === "tool") {
      const activity = toolResultActivity(message);
      sections = applyToolResultToSections(sections, activity);
    }
  });
  if (sections.length === 0 && !finalContent) {
    return [];
  }
  return [
    {
      id: `${idPrefix}-assistant`,
      role: "assistant",
      content: finalContent,
      status: "success",
      ...runTimingFields(timing),
      sections: finalizeToolSections(sections),
    },
  ];
}

function apiEventToTraceEvent(event) {
  return {
    id: `${event.execution_id || "execution"}-${event.sequence || 0}`,
    type: event.kind || "runtime.event",
    timestamp: "",
    actor: "runtime",
    payload: tracePreviewPayload(event.data || {}),
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

function projectLiveExecutionEvent(setMessages, assistantId, event) {
  setMessages((items) => {
    let changed = false;
    const next = items.map((item) => {
      if (item.id !== assistantId) {
        return item;
      }
      const projected = projectLiveAssistantEvent(item, event);
      changed = changed || projected !== item;
      return projected;
    });
    return changed ? next : items;
  });
}

export function projectLiveAssistantEvent(message, event, now = Date.now()) {
  const kind = String(event?.kind || "");
  const payload = event?.data && typeof event.data === "object" ? event.data : {};
  if (payload.run_timing) {
    message = { ...message, ...runTimingFields(payload.run_timing) };
  }

  if (kind === "model.reasoning.delta") {
    const delta = String(payload.text || "");
    return delta ? { ...message, sections: appendDeltaSection(message.sections, "Thinking", delta) } : message;
  }

  if (kind === "model.text.delta") {
    return { ...message, content: `${message.content || ""}${String(payload.text || "")}` };
  }

  if (kind === "model.tool_call.completed") {
    let sections = Array.isArray(message.sections) ? message.sections : [];
    if (String(message.content || "").trim()) {
      sections = appendTextSection(sections, "Assistant content", message.content);
    }
    const call = toolCallState({
      id: payload.call_id,
      name: payload.name,
      arguments: payload.arguments,
    });
    return { ...message, content: "", sections: appendToolCallsSection(sections, [call]) };
  }

  if (kind === "lora.runtime.message" && payload.role === "tool") {
    const result = toolResultActivity(payload);
    return {
      ...message,
      sections: applyToolResultToSections(message.sections || [], result),
    };
  }

  if (kind === "tool.completed" || kind === "tool.failed" || kind === "tool.cancelled") {
    const failed = kind !== "tool.completed";
    const result = {
      toolCallId: String(payload.call_id || ""),
      content: failed ? String(payload.error_kind || kind.replace("tool.", "")) : "",
      status: failed ? "error" : "success",
    };
    return {
      ...message,
      sections: applyToolResultToSections(message.sections || [], result),
    };
  }

  if (kind === "execution.completed") {
    return {
      ...message,
      content: cleanContent(String(message.content || "")),
      endedAt: message.endedAt ?? now,
      sections: finalizeToolSections(message.sections || []),
      status: "success",
    };
  }

  if (kind === "execution.cancelled") {
    return {
      ...message,
      content: String(payload.reason || "Chat cancelled."),
      endedAt: message.endedAt ?? now,
      sections: finalizeToolSections(message.sections || []),
      status: "error",
    };
  }

  if (kind === "lora.transport.error" || kind === "execution.failed" || kind === "execution.deadline_exceeded") {
    return {
      ...message,
      content: `Error: ${payload.error || payload.message || "chat failed"}`,
      endedAt: message.endedAt ?? now,
      sections: finalizeToolSections(message.sections || []),
      status: "error",
    };
  }

  return message;
}

function visibleActivitySections(message) {
  const sections = Array.isArray(message.sections) ? message.sections : [];
  return sections.filter((section) => !(section.type === "text" && section.title === "Thinking"));
}

function activityLiveStatus(message) {
  if (message.status !== "running") {
    return "";
  }
  const sections = Array.isArray(message.sections) ? message.sections : [];
  const liveSection = latestLiveStatusSection(sections);
  if (!liveSection) {
    return hasVisibleAssistantContent(message) ? "" : "Thinking";
  }
  if (liveSection.type === "tools") {
    return latestToolDescriptionFromSection(liveSection);
  }
  if (liveSection.type === "text" && liveSection.title === "Thinking") {
    return cleanContent(String(liveSection.content || "")) || "Thinking";
  }
  return "";
}

function latestLiveStatusSection(sections) {
  for (let index = sections.length - 1; index >= 0; index -= 1) {
    const section = sections[index];
    if (!section) {
      continue;
    }
    if (section.type === "text" && section.title === "Thinking") {
      return section;
    }
    if (section.type === "tools" && section.status === "running") {
      return section;
    }
    if (section.type === "text" && section.title === "Assistant content") {
      return section;
    }
  }
  return null;
}

function latestToolDescriptionFromSection(section) {
  const calls = Array.isArray(section?.calls) ? section.calls : [];
  for (let callIndex = calls.length - 1; callIndex >= 0; callIndex -= 1) {
    const call = calls[callIndex];
    const description = String(call.description || "").trim() || toolCallDisplayLabel(call);
    if (description) {
      return description;
    }
  }
  return "";
}

function hasVisibleAssistantContent(message) {
  if (cleanContent(String(message.content || "")).trim()) {
    return true;
  }
  return (Array.isArray(message.sections) ? message.sections : []).some(
    (section) =>
      section.type === "text" &&
      section.title === "Assistant content" &&
      cleanContent(String(section.content || "")).trim(),
  );
}

function appendDeltaSection(sections, title, delta) {
  const safeSections = Array.isArray(sections) ? [...sections] : [];
  const last = safeSections[safeSections.length - 1];
  if (last?.type === "text" && last.title === title) {
    safeSections[safeSections.length - 1] = { ...last, content: `${last.content || ""}${delta}` };
    return safeSections;
  }
  return [
    ...safeSections,
    {
      id: `text-${Date.now()}-${Math.random()}`,
      type: "text",
      title,
      content: delta,
    },
  ];
}

function appendTextSection(sections, title, content) {
  const value = String(content || "").trim();
  if (!value) {
    return Array.isArray(sections) ? sections : [];
  }
  return [
    ...(Array.isArray(sections) ? sections : []),
    {
      id: `text-${Date.now()}-${Math.random()}`,
      type: "text",
      title,
      content: value,
    },
  ];
}

function appendToolCallsSection(sections, calls) {
  const safeCalls = Array.isArray(calls) ? calls.filter(Boolean) : [];
  if (!safeCalls.length) {
    return Array.isArray(sections) ? sections : [];
  }
  const nextSections = Array.isArray(sections) ? [...sections] : [];
  let toolSectionIndex = nextSections.length - 1;
  while (
    toolSectionIndex >= 0 &&
    nextSections[toolSectionIndex]?.type === "text" &&
    nextSections[toolSectionIndex]?.title === "Thinking"
  ) {
    toolSectionIndex -= 1;
  }
  const previousToolSection = nextSections[toolSectionIndex];
  if (previousToolSection?.type === "tools") {
    const mergedSection = {
      ...previousToolSection,
      status: "running",
      calls: mergeToolCalls(previousToolSection.calls || [], safeCalls),
    };
    nextSections.splice(toolSectionIndex, 1);
    nextSections.push(mergedSection);
    return nextSections;
  }
  nextSections.push({
    id: `tools-${Date.now()}-${Math.random()}`,
    type: "tools",
    status: "running",
    calls: safeCalls,
  });
  return nextSections;
}

function applyToolResultToSections(sections, result) {
  const nextSections = Array.isArray(sections)
    ? sections.map((section) =>
        section.type === "tools" ? { ...section, calls: [...(section.calls || [])] } : section,
      )
    : [];
  for (let index = nextSections.length - 1; index >= 0; index -= 1) {
    const section = nextSections[index];
    if (section.type !== "tools") {
      continue;
    }
    const calls = applyToolResult(section.calls || [], result);
    if (calls !== section.calls) {
      nextSections[index] = {
        ...section,
        calls,
        status: calls.some((call) => call.status === "running") ? "running" : "done",
      };
      return nextSections;
    }
  }
  return appendToolCallsSection(nextSections, applyToolResult([], result));
}

function finalizeToolSections(sections) {
  return (Array.isArray(sections) ? sections : []).map((section) => {
    if (section.type !== "tools") {
      return section;
    }
    const calls = (section.calls || []).map((call) =>
      call.status === "running" ? { ...call, status: "success" } : call,
    );
    const hasError = calls.some((call) => call.status === "error");
    return { ...section, calls, status: hasError ? "error" : "done" };
  });
}

function toolGroupTitle(section) {
  const calls = Array.isArray(section.calls) ? section.calls : [];
  const count = calls.length;
  const verb = section.status === "running" ? "Executing" : "Executed";
  return `${verb} ${count} tool call${count === 1 ? "" : "s"}`;
}

function traceTabEvents(tab, events) {
  if (tab === "Tools") {
    return traceToolEvents(events);
  }
  if (tab === "Changes") {
    return events.filter((event) => {
      const type = String(event.type || "");
      return type.startsWith("file.") || type === "diff.created";
    });
  }
  return events;
}

export function traceToolEvents(events) {
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
      const callId = String(payload.model_tool_call_id || payload.tool_call_id || payload.id || event.id || "");
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
      const callId = String(payload.model_tool_call_id || payload.tool_call_id || "");
      const status = String(payload.status || "success") === "error" || payload.error ? "error" : "success";
      const patch = {
        tool_call_id: callId,
        result_event: event,
        has_result: true,
        result: stringifyDetail(payload.error || payload.preview || payload.result || payload.content || ""),
        result_ref: payload.result_ref || "",
        result_size: payload.result_size || 0,
        truncated: Boolean(payload.truncated),
        status,
      };
      if (payload.tool_name) {
        patch.tool_name = String(payload.tool_name);
      }
      upsert(callId, patch);
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
    ["profile", settings.profile],
    ["routes", settings.routes],
    ["fallback", settings.fallback],
    ["max_steps", settings.max_steps],
    ["context_window", settings.context_window],
    ["compression_trigger", compressionTriggerLabel(settings.context_window, settings.context_compression_trigger_ratio)],
    ["user_lora_root", settings.user_lora_root],
  ];
}

export function formatConfigValue(key, value) {
  if (key === "routes") {
    const routes = Array.isArray(value) ? value : [];
    if (routes.length === 0) {
      return "-";
    }
    return routes.map((route, index) => {
      const id = String(route?.id || `route-${index + 1}`);
      const provider = String(route?.provider || "unknown");
      const model = String(route?.model_name || route?.model || "unknown");
      return `${id}  ${provider} / ${model}`;
    }).join("\n");
  }
  if (key === "fallback") {
    return Array.isArray(value) && value.length ? value.join(" → ") : "-";
  }
  if (value === undefined || value === null || value === "") {
    return "-";
  }
  return String(value);
}

function flattenSessionGroups(groups) {
  return groups.flatMap((group) => group.sessions || []);
}

export function firstSessionIdInScope(groups, activeScopeId) {
  const activeGroup = (groups || []).find((group) => group.scope?.scope_id === activeScopeId);
  return activeGroup?.sessions?.[0]?.session_id || "";
}

function latestItems(items, limit) {
  if (!Array.isArray(items) || items.length <= limit) {
    return items;
  }
  return items.slice(items.length - limit);
}

function applyRunningSessionStatus(groups, runningSessionIds) {
  if (!Object.keys(runningSessionIds).length) {
    return groups;
  }
  return groups.map((group) => ({
    ...group,
    sessions: (group.sessions || []).map((session) =>
      runningSessionIds[session.session_id] ? { ...session, last_case_run_status: "running" } : session,
    ),
  }));
}

function omitKey(items, key) {
  if (!items[key]) {
    return items;
  }
  const { [key]: _removed, ...rest } = items;
  return rest;
}

function scopeIdFromWorkspace(workspaceRoot) {
  return workspaceRoot ? `project:${workspaceRoot}` : "";
}

function compactLayoutMatches() {
  return Boolean(globalThis.window?.matchMedia?.(COMPACT_LAYOUT_QUERY).matches);
}

function settingsToDraft(settings) {
  return {
    approvalsEnabled: settings.approvals_enabled !== false,
    workspaceRoot: settings.workspace_root || "",
    agent: settings.agent || "",
    profile: settings.profile || "default",
    modelRoutes: (settings.routes || []).map((route) => ({ ...route, api_key: "" })),
    fallback: Array.isArray(settings.fallback) && settings.fallback.length
      ? [...settings.fallback]
      : (settings.routes || []).map((route) => route.id),
    retry: {
      max_attempts_per_route: settings.retry?.max_attempts_per_route ?? 2,
      attempt_idle_timeout_seconds: settings.retry?.attempt_idle_timeout_seconds ?? 60,
      backoff_initial: settings.retry?.backoff_initial ?? 0.5,
      backoff_maximum: settings.retry?.backoff_maximum ?? 4,
      backoff_multiplier: settings.retry?.backoff_multiplier ?? 2,
    },
    maxSteps: Number.isFinite(settings.max_steps) ? settings.max_steps : -1,
    contextWindow: Number.isFinite(settings.context_window) ? String(settings.context_window) : "",
    apiKey: "",
  };
}

function emptyModelRoute(id) {
  return {
    id,
    provider: "openai",
    model_name: "",
    base_url: "https://api.openai.com/v1",
    api_key_env: `${id.toUpperCase().replace(/[^A-Z0-9]+/g, "_")}_API_KEY`,
    api_key_source: "missing",
    api_key: "",
  };
}

function nextRouteId(routes) {
  const used = new Set(routes.map((route) => route.id));
  let index = routes.length + 1;
  while (used.has(`route-${index}`)) index += 1;
  return `route-${index}`;
}

function fallbackDisplayRoutes(routes, fallback) {
  const ordered = fallback.map((routeId) => routes.find((route) => route.id === routeId)).filter(Boolean);
  return [...ordered, ...routes.filter((route) => !fallback.includes(route.id))];
}

export function modelGroupValidationError(draft) {
  if (!draft.profile?.trim()) return "Model group profile is required.";
  if (!Array.isArray(draft.modelRoutes) || draft.modelRoutes.length === 0) return "Add at least one model route.";
  const required = ["id", "provider", "model_name", "base_url", "api_key_env"];
  for (const [index, route] of draft.modelRoutes.entries()) {
    if (required.some((field) => !String(route[field] || "").trim())) return `Route ${index + 1} has incomplete fields.`;
  }
  const ids = draft.modelRoutes.map((route) => route.id.trim());
  if (new Set(ids).size !== ids.length) return "Route IDs must be unique.";
  if (!Array.isArray(draft.fallback) || draft.fallback.length === 0) return "Enable at least one route in the fallback order.";
  return "";
}

export function settingsForSave(draft, settings) {
  const workspaceChanged = draft.workspaceRoot.trim() !== (settings.workspace_root || "").trim();
  const keptPreviousAgent = draft.agent.trim() === (settings.agent || "").trim();
  return workspaceChanged && keptPreviousAgent ? { ...draft, agent: "" } : draft;
}

export function shouldSubmitComposer(event) {
  return event.key === "Enter" && !event.shiftKey && !event.nativeEvent?.isComposing;
}

export async function initializeWorkbench(
  refresh,
  {
    attempts = 20,
    retryDelay = () => new Promise((resolve) => globalThis.setTimeout(resolve, 250)),
  } = {},
) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await refresh();
    } catch (err) {
      lastError = err;
      if (!isTransientFetchError(err) || attempt === attempts - 1) {
        throw err;
      }
      await retryDelay();
    }
  }
  throw lastError;
}

function isTransientFetchError(error) {
  const message = String(error?.message || error).toLowerCase();
  return message.includes("failed to fetch") || message.includes("fetch failed") || message.includes("networkerror");
}

function primaryModel(settings) {
  return settings.routes?.[0]?.model_name || "unconfigured model";
}

function formatContextWindow(value) {
  return Number.isFinite(value) ? `${value} tokens` : "unset";
}

function compressionTriggerLabel(contextWindow, triggerRatio) {
  return Number.isFinite(contextWindow) && Number.isFinite(triggerRatio)
    ? `${Math.floor(contextWindow * triggerRatio)} tokens`
    : "disabled";
}

function cleanContent(content) {
  const match = content.match(/<user-message>([\s\S]*?)<\/user-message>/);
  return (match ? match[1] : content).trim();
}

function cleanSessionTitle(title) {
  return String(title || "").replace(/\s+/g, " ").trim();
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
    resultRef: "",
    resultSize: 0,
    truncated: false,
    status: "running",
  };
}

function mergeToolCalls(current, nextCalls) {
  const merged = Array.isArray(current) ? [...current] : [];
  const safeNextCalls = Array.isArray(nextCalls) ? nextCalls : [];
  for (const call of safeNextCalls) {
    const index = call.id ? merged.findIndex((item) => item.id === call.id) : -1;
    if (index >= 0) {
      merged[index] = {
        ...merged[index],
        ...call,
        result: merged[index].result || call.result || "",
        resultRef: merged[index].resultRef || call.resultRef || "",
      };
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
    resultRef: result.resultRef || "",
    resultSize: result.resultSize || 0,
    truncated: Boolean(result.truncated),
    status: result.status,
  };
  if (result.toolName) {
    patch.name = result.toolName;
  }
  if (index >= 0) {
    merged[index] = { ...merged[index], ...patch };
    return merged;
  }
  const fallbackIndex = findLastRunningToolCallIndex(merged);
  if (fallbackIndex >= 0) {
    merged[fallbackIndex] = { ...merged[fallbackIndex], ...patch };
    return merged;
  }
  if (result.toolCallId) {
    merged.push({
      id: result.toolCallId,
      name: result.toolName || "tool",
      description: "",
      arguments: "",
      ...patch,
    });
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

function toolCallDisplayLabel(call) {
  const action = toolActionLabel(call.name);
  const target = shortToolTarget(call.name, call.arguments);
  return target ? `${action} ${target}` : action;
}

function toolStatusLabel(status) {
  const kind = statusKind(status);
  if (kind === "error") {
    return "Error";
  }
  if (kind === "success") {
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

function stringifyFullDetail(value) {
  return typeof value === "string" ? value : safeJsonStringify(value);
}

function activityTone(status) {
  const kind = statusKind(status);
  if (kind === "error") {
    return "error";
  }
  if (kind === "running") {
    return "warning";
  }
  if (kind === "success") {
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

export function eventSummary(event) {
  const payload = event.payload || {};
  if (event.type === "conversation.tool_message") {
    const results = conversationToolMessageResults(payload);
    if (results.length) {
      return results
        .map((result) => `${String(result.name || "tool")}  ${conversationToolResultStatus(result)}`)
        .join("  ·  ");
    }
    return "No tool results";
  }
  if (payload.trace_tool) {
    const status = String(payload.status || "running");
    const parts = [status === "error" ? "失败" : status === "success" ? "已完成" : "运行中"];
    const target = shortToolTarget(payload.tool_name, payload.arguments);
    if (target) {
      parts.push(target);
    }
    return parts.join("  ");
  }
  if (isFileTraceEvent(event)) {
    return fileEventPath(event) || "Path unavailable";
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

export function eventTitle(event) {
  const payload = event.payload || {};
  if (payload.trace_tool) {
    return toolActionLabel(payload.tool_name);
  }
  if (isFileTraceEvent(event)) {
    return fileEventAction(event.type);
  }
  return event.type || "event";
}

export function traceEventDetails(event, tab) {
  const payload = event.payload || {};
  if (tab === "Tools" && payload.trace_tool) {
    const sections = [];
    if (payload.arguments) {
      sections.push(`Arguments\n${payload.arguments}`);
    }
    if (payload.result) {
      sections.push(`${payload.status === "error" ? "Error" : "Result"}\n${payload.result}`);
    }
    return sections.join("\n\n");
  }
  if (tab === "Changes" && isFileTraceEvent(event)) {
    return safeJsonStringify(payload);
  }
  if (tab === "Events") {
    if (event.type === "conversation.tool_message") {
      return conversationToolMessageDetails(payload);
    }
    const content = typeof payload.content === "string" ? payload.content : "";
    return content || safeJsonStringify(payload);
  }
  return "";
}

function conversationToolMessageResults(payload) {
  const results = Array.isArray(payload?.results) ? payload.results : [];
  return results.filter((result) => result && typeof result === "object");
}

function conversationToolResultEnvelope(result) {
  return parseJsonObject(result?.output);
}

function conversationToolResultStatus(result) {
  const envelope = conversationToolResultEnvelope(result);
  return String(envelope.status || result?.status || (envelope.error || result?.error ? "error" : "result"));
}

function conversationToolResultValue(result) {
  const envelope = conversationToolResultEnvelope(result);
  let value;
  if (envelope.error) {
    value = envelope.error;
  } else if (Object.prototype.hasOwnProperty.call(envelope, "result")) {
    value = envelope.result;
  } else {
    value = result?.error || result?.output || "";
  }
  const nested = parseJsonObject(value);
  if (nested.error) {
    return nested.error;
  }
  if (Object.prototype.hasOwnProperty.call(nested, "result")) {
    return nested.result;
  }
  return value;
}

function conversationToolMessageDetails(payload) {
  const results = conversationToolMessageResults(payload);
  if (!results.length) {
    return "No tool results";
  }
  return results.map((result) => {
    const name = String(result.name || "tool");
    const status = conversationToolResultStatus(result);
    const callId = String(result.call_id || "");
    const heading = [name, status, callId].filter(Boolean).join("  ·  ");
    return `${heading}\n${stringifyDetail(conversationToolResultValue(result))}`;
  }).join("\n\n");
}

function isFileTraceEvent(event) {
  const type = String(event?.type || "");
  return type.startsWith("file.") || type === "diff.created";
}

function fileEventPath(event) {
  const payload = event?.payload || {};
  return String(payload.path || payload.file_path || payload.file || payload.target || "").trim();
}

function fileEventAction(type) {
  const actions = {
    "file.read": "Read file",
    "file.write": "Wrote file",
    "file.edit": "Edited file",
    "file.delete": "Deleted file",
    "diff.created": "Created diff",
  };
  return actions[String(type || "")] || String(type || "File event");
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
  const kind = statusKind(status);
  if (kind === "running") {
    return "warning";
  }
  if (kind === "error") {
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

function sessionStatusKind(status) {
  const kind = statusKind(status);
  return kind === "running" || kind === "error" ? kind : "success";
}

function statusKind(status) {
  const value = String(status || "").toLowerCase();
  if (value.includes("error") || value.includes("fail")) return "error";
  if (value.includes("run") || value.includes("live") || value.includes("load")) return "running";
  if (value.includes("success") || value.includes("done") || value.includes("pass")) return "success";
  return "ready";
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
