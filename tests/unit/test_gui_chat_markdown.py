from __future__ import annotations

import unittest

from gui.widgets.chat_markdown import (
    CodeBlockData,
    HeadingBlockData,
    ListBlockData,
    ParagraphBlockData,
    QuoteBlockData,
    parse_markdown_blocks,
)


class GuiChatMarkdownTests(unittest.TestCase):
    def test_parse_markdown_blocks_extracts_notebook_structures(self) -> None:
        blocks = parse_markdown_blocks(
            "# Heading\n\n"
            "Paragraph with `code` and more text.\n\n"
            "> Quote line\n\n"
            "- one\n"
            "- two\n\n"
            "```python\n"
            "print('hi')\n"
            "```\n"
        )

        self.assertIsInstance(blocks[0], HeadingBlockData)
        self.assertEqual(blocks[0].level, 1)
        self.assertEqual(blocks[0].text, "Heading")

        self.assertIsInstance(blocks[1], ParagraphBlockData)
        self.assertEqual([span.kind for span in blocks[1].spans], ["text", "code", "text"])

        self.assertIsInstance(blocks[2], QuoteBlockData)
        self.assertEqual(blocks[2].text, "Quote line")

        self.assertIsInstance(blocks[3], ListBlockData)
        self.assertEqual(blocks[3].items, ["one", "two"])

        self.assertIsInstance(blocks[4], CodeBlockData)
        self.assertEqual(blocks[4].language, "python")
        self.assertEqual(blocks[4].code, "print('hi')")
        self.assertTrue(blocks[4].closed)

    def test_parse_markdown_blocks_accepts_realistic_fence_languages(self) -> None:
        blocks = parse_markdown_blocks(
            "```c++\n"
            "std::cout << \"hi\";\n"
            "```\n"
        )

        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], CodeBlockData)
        self.assertEqual(blocks[0].language, "c++")
        self.assertEqual(blocks[0].code, 'std::cout << "hi";')
        self.assertTrue(blocks[0].closed)

    def test_parse_markdown_blocks_keeps_bare_closing_fence_only(self) -> None:
        blocks = parse_markdown_blocks(
            "```python\n"
            "print('hi')\n"
            "```python\n"
            "print('still code')\n"
            "```\n"
        )

        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], CodeBlockData)
        self.assertEqual(blocks[0].language, "python")
        self.assertEqual(blocks[0].code, "print('hi')\n```python\nprint('still code')")
        self.assertTrue(blocks[0].closed)

    def test_parse_markdown_blocks_keeps_incomplete_fence_as_code_like_text(self) -> None:
        blocks = parse_markdown_blocks("```python\nprint('hi')\n")

        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], CodeBlockData)
        self.assertFalse(blocks[0].closed)
        self.assertEqual(blocks[0].language, "python")
        self.assertEqual(blocks[0].code, "print('hi')")

    def test_parse_markdown_blocks_groups_plain_lines_into_one_paragraph(self) -> None:
        blocks = parse_markdown_blocks("First plain line\nSecond plain line\n\nThird plain line\n")

        self.assertEqual(len(blocks), 2)
        self.assertIsInstance(blocks[0], ParagraphBlockData)
        self.assertEqual(blocks[0].plain_text, "First plain line\nSecond plain line")
        self.assertIsInstance(blocks[1], ParagraphBlockData)
        self.assertEqual(blocks[1].plain_text, "Third plain line")

    def test_parse_markdown_blocks_parses_ordered_lists(self) -> None:
        blocks = parse_markdown_blocks("1. one\n2. two\n")

        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], ListBlockData)
        self.assertTrue(blocks[0].ordered)
        self.assertEqual(blocks[0].items, ["one", "two"])


if __name__ == "__main__":
    unittest.main()
