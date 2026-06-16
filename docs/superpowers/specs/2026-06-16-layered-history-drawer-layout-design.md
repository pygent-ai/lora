# Layered History Drawer Layout Design

Date: 2026-06-16

## Goal

Change the GUI shell from three equal top-level panes into a layered command-center layout:

- the session history becomes a lower-priority background navigation layer,
- the history layer can collapse into a slim rail,
- the conversation pane and trace inspector become the foreground workbench layer.

This preserves the existing conversation, session, project, theme, settings, and trace behaviors while changing the visual hierarchy and resizing model.

## Current Layout

`src/gui/main_window.py` currently creates a single `CentralShell` with a `QHBoxLayout`:

```text
SessionSidebar | ChatPane | TraceInspector
```

The sidebar and inspector have fixed widths, while the chat pane receives the remaining space. This makes session history a peer of the active work area and causes the sidebar to permanently compete with the chat transcript for horizontal space.

## Desired Layout

The shell should become a layered container:

```text
CentralShell
+-- HistoryLayer
|   `-- SessionSidebar
`-- WorkbenchLayer
    |-- ChatPane
    `-- TraceInspector
```

`HistoryLayer` sits underneath the workbench and owns the left navigation region. `WorkbenchLayer` sits above it and contains the active conversation and event inspector as one foreground surface.

## Visual Hierarchy

### History Layer

The history layer should read as navigation infrastructure, not as the primary content surface.

- Expanded width: 320 px.
- Collapsed width: 64 px.
- Background should be weaker than the workbench: lower contrast, softer border, and less visual weight.
- Expanded mode shows the full existing `SessionSidebar`.
- Collapsed mode shows a slim rail with essential actions:
  - Lora identity,
  - expand/collapse control,
  - new chat,
  - active session affordance,
  - settings/theme access.

The collapsed rail should remain useful without showing dense session rows. Full session browsing happens in expanded mode.

### Workbench Layer

The workbench layer should read as the current active workspace.

- It contains `ChatPane` and `TraceInspector`.
- It is visually elevated above the history layer through stronger border, surface, and optional shadow.
- It keeps a horizontal internal layout:

```text
ChatPane | TraceInspector
```

- `ChatPane` receives flexible width.
- `TraceInspector` keeps a fixed or bounded width around 400-420 px.

The chat and inspector should feel like paired foreground instruments: conversation on the left, live telemetry on the right.

## Layout Behavior

### Collapsed History

When history is collapsed:

- `HistoryLayer` width is 64 px.
- `WorkbenchLayer` starts after the rail with a small gap.
- The chat pane gets maximum usable width.
- The inspector remains visible if the window is wide enough.

Recommended geometry:

```text
left rail: 64 px
outer margin: 12-16 px
workbench left offset: 76-84 px
gutter between chat and inspector: 14 px
inspector width: 400-420 px
```

### Expanded History

When history is expanded:

- `HistoryLayer` width is 320 px.
- On wide windows, `WorkbenchLayer` shifts right so the full history remains visible.
- On narrower windows, the expanded history may behave like an overlay drawer above the background but beneath or beside the workbench, as long as chat remains usable.

Recommended wide-window geometry:

```text
history width: 320 px
workbench left offset: 300-336 px
outer margin: 12-16 px
```

### Minimum Width Handling

At constrained widths:

- keep the history collapsed by default,
- allow the inspector to shrink to a lower bound before hiding is considered,
- keep chat as the primary surface,
- do not allow controls or header text to overlap.

This design does not require adding an inspector collapse control in the first implementation pass.

## Interaction

### Collapse Control

Add a clear icon button near the top of the history layer.

- In expanded mode, it collapses the history into a rail.
- In collapsed mode, it expands history.
- It must have a tooltip.
- The collapsed state should persist for the current window lifetime. Persisting it to `GuiProjectState` can be added if it fits existing state patterns without broad refactoring.

### Session Selection

Session selection behavior stays unchanged:

- selecting a session still calls the existing `session_selected` signal,
- the active session still renders in `ChatPane`,
- `TraceInspector` still resets to the selected session context.

In collapsed mode, selecting arbitrary older sessions is not required from the rail. The user expands history for full browsing.

### New Chat

The new chat action remains available in both modes:

- expanded mode uses the existing full button,
- collapsed mode uses an icon-only rail button with tooltip.

### Theme And Settings

Theme and settings remain available:

- expanded mode uses existing controls,
- collapsed mode uses compact icon controls or an overflow-style compact section.

## Component Responsibilities

### `src/gui/main_window.py`

Owns the new top-level shell composition.

Expected changes:

- replace the direct three-widget `QHBoxLayout` with a layered shell widget,
- place `SessionSidebar` in the background history layer,
- place `ChatPane` and `TraceInspector` in the foreground workbench layer,
- wire collapse state changes to shell geometry updates,
- keep existing signal connections intact.

### `src/gui/widgets/sessions.py`

Owns expanded and collapsed sidebar presentation.

Expected changes:

- add a collapsed mode API,
- add a collapse toggle signal,
- expose compact rail controls,
- hide dense session group contents when collapsed,
- preserve existing expanded sidebar behavior.

### `src/gui/theme.py`

Owns QSS styling for the new layers and states.

Expected changes:

- add selectors for `HistoryLayer`, `HistoryRail`, `WorkbenchLayer`, and collapse controls,
- reduce visual weight of history in comparison to workbench,
- preserve current day/night theme tokens,
- avoid changing unrelated widget object names that tests depend on.

### Tests

Expected test coverage:

- main window creates a layered shell and keeps sidebar, chat, and inspector connected,
- sidebar can switch between expanded and collapsed states,
- collapsed sidebar keeps new chat and settings/theme controls reachable,
- theme stylesheet includes the new layer selectors,
- existing session selection and inspector reset tests keep passing.

## Implementation Strategy

Implement in small vertical steps:

1. Add tests for the shell structure and sidebar collapse API.
2. Introduce the layered shell in `MainWindow`.
3. Add collapsed mode to `SessionSidebar`.
4. Add QSS for background history and foreground workbench layers.
5. Run focused GUI tests.
6. Run a broader GUI unit test subset.

## Non-Goals

This layout change does not include:

- changing runtime execution behavior,
- changing session persistence semantics,
- replacing `TraceInspector` with a new inspector component,
- adding a global command bar,
- adding inspector collapse behavior,
- redesigning chat rows or tool cards beyond spacing needed for the new shell.

## Acceptance Criteria

The change is successful when:

- history visually reads as a lower-priority navigation layer,
- history can collapse to a compact rail,
- chat and inspector read as a foreground workbench,
- the chat pane has more breathing room when history is collapsed,
- existing chat, session, project, theme, settings, and inspector behavior remains intact,
- focused GUI tests pass for the new layout behavior.
