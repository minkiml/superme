import { useState } from 'react'
import ChatHeader from './ChatHeader'
import MessageList from './MessageList'
import Composer from './Composer'
import SessionDrawer from './SessionDrawer'
import ConfirmDialog from '@/ui/ConfirmDialog'
import { useAgentSocket } from './hooks/useAgentSocket'
import { useSessions } from './hooks/useSessions'
import { GLOBAL, type ContextRef } from '@/lib/contexts'

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
}: {
  contextId?: string
  contexts?: ContextRef[]
  onContextChange?: (id: string) => void
  onCollapse?: () => void
}) {
  const ctxLabel = contexts.find((c) => c.id === contextId)?.label ?? contextId
  const [input, setInput] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [confirmId, setConfirmId] = useState<string | null>(null) // session pending "forget"

  const sessions = useSessions(contextId)
  const socket = useAgentSocket(contextId, {
    onResult: (text, sessionId) => {
      sessions.appendMessage({ role: 'superme', text })
      if (sessionId) sessions.claimSession(sessionId)
    },
    onError: (message) => sessions.appendMessage({ role: 'superme', text: '⚠ ' + message }),
  })

  function send() {
    const text = input
    if (!text.trim()) return
    if (!socket.send(text, sessions.sessionRef.current)) return
    sessions.appendMessage({ role: 'you', text })
    setInput('')
  }

  function openSession(id: string) {
    sessions.openSession(id)
    socket.clearStream()
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
    <div className="relative flex h-full min-h-0 flex-col border-l border-line bg-surface">
      <ChatHeader
        ready={socket.ready}
        contexts={contexts}
        contextId={contextId}
        onContextChange={onContextChange}
        busy={socket.busy}
        onCollapse={onCollapse}
        activeTitle={activeTitle}
        meta={socket.meta}
        onOpenDrawer={() => setDrawerOpen(true)}
      />

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
      />

      <Composer
        value={input}
        onChange={setInput}
        onSend={send}
        ready={socket.ready}
        busy={socket.busy}
        commands={socket.commands}
        ctxLabel={ctxLabel}
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
          title="Forget this session?"
          body={
            <>
              {confirmTitle ? <span className="text-fg">“{confirmTitle}”</span> : 'This session'} will be
              removed from your list. The conversation history is kept on disk — nothing is deleted.
            </>
          }
          confirmLabel="Forget"
          onCancel={() => setConfirmId(null)}
          onConfirm={() => {
            sessions.removeSession(confirmId)
            setConfirmId(null)
          }}
        />
      )}
    </div>
  )
}
