import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { runDesktopDev } from "./dev.mjs";

function createHarness() {
  const calls = [];
  const child = new EventEmitter();
  child.connected = true;
  child.exitCode = null;
  child.killed = false;
  child.send = (message) => calls.push(["send", message]);
  child.kill = () => {
    child.killed = true;
    calls.push(["kill"]);
  };

  const viteServer = {
    resolvedUrls: { local: ["http://127.0.0.1:5173/"] },
    async listen() {
      calls.push(["vite:listen"]);
    },
    async close() {
      calls.push(["vite:close"]);
    },
  };

  return {
    calls,
    child,
    viteServer,
    signalSource: new EventEmitter(),
    createViteServer: async (config) => {
      calls.push(["vite:create", config]);
      return viteServer;
    },
    spawnProcess: (...args) => {
      calls.push(["electron:spawn", ...args]);
      return child;
    },
  };
}

test("development launcher starts Vite before Electron and forwards its URL", async () => {
  const harness = createHarness();
  const running = runDesktopDev({
    createViteServer: harness.createViteServer,
    spawnProcess: harness.spawnProcess,
    electronPath: "electron-test",
    root: "desktop-root",
    signalSource: harness.signalSource,
    logger: { log() {}, error() {} },
  });

  await new Promise((resolve) => setImmediate(resolve));
  harness.child.exitCode = 0;
  harness.child.emit("exit", 0, null);

  assert.equal(await running, 0);
  assert.equal(harness.calls[0][0], "vite:create");
  assert.deepEqual(harness.calls[0][1].server, { host: "127.0.0.1" });
  assert.equal(harness.calls[1][0], "vite:listen");
  assert.equal(harness.calls[2][0], "electron:spawn");
  assert.equal(harness.calls[2][1], "electron-test");
  assert.deepEqual(harness.calls[2][2], ["desktop-root"]);
  assert.equal(harness.calls[2][3].env.VITE_DEV_SERVER_URL, "http://127.0.0.1:5173/");
  assert.deepEqual(harness.calls[2][3].stdio, ["inherit", "inherit", "inherit", "ipc"]);
  assert.equal(harness.calls.at(-1)[0], "vite:close");
});

test("development launcher asks Electron to quit before closing Vite", async () => {
  const harness = createHarness();
  const running = runDesktopDev({
    createViteServer: harness.createViteServer,
    spawnProcess: harness.spawnProcess,
    electronPath: "electron-test",
    root: "desktop-root",
    signalSource: harness.signalSource,
    logger: { log() {}, error() {} },
    forceShutdownMs: 100,
  });

  await new Promise((resolve) => setImmediate(resolve));
  harness.signalSource.emit("SIGINT");

  assert.deepEqual(harness.calls.at(-1), ["send", { type: "lora:dev-shutdown" }]);
  assert.equal(harness.calls.some(([name]) => name === "vite:close"), false);

  harness.child.exitCode = 0;
  harness.child.emit("exit", 0, null);
  assert.equal(await running, 0);
  assert.equal(harness.calls.at(-1)[0], "vite:close");
});

test("development launcher boots a real Vite server before handing off to Electron", async () => {
  const child = new EventEmitter();
  child.connected = false;
  child.exitCode = null;
  child.killed = false;
  const root = fileURLToPath(new URL("..", import.meta.url));
  let receivedUrl;

  const exitCode = await runDesktopDev({
    root,
    signalSource: new EventEmitter(),
    logger: { log() {}, error() {} },
    spawnProcess: (_command, _args, options) => {
      receivedUrl = options.env.VITE_DEV_SERVER_URL;
      setImmediate(() => {
        child.exitCode = 0;
        child.emit("exit", 0, null);
      });
      return child;
    },
  });

  assert.equal(exitCode, 0);
  assert.match(receivedUrl, /^http:\/\/127\.0\.0\.1:\d+\/$/);
});
