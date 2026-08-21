import { useEffect, useMemo, useRef, useState } from 'react'
import { Bot, User, ShieldCheck } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import { getWorkItemTimeline, getDevLog, type WorkItemTimeline } from '@/lib/api/dev'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import type { TimelineFrame } from './hooks/useAgentSocket'
import { useStickyScroll } from './useStickyScroll'

// The unified work-item timeline — a READ-ONLY mirror of every phase agent's turns in one
// chronological scroll.
//
// Live frames append to the item's CURRENT phase lane. The input box lives in the Composer; this
// view never sends.

const PHASE_LABEL: Record<string, string> = {
  triage: 'Triage', plan: 'Plan', build: 'Build', vet: 'Vet', review: 'Review', close: 'Close',
}

// The present-participle a running phase shows in the live indicator ("Building…").
const PHASE_VERB: Record<string, string> = {
  triage: 'Triaging', plan: 'Planning', build: 'Building', vet: 'Vetting', review: 'Reviewing',
  close: 'Closing',
}

type Speaker = 'you' | 'agent' | 'deputy'

// The `user` half of `report_completion`, verbatim. The kernel routes on `machine`; this half is
// written FOR the owner.
//
// The renderer shapes nothing: adding a field to the tool's `user` group is the only way to add a
// line here.
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

// The SAME two fills a general session gives them: a bubble must not change appearance depending on
// the thread it is read in.
const SPEAKER_META: Record<Speaker,
  { label: string; Icon: typeof Bot; tint: string; bubble: string; right?: boolean }> = {
  you: { label: 'You', Icon: User, tint: 'text-fg', right: true,
         bubble: 'border-line border-l-2 border-l-[var(--chat-accent)] bg-surface' },
  agent: { label: 'superme', Icon: Bot, tint: 'text-accent-text', bubble: 'bg-hover border-line' },
  deputy: { label: 'Deputy', Icon: ShieldCheck, tint: 'text-deputy', bubble: 'bg-deputy/10 border-deputy/30' },
}

// The schema value is a routing token; the owner reads a phrase. This map is the ONLY shaping the
// card does.
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

// Close's card is the last word on a work-item, so it is labelled for the ITEM, not the run.
//
// Same token, different sentence: the reader is asking a different question at the end than in the
// middle.
const CLOSE_TONE: Record<string, { dot: string; text: string; label: string }> = {
  success: { dot: 'bg-success', text: 'text-success', label: 'work completed' },
  clean_noop: { dot: 'bg-success', text: 'text-success', label: 'work completed · nothing to update' },
}

