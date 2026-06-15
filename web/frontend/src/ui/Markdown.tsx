import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Render assistant/markdown text (bold, italic, code, lists, links, headings) with
// Tailwind arbitrary-variant styling — no global CSS, no @tailwindcss/typography.
const MD =
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

export default function Markdown({ text }: { text: string }) {
  return (
    <div className={MD}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  )
}
