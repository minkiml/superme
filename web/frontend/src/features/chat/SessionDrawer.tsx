import { useRef, useState } from 'react'
import { Plus, X, Pencil } from 'lucide-react'
import type { SessionMeta } from '@/lib/api'

// The slide-over list of past conversations: start a new chat, open a past one (replays
// its transcript), rename it (owner title override), or forget one (the parent confirms before
// removing). Rendered conditionally by the parent; backdrop click closes it.
export default function SessionDrawer({
  sessions,
  activeId,
  busy,
  onClose,
  onNewChat,
  onOpenSession,
  onRename,
  onForget,
}: {
  sessions: SessionMeta[]
  activeId: string | null
  busy: boolean
  onClose: () => void
  onNewChat: () => void
  onOpenSession: (id: string) => void
  onRename: (id: string, title: string) => void // set/clear the owner title (blank ⇒ auto)
  onForget: (id: string) => void
}) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  // Enter and blur can both fire (Enter → we close the input → blur); this guards to a single commit
  // per edit. Reset when a new edit starts.
  const doneRef = useRef(false)

  function startEdit(s: SessionMeta) {
    doneRef.current = false
    setDraft(s.title)
    setEditingId(s.id)
  }
  function commit(id: string) {
    if (doneRef.current) return
    doneRef.current = true
    onRename(id, draft.trim()) // blank ⇒ server reverts to the transcript-derived title
    setEditingId(null)
  }
  function cancel() {
    doneRef.current = true // block the ensuing blur from committing
    setEditingId(null)
  }

  return (
    <div className="absolute inset-0 z-20 flex" onClick={onClose}>
      <div className="flex-1 bg-black/40" />
      <div
        className="flex h-full w-72 max-w-[85%] flex-col border-l border-line bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-3 py-2.5">
          <span className="text-sm font-semibold text-fg">Conversations</span>
          <button
            onClick={onClose}
            className="rounded p-1 text-muted hover:bg-hover hover:text-fg"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>
        <button
          onClick={() => {
            onNewChat()
            onClose()
          }}
          disabled={busy}
          className="m-2 flex items-center gap-1.5 rounded-md border border-line px-2.5 py-2 text-left text-xs font-medium text-fg hover:bg-hover disabled:opacity-50"
        >
          <Plus size={14} /> New chat
        </button>
        <div className="min-h-0 flex-1 space-y-0.5 overflow-auto px-2 pb-2">
          {sessions.length === 0 && (
            <div className="px-2 py-3 text-xs text-muted">No past conversations yet.</div>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`group flex items-center gap-1 rounded-md px-2 py-1.5 ${
                activeId === s.id ? 'bg-accent-soft' : 'hover:bg-hover'
              }`}
            >
              {editingId === s.id ? (
                <input
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.preventDefault(); commit(s.id) }
                    else if (e.key === 'Escape') { e.preventDefault(); cancel() }
                  }}
                  onBlur={() => commit(s.id)}
                  placeholder="Title (blank = auto)"
                  className="min-w-0 flex-1 rounded border border-accent bg-app px-1.5 py-1 text-xs text-fg outline-none"
                />
              ) : (
                <>
                  <button
                    onClick={() => {
                      if (!busy) {
                        onOpenSession(s.id)
                        onClose()
                      }
                    }}
                    disabled={busy}
                    className="min-w-0 flex-1 text-left disabled:opacity-50"
                  >
                    <div className={`truncate text-xs ${activeId === s.id ? 'text-accent-text' : 'text-fg'}`}>
                      {s.title}
                    </div>
                    <div className="text-[10px] text-muted">
                      {s.surface} · {s.message_count} msg{s.message_count === 1 ? '' : 's'}
                    </div>
                  </button>
                  <button
                    onClick={() => startEdit(s)}
                    className="shrink-0 rounded p-1 text-muted opacity-0 hover:text-accent-text group-hover:opacity-100"
                    title="Rename this session"
                    aria-label="Rename this session"
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    onClick={() => onForget(s.id)}
                    className="shrink-0 rounded p-1 text-muted opacity-0 hover:text-danger group-hover:opacity-100"
                    title="Remove this session"
                    aria-label="Remove this session"
                  >
                    <X size={14} />
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