function ReportCard({ r, phase }: { r: Report; phase: string | null }) {
  const t = (phase === 'close' ? CLOSE_TONE[r.outcome] : undefined)
    ?? OUTCOME_TONE[r.outcome] ?? { dot: 'bg-line', text: 'text-muted', label: r.outcome }
  return (
    <div className="rounded-lg border border-line bg-surface px-2.5 py-2">
      <div className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${t.dot}`} />
        <span className={`text-[11px] font-semibold uppercase tracking-wide ${t.text}`}>{t.label}</span>
      </div>
      <div className="mt-1 text-[12.5px] text-fg">{r.summary}</div>
      <div className="mt-0.5 text-[12px] text-muted"><span className="text-faint">next · </span>{r.next}</div>
      {/* Each question carries its own recommendation, so the owner settles the round by accepting
          it. */}
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

  // Its real turns AT the agent, and its DECISIONS; the judging turn is NEVER chat.
  const log = useLive(K.devLog(contextId, itemId, 50), () => getDevLog(contextId, { itemId, limit: 50 }), 10000)
  const logEvents = log.data?.events ?? []

  const deputyQueries = useMemo(
    () => logEvents
      .filter((e) => String(e.kind) === 'deputy.query')
      .map((e) => String(e.meta?.text ?? '').trim())
      .filter(Boolean),
    [logEvents],
  )
  // Each carries the gate and the moment, so the timeline can put it WHERE IT HAPPENED. A decision
  // is history.
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
  // `meta` IS the `report_completion` payload plus the kernel's run stamp, so the card reads it
  // straight off.
  const reports = useMemo(() => {
    // A report is written at its run's very END, so an unstamped row belongs to the newest run
    // already started.
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

  // Liveness BETWEEN refreshes comes from the broker, so this never polls — that would
  // double-render the live rows.
  useEffect(() => {
    let alive = true
    getWorkItemTimeline(itemId, contextId)
      .then((d) => { if (alive) { setData(d); setErr(null) } })
      .catch((e) => { if (alive) setErr(String(e)) })
    return () => { alive = false }
  }, [itemId, contextId, refreshKey])

  // Flatten runs into bubbles, tracking phase changes for handover dividers.
  const { bubbles, toolCounts } = useMemo(() => {
    const out: Bubble[] = []
    const counts: Record<number, number> = {}
    for (const run of data?.runs ?? []) {
      // The deputy's JUDGING run is not a conversation: its prompt is a kernel string and its reply
      // is private reasoning.
      if (run.feature === 'deputy') continue
      // The report IS the closing statement. Interactive turns are exempt: a person is in the room.
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
          // A phase prompt is the kernel's own trigger; the exceptions are the two a PERSON
          // authored.
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

  // Live frames append to the CURRENT phase lane. Skip as many replies as history already holds.
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
    // While the deputy judges, the indicator says so and nothing else: its reply is a private read.
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

  // Neither is a turn in the run trail, so `created_at` is the only thing relating them.
  const timed: Bubble[] = [
    ...reports.map((r, i) => ({ key: `rep-${i}`, speaker: 'agent' as const, phase: null,
                                run: r.run, text: '', ts: r.ts, report: r.report })),
    ...deputyDecisions.map((d, i) => ({ key: `dep-${i}`, speaker: 'deputy' as const,
                                        phase: d.phase, text: d.text, ts: d.ts })),
  ].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0))

  // A report inherits the lane it closes; a decision names its own gate and keeps it.
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
      {/* Two different emptinesses: a run IS in flight means the first turn has not landed, not
          that agents never ran. */}
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
        // One attribution per speaker per run: four things said in a row are one turn of one
        // speaker.
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
            <div className={`flex gap-2 ${sm.right ? 'flex-row-reverse' : ''}`}>
              {/* The gutter is held on a continuation row, so a bubble does not step out when its
                  header is dropped. */}
              <div className={`mt-0.5 shrink-0 ${sm.tint}`} title={sm.label}>
                {lead ? <sm.Icon size={14} /> : <span className="block h-[14px] w-[14px]" />}
              </div>
              {/* The owner's column hugs its side; a turn spanning the full width reads as
                  narration. */}
              <div className={`min-w-0 ${sm.right ? 'max-w-[85%]' : 'flex-1'}`}>
                {lead && (
                  <div className={`mb-0.5 flex items-center gap-1.5 text-[10px] text-faint
                                   ${sm.right ? 'justify-end' : ''}`}>
                    <span className={sm.tint}>{sm.label}</span>
                    {/* The phase names WHICH AGENT is talking; the owner is the same person in
                        every lane. */}
                    {b.speaker !== 'you' && b.phase && <span>· {PHASE_LABEL[b.phase] ?? b.phase}</span>}
                  </div>
                )}
                {b.report
                  ? <ReportCard r={b.report} phase={b.phase} />
                  : (
                    <div className={`rounded-lg border px-3 py-2 text-[12.5px] text-fg ${sm.bubble}`}>
                      {b.speaker === 'you'
                        ? <span className="whitespace-pre-wrap [overflow-wrap:anywhere]">{b.text}</span>
                        : <Markdown text={b.text} variant="chat" tone="dev" />}
                    </div>
                  )}
              </div>
            </div>
          </div>
        )
      })}

      {/* A gently blinking phase verb, no elapsed — the work-item card already owns the live
          timer. */}
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
