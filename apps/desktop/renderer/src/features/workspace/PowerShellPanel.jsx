import React, { useEffect, useRef, useState } from "react";
import { RotateCcw, TerminalSquare } from "lucide-react";

export function PowerShellPanel({ api, scopeId, workspaceRoot }) {
  const [command, setCommand] = useState("");
  const [history, setHistory] = useState([]);
  const [cwd, setCwd] = useState(workspaceRoot || "");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const outputRef = useRef(null);
  const inputRef = useRef(null);
  const historyIndexRef = useRef(-1);

  useEffect(() => {
    let cancelled = false;
    setCommand("");
    setHistory([]);
    setCwd(workspaceRoot || "");
    setError("");
    historyIndexRef.current = -1;
    if (scopeId?.startsWith("project:")) {
      api.resetTerminal(scopeId).catch((err) => {
        if (!cancelled) setError(String(err?.message || err));
      });
    }
    return () => { cancelled = true; };
  }, [api, scopeId, workspaceRoot]);

  useEffect(() => {
    const element = outputRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [history, running]);

  async function runCommand(event) {
    event?.preventDefault();
    const value = command.trim();
    if (!value || running || !scopeId) return;
    setCommand("");
    setRunning(true);
    setError("");
    const promptCwd = cwd;
    try {
      const result = await api.executeTerminalCommand(scopeId, value);
      setHistory((current) => [...current, { command: value, promptCwd, ...result }]);
      setCwd(result.cwd || cwd);
    } catch (err) {
      const message = String(err?.message || err);
      setHistory((current) => [...current, { command: value, promptCwd, output: message, exit_code: 1, cwd }]);
      setError(message);
    } finally {
      setRunning(false);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }

  function navigateHistory(event) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const commands = history.map((item) => item.command);
    if (commands.length === 0) return;
    if (event.key === "ArrowUp") {
      historyIndexRef.current = historyIndexRef.current < 0
        ? commands.length - 1
        : Math.max(0, historyIndexRef.current - 1);
      setCommand(commands[historyIndexRef.current]);
    } else if (historyIndexRef.current >= 0) {
      historyIndexRef.current += 1;
      if (historyIndexRef.current >= commands.length) {
        historyIndexRef.current = -1;
        setCommand("");
      } else {
        setCommand(commands[historyIndexRef.current]);
      }
    }
  }

  async function restart() {
    if (!scopeId || running) return;
    setRunning(true);
    setError("");
    try {
      await api.resetTerminal(scopeId);
      setHistory([]);
      setCwd(workspaceRoot || "");
      historyIndexRef.current = -1;
    } catch (err) {
      setError(String(err?.message || err));
    } finally {
      setRunning(false);
    }
  }

  if (!scopeId?.startsWith("project:")) {
    return <div className="workspace-empty"><TerminalSquare aria-hidden="true" /><strong>Select a project</strong><span>PowerShell is available for project chats.</span></div>;
  }

  return (
    <div className="powershell-panel">
      <div className="terminal-toolbar">
        <span title={cwd}>{cwd}</span>
        <button type="button" disabled={running} title="Restart PowerShell" onClick={restart}><RotateCcw aria-hidden="true" />Restart</button>
      </div>
      <div className="terminal-output" ref={outputRef} onClick={() => inputRef.current?.focus()}>
        <div className="terminal-banner">Windows PowerShell · {workspaceRoot}</div>
        <div role="log" aria-live="polite">
          {history.map((item, index) => (
            <div className="terminal-entry" key={`${index}-${item.command}`}>
              <div className="terminal-command"><span>PS {item.promptCwd || workspaceRoot}&gt;</span> {item.command}</div>
              {item.output && <pre>{item.output}</pre>}
              {item.exit_code !== 0 && <div className="terminal-exit">Exit code {item.exit_code}</div>}
            </div>
          ))}
          {running && <div className="terminal-running">Running…</div>}
        </div>
        <form className="terminal-input-row" onSubmit={runCommand}>
          <label htmlFor="powershell-command">PS {cwd}&gt;</label>
          <input id="powershell-command" ref={inputRef} autoCapitalize="off" autoComplete="off" spellCheck="false"
            value={command} disabled={running} aria-label="PowerShell command"
            onChange={(event) => { setCommand(event.target.value); historyIndexRef.current = -1; }}
            onKeyDown={navigateHistory} />
        </form>
      </div>
      {error && <div className="sr-only" role="alert">{error}</div>}
    </div>
  );
}
