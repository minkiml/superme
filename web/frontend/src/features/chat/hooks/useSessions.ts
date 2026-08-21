import { useEffect, useRef, useState } from 'react'
import { listSessions, readSession, renameSession, deleteSession, type SessionMeta, type ChatMode } from '@/lib/api'
import type { Msg } from '../types'

// Owns the conversation list, the active session's replayed bubbles, and the continuity that
// survives a refresh.
//
// The panel remounts when context or mode changes, so both are fixed for this hook's lifetime.
export function useSessions(contextId: string, mode: ChatMode = 'core') {
  const STORE_KEY = `superme.session.${contextId}.${mode}`
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Msg[]>([])
  const [olderHidden, setOlderHidden] = useState(0) // # of replayed-but-skipped older bubbles
  const sessionRef = useRef<string | null>(null) // synchronous read for ws sends

  // Keep the active id in sync. `persist` decides whether it becomes the remembered general
  // session.
  function setSession(id: string | null, persist = true) {
    sessionRef.current = id
    setActiveId(id)
    if (!persist) return
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

  // The replay window grows in pages; `limitRef` is the synchronous truth for loading more.
  const PAGE = 10
  const limitRef = useRef(PAGE)

  async function fetchMessages(id: string, limit: number) {
    const s = await readSession(id, contextId, limit)
    setMessages(s.messages)
    setOlderHidden(s.truncated ? s.total - s.messages.length : 0)
  }

  // Replay a past session's bubbles (history lives in the SDK transcript). Resets the window to the
  // most recent page.
  async function openSession(id: string, persist = true) {
    setSession(id, persist)
    limitRef.current = PAGE
    try {
      await fetchMessages(id, PAGE)
    } catch {
      setMessages([])
      setOlderHidden(0)
    }
  }

  // "See more": widen the replay window by another page and re-read (reveals 10 older bubbles).
  async function loadMoreMessages() {
    const id = sessionRef.current
    if (!id) return
    limitRef.current += PAGE
    try {
      await fetchMessages(id, limitRef.current)
    } catch {
      /* keep the current window on failure */
    }
  }

  function newChat(persist = true) {
    setSession(null, persist)
    setMessages([])
    setOlderHidden(0)
  }

  // Restore the remembered general session, or a blank new chat when there is none.
  function resumeStored() {
    const stored = localStorage.getItem(STORE_KEY)
    if (stored) openSession(stored)
    else newChat()
  }

  // One hard delete of the row and its transcript; the run trace is preserved server-side.
  async function removeSession(id: string) {
    try {
      await deleteSession(id, contextId)
    } catch {
      /* ignore */
    }
    if (sessionRef.current === id) newChat()
    refreshSessions()
  }

  // Optimistic: patch the row immediately, then reconcile with the server's effective title.
  async function renameSessionTitle(id: string, title: string) {
    setSessions((xs) => xs.map((s) => (s.id === id ? { ...s, title } : s)))
    try {
      const r = await renameSession(id, title, contextId)
      setSessions((xs) => xs.map((s) => (s.id === id ? { ...s, title: r.title } : s)))
    } catch {
      refreshSessions() // revert to server truth on failure
    }
  }

  // A finished turn may mint a new id. `persist` is false for bound turns.
  function claimSession(id: string, persist = true) {
    // Refresh when the list has never heard of this id — a first session, or a freshly minted
    // thread.
    const known = sessions.some((s) => s.id === id || (s.thread_ids ?? []).includes(id))
    setSession(id, persist)
    if (!sessionRef.current || !known) refreshSessions()
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
    resumeStored,
    removeSession,
    renameSessionTitle,
    loadMoreMessages,
    claimSession,
    appendMessage,
  }
}
