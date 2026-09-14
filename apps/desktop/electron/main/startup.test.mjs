import assert from "node:assert/strict";
import test from "node:test";
import { launchDesktop } from "./startup.mjs";

test("renderer loads while backend is still warming up, after its port is selected", async () => {
  let finishBackend;
  let windowLoaded = false;
  let prepared = false;
  const ready = new Promise((resolve) => { finishBackend = resolve; });
  const launch = launchDesktop({
    prepareBackend: async () => { prepared = true; return { ready }; },
    createWindow: async () => { assert.equal(prepared, true); windowLoaded = true; },
    isQuitting: () => false,
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(windowLoaded, true);
  finishBackend();
  await launch;
});

test("quitting during backend preparation does not reopen a window", async () => {
  await launchDesktop({
    prepareBackend: async () => ({ ready: Promise.resolve() }),
    createWindow: () => assert.fail("must not reopen during shutdown"),
    isQuitting: () => true,
  });
});
