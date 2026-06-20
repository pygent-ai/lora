const FENCE_RE = /^ {0,3}```\s*([A-Za-z0-9_+.#-]*)\s*$/;
const HEADING_RE = /^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/;
const ORDERED_LIST_RE = /^ {0,3}\d+[.)]\s+(.+)$/;
const UNORDERED_LIST_RE = /^ {0,3}[-*+]\s+(.+)$/;
const QUOTE_RE = /^ {0,3}>\s?(.*)$/;
const RULE_RE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const TABLE_SEPARATOR_CELL_RE = /^:?-{3,}:?$/;

export function parseMarkdownBlocks(markdown) {
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fenceMatch = line.match(FENCE_RE);
    if (fenceMatch) {
      const language = fenceMatch[1] || "";
      const codeLines = [];
      index += 1;
      while (index < lines.length && !FENCE_RE.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push({ type: "code", language, text: codeLines.join("\n") });
      continue;
    }

    const headingMatch = line.match(HEADING_RE);
    if (headingMatch) {
      blocks.push({
        type: "heading",
        level: Math.min(headingMatch[1].length, 3),
        text: headingMatch[2],
      });
      index += 1;
      continue;
    }

    if (RULE_RE.test(line)) {
      blocks.push({ type: "rule" });
      index += 1;
      continue;
    }

    if (isTableHeader(lines, index)) {
      const header = splitTableRow(lines[index]);
      const alignments = splitTableRow(lines[index + 1]).map(tableAlignment);
      const rows = [];
      index += 2;
      while (index < lines.length && isPipeTableRow(lines[index])) {
        rows.push(padTableRow(splitTableRow(lines[index]), header.length));
        index += 1;
      }
      blocks.push({
        type: "table",
        header,
        alignments: padTableRow(alignments, header.length, ""),
        rows,
      });
      continue;
    }

    const quoteMatch = line.match(QUOTE_RE);
    if (quoteMatch) {
      const quoteLines = [quoteMatch[1]];
      index += 1;
      while (index < lines.length) {
        const nextQuoteMatch = lines[index].match(QUOTE_RE);
        if (!nextQuoteMatch) {
          break;
        }
        quoteLines.push(nextQuoteMatch[1]);
        index += 1;
      }
      blocks.push({ type: "quote", text: quoteLines.join("\n") });
      continue;
    }

    const orderedMatch = line.match(ORDERED_LIST_RE);
    const unorderedMatch = line.match(UNORDERED_LIST_RE);
    if (orderedMatch || unorderedMatch) {
      const ordered = Boolean(orderedMatch);
      const items = [];
      while (index < lines.length) {
        const match = ordered ? lines[index].match(ORDERED_LIST_RE) : lines[index].match(UNORDERED_LIST_RE);
        if (!match) {
          break;
        }
        items.push(match[1]);
        index += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !startsMarkdownBlock(lines[index])) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push({ type: "paragraph", text: paragraphLines.join("\n") });
  }

  return blocks;
}

export function parseInlineMarkdown(text) {
  return parseInline(String(text || ""), 0);
}

function startsMarkdownBlock(line) {
  return (
    FENCE_RE.test(line) ||
    HEADING_RE.test(line) ||
    RULE_RE.test(line) ||
    isPipeTableRow(line) ||
    QUOTE_RE.test(line) ||
    ORDERED_LIST_RE.test(line) ||
    UNORDERED_LIST_RE.test(line)
  );
}

function isTableHeader(lines, index) {
  if (index + 1 >= lines.length) {
    return false;
  }
  const header = lines[index];
  const separator = lines[index + 1];
  if (!isPipeTableRow(header) || !isPipeTableRow(separator)) {
    return false;
  }
  const headerCells = splitTableRow(header);
  const separatorCells = splitTableRow(separator);
  return (
    headerCells.length > 1 &&
    separatorCells.length >= headerCells.length &&
    separatorCells.every((cell) => TABLE_SEPARATOR_CELL_RE.test(cell.trim()))
  );
}

function isPipeTableRow(line) {
  const trimmed = String(line || "").trim();
  return trimmed.includes("|") && !FENCE_RE.test(trimmed);
}

function splitTableRow(line) {
  const trimmed = String(line || "").trim();
  const bounded = trimmed.startsWith("|") && trimmed.endsWith("|");
  const content = bounded ? trimmed.slice(1, -1) : trimmed;
  return content.split("|").map((cell) => cell.trim());
}

function tableAlignment(cell) {
  const trimmed = cell.trim();
  if (trimmed.startsWith(":") && trimmed.endsWith(":")) {
    return "center";
  }
  if (trimmed.endsWith(":")) {
    return "right";
  }
  return "";
}

function padTableRow(row, length, fallback = "") {
  return Array.from({ length }, (_, index) => row[index] ?? fallback);
}

function parseInline(text, depth) {
  if (!text || depth > 6) {
    return text ? [{ type: "text", text }] : [];
  }

  const segments = [];
  let index = 0;

  while (index < text.length) {
    const next = findNextToken(text, index);
    if (!next) {
      pushText(segments, text.slice(index));
      break;
    }

    if (next.index > index) {
      pushText(segments, text.slice(index, next.index));
    }

    const parsed = parseToken(text, next, depth);
    if (!parsed) {
      pushText(segments, text[next.index]);
      index = next.index + 1;
      continue;
    }

    segments.push(parsed.segment);
    index = parsed.nextIndex;
  }

  return segments;
}

function findNextToken(text, start) {
  const candidates = [
    { type: "code", index: text.indexOf("`", start) },
    { type: "bold", index: text.indexOf("**", start) },
    { type: "italicStar", index: text.indexOf("*", start) },
    { type: "italicUnderscore", index: text.indexOf("_", start) },
    { type: "link", index: text.indexOf("[", start) },
  ].filter((candidate) => candidate.index >= 0);

  candidates.sort((left, right) => left.index - right.index);
  return candidates[0] || null;
}

