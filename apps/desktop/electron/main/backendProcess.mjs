import { spawn } from "node:child_process";
import { createWriteStream, mkdirSync } from "node:fs";
import path from "node:path";

export const DEFAULT_API_HOST = "127.0.0.1";
export const DEFAULT_API_PORT = 8765;

export function resolveBackendLaunch({
  appPath,
  isPackaged,
  platform = process.platform,
  port = DEFAULT_API_PORT,
  repoRoot,
  resourcesPath,
  workspaceRoot,
}) {
  if (isPackaged) {
    const command = path.join(resourcesPath, "backend", "lora-api", backendExecutableName(platform));
    return {
      command,
      args: backendArgs({ port, workspaceRoot }),
      cwd: path.dirname(command),
    };
  }

  const resolvedRepoRoot = repoRoot || path.resolve(appPath, "..", "..");
  return {
    command: "uv",
    args: backendArgs({ port, workspaceRoot: resolvedRepoRoot, prefix: ["run", "lora-api"] }),
    cwd: resolvedRepoRoot,
  };
}

export function backendExecutableName(platform = process.platform) {
  return platform === "win32" ? "lora-api.exe" : "lora-api";
}

export function apiBaseUrl(port = DEFAULT_API_PORT) {
  return `http://${DEFAULT_API_HOST}:${port}`;
}

export function startBackendProcess(launch, { env = process.env, logPath } = {}) {
  const child = spawn(launch.command, launch.args, {
    cwd: launch.cwd,
    env: {
      ...env,
      PYTHONUNBUFFERED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });

  if (logPath) {
    mkdirSync(path.dirname(logPath), { recursive: true });
    const logStream = createWriteStream(logPath, { flags: "a" });
    logStream.write(`\n[${new Date().toISOString()}] ${launch.command} ${launch.args.join(" ")}\n`);
    child.stdout?.pipe(logStream, { end: false });
    child.stderr?.pipe(logStream, { end: false });
    child.once("exit", (code, signal) => {
      logStream.write(`[${new Date().toISOString()}] exited code=${code ?? ""} signal=${signal ?? ""}\n`);
      logStream.end();
    });
  }

  return child;
}

export async function waitForBackend({ baseUrl, timeoutMs = 30_000, retryDelayMs = 300 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastError;

  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/health`);
      if (response.ok) {
        return true;
      }
      lastError = new Error(`Health check failed with ${response.status}`);
    } catch (err) {
      lastError = err;
    }
    await delay(retryDelayMs);
  }

  throw lastError instanceof Error ? lastError : new Error("Timed out waiting for lora-api");
}

export function stopBackendProcess(child) {
  if (!child || child.killed || child.exitCode !== null) {
    return;
  }
  child.kill();
}

function backendArgs({ port, workspaceRoot, prefix = [] }) {
  const args = [...prefix, "--host", DEFAULT_API_HOST, "--port", String(port)];
  if (workspaceRoot) {
    args.push("--workspace-root", workspaceRoot);
  }
  return args;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
