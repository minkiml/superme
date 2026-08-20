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
import type { Msg } from './types'

// F2: the phases whose session is the item's own worker (not the intake thread the owner talks in).
// While the item sits in one of these, the chat input is greyed — there's no live intake worker to
// receive a message; the owner watches the build/vet stream read-only.
const AUTONOMOUS_PHASES = new Set(['build', 'vet'])

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
  // F2 unified timeline state (only used when bound to a work-item).
  const [liveFrames, setLiveFrames] = useState<TimelineFrame[]>([])
  const [timelineKey, setTimelineKey] = useState(0)   // parent bumps → TimelineView re-fetches history
  const [boundPhase, setBoundPhase] = useState<string | null>(null)
  const [boundRunning, setBoundRunning] = useState(false)
  // Terminal = the item is finished (done / abandoned / superseded). Its phase threads were
  // reclaimed at clearance, so there is no conversation left to continue — see the composer lock.
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
        // A bound turn's session belongs to the work-item, not the context's general chat —
        // claim it transiently (persist=false) so it doesn't overwrite the remembered general
        // session; the item owns it (surfaced up + persisted onto the work-item below).
        sessions.claimSession(sessionId, !binding)
        if (binding && sessionId !== binding.sessionId) onBindingSession?.(sessionId)
      }
      // An interactive intake turn just finished — reload the timeline so its settled events land.
      if (binding) setTimelineKey((k) => k + 1)
    },
    onError: (message) => sessions.appendMessage({ role: 'system', text: message }),
    // F2: a watched item's live background run event → buffer it for the timeline view.
    onTimeline: (f) => setLiveFrames((prev) => [...prev, f]),
  })

  // The work-item this chat is actually on — derived from the ACTIVE SESSION's durable stamp (server
  // truth, work-item-session-recognition-prd), so the indicator is correct however the session was
  // opened (work-item card OR picker) and clears when you switch to a general session. `binding` is
  // only an OPTIMISTIC fallback for a just-clicked card whose session isn't listed yet (or an item
  // that has no session at all yet).
  // Matched against the channel's THREADS, not just its row id: a work-item row stands for one
  // thread per phase, and a finished turn hands back whichever thread the phase named — so an id
  // that is not the row's own is still this same channel, and the timeline must not blink out.
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

  // F2: subscribe this panel to the bound item's live event broker (build/vet/other-phase runs
  // stream in), and drop the subscription when the binding changes/clears.
  useEffect(() => {
    const id = chipItem?.id && mode === 'dev' ? chipItem.id : null
    if (!socket.ready) return
    socket.watch(id)
    setLiveFrames([])           // fresh buffer for the new item
    setTimelineKey((k) => k + 1)
    return () => socket.watch(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chipItem?.id, mode, socket.ready])

  // F2: track the bound item's phase + running so the timeline attributes live frames to the right
  // lane and the composer greys during build/vet. On a run-end (running true→false) reload the
  // authoritative history and drop the reconciled live-frame tail.
  // Shares the drilldown's `itemDetail` key — with the modal open over the same item this is one
  // request between them, not two on different clocks. Cadence follows the item: brisk while a run
  // is in flight, slow at rest (it used to poll every 2s forever, including for an idle item).
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
    // Re-pull the authoritative trail on any STRUCTURAL change (phase moved, or a run just ended)
    // AND on a slow heartbeat while running. The timeline endpoint includes in-progress runs, so
    // this lands every phase's bubbles within a couple seconds with no manual refresh — re-fetching
    // ONLY on the running true→false edge let fast back-to-back autopilot runs slip past (build/vet/
    // deputy went missing until a hard refresh). The view dedups liveFrames against loaded history
    // by run, so extra fetches never double-render.
    const prev = prevBound.current
    const structural = phase !== prev.phase || (prev.running && !running)
    prev.ticks += 1
    const heartbeat = running && prev.ticks % 2 === 0
    if (structural) { setTimelineKey((k) => k + 1); setLiveFrames([]) }
    else if (heartbeat) setTimelineKey((k) => k + 1)
    prev.running = running
    prev.phase = phase
    // `detailAt` (the fetch stamp) is the dependency that makes this run once per RESPONSE — the
    // detail object itself is referentially new on every poll whether or not anything changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boundId, detailAt])
  // Reset the edge-detector when the bound item changes — otherwise the new item's first read is
  // compared against the previous item's phase and spuriously reads as a structural change.
  useEffect(() => { prevBound.current = { running: false, phase: null, ticks: 0 } }, [boundId])

  // Deputy turns woven into the bound work-item thread (autopilot slice 4b). The deputy acts in the
  // BACKGROUND (headless, at gates), so its words can't arrive over the live socket — they're
  // persisted as `deputy.*` dev events and replayed here whenever the owner opens the item's chat.
  // Appended after the session bubbles (the deputy always acts at the item's CURRENT gate, i.e. its
  // latest state), each an attributed `deputy` bubble. Poll-refreshed so a decision made while the
  // panel is open still surfaces. NOT merged into the SDK transcript — kept independent, exactly as
  // the deputy session is (design §"Interface": stored attribution, never inferred).
  // The deputy's channel presence is ONLY its real turns AT the agent (Q1). Its governance moves —
  // approve / send-back / escalate and their rationale — belong in the Deputy-log drilldown
  // (WorkItemModal), OUT of the channel (owner's Q1 spec), so the chat stays a clean 3-speaker
  // conversation. Here we fetch just the `deputy.query` markers: the exact text of each turn the
  // deputy fired at the agent, so the matching transcript user-bubble can be re-attributed (Q1-D).
  // Same dev-log key the timeline and the drilldown read — one fetch feeds all three.
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

  // Re-attribute the deputy's real turns (Q1-D): a transcript bubble the owner appears to have sent
  // that exactly matches a `deputy.query` marker was actually the DEPUTY talking to the agent on the
  // owner's behalf — render it as the deputy (3 speakers), not as the owner. The agent never saw a
  // difference; only the owner's view distinguishes them.
  const displayMessages = deputyQueries.length
    ? sessions.messages.map((m) =>
        m.role === 'you' && deputyQueries.includes((m.text ?? '').trim())
          ? { ...m, role: 'deputy' as const }
          : m)
    : sessions.messages

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
      // The chat rail's accent follows the mode: core = mint (pastel green), dev = blue. It holds a
      // whole `rgb(...)` string, so a translucent variant cannot be derived at the point of use —
      // Tailwind cannot decompose one to apply `/10`. Both bubble rails read it at full strength.
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

      {/* F2: bound to a work-item → the unified live timeline (all phases, read-only mirror).
          A general chat → the normal session transcript. */}
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
        // F2: grey the input while the item is in an autonomous phase (build/vet) — no live intake
        // worker to receive it; the owner watches the stream and speaks again at review.
        // A session outlives its work-item only when the folder left out of band (a reset, a hand
        // delete) — the startup reconciler retires those, but one can vanish while the daemon is
        // up. The transcript still reads: it happened. Sending does not, because a resumed thread
        // would answer about work that no longer exists, and the box being live is the whole
        // reason that reads as confusion rather than as history.
        // A TERMINAL item's transcript is worth opening — it is how the work went — but there is
        // nothing left to send INTO. Clearance reclaimed its phase threads at close, so binding a
        // shipped item lands on `newChat` (see the binding effect): the box looks live, and typing
        // in it would mint a brand-new thread against finished work, carrying none of the history
        // shown directly above it. Read-only is the honest state.
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
