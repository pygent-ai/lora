import React from "react";

export function WorkspaceSettings({ draft, setField, activeSection }) {
  return <>
          <section className="settings-disclosure settings-workspace" hidden={activeSection !== "workspace"}>
          <h4>项目默认值</h4>
          <div className="settings-disclosure-body">
          <label>
            <span>工作目录</span>
            <input value={draft.workspaceRoot} onChange={(event) => setField("workspaceRoot", event.target.value)} />
          </label>
          <label>
            <span>Agent</span>
            <input value={draft.agent} onChange={(event) => setField("agent", event.target.value)} />
          </label>
          </div>
          </section>
  </>;
}
