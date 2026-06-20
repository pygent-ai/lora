# Lora Command Center Visual Redesign

Date: 2026-06-02

## Goal

Upgrade the existing PySide6 GUI from a functional conversation workbench into a polished Agent Command Center. The redesign should make Lora feel like a premium engineering tool: calm, precise, observable, and visually memorable.

The project must keep the current light/dark theme switch. The redesign should make both modes feel intentional instead of treating the light theme as a simple inversion of the dark theme.

## Product Direction

The target experience is a high-end AI operations console:

- left side: session and workspace navigation,
- center: conversation and execution canvas,
- right side: live telemetry, trace, tool calls, files, and runtime state,
- top-level feel: compact, serious, readable, and richly layered.

The memorable quality should be "I can see the agent working." The GUI should not look like a generic chat app. Tool calls, trace events, and run status should become first-class visual objects.

## Design Principles

1. Make observability beautiful.
   Runtime events, tool calls, files, errors, and state transitions should be visually structured, not hidden as raw tree rows.

2. Keep density professional.
   This is an engineering workbench. Use compact controls, clear hierarchy, and scan-friendly alignment instead of oversized marketing-style panels.

3. Use restrained drama.
   The app can have depth, glow, motion, and texture, but only where they reinforce status or focus.

4. Preserve interaction clarity.
   Every visual upgrade must keep session selection, message sending, theme switching, settings, and inspector navigation obvious.

5. Treat day and night as sibling themes.
   Day mode should feel like a bright technical studio. Night mode should feel like a dark command console. Both should share component structure and state semantics.

## Visual Language

### Night Theme

Night is the signature mode.

- Background: deep graphite, not pure black.
- Surfaces: layered charcoal panels with subtle border contrast.
- Primary accent: cyan-teal for active/running states.
- Secondary accent: cool blue for selection and navigation.
- Warning accent: amber.
- Error accent: red-coral.
- Success accent: green-teal.

Night mode should avoid heavy purple gradients and avoid flat dark-blue monotony. The palette should feel technical, mineral, and precise.

### Day Theme

Day mode should not become plain white.

- Background: cool porcelain gray.
- Surfaces: white and very light blue-gray.
- Primary accent: deep teal or blue-gray.
- Borders: soft but visible.
- Status colors: same semantic family as night mode, adjusted for contrast.

Day mode should preserve the Command Center feel through structure, spacing, and state color rather than relying on dark ambience.

### Typography

Use the existing Windows-friendly CJK stack, but define clear type roles:

- brand/title: larger, high weight, tight but not oversized,
- section labels: uppercase or small caps style through weight/spacing where Qt supports it cleanly,
- body text: readable 13-14 px,
- metadata: 11-12 px,
- code/tool arguments: monospace family where practical.

Chinese and English must both remain legible. Avoid fonts that look good for English but weak for CJK.

### Shape And Depth

Use a more precise radius scale:

- 6 px for small tool chips and inline status tags,
- 8 px for cards and rows,
- 10-12 px for major panes,
- no oversized pill shapes unless the control is truly a segmented toggle.

Major panes should feel layered, but not like nested cards inside cards. Use bands, borders, and section headers rather than stacking decorative containers.

## Main Layout

Keep the current three-pane shell, but make it more deliberate.

### Top-Level Shell

The shell remains:

- `SessionSidebar`
- `ChatPane`
- `TraceInspector`

The window should gain stronger layout rhythm:

- slightly tighter outer margins,
- consistent gutter spacing,
- aligned top baselines across the three panes,
- fixed sidebar and inspector widths tuned for dense scanning,
- center pane receives most horizontal breathing room.

Optional future enhancement: a slim global command/status bar above the three panes with workspace, agent, model, active run status, and quick actions. This should only be introduced if it does not crowd the main three-pane layout.

## Session Sidebar

The sidebar should become a compact navigation and context panel.

### Structure

Top area:

