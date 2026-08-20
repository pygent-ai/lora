import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { EventEmitter, once } from "node:events";
import path from "node:path";
import test from "node:test";

import {
  findAvailablePort,
  resolveBackendLaunch,
  resolveUserDataPath,
  stopBackendProcess,
  waitForBackend,
} from "./backendProcess.mjs";

test("desktop user data is stored under the user Lora directory", () => {
  const homePath = path.resolve("test-home");
  assert.equal(
    resolveUserDataPath(homePath),
    path.join(homePath, ".lora", "desktop"),
  );
});

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

test("development backend launch runs lora-api directly from the project environment", () => {
  const repoRoot = "E:\\Projects\\lora";

  const launch = resolveBackendLaunch({
    appPath: `${repoRoot}\\apps\\desktop`,
    isPackaged: false,
    platform: "win32",
    port: 9123,
    repoRoot,
    resourcesPath: `${repoRoot}\\apps\\desktop`,
  });

  assert.equal(launch.command, path.win32.join(repoRoot, ".venv", "Scripts", "lora-api.exe"));
  assert.deepEqual(launch.args, [
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

test("stopBackendProcess terminates the whole backend process tree on Windows", () => {
  const calls = [];
  const child = {
    pid: 4321,
    killed: false,
    exitCode: null,
    kill() {
      calls.push(["child.kill"]);
    },
  };

  stopBackendProcess(child, {
    platform: "win32",
    killProcessTree(pid) {
      calls.push(["tree.kill", pid]);
      return { status: 0 };
    },
  });

  assert.deepEqual(calls, [["tree.kill", 4321]]);
});

test("stopBackendProcess falls back to child.kill when Windows tree termination fails", () => {
  const calls = [];
  const child = {
    pid: 4321,
    killed: false,
    exitCode: null,
    kill() {
      calls.push(["child.kill"]);
    },
  };

  stopBackendProcess(child, {
    platform: "win32",
    killProcessTree(pid) {
      calls.push(["tree.kill", pid]);
      return { status: 1 };
    },
  });

  assert.deepEqual(calls, [["tree.kill", 4321], ["child.kill"]]);
});

test("stopBackendProcess uses the normal child signal outside Windows", () => {
  const calls = [];
  const child = {
    pid: 4321,
    killed: false,
    exitCode: null,
    kill() {
      calls.push(["child.kill"]);
    },
  };

  stopBackendProcess(child, {
    platform: "linux",
    killProcessTree() {
      calls.push(["tree.kill"]);
    },
  });

  assert.deepEqual(calls, [["child.kill"]]);
});

test(
  "stopBackendProcess removes a real Windows descendant process",
  { skip: process.platform !== "win32", timeout: 10_000 },
  async () => {
    const parentScript = `
      const { spawn } = require("node:child_process");
      const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], {
        stdio: "ignore",
        windowsHide: true,
      });
      process.send(child.pid);
      setInterval(() => {}, 1000);
    `;
    const parent = spawn(process.execPath, ["-e", parentScript], {
      stdio: ["ignore", "ignore", "ignore", "ipc"],
      windowsHide: true,
    });
    const [descendantPid] = await once(parent, "message");
    const parentExit = once(parent, "exit");

    try {
      stopBackendProcess(parent);
      await parentExit;
      await waitUntilProcessExits(descendantPid);
      assert.equal(isProcessRunning(descendantPid), false);
    } finally {
      if (parent.exitCode === null) {
        parent.kill();
      }
      if (isProcessRunning(descendantPid)) {
        process.kill(descendantPid);
      }
    }
  },
);

async function waitUntilProcessExits(pid, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  while (isProcessRunning(pid) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
}

function isProcessRunning(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
