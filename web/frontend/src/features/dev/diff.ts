// Unified patch to structured hunks, plus per-line syntax highlighting.
//
// PARSE here, RENDER in the component: parsing is a pure function over text, which is the only
// reason it is testable without a browser.

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

// Registered explicitly: the all-languages build is a megabyte of grammars no diff here will
// contain.
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

/**
 * Per LINE, not per file: a diff is two interleaved programs, and a highlighter fed that as one
 * unit loses its place.
 *
 * The trade is missing multi-line constructs, which beats colouring the rest of a file wrong.
 */
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

/**
 * File headers are DROPPED: the page already names the file above the patch. Text before the first
 * hunk is ignored.
 */
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
      // A stripped patch can leave a bare empty line, which is still a context line and must
      // advance BOTH counters.
      cur.rows.push({ kind: 'ctx', text: line.startsWith(' ') ? line.slice(1) : line,
                      oldNo: oldNo++, newNo: newNo++ })
    }
  }
  return hunks
}

export type Chunk =
  | { type: 'rows'; rows: Row[] }
  | { type: 'fold'; rows: Row[] }   // a collapsed run of untouched context

/**
 * Keeps context either side of every change. A run folds only when hiding it saves more lines than
 * the expander costs.
 */
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

/**
 * A deletion run followed by additions is a REPLACEMENT, paired row by row.
 *
 * Unequal runs pad with nulls rather than stretching: putting unrelated lines opposite each other
 * is worse than a visible gap.
 */
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
