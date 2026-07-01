import { useCallback, useEffect, useState } from 'react'
import { BookOpen, Loader2, RefreshCw } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import { readDoc, type Doc } from '@/lib/api'

// One doc, rendered. The dedicated Docs section (PRD docs dashboard) gives each doc its own
// sub-page in the sidebar accordion; `slug` selects which (or 'overview' = superme-docs/README.md).
// Listing/nav lives in App.tsx (fetched once, like Domains); this is purely the reading pane.

export default function DocsPage({
  slug = 'overview',
  onOpenDoc,
}: {
  slug?: string
  onOpenDoc?: (slug: string) => void
}) {
  const [doc, setDoc] = useState<Doc | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    readDoc(slug)
      .then((d) => {
        setDoc(d)
        setErr(null)
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false))
  }, [slug])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className="h-full overflow-y-auto">
    <div className="mx-auto max-w-4xl px-8 py-8">
      <div className="mb-5 flex items-center gap-3">
        <div className="flex items-center gap-2">
          <BookOpen size={18} className="text-accent-text" />
          <h1 className="text-lg font-semibold text-fg">{doc?.title ?? 'Docs'}</h1>
        </div>
        <button
          onClick={load}
          title="Refresh"
          className="ml-auto rounded p-1.5 text-muted transition hover:bg-hover hover:text-fg"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
        </button>
      </div>

      {err ? (
        <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          Couldn’t load this doc — {err}
        </div>
      ) : doc === null ? (
        <div className="flex items-center gap-2 py-10 text-sm text-muted">
          <Loader2 size={14} className="animate-spin" /> Loading…
        </div>
      ) : (
        <article className="rounded-xl border border-line bg-surface px-8 py-7">
          {/* Drop the doc's own leading "# Title" — the page header already shows it. */}
          <Markdown
            text={doc.content.replace(/^#\s+.*\n+/, '')}
            variant="doc"
            onInternalLink={onOpenDoc}
          />
        </article>
      )}
    </div>
    </div>
  )
}
