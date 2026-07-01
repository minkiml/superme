import { useEffect, useRef, useState } from 'react'
import { listSessions, readSession, deleteSession, type SessionMeta, type ChatMode } from '@/lib/api'
import type { Msg } from '../types'

// Owns the conversation list + the active session's replayed bubbles, and the
// localStorage continuity that survives a refresh. The ChatPanel remounts (parent `key`)
// when the context OR mode changes, so `contextId`/`mode` are fixed for this hook's
// lifetime and the mount effect can stay `[]`. The list + stored session are scoped by
// mode (core|dev) so each mode shows only its own threads.
export function useSessions(contextId: string, mode: ChatMode = 'core') {
  const STORE_KEY = `superme.session.${contextId}.${mode}`
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Msg[]>([])
  const [olderHidden, setOlderHidden] = useState(0) // # of replayed-but-skipped older bubbles
  const sessionRef = useRef<string | null>(null) // synchronous read for ws sends

  // Keep the active id in sync across ref (sends), state (picker), and localStorage.
  function setSession(id: string | null) {
    sessionRef.current = id
    setActiveId(id)
    if (id) localStorage.setItem(STORE_KEY, id)
    else localStorage.removeItem(STORE_KEY)
  }

  async function refreshSessions() {
    try {
      setSessions(await listSessions(contextId, mode))
    } catch {
      /* daemon may be down; leave the list as-is */
    }
  }

  // Replay a past session's bubbles (history lives in the SDK transcript).
  async function openSession(id: string) {
    setSession(id)
    try {
      const s = await readSession(id, contextId)
      setMessages(s.messages)
      setOlderHidden(s.truncated ? s.total - s.messages.length : 0)
    } catch {
      setMessages([])
      setOlderHidden(0)
    }
  }

  function newChat() {
    setSession(null)
    setMessages([])
    setOlderHidden(0)
  }

  // Forget (default) keeps the transcript on disk; purge=true deletes it from disk too.
  async function removeSession(id: string, purge = false) {
    try {
      await deleteSession(id, contextId, purge)
    } catch {
      /* ignore */
    }
    if (sessionRef.current === id) newChat()
    refreshSessions()
  }

  // A finished turn may mint a brand-new session id — claim it (and list it if new).
  function claimSession(id: string) {
    const wasNew = !sessionRef.current
    setSession(id)
    if (wasNew) refreshSessions()
  }

  function appendMessage(m: Msg) {
    setMessages((x) => [...x, m])
  }

  // On mount: resume the stored session (replay its bubbles) + load the list.
  useEffect(() => {
    const stored = localStorage.getItem(STORE_KEY)
    if (stored) openSession(stored)
    refreshSessions()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return {
    sessions,
    activeId,
    messages,
    olderHidden,
    sessionRef,
    refreshSessions,
    openSession,
    newChat,
    removeSession,
    claimSession,
    appendMessage,
  }
}
