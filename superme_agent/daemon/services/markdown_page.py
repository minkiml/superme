"""A small markdown to HTML renderer for the standalone doc pages the daemon serves.

Deliberately NOT a general engine: anything unrecognised falls through as a paragraph.
"""

import html
import re

# Applied in order to already-escaped text. Code spans lift out first, so `**not bold**` in
# backticks stays literal.
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """Escape one line/paragraph and apply the inline rules."""
    spans: list[str] = []

    def stash(m: re.Match) -> str:
        spans.append(html.escape(m.group(1)))
        return f"\x00{len(spans) - 1}\x00"

    text = _CODE.sub(stash, text)          # lift code spans BEFORE escaping the rest
    text = html.escape(text)
    text = _LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{spans[int(m.group(1))]}</code>", text)


def _table(rows: list[str]) -> str:
    """A pipe table → <table>. `rows[1]` is the `|---|` separator, dropped."""
    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    head = "".join(f"<th>{_inline(c)}</th>" for c in cells(rows[0]))
    body = "".join(
        "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells(r)) + "</tr>" for r in rows[2:])
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render(md: str) -> str:
    """Render a markdown document to an HTML fragment."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)

    # YAML frontmatter, only when it opens the document — machine fields, shown but set back.
    if lines and lines[0].strip() == "---":
        end = next((j for j in range(1, n) if lines[j].strip() == "---"), None)
        if end is not None:
            body = html.escape("\n".join(lines[1:end]))
            out.append(f'<pre class="fm">{body}</pre>')
            i = end + 1

    para: list[str] = []

    def flush() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):                                    # fenced code
            flush()
            lang = stripped[3:].strip()
            j = i + 1
            while j < n and lines[j].strip() != "```":
                j += 1
            code = html.escape("\n".join(lines[i + 1:j]))
            label = f'<span class="lang">{html.escape(lang)}</span>' if lang else ""
            out.append(f"<div class='code'>{label}<pre>{code}</pre></div>")
            i = j + 1
            continue

        if not stripped:                                                   # blank → end paragraph
            flush()
            i += 1
            continue

        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):                # horizontal rule
            flush()
            out.append("<hr>")
            i += 1
            continue

        m = re.match(r"(#{1,6})\s+(.*)", stripped)                         # heading
        if m:
            flush()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        # table: a pipe row followed by a |---| separator
        if "|" in stripped and i + 1 < n and re.fullmatch(r"\|?[\s:|-]+\|?", lines[i + 1].strip()) \
                and "-" in lines[i + 1]:
            flush()
            j = i + 2
            while j < n and "|" in lines[j] and lines[j].strip():
                j += 1
            out.append(_table(lines[i:j]))
            i = j
            continue

        if stripped.startswith(">"):                                       # blockquote
            flush()
            j = i
            quoted = []
            while j < n and lines[j].strip().startswith(">"):
                quoted.append(lines[j].strip().lstrip(">").strip())
                j += 1
            out.append(f"<blockquote>{_inline(' '.join(quoted))}</blockquote>")
            i = j
            continue

        m = re.match(r"([-*+]|\d+\.)\s+(.*)", stripped)                    # list
        if m:
            flush()
            tag = "ul" if m.group(1) in "-*+" else "ol"
            items: list[str] = []
            while i < n:
                lm = re.match(r"\s*([-*+]|\d+\.)\s+(.*)", lines[i])
                if lm:
                    items.append(lm.group(2))
                elif lines[i].startswith((" ", "\t")) and lines[i].strip() and items:
                    items[-1] += " " + lines[i].strip()                    # wrapped continuation
                else:
                    break
                i += 1
            body = "".join(f"<li>{_inline(it)}</li>" for it in items)
            out.append(f"<{tag}>{body}</{tag}>")
            continue

        para.append(stripped)
        i += 1

    flush()
    return "\n".join(out)


# Every colour comes from the variables `_PAGE_CSS` declares, which carries both themes. No
# literal colour here.
DOC_CSS = """
.doc { font: 14.5px/1.65 ui-sans-serif, system-ui, -apple-system, sans-serif; color: var(--body); }
.doc h1, .doc h2, .doc h3, .doc h4 { color: var(--fg); font-weight: 650; line-height: 1.3;
  margin: 26px 0 10px; }
.doc h1 { font-size: 21px; margin-top: 0; }
.doc h2 { font-size: 16.5px; border-bottom: 1px solid var(--line); padding-bottom: 6px; }
.doc h3 { font-size: 14.5px; }
.doc h4 { font-size: 13.5px; color: var(--soft); }
.doc p { margin: 0 0 12px; }
.doc ul, .doc ol { margin: 0 0 12px; padding-left: 22px; }
.doc li { margin: 3px 0; }
.doc strong { color: var(--fg); font-weight: 640; }
.doc p > strong:first-child, .doc li > strong:first-child { color: var(--warn); }
.doc a { color: var(--accent); }
.doc hr { border: 0; border-top: 1px solid var(--line); margin: 22px 0; }
.doc blockquote { margin: 0 0 12px; padding: 2px 0 2px 14px; border-left: 3px solid var(--line);
  color: var(--muted); }
.doc code { background: var(--surface); border: 1px solid var(--line); border-radius: 5px;
  padding: .5px 5px; font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--accent); }
.doc .code { position: relative; margin: 0 0 14px; }
.doc .code .lang { position: absolute; top: 7px; right: 11px; font-size: 10.5px; color: var(--faint);
  letter-spacing: .04em; text-transform: uppercase; }
.doc .code pre { margin: 0; }
.doc pre { padding: 13px 15px; background: var(--surface); border: 1px solid var(--line);
  border-radius: 8px; overflow-x: auto; white-space: pre;
  font: 12.5px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--body); }
/* Inside a FENCE the accent tint is switched back off: it marks the one typable token in a line of
   prose, and a whole block already reads as code. The app makes the same exception. */
.doc pre code { background: none; border: 0; padding: 0; color: var(--body); }
.doc pre.fm { color: var(--muted); font-size: 11.5px; margin: 0 0 22px; }
.doc table { border-collapse: separate; border-spacing: 0; width: 100%; margin: 0 0 14px;
  border: 1px solid var(--line); border-radius: 8px; overflow: hidden; font-size: 13.5px; }
.doc th, .doc td { text-align: left; vertical-align: top; padding: 8px 11px;
  border-bottom: 1px solid var(--line); border-right: 1px solid var(--line); }
.doc thead th { background: var(--surface); color: var(--muted); font-size: 11.5px; font-weight: 650;
  text-transform: uppercase; letter-spacing: .04em; }
.doc tr > *:last-child { border-right: 0; }
.doc tbody tr:last-child > * { border-bottom: 0; }
"""
