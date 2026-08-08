import { useEffect, useMemo, useRef, useState } from 'react'
import { Bot, User, ShieldCheck } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import { getWorkItemTimeline, getDevLog, type WorkItemTimeline } from '@/lib/api/dev'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import type { TimelineFrame } from './hooks/useAgentSocket'
import { useStickyScroll } from './useStickyScroll'

// F2 — the unified work-item timeline: a READ-ONLY mirror of every phase agent's turns
// (triage · plan · build · vet · review · close) in one chronological scroll, with handover
// dividers and speaker attribution (owner · agent · deputy). History loads from the timeline
// endpoint; live build/vet frames stream in via the socket's `watch` channel and append to the
// item's CURRENT phase lane (run-lock ⇒ one live run per item, so no phase ambiguity). The input
// box lives in the Composer and routes to the intake session — this view never sends anything.

const PHASE_LABEL: Record<string, string> = {
  triage: 'Triage', plan: 'Plan', build: 'Build', vet: 'Vet', review: 'Review', close: 'Close',
}

// The present-participle a running phase shows in the live indicator ("Building…").
const PHASE_VERB: Record<string, string> = {
  triage: 'Triaging', plan: 'Planning', build: 'Building', vet: 'Vetting', review: 'Reviewing',
  close: 'Closing',
}

type Speaker = 'you' | 'agent' | 'deputy'

// A phase run's closing card — the `user` half of `report_completion`, verbatim. The kernel routes
// on `machine`; this is the half written FOR the owner, so the renderer shapes nothing: it lays out
// the fields it was given and stops. Adding a field to the tool's `user` group is the only way to
// add a line here (renovation §3.2 — one structured source of truth).
type Report = {
  outcome: string
  summary: string
  next: string
  questions: { question: string; recommend?: string; why?: string; instead?: string }[]
}

type Bubble = {
  key: string
  speaker: Speaker
  phase: string | null
  text: string
  live?: boolean
  run?: number | null     // which run said it — the attribution header prints once per (speaker, run)
  ts?: string             // event time — orders reports against turns
  report?: Report         // set ⇒ this entry renders as the closing card, not a speech bubble
}

// One run's `feature` → who is speaking for its reply events.
function replySpeaker(feature: string | null | undefined): Speaker {
  if (feature === 'deputy') return 'deputy'
  return 'agent' // chat (interactive owner turn) + every phase agent reply is the agent talking
}

const SPEAKER_META: Record<Speaker, { label: string; Icon: typeof Bot; tint: string; bubble: string }> = {
  you: { label: 'You', Icon: User, tint: 'text-fg', bubble: 'bg-[--chat-accent]/10 border-[--chat-accent]/25' },
  agent: { label: 'superme', Icon: Bot, tint: 'text-accent-text', bubble: 'bg-surface border-line' },
  deputy: { label: 'Deputy', Icon: ShieldCheck, tint: 'text-deputy', bubble: 'bg-deputy/10 border-deputy/30' },
}

// Outcome → glance colour + owner-facing label. The schema value is a routing token; the owner
// reads a phrase. This map is the ONLY shaping the card does.
const OUTCOME_TONE: Record<string, { dot: string; text: string; label: string }> = {
  success: { dot: 'bg-success', text: 'text-success', label: 'done' },
  partial: { dot: 'bg-warn', text: 'text-warn', label: 'partly done' },
  clean_noop: { dot: 'bg-line', text: 'text-muted', label: 'nothing to do' },
  blocked: { dot: 'bg-warn', text: 'text-warn', label: 'blocked' },
  needs_user: { dot: 'bg-warn', text: 'text-warn', label: 'needs you' },
  split: { dot: 'bg-accent', text: 'text-accent-text', label: 'splitting' },
  revise: { dot: 'bg-accent', text: 'text-accent-text', label: 'back to plan' },
  exhausted: { dot: 'bg-danger', text: 'text-danger', label: 'out of budget' },
  stagnated: { dot: 'bg-danger', text: 'text-danger', label: 'no progress' },
}

