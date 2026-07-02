import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Hammer, X } from 'lucide-react'
import ChatHeader from './ChatHeader'
import MessageList from './MessageList'
import Composer from './Composer'
import SessionDrawer from './SessionDrawer'
import ConfirmDialog from '@/ui/ConfirmDialog'
import { useAgentSocket } from './hooks/useAgentSocket'
import { useSessions } from './hooks/useSessions'
import { GLOBAL, type ContextRef } from '@/lib/contexts'
import type { ChatMode } from '@/lib/api'

// A dev-mode binding: the chat is taken over as one work-item's dev thread.
export type DevBinding = { workItemId: string; sessionId: string | null; title: string; contextId: string }

// The persistent chat rail. Its context is selectable and detached from whichever
// dashboard page is active — the parent remounts it via a `key` on context change, so the
// two hooks below capture `contextId` once and their `[]` effects re-run for the new one.
//
// This component is just the conductor: useSessions owns the conversation list + replayed
// bubbles, useAgentSocket owns the live WebSocket turn, and the presentational children
// render them.
export default function ChatPanel({
  contextId = 'global',
  contexts = [GLOBAL],
  onContextChange,
  onCollapse,
  mode = 'core',
  onModeChange,
  binding = null,
  onUnbind,
  onBindingSession,
  tag,
  modelOverride,
  effortOverride,
}: {
  contextId?: string
  contexts?: ContextRef[]
  onContextChange?: (id: string) => void
  onCollapse?: () => void
  mode?: ChatMode
  onModeChange?: (m: ChatMode) => void
  binding?: DevBinding | null
  onUnbind?: () => void
  onBindingSession?: (sessionId: string) => void
  tag?: { color: string; icon: string | null; isHub: boolean }
  modelOverride?: string | null // the context's current /model selection (for the picker)
  effortOverride?: string | null // the context's current /effort selection (for the picker)
}) {
  const ctxLabel = contexts.find((c) => c.id === contextId)?.label ?? contextId
  const [input, setInput] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [confirmId, setConfirmId] = useState<string | null>(null) // session pending "forget"

  const sessions = useSessions(contextId, mode)
  const socket = useAgentSocket(contextId, mode, {
    onResult: (text, sessionId) => {
      sessions.appendMessage({ role: 'superme', text })
      if (sessionId) {
        // A bound turn's session belongs to the work-item, not the context's general chat —
        // claim it transiently (persist=false) so it doesn't overwrite the remembered general
        // session; the item owns it (surfaced up + persisted onto the work-item below).
        sessions.claimSession(sessionId, !binding)
        if (binding && sessionId !== binding.sessionId) onBindingSession?.(sessionId)
      }
    },
    onError: (message) => sessions.appendMessage({ role: 'superme', text: '⚠ ' + message }),
  })

  // Binding take-over: open the item's dev thread (resume its session, or a fresh chat if it has
  // none yet) — TRANSIENTLY, so it doesn't clobber the context's general session. When the binding
  // is dropped (unbind, Inbox tab, or switching to core), restore the general session. Keyed on
  // the work-item id, plus a first-mount guard so a fresh mount's own resume isn't overridden.
  const boundItemRef = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    const id = binding?.workItemId ?? null
    const first = boundItemRef.current === undefined
    if (id === boundItemRef.current) return
    boundItemRef.current = id
    socket.clearStream()
    socket.clearMeta() // the model·context% readout belongs to a turn, not a session — reset it
    if (binding) {
      if (binding.sessionId) sessions.openSession(binding.sessionId, false)
      else sessions.newChat(false)
    } else if (!first) {
      // Unbound after having been bound — go back to the remembered general session.
      sessions.resumeStored()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [binding?.workItemId])

  function send() {
    const text = input
    if (!text.trim()) return
    // Carry the chat mode; a card binding also tags the work-item so the daemon persists
    // the session to it. A general dev chat (no binding) sends mode=dev with no work-item.
    if (!socket.send(text, sessions.sessionRef.current, { mode, workItemId: binding?.workItemId })) return
    sessions.appendMessage({ role: 'you', text })
    setInput('')
  }

  // Send an exact command (e.g. the model picker's `/model opus`) without touching the input box.
  function sendCommand(text: string) {
    if (!socket.send(text, sessions.sessionRef.current, { mode, workItemId: binding?.workItemId })) return
    sessions.appendMessage({ role: 'you', text })
  }

  function openSession(id: string) {
    sessions.openSession(id)
    socket.clearStream()
    socket.clearMeta() // don't carry the previous session's model·context% into this one
  }

  function newChat() {
    sessions.newChat()
    socket.clearStream()
    socket.clearMeta()
    setInput('')
  }

  const activeTitle = sessions.activeId
    ? (sessions.sessions.find((s) => s.id === sessions.activeId)?.title ?? 'Conversation')
    : 'New chat'
  const confirmTitle = sessions.sessions.find((s) => s.id === confirmId)?.title

  return (
    <div
      className="relative flex h-full min-h-0 flex-col border-l border-line bg-surface"
      // The chat rail's accent follows the mode: core = mint (pastel green), dev = blue.
      style={{ ['--chat-accent' as string]: mode === 'core' ? 'var(--c-core)' : 'var(--c-dev)' } as CSSProperties}
    >
      <ChatHeader
        ready={socket.ready}
        contexts={contexts}
        contextId={contextId}
        tag={tag}
        onContextChange={onContextChange}
        busy={socket.busy}
        onCollapse={onCollapse}
        activeTitle={activeTitle}
        meta={socket.meta}
        onOpenDrawer={() => { sessions.refreshSessions(); setDrawerOpen(true) }}
        mode={mode}
        onModeChange={(m) => onModeChange?.(m)}
      />

      {binding && (
        <div className="flex items-center gap-1.5 border-b border-accent/30 bg-accent/10 px-3 py-1.5 text-[11px]">
          <Hammer size={12} className="shrink-0 text-accent-text" />
          <span className="text-accent-text">Dev</span>
          <span className="min-w-0 flex-1 truncate text-fg" title={binding.title}>
            {binding.title}
          </span>
          <button
            onClick={onUnbind}
            title="Unbind — back to chat"
            aria-label="Unbind"
            className="shrink-0 rounded p-0.5 text-muted hover:bg-hover hover:text-fg"
          >
            <X size={13} />
          </button>
        </div>
      )}

      <MessageList
        messages={sessions.messages}
        live={socket.live}
        busy={socket.busy}
        statusLabel={socket.statusLabel}
        elapsed={socket.elapsed}
        olderHidden={sessions.olderHidden}
        approval={socket.approval}
        ctxLabel={ctxLabel}
        onAnswer={socket.answer}
        tone={mode === 'core' ? 'core' : 'dev'}
      />

      <Composer
        value={input}
        onChange={setInput}
        onSend={send}
        ready={socket.ready}
        busy={socket.busy}
        commands={socket.commands}
        ctxLabel={ctxLabel}
        onPaletteOpen={socket.refreshCommands}
        modelOverride={modelOverride}
        effortOverride={effortOverride}
        onSelectModel={(model, effort) => sendCommand(`/model ${model} ${effort}`)}
      />

      {drawerOpen && (
        <SessionDrawer
          sessions={sessions.sessions}
          activeId={sessions.activeId}
          busy={socket.busy}
          onClose={() => setDrawerOpen(false)}
          onNewChat={newChat}
          onOpenSession={openSession}
          onForget={setConfirmId}
        />
      )}

      {confirmId && (
        <ConfirmDialog
          title="Remove this session?"
          body={
            <>
              {confirmTitle ? <span className="text-fg">“{confirmTitle}”</span> : 'This session'}:{' '}
              <span className="text-fg">Forget</span> removes it from your list but keeps the transcript on
              disk. <span className="text-fg">Delete from disk</span> also erases the transcript —
              permanent, no recovery.
            </>
          }
          secondaryLabel="Forget"
          onSecondary={() => {
            sessions.removeSession(confirmId, false)
            setConfirmId(null)
          }}
          confirmLabel="Delete from disk"
          onCancel={() => setConfirmId(null)}
          onConfirm={() => {
            sessions.removeSession(confirmId, true)
            setConfirmId(null)
          }}
        />
      )}
    </div>
  )
}