- Lora brand,
- workspace path,
- active agent/model summary,
- new chat command.

Middle:

- recent sessions list,
- selected session emphasized with an accent rail and stronger surface,
- session rows show title and optional small metadata when available.

Bottom:

- theme segmented control,
- settings command,
- optional app/runtime status line.

### Session Rows

Rows should evolve from simple rounded buttons into compact navigation items:

- left accent rail for selected state,
- title with ellipsis,
- optional timestamp or short id in muted text,
- delete button visible on hover/focus or visually subdued by default,
- hover state with subtle border and surface lift.

The row height can increase slightly if metadata is added, but should stay dense enough for many sessions.

## Chat Pane

The chat pane becomes the execution canvas.

### Header

The header should show more than a raw session id:

- session title or shortened id,
- current run status,
- optional model/agent chip,
- error/running/completed state indicator.

The header should feel integrated with the pane, not like a generic label.

### Message Presentation

Messages should remain readable and compact:

- user messages align right,
- assistant messages align left,
- avatars/icons are reduced and refined,
- bubbles have role-specific surface treatments,
- markdown-like text should be easier to read,
- code/tool snippets should use a monospace style and stronger containment.

User and assistant bubbles should differ by more than alignment. For example:

- user: cleaner solid or lightly tinted surface,
- assistant: layered surface with a subtle left accent or icon,
- errors: dedicated error block, not just assistant text prefixed with `Error:`.

### Runtime Events In Chat

Tool calls should become execution cards inside the transcript:

- collapsed by default when completed,
- expanded while running,
- title contains tool name,
- body contains arguments/result summary,
- status is visible: running, success, warning, error,
- tool result visually links to its originating tool call where possible.

This is one of the highest-impact upgrades. It makes the agent feel observable rather than opaque.

### Composer

The composer should feel like a command input:

- larger, cleaner input surface,
- send button uses an icon plus text or icon-only if a tooltip is added,
- running state changes the action area into a clear "Running" or future stop control,
- placeholder text remains short and direct.

The input should avoid layout jumps when the button text changes from `Send` to `Running`.

## Trace Inspector

The inspector should become a telemetry panel rather than a plain tabbed tree.

### Header

Show:

- current status,
- session id,
- run id,
- live/completed/error indicator,
- compact config summary if space allows.

Status should use semantic color and a small visual indicator.

### Tabs

Keep the existing tabs:

- Events
- Tools
- Files
- Config

Restyle tabs as compact segmented navigation. The selected tab should be obvious without taking much vertical space.

### Events View

Events should use a timeline treatment:

- event type grouped or filtered,
- each event row has icon/status color,
- timestamp and actor metadata are muted,
- detail expands on demand,
- error and warning events stand out.

The current `QTreeWidget` can remain initially, but the visual goal is a timeline-like component. A later implementation may replace it with custom row widgets if needed.

### Tools View

Tool calls should be the strongest inspector view:

- tool name,
- call/result state,
- duration if available,
- compact argument preview,
- expandable JSON detail,
- error result clearly marked.

### Files View

File activity should summarize impact:

- filename,
- event type,
- relative path,
- created/modified/deleted/diff state,
- expandable detail.

### Config View

Config should be structured as key/value rows:

- workspace,
- Lora root,
- agent,
- model,
- API key source,
- base URL,
- max steps.

Avoid raw list styling. This view should look like an inspector property sheet.

## Theme System

Refactor `src/gui/theme.py` into a token-oriented theme system.

Recommended structure:

```text
ThemePalette
  name
  colors
  typography
  spacing
  radius
  state_colors
```

The stylesheet can still be emitted as QSS, but it should be built from named tokens. This allows day and night to share component rules while using different palettes.

Required tokens:

- `bg`
- `surface`
- `surface_alt`
- `surface_elevated`
- `border`
- `border_strong`
- `text`
- `text_muted`
- `text_soft`
- `accent`
- `accent_hover`
- `accent_text`
- `selection`
- `input`
- `success`
- `warning`
- `error`
- `running`

