# Desktop Silver Tech Visual Style

Date: 2026-06-20

## Goal

将 `apps/desktop/renderer` 的现有 React 前端升级为白色、银色为主的金属科技风 Lora Workbench。改版重点是视觉语言、组件状态和信息层级，不改变会话、设置、运行、trace、流式聊天等业务行为。

目标体验应当是冷静、干净、精密、带一点金属仪器感的本地 AI 工程工作台。界面不应是深色控制台，也不应大面积使用蓝色；蓝色或青色只能作为极小面积的状态辅助色。

Demo reference:

- `apps/desktop/prototypes/cool-tech-style-demo.html`
- Current intended preview URL: `http://127.0.0.1:4177/cool-tech-style-demo.html?rev=silver-flat-rounded`

## Current Baseline

当前前端主要文件：

- `apps/desktop/renderer/src/app/App.jsx`
- `apps/desktop/renderer/src/app/app.css`
- `apps/desktop/renderer/src/app/layoutState.js`
- `apps/desktop/renderer/src/shared/api/client.js`

当前视觉基底已经具备：

- 三栏结构：history sidebar、chat workbench、trace inspector。
- CSS token：`--bg`、`--surface`、`--line`、`--text`、`--accent`、`--accent-2`、语义状态色。
- 折叠侧栏、折叠 trace、settings modal、toast、trace tabs、chat composer。

主要问题：

- 现有风格偏普通浅色 SaaS，缺少金属科技质感。
- 早期 demo 曾偏深蓝，不符合目标方向。
- 按钮不应使用重色或明显渐变，尤其 `New Chat` 需要浅色、轻量。
- 圆角需要更柔和，避免尖锐、小气。
- 组件状态需要更像仪器指示灯，而不是大面积染色。
- 图标应替代裸字符 `+`、`<`、`>`、`[]`、`*`、`x`。

## Design Direction

关键词：

- 白色
- 银色
- 冷灰
- 金属科技
- 无明显渐变
- 柔和圆角
- 轻量按钮
- 精密仪器感

视觉方向：

- 主背景为冷白或浅银灰。
- 主面板为白色、近白、浅银灰。
- 金属感来自细边框、轻阴影、内高光、银灰层次，而不是大渐变。
- 按钮、背景、大面板不使用明显渐变色。
- 选中态和运行态用灰钢色、细边框、小状态点表达。
- success、warning、error 保留语义颜色，但只用于小面积 chip、dot、border 或文字。

Non-goals:

- 不做深色主题作为首要方向。
- 不做满屏蓝色、青色、紫色或霓虹风。
- 不做强玻璃拟态、大面积发光、装饰性光斑背景。
- 不做过度圆润的胶囊式消费级 UI。

## Design Principles

1. 白银优先。
   页面第一眼应读作白色、银色、冷灰金属，而不是蓝色科技。

2. 金属感要克制。
   使用纯色面、细边框、轻阴影和高质量 spacing；避免靠渐变堆质感。

3. 状态像仪表灯。
   Running、Done、Warning、Error 应清楚，但只占小面积，不能污染整体银白基调。

4. 圆角更柔和。
   控件不要尖锐。主面板、按钮、消息、卡片使用 12-16 px 的圆角区间。

5. 保持工程密度。
   保留当前三栏工作台的信息密度，不做 landing page、hero 或营销式大模块。

## Color System

第一阶段建议将 `:root` 改为银白科技 token：

```css
:root {
  --bg: #f3f6f8;
  --bg-quiet: #e4e9ed;
  --surface: #fbfcfd;
  --surface-2: #f0f4f7;
  --surface-3: #e7edf2;
  --surface-glass: rgba(250, 252, 253, 0.9);
  --metal-1: #ffffff;
  --metal-2: #dfe5ea;
  --metal-3: #b8c2ca;
  --line: #c8d0d7;
  --line-strong: #aab5be;
  --line-glow: rgba(139, 156, 170, 0.32);
  --text: #1c2630;
  --muted: #62717e;
  --soft: #8a98a3;
  --accent: #6f7e8b;
  --accent-2: #c7d0d8;
  --accent-deep: #465460;
  --success: #168263;
  --warning: #a96e12;
  --error: #c7435a;
  --shadow: 0 24px 70px rgba(42, 55, 66, 0.16);
}
```

Usage notes:

- `--bg` owns the app background and should be mostly flat.
- `--surface` is for the main workbench.
- `--surface-2` is for trace/sidebar alternate panels.
- `--surface-3` is for selected rows, active tabs, and subtle elevated areas.
- `--accent` is steel gray, not blue.
- `--accent-2` is pale silver for quiet support states.
- `--success`、`--warning`、`--error` stay semantic and should be used sparingly.

## Background And Surface Rules

App background:

