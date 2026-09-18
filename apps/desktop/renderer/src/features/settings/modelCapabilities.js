export const MODALITIES = { text: "文本", image: "图片", audio: "音频", video: "视频", embedding: "向量" };
export const PURPOSES = [
  { id: "chat", name: "对话", description: "文本问答、写作与总结" },
  { id: "agentic", name: "Agentic · 工具执行", description: "工具调用、代码与执行任务" },
  { id: "understanding", name: "多模态理解", description: "默认图片理解与文本回答，可扩展音视频" },
  { id: "generation", name: "多模态生成", description: "默认图片输出，可调整音视频输出" },
];
export function purposeCapabilities(id, current) {
  const capabilities = {
    modalities: { input: ["text"], output: ["text"] }, streaming: { output: ["text"] },
    tools: { call: false, choice: [], parallel: false },
    structured_output: { json_object: false, json_schema: false },
    reasoning: { supported: false, controllable: false },
    limits: structuredClone(current?.limits || { context_tokens: null, max_output_tokens: null }),
  };
  if (id === "agentic") capabilities.tools = { call: true, choice: ["auto"], parallel: true };
  if (id === "understanding") capabilities.modalities.input.push("image");
  if (id === "generation") { capabilities.modalities.output = ["image"]; capabilities.streaming.output = []; }
  return capabilities;
}
export function capabilitySignature(value) {
  return JSON.stringify([value?.modalities?.input, value?.modalities?.output, value?.streaming?.output, value?.tools, value?.structured_output, value?.reasoning]);
}
export function updateCapability(current, section, field, value) {
  const next = { ...current, [section]: { ...current[section], [field]: value } };
  if (section === "modalities" && field === "output") next.streaming = { ...current.streaming, output: (current.streaming?.output || []).filter(item => value.includes(item)) };
  if (section === "reasoning" && field === "supported" && !value) next.reasoning.controllable = false;
  return next;
}
export function capabilityTags(value) {
  const tags = [];
  for (const item of value?.modalities?.input || []) tags.push(`${MODALITIES[item] || item}输入`);
  for (const item of value?.modalities?.output || []) tags.push(`${MODALITIES[item] || item}输出`);
  if (value?.tools?.call) tags.push("工具调用");
  if (value?.tools?.parallel) tags.push("并行工具");
  if (value?.streaming?.output?.length) tags.push("流式输出");
  if (value?.structured_output?.json_object) tags.push("JSON Object");
  if (value?.structured_output?.json_schema) tags.push("JSON Schema");
  if (value?.reasoning?.supported) tags.push(value.reasoning.controllable ? "可控推理" : "推理");
  return tags;
}
