import { useEffect, useState } from "react";
import { applyBuiltinProvider, modelDiscoveryRequest, settingsToDraft, renamedConnectionValue, emptyNativeConnection, nextConnectionKey } from "./settingsModel.js";

export function useSettingsDraft(settings, api, disabled) {
  const [draft, setDraft] = useState(() => settingsToDraft(settings));
  const [catalogs, setCatalogs] = useState(null);
  const [discovered, setDiscovered] = useState({});
  const [discoveryState, setDiscoveryState] = useState({});
  useEffect(() => { setDraft(settingsToDraft(settings)); }, [settings]);
  useEffect(() => {
    if (!api?.getModelCatalogs) return;
    let active = true;
    api.getModelCatalogs().then(value => { if (active) setCatalogs(value); }).catch(() => {});
    return () => { active = false; };
  }, [api]);
  function setField(field, value) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function setModel(modelKey, field, value) {
    setDraft((current) => {
      const model = current.models[modelKey];
      if (!model) return current;
      if (field === "context_tokens" || field === "max_output_tokens") {
        return { ...current, models: { ...current.models, [modelKey]: { ...model, capabilities: { ...model.capabilities, limits: { ...model.capabilities?.limits, [field]: Number(value) || null } } } } };
      }
      return { ...current, models: { ...current.models, [modelKey]: { ...model, [field]: value } } };
    });
  }

  function setModelId(modelKey, modelId) {
    setDraft((current) => {
      const model = current.models[modelKey];
      if (!model) return current;
      const provider = current.connections[model.connection]?.provider;
      const known = catalogs?.model_capabilities?.find((item) => item.provider === provider && item.protocol === model.protocol && item.model_id === modelId);
      return { ...current, models: { ...current.models, [modelKey]: { ...model, model_id: modelId, ...(known?.capabilities ? { capabilities: structuredClone(known.capabilities) } : {}) } } };
    });
  }

  function setConnection(connectionKey, field, value) {
    setDraft((current) => {
      const connection = current.connections[connectionKey];
      if (!connection) return current;
      const next = field === "proxy" ? { ...connection, proxy: value || undefined } : { ...connection, [field]: value };
      return { ...current, connections: { ...current.connections, [connectionKey]: next } };
    });
  }

  function setConnectionProvider(connectionKey, provider) {
    setDraft((current) => {
      const providerCatalog = catalogs?.providers?.[provider];
      return applyBuiltinProvider(current, connectionKey, provider, providerCatalog);
    });
  }

  function setConnectionProtocolUrl(connectionKey, protocol, baseUrl) {
    setDraft((current) => {
      const connection = current.connections[connectionKey];
      if (!connection) return current;
      return { ...current, connections: { ...current.connections, [connectionKey]: { ...connection, protocols: { ...connection.protocols, [protocol]: { base_url: baseUrl } } } } };
    });
  }

  function addConnectionProtocol(connectionKey, protocol) {
    if (!protocol) return;
    setDraft((current) => {
      const connection = current.connections[connectionKey];
      if (!connection || connection.protocols?.[protocol]) return current;
      const baseUrl = catalogs?.providers?.[connection.provider]?.protocols?.[protocol]?.base_url || "";
      return { ...current, connections: { ...current.connections, [connectionKey]: { ...connection, protocols: { ...connection.protocols, [protocol]: { base_url: baseUrl } } } } };
    });
  }

  function removeConnectionProtocol(connectionKey, protocol) {
    setDraft((current) => {
      if (Object.values(current.models).some((model) => model.connection === connectionKey && model.protocol === protocol)) return current;
      const connection = current.connections[connectionKey];
      if (!connection) return current;
      const { [protocol]: _removed, ...protocols } = connection.protocols || {};
      return { ...current, connections: { ...current.connections, [connectionKey]: { ...connection, protocols } } };
    });
  }

  function setConnectionAuthentication(connectionKey, mode) {
    const connection = draft.connections[connectionKey];
    const protocol = Object.keys(connection?.protocols || {})[0];
    const suggested = catalogs?.providers?.[connection?.provider]?.protocols?.[protocol]?.api_key_env;
    setConnection(connectionKey, "credential", mode === "api-key" ? { env: connection?.credential?.env || suggested || `${connectionKey.toUpperCase().replace(/[^A-Z0-9]+/g, "_")}_API_KEY` } : { none: true });
  }

  function addConnection() {
    setDraft((current) => {
      const key = nextConnectionKey(current.connections);
      let credentialIndex = 1;
      const used = new Set([...Object.values(current.connections).map(connection => connection.credential?.env), ...Object.keys(current.credentialValues)]);
      while (used.has(`LORA_PROVIDER_${credentialIndex}_API_KEY`)) credentialIndex += 1;
      const connection = { ...emptyNativeConnection(key), credential: { env: `LORA_PROVIDER_${credentialIndex}_API_KEY` }, credential_source: "missing", protocols: { openai_chat_completions: { base_url: "" } } };
      return { ...current, connections: { ...current.connections, [key]: connection } };
    });
  }

  function renameConnection(previousKey, nextKey) {
    setDraft((current) => {
      const key = nextKey.trim();
      if (!key || key === previousKey || Object.hasOwn(current.connections, key)) return current;
      const connections = Object.fromEntries(Object.entries(current.connections).map(([name, connection]) => [name === previousKey ? key : name, name === previousKey ? renamedConnectionValue(connection, previousKey, key, catalogs) : connection]));
      const models = Object.fromEntries(Object.entries(current.models).map(([name, model]) => [name, model.connection === previousKey ? { ...model, connection: key } : model]));
      return { ...current, connections, models };
    });
  }

  function removeConnection(connectionKey) {
    setDraft((current) => {
      if (Object.values(current.models).some((model) => model.connection === connectionKey)) return current;
      const { [connectionKey]: _removed, ...connections } = current.connections;
      return { ...current, connections };
    });
  }

  function renameModel(previousKey, nextKey) {
    setDraft((current) => {
      const key = nextKey.trim();
      if (!key || key === previousKey || Object.hasOwn(current.models, key)) return current;
      const models = {};
      for (const [name, model] of Object.entries(current.models)) models[name === previousKey ? key : name] = model;
      const modelGroups = Object.fromEntries(Object.entries(current.modelGroups).map(([name, group]) => [name, { models: group.models.map((item) => item === previousKey ? key : item) }]));
      return { ...current, models, modelGroups };
    });
  }

  function removeModel(modelKey) {
    setDraft((current) => {
      if (Object.values(current.modelGroups).some((group) => group.models.includes(modelKey))) return current;
      const { [modelKey]: _removed, ...models } = current.models;
      return { ...current, models };
    });
  }

  function toggleGroupModel(groupName, modelKey) {
    setDraft((current) => {
      const previous = current.modelGroups[groupName]?.models || [];
      const models = previous.includes(modelKey) ? previous.filter((item) => item !== modelKey) : [...previous, modelKey];
      return { ...current, modelGroups: { ...current.modelGroups, [groupName]: { models } } };
    });
  }

  function moveGroupModel(groupName, modelKey, offset) {
    setDraft((current) => {
      const models = [...(current.modelGroups[groupName]?.models || [])];
      const from = models.indexOf(modelKey);
      const to = from + offset;
      if (from < 0 || to < 0 || to >= models.length) return current;
      [models[from], models[to]] = [models[to], models[from]];
      return { ...current, modelGroups: { ...current.modelGroups, [groupName]: { models } } };
    });
  }

  function renameGroup(previousName, nextName) {
    setDraft((current) => {
      const name = nextName.trim();
      if (!name || name === previousName || Object.hasOwn(current.modelGroups, name)) return current;
      const modelGroups = Object.fromEntries(Object.entries(current.modelGroups).map(([key, group]) => [key === previousName ? name : key, group]));
      return { ...current, modelGroups, defaultModelGroup: current.defaultModelGroup === previousName ? name : current.defaultModelGroup };
    });
  }

  function applyCapabilityPreset(modelKey, presetName) {
    const preset = catalogs?.capability_presets?.[presetName];
    if (!preset) return;
    setDraft((current) => ({ ...current, models: { ...current.models, [modelKey]: { ...current.models[modelKey], capabilities: structuredClone(preset) } } }));
  }

  async function discoverModelIds(modelKey) {
    const model = draft.models[modelKey];
    if (!api?.discoverModels || !model) return;
    const request = modelDiscoveryRequest(draft, model.connection);
    if (!request) return;
    setDiscoveryState((current) => ({ ...current, [modelKey]: { loading: true } }));
    try {
    const response = await api.discoverModels({ ...request, protocol: model.protocol });
    setDiscovered((current) => ({ ...current, [modelKey]: response.models || [] }));
    setDiscoveryState((current) => ({ ...current, [modelKey]: { message: response.models?.length ? `已获取 ${response.models.length} 个模型，请在模型 ID 输入框中选择。` : "未获取到模型，可手动填写模型 ID。" } }));
    } catch {
      setDiscoveryState((current) => ({ ...current, [modelKey]: { message: "获取失败，请检查连接地址与凭据，或手动填写模型 ID。" } }));
    }
  }

  return { settings, api, disabled, draft, setDraft, catalogs, discovered, discoveryState, setField, setModel, setModelId, setConnection, setConnectionProvider, setConnectionProtocolUrl, addConnectionProtocol, removeConnectionProtocol, setConnectionAuthentication, addConnection, renameConnection, removeConnection, renameModel, removeModel, toggleGroupModel, moveGroupModel, renameGroup, applyCapabilityPreset, discoverModelIds };
}
