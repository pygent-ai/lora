# Chat Transcript Componentized Rendering Design

Date: 2026-06-06

## Goal

Replace the chat transcript's current rich-text `QLabel` rendering path with a componentized Qt widget rendering system that follows the same technical direction as `SessionList`.

The redesign must preserve all current transcript behavior:

- streaming assistant output,
- grouped tool-call cards and expandable detail rows,
- selectable / copyable text,
- existing message ordering and persisted history replay.

The primary motivation is rendering stability. The current assistant Markdown path relies on `QTextDocument -> HTML -> QLabel`, which makes code blocks, quotes, spacing, and type treatment hard to control and inconsistent with browser previews. The new design should make transcript rendering deterministic and widget-driven.

## Scope

This change applies to the entire chat transcript area inside `ChatPane`.

In scope:

- user message rendering,
- assistant message rendering,
- thinking rows,
- grouped tool-call rows,
- assistant Markdown parsing into renderable blocks,
- streaming update behavior for assistant content,
- tests covering history replay and live runtime updates.

Out of scope:

- sidebar session list redesign,
- input composer redesign,
- runtime persistence format changes,
- syntax highlighting beyond stable notebook-style code blocks,
- inspector panel redesign.

## Recommendation

Implement the transcript using a document-block component architecture.

This is the selected `B` approach from design discussion:

- outer transcript rows are native widgets,
- assistant content is rendered as a list of document-block widgets,
- Markdown is parsed into a lightweight internal block model,
- no assistant transcript content is rendered through one large rich-text `QLabel`.

This approach keeps the rendering model aligned with `SessionList` and other native Qt widget sections while avoiding the complexity of a full custom text engine.

## Current Problem Summary

The current transcript uses:

- `QVBoxLayout` rows for top-level message placement,
- `QLabel` for user bubbles,
- `QLabel` for assistant content,
- `QTextDocument.setMarkdown()` plus post-processed HTML for assistant Markdown.

That pipeline causes recurring problems:

1. Qt rich text does not behave like browser HTML/CSS.
2. Code blocks and quotes are difficult to make visually stable.
3. Incremental Markdown display requires fragile string-level HTML surgery.
4. Styling corrections for one structure can easily break another.

The issue is architectural rather than cosmetic. More HTML patching will continue to be high-risk.

## Target Architecture

### Transcript Container

Keep `ChatPane` as the transcript owner, but make the message stream contain typed row widgets instead of label-heavy rows.

Top-level row types:

- `UserMessageRow`
- `AssistantMessageRow`
- `ThinkingRow`
- `ToolGroupRow`

The transcript ordering model remains exactly as it is today:

- user rows appear where user messages occur,
- assistant prose rows appear where assistant content occurs,
- tool rows appear where grouped tool calls occur,
- thinking rows can temporarily appear before an assistant row begins streaming.

### Assistant Message Row

`AssistantMessageRow` becomes a native widget with:

- assistant avatar,
- content container,
- optional internal vertical spacing between rendered blocks.

The content container renders a block list rather than one rich-text label.

### Document Block List

Assistant content is displayed through a `DocumentBlockList` widget that owns an ordered set of child block widgets.

Initial block types:

- `HeadingBlock`
- `ParagraphBlock`
- `QuoteBlock`
- `ListBlock`
- `CodeBlock`
- `PlainTextBlock`

Each block is a native Qt widget with its own layout, spacing, fonts, and surface treatment.

This keeps code blocks, quotes, and headings visually stable without depending on Qt rich text quirks.

## Markdown Model

### Parsing Strategy

Do not introduce a full Markdown engine.

Instead, add a lightweight transcript-oriented parser that converts assistant raw text into an internal block list. The parser only needs to support structures that materially affect the transcript UI:

- ATX headings (`#` through `######`)
- fenced code blocks
- quoted lines (`>`)
- unordered lists (`-`, `*`, `+`)
- ordered lists (`1.` style)
- paragraph grouping
- plain text fallback

