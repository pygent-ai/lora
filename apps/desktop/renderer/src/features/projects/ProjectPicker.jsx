import React, { useEffect, useRef, useState } from "react";
import { Check, FolderOpen, Search, X } from "lucide-react";
import { cleanProjectPath, isAbsoluteProjectPath, projectChoices, projectPathKey } from "./projectPaths.js";

export function ProjectPicker({ projects, currentPath, disabled, onSelect, onClose }) {
  const dialogRef = useRef(null);
  const inputRef = useRef(null);
  const busyRef = useRef(false);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const chooseDirectory = globalThis.window?.loraDesktop?.chooseProjectDirectory;
  const choices = projectChoices(projects, currentPath, query);
  const path = cleanProjectPath(query);
  const blocked = busy || disabled;

  useEffect(() => {
    const trigger = document.activeElement;
    const dialog = dialogRef.current;
    dialog.showModal();
    inputRef.current.focus();
    return () => { dialog.close(); trigger?.focus?.(); };
  }, []);

  function close() {
    dialogRef.current.close();
    onClose();
  }

  async function select(workspaceRoot) {
    if (busyRef.current || disabled) return;
    if (projectPathKey(workspaceRoot) === projectPathKey(currentPath)) {
      close();
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await onSelect(cleanProjectPath(workspaceRoot));
      close();
    } catch (err) {
      setError(projectErrorMessage(err));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function browse() {
    if (busyRef.current || disabled) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    let selected;
    try {
      selected = await chooseDirectory(isAbsoluteProjectPath(path) ? path : currentPath);
    } catch (err) {
      setError(projectErrorMessage(err));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
    if (selected) {
      setQuery(selected);
      await select(selected);
    }
  }

  return (
    <dialog ref={dialogRef} className="project-picker" aria-labelledby="project-picker-title"
      aria-describedby="project-picker-description" aria-busy={busy}
      onCancel={(event) => { event.preventDefault(); if (!busy) close(); }}>
      <header className="project-picker-header">
        <div><h2 id="project-picker-title">打开项目</h2>
          <p id="project-picker-description">继续最近的项目，或选择一个本地文件夹。</p></div>
        <button className="icon-button" aria-label="Close project picker" disabled={busy} onClick={close}><X aria-hidden="true" /></button>
      </header>
      <form onSubmit={(event) => {
        event.preventDefault();
        if (isAbsoluteProjectPath(path)) void select(path);
        else if (query.trim() && choices.length === 1) void select(choices[0].workspace_root);
      }}>
        <label className="project-search"><Search aria-hidden="true" />
          <input ref={inputRef} aria-label="Search projects or enter an absolute path" autoComplete="off" spellCheck={false}
            placeholder="搜索项目，或粘贴文件夹路径" value={query} disabled={blocked}
            onChange={(event) => { setQuery(event.target.value); setError(""); }} /></label>
        <div className="project-picker-actions">
          {typeof chooseDirectory === "function" && <button type="button" className="route-add" disabled={blocked} onClick={browse}>
            <FolderOpen aria-hidden="true" /> 浏览文件夹</button>}
          <button className="route-add project-open" type="submit" disabled={blocked || !isAbsoluteProjectPath(path)}>打开项目</button>
        </div>
      </form>
      <div className="section-label">最近项目</div>
      <div className="project-picker-list">
        {choices.map((project) => {
          const root = project.workspace_root;
          const current = projectPathKey(root) === projectPathKey(currentPath);
          return <button type="button" className={`project-choice${current ? " current" : ""}`} key={projectPathKey(root)}
            disabled={blocked} title={root} onClick={() => select(root)} aria-current={current ? "true" : undefined}>
            <FolderOpen aria-hidden="true" /><span className="project-choice-copy">
              <strong>{project.label || root.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || root}</strong><span>{root}</span>
            </span>{current && <span className="project-current"><Check aria-hidden="true" />当前项目</span>}
          </button>;
        })}
        {choices.length === 0 && <p className="empty-state">{query ? "没有匹配的项目，可粘贴文件夹的完整路径打开。" : "还没有最近项目，选择一个文件夹开始。"}</p>}
      </div>
      <footer className="project-picker-footer">
        {error ? <p className="project-picker-error" role="alert">{error}</p> :
          <p role="status">{busy ? "正在打开项目…" : disabled ? "当前任务结束后即可切换项目。" : <><span>选择项目即可切换</span><span className="project-key-hints"><kbd>Enter</kbd> 打开 <kbd>Esc</kbd> 关闭</span></>}</p>}
      </footer>
    </dialog>
  );
}

function projectErrorMessage(error) {
  const message = error.message || String(error);
  try {
    const body = JSON.parse(message);
    return typeof body.detail === "string" ? body.detail : message;
  } catch {
    return message;
  }
}
