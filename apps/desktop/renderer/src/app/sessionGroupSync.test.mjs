import assert from "node:assert/strict";
import test from "node:test";
import { createSessionGroupSync } from "./sessionGroupSync.js";

const flush = () => new Promise((resolve) => setImmediate(resolve));

function surfaces() {
  const timers = new Map();
  let id = 0;
  const window = Object.assign(new EventTarget(), {
    setTimeout(callback) { timers.set(++id, callback); return id; },
    clearTimeout(timer) { timers.delete(timer); },
  });
  const document = Object.assign(new EventTarget(), { visibilityState: "visible" });
  return {
    window, document, timers,
    async tick() {
      const callbacks = [...timers.values()];
      timers.clear();
      callbacks.forEach((callback) => callback());
      await flush();
    },
  };
}

test("background sync discovers externally created sessions and status changes without chat operations", async () => {
  const env = surfaces();
  let groups = [{ sessions: [{ session_id: "parent" }] }];
  const updates = [];
  const sync = createSessionGroupSync({ listSessionGroups: async () => ({ groups }) }, (value) => updates.push(value));
  const stop = sync.start(env);
  await env.tick();
  groups = [{ sessions: [{ session_id: "parent" }, { session_id: "child", last_case_run_status: "running" }] }];
  await env.tick();
  assert.deepEqual(updates.at(-1), groups);
  groups = [{ sessions: [{ session_id: "child", last_case_run_status: "completed" }] }];
  env.window.dispatchEvent(new Event("focus"));
  await flush();
  assert.deepEqual(updates.at(-1), groups);
  stop();
  assert.equal(env.timers.size, 0);
});

test("older requests cannot overwrite a newer explicit refresh", async () => {
  const responses = [];
  const updates = [];
  const sync = createSessionGroupSync({ listSessionGroups: () => new Promise((resolve) => responses.push(resolve)) }, (groups) => updates.push(groups));
  const old = sync.refresh();
  const latest = sync.refresh();
  responses[1]({ groups: ["new"] });
  await latest;
  responses[0]({ groups: ["old"] });
  await old;
  assert.deepEqual(updates, [["new"]]);
});

test("hidden windows pause requests, failures retry, and returning to the window refreshes", async () => {
  const env = surfaces();
  let calls = 0;
  const updates = [];
  const sync = createSessionGroupSync({ listSessionGroups: async () => {
    if (++calls === 1) throw new Error("offline");
    return { groups: ["recovered"] };
  } }, (groups) => updates.push(groups));
  const stop = sync.start(env);
  await env.tick();
  await env.tick();
  assert.deepEqual(updates, [["recovered"]]);
  env.document.visibilityState = "hidden";
  await env.tick();
  assert.equal(calls, 2);
  env.document.visibilityState = "visible";
  env.document.dispatchEvent(new Event("visibilitychange"));
  await flush();
  assert.equal(calls, 3);
  stop();
  env.window.dispatchEvent(new Event("focus"));
  await env.tick();
  assert.equal(calls, 3);
});

test("polls do not overlap and unmount aborts and ignores pending responses", async () => {
  const env = surfaces();
  let resolve;
  let signal;
  let calls = 0;
  const updates = [];
  const sync = createSessionGroupSync({ listSessionGroups: (options) => {
    calls++;
    signal = options.signal;
    return new Promise((done) => { resolve = done; });
  } }, (groups) => updates.push(groups));
  const stop = sync.start(env);
  await env.tick();
  env.window.dispatchEvent(new Event("focus"));
  assert.equal(calls, 1);
  stop();
  assert.equal(signal.aborted, true);
  resolve({ groups: ["stale"] });
  await flush();
  assert.deepEqual(updates, []);
  assert.equal(env.timers.size, 0);
});
