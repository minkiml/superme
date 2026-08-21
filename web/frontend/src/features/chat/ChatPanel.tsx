import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Hammer, Plus, PanelRightOpen } from 'lucide-react'
import ChatHeader from './ChatHeader'
import MessageList from './MessageList'
import TimelineView from './TimelineView'
import ApprovalCard from './ApprovalCard'
import Composer from './Composer'
import SessionDrawer from './SessionDrawer'
import { sessionCategory } from './sessionCategory'
import ConfirmDialog from '@/ui/ConfirmDialog'
import { useAgentSocket, type TimelineFrame } from './hooks/useAgentSocket'
import { useSessions } from './hooks/useSessions'
import { GLOBAL, type ContextRef } from '@/lib/contexts'
import { getRuns, type ChatMode, type SessionMeta } from '@/lib/api'
import { getDevLog, getWorkItemDetail, type WorkItemDetail } from '@/lib/api/dev'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'

// The phases whose session is the item's own worker, not the intake thread. The input greys here.
const AUTONOMOUS_PHASES = new Set(['build', 'vet'])

// A dev-mode binding: the chat is taken over as one work-item's dev thread.
export type DevBinding = { workItemId: string; sessionId: string | null; title: string; contextId: string }
// A one-shot turn that BIRTHS a fresh session, never inheriting the open one. `kind` and
// `subjectRunId` stamp its identity.
export type SeedTurn = { prompt: string; kind?: string; subjectRunId?: number }