Component-specific selectors should remain stable because tests already assert several object names.

## Icons

Introduce a consistent icon strategy.

Preferred options:

1. Use Qt standard icons only where they are acceptable.
2. Add a small local SVG icon set for command-center actions and status.
3. Optionally adopt an icon package if project policy allows another dependency.

Icons should cover:

- new chat,
- settings,
- send,
- running,
- completed,
- error,
- tool,
- file,
- trace/event,
- theme day/night,
- delete.

Do not rely on icon-only controls unless each has a tooltip and accessible text where Qt supports it.

## Motion And Micro-Interactions

Motion should be subtle:

- new message fade/slide-in,
- tool card expand/collapse animation,
- running indicator pulse,
- selected session transition,
- hover border/surface transition,
- theme toggle feedback.

Animations should be short and should not block runtime updates. If PySide6 animation complexity grows too much, prioritize static polish first.

## Component Mapping

Primary files expected to change during implementation:

- `src/gui/theme.py`
- `src/gui/main_window.py`
- `src/gui/widgets/sessions.py`
- `src/gui/widgets/chat.py`
- `src/gui/widgets/inspector.py`
- `src/gui/widgets/settings.py`

Likely test files:

- `tests/unit/test_gui_theme.py`
- `tests/unit/test_gui_chat_widget.py`
- `tests/unit/test_gui_inspector.py`
- `tests/unit/test_gui_session_sidebar.py`
- `tests/unit/test_gui_main_window.py`

## Implementation Strategy

The redesign should be implemented in vertical slices:

1. Theme tokens and base QSS.
2. Shell and pane styling.
3. Sidebar row redesign.
4. Chat message and composer redesign.
5. Tool/runtime event cards in chat.
6. Inspector telemetry restyling.
7. Icon pass.
8. Motion pass.
9. Visual QA in both day and night themes.

This sequence keeps visual progress visible while preserving the existing application behavior.

## Testing And Verification

Automated tests should verify:

- day and night themes are still available,
- stylesheet contains expected stable selectors,
- theme switching updates the sidebar segment state,
- chat running state does not resize the send button unexpectedly,
- session rows keep selected state styling,
- inspector can render empty, running, completed, and error states,
- tool call/result rows render without losing detail payloads.

Manual verification should cover:

- launch the GUI,
- inspect day theme,
- inspect night theme,
- create/select/delete sessions,
- send a message,
- observe streaming output,
- observe tool call/result rendering,
- inspect Events, Tools, Files, and Config tabs,
- resize the window to common desktop sizes,
- confirm text does not overlap or clip.

Visual verification should include screenshots for:

- day idle,
- day running,
- night idle,
- night running,
- inspector with populated trace data.

## Out Of Scope

This redesign does not include:

- changing core Lora runtime behavior,
- changing session persistence semantics,
- adding a web frontend,
- adding installer packaging,
- adding full case authoring UI,
- adding regression dashboard workflows.

These can be designed separately after the Command Center visual upgrade lands.

## Implementation Decisions

Use a local SVG icon set for the redesigned GUI. This gives the strongest visual control without adding a runtime dependency. Qt standard icons may remain as temporary fallbacks while the local icon set is introduced, but the finished redesign should use one coherent icon language.

Defer the global command/status bar from the first implementation pass. Polish the three existing panes first. Add the global bar only in a later design if the interface still needs stronger workspace, model, and run context after the pane redesign.

## Acceptance Criteria

The redesign is successful when:

- the GUI clearly reads as a polished Agent Command Center,
- day and night themes both feel complete,
- the user can understand current run state at a glance,
- tool calls and trace events are visually prominent and easy to scan,
- no existing core runtime behavior changes,
- existing GUI tests are updated or extended to cover the new stable behavior,
- manual screenshots show no clipping, overlap, or incoherent layout at normal desktop sizes.