function parseToken(text, token, depth) {
  if (token.type === "code") {
    const end = text.indexOf("`", token.index + 1);
    if (end <= token.index + 1) {
      return null;
    }
    return {
      segment: { type: "code", text: text.slice(token.index + 1, end) },
      nextIndex: end + 1,
    };
  }

  if (token.type === "bold") {
    const end = text.indexOf("**", token.index + 2);
    if (end <= token.index + 2) {
      return null;
    }
    return {
      segment: { type: "strong", children: parseInline(text.slice(token.index + 2, end), depth + 1) },
      nextIndex: end + 2,
    };
  }

  if (token.type === "italicStar") {
    if (text[token.index + 1] === "*") {
      return null;
    }
    const end = text.indexOf("*", token.index + 1);
    if (end <= token.index + 1) {
      return null;
    }
    return {
      segment: { type: "em", children: parseInline(text.slice(token.index + 1, end), depth + 1) },
      nextIndex: end + 1,
    };
  }

  if (token.type === "italicUnderscore") {
    const end = text.indexOf("_", token.index + 1);
    if (end <= token.index + 1) {
      return null;
    }
    return {
      segment: { type: "em", children: parseInline(text.slice(token.index + 1, end), depth + 1) },
      nextIndex: end + 1,
    };
  }

  const closeLabel = text.indexOf("]", token.index + 1);
  if (closeLabel < 0 || text[closeLabel + 1] !== "(") {
    return null;
  }
  const closeHref = text.indexOf(")", closeLabel + 2);
  if (closeHref < 0) {
    return null;
  }
  const href = text.slice(closeLabel + 2, closeHref).trim();
  if (!isSafeHref(href)) {
    return null;
  }
  return {
    segment: {
      type: "link",
      href,
      children: parseInline(text.slice(token.index + 1, closeLabel), depth + 1),
    },
    nextIndex: closeHref + 1,
  };
}

function isSafeHref(href) {
  return /^(https?:|mailto:|#|\/)/i.test(href);
}

function pushText(segments, text) {
  if (!text) {
    return;
  }
  const previous = segments[segments.length - 1];
  if (previous?.type === "text") {
    previous.text += text;
    return;
  }
  segments.push({ type: "text", text });
}
