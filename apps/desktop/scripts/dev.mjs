import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import electronExecutable from "electron";
import { createServer } from "vite";

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const shutdownMessage = { type: "lora:dev-shutdown" };

export async function runDesktopDev({
  createViteServer = createServer,
  spawnProcess = spawn,
  electronPath = electronExecutable,
  root = desktopRoot,
  signalSource = process,
  logger = console,
  forceShutdownMs = 5_000,
} = {}) {
  let viteServer;
  let electronProcess;
  let forceShutdownTimer;
  let shuttingDown = false;

  const removeSignalHandlers = () => {
    signalSource.off("SIGINT", requestShutdown);
    signalSource.off("SIGTERM", requestShutdown);
  };

  const closeVite = async () => {
    if (viteServer) {
      await viteServer.close();
      viteServer = undefined;
    }
  };

  const requestShutdown = () => {
    if (shuttingDown) {
      return;
    }
    shuttingDown = true;

    if (!electronProcess || electronProcess.exitCode !== null || electronProcess.killed) {
      void closeVite();
      return;
    }

    if (electronProcess.connected) {
      try {
        electronProcess.send(shutdownMessage, (error) => {
          if (error && electronProcess.exitCode === null && !electronProcess.killed) {
            electronProcess.kill();
          }
        });
      } catch {
        electronProcess.kill();
      }
    } else {
      electronProcess.kill();
    }

    forceShutdownTimer = setTimeout(() => {
      if (electronProcess.exitCode === null && !electronProcess.killed) {
        electronProcess.kill();
      }
    }, forceShutdownMs);
    forceShutdownTimer.unref?.();
  };

  try {
    viteServer = await createViteServer({
      root,
      clearScreen: false,
      server: { host: "127.0.0.1" },
    });
    await viteServer.listen();

    const viteUrl = viteServer.resolvedUrls?.local?.[0];
    if (!viteUrl) {
      throw new Error("Vite did not expose a local development server URL");
    }

    logger.log(`[lora] Starting Electron with ${viteUrl}`);
    electronProcess = spawnProcess(electronPath, [root], {
      cwd: root,
      env: {
        ...process.env,
        VITE_DEV_SERVER_URL: viteUrl,
      },
      stdio: ["inherit", "inherit", "inherit", "ipc"],
    });

    signalSource.on("SIGINT", requestShutdown);
    signalSource.on("SIGTERM", requestShutdown);

    return await new Promise((resolve, reject) => {
      electronProcess.once("error", reject);
      electronProcess.once("exit", (code, signal) => {
        if (signal && !shuttingDown) {
          logger.error(`[lora] Electron exited from signal ${signal}`);
        }
        resolve(code ?? (shuttingDown ? 0 : 1));
      });
    });
  } finally {
    removeSignalHandlers();
    if (forceShutdownTimer) {
      clearTimeout(forceShutdownTimer);
    }
    await closeVite();
  }
}

function isMainModule() {
  return process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
}

if (isMainModule()) {
  const exitCode = await runDesktopDev();
  process.exitCode = exitCode;
}
