import React from "react";

export function RuntimeSettings({ draft, setField, activeSection, disabled }) {
  return <>
          <section hidden={activeSection !== "runtime"} className="settings-disclosure"><h4>运行参数</h4><div className="settings-disclosure-body">

          <label>
            <span>最大执行步数（-1 表示不限）</span>
            <input
              type="number"
              value={draft.maxSteps}
              onChange={(event) => setField("maxSteps", Number(event.target.value))}
            />
          </label>
          <label>
            <span>上下文窗口（tokens）</span>
            <input
              min="1"
              placeholder="留空使用模型默认值"
              type="number"
              value={draft.contextWindow}
              onChange={(event) => setField("contextWindow", event.target.value)}
            />
          </label>
          </div></section>
          <div hidden={activeSection !== "runtime"} className="settings-permissions">
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
  </>;
}
