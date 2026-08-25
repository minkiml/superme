// The one syntax highlighter. Grammars are registered here and nowhere else.
//
// Token classes are themed in `index.css` against our own colour tokens, so highlighting
// follows the light/dark switch instead of carrying a second fixed palette.

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
import diff from 'highlight.js/lib/languages/diff'
import ini from 'highlight.js/lib/languages/ini'

// Registered explicitly: the all-languages build is a megabyte of grammars nothing here contains.
const LANGS: Record<string, unknown> = {
  javascript, typescript, python, bash, json, yaml, markdown, css, xml, sql, go, rust, diff, ini,
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
  toml: 'ini', ini: 'ini', cfg: 'ini', diff: 'diff', patch: 'diff',
}

/** The highlight.js language for a path, or null when we have no grammar for it. */
export function langFor(path: string): string | null {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  const lang = EXT_LANG[ext]
  return lang && hljs.getLanguage(lang) ? lang : null
}

/**
 * The language a fence names, or null. An unlabelled fence stays null on purpose: auto-detection
 * paints ASCII trees and shell transcripts as code that means something.
 */
export function langForFence(name: string | undefined): string | null {
  const n = (name || '').trim().toLowerCase()
  if (!n) return null
  if (hljs.getLanguage(n)) return n          // hljs knows its own aliases: py, js, sh, yml
  const mapped = EXT_LANG[n]
  return mapped && hljs.getLanguage(mapped) ? mapped : null
}

/** Highlighted HTML for `text` in `lang`, or the text escaped when there is no grammar. */
export function highlight(text: string, lang: string | null): string {
  if (!text) return ''
  if (!lang) return escapeHtml(text)
  try {
    return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value
  } catch {
    return escapeHtml(text)
  }
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>]/g, (c) => (c === '&' ? '&amp;' : c === '<' ? '&lt;' : '&gt;'))
}
