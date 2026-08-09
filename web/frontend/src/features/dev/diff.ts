// Unified-patch → structured hunks, plus per-line syntax highlighting.
//
// The PR page used to render `git`'s raw text with one colour per line kind. That is fine for
// someone already fluent in unified diffs and poor for everyone else (owner, 2026-08-09): a
// replacement reads as a block of red followed by an unrelated block of green, the code inside it
// has no syntax at all, and six lines of untouched context around a four-line change make the
// change the smaller half of the page.
//
// So: PARSE here, RENDER in the component. Parsing is a pure function over text — which is the
// only reason any of this is testable without a browser.

import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import markdown from 'highlight.js/lib/languages/markdown'
import css from 'highlight.js/lib/languages/css'
import xml from 'highlight.js/lib/languages/xml'
import sql from 'highlight.js/lib/languages/sql'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'

// Registered EXPLICITLY, not via the full bundle: `highlight.js`'s all-languages build is ~1MB of
// grammars for languages no diff here will ever contain. These cover what our repos are written
// in; an unlisted extension renders as plain text, which is exactly what it does today.
const LANGS: Record<string, unknown> = {
  javascript, typescript, python, bash, json, yaml, markdown, css, xml, sql, go, rust,
}
for (const [name, def] of Object.entries(LANGS)) {
  hljs.registerLanguage(name, def as never)
}

const EXT_LANG: Record<string, string> = {
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  py: 'python', pyi: 'python',
  sh: 'bash', bash: 'bash', zsh: 'bash',
  json: 'json', yml: 'yaml', yaml: 'yaml',
  md: 'markdown', markdown: 'markdown',
  css: 'css', scss: 'css',
  html: 'xml', xml: 'xml', svg: 'xml',
  sql: 'sql', go: 'go', rs: 'rust',
}

/** The highlight.js language for a path, or null when we have no grammar for it. */
export function langFor(path: string): string | null {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  const lang = EXT_LANG[ext]
  return lang && hljs.getLanguage(lang) ? lang : null
}

/** One line highlighted to HTML, or escaped plain text when the language is unknown.
 *
 *  Per LINE, not per file: a diff never holds a syntactically complete file — the `-` and `+`
 *  sides are two different programs interleaved, and feeding that to a highlighter as one unit
 *  makes it lose its place at the first hunk. Per-line highlighting misses multi-line constructs
 *  (a docstring's continuation lines read as plain), which is a fair trade for never colouring the
 *  rest of a file wrong because one hunk opened a bracket it does not close. */
export function highlight(line: string, lang: string | null): string {
  if (!line) return ''
  if (!lang) return escapeHtml(line)
  try {
    return hljs.highlight(line, { language: lang, ignoreIllegals: true }).value
  } catch {
    return escapeHtml(line)
  }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>]/g, (c) => (c === '&' ? '&amp;' : c === '<' ? '&lt;' : '&gt;'))
}

export type Row = {
  kind: 'add' | 'del' | 'ctx'
  text: string          // the line WITHOUT its +/-/space marker
  oldNo: number | null  // line number on the left (null for an addition)
  newNo: number | null  // line number on the right (null for a deletion)
}

export type Hunk = {
  header: string        // the raw `@@ … @@` line, kept verbatim — it names the function
  rows: Row[]
}

/** Parse a unified patch into hunks of numbered rows. File headers (`diff --git`, `index`, `---`,
 *  `+++`) are DROPPED: the page already names the file above the patch, so repeating it is a line
 *  of noise between the reader and the change. Text before the first `@@` is ignored. */
export function parsePatch(text: string): Hunk[] {
  const hunks: Hunk[] = []
  let cur: Hunk | null = null
  let oldNo = 0
  let newNo = 0
  for (const line of (text || '').split('\n')) {
    const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line)
    if (m) {
      cur = { header: line, rows: [] }
      hunks.push(cur)
      oldNo = Number(m[1])
      newNo = Number(m[2])
      continue
    }
    if (!cur) continue
    // `\ No newline at end of file` is metadata about the previous row, not a row.
    if (line.startsWith('\\')) continue
    if (line.startsWith('+')) {
      cur.rows.push({ kind: 'add', text: line.slice(1), oldNo: null, newNo: newNo++ })
    } else if (line.startsWith('-')) {
      cur.rows.push({ kind: 'del', text: line.slice(1), oldNo: oldNo++, newNo: null })
    } else {
      // A context line. Git writes it with a leading space; a trailing-whitespace-stripped patch
      // can leave a bare empty line, which is still a context line and must still advance BOTH
      // counters — dropping it desynchronises every line number below it.
      cur.rows.push({ kind: 'ctx', text: line.startsWith(' ') ? line.slice(1) : line,
                      oldNo: oldNo++, newNo: newNo++ })
    }
  }
  return hunks
}

export type Chunk =
  | { type: 'rows'; rows: Row[] }
  | { type: 'fold'; rows: Row[] }   // a collapsed run of untouched context

/** Split a hunk's rows into rendered runs and FOLDED context.
 *
 *  Keeps `pad` context rows either side of every change; any longer untouched run in between (or
 *  at the edges) collapses to one expander. A run only folds when hiding it actually saves lines —
 *  a "show 1 more line" control costs the reader more than the line it hides. */
export function foldContext(rows: Row[], pad = 3): Chunk[] {
  const keep = new Array<boolean>(rows.length).fill(false)
  rows.forEach((r, i) => {
    if (r.kind === 'ctx') return
    for (let j = Math.max(0, i - pad); j <= Math.min(rows.length - 1, i + pad); j++) keep[j] = true
  })
  const out: Chunk[] = []
  let run: Row[] = []
  let runKept = keep[0] ?? true
  const flush = () => {
    if (!run.length) return
    // 2 is the break-even: folding fewer lines than the expander occupies is a net loss.
    out.push(runKept || run.length <= 2 ? { type: 'rows', rows: run } : { type: 'fold', rows: run })
    run = []
  }
  rows.forEach((r, i) => {
    if (keep[i] !== runKept) { flush(); runKept = keep[i] }
    run.push(r)
  })
  flush()
  return out
}

export type Pair = { left: Row | null; right: Row | null }

/** Rows re-arranged into left/right pairs for the split view.
 *
 *  A run of deletions immediately followed by additions is a REPLACEMENT: pair them off row by row
 *  so "this line became that line" sits on one visual line. Unequal runs pad with nulls rather than
 *  stretching — an 8-for-3 replacement is not eight pairs, and pretending otherwise would put
 *  unrelated lines opposite each other, which is worse than an obvious gap. */
export function pairRows(rows: Row[]): Pair[] {
  const out: Pair[] = []
  let i = 0
  while (i < rows.length) {
    const r = rows[i]
    if (r.kind === 'ctx') { out.push({ left: r, right: r }); i++; continue }
    const dels: Row[] = []
    const adds: Row[] = []
    while (i < rows.length && rows[i].kind === 'del') dels.push(rows[i++])
    while (i < rows.length && rows[i].kind === 'add') adds.push(rows[i++])
    for (let k = 0; k < Math.max(dels.length, adds.length); k++) {
      out.push({ left: dels[k] ?? null, right: adds[k] ?? null })
    }
  }
  return out
}
