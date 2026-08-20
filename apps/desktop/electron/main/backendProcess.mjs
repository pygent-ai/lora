import { spawn, spawnSync } from "node:child_process";
import { createWriteStream, mkdirSync } from "node:fs";
import net from "node:net";
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
    command: developmentBackendPath(resolvedRepoRoot, platform),
    args: backendArgs({ port, workspaceRoot: resolvedRepoRoot }),
    cwd: resolvedRepoRoot,
  };
}

export function backendExecutableName(platform = process.platform) {
  return platform === "win32" ? "lora-api.exe" : "lora-api";
}

function developmentBackendPath(repoRoot, platform) {
  const pathImpl = platform === "win32" ? path.win32 : path.posix;
  const binDirectory = platform === "win32" ? "Scripts" : "bin";
  return pathImpl.join(repoRoot, ".venv", binDirectory, backendExecutableName(platform));
}

export function apiBaseUrl(port = DEFAULT_API_PORT) {
  return `http://${DEFAULT_API_HOST}:${port}`;
}

export function resolveUserDataPath(homePath) {
  return path.resolve(homePath, ".lora", "desktop");
}

export async function findAvailablePort(
  preferredPort = DEFAULT_API_PORT,
  { maxAttempts = 100, isAvailable = isPortAvailable } = {},
) {
  for (let offset = 0; offset < maxAttempts; offset += 1) {
    const port = preferredPort + offset;
    if (port > 65_535) {
      break;
    }
    if (await isAvailable(port)) {
      return port;
    }
  }
  throw new Error(`No available local port found from ${preferredPort}`);
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

export async function waitForBackend({
  baseUrl,
  child,
  expectedInstanceId,
  fetchImpl = fetch,
  timeoutMs = 30_000,
  retryDelayMs = 300,
} = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  let exitListener;
  const childExit = child
    ? new Promise((_, reject) => {
        exitListener = (code, signal) => reject(backendExitError(code, signal));
        if (child.exitCode !== null || child.signalCode) {
          exitListener(child.exitCode, child.signalCode);
        } else {
          child.once("exit", exitListener);
        }
      })
    : null;

  try {
    while (Date.now() < deadline) {
      try {
        const response = await raceChildExit(fetchImpl(`${baseUrl}/health`), childExit);
        if (response.ok) {
          const actualInstanceId = response.headers?.get?.("x-lora-backend-instance") || "";
          if (!expectedInstanceId || actualInstanceId === expectedInstanceId) {
            return true;
          }
          lastError = new Error(
            `Health check reached another lora-api instance at ${baseUrl}`,
          );
        } else {
          lastError = new Error(`Health check failed with ${response.status}`);
        }
      } catch (err) {
        if (isBackendExitError(err)) {
          throw err;
        }
        lastError = err;
      }
      await raceChildExit(delay(retryDelayMs), childExit);
    }
  } finally {
    if (child && exitListener) {
      child.off("exit", exitListener);
    }
  }

  throw lastError instanceof Error ? lastError : new Error("Timed out waiting for lora-api");
}

export function stopBackendProcess(
  child,
  { platform = process.platform, killProcessTree = killWindowsProcessTree } = {},
) {
  if (!child || child.killed || child.exitCode !== null) {
    return;
  }

  if (platform === "win32" && Number.isInteger(child.pid)) {
    const result = killProcessTree(child.pid);
    if (!result?.error && result?.status === 0) {
      return;
    }
  }

  child.kill();
}

function killWindowsProcessTree(pid) {
  return spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], {
    windowsHide: true,
    stdio: "ignore",
  });
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

function isPortAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once("error", () => resolve(false));
    server.listen({ host: DEFAULT_API_HOST, port, exclusive: true }, () => {
      server.close(() => resolve(true));
    });
  });
}

function raceChildExit(operation, childExit) {
  return childExit ? Promise.race([operation, childExit]) : operation;
}

function backendExitError(code, signal) {
  const error = new Error(
    `lora-api exited before becoming ready (code=${code ?? ""} signal=${signal ?? ""})`,
  );
  error.code = "LORA_BACKEND_EXITED";
  return error;
}

function isBackendExitError(error) {
  return error?.code === "LORA_BACKEND_EXITED";
}
