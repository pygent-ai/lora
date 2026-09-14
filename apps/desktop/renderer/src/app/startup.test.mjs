import assert from "node:assert/strict";
import test from "node:test";
import { waitForDesktopBackend } from "./startup.js";

test("browser previews do not wait for Electron IPC", async () => {
  await waitForDesktopBackend({ desktop: {}, delay: () => assert.fail("unexpected delay") });
});

test("slow packaged backends remain on startup screen until ready", async () => {
  let checks = 0;
  await waitForDesktopBackend({
    desktop: { getBackendStatus: async () => ({ state: ++checks > 40 ? "ready" : "starting" }) },
    delay: async () => {},
  });
  assert.equal(checks, 41);
});

test("backend startup failures are surfaced rather than left on an empty page", async () => {
  for (const state of ["error", "exited", "stopping"]) {
    await assert.rejects(waitForDesktopBackend({
      desktop: { getBackendStatus: async () => ({ state, error: "backend failed" }) },
    }), /backend failed/);
  }
});

test("startup is bounded even when the backend never becomes ready", async () => {
  let elapsed = 0;
  await assert.rejects(waitForDesktopBackend({
    desktop: { getBackendStatus: async () => ({ state: "starting" }) },
    now: () => elapsed, timeoutMs: 100,
    delay: async () => { elapsed += 50; },
  }), /启动超时/);
});
