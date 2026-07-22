import { useEffect, useMemo, useRef, useState } from 'react'
import { Bot, User, ShieldCheck } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import { getWorkItemTimeline, type WorkItemTimeline } from '@/lib/api/dev'
import type { TimelineFrame } from './hooks/useAgentSocket'

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

type Bubble = {
  key: string
  speaker: Speaker
  phase: string | null
  text: string
  live?: boolean
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
  const scrollRef = useRef<HTMLDivElement | null>(null)

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
      let tools = 0
      for (const ev of run.events ?? []) {
        if (ev.kind === 'reply') {
          out.push({ key: `r${run.run_id}-${ev.seq}`, speaker: replySpeaker(run.feature),
                     phase: run.phase ?? null, text: ev.description || '' })
        } else if (ev.kind === 'prompt') {
          if (run.feature === 'chat') {
            out.push({ key: `p${run.run_id}-${ev.seq}`, speaker: 'you',
                       phase: run.phase ?? null, text: ev.description || '' })
          }
        } else {
          tools += 1
        }
      }
      counts[run.run_id] = tools
    }
    return { bubbles: out, toolCounts: counts }
  }, [data])

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
    if (f.kind === 'reply') {
      const rid = f.run_id ?? -1
      const already = historyReplies[rid] ?? 0
      const seen = seenPerRun[rid] ?? 0
      seenPerRun[rid] = seen + 1
      if (seen < already) continue // this reply is already rendered from history
      liveBubbles.push({ key: `live-${i}`, speaker: 'agent', phase: currentPhase, text: f.description || '', live: true })
    } else {
      liveTools += 1
    }
  }
  // The interactive (owner-fired) turn streams separately — show it as a live agent bubble at the end.
  if (interactiveLive.trim()) {
    liveBubbles.push({ key: 'interactive-live', speaker: 'agent', phase: currentPhase, text: interactiveLive, live: true })
  }

  const all = [...bubbles, ...liveBubbles]

  // Auto-scroll to the newest as content grows.
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [all.length, interactiveLive])

  return (
    <div ref={scrollRef} className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-3 py-3">
      {err && !data && <div className="px-1 text-[12px] text-danger">Couldn’t load the timeline — {err}</div>}
      {data && all.length === 0 && (
        <div className="px-1 pt-4 text-center text-[12px] text-faint">No turns yet — this item’s agents haven’t run.</div>
      )}
      {all.map((b, i) => {
        const prevPhase = i > 0 ? all[i - 1].phase : null
        const showDivider = b.phase && b.phase !== prevPhase
        const sm = SPEAKER_META[b.speaker]
        return (
          <div key={b.key}>
            {showDivider && (
              <div className="my-2 flex items-center gap-2 text-[10px] font-medium uppercase tracking-wide text-faint">
                <span className="h-px flex-1 bg-line" />
                {PHASE_LABEL[b.phase!] ?? b.phase} phase
                <span className="h-px flex-1 bg-line" />
              </div>
            )}
            <div className="flex gap-2">
              <div className={`mt-0.5 shrink-0 ${sm.tint}`} title={sm.label}>
                <sm.Icon size={14} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="mb-0.5 flex items-center gap-1.5 text-[10px] text-faint">
                  <span className={sm.tint}>{sm.label}</span>
                  {b.phase && <span>· {PHASE_LABEL[b.phase] ?? b.phase}</span>}
                </div>
                <div className={`rounded-lg border px-2.5 py-1.5 text-[12.5px] text-fg ${sm.bubble}`}>
                  <Markdown text={b.text} variant="chat" tone="dev" />
                </div>
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