Inline formatting should be intentionally limited in the first implementation.

### Inline Formatting

The first pass should support:

- inline code spans,
- plain text escaping,
- optional emphasis only if it stays simple and stable.

If inline emphasis increases complexity or destabilizes layout, it should be deferred. Code spans are the highest-priority inline feature because they are visually important in chat transcripts.

### Raw Source Preservation

Every assistant message row should preserve:

- the original raw assistant text,
- the parsed block list derived from that text.

Raw source preservation is required for:

- streaming updates,
- re-parsing,
- copy/export behaviors,
- future richer rendering improvements.

## Streaming Behavior

Streaming must remain first-class.

### Streaming Model

During assistant streaming:

1. accumulate raw assistant text,
2. parse it into document blocks,
3. patch the current `AssistantMessageRow`,
4. keep the row in place in the transcript.

This should not rebuild the entire transcript. Only the current active assistant row should be refreshed.

### Block Refresh Strategy

To keep implementation manageable:

- allow full re-parse of the current assistant message on each delta,
- but only replace the widgets inside the active assistant row's block container.

That is acceptable because:

- only one assistant row is active during streaming,
- transcript messages are typically small enough for local re-parse,
- this avoids fragile incremental Markdown mutation logic.

If needed later, this can be optimized into tail-block patching, but that is not required for the redesign.

### Incomplete Markdown

Streaming content may end mid-structure.

The parser must degrade gracefully:

- incomplete code fence: render as temporary plain/code-like text until closed,
- incomplete list: render accumulated items so far,
- incomplete quote: render current quoted lines as a quote block,
- incomplete heading marker: render as heading if intention is clear, otherwise as plain text.

The goal is stable, predictable partial rendering rather than strict Markdown correctness.

## Row Types

### UserMessageRow

Keep the existing user-side alignment and compact bubble presentation, but move it into a dedicated row widget rather than building it ad hoc in `ChatPane.add_message()`.

Responsibilities:

- right-aligned layout,
- user avatar,
- compact plain-text bubble,
- selectable text.

User messages do not need Markdown rendering.

### AssistantMessageRow

Responsibilities:

- left-aligned layout,
- assistant avatar,
- native block container for parsed document blocks,
- support for streaming updates,
- text selection inside blocks.

Assistant rows should no longer expose one monolithic `AssistantBubble` rich-text surface.

### ThinkingRow

Keep the current lightweight "Thinking" presentation as a dedicated row widget. It should remain easy to insert before assistant streaming starts and easy to remove once real content arrives.

### ToolGroupRow

Preserve the current grouped tool-call model and expansion behavior.

This redesign does not change the grouping algorithm. It changes only the rendering architecture of prose messages around it so the whole transcript follows the same widget-driven philosophy.

`ToolGroupRow` can continue to evolve from the existing `_ToolGroupWidget`, but it should conceptually become one of the transcript row widgets rather than a special-case add-on.

## Copy and Selection Requirements

Selectable text is a hard requirement.

The redesign must preserve copyability for:

- user text,
- assistant paragraphs,
- headings,
- list items,
- quote text,
- code block content,
- tool summaries and details where applicable.

Preferred implementation:

- use selectable text widgets for text-bearing blocks,
- use dedicated read-only text presentation for code blocks if needed,
- avoid custom painting that would make selection difficult.

If a custom block surface uses nested labels, each text area must still expose selection behavior.

## Visual Design Direction

The selected rendering style is the notebook-style direction chosen during design review.

That means:

- assistant prose should feel like a technical notebook rather than a browser article,
- headings should be crisp and compact,
- paragraphs should be calm and readable,
- quotes should read as soft callout panels,
- code blocks should be stable, continuous dark surfaces with true monospaced text.

This style should be implemented through native widget composition and palette-driven styling, not HTML emulation.

## Component Responsibilities

### ChatPane