function ReportCard({ r }: { r: Report }) {
  const t = OUTCOME_TONE[r.outcome] ?? { dot: 'bg-line', text: 'text-muted', label: r.outcome }
  return (
    <div className="rounded-lg border border-line bg-surface px-2.5 py-2">
      <div className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${t.dot}`} />
        <span className={`text-[11px] font-semibold uppercase tracking-wide ${t.text}`}>{t.label}</span>
      </div>
      <div className="mt-1 text-[12.5px] text-fg">{r.summary}</div>
      <div className="mt-0.5 text-[12px] text-muted"><span className="text-faint">next · </span>{r.next}</div>
      {/* needs_user only — each question carries its own recommendation, so the owner can settle the
          round by accepting rather than reconstructing what was asked. */}
      {r.questions.length > 0 && (
        <ol className="mt-1.5 list-decimal space-y-1.5 pl-4 text-[12px] leading-snug">
          {r.questions.map((q, i) => (
            <li key={i} className="text-fg">
              <span className="font-medium">{q.question}</span>
              {q.recommend && <span className="mt-0.5 block text-muted"><span className="text-fg">Recommend</span> — {q.recommend}</span>}
              {q.why && <span className="block text-muted">Why — {q.why}</span>}
              {q.instead && <span className="block text-muted">Instead — {q.instead}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

export default function TimelineView({
  itemId, contextId, refreshKey, running, currentPhase, liveFrames, interactiveLive,
  busy, runFeature,
}: {
  itemId: string
  contextId: string
  refreshKey: number              // parent bumps this at run boundaries → re-fetch authoritative history
  running: boolean
  currentPhase: string | null
  liveFrames: TimelineFrame[]     // socket `timeline` frames since the last history load (parent clears on refresh)
  interactiveLive: string         // the in-progress interactive turn's streaming text (intake)
  busy: boolean                   // an interactive (owner-fired) turn is in flight — drives the "Thinking…" verb
  runFeature: string | null       // the live run's role (triage/…/deputy) → the phase-specific verb
}) {
  const [data, setData] = useState<WorkItemTimeline | null>(null)
  const [err, setErr] = useState<string | null>(null)

  // The deputy's channel presence (renovation §2.1) — TWO things, and only these two:
  //   `queries` — the exact text of each real turn it fired AT the agent (a send-back). Those are
  //               genuine transcript, so the matching prompt event renders as a deputy bubble.
  //   `says`    — its DECISIONS (approve/escalate), one headline each, from `meta.speech`.
  // Both are dev-log events, not run replies. Its internal judging turn — the "[deputy] judging
  // the review gate" prompt and its own reasoning — is NEVER chat: it is a private read the owner
  // inspects in the Deputy tab, and rendering it here would put the deputy's thinking in the middle
  // of a conversation it was never part of. Decisions are windowed to the current gate (since the
  // last `phase.advance`) so earlier rounds don't accumulate.
  //
  // All three are derived from ONE dev-log read — the same cache key the drilldown and the chat
  // rail subscribe to, so the item's log is fetched once for all of them instead of three times on
  // three clocks. The dev log is a DIFFERENT table from the run_event trail this view renders, so
  // reading it here can never double-render the live frames.
  const log = useLive(K.devLog(contextId, itemId, 50), () => getDevLog(contextId, { itemId, limit: 50 }), 10000)
  const logEvents = log.data?.events ?? []

  const deputyQueries = useMemo(
    () => logEvents
      .filter((e) => String(e.kind) === 'deputy.query')
      .map((e) => String(e.meta?.text ?? '').trim())
      .filter(Boolean),
    [logEvents],
  )
  // Every decision the deputy made on this item, each carrying the gate it decided and the moment
  // it decided — so the timeline can put it WHERE IT HAPPENED. It is a real event with a real time
  // and a real subject, and the two things that were wrong both followed from pretending otherwise
  // (owner, 2026-08-03):
  //   · it was labelled with the item's CURRENT phase, so the triage approval read "Deputy · Plan";
  //   · it was appended after everything else, so it pinned itself to the bottom and each new turn
  //     slid in above it — the thread ran backwards around that one bubble.
  // Windowing it to "the current gate" was the third mistake: three approvals happened, and the
  // owner saw one. A decision is history, and history does not expire.
  const deputyDecisions = useMemo(
    () => logEvents
      .filter((e) => String(e.kind) === 'deputy.approve' || String(e.kind) === 'deputy.escalate')
      .map((e) => ({
        ts: String(e.created_at ?? ''),
        phase: (e.meta?.gate ? String(e.meta.gate) : null),  // the gate it judged, not where we are now
        text: String(e.meta?.speech ?? '').trim(),
      }))
      .filter((d) => d.text)
      .reverse(),                            // the log is newest-first; the timeline reads oldest-first
    [logEvents],
  )
  // Every phase run's closing card, oldest-first, from the `run.report` event's `user` payload.
  // `meta` IS the report_completion payload (runs.read_completion / ws stores it whole) plus the
  // kernel's `run_id` stamp, so the card reads `user` straight off it — no parsing, no reshaping.
  const reports = useMemo(() => {
    // A report is written at its run's very END, so a row logged before the stamp existed belongs
    // to the newest run that had already started when it landed. Oldest-first, so the last match wins.
    const starts = (data?.runs ?? [])
      .filter((r) => r.started_at)
      .map((r) => ({ id: r.run_id, at: String(r.started_at) }))
      .sort((a, b) => (a.at < b.at ? -1 : 1))
    return logEvents
      .filter((e) => String(e.kind) === 'run.report')
      .map((e) => {
        const m = (e.meta ?? {}) as Record<string, any>
        const u = (m.user ?? {}) as Record<string, any>
        const ts = String(e.created_at ?? '')
        let run: number | null = typeof m.run_id === 'number' ? m.run_id : null
        if (run == null) for (const s of starts) if (s.at <= ts) run = s.id
        return {
          ts,
          run,                                 // which run this card ENDS
          report: {
            outcome: String(m.outcome ?? ''),
            summary: String(u.summary ?? m.summary ?? ''),
            next: String(u.next ?? m.next ?? ''),
            questions: Array.isArray(u.questions) ? u.questions : [],
          } as Report,
        }
      })
      .filter((r) => r.report.summary)
      .reverse()                             // the log is newest-first; the timeline reads oldest-first
  }, [logEvents, data])

  // Load authoritative history on open and whenever the parent bumps `refreshKey` (a run just
  // ended → its events are now in the trail). Liveness BETWEEN refreshes comes from `liveFrames`
  // (broker), so we never poll here — that would double-render the run_event rows capture writes live.
  useEffect(() => {
    let alive = true
    getWorkItemTimeline(itemId, contextId)
      .then((d) => { if (alive) { setData(d); setErr(null) } })
      .catch((e) => { if (alive) setErr(String(e)) })
    return () => { alive = false }
  }, [itemId, contextId, refreshKey])

  // Flatten runs → bubbles, tracking phase changes for handover dividers. Prompts show only for
  // interactive owner turns (a phase run's "prompt" is the internal kernel trigger — noise here).
  const { bubbles, toolCounts } = useMemo(() => {
    const out: Bubble[] = []
    const counts: Record<number, number> = {}
    for (const run of data?.runs ?? []) {
      // The deputy's JUDGING run is not a conversation (§2.1): its prompt is a kernel string and
      // its reply is private reasoning about work it wasn't part of. Skipped whole — what the
      // deputy says in the channel is its send-back turns and its decision headlines, both below.
      if (run.feature === 'deputy') continue
      // `report_completion` IS the run's closing statement — the owner reads that card as its last
      // word, so anything the agent types after the call is a restatement of it. The call sits in
      // the trail, so the cut is exact and works on runs logged before any of this existed. (Time
      // can't do this job: the run.report event is written at run END, i.e. after the extra line.)
      // The full text stays in the run trail either way — that is a log. Interactive turns are
      // exempt: there a person is in the room, so a reply after the report is a reply to them.
      let ended = false
      let tools = 0
      for (const ev of run.events ?? []) {
        if (ev.kind === 'mcp' && (ev.description || '').includes('report_completion')
            && run.feature !== 'chat') ended = true
        if (ev.kind === 'reply') {
          if (ended) continue
          out.push({ key: `r${run.run_id}-${ev.seq}`, speaker: replySpeaker(run.feature),
                     phase: run.phase ?? null, run: run.run_id ?? null,
                     text: ev.description || '', ts: ev.created_at })
        } else if (ev.kind === 'prompt') {
          // A phase run's prompt is the internal kernel trigger — noise here. The exceptions are
          // the two prompts a PERSON authored: the owner's own interactive turn, and a deputy
          // send-back (matched against its `deputy.query` marker — the agent got it as a plain
          // user turn and couldn't tell the sender; only the owner's view distinguishes them).
          const text = ev.description || ''
          const fromDeputy = deputyQueries.includes(text.trim())
          if (run.feature === 'chat' || fromDeputy) {
            out.push({ key: `p${run.run_id}-${ev.seq}`, speaker: fromDeputy ? 'deputy' : 'you',
                       phase: run.phase ?? null, run: run.run_id ?? null, text, ts: ev.created_at })
          }
        } else {
          tools += 1
        }
      }
      counts[run.run_id] = tools
    }
    return { bubbles: out, toolCounts: counts }
  }, [data, deputyQueries])

  // Live frames (background build/vet) → append to the CURRENT phase lane. Reply frames become
  // bubbles; tool frames just bump a live tool count shown on the running indicator. Dedup against
  // loaded history: the parent re-fetches the authoritative trail on a heartbeat, so a reply already
  // in `data` must be suppressed here or it renders twice. We skip the FIRST N live reply frames per
  // run, where N is how many replies history already has for that run — so genuinely newer frames
  // (streamed since the last fetch) still show instantly while settled ones defer to history.
  const historyReplies: Record<number, number> = {}
  for (const run of data?.runs ?? []) {
    const rid = run.run_id ?? -1
    historyReplies[rid] = (run.events ?? []).filter((e) => e.kind === 'reply').length
  }
  const liveBubbles: Bubble[] = []
  const seenPerRun: Record<number, number> = {}
  let liveTools = 0
  for (let i = 0; i < liveFrames.length; i++) {
    const f = liveFrames[i]
    // Same rule live: while the deputy judges, the indicator says "Deputy reviewing" and nothing
    // else — its streaming reply is its private read, not a turn in this thread.
    if (runFeature === 'deputy' && f.kind === 'reply') continue
    if (f.kind === 'reply') {
      const rid = f.run_id ?? -1
      const already = historyReplies[rid] ?? 0
      const seen = seenPerRun[rid] ?? 0
      seenPerRun[rid] = seen + 1
      if (seen < already) continue // this reply is already rendered from history
      liveBubbles.push({ key: `live-${i}`, speaker: 'agent', phase: currentPhase, run: rid,
                        text: f.description || '', live: true })
    } else {
      liveTools += 1
    }
  }
  // The interactive (owner-fired) turn streams separately — show it as a live agent bubble at the end.
  if (interactiveLive.trim()) {
    liveBubbles.push({ key: 'interactive-live', speaker: 'agent', phase: currentPhase, text: interactiveLive, live: true })
  }

  // Everything the dev-event log contributes to the thread: each run's closing card, and each
  // deputy decision. Neither is a turn in the run trail, so `created_at` is the only thing that
  // relates them to the turns — one sorted list, then one stable merge below. A report is the LAST
  // thing its run said, so it lands after that run's turns; a decision lands at the gate it closed.
  const timed: Bubble[] = [
    ...reports.map((r, i) => ({ key: `rep-${i}`, speaker: 'agent' as const, phase: null,
                                run: r.run, text: '', ts: r.ts, report: r.report })),
    ...deputyDecisions.map((d, i) => ({ key: `dep-${i}`, speaker: 'deputy' as const,
                                        phase: d.phase, text: d.text, ts: d.ts })),
  ].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0))

  // A report carries no phase of its own — it inherits the lane it closes. A decision names its own
  // gate and keeps it.
  const settled: Bubble[] = []
  const place = (b: Bubble) => settled.push(
    b.phase ? b : { ...b, phase: settled[settled.length - 1]?.phase ?? null })
  let ri = 0
  for (const b of bubbles) {
    while (ri < timed.length && timed[ri].ts && b.ts && timed[ri].ts! <= b.ts) place(timed[ri++])
    settled.push(b)
  }
  for (; ri < timed.length; ri++) place(timed[ri])
  const all = [...settled, ...liveBubbles]

  // Same scroll rule as the chat rail: follow the newest only while the owner is reading the bottom.
  const { scrollRef, onScroll } = useStickyScroll([all.length, interactiveLive])

  return (
    <div ref={scrollRef} onScroll={onScroll} className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-3 py-3">
      {err && !data && <div className="px-1 text-[12px] text-danger">Couldn’t load the timeline — {err}</div>}
      {/* Two different emptinesses, and saying the wrong one contradicts the spinner directly
          below this line. A run IS in flight → the first turn simply hasn't landed yet (a run's
          opening frames are a prompt and a skill launch, neither of which is a bubble). Only a
          genuinely idle item has "agents haven't run". Caught by the owner, 2026-08-03: the rail
          read "this item's agents haven't run" while the card beside it said TRIAGING…. */}
      {data && all.length === 0 && (
        <div className="px-1 pt-4 text-center text-[12px] text-faint">
          {running ? 'Starting — the first turn will appear here.'
                   : 'No turns yet — this item’s agents haven’t run.'}
        </div>
      )}
      {all.map((b, i) => {
        const prev = i > 0 ? all[i - 1] : null
        const showDivider = b.phase && b.phase !== (prev?.phase ?? null)
        const sm = SPEAKER_META[b.speaker]
        // ONE attribution per speaker per run (owner, 2026-08-02). A phase agent says four things
        // in a row and they are one turn of one speaker — repeating "superme" above each made the
        // column read as four separate arrivals. The header reappears when the speaker changes, the
        // run changes, or a phase divider has just re-set the context.
        const lead = showDivider || !prev || prev.speaker !== b.speaker || (prev.run ?? null) !== (b.run ?? null)
        return (
          <div key={b.key} className={lead ? '' : '-mt-1'}>
            {showDivider && (
              <div className="my-2 flex items-center gap-2 text-[10px] font-medium uppercase tracking-wide text-faint">
                <span className="h-px flex-1 bg-line" />
                {PHASE_LABEL[b.phase!] ?? b.phase} phase
                <span className="h-px flex-1 bg-line" />
              </div>
            )}
            <div className="flex gap-2">
              {/* The icon gutter is held even on a continuation row, so every bubble in a run stays
                  on one left edge instead of stepping out when its header is dropped. */}
              <div className={`mt-0.5 shrink-0 ${sm.tint}`} title={sm.label}>
                {lead ? <sm.Icon size={14} /> : <span className="block h-[14px] w-[14px]" />}
              </div>
              <div className="min-w-0 flex-1">
                {lead && (
                  <div className="mb-0.5 flex items-center gap-1.5 text-[10px] text-faint">
                    <span className={sm.tint}>{sm.label}</span>
                    {b.phase && <span>· {PHASE_LABEL[b.phase] ?? b.phase}</span>}
                  </div>
                )}
                {b.report
                  ? <ReportCard r={b.report} />
                  : (
                    <div className={`rounded-lg border px-2.5 py-1.5 text-[12.5px] text-fg ${sm.bubble}`}>
                      <Markdown text={b.text} variant="chat" tone="dev" />
                    </div>
                  )}
              </div>
            </div>
          </div>
        )
      })}

      {/* The live incoming indicator — a gently blinking phase verb, no elapsed (the work-item card
          already owns the live timer; a second one here just duplicates it and ticks a hair off).
          Covers both a background phase run (`running` — Building… / Vetting… / Deputy reviewing…)
          and an interactive owner turn (`busy` — Thinking… / Responding…). */}
      {(running || busy) && (() => {
        const verb = running
          ? (runFeature === 'deputy' ? 'Deputy reviewing' : PHASE_VERB[currentPhase ?? ''] ?? 'Working')
          : interactiveLive.trim() ? 'Responding' : 'Thinking'
        return (
          <div className="flex items-center gap-1.5 px-1 pt-0.5 text-[11px] text-muted">
            <span className="flex gap-0.5">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--chat-accent)] [animation-delay:-0.3s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--chat-accent)] [animation-delay:-0.15s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--chat-accent)]" />
            </span>
            <span className="animate-pulse text-accent-text">{verb}…</span>
          </div>
        )
      })()}
    </div>
  )
}
