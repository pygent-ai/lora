import React, { useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, Plus, Search, X } from "lucide-react";

function modelDisplay(draft, modelKey) {
  const model = draft.models[modelKey];
  const connection = draft.connections[model?.connection];
  return {
    title: model?.model_id || modelKey,
    meta: `${connection?.provider || "未知供应商"} · ${modelKey}`,
  };
}

function GroupModelSelect({ draft, groupName, selected, onSelect }) {
  const [query, setQuery] = useState("");
  const detailsRef = useRef(null);
  const available = useMemo(() => Object.keys(draft.models).filter((modelKey) => {
    if (selected.includes(modelKey)) return false;
    const display = modelDisplay(draft, modelKey);
    const search = `${display.title} ${display.meta}`.toLocaleLowerCase();
    return search.includes(query.trim().toLocaleLowerCase());
  }), [draft, query, selected]);
  const remaining = Object.keys(draft.models).length - selected.length;

  return <details className="group-model-select" ref={detailsRef}>
    <summary aria-label={`为 ${groupName} 添加模型`} aria-disabled={!remaining} onClick={(event) => { if (!remaining) event.preventDefault(); }}>
      <span><Plus size={14} aria-hidden="true" />{remaining ? "添加模型" : "全部模型已加入"}</span>
      {remaining > 0 && <><small>{remaining} 个可选</small><ChevronDown size={15} aria-hidden="true" /></>}
    </summary>
    {remaining > 0 && <div className="group-model-menu">
      <label className="group-model-search">
        <Search size={14} aria-hidden="true" />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索模型、供应商或本地名称" autoFocus />
      </label>
      <div className="group-model-results" role="listbox" aria-label="可添加模型">
        {available.map((modelKey) => {
          const display = modelDisplay(draft, modelKey);
          return <button key={modelKey} type="button" role="option" onClick={() => {
            onSelect(modelKey);
            setQuery("");
            detailsRef.current?.removeAttribute("open");
          }}>
            <span><strong>{display.title}</strong><small>{display.meta}</small></span>
            <Plus size={15} aria-hidden="true" />
          </button>;
        })}
        {!available.length && <p>没有匹配的模型</p>}
      </div>
    </div>}
  </details>;
}

export function GroupSettings({ draft, setDraft, activeSection, setField, renameGroup, toggleGroupModel, moveGroupModel }) {
  return <>
    <section hidden={activeSection !== "model-config"} className="fallback-editor" aria-label="Model groups">
      <div className="model-group-heading">
        <div>
          <strong>模型组</strong>
          <span>选择组内模型并排列调用顺序；第一项为主模型，其余按顺序回退。</span>
        </div>
      </div>
      {!Object.keys(draft.modelGroups).length && <p className="settings-inline-empty">添加模型时可以同时创建模型组，也可以在这里手动添加。</p>}
      <div className="model-group-list">
        {Object.entries(draft.modelGroups).map(([groupName, group]) => {
          const route = group.models.map((key) => modelDisplay(draft, key).title);
          return <div className="model-group-card" key={groupName}>
            <label className="group-name-field">
              <span>组名</span>
              <input defaultValue={groupName} onBlur={(event) => renameGroup(groupName, event.target.value)} />
              <small>{draft.defaultModelGroup === groupName ? "默认模型组 · " : ""}{route.length ? `${route.length} 个模型 · ${route.join(" → ")}` : "请至少选择一个模型"}</small>
            </label>
            <div className="group-model-picker" aria-label={`${groupName} models`}>
              <div className="group-model-picker-heading"><span>调用顺序</span><small>从上到下依次尝试</small></div>
              {group.models.map((modelKey, order) => {
                const display = modelDisplay(draft, modelKey);
                return <div className="group-model-option selected" key={modelKey}>
                  <span className="group-model-rank">{order + 1}</span>
                  <span className="group-model-copy"><strong>{display.title}</strong><small>{order === 0 ? "主模型" : `备用 ${order}`} · {display.meta}</small></span>
                  <div className="fallback-order-actions">
                    <button type="button" disabled={order === 0} onClick={() => moveGroupModel(groupName, modelKey, -1)} title="提高优先级" aria-label={`提高 ${display.title} 的优先级`}><ArrowUp /></button>
                    <button type="button" disabled={order === group.models.length - 1} onClick={() => moveGroupModel(groupName, modelKey, 1)} title="降低优先级" aria-label={`降低 ${display.title} 的优先级`}><ArrowDown /></button>
                    <button type="button" onClick={() => toggleGroupModel(groupName, modelKey)} title="从模型组移除" aria-label={`从 ${groupName} 移除 ${display.title}`}><X /></button>
                  </div>
                </div>;
              })}
              {!group.models.length && <div className="group-model-empty">选择一个模型作为主模型</div>}
              <GroupModelSelect draft={draft} groupName={groupName} selected={group.models} onSelect={(modelKey) => toggleGroupModel(groupName, modelKey)} />
            </div>
          </div>;
        })}
      </div>
      <button className="plain-action" disabled={!Object.keys(draft.models).length} type="button" onClick={() => setDraft((current) => {
        let index = Object.keys(current.modelGroups).length + 1;
        while (Object.hasOwn(current.modelGroups, `group-${index}`)) index += 1;
        const name = `group-${index}`;
        return { ...current, modelGroups: { ...current.modelGroups, [name]: { models: Object.keys(current.models).slice(0, 1) } }, defaultModelGroup: current.defaultModelGroup || name };
      })}><Plus aria-hidden="true" /> 添加模型组</button>
      <label><span>Agent 默认模型组</span><select value={draft.defaultModelGroup} onChange={(event) => setField("defaultModelGroup", event.target.value)}>{Object.keys(draft.modelGroups).map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
      <details className="settings-disclosure settings-retry">
        <summary>重试与切换 <span>全局生效</span></summary>
        <div className="settings-disclosure-body">
          <p className="settings-inline-empty" id="model-retry-scope">适用于所有模型组。这里调整的是全局重试参数，不会为单个模型组保存独立策略。</p>
          <div className="retry-grid" aria-describedby="model-retry-scope">
            <label><span>每个模型尝试次数</span><input min="1" type="number" value={draft.retry.max_attempts_per_model} onChange={(event) => setField("retry", { ...draft.retry, max_attempts_per_model: event.target.value })} /></label>
            <label><span>空闲超时（秒）</span><input min="0.1" step="0.1" type="number" value={draft.retry.attempt_idle_timeout_seconds} onChange={(event) => setField("retry", { ...draft.retry, attempt_idle_timeout_seconds: event.target.value })} /></label>
          </div>
        </div>
      </details>
    </section>
  </>;
}
