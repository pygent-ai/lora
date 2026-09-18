import React, { useEffect, useRef, useState } from "react";
import { ArrowLeft, Settings as SettingsIcon } from "lucide-react";
import { useSettingsDraft } from "./useSettingsDraft.js";
import { modelGroupValidationError, settingsToDraft } from "./settingsModel.js";
import "./settings.css";
import { WorkspaceSettings } from "./WorkspaceSettings.jsx";
import { ConnectionSettings } from "./ConnectionSettings.jsx";
import { ModelSettings } from "./ModelSettings.jsx";
import { GroupSettings } from "./GroupSettings.jsx";
import { RuntimeSettings } from "./RuntimeSettings.jsx";

export function SettingsPage({ settings, disabled, onClose, onSave, api }) {

  const [activeSection, setActiveSection] = useState("model-config");
  const [confirmClose, setConfirmClose] = useState(false);

  const settingsRef = useRef(null);

  const controller = useSettingsDraft(settings, api, disabled);
  const { draft } = controller;
  const dirty = JSON.stringify(draft) !== JSON.stringify(settingsToDraft(settings));
  const sections = [
    ["model-config", "模型配置", "服务连接、模型与模型组"],
    ["workspace", "项目与 Agent", "工作目录与执行角色"],
    ["runtime", "运行与权限", "执行步数、上下文与审批"],
  ];
  const currentSection = sections.find(([id]) => id === activeSection);
  function requestClose() {
    if (disabled) return;
    if (dirty) setConfirmClose(true);
    else onClose();
  }
  function navigateSection(id) {
    setActiveSection(id);
    setConfirmClose(false);
    settingsRef.current?.querySelector(".settings-form")?.scrollTo(0, 0);
  }
  const missingCredential = Object.entries(draft.connections).find(([, connection]) => !connection.credential?.none && connection.credential_source === "missing" && !draft.credentialValues[connection.credential?.env]?.trim());
  const validationError = modelGroupValidationError(draft) || (missingCredential ? `连接 ${missingCredential[0]} 缺少 API Key，请在简洁配置中填写。` : "");
  const validationSection = "model-config";

  useEffect(() => {
    const previous = document.activeElement;
    settingsRef.current?.querySelector("button")?.focus();
    return () => previous?.focus?.();
  }, []);

  return (
    <div className="settings-page">
      <section className="settings-panel" aria-label="Settings" role="region" ref={settingsRef} onKeyDown={(event) => {
        if (event.key === "Escape") { event.stopPropagation(); confirmClose ? setConfirmClose(false) : requestClose(); }

      }}>
        <header className="settings-header">
          <div>
            <h2>设置</h2>
          </div>
          <button className="plain-action settings-back" type="button" disabled={disabled} onClick={requestClose} aria-label="Close settings" title="Close settings">
            <ArrowLeft size={18} aria-hidden="true" /> 返回工作区
          </button>
        </header>

        <div className="settings-layout">
          <nav className="settings-nav" aria-label="配置分类">
            <span className="settings-nav-label">工作区设置</span>
            {sections.map(([id, title, description, count], index) => <button type="button" key={id} aria-current={activeSection === id ? "page" : undefined} onClick={() => navigateSection(id)}>
              <span className="settings-nav-icon">{index < 3 ? String(index + 1).padStart(2, "0") : <SettingsIcon size={16} />}</span>
              <span><strong>{title}</strong><small>{description}</small></span>
              {count !== undefined && <span className="settings-nav-count">{count}</span>}
            </button>)}
          </nav>
          <div className="settings-form">
            <div className="settings-page-heading"><h3>{currentSection[1]}</h3></div>
            <fieldset className="settings-fields" disabled={disabled}>
          <WorkspaceSettings {...controller} activeSection={activeSection} />
          <ConnectionSettings {...controller} activeSection={activeSection} />
          <ModelSettings {...controller} activeSection={activeSection} />
          <GroupSettings {...controller} activeSection={activeSection} />
          <RuntimeSettings {...controller} activeSection={activeSection} />
            </fieldset>
          </div>
        </div>

        <footer className="settings-actions">
          <div className="settings-save-state" aria-live="polite">
            {confirmClose ? <><strong>有尚未保存的修改</strong><span>关闭后，本次修改将被丢弃。</span></> : <><strong>{disabled ? "正在应用设置" : dirty ? "有未保存的修改" : "尚未修改"}</strong><span>{validationError || "配置完整，可以保存并应用。"}</span></>}
          </div>
          {!confirmClose && validationError && activeSection !== validationSection && <button type="button" className="plain-action" onClick={() => navigateSection(validationSection)}>前往完善</button>}
          {confirmClose && <button type="button" className="plain-action" onClick={() => setConfirmClose(false)}>继续编辑</button>}
          {confirmClose && <button type="button" className="plain-action settings-discard" onClick={onClose}>放弃修改</button>}
          <button hidden={confirmClose} className="plain-action" disabled={disabled} type="button" onClick={requestClose}>
            取消
          </button>
          <button className="send" aria-label="Save and Reload" disabled={disabled || Boolean(validationError)} type="button" onClick={() => onSave(draft)}>
            {disabled ? "正在保存…" : "保存并应用"}
          </button>
        </footer>
      </section>
    </div>
  );
}
