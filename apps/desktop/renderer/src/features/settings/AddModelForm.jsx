import React, { useEffect, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { addModelToGroup, modelDiscoveryRequest, protocolLabel } from "./settingsModel.js";

export function AddModelForm({ draft, setDraft, catalogs, api, onDone, onCancel }) {
  const [connection, setConnection] = useState("");
  const [modelId, setModelId] = useState("");
  const [group, setGroup] = useState("");
  const [message, setMessage] = useState("");
  const [discovered, setDiscovered] = useState([]);
  const [discovery, setDiscovery] = useState({ status: "idle", refresh: 0 });
  const connections = Object.keys(draft.connections).filter(key => Object.keys(draft.connections[key].protocols || {}).length);
  const selected = connections.includes(connection) ? connection : connections[0] || "";
  const selectedGroup = group === "__new__" ? "" : Object.hasOwn(draft.modelGroups, group) ? group : draft.defaultModelGroup;
  const protocol = Object.keys(draft.connections[selected]?.protocols || {})[0];
  const request = modelDiscoveryRequest(draft, selected);
  const requestSignature = JSON.stringify(request);

  useEffect(() => {
    const controller = new AbortController();
    setDiscovered([]);
    if (!api?.discoverModels || !request) {
      setDiscovery((current) => ({ ...current, status: "unavailable" }));
      return () => controller.abort();
    }
    setDiscovery((current) => ({ ...current, status: "loading" }));
    api.discoverModels(request, { signal: controller.signal }).then((response) => {
      if (controller.signal.aborted) return;
      const models = Array.isArray(response?.models) ? response.models : [];
      setDiscovered(models);
      setDiscovery((current) => ({ ...current, status: models.length ? "ready" : "empty" }));
    }).catch((error) => {
      if (controller.signal.aborted || error?.name === "AbortError") return;
      setDiscovery((current) => ({ ...current, status: "error" }));
    });
    return () => controller.abort();
  }, [api, requestSignature, discovery.refresh]);

  function add() {
    try {
      const next = addModelToGroup(draft, { connectionKey: selected, modelId, groupName: selectedGroup }, catalogs);
      setDraft(next);
      setModelId("");
      onDone();
    } catch (error) { setMessage(error.message); }
  }

  const discoveryText = {
    loading: "正在从连接获取模型…",
    ready: `已获取 ${discovered.length} 个模型，也可以手动输入`,
    empty: "服务商没有返回模型，可手动输入模型 ID",
    error: "自动获取失败，可手动输入或重试",
    unavailable: "当前环境不支持自动获取，请手动输入模型 ID",
    idle: "",
  }[discovery.status];

  return <section className="add-model-form" aria-label="添加模型表单">
    <div className="add-model-fields">
      <label><span>服务连接</span><select value={selected} onChange={event => { setConnection(event.target.value); setModelId(""); }}>{connections.map(key => <option key={key} value={key}>{key}</option>)}</select></label>
      <label className="add-model-id-field">
        <span>模型 ID</span>
        <div className="add-model-id-control">
          <input list="add-model-discovered" value={modelId} onChange={event => setModelId(event.target.value)} placeholder={discovery.status === "loading" ? "正在获取模型…" : "选择或输入模型 ID"} />
          <button type="button" disabled={!api?.discoverModels || discovery.status === "loading"} onClick={() => setDiscovery((current) => ({ ...current, refresh: current.refresh + 1 }))} aria-label="重新获取模型" title="重新获取模型"><RefreshCw size={14} aria-hidden="true" /></button>
        </div>
        <datalist id="add-model-discovered">{discovered.map((item) => <option key={item.id} value={item.id}>{item.owned_by || ""}</option>)}</datalist>
        <small className={`add-model-discovery ${discovery.status}`} role="status">{discoveryText}</small>
      </label>
      <label><span>加入模型组</span><select value={group === "__new__" ? "__new__" : selectedGroup || "__new__"} onChange={event => setGroup(event.target.value)}>{Object.keys(draft.modelGroups).map(name => <option key={name} value={name}>{name}</option>)}<option value="__new__">创建新组{!Object.keys(draft.modelGroups).length ? "并设为默认" : ""}</option></select></label>
      <button type="button" className="route-add" disabled={!selected || !modelId.trim()} onClick={add}><Plus size={16} /> 确认添加</button>
    </div>
    <p className="add-model-hint">使用 {protocolLabel(protocol)}；新模型追加到所选组末尾，已有优先级不变。协议与能力可在下方调整。</p>
    <button type="button" className="plain-action" onClick={onCancel}>取消添加</button>
    {message && <p className="add-model-message" role="status">{message}</p>}
  </section>;
}
