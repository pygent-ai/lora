import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_API_PORT,
  apiBaseUrl,
  findAvailablePort,
  resolveBackendLaunch,
  resolveUserDataPath,
  startBackendProcess,
  stopBackendProcess,
  waitForBackend,
} from "./backendProcess.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

app.setPath("userData", resolveUserDataPath(app.getPath("home")));

let mainWindow;
let backendProcess;
let backendStatus = { state: "starting", error: null };
let quitting = false;

process.on("message", (message) => {
  if (message?.type === "lora:dev-shutdown") {
    app.quit();
  }
});

async function startBackend() {
  const preferredPort = Number(process.env.LORA_API_PORT || DEFAULT_API_PORT);
  const port = await findAvailablePort(preferredPort);
  const baseUrl = apiBaseUrl(port);
  const instanceId = randomUUID();
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
    env: {
      ...process.env,
      LORA_BACKEND_INSTANCE_ID: instanceId,
    },
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
    await waitForBackend({ baseUrl, child: backendProcess, expectedInstanceId: instanceId });
    backendStatus = { state: "ready", error: null, port };
  } catch (err) {
    stopBackendProcess(backendProcess);
    backendStatus = {
      state: "error",
      error: err instanceof Error ? err.message : String(err),
      port,
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
ipcMain.handle("project:choose-directory", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openDirectory", "createDirectory"],
    title: "Choose Project",
  });
  return result.canceled ? null : result.filePaths[0] || null;
});

app.whenReady().then(async () => {
  await startBackend();
  if (quitting) {
    return;
  }
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
  quitting = true;
  backendStatus = { state: "stopping", error: null };
  stopBackendProcess(backendProcess);
});
