import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { resolveBackendLaunch } from "./backendProcess.mjs";

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
