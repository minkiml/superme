import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Render markdown with Tailwind arbitrary-variant styling — no global CSS, no typography plugin.
//
// Three variants: `chat` is tight, `doc` is a full-width page, `report` speaks the drilldown
// panel's voice. One accent rule: code is tinted, prose is not.

// ONE table grammar, every variant: a table is the same object wherever it lands.
//
// `border-separate`, NOT `border-collapse`, which ignores its own border-radius. So each cell owns
// its right and bottom rule.
const TABLE_GRID =
  '[&_table]:my-3 [&_table]:w-full [&_table]:border-separate [&_table]:border-spacing-0 ' +
  '[&_table]:overflow-hidden [&_table]:rounded-md [&_table]:border [&_table]:border-line ' +
  '[&_thead]:bg-sunken ' +
  '[&_th]:border-b [&_th]:border-r [&_th]:border-line [&_th]:text-left ' +
  '[&_th]:font-semibold [&_th]:uppercase [&_th]:tracking-wide [&_th]:text-muted ' +
  '[&_td]:border-b [&_td]:border-r [&_td]:border-line [&_td]:align-top [&_td]:break-words ' +
  '[&_tr>*:last-child]:border-r-0 [&_tbody_tr:last-child>*]:border-b-0 '

const CHAT =
  'text-sm leading-relaxed space-y-2 ' +
  TABLE_GRID +
  '[&_table]:text-[12px] [&_th]:px-2 [&_th]:py-1 [&_th]:text-[10px] [&_td]:px-2 [&_td]:py-1 ' +
  '[&_p]:m-0 ' +
  '[&_strong]:font-semibold [&_em]:italic ' +
  '[&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-1 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5 ' +
  '[&_code]:rounded [&_code]:bg-hover [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[0.85em] [&_code]:text-accent-text ' +
  '[&_pre]:my-1 [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-sunken [&_pre]:p-2 ' +
  '[&_pre_code]:bg-transparent [&_pre_code]:p-0 ' +
  '[&_a]:text-accent [&_a]:underline ' +
  '[&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-semibold ' +
  '[&_blockquote]:border-l-2 [&_blockquote]:border-line [&_blockquote]:pl-3 [&_blockquote]:text-muted'

const DOC =
  'text-[15px] leading-7 text-fg ' +
  // paragraphs + spacing
  '[&>p]:my-4 [&_p]:leading-7 ' +
  '[&_strong]:font-semibold [&_strong]:text-fg [&_em]:italic ' +
  // headings — real hierarchy
  '[&_h1]:text-[1.7rem] [&_h1]:font-bold [&_h1]:tracking-tight [&_h1]:mt-0 [&_h1]:mb-5 ' +
  '[&_h2]:text-xl [&_h2]:font-semibold [&_h2]:tracking-tight [&_h2]:mt-9 [&_h2]:mb-3 [&_h2]:pb-1.5 [&_h2]:border-b [&_h2]:border-line ' +
  '[&_h3]:text-base [&_h3]:font-semibold [&_h3]:mt-6 [&_h3]:mb-2 [&_h3]:text-fg ' +
  '[&_h4]:text-sm [&_h4]:font-semibold [&_h4]:uppercase [&_h4]:tracking-wide [&_h4]:text-muted [&_h4]:mt-5 [&_h4]:mb-2 ' +
  // lists
  '[&_ul]:my-4 [&_ul]:list-disc [&_ul]:pl-6 [&_ol]:my-4 [&_ol]:list-decimal [&_ol]:pl-6 [&_li]:my-1.5 [&_li]:leading-7 ' +
  '[&_li>ul]:my-1.5 [&_li>ol]:my-1.5 ' +
  // inline + block code
  '[&_code]:rounded [&_code]:bg-hover [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[0.85em] [&_code]:text-accent-text ' +
  '[&_pre]:my-4 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-line [&_pre]:bg-sunken [&_pre]:p-3.5 [&_pre]:text-[13px] [&_pre]:leading-6 ' +
  '[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-fg ' +
  // links
  '[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2 hover:[&_a]:text-accent-text ' +
  // tables — the shared grid, at page scale
  TABLE_GRID +
  '[&_table]:text-sm [&_th]:px-3 [&_th]:py-2 [&_th]:text-[11px] [&_td]:px-3 [&_td]:py-2 ' +
  // rules + quotes
  '[&_hr]:my-7 [&_hr]:border-line ' +
  '[&_blockquote]:my-4 [&_blockquote]:border-l-2 [&_blockquote]:border-accent/50 [&_blockquote]:bg-sunken/40 [&_blockquote]:py-1 [&_blockquote]:pl-4 [&_blockquote]:pr-3 [&_blockquote]:text-muted'

