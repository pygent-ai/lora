import assert from "node:assert/strict";
import test from "node:test";
import { cleanProjectPath, isAbsoluteProjectPath, projectChoices, projectPathKey } from "../features/projects/projectPaths.js";
import { settingsPayload } from "../shared/api/client.js";

test("pasted Windows paths accept Explorer quotes, spaces, drives and network shares", () => {
  assert.equal(cleanProjectPath('  "C:\\My Projects\\lora"  '), "C:\\My Projects\\lora");
  for (const path of ['"C:\\My Projects\\lora"', "C:/", "\\\\server\\share", "/home/user/project"]) {
    assert.equal(isAbsoluteProjectPath(path), true, path);
  }
  for (const path of ["", "lora", "C:relative", "C:/one\nC:/two"]) {
    assert.equal(isAbsoluteProjectPath(path), false, path);
  }
});

test("recent projects deduplicate Windows casing and separators without merging POSIX case", () => {
  const projects = [
    { workspace_root: "c:/work/lora/", label: "lora" },
    { workspace_root: "D:\\work\\lora", label: "lora" },
    { workspace_root: "/work/Lora" }, { workspace_root: "/work/lora" },
  ];
  assert.equal(projectChoices(projects, "C:\\work\\lora").length, 4);
  assert.equal(projectChoices(projects, "C:\\work\\lora", "d:/work")[0].workspace_root, "D:\\work\\lora");
  assert.notEqual(projectPathKey("/work/Lora"), projectPathKey("/work/lora"));
});

test("switching projects leaves model routes and context settings untouched", () => {
  assert.deepEqual(settingsPayload({ workspaceRoot: "C:/project", agent: "" }), {
    workspace_root: "C:/project", agent_alias: "",
  });
  assert.deepEqual(settingsPayload({ contextWindow: "" }), { context_window: null });
});
