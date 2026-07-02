import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Render markdown with Tailwind arbitrary-variant styling — no global CSS, no
// @tailwindcss/typography. Two variants:
//   'chat' (default) — tight, sized for assistant bubbles.
//   'doc'            — document-page quality: heading hierarchy, readable measure, styled
//                      tables, rules, code blocks (for the Docs dashboard).

const CHAT =
  'text-sm leading-relaxed space-y-2 ' +
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
  // tables — bordered, padded, header tint, zebra
  '[&_table]:my-5 [&_table]:w-full [&_table]:border-collapse [&_table]:text-sm [&_table]:overflow-hidden [&_table]:rounded-lg [&_table]:border [&_table]:border-line ' +
  '[&_thead]:bg-sunken ' +
  '[&_th]:border-b [&_th]:border-line [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-fg ' +
  '[&_td]:border-t [&_td]:border-line [&_td]:px-3 [&_td]:py-2 [&_td]:align-top ' +
  '[&_tbody_tr:nth-child(even)]:bg-surface/40 ' +
  // rules + quotes
  '[&_hr]:my-7 [&_hr]:border-line ' +
  '[&_blockquote]:my-4 [&_blockquote]:border-l-2 [&_blockquote]:border-accent/50 [&_blockquote]:bg-sunken/40 [&_blockquote]:py-1 [&_blockquote]:pl-4 [&_blockquote]:pr-3 [&_blockquote]:text-muted'

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
  variant?: 'chat' | 'doc'
  tone?: 'universal' | 'dev' | 'core'
  onInternalLink?: (slug: string) => void
}) {
  return (
    <div className={`${variant === 'doc' ? DOC : CHAT}${tone ? ' ' + TONE[tone] + ' ' + BOLD_TINT : ''}`}>
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
