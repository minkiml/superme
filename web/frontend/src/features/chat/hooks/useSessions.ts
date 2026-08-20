import { useEffect, useRef, useState } from 'react'
import { listSessions, readSession, renameSession, deleteSession, type SessionMeta, type ChatMode } from '@/lib/api'
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

  // Keep the active id in sync across ref (sends), state (picker), and localStorage. `persist`
  // controls whether this becomes the context's remembered "general" session: TRUE for the
  // owner's own general chat, FALSE for a work-item binding (transient — it must not clobber the
  // general session so unbinding can restore it). See resumeStored().
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

  // The replay window grows in pages of 10 via "See more" (limitRef is the synchronous truth for
  // loadMore; msgLimit isn't needed as state — the message list itself drives rendering).
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

  // Restore the context's remembered general session (used when a work-item binding is dropped) —
  // the last non-work-item thread, or a blank new chat if there's none.
  function resumeStored() {
    const stored = localStorage.getItem(STORE_KEY)
    if (stored) openSession(stored)
    else newChat()
  }

  // One hard delete: drops the session row + its transcript from disk (its run trace is preserved
  // server-side). If the deleted session is the one open here, fall back to a fresh chat.
  async function removeSession(id: string) {
    try {
      await deleteSession(id, contextId)
    } catch {
      /* ignore */
    }
    if (sessionRef.current === id) newChat()
    refreshSessions()
  }

  // Set (or clear, with a blank title) an owner title override. Optimistic — patch the list row
  // immediately, then reconcile with the server's effective title (a clear re-derives it).
  async function renameSessionTitle(id: string, title: string) {
    setSessions((xs) => xs.map((s) => (s.id === id ? { ...s, title } : s)))
    try {
      const r = await renameSession(id, title, contextId)
      setSessions((xs) => xs.map((s) => (s.id === id ? { ...s, title: r.title } : s)))
    } catch {
      refreshSessions() // revert to server truth on failure
    }
  }

  // A finished turn may mint a brand-new session id — claim it (and list it if new). `persist`
  // is false for bound (work-item) turns so they don't overwrite the general session.
  function claimSession(id: string, persist = true) {
    // Refresh when the list has never heard of this id — a first session, or a work-item channel
    // whose phase just moved and answered from a thread minted this turn. Without it the row stays
    // stale and the rail cannot tell the new thread belongs to a channel it is already showing.
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
