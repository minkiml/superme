import { useMemo } from 'react'
import { ArrowRight, Check, Loader2, FileText, Ban, GitMerge, ExternalLink, ChevronRight, RotateCcw, Play } from 'lucide-react'
import SectionHeader from '@/ui/SectionHeader'
import { type WorkItem, type DrilldownAction } from '@/lib/api'
import { fmtModel, fmtTokens } from '@/lib/format'
import { PHASE_LABEL } from '../common'

// Shared atoms: the chrome and text handling every pane in this folder reaches for.

// The progress indicator, deliberately NOT tabs: a button under a stage that already happened acts
// on the LIVE phase.
//
// Reading a past stage is what the Reports tab is for.
export function ProgressBar({ pipeline, phase, running, done }: {
  pipeline: string[]; phase: string; running: boolean; done: boolean
}) {
  const idx = pipeline.indexOf(phase)
  return (
    <div className="mt-3 flex items-center gap-0" aria-label="pipeline progress">
      {pipeline.map((p, i) => {
        const state = done || i < idx ? 'done' : i === idx ? 'current' : 'future'
        const last = i === pipeline.length - 1
        return (
          // The last stage carries no connector, so it sizes to its label and the connectors absorb
          // the width.
          <div key={p} className={`flex items-center ${last ? 'min-w-0 shrink-0' : 'min-w-0 flex-1'}`}>
            <div className="flex flex-col items-center gap-1">
              <span className={`grid h-3 w-3 place-items-center rounded-full ${
                state === 'done' ? 'bg-success'
                : state === 'current' ? (running ? 'animate-pulse bg-accent' : 'bg-accent')
                : 'bg-line'
              }`}>
                {state === 'current' && running && <span className="h-1 w-1 rounded-full bg-on-accent" />}
              </span>
              <span className={`whitespace-nowrap text-[10px] font-medium uppercase tracking-wide ${
                state === 'current' ? 'text-fg' : state === 'done' ? 'text-faint' : 'text-line'
              }`}>{PHASE_LABEL[p] ?? p}</span>
            </div>
            {i < pipeline.length - 1 && (
              <span className={`-mt-4 h-px min-w-2 flex-1 ${i < idx || done ? 'bg-success' : 'bg-line'}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}

export function ActionButton({ a, busy, primary, quiet, onClick }: {
  a: DrilldownAction; busy: boolean; primary?: boolean; quiet?: boolean; onClick: () => void
}) {
  // Both come from the server. Greyed has to LOOK greyed, or it still reads as "do this".
  const cls = primary && a.active
    ? 'bg-accent font-medium text-on-accent hover:opacity-90 px-3'
    : quiet
      ? 'text-faint hover:text-fg px-2.5'
      : 'border border-line bg-surface text-muted hover:bg-hover hover:text-fg px-3'
  return (
    <button onClick={onClick} disabled={!a.active || busy} title={a.reason}
            className={`inline-flex items-center gap-1.5 rounded-md py-1.5 text-[13px] transition disabled:opacity-40 ${cls}`}>
      {busy ? <Loader2 size={14} className="animate-spin" /> : <ActionIcon id={a.id} />}
      {a.label}
    </button>
  )
}

export function ActionIcon({ id }: { id: string }) {
  const map: Record<string, typeof Check> = {
    approve: Check, drop: Ban, run: Play,
    force: ArrowRight, merge: GitMerge, pr: ExternalLink,
    rerun: RotateCcw,
  }
  const Icon = map[id] ?? ChevronRight
  return <Icon size={14} />
}

// ── Quick View › Now ────────────────────────────────────────────────────────────────────────────

// A fixed width, so every row's content starts at the same x; ragged labels read as unrelated
// fragments.
export function Row({ n, label, children }: { n: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      {/* The META step, since these are row LABELS, and wide enough for the longest on ONE line. */}
      <span className="mt-px w-[5.75rem] shrink-0 whitespace-nowrap font-mono text-[11px] tracking-wide text-warn">
        {n} {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

// ── Quick View › Proof ──────────────────────────────────────────────────────────────────────────

// Sentence-case lines that ARE sentences but reach us lowercase — only when the string starts with
// a LETTER.
//
// A line opening with a backtick starts a code span, and capitalising into it would rewrite a
// command.
export function sentence(text: string): string {
  return /^[a-z]/.test(text) ? text[0].toUpperCase() + text.slice(1) : text
}

// `code` is the only inline markup our templates use, and raw backticks read as quotes. 12px
// absolute, never `em`.
export function codeSpans(text: string) {
  return text.split('`').map((part, i) => (i % 2
    // Tinted like every other code span, so the one token the owner can type keeps its colour
    // across tabs.
    ? <code key={i} className="rounded bg-hover px-1 font-mono text-[12px] text-accent-text">{part}</code>
    : <span key={i}>{part}</span>))
}

// The run telemetry line — model · context fill · per-phase token chips (3-type basis, same as the
// Activity log). Phases with no recorded spend stay hidden.
export function RunMeta({ it }: { it: WorkItem }) {
  const phases = useMemo(
    () => Object.entries(it.phase_tokens ?? {}).filter(([, v]) => v > 0),
    [it.phase_tokens],
  )
  const bits = [
    it.model && fmtModel(it.model),
    it.ctx_pct != null && `ctx ${it.ctx_pct}%`,
    (it.total_tokens ?? 0) > 0 && `Σ ${fmtTokens(it.total_tokens ?? 0)} tok`,
  ].filter(Boolean)
  if (!bits.length && !phases.length) return null
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-faint">
      <span>{bits.join(' · ')}</span>
      {phases.map(([p, v]) => (
        <span key={p} title={`Tokens spent while in ${p}`} className="rounded bg-hover px-1.5 py-0.5 font-mono text-[10px]">
          {p} {fmtTokens(v)}
        </span>
      ))}
    </div>
  )
}

export function Section({ icon: Icon, title, children }: { icon: typeof FileText; title: string; children: React.ReactNode }) {
  return (
    <section>
      <SectionHeader className="mb-1.5 flex items-center gap-1.5">
        <Icon size={12} /> {title}
      </SectionHeader>
      {children}
    </section>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="text-[13px] text-faint">{children}</div>
}

export function Loading() {
  return (
    <div className="flex items-center gap-2 py-6 text-[13px] text-muted">
      <Loader2 size={14} className="animate-spin" /> Loading…
    </div>
  )
}
