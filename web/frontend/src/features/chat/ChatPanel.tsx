import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Hammer, Plus, PanelRightOpen } from 'lucide-react'
import ChatHeader from './ChatHeader'
import MessageList from './MessageList'
import Composer from './Composer'
import SessionDrawer from './SessionDrawer'
import { sessionCategory } from './sessionCategory'
import ConfirmDialog from '@/ui/ConfirmDialog'
import { useAgentSocket } from './hooks/useAgentSocket'
import { useSessions } from './hooks/useSessions'
import { GLOBAL, type ContextRef } from '@/lib/contexts'
import { getRuns, type ChatMode, type SessionMeta } from '@/lib/api'

// A dev-mode binding: the chat is taken over as one work-item's dev thread.
export type DevBinding = { workItemId: string; sessionId: string | null; title: string; contextId: string }
// A one-shot turn that BIRTHS a fresh session (never inherits the open one). `kind`/`subjectRunId`
// stamp its agent identity — v1: kind='diagnosis' pointed at the Activity run it inspects.
export type SeedTurn = { prompt: string; kind?: string; subjectRunId?: number }

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
  seed,
  onSeedConsumed,
  collapsed = false,
  onExpand,
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
  seed?: SeedTurn | null // a one-shot turn to birth a fresh session (onboarding / diagnosis); parent clears it
  onSeedConsumed?: () => void
  collapsed?: boolean // render the narrow quick-switch rail instead of the full panel
  onExpand?: () => void // open the full panel (from the collapsed rail)
}) {
  const ctxLabel = contexts.find((c) => c.id === contextId)?.label ?? contextId
  const [input, setInput] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [confirmId, setConfirmId] = useState<string | null>(null) // session pending "forget"
  // The per-session model/effort runtime override (the composer picker). Held here and sent per-turn
  // as msg.model / msg.effort — it NEVER writes a persisted default. Model re-derives from the
  // session's last run on open (session-model-precedence); effort resets per session (runs don't
  // record effort). null = follow the server precedence (work-item → repo → system).
  const [sessionModel, setSessionModel] = useState<string | null>(null)
  const [sessionEffort, setSessionEffort] = useState<string | null>(null)

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

  // The work-item this chat is actually on — derived from the ACTIVE SESSION's durable stamp (server
  // truth, work-item-session-recognition-prd), so the indicator is correct however the session was
  // opened (work-item card OR picker) and clears when you switch to a general session. `binding` is
  // only an OPTIMISTIC fallback for a just-clicked card whose session isn't listed yet (or an item
  // that has no session at all yet).
  const activeSess = sessions.sessions.find((s) => s.id === sessions.activeId)
  const stampedItem = activeSess?.item_id
    ? { id: activeSess.item_id, title: activeSess.item_title || activeSess.item_id }
    : null
  const optimisticItem =
    binding && (binding.sessionId === sessions.activeId || (binding.sessionId == null && sessions.activeId == null))
      ? { id: binding.workItemId, title: binding.title }
      : null
  const chipItem = stampedItem ?? optimisticItem

  // Reconcile: once the active session diverges from a card-set binding, that binding is stale — drop
  // it so it can't mis-tag turns or leave a wrong indicator up. Keyed on activeId ALONE (not binding),
  // so a fresh card-click isn't cleared before its take-over effect opens the item's session.
  useEffect(() => {
    if (binding?.sessionId && binding.sessionId !== sessions.activeId) onUnbind?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions.activeId])

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
    } else if (!first && sessions.activeId != null) {
      // Unbound after having been bound — go back to the remembered general session. But if the rail
      // is already on a fresh empty chat (activeId null — e.g. "New chat" pressed while bound), keep
      // that; resuming the stored session would clobber the just-created new chat.
      sessions.resumeStored()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [binding?.workItemId])

  // Persist the model·context% readout across session opens: when the active session changes (mount
  // resume, open-session, binding, unbind) seed the header from that session's LAST recorded run, so
  // it shows the model instead of "history". Only OVERWRITES on a found run — never clears here, so a
  // freshly-finished live turn's meta (or a brand-new session mid-turn) is left intact.
  useEffect(() => {
    setSessionEffort(null)                     // effort isn't re-derivable from a run → reset per session
    const id = sessions.activeId
    if (!id) { setSessionModel(null); return } // new chat → follow the server default
    setSessionModel(null)                      // default until this session's last run seeds it
    let alive = true
    getRuns(contextId)
      .then(({ live, history }) => {
        if (!alive) return
        const runs = [...live, ...history].filter((r) => r.session_id === id)
        if (!runs.length) return
        const last = runs.reduce((a, b) => (Date.parse(b.started_at) > Date.parse(a.started_at) ? b : a))
        socket.seedMeta({ model: last.model ?? null, pct: last.ctx_pct ?? null, window: null })
        setSessionModel(last.model ?? null)    // re-apply the session's last model (session-model-precedence)
      })
      .catch(() => {})
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions.activeId, contextId])

  function send() {
    const text = input
    if (!text.trim()) return
    // Carry the chat mode + the per-session model/effort override; a card binding also tags the
    // work-item so the daemon persists the session to it. A general dev chat (no binding) sends
    // mode=dev with no work-item.
    if (!socket.send(text, sessions.sessionRef.current,
                     { mode, workItemId: binding?.workItemId, model: sessionModel, effort: sessionEffort })) return
    sessions.appendMessage({ role: 'you', text })
    setInput('')
  }

  // One-shot seed (onboarding launch): send the kickoff once the socket is ready, then tell the parent
  // to clear it. A workflow launch BIRTHS ITS OWN fresh general session — it must never inherit whichever
  // session is open (esp. a work-item session, which would run onboarding bound to that item). So we
  // start a new chat and send with an EXPLICIT null resume + no workItemId, so the daemon mints a fresh
  // session regardless of the rail's current state. Guarded so it fires once per seed.
  const seededRef = useRef(false)
  useEffect(() => {
    if (!seed) { seededRef.current = false; return }
    if (!socket.ready || seededRef.current) return
    seededRef.current = true
    if (binding) onUnbind?.()          // drop any work-item binding before the fresh workflow session
    sessions.newChat()                 // clear the rail to a new session
    socket.clearStream()
    socket.clearMeta()
    // kind/subjectRunId (v1: diagnosis pointed at an Activity run) ride the birth turn — the daemon
    // stamps them write-once so the session keeps its identity on resume.
    if (!socket.send(seed.prompt, null, { mode, kind: seed.kind, subjectRunId: seed.subjectRunId })) {
      seededRef.current = false
      return
    }
    sessions.appendMessage({ role: 'you', text: seed.prompt })
    onSeedConsumed?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed, socket.ready])

  function openSession(id: string) {
    sessions.openSession(id)
    socket.clearStream()
    socket.clearMeta() // don't carry the previous session's model·context% into this one
  }

  function newChat() {
    // A New chat is always a fresh GENERAL session — drop any work-item binding first, or the new
    // chat inherits it and its turns get mis-tagged to that item (the take-over effect keeps the
    // fresh chat rather than resuming the stored session, thanks to its activeId==null guard).
    if (binding) onUnbind?.()
    sessions.newChat()
    socket.clearStream()
    socket.clearMeta()
    setInput('')
  }

  const activeTitle = sessions.activeId
    ? (sessions.sessions.find((s) => s.id === sessions.activeId)?.title ?? 'Conversation')
    : 'New chat'
  const confirmTitle = sessions.sessions.find((s) => s.id === confirmId)?.title

  // Keep the collapsed rail's session list fresh — refresh whenever we enter collapsed mode.
  useEffect(() => {
    if (collapsed) sessions.refreshSessions()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collapsed])

  // Collapsed → the narrow quick-switch rail: recent sessions (current repo + mode) as category
  // chips + a dev/core toggle, no repo switcher. Clicking a session (or New chat) opens it and
  // expands the panel. All the session state lives here already (useSessions), so the rail is just
  // an alternate render of this same conductor.
  if (collapsed) {
    return (
      <CollapsedRail
        sessions={sessions.sessions}
        activeId={sessions.activeId}
        mode={mode}
        onModeChange={(m) => onModeChange?.(m)}
        onOpenSession={(id) => { openSession(id); onExpand?.() }}
        onNewChat={() => { newChat(); onExpand?.() }}
        onExpand={() => onExpand?.()}
      />
    )
  }

  return (
    <div
      className="relative flex h-full min-h-0 flex-col border-l border-line bg-surface"
      // The chat rail's accent follows the mode: core = mint (pastel green), dev = blue.
      style={{ ['--chat-accent' as string]: mode === 'core' ? 'rgb(var(--c-core))' : 'rgb(var(--c-dev))' } as CSSProperties}
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

      {chipItem && (
        <div className="flex items-center gap-1.5 border-b border-accent/30 bg-accent/10 px-3 py-1.5 text-[11px]">
          <Hammer size={12} className="shrink-0 text-accent-text" />
          <span className="text-accent-text">Dev</span>
          <span className="min-w-0 flex-1 truncate text-fg" title={chipItem.title}>
            {chipItem.title}
          </span>
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
        onLoadMore={sessions.loadMoreMessages}
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
        modelOverride={sessionModel}
        effortOverride={sessionEffort}
        onSelectModel={(model, effort) => {
          // A pure FE state change — the session's runtime model/effort, sent on the next turn's
          // frame. 'reset' clears back to the server default. NEVER writes a persisted default.
          setSessionModel(model === 'reset' ? null : model)
          setSessionEffort(effort === 'reset' ? null : effort)
        }}
      />

      {drawerOpen && (
        <SessionDrawer
          sessions={sessions.sessions}
          activeId={sessions.activeId}
          busy={socket.busy}
          onClose={() => setDrawerOpen(false)}
          onNewChat={newChat}
          onOpenSession={openSession}
          onRename={sessions.renameSessionTitle}
          onForget={setConfirmId}
        />
      )}

      {confirmId && (
        <ConfirmDialog
          title="Delete this session?"
          body={
            <>
              {confirmTitle ? <span className="text-fg">“{confirmTitle}”</span> : 'This session'} will be
              permanently deleted — its transcript is erased and it can’t be reworked. Its activity &amp;
              token history is kept.
            </>
          }
          confirmLabel="Delete"
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

// The collapsed chat rail — a slim quick-switcher. Shows the current repo's recent sessions (this
// mode) as category chips, a dev/core mode toggle, and New chat. No repo switcher: the sessions are
// always the repo the rail is currently pointed at (session-kinds-diagnose categories).
function CollapsedRail({
  sessions, activeId, mode, onModeChange, onOpenSession, onNewChat, onExpand,
}: {
  sessions: SessionMeta[]
  activeId: string | null
  mode: ChatMode
  onModeChange: (m: ChatMode) => void
  onOpenSession: (id: string) => void
  onNewChat: () => void
  onExpand: () => void
}) {
  // Only threads touched in the last 24h, most-recent first, capped at 8 — the collapsed rail is a
  // quick-switcher for what's live now, not a full history (that's the expanded drawer).
  const dayAgo = Date.now() - 24 * 60 * 60 * 1000
  const recent = sessions
    .filter((s) => Date.parse(s.updated_at) >= dayAgo)
    .slice(0, 8)

  // Hover tooltip is rendered fixed-position (not inside the scroll container, which would clip a
  // left-anchored popover) so it appears instantly and fully. `top` is the button's vertical centre.
  const [hover, setHover] = useState<{ id: string; top: number; right: number } | null>(null)

  return (
    <div className="flex h-full w-full flex-col items-center gap-2 bg-surface py-3">
      <button
        onClick={onExpand}
        title="Expand chat"
        aria-label="Expand chat"
        className="rounded-md p-1.5 text-accent-text transition-colors hover:bg-accent/15"
      >
        <PanelRightOpen size={18} />
      </button>

      {/* dev / core mode toggle — vertical segmented; re-scopes the session list below. */}
      <div className="flex flex-col gap-0.5 rounded-md bg-hover p-0.5">
        {(['dev', 'core'] as ChatMode[]).map((m) => (
          <button
            key={m}
            onClick={() => onModeChange(m)}
            title={`${m} chat`}
            className={`rounded px-1.5 py-1 text-[9px] font-medium uppercase tracking-wide transition-colors ${
              mode === m ? 'bg-surface text-fg' : 'text-muted hover:text-fg'
            }`}
          >
            {m}
          </button>
        ))}
      </div>

      <div className="h-px w-6 shrink-0 bg-line" />

      <button
        onClick={onNewChat}
        title="New chat"
        aria-label="New chat"
        className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-line bg-surface text-muted transition-colors hover:border-accent hover:text-accent-text"
      >
        <Plus size={15} />
      </button>

      {/* recent sessions for this repo + mode — click opens the session AND expands the panel. */}
      <div className="flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto pt-0.5">
        {recent.map((s) => {
          const cat = sessionCategory(s)
          const on = s.id === activeId
          return (
            <div key={s.id} className="shrink-0">
              <button
                onClick={() => onOpenSession(s.id)}
                onMouseEnter={(e) => {
                  const r = e.currentTarget.getBoundingClientRect()
                  setHover({ id: s.id, top: r.top + r.height / 2, right: window.innerWidth - r.left })
                }}
                onMouseLeave={() => setHover((h) => (h?.id === s.id ? null : h))}
                aria-label={s.title}
                className={`grid h-8 w-8 place-items-center rounded-md border transition-colors ${
                  on ? 'border-accent bg-accent/10' : 'border-transparent hover:bg-hover'
                }`}
              >
                <cat.Icon size={15} className={cat.color} />
              </button>
            </div>
          )
        })}
      </div>

      {/* Fixed-position hover tooltip — shows the (full) session title the icon can't. Rendered
          outside the scroll container so it never gets clipped and appears instantly. */}
      {hover &&
        (() => {
          const s = recent.find((x) => x.id === hover.id)
          if (!s) return null
          const cat = sessionCategory(s)
          return (
            <div
              style={{ top: hover.top, right: hover.right + 8 }}
              className="pointer-events-none fixed z-50 flex max-w-[240px] -translate-y-1/2 items-center gap-1.5 whitespace-nowrap rounded-md border border-line bg-surface px-2 py-1 text-[11px] shadow-lg"
            >
              <cat.Icon size={11} className={`shrink-0 ${cat.color}`} />
              <span className="truncate text-fg">{s.title}</span>
            </div>
          )
        })()}
    </div>
  )
}
