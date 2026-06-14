import { useState } from 'react'
import { injectNote } from '../api'

export default function InjectForm({ onInjected }: { onInjected: (path: string) => void }) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [status, setStatus] = useState<string | null>(null)

  async function submit() {
    if (!title.trim()) return
    setStatus('Saving…')
    try {
      const { path } = await injectNote(title.trim(), content)
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
      <div className="border-t border-slate-800 px-3 py-2">
        <button
          className="w-full rounded bg-slate-800 px-3 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700"
          onClick={() => setOpen(true)}
        >
          + Inject knowledge
        </button>
        {status && <div className="mt-1 text-xs text-slate-500">{status}</div>}
      </div>
    )
  }

  return (
    <div className="space-y-2 border-t border-slate-800 p-3">
      <input
        className="w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-200 outline-none ring-1 ring-slate-700"
        placeholder="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <textarea
        className="h-20 w-full resize-none rounded bg-slate-950 px-2 py-1 text-sm text-slate-200 outline-none ring-1 ring-slate-700"
        placeholder="What should SuperMe know?"
        value={content}
        onChange={(e) => setContent(e.target.value)}
      />
      <div className="flex gap-2">
        <button className="rounded bg-emerald-600 px-3 py-1 text-sm text-white" onClick={submit}>
          Save
        </button>
        <button className="rounded bg-slate-800 px-3 py-1 text-sm text-slate-300" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  )
}
