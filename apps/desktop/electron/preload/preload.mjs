import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("__LORA_API_BASE_URL__", process.env.LORA_API_BASE_URL || "http://127.0.0.1:8765");

contextBridge.exposeInMainWorld("loraDesktop", {
  getBackendStatus: () => ipcRenderer.invoke("backend:status"),
});