const REPORT =
  'text-[13px] leading-6 text-fg ' +
  '[&>p]:my-2.5 [&_p]:leading-6 ' +
  '[&_strong]:font-semibold [&_strong]:text-fg [&_em]:italic ' +
  // Bold does two jobs, so tint only the bold that OPENS a block — that one is its name.
  '[&_p>strong:first-child]:text-warn [&_li>strong:first-child]:text-warn ' +
  // headings — the panel's own section vocabulary, not a document's
  '[&_h1]:text-[14px] [&_h1]:font-semibold [&_h1]:text-fg [&_h1]:mt-0 [&_h1]:mb-3 ' +
  // h2 is the report's own section vocabulary, so it steps above the body while staying below the
  // amber block labels.
  '[&_h2]:text-[11px] [&_h2]:font-semibold [&_h2]:uppercase [&_h2]:tracking-wide [&_h2]:text-accent-text [&_h2]:mt-5 [&_h2]:mb-2 ' +
  '[&_h3]:text-[13px] [&_h3]:font-semibold [&_h3]:text-fg [&_h3]:mt-4 [&_h3]:mb-1.5 ' +
  '[&_h4]:text-[11px] [&_h4]:font-semibold [&_h4]:uppercase [&_h4]:tracking-wide [&_h4]:text-muted [&_h4]:mt-3.5 [&_h4]:mb-1.5 ' +
  // lists
  '[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-[18px] [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-[18px] ' +
  '[&_li]:my-1 [&_li]:leading-6 [&_li]:pl-0.5 [&_li>ul]:my-1 [&_li>ol]:my-1 ' +
  '[&_li]:marker:text-faint ' +
  // An ABSOLUTE 12px, never `em`: relative sizing shrank the same token by a different amount per
  // container.
  '[&_code]:rounded [&_code]:bg-hover [&_code]:px-1 [&_code]:py-px [&_code]:text-[12px] [&_code]:text-accent-text ' +
  '[&_pre]:my-2.5 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:border [&_pre]:border-line [&_pre]:bg-sunken [&_pre]:p-2.5 [&_pre]:text-[12px] [&_pre]:leading-5 ' +
  '[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-fg ' +
  '[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2 ' +
  // A cell is BODY text; the header row is a label, so it takes the meta step.
  TABLE_GRID +
  '[&_table]:text-[13px] [&_th]:px-2.5 [&_th]:py-1.5 [&_th]:text-[11px] [&_td]:px-2.5 [&_td]:py-1.5 ' +
  // rules + quotes
  '[&_hr]:my-4 [&_hr]:border-line ' +
  '[&_blockquote]:my-2.5 [&_blockquote]:border-l-2 [&_blockquote]:border-line [&_blockquote]:pl-3 [&_blockquote]:text-muted'

const VARIANTS = { chat: CHAT, doc: DOC, report: REPORT }

// A relative `.md` link is an INTERNAL doc link, resolved to a slug so the host routes in-app
// rather than navigating to a dead URL.
function internalSlug(href: string | undefined): string | null {
  if (!href || /^[a-z]+:/i.test(href) || href.startsWith('#')) return null
  const m = href.replace(/^\.?\//, '').match(/^([\w-]+)\.md$/i)
  if (!m) return null
  return m[1].toLowerCase() === 'readme' ? 'overview' : m[1]
}

// Only CODE spans take the scope hue. Literal class strings, so the JIT picks them up, and placed
// after the base so they win.
const TONE: Record<string, string> = {
  universal: '[&_code]:text-universal [&_pre_code]:text-universal',
  dev: '[&_code]:text-dev [&_pre_code]:text-dev',
  core: '[&_code]:text-core [&_pre_code]:text-core',
}
// Bold takes one consistent colour across every tinted preview, so emphasis reads uniformly.
const BOLD_TINT = '[&_strong]:text-warn'

// Markdown needs a BLANK LINE to end a block; without one a paragraph under a table parses as
// another row.
//
// A block label is deliberately narrow, so mid-sentence emphasis never matches.
const BLOCK_LABEL = /^\*\*[^*\n]{1,40}[.:]\*\*/
function openBlocks(md: string): string {
  const lines = md.split('\n')
  const out: string[] = []
  let fenced = false
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const prev = out[out.length - 1]
    if (!fenced && prev !== undefined && prev.trim() !== '' && !/^\s*\|/.test(prev)
        && BLOCK_LABEL.test(line))
      out.push('')
    out.push(line)
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced
    if (fenced) continue
    const next = lines[i + 1]
    if (/^\s*\|/.test(line) && next !== undefined && next.trim() !== '' && !/^\s*\|/.test(next))
      out.push('')
  }
  return out.join('\n')
}

export default function Markdown({
  text,
  variant = 'chat',
  tone,
  onInternalLink,
}: {
  text: string
  variant?: 'chat' | 'doc' | 'report'
  tone?: 'universal' | 'dev' | 'core'
  onInternalLink?: (slug: string) => void
}) {
  // Bold is rare in a preview and marks the one thing that matters; a report is bold-labelled top
  // to bottom.
  const tint = tone ? ' ' + TONE[tone] + (variant === 'report' ? '' : ' ' + BOLD_TINT) : ''
  // A comment is a comment: printed as a paragraph, an authoring note reads as instruction to
  // whoever opens the doc.
  const body = useMemo(() => openBlocks(text.replace(/[ \t]*<!--[\s\S]*?-->\n?/g, '')), [text])
  return (
    <div className={`${VARIANTS[variant]}${tint}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children, ...props }) {
            const slug = internalSlug(href)
            if (slug && onInternalLink) {
              return (
                <a
                  href={href}
                  onClick={(e) => {
                    e.preventDefault()
                    onInternalLink(slug)
                  }}
                  {...props}
                >
                  {children}
                </a>
              )
            }
            // External: open safely in a new tab.
            return (
              <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
                {children}
              </a>
            )
          },
        }}
      >
        {body}
      </ReactMarkdown>
    </div>
  )
}
