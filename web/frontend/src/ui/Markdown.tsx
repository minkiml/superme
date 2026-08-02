import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Render markdown with Tailwind arbitrary-variant styling — no global CSS, no
// @tailwindcss/typography. Three variants:
//   'chat' (default) — tight, sized for assistant bubbles.
//   'doc'            — document-page quality: heading hierarchy, readable measure, styled
//                      tables, rules, code blocks (for the Docs dashboard).
//   'report'         — a doc rendered INSIDE a panel: the drilldown's Reports tab. Same markdown,
//                      but 'doc' is calibrated for a full-width page (15px/leading-7, 1.7rem h1,
//                      ruled h2, 4–7-unit rhythm) and in a ~700px modal beside 11–13px chrome that
//                      reads as a different application. This variant speaks the panel's voice:
//                      13px body, section heads in the same muted-caps as "Mechanical checks",
//                      compact tables. One accent rule — code is tinted, prose is not.

// ONE table grammar, every variant (owner, 2026-08-01). A markdown table is the same object
// wherever it lands — chat, docs page, drilldown report — and it was three different objects: a
// full grid here, horizontals-with-zebra there, entirely unstyled in chat. This holds the
// STRUCTURE; each variant appends only its own sizes below (Tailwind needs the size classes
// written out literally, so they can't be interpolated in).
//
// `border-separate` + zero spacing, NOT `border-collapse`: a collapsed table ignores its own
// border-radius, so `rounded-md` renders square. Separate borders honour it, at the cost of drawing
// the grid deliberately — each cell owns its RIGHT and BOTTOM rule, the table owns the outer frame,
// and the last cell in a row / the last row drop theirs so the edge stays one hairline, not two.
// No zebra: the rules carry the grid, and a stripe on top of them is a second answer to the same
// question. Auto layout (not table-fixed) so a wide evidence column can take the width a column of
// short ids doesn't need; `break-words` keeps one long token from overflowing the frame.
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
  '[&_code]:rounded [&_code]:bg-sunken [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[0.85em] ' +
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
  '[&_code]:rounded [&_code]:bg-sunken [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[0.85em] [&_code]:text-accent-text ' +
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
  // Reports are written as `**Label:** value` blocks, so bold does two jobs: it NAMES a block, and
  // it emphasises a word mid-sentence. Tinting all of it (what `tone` used to do) made every line
  // the loud line. Tint only the bold that OPENS a paragraph or list item — that one is the block's
  // name, the same role `SectionHeader` plays for a native section.
  '[&_p>strong:first-child]:text-warn [&_li>strong:first-child]:text-warn ' +
  // headings — the panel's own section vocabulary, not a document's
  '[&_h1]:text-[14px] [&_h1]:font-semibold [&_h1]:text-fg [&_h1]:mt-0 [&_h1]:mb-3 ' +
  '[&_h2]:text-[11px] [&_h2]:font-semibold [&_h2]:uppercase [&_h2]:tracking-wide [&_h2]:text-muted [&_h2]:mt-5 [&_h2]:mb-2 ' +
  '[&_h3]:text-[13px] [&_h3]:font-semibold [&_h3]:text-fg [&_h3]:mt-4 [&_h3]:mb-1.5 ' +
  '[&_h4]:text-[11px] [&_h4]:font-semibold [&_h4]:uppercase [&_h4]:tracking-wide [&_h4]:text-muted [&_h4]:mt-3.5 [&_h4]:mb-1.5 ' +
  // lists
  '[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-[18px] [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-[18px] ' +
  '[&_li]:my-1 [&_li]:leading-6 [&_li]:pl-0.5 [&_li>ul]:my-1 [&_li>ol]:my-1 ' +
  '[&_li]:marker:text-faint ' +
  // code — an ABSOLUTE 12px, never `em` (owner, 2026-08-02). Relative sizing made one `--date`
  // render 11.44px in a paragraph and 10.56px in a table cell on the same screen: the token that
  // most needs to stay legible was the one that shrank, and by a different amount per container.
  '[&_code]:rounded [&_code]:bg-sunken [&_code]:px-1 [&_code]:py-px [&_code]:text-[12px] ' +
  '[&_pre]:my-2.5 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:border [&_pre]:border-line [&_pre]:bg-sunken [&_pre]:p-2.5 [&_pre]:text-[12px] [&_pre]:leading-5 ' +
  '[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-fg ' +
  '[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2 ' +
  // tables — the shared grid. A cell is BODY text (13px) like every other sentence in the report;
  // the scope table IS the triage report's content, so shrinking it shrank the point of the page.
  // The header row is a label, so it takes the meta step (11px).
  TABLE_GRID +
  '[&_table]:text-[13px] [&_th]:px-2.5 [&_th]:py-1.5 [&_th]:text-[11px] [&_td]:px-2.5 [&_td]:py-1.5 ' +
  // rules + quotes
  '[&_hr]:my-4 [&_hr]:border-line ' +
  '[&_blockquote]:my-2.5 [&_blockquote]:border-l-2 [&_blockquote]:border-line [&_blockquote]:pl-3 [&_blockquote]:text-muted'

const VARIANTS = { chat: CHAT, doc: DOC, report: REPORT }

// A relative `*.md` link (e.g. `knowledge-tiers.md`, `./README.md`) is an INTERNAL doc link —
// resolve it to a doc slug so the host can route in-app instead of letting the browser navigate
// to a dead URL. Anything with a scheme (http:, mailto:) or an anchor is treated as external.
function internalSlug(href: string | undefined): string | null {
  if (!href || /^[a-z]+:/i.test(href) || href.startsWith('#')) return null
  const m = href.replace(/^\.?\//, '').match(/^([\w-]+)\.md$/i)
  if (!m) return null
  return m[1].toLowerCase() === 'readme' ? 'overview' : m[1]
}

// Optional scope tint — the prose stays white (the variant's `text-fg`); only inline/block CODE
// spans take the scope hue. Literal class strings so Tailwind's JIT picks them up; placed AFTER the
// variant base so they win over its `[&_code]:text-accent-text`. Used by the Foundations file
// preview (universal = purple, dev = blue, core = green).
const TONE: Record<string, string> = {
  universal: '[&_code]:text-universal [&_pre_code]:text-universal',
  dev: '[&_code]:text-dev [&_pre_code]:text-dev',
  core: '[&_code]:text-core [&_pre_code]:text-core',
}
// Bold (**…**) gets ONE consistent color across every tinted preview (independent of scope) so
// emphasis reads uniformly. Placed after the base so it wins over `[&_strong]:text-fg`.
const BOLD_TINT = '[&_strong]:text-warn'

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
  // The bold tint rides with the tone in a doc PREVIEW, where bold is rare and marks the one thing
  // that matters. A report is bold-labelled top to bottom — tinting all of it makes every line the
  // loud line, which is no hierarchy at all. So `report` takes the code tint and leaves prose alone.
  const tint = tone ? ' ' + TONE[tone] + (variant === 'report' ? '' : ' ' + BOLD_TINT) : ''
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
        {text}
      </ReactMarkdown>
    </div>
  )
}
