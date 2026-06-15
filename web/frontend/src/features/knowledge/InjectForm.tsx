import { useState } from 'react'
import { injectNote } from '@/lib/api'

export default function InjectForm({
  onInjected,
  contextId = 'global',
}: {
  onInjected: (path: string) => void
  contextId?: string
}) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [status, setStatus] = useState<string | null>(null)

  async function submit() {
    if (!title.trim()) return
    setStatus('Saving…')
    try {
      const { path } = await injectNote(title.trim(), content, 'knowledge', contextId)
      setStatus(`Injected → ${path}`)
      setTitle('')
      setContent('')
      setOpen(false)
      onInjected(path)
    } catch (e) {
      setStatus(String(e))
    }
  }

  if (!open) {
    return (
      <div className="border-t border-line px-3 py-2">
        <button
          className="w-full rounded bg-hover px-3 py-2 text-sm font-medium text-fg hover:bg-hover"
          onClick={() => setOpen(true)}
        >
          + Inject knowledge
        </button>
        {status && <div className="mt-1 text-xs text-muted">{status}</div>}
      </div>
    )
  }

  return (
    <div className="space-y-2 border-t border-line p-3">
      <input
        className="w-full rounded bg-sunken px-2 py-1 text-sm text-fg outline-none ring-1 ring-line"
        placeholder="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <textarea
        className="h-20 w-full resize-none rounded bg-sunken px-2 py-1 text-sm text-fg outline-none ring-1 ring-line"
        placeholder="What should SuperMe know?"
        value={content}
        onChange={(e) => setContent(e.target.value)}
      />
      <div className="flex gap-2">
        <button className="rounded bg-success px-3 py-1 text-sm text-on-accent" onClick={submit}>
          Save
        </button>
        <button className="rounded bg-hover px-3 py-1 text-sm text-fg" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  )
}