- Use a flat color: `background: var(--bg);`
- Do not use visible linear/radial gradients for the main background.
- Do not use decorative orbs, bokeh, glow blobs, purple/blue gradients, or dark console ambience.

Pane treatment:

- `.workbench` should use a white or near-white background.
- Use `border: 1px solid var(--line-strong)`.
- Use light elevation, e.g. `box-shadow: var(--shadow), inset 0 1px 0 rgba(255,255,255,0.95)`.
- Keep surfaces crisp and readable; translucent overlays should be rare.

## Shape And Radius

Use a softer radius scale:

- 12 px for icon buttons, tabs, chips, session rows, message bubbles, activity cards, inputs.
- 14 px for composer box and collapsed brand square.
- 16 px for major panes and settings modal.
- 999 px only for true dots or rails that are intentionally circular/pill-like.

Avoid:

- 6-8 px radii for visible primary controls; they feel too sharp for this direction.
- Oversized pill buttons unless the component is a segmented control.

## Typography

Continue using the Windows/CJK-friendly stack:

```css
font-family: "Microsoft YaHei UI", "Segoe UI", system-ui, sans-serif;
```

Type roles:

- Brand: 22-24 px, weight 760-800。
- Pane title: 15-17 px, weight 720-760。
- Body/chat: 13-14 px, line-height 1.6-1.7。
- Metadata: 11-12 px, muted。
- Tool/config/detail: `"Cascadia Mono", Consolas, monospace`。

Do not use negative letter spacing. Keep labels compact and scan-friendly.

## Layout

Keep the current grid:

- `.app-shell`: history + workbench。
- `.workbench`: chat + trace。
- `--history-width`: default 304 px, collapsed 68 px。
- `--trace-width`: default 388 px, collapsed 56 px。

Spacing:

- Outer padding: 16 px.
- Gutter: 14 px.
- Pane internal padding should remain compact.
- Do not introduce nested large cards inside the workbench.

## Component Specs

### History Sidebar

Goal: compact project/session navigation with a polished silver instrument feel.

Visual rules:

- `.history` can stay transparent over the flat silver background.
- Brand mark should be steel gray or silver, not cyan/blue.
- Collapsed brand square should use a light silver background, not a dark or colorful fill.
- Session rows use white or near-white background on hover/active.
- Active session uses a steel-gray left rail and subtle border/shadow.
- `session-status` is a small chip that does not squeeze the title.

`New Chat` button:

- Must be light, not dark.
- Use white or very light silver background: e.g. `#f8fafb`.
- Use a thin gray border: e.g. `#bcc7d0`.
- Text/icon should be dark gray.
- Hover can become `#eef3f6`.
- No visible gradient.
- Shadow should be light and shallow.

### Chat Header

Goal: clean run context bar.

Visual rules:

- Header background should be flat near-white.
- It should separate from transcript through border and spacing, not through color drama.
- Status pill should be steel-gray for running, with text label.
- Do not use large blue/cyan fills.

### Transcript

Goal: light execution canvas.

Visual rules:

- Transcript background should be flat light silver: e.g. `#f5f7f9`.
- Assistant bubble: white background, gray border, subtle left steel accent.
- User bubble: light silver background, gray border.
- Avatar can be a small white/silver square with steel-gray text.
- Avoid blue-tinted message fills.

### Runtime Activity Cards

Goal: tool/runtime activity as precision instrument cards.

Visual rules:

- Card background: white.
- Border: silver-gray.
- Header: flat light silver.
- Detail text: muted gray monospace.
- Running state: steel-gray chip/dot.
- Warning/error states: small semantic chip/dot and border, not full-card color fill.

### Composer

Goal: command input without heaviness.

Visual rules:

- Composer area uses light silver surface.
- Composer box is white, 14 px radius, gray border.
- Textarea text is dark gray.
- Send button follows the light button rule unless a stronger primary button is explicitly requested.
- Button width must remain stable across labels.

### Trace Inspector

Goal: telemetry panel with silver property-sheet feel.

Visual rules:

- Trace panel background should be light silver, slightly different from chat.
- Trace header is flat near-white.
- Tabs are compact segmented controls with white active tab and steel-gray underline or border.
- Timeline uses gray vertical line and small status dots.
- Config rows are white or near-white property rows.

### Settings Modal

Goal: elevated light system panel.

Visual rules:

- Backdrop can be translucent light gray with blur.
- Modal background: near-white.
- Radius: 16 px.
- Inputs: white, gray border, 12 px radius.
- Save button follows light silver primary style unless there is a strong reason to emphasize it.

### Toast

Goal: low-disruption notification.

Visual rules:

- White background.
- Gray border.
- Steel-gray text for normal info.
- Semantic color only for warning/error.
- Right-bottom placement remains.

## Icon Strategy

