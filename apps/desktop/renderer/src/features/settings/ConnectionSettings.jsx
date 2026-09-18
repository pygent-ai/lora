import React from "react";
import { Plus, Trash2 } from "lucide-react";
import {
  changeConnectionProtocol,
  protocolChoicesForConnection,
  protocolLabel,
  providerSelectionValue,
} from "./settingsModel.js";

export function ConnectionSettings({
  draft,
  setDraft,
  catalogs,
  activeSection,
  addConnection,
  removeConnection,
  renameConnection,
  setConnection,
  setConnectionProvider,
  setConnectionAuthentication,
  setConnectionProtocolUrl,
  removeConnectionProtocol,
  addConnectionProtocol,
}) {
  return <section hidden={activeSection !== "model-config"} className="model-group-editor" aria-label="Native Pygent connections">
    <div className="model-group-heading">
      <div><strong>1 · 服务连接</strong></div>
      <button className="route-add" type="button" onClick={addConnection}><Plus aria-hidden="true" /> 添加连接</button>
    </div>
    {!Object.keys(draft.connections).length && <p className="settings-inline-empty">还没有服务连接，请点击“添加连接”开始。</p>}
    <div className="model-route-list">
      {Object.entries(draft.connections).map(([connectionKey, connection], index) => {
        const referenced = Object.values(draft.models).some((model) => model.connection === connectionKey);
        const providers = Object.entries(catalogs?.providers || {});
        const providerSelection = providerSelectionValue(connection.provider, catalogs);
        const credentialEnv = connection.credential?.env || "";
        const authMode = connection.credential?.none ? "none" : "api-key";
        const protocols = Object.entries(connection.protocols || {});
        const modelCount = Object.values(draft.models).filter((model) => model.connection === connectionKey).length;
        const hasNewKey = Boolean(draft.credentialValues[credentialEnv]?.trim());
        const savedKey = Boolean(connection.credential_source && connection.credential_source !== "missing");
        const protocolChoices = protocolChoicesForConnection("__custom__", catalogs);
        const addableProtocols = protocolChoices.filter((protocol) => !connection.protocols?.[protocol]);
        const missing = !connection.provider
          ? "请选择供应商"
          : !protocols.length || protocols.some(([, endpoint]) => !endpoint?.base_url?.trim())
            ? "请填写 Base URL"
            : authMode === "api-key" && !hasNewKey && connection.credential_source === "missing"
              ? "请填写 API Key"
              : "";

        function selectProvider(value) {
          if (value === "__custom__") setConnection(connectionKey, "provider", connectionKey);
          else setConnectionProvider(connectionKey, value);
        }

        function protocolSelect(protocol) {
          return <label>
            <span>接口协议</span>
            <select value={protocol} onChange={(event) => setDraft((current) => changeConnectionProtocol(current, connectionKey, protocol, event.target.value))}>
              {protocolChoices.map((key) => <option key={key} value={key} disabled={key !== protocol && Boolean(connection.protocols?.[key])}>{protocolLabel(key)}</option>)}
            </select>
          </label>;
        }

        return <article className="model-route-card connection-route-card" key={connectionKey}>
          <header className="model-route-title">
            <span className="route-rank">C{String(index + 1).padStart(2, "0")}</span>
            <strong>{connectionKey}</strong>
            <span className="connection-meta">{modelCount} 个模型{providerSelection === "__custom__" ? " · 自定义" : " · Pygent 内置"}</span>
            <button className="route-remove" disabled={referenced} type="button" onClick={() => removeConnection(connectionKey)} aria-label={`Remove ${connectionKey}`} title={referenced ? "先让模型改用其他连接" : "删除连接"}><Trash2 aria-hidden="true" /></button>
          </header>
          {missing && <div className="connection-missing" role="status">{missing}</div>}

          <div className="connection-primary-fields">
            <label><span>连接名称</span><input defaultValue={connectionKey} onBlur={(event) => renameConnection(connectionKey, event.target.value)} /></label>
            <label>
              <span>供应商</span>
              <select value={providerSelection || ""} onChange={(event) => selectProvider(event.target.value)}>
                {!catalogs && <option value={connection.provider}>{connection.provider || "请选择"}</option>}
                {providers.length > 0 && <optgroup label="Pygent 内置">{providers.map(([key, item]) => <option key={key} value={key}>{item.display_name || key}</option>)}</optgroup>}
                <optgroup label="自定义"><option value="__custom__">自定义供应商</option></optgroup>
              </select>
            </label>
            {authMode === "api-key" && <label>
              <span>API Key <small className="field-required">{savedKey ? "已配置" : "必填"}</small></span>
              <input disabled={!credentialEnv} type="password" autoComplete="off" placeholder={savedKey ? "留空保留已有密钥" : "填写 API Key"} value={draft.credentialValues[credentialEnv] || ""} onChange={(event) => setDraft((current) => ({ ...current, credentialValues: { ...current.credentialValues, [credentialEnv]: event.target.value } }))} />
            </label>}
          </div>

          <div className="connection-endpoints">
            <div className="connection-section-heading"><strong>接口地址</strong><span>一个连接可以提供多个协议接口</span></div>
            <div className="protocol-interface-list">
              {protocols.map(([protocol, endpoint]) => {
                const protocolReferenced = Object.values(draft.models).some((model) => model.connection === connectionKey && model.protocol === protocol);
                return <div className="protocol-interface-row" key={protocol}>
                  <label><span>Base URL <small className="field-required">必填</small></span><input placeholder="https://api.example.com/v1" value={endpoint?.base_url || ""} onChange={(event) => setConnectionProtocolUrl(connectionKey, protocol, event.target.value)} /></label>
                  {protocolSelect(protocol)}
                  <button className="route-remove" disabled={protocolReferenced || protocols.length === 1} type="button" onClick={() => removeConnectionProtocol(connectionKey, protocol)} aria-label={`Remove ${protocol}`} title={protocolReferenced ? "有模型正在使用此接口" : protocols.length === 1 ? "至少保留一个接口" : "删除接口"}><Trash2 aria-hidden="true" /></button>
                </div>;
              })}
            </div>
            {addableProtocols.length > 0 && <label className="protocol-add-row">
              <span><Plus size={14} /> 添加接口</span>
              <select value="" onChange={(event) => addConnectionProtocol(connectionKey, event.target.value)}>
                <option value="">选择接口协议…</option>
                {addableProtocols.map((protocol) => <option key={protocol} value={protocol}>{protocolLabel(protocol)}</option>)}
              </select>
            </label>}
          </div>

          <details className="connection-advanced settings-detail">
            <summary>高级配置 <span>认证、代理与 TLS</span></summary>
            <div className="model-route-grid">
              <label><span>认证方式</span><select value={authMode} onChange={(event) => setConnectionAuthentication(connectionKey, event.target.value)}><option value="api-key">API Key</option><option value="none">无需认证</option></select></label>
              {authMode === "api-key" && <label><span>凭据名称</span><input value={credentialEnv} onChange={(event) => setConnection(connectionKey, "credential", event.target.value ? { env: event.target.value } : { none: true })} /><small>配置只保存名称，不保存密钥。</small></label>}
              <label><span>代理（可选）</span><input placeholder="http://127.0.0.1:7890" value={connection.proxy || ""} onChange={(event) => setConnection(connectionKey, "proxy", event.target.value)} /></label>
              <label><span>TLS 证书验证</span><select value={connection.verify_ssl === false ? "off" : "on"} onChange={(event) => setConnection(connectionKey, "verify_ssl", event.target.value === "on")}><option value="on">开启（推荐）</option><option value="off">关闭</option></select></label>
            </div>
          </details>
        </article>;
      })}
    </div>
  </section>;
}
