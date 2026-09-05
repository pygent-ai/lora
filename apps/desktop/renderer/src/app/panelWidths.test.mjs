import test from "node:test";
import assert from "node:assert/strict";
import { normalizePanelWidths, fitPanelWidths } from "./panelWidths.js";

test("saved panel widths are bounded and invalid values use defaults", () => {
  assert.deepEqual(normalizePanelWidths({ history: -50, trace: 9999 }), { history: 200, trace: 720 });
  assert.deepEqual(normalizePanelWidths({ history: "wide", trace: null }), { history: 272, trace: 420 });
});

test("panels shrink on smaller windows while reserving conversation space", () => {
  const result = fitPanelWidths({ history: 480, trace: 720 }, {}, 1100);
  assert.ok(result.history + result.trace <= 780);
  assert.ok(result.history >= 200 && result.trace >= 260);
});

test("collapsed widths stay fixed and saved widths restore with room", () => {
  assert.deepEqual(fitPanelWidths({ history: 350, trace: 500 }, { historyCollapsed: true, traceCollapsed: true }, 1100), { history: 68, trace: 56 });
  assert.deepEqual(fitPanelWidths({ history: 350, trace: 500 }, {}, 1600), { history: 350, trace: 500 });
});
