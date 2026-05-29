# User Prompt Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement structured user-message wrapping and triggered system reminders for CLI context with optional time.

**Architecture:** Add resolved user/CLI configuration to `RunConfig`, wrap new user messages before they enter runtime history, and add a request-scoped dynamic `runtime.system_reminder` module backed by session state. CLI reminders are pending-state driven, so ordinary events do not refresh them.

**Tech Stack:** Python dataclasses, current YAML-subset config parser, existing prompt module framework, pytest/unittest tests.

---

### Task 1: Config And Wrapper Tests

**Files:**
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_runtime_adapter.py`
- Modify: `src/lora/schema/models.py`
- Modify: `src/lora/config.py`
- Modify: `src/lora/runtime.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert `user.identity` and `cli.bash.presets` resolve into `RunConfig`, and that `run_turn()` stores wrapped model-visible user content while preserving raw content in the event payload.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/unit/test_config.py tests/unit/test_runtime_adapter.py -q`
Expected: failure because `RunConfig` has no user/CLI fields and user messages are not wrapped.

- [ ] **Step 3: Implement minimal config and wrapper**

Add small dataclasses for CLI presets/config, resolve defaults in `load_run_config()`, and add `wrap_user_message()` in runtime. Use the wrapper when appending user turns.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_config.py tests/unit/test_runtime_adapter.py -q`
Expected: pass.

### Task 2: Triggered System Reminder Tests

**Files:**
- Modify: `tests/unit/test_runtime_adapter.py`
- Modify: `tests/scenario/test_cli_flow.py`
- Modify: `src/lora/agent.py`

- [ ] **Step 1: Write failing tests**

Add tests for first-session CLI reminder with time, no repeated full CLI reminder on later turns, no system reminder when no pending state exists, and one-shot pending new CLI with `include_time=true`.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/unit/test_runtime_adapter.py tests/scenario/test_cli_flow.py -q`
Expected: failure because `runtime.system_reminder` and CLI reminder state do not exist yet.

- [ ] **Step 3: Implement reminder state and dynamic module**

Add a session-state helper in `agent.py`, register `runtime.system_reminder`, render first-session CLI context and pending new CLI entries, and consume pending state only after prompt rendering is persisted.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_runtime_adapter.py tests/scenario/test_cli_flow.py -q`
Expected: pass.

### Task 3: CLI Change Detection Tests

**Files:**
- Modify: `tests/unit/test_runtime_adapter.py`
- Modify: `src/lora/agent.py`

- [ ] **Step 1: Write failing test**

Add a focused test that seeds known CLI state as unavailable, simulates a tool-result-triggered CLI availability change, and asserts the next prompt includes `<new-bash-cli>` once.

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/unit/test_runtime_adapter.py -q`
Expected: failure because no change detector marks pending CLI entries.

- [ ] **Step 3: Implement conservative detector**

After bash tool execution, re-check configured presets that are not known as installed and append newly available tools to pending state with `include_time=true`.

- [ ] **Step 4: Run relevant tests**

Run: `pytest tests/unit/test_runtime_adapter.py tests/unit/test_config.py tests/scenario/test_cli_flow.py -q`
Expected: pass.

### Task 4: Final Verification

**Files:**
- All modified source and tests.

- [ ] **Step 1: Run full test suite**

Run: `pytest -q`
Expected: pass, or report unrelated pre-existing failures with evidence.

- [ ] **Step 2: Review diff**

Run: `git diff --stat` and inspect changed hunks to ensure only intended files were modified.
