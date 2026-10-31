// Renders the Markdown subset the assistant writes (headings, bold section titles,
// bullet and numbered lists, bold / italic / code, simple tables) as React elements.
//
// Safety: it never produces raw HTML. Text becomes React text nodes, which React
// escapes, so a reply containing "<script>" shows those characters literally.
// Faithfulness: only Markdown syntax is removed; every word and figure the reply
// contains is rendered (tests check this), so the number check's verdict on the raw
// text still describes exactly what the adviser reads.

import type { ReactNode } from "react";

type Block =
  | { kind: "heading"; text: string }
  | { kind: "para"; lines: string[] }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "table"; header: string[]; rows: string[][] };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d{1,3}[.)]\s+(.*)$/;
const HASH_HEADING = /^\s*#{1,4}\s+(.*?)\s*#*\s*$/;
// A line that is entirely bold ("**Where the risk sits**", optionally with a colon)
// is a section title.
const BOLD_HEADING = /^\s*\*\*([^*]+?)\*\*\s*:?\s*$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

const cells = (line: string) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

export function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m: RegExpMatchArray | null;
    if ((m = line.match(HASH_HEADING)) || (m = line.match(BOLD_HEADING))) {
      blocks.push({ kind: "heading", text: m[1] });
      i++;
    } else if (TABLE_ROW.test(line) && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
      const header = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && TABLE_ROW.test(lines[i])) rows.push(cells(lines[i++]));
      blocks.push({ kind: "table", header, rows });
    } else if (BULLET.test(line) || NUMBERED.test(line)) {
      const kind: "ul" | "ol" = BULLET.test(line) ? "ul" : "ol";
      const pat = kind === "ul" ? BULLET : NUMBERED;
      const items: string[] = [];
      while (i < lines.length && lines[i].trim()) {
        const item = lines[i].match(pat);
        if (item) items.push(item[1]);
        else if (BULLET.test(lines[i]) || NUMBERED.test(lines[i])) break; // the other list kind
        else if (items.length) items[items.length - 1] += ` ${lines[i].trim()}`; // wrapped line
        i++;
      }
      blocks.push(kind === "ul" ? { kind: "ul", items } : { kind: "ol", items });
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() && !BULLET.test(lines[i]) && !NUMBERED.test(lines[i])
             && !HASH_HEADING.test(lines[i]) && !BOLD_HEADING.test(lines[i])
             && !(TABLE_ROW.test(lines[i]) && TABLE_RULE.test(lines[i + 1] ?? ""))) {
        para.push(lines[i].trim());
        i++;
      }
      blocks.push({ kind: "para", lines: para });
    }
  }
  return blocks;
}

/** Inline **bold**, *italic* / _italic_ and `code`; anything else stays plain text. */
export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|`([^`]+)`|(?<![\w*])\*(?!\s)([^*]+?)\*(?![\w*])|(?<!\w)_(?!\s)([^_]+?)_(?!\w)/g;
  let last = 0, k = 0;
  for (const m of text.matchAll(re)) {
    if (m.index! > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) out.push(<strong key={k++} className="font-semibold">{inline(m[1])}</strong>);
    else if (m[2] !== undefined) out.push(<code key={k++} className="font-mono text-[0.9em]">{m[2]}</code>);
    else out.push(<em key={k++}>{m[3] ?? m[4]}</em>);
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`md ${className}`}>
      {parseBlocks(text).map((b, i) => {
        if (b.kind === "heading") return <h4 key={i} className="md-h">{inline(b.text)}</h4>;
        if (b.kind === "ul" || b.kind === "ol") {
          const items = b.items.map((it, j) => <li key={j}>{inline(it)}</li>);
          return b.kind === "ul" ? <ul key={i} className="md-ul">{items}</ul> : <ol key={i} className="md-ol">{items}</ol>;
        }
        if (b.kind === "table") {
          return (
            <div key={i} className="overflow-x-auto">
              <table className="data md-table">
                <thead><tr>{b.header.map((h, j) => <th key={j}>{inline(h)}</th>)}</tr></thead>
                <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, n) => <td key={n}>{inline(c)}</td>)}</tr>)}</tbody>
              </table>
            </div>
          );
        }
        return <p key={i} className="md-p">{b.lines.flatMap((l, j) => (j ? [<br key={`b${j}`} />, ...inline(l)] : inline(l)))}</p>;
      })}
    </div>
  );
}
