import test from "node:test";
import assert from "node:assert/strict";
import { purposeCapabilities, updateCapability } from "./modelCapabilities.js";

test("purpose templates preserve limits without mutating current capabilities", () => {
  const current = purposeCapabilities("agentic", { limits: { context_tokens: 32000, max_output_tokens: 4000 } });
  for (const id of ["chat", "agentic", "understanding", "generation"]) {
    const next = purposeCapabilities(id, current);
    assert.deepEqual(next.limits, current.limits);
    assert.notEqual(next.limits, current.limits);
    assert.ok(next.streaming.output.every(type => next.modalities.output.includes(type)));
  }
  assert.equal(current.tools.call, true);
});
test("removing an output modality removes only its corresponding stream", () => {
  const value = purposeCapabilities("chat");
  value.modalities.output = ["text", "audio"];
  value.streaming.output = ["text", "audio"];
  const next = updateCapability(value, "modalities", "output", ["text"]);
  assert.deepEqual(next.streaming.output, ["text"]);
  assert.deepEqual(value.streaming.output, ["text", "audio"]);
});
test("disabling reasoning also disables controllability", () => {
  const value = purposeCapabilities("chat");
  value.reasoning = { supported: true, controllable: true };
  assert.deepEqual(updateCapability(value, "reasoning", "supported", false).reasoning, { supported: false, controllable: false });
  assert.equal(value.reasoning.controllable, true);
});
