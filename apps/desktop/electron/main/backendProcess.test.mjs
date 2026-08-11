import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import path from "node:path";
import test from "node:test";

import {
  findAvailablePort,
  resolveBackendLaunch,
  waitForBackend,
} from "./backendProcess.mjs";

test("packaged backend launch uses bundled lora-api exe from Electron resources", () => {
  const resourcesPath = "C:\\Program Files\\Lora Desktop\\resources";
  const workspaceRoot = "C:\\Users\\Alice\\AppData\\Roaming\\Lora Desktop";

  const launch = resolveBackendLaunch({
    appPath: `${resourcesPath}\\app.asar`,
    isPackaged: true,
    platform: "win32",
    port: 8765,
    resourcesPath,
    workspaceRoot,
  });

  const expectedCommand = path.win32.join(resourcesPath, "backend", "lora-api", "lora-api.exe");
  assert.equal(launch.command, expectedCommand);
  assert.deepEqual(launch.args, [
    "--host",
    "127.0.0.1",
    "--port",
    "8765",
    "--workspace-root",
    workspaceRoot,
  ]);
  assert.equal(launch.cwd, path.win32.dirname(expectedCommand));
});

test("development backend launch runs uv from the repository root", () => {
  const repoRoot = "E:\\Projects\\lora";

  const launch = resolveBackendLaunch({
    appPath: `${repoRoot}\\apps\\desktop`,
    isPackaged: false,
    platform: "win32",
    port: 9123,
    repoRoot,
    resourcesPath: `${repoRoot}\\apps\\desktop`,
  });

  assert.equal(launch.command, "uv");
  assert.deepEqual(launch.args, [
    "run",
    "--no-sync",
    "lora-api",
    "--host",
    "127.0.0.1",
    "--port",
    "9123",
    "--workspace-root",
    repoRoot,
  ]);
  assert.equal(launch.cwd, repoRoot);
});

test("findAvailablePort skips an occupied preferred port", async () => {
  const checked = [];
  const port = await findAvailablePort(8765, {
    isAvailable: async (candidate) => {
      checked.push(candidate);
      return candidate === 8767;
    },
  });

  assert.equal(port, 8767);
  assert.deepEqual(checked, [8765, 8766, 8767]);
});

test("waitForBackend accepts only the backend instance it started", async () => {
  const response = {
    ok: true,
    headers: new Headers({ "X-Lora-Backend-Instance": "instance-new" }),
  };

  await assert.doesNotReject(
    waitForBackend({
      baseUrl: "http://127.0.0.1:8765",
      expectedInstanceId: "instance-new",
      fetchImpl: async () => response,
      timeoutMs: 100,
      retryDelayMs: 1,
    }),
  );
});

test("waitForBackend rejects when the spawned backend exits behind a stale healthy service", async () => {
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  const waiting = waitForBackend({
    baseUrl: "http://127.0.0.1:8765",
    child,
    expectedInstanceId: "instance-new",
    fetchImpl: async () => ({
      ok: true,
      headers: new Headers({ "X-Lora-Backend-Instance": "instance-old" }),
    }),
    timeoutMs: 1_000,
    retryDelayMs: 10,
  });

  setImmediate(() => child.emit("exit", 1, null));

  await assert.rejects(waiting, /lora-api exited before becoming ready/);
});
