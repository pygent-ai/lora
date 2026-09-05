import React, { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, FileText, Folder, X } from "lucide-react";

export function FileExplorer({ api, scopeId, workspaceRoot }) {
  const [entries, setEntries] = useState([]);
  const [tabs, setTabs] = useState([]);
  const [activePath, setActivePath] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setEntries([]);
    setTabs([]);
    setActivePath("");
    setError("");
    if (!scopeId?.startsWith("project:")) return undefined;
    api.listWorkspaceEntries(scopeId).then((response) => {
      if (!cancelled) setEntries(response.entries || []);
    }).catch((err) => {
      if (!cancelled) setError(String(err?.message || err));
    });
    return () => { cancelled = true; };
  }, [api, scopeId]);

  async function openFile(path) {
    setError("");
    try {
      const file = await api.readWorkspaceFile(scopeId, path);
      setTabs((current) => [...current.filter((item) => item.path !== path), file]);
      setActivePath(path);
    } catch (err) {
      setError(String(err?.message || err));
    }
  }

  function closeFile(path) {
    setTabs((current) => {
      const index = current.findIndex((item) => item.path === path);
      const next = current.filter((item) => item.path !== path);
      if (activePath === path) {
        setActivePath(next[Math.min(index, next.length - 1)]?.path || "");
      }
      return next;
    });
  }

  if (!scopeId?.startsWith("project:")) {
    return <div className="workspace-empty"><Folder aria-hidden="true" /><strong>Select a project</strong><span>Files are available for project chats.</span></div>;
  }

  const activeFile = tabs.find((item) => item.path === activePath) || null;
  return (
    <div className="file-explorer">
      <div className="file-tree-pane">
        <div className="workspace-root" title={workspaceRoot}>{workspaceRoot || "Project files"}</div>
        {error && <div className="workspace-error" role="alert">{error}</div>}
        <div className="file-tree" role="tree" aria-label="Project files">
          {entries.map((entry) => (
            <FileTreeEntry api={api} entry={entry} key={entry.path} level={1} scopeId={scopeId} onOpen={openFile} />
          ))}
          {!error && entries.length === 0 && <div className="empty-state compact">Empty project</div>}
        </div>
      </div>
      <div className="file-preview-pane">
        <div className="file-tabs" role="tablist" aria-label="Open files">
          {tabs.map((file) => (
            <div className={file.path === activePath ? "file-tab active" : "file-tab"} key={file.path}>
              <button role="tab" aria-selected={file.path === activePath} title={file.path} type="button" onClick={() => setActivePath(file.path)}>
                {fileName(file.path)}
              </button>
              <button className="file-tab-close" aria-label={`Close ${file.path}`} type="button" onClick={() => closeFile(file.path)}><X aria-hidden="true" /></button>
            </div>
          ))}
        </div>
        {activeFile ? (
          <pre className="file-preview" aria-label={`Contents of ${activeFile.path}`}><code>{activeFile.content}</code></pre>
        ) : (
          <div className="workspace-empty compact"><FileText aria-hidden="true" /><span>Double-click a file to open it</span></div>
        )}
      </div>
    </div>
  );
}

function FileTreeEntry({ api, entry, level, scopeId, onOpen }) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState(null);
  const [error, setError] = useState("");
  const isDirectory = entry.kind === "directory";

  async function toggleDirectory() {
    if (!isDirectory) return;
    if (!expanded && children === null) {
      try {
        const response = await api.listWorkspaceEntries(scopeId, entry.path);
        setChildren(response.entries || []);
      } catch (err) {
        setError(String(err?.message || err));
      }
    }
    setExpanded((value) => !value);
  }

  function handleKeyDown(event) {
    if (event.key === "Enter") {
      event.preventDefault();
      if (isDirectory) toggleDirectory();
      else onOpen(entry.path);
    } else if (isDirectory && event.key === "ArrowRight" && !expanded) {
      event.preventDefault();
      toggleDirectory();
    } else if (isDirectory && event.key === "ArrowLeft" && expanded) {
      event.preventDefault();
      setExpanded(false);
    }
  }

  return (
    <div role="none">
      <button className="file-tree-row" type="button" role="treeitem" aria-expanded={isDirectory ? expanded : undefined}
          style={{ paddingLeft: `${8 + level * 14}px` }} title={entry.path}
          onClick={isDirectory ? toggleDirectory : undefined}
          onDoubleClick={isDirectory ? undefined : () => onOpen(entry.path)} onKeyDown={handleKeyDown}>
        {isDirectory ? (expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />) : <span className="file-tree-spacer" />}
        {isDirectory ? <Folder aria-hidden="true" /> : <FileText aria-hidden="true" />}
        <span>{entry.name}</span>
      </button>
      {error && <div className="workspace-error tree-error">{error}</div>}
      {expanded && (children || []).map((child) => (
        <FileTreeEntry api={api} entry={child} key={child.path} level={level + 1} scopeId={scopeId} onOpen={onOpen} />
      ))}
    </div>
  );
}

function fileName(path) {
  return String(path || "").split("/").pop() || path;
}
