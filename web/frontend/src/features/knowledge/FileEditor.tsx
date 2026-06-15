import { useEffect, useState } from 'react'
import { readFile, writeFile } from '@/lib/api'

export default function FileEditor({
  path,
  contextId = 'global',
}: {
  path: string | null
  contextId?: string
}) {
  const [content, setContent] = useState('')
  const [loaded, setLoaded] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!path) return
    setLoading(true)
    setStatus(null)
    readFile(path, contextId)
      .then((c) => {
        setContent(c)
        setLoaded(c)
      })
      .catch((e) => setStatus(String(e)))
      .finally(() => setLoading(false))
  }, [path, contextId])

  if (!path) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted">
        Select a file from the knowledge tree to view or edit it.
      </div>
    )
  }

  const dirty = content !== loaded

  async function save() {
    if (!path) return
    setStatus('Saving…')
    try {
      await writeFile(path, content, contextId)
      setLoaded(content)
      setStatus('Saved ✓')
    } catch (e) {
      setStatus(String(e))
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-line px-4 py-2">
        <span className="font-mono text-sm text-fg">{path}</span>
        <div className="flex items-center gap-3">
          {status && <span className="text-xs text-muted">{status}</span>}
          <button
            className="rounded bg-success px-3 py-1 text-sm font-medium text-on-accent disabled:opacity-40"
            disabled={!dirty || loading}
            onClick={save}
          >
            Save
          </button>
        </div>
      </div>
      <textarea
        className="flex-1 resize-none bg-sunken p-4 font-mono text-sm text-fg outline-none"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        spellCheck={false}
      />
    </div>
  )
}
