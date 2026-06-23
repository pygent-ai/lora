import { app, BrowserWindow, ipcMain, shell } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_API_PORT,
  apiBaseUrl,
  resolveBackendLaunch,
  startBackendProcess,
  stopBackendProcess,
  waitForBackend,
} from "./backendProcess.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

let mainWindow;
let backendProcess;
let backendStatus = { state: "starting", error: null };

async function startBackend() {
  const port = Number(process.env.LORA_API_PORT || DEFAULT_API_PORT);
  const baseUrl = apiBaseUrl(port);
  process.env.LORA_API_BASE_URL = baseUrl;

  const launch = resolveBackendLaunch({
    appPath: app.getAppPath(),
    isPackaged: app.isPackaged,
    platform: process.platform,
    port,
    repoRoot: process.env.LORA_REPO_ROOT,
    resourcesPath: process.resourcesPath,
    workspaceRoot: app.getPath("userData"),
  });

  backendProcess = startBackendProcess(launch, {
    logPath: path.join(app.getPath("userData"), "logs", "lora-api.log"),
  });

  backendProcess.once("exit", (code, signal) => {
    if (backendStatus.state !== "stopping") {
      backendStatus = {
        state: "exited",
        error: `lora-api exited with code=${code ?? ""} signal=${signal ?? ""}`,
      };
    }
  });

  try {
    await waitForBackend({ baseUrl });
    backendStatus = { state: "ready", error: null };
  } catch (err) {
    backendStatus = {
      state: "error",
      error: err instanceof Error ? err.message : String(err),
    };
  }

  return baseUrl;
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 1040,
    minHeight: 720,
    show: false,
    title: "Lora Desktop",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "..", "preload", "preload.mjs"),
      sandbox: false,
    },
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow?.show();
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  if (!app.isPackaged && process.env.VITE_DEV_SERVER_URL) {
    await mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    return;
  }

  await mainWindow.loadFile(path.join(app.getAppPath(), "dist", "index.html"));
}

ipcMain.handle("backend:status", () => backendStatus);

app.whenReady().then(async () => {
  await startBackend();
  await createWindow();

  app.on("activate", async () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      await createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  backendStatus = { state: "stopping", error: null };
  stopBackendProcess(backendProcess);
});
