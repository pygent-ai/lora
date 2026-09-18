import { ModelCapabilitiesEditor } from "./ModelCapabilitiesEditor.jsx";
import React, { useState } from "react";
import { AddModelForm } from "./AddModelForm.jsx";
import { Plus, Trash2 } from "lucide-react";
import { protocolLabel } from "./settingsModel.js";

export function ModelSettings({ draft, setDraft, catalogs, activeSection, discovered, discoveryState, api, removeModel, renameModel, setModel, setModelId, discoverModelIds }) {
  const [adding, setAdding] = useState(false);
  return <>
          <section hidden={activeSection !== "model-config"} className="model-group-editor" aria-label="Native Pygent models">

            <div className="model-group-heading">
              <div>
                <strong>2 · 模型目录</strong>
                <span>模型只选择已有连接、协议和真实 Model ID；连接参数不会重复保存。</span>
              </div>
              <button className="route-add" type="button" disabled={adding || !Object.values(draft.connections).some(connection => Object.keys(connection.protocols || {}).length)} onClick={() => setAdding(true)}>
                <Plus aria-hidden="true" /> 添加模型
              </button>
            </div>
            {!Object.keys(draft.models).length && <p className="settings-inline-empty">还没有模型。先添加服务连接，再点击“添加模型”。</p>}
            {adding && <AddModelForm draft={draft} setDraft={setDraft} catalogs={catalogs} api={api} onDone={() => setAdding(false)} onCancel={() => setAdding(false)} />}
            <div className="model-route-list">
              {Object.entries(draft.models).map(([modelKey, model], index) => {
                const referenced = Object.values(draft.modelGroups).some((group) => group.models.includes(modelKey));
                const selectedConnection = draft.connections[model.connection];
                const protocols = Object.keys(selectedConnection?.protocols || {});
                return <article className="model-route-card" key={modelKey}>
                  <header className="model-route-title">
                    <span className="route-rank">{String(index + 1).padStart(2, "0")}</span>
                    <strong>{modelKey}</strong>
                    <button
                      className="route-remove"
                      disabled={referenced}
                      type="button"
                      onClick={() => removeModel(modelKey)}
                      aria-label={`Remove ${modelKey}`}
                      title={referenced ? "先从所有模型组移除" : "删除模型"}
                    >
                      <Trash2 aria-hidden="true" />
                    </button>
                  </header>
                  <div className="settings-card-summary"><span>{model.model_id || "请填写模型 ID"}</span><span>{protocolLabel(model.protocol)}</span><span>{Object.entries(draft.modelGroups).filter(([, group]) => group.models.includes(modelKey)).map(([name]) => name).join("、") || "尚未加入模型组"}</span></div>
                  <div className="model-config-sections">
                    <section className="model-config-block" aria-label={`${modelKey} model identity`}>
                      <div className="model-config-block-heading"><strong>模型信息</strong><span>决定调用谁、使用哪种 API 格式；不属于连接。</span></div>
                      <div className="model-route-grid">

                        <label><span>使用连接</span><select value={model.connection || ""} onChange={(event) => { const connection = event.target.value; const protocol = Object.keys(draft.connections[connection]?.protocols || {})[0] || ""; setDraft((current) => ({ ...current, models: { ...current.models, [modelKey]: { ...current.models[modelKey], connection, protocol } } })); }}>{Object.keys(draft.connections).map((key) => <option key={key} value={key}>{key} · {draft.connections[key].provider}</option>)}</select><small>选择上一步配置的可复用连接。</small></label>

                        <label><span>服务商模型 ID</span><input list={`models-${modelKey}`} placeholder="例如 gpt-5.1-codex" value={model.model_id || ""} onChange={(event) => setModelId(modelKey, event.target.value)} /><small>这是服务商文档或“发现模型”返回的真实 ID。</small></label>
                        <fieldset className="model-memberships route-secret"><legend>所属模型组</legend>{Object.entries(draft.modelGroups).map(([name, group]) => <label key={name}><input type="checkbox" checked={group.models.includes(modelKey)} onChange={() => setDraft(current => { const previous = current.modelGroups[name]; return { ...current, modelGroups: { ...current.modelGroups, [name]: { ...previous, models: previous.models.includes(modelKey) ? previous.models.filter(key => key !== modelKey) : [...previous.models, modelKey] } } }; })} /><span>{name}{draft.defaultModelGroup === name ? " · 默认组" : ""}</span></label>)}{!Object.keys(draft.modelGroups).length && <small>请在下方添加模型组。</small>}</fieldset>
                        <datalist id={`models-${modelKey}`}>{(discovered[modelKey] || []).map((item) => <option key={item.id} value={item.id} />)}</datalist>

                        <button className="plain-action discover-action" disabled={!api?.discoverModels || discoveryState[modelKey]?.loading} type="button" onClick={() => discoverModelIds(modelKey)}>{discoveryState[modelKey]?.loading ? "正在获取模型…" : "通过连接发现模型 ID"}</button>
                        {discoveryState[modelKey]?.message && <small className="route-secret" role="status">{discoveryState[modelKey].message}</small>}
                      </div>
                    </section>
                    <ModelCapabilitiesEditor value={model.capabilities} catalogs={catalogs} onChange={value => setModel(modelKey, "capabilities", value)} />
                    <details className="model-capability-advanced settings-detail"><summary>详细配置 <span>名称与 API 协议</span></summary><div className="model-route-grid">
                        <label><span>本地名称</span><input defaultValue={modelKey} onBlur={(event) => renameModel(modelKey, event.target.value)} /><small>仅供 Lora 的模型组引用，不会发送给服务商。</small></label>
                        <label><span>API 协议</span><select value={model.protocol || ""} onChange={(event) => setModel(modelKey, "protocol", event.target.value)}>{!protocols.includes(model.protocol) && <option value={model.protocol}>{protocolLabel(model.protocol)}</option>}{protocols.map((key) => <option key={key} value={key}>{protocolLabel(key)}</option>)}</select><small>只能选择当前连接提供的协议。</small></label>



                    </div></details>
                  </div>
                </article>;
              })}
            </div>
          </section>
  </>;
}