Use `lucide-react` in the real React implementation.

Icons should cover:

- new chat
- settings
- send
- collapse/expand history
- collapse/expand trace
- folder/project
- message/session
- check/done
- alert/error
- loader/running

Rules:

- Replace ASCII glyphs such as `+`、`<`、`>`、`[]`、`*`、`x`.
- Icon strokes should be 16-18 px, steel-gray by default.
- Icon-only buttons need `title` or `aria-label`.

## CSS Token Refactor

First implementation can stay in `app.css`; a separate token file is optional.

Recommended semantic token additions:

```css
:root {
  --state-ready: var(--soft);
  --state-running: var(--accent);
  --state-success: var(--success);
  --state-warning: var(--warning);
  --state-error: var(--error);

  --pane-bg: var(--surface);
  --pane-alt-bg: var(--surface-2);
  --row-active-bg: #f8fafb;
  --button-bg: #f8fafb;
  --button-hover-bg: #eef3f6;
  --input-bg: #ffffff;
}
```

## Implementation Plan

### Slice 1: Token And Base Theme

- Replace current palette with silver-tech tokens.
- Set body background to flat `var(--bg)`.
- Remove obvious gradients from buttons, large backgrounds, and primary panels.
- Verify contrast in all panes.

### Slice 2: Shape And Buttons

- Apply 12/14/16 px radius scale.
- Restyle `New Chat`, `Send`, settings save, plain actions, icon buttons.
- Ensure `New Chat` reads light silver, not dark.

### Slice 3: Shell And Surfaces

- Restyle `.workbench`, `.chat-header`, `.transcript`, `.trace`, `.composer`, `.settings-panel`.
- Keep layout behavior unchanged.
- Verify collapsed history and trace states.

### Slice 4: Navigation And Status

- Add or update `.session-status` styles.
- Restyle session group and session row states.
- Normalize `.status-pill`, `.status-dot`, `.toast` to small, steel/semantic states.

### Slice 5: Chat And Runtime Cards

- Restyle message bubbles, avatar, activity cards.
- Keep long tool output scrollable.
- Ensure no blue-tinted fills remain in normal chat surfaces.

### Slice 6: Trace Inspector

- Restyle tabs as light segmented controls.
- Restyle event rows as light telemetry timeline.
- Restyle config rows as property-sheet rows.

### Slice 7: Icon Pass

- Add `lucide-react`.
- Replace ASCII glyphs.
- Keep accessible labels/tooltips.

### Slice 8: Responsive QA

- Check desktop widths around 1280, 1440, 1728.
- Check narrow breakpoint below 1040 px.
- Verify text clipping, button width, collapsed rail, settings modal.

## Testing And Verification

Automated checks:

- `npm test` in `apps/desktop` should continue to pass.
- Add tests only where existing behavior warrants it:
  - history collapsed state
  - trace collapsed state
  - session status label/tone mapping
  - running send button disabled state

Manual verification:

- Start desktop renderer.
- Select existing session.
- Create new chat.
- Send a message.
- Observe running state.
- Inspect Events, Tools, Files, Config tabs.
- Open settings.
- Trigger or simulate toast/error state.
- Resize above and below 1040 px.

Visual QA checklist:

- Page reads white/silver/metal, not blue/dark.
- Main background has no visible gradient.
- Buttons have no visible gradient.
- `New Chat` is light and not visually heavy.
- Rounded corners are soft but not pill-like.
- Status chips are small and stable.
- Sidebar active row is visible but not loud.
- Trace panel reads as telemetry/property sheet.
- Composer does not jump when running.
- Settings modal and inputs have enough contrast.

## Accessibility

- Maintain focus-visible outlines for buttons and interactive rows.
- Do not rely on color alone for running/error; include text labels or icons.
- Keep font sizes at or above current baseline.
- Ensure light gray text meets contrast requirements.
- Do not introduce negative letter spacing.

## Out Of Scope

This spec does not include:

- changing backend APIs
- changing session persistence
- adding new trace data fields
- adding multi-window behavior
- redesigning legacy PySide UI
- adding theme switching unless requested separately
- changing the core chat workflow

## Acceptance Criteria

The redesign is complete when:

- The desktop renderer clearly reads as a white/silver metal-tech workbench.
- Large backgrounds, buttons, panes, and message surfaces do not use visible gradients.
- `New Chat` and other primary controls are light, not dark.
- Core layout and interactions behave the same as before.
- Running, ready, done, warning, and error states are distinct but small and disciplined.
- Session sidebar, chat pane, trace inspector, composer, settings modal, and toast share the same silver-tech visual language.
- No visible control relies on raw ASCII symbols as final iconography.
- Manual QA shows no clipping, overlap, unreadable text, or incoherent layout at normal desktop sizes.
