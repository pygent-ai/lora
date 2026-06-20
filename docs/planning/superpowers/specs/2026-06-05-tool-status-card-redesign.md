# Tool Status Card Redesign

Date: 2026-06-05

## Goal

Redesign the tool transcript UI inside the chat pane so tool execution feels observable, lightweight, and visually structured.

The redesign should replace the current chip-like tool summary with a status-aware collapsible row:

- collapsed: a single-line summary row matching the user's reference,
- expanded: one timeline-style card with a left status rail and a right content card,
- running: animated emphasis while execution is in progress,
- success: soft green completion surface,
- error: soft red failure surface.

The implementation should preserve the current message ordering and tool-call grouping behavior.

## Scope

This change applies only to the tool display inside `ChatPane`.

In scope:

- `_ToolGroupWidget` visual structure,
- per-tool-call metadata captured for display,
- chat theme styling for collapsed and expanded tool states,
- tests covering history replay and live runtime updates.

Out of scope:

- inspector tool tree redesign,
- changing the grouping model from one group to many cards,
- new retry / copy / raw payload actions,
- changing how tools are persisted by the runtime.

## User-Facing Behavior

### Collapsed State

When the tool group is collapsed, show a single horizontal summary row:

- left: one-line description text,
- right: a `>` collapse indicator,
- no leading status pill.

State styling:

- running: the row text should animate with a soft pulse or blink effect,
- success: the row background becomes light green,
- error: the row background becomes light red.

The summary text should reflect the most recent tool call description in the group, consistent with the current transcript behavior.

### Expanded State

When the tool group is expanded, render a single composite card:

- left side: a vertical status rail,
- rail includes a green circular indicator for successful items,
- rail includes a red circular indicator for failed items,
- rail includes a running indicator for active items,
- rail items are connected by a vertical line.

Right side: one large card containing the ordered tool calls for the group.

Each tool-call row inside the card shows:

- first line: tool description,
- second line: status label plus tool start time,
- far right: elapsed duration.

The expanded representation stays a single card, not one separate card per tool call.

## Data Model Changes

Extend the tool call state captured by chat widgets so each call stores:

- `started_at` as a display-ready timestamp or source datetime,
- `finished_at` when a result arrives,
- derived duration text for expanded rendering.

If only partial timing data is available:

- running items show the start time and a live / pending duration label,
- completed items show computed elapsed time,
- missing data falls back gracefully without exposing internal ids.

## Data Sources

Tool calls can arrive from:

- persisted chat history,
- live `InspectorEvent` runtime events.

The redesign should preserve matching by `tool_call_id` where available, and continue falling back to the latest running call when ids are absent.

For live runtime events, start time is captured when the tool call enters the chat transcript.

For persisted history, if no timestamp exists in the stored payload, the UI may use a neutral fallback such as an empty time label rather than inventing incorrect times.

## Interaction Model

The existing group interaction remains:

- tool calls arriving back-to-back join the same `_ToolGroupWidget`,
- assistant prose before or after tools still breaks the grouping as it does now,
- users can toggle expanded / collapsed state through the right-side chevron button.

Chevron behavior:

- collapsed: `>`,
- expanded: downward indicator.

The toggle target should remain compact and easy to hit without dominating the row.

## Visual Design Direction

The component should feel like precise execution telemetry rather than a generic accordion.

### Collapsed Row

- flat, concise, horizontally aligned,
- subtle border,
- state-colored fill only after completion or error,
- animation only for running state,
- text-first hierarchy.

### Expanded Card

- left rail is visually separate from the content card,
- right card has soft elevation and rounded corners,
- metadata line uses semantic status color and subdued time text,
- duration appears as a compact badge or aligned text block on the right.

The design should work in both day and night themes, using the existing palette system instead of hardcoded one-off values.

## Implementation Plan

### Chat Widget

Update `src/gui/widgets/chat.py` to:

- enrich `_ToolCallState` with timing metadata,
- capture timing when calls are created and when results are applied,
- replace the current expanded detail rows with a timeline-card layout,
- adjust collapsed summary layout to match the requested single-line style,
- keep tool ordering and result matching behavior unchanged.

### Theme

Update `src/gui/theme.py` to add styling for:

- collapsed summary row states,
- animated running text treatment,
- timeline rail and status dots,
- expanded content card,
- metadata and duration labels.

### Tests

Update `tests/unit/test_gui_chat_widget.py` to verify:

- collapsed rows still summarize grouped tool calls,
- expanded state renders ordered tool descriptions,
- status colors / properties still reflect running, success, and error,
- tool results still attach to the correct call by id,
- unknown tool results still avoid showing internal ids.

## Error Handling

If a tool result arrives without a known matching call:

- keep the existing suppression behavior,
- do not create a visible card showing only an internal tool id.

If a running tool has no completion event yet:

- keep it visible as running,
- show no false success styling,
- allow expanded view to display a running duration placeholder.

## Testing Strategy

Primary verification:

- unit tests for chat widget ordering, grouping, and expanded rendering,
- manual GUI verification in both themes,
- manual verification for three states: running, success, error.

Manual checks should confirm:

- collapsed running row animates,
- success row turns light green,
- error row turns red,
- expanded card shows left status rail and right content card,
- each item shows description, status + start time, and duration.

## Risks

1. Existing tests assume the older flat detail rows.
   The tests will need to assert the new structure without overfitting to pixel details.

2. Persisted history may not carry timestamps.
   The UI must degrade gracefully instead of fabricating times.

3. Qt stylesheet animation support is limited.
   Running-state blinking may require a timer-driven property change in the widget rather than pure QSS.

## Recommendation

Implement the redesign by evolving the existing `_ToolGroupWidget` rather than replacing the grouping system.

This keeps the behavioral model stable, minimizes regression risk, and delivers the requested visual change with the smallest structural surface area.