// The persistent chat rail, and just the conductor: `useSessions` owns the conversation list,
// `useAgentSocket` the live turn.
//
// The parent remounts it via a `key` on context change, so the hooks below capture `contextId`
// once.
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
  // The composer picker's runtime override, sent per turn and NEVER written as a persisted default.
  // `null` follows the server precedence.
  const [sessionModel, setSessionModel] = useState<string | null>(null)
  const [sessionEffort, setSessionEffort] = useState<string | null>(null)
  // Unified timeline state (only used when bound to a work-item).
  const [liveFrames, setLiveFrames] = useState<TimelineFrame[]>([])
  const [timelineKey, setTimelineKey] = useState(0)   // parent bumps → TimelineView re-fetches history
  const [boundPhase, setBoundPhase] = useState<string | null>(null)
  const [boundRunning, setBoundRunning] = useState(false)
  // A terminal item's phase threads were reclaimed at clearance, so there is no conversation left
  // to continue.
  const [boundTerminal, setBoundTerminal] = useState(false)
  const [runFeature, setRunFeature] = useState<string | null>(null) // the live run's role → the chat verb (Building… / Deputy reviewing…)
  // Edge-detector for the bound item's detail feed (phase moved / run ended / heartbeat).
  const prevBound = useRef<{ running: boolean; phase: string | null; ticks: number }>({
    running: false, phase: null, ticks: 0,
  })

  const sessions = useSessions(contextId, mode)
  const socket = useAgentSocket(contextId, mode, {
    onResult: (text, sessionId) => {
      sessions.appendMessage({ role: 'superme', text })
      if (sessionId) {
        // A bound turn's session belongs to the work-item, so claim it transiently — persisting
        // would overwrite the general session.
        sessions.claimSession(sessionId, !binding)
        if (binding && sessionId !== binding.sessionId) onBindingSession?.(sessionId)
      }
      // An interactive intake turn just finished — reload the timeline so its settled events land.
      if (binding) setTimelineKey((k) => k + 1)
    },
    onError: (message) => sessions.appendMessage({ role: 'system', text: message }),
    // A watched item's live background run event → buffer it for the timeline view.
    onTimeline: (f) => setLiveFrames((prev) => [...prev, f]),
  })

  // Derived from the ACTIVE SESSION's durable stamp, and matched against the channel's THREADS, not
  // just its row id.
  const activeSess = sessions.sessions.find(
    (s) => s.id === sessions.activeId || (s.thread_ids ?? []).includes(sessions.activeId ?? ''),
  )
  const stampedItem = activeSess?.item_id
    ? { id: activeSess.item_id, title: activeSess.item_title || activeSess.item_id }
    : null
  const optimisticItem =
    binding && (binding.sessionId === sessions.activeId || (binding.sessionId == null && sessions.activeId == null))
      ? { id: binding.workItemId, title: binding.title }
      : null
  const chipItem = stampedItem ?? optimisticItem

  // Subscribe this panel to the bound item's live event broker, and drop the subscription when the
  // binding clears.
  useEffect(() => {
    const id = chipItem?.id && mode === 'dev' ? chipItem.id : null
    if (!socket.ready) return
    socket.watch(id)
    setLiveFrames([])           // fresh buffer for the new item
    setTimelineKey((k) => k + 1)
    return () => socket.watch(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chipItem?.id, mode, socket.ready])

  // Track the bound item's phase and running state, so the timeline attributes live frames to the
  // right lane.
  const boundId = mode === 'dev' ? chipItem?.id ?? null : null
  const detailQ = useLive<WorkItemDetail>(
    boundId ? K.itemDetail(contextId, boundId) : null,
    () => getWorkItemDetail(boundId as string, contextId),
    boundRunning ? 2500 : 10000,
  )
  const boundDetail = detailQ.data
  const detailAt = detailQ.fetchedAt

  useEffect(() => {
    if (!boundId) {
      setBoundPhase(null); setBoundRunning(false); setRunFeature(null); setBoundTerminal(false)
      return
    }
    if (!boundDetail) return
    const running = !!boundDetail.item.running
    const phase = boundDetail.item.phase ?? null
    setBoundPhase(phase)
    setBoundRunning(running)
    setBoundTerminal(boundDetail.item.status === 'done' || !!boundDetail.item.done_at)
    setRunFeature(running ? boundDetail.item.run_feature ?? null : null)
    // Also on a slow heartbeat, because fast back-to-back runs slip past a running-edge trigger.
    const prev = prevBound.current
    const structural = phase !== prev.phase || (prev.running && !running)
    prev.ticks += 1
    const heartbeat = running && prev.ticks % 2 === 0
    if (structural) { setTimelineKey((k) => k + 1); setLiveFrames([]) }
    else if (heartbeat) setTimelineKey((k) => k + 1)
    prev.running = running
    prev.phase = phase
    // `detailAt` makes this run once per RESPONSE; the detail object is referentially new on every
    // poll. eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boundId, detailAt])
  // Reset the edge-detector on a new item, or its first read is compared against the previous
  // item's phase.
  useEffect(() => { prevBound.current = { running: false, phase: null, ticks: 0 } }, [boundId])

  // The deputy acts headlessly, so its words are replayed from dev events. Only its turns AT the
  // agent belong here.
  const deputyLog = useLive(
    boundId ? K.devLog(contextId, boundId, 50) : null,
    () => getDevLog(contextId, { itemId: boundId as string, limit: 50 }),
    8000,
  )
  const deputyQueries = useMemo(
    () => (deputyLog.data?.events ?? [])
      .filter((e) => String(e.kind) === 'deputy.query')
      .map((e) => String(e.meta?.text ?? '').trim())
      .filter(Boolean),
    [deputyLog.data],
  )

  // A transcript bubble matching a `deputy.query` marker was the DEPUTY talking on the owner's
  // behalf, so render it as such.
  const displayMessages = deputyQueries.length
    ? sessions.messages.map((m) =>
        m.role === 'you' && deputyQueries.includes((m.text ?? '').trim())
          ? { ...m, role: 'deputy' as const }
          : m)
    : sessions.messages

  // A binding the active session has diverged from is stale. Keyed on `activeId` alone, so a fresh
  // click survives.
  useEffect(() => {
    if (binding?.sessionId && binding.sessionId !== sessions.activeId) onUnbind?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions.activeId])

  // Open the item's own thread TRANSIENTLY, so it does not clobber the context's general session.
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
      // If the rail is already on a fresh empty chat, keep it: resuming would clobber the new one.
      sessions.resumeStored()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [binding?.workItemId])

  // Seed the header from the session's LAST recorded run. Only overwrites on a found run, never
  // clears.
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
    // A card binding also tags the work-item, so the daemon persists the session to it.
    if (!socket.send(text, sessions.sessionRef.current,
                     { mode, workItemId: binding?.workItemId, model: sessionModel, effort: sessionEffort })) return
    sessions.appendMessage({ role: 'you', text })
    setInput('')
  }

  // A workflow launch BIRTHS ITS OWN session: inheriting a work-item's would run onboarding bound
  // to that item.
  const seededRef = useRef(false)
  useEffect(() => {
    if (!seed) { seededRef.current = false; return }
    if (!socket.ready || seededRef.current) return
    seededRef.current = true
    if (binding) onUnbind?.()          // drop any work-item binding before the fresh workflow session
    sessions.newChat()                 // clear the rail to a new session
    socket.clearStream()
    socket.clearMeta()
    // These ride the birth turn, and the daemon stamps them write-once, so the session keeps its
    // identity on resume.
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
    // A New chat is always a fresh GENERAL session, or its turns get mis-tagged to the bound item.
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

  // Collapsed, this is an alternate render of the same conductor: recent sessions as chips, a mode
  // toggle, no repo switcher.
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
      // It holds a whole `rgb(...)` string, so a translucent variant cannot be derived at the point
      // of use.
      style={{
        ['--chat-accent' as string]: mode === 'core' ? 'rgb(var(--c-core))' : 'rgb(var(--c-dev))',
      } as CSSProperties}
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

      {/* bound to a work-item, so the unified live timeline; a general chat gets the session
      {   transcript */}
      {chipItem ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <TimelineView
            itemId={chipItem.id}
            contextId={contextId}
            refreshKey={timelineKey}
            running={boundRunning}
            currentPhase={boundPhase}
            liveFrames={liveFrames}
            interactiveLive={socket.live}
            busy={socket.busy}
            runFeature={runFeature}
          />
          {socket.approval && (
            <div className="shrink-0 px-3 pb-1">
              <ApprovalCard approval={socket.approval} onAnswer={socket.answer} />
            </div>
          )}
        </div>
      ) : (
        <MessageList
          messages={displayMessages}
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
      )}

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
        // Read-only whenever there is nothing to send into: an autonomous phase, a vanished item,
        // or a terminal one.
        locked={
          boundTerminal
            ? { reason: 'This work-item and its sessions are closed — the transcript is history.' }
            : activeSess?.item_gone
            ? { reason: 'This work-item no longer exists — the conversation is history now.' }
            : chipItem && boundPhase && AUTONOMOUS_PHASES.has(boundPhase)
            ? { reason: `The ${boundPhase} agent is working — it'll report at review.` }
            : null
        }
        onSelectModel={(model, effort) => {
          // A pure FE state change, sent on the next turn's frame. `reset` clears back to the
          // server default.
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

// The collapsed chat rail — a slim quick-switcher over the repo the rail points at.
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
  // The collapsed rail is a quick-switcher for what is live now, not a full history; that is the
  // expanded drawer.
  const dayAgo = Date.now() - 24 * 60 * 60 * 1000
  const recent = sessions
    .filter((s) => Date.parse(s.updated_at) >= dayAgo)
    .slice(0, 8)

  // Rendered fixed-position, because the scroll container would clip a left-anchored popover. `top`
  // is the button's centre.
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

      {/* Shows the full session title the icon cannot, rendered outside the scroll container so it
          is never clipped. */}
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