`ChatPane` remains responsible for:

- session transcript container ownership,
- message order,
- active streaming state,
- tool-group lifecycle,
- thinking-row lifecycle,
- scrolling.

It should stop owning rich-text formatting logic directly.

### Transcript Row Widgets

Each row widget owns only its local presentation and update behavior.

This separation should reduce the amount of rendering-specific conditionals currently concentrated in `ChatPane`.

### Markdown Parser / Block Builder

Introduce a dedicated parsing/building layer that:

- accepts raw assistant text,
- returns a list of block descriptors,
- can rebuild a row from those descriptors.

This layer should be pure and testable without GUI rendering.

## Data Structures

Add internal block descriptor types, for example:

- `HeadingBlockData(level, text)`
- `ParagraphBlockData(inlines)`
- `QuoteBlockData(children or text)`
- `ListBlockData(ordered, items)`
- `CodeBlockData(language, code)`
- `PlainTextBlockData(text)`

Inline data may be minimal at first:

- `TextSpan`
- `CodeSpan`

These structures should be UI-agnostic so tests can validate parsing separately from widget rendering.

## History Replay

History replay behavior must remain unchanged from the user's perspective.

When `render_history()` is called:

- assistant messages are parsed into block rows,
- tool groups still reconstruct correctly,
- tool results still attach to the correct grouped tool row,
- order remains identical to live runtime ordering rules.

The transcript flow observed in existing tests must remain valid.

## Tool Group Preservation

Tool rows must keep all current behaviors:

- consecutive tool calls collapse into one group,
- tool results match by `tool_call_id` where available,
- unknown tool results do not leak internal ids,
- expansion/collapse remains available,
- tool rows stay in the same relative order against assistant prose rows.

This redesign should not destabilize tool rendering while solving transcript prose rendering.

## Testing Strategy

### Parser Tests

Add focused tests for the new Markdown block parser:

- heading parsing,
- paragraph grouping,
- quote parsing,
- list parsing,
- code fence parsing,
- incomplete streaming fragments.

These tests should validate block descriptors without needing Qt widgets.

### Row Rendering Tests

Update chat widget tests to assert:

- assistant rows are rendered through block widgets rather than rich-text labels,
- code blocks are their own widgets,
- history and runtime transcript order still match,
- thinking row replacement still works,
- tool grouping still behaves the same,
- selection-capable widgets exist for transcript text.

### Manual Verification

Manual QA should check:

- streaming assistant content grows smoothly,
- code blocks look continuous,
- monospaced font is visibly correct,
- copied code text matches source text,
- day and night themes remain coherent,
- narrow transcript widths wrap correctly.

## Migration Plan

### Phase 1

Introduce the block model and assistant row widget while keeping existing top-level transcript flow.

### Phase 2

Replace assistant `QLabel` rendering with block-container rendering.

### Phase 3

Refactor user, thinking, and tool rows into more explicit row widget types for consistency.

### Phase 4

Remove obsolete rich-text Markdown helpers once all assistant transcript paths use componentized blocks.

## Risks

1. Streaming refresh may become visually noisy if the whole assistant row is rebuilt too aggressively.
   Mitigation: limit rebuild scope to the active row only and keep row-local layout stable.

2. Copy behavior may regress if decorative wrappers intercept selection.
   Mitigation: use selectable child text widgets and add explicit tests.

3. Parser complexity could grow toward a full Markdown engine.
   Mitigation: keep the supported transcript grammar intentionally narrow and practical.

4. Existing tests currently target label-based implementation details.
   Mitigation: update tests toward behavior and widget role rather than exact old object names where appropriate.

## Success Criteria

The redesign is successful when:

- the transcript no longer depends on one rich-text assistant label for Markdown rendering,
- code blocks render as stable continuous native surfaces,
- assistant transcript styling is visually consistent between design intent and Qt output,
- all current transcript capabilities remain intact,
- chat widget tests pass with the new architecture.
