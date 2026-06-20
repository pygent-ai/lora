import assert from "node:assert/strict";
import test from "node:test";

import { parseInlineMarkdown, parseMarkdownBlocks } from "./markdown.js";

test("assistant markdown parser extracts common block types", () => {
  assert.deepEqual(parseMarkdownBlocks("# Title\n\n- **one**\n- two\n\n> quoted\n\nDone."), [
    { type: "heading", level: 1, text: "Title" },
    { type: "list", ordered: false, items: ["**one**", "two"] },
    { type: "quote", text: "quoted" },
    { type: "paragraph", text: "Done." },
  ]);
});

test("assistant markdown parser renders incomplete streaming fence as code", () => {
  assert.deepEqual(parseMarkdownBlocks("```js\nconsole.log('streaming')"), [
    { type: "code", language: "js", text: "console.log('streaming')" },
  ]);
});

test("assistant markdown inline parser extracts formatting and safe links", () => {
  assert.deepEqual(parseInlineMarkdown("Hello **bold** `code` [docs](https://example.com)"), [
    { type: "text", text: "Hello " },
    { type: "strong", children: [{ type: "text", text: "bold" }] },
    { type: "text", text: " " },
    { type: "code", text: "code" },
    { type: "text", text: " " },
    { type: "link", href: "https://example.com", children: [{ type: "text", text: "docs" }] },
  ]);
});

test("assistant markdown parser extracts pipe tables", () => {
  assert.deepEqual(
    parseMarkdownBlocks("| Name | Status | Notes |\n| --- | :---: | ---: |\n| **API** | Done | `ok` |\n| UI | WIP | 2 |"),
    [
      {
        type: "table",
        header: ["Name", "Status", "Notes"],
        alignments: ["", "center", "right"],
        rows: [
          ["**API**", "Done", "`ok`"],
          ["UI", "WIP", "2"],
        ],
      },
    ],
  );
});

test("assistant markdown parser renders streaming table after separator arrives", () => {
  assert.deepEqual(parseMarkdownBlocks("| A | B |\n| --- | --- |\n| 1 | 2 |"), [
    {
      type: "table",
      header: ["A", "B"],
      alignments: ["", ""],
      rows: [["1", "2"]],
    },
  ]);
});
