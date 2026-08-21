import { Bot } from 'lucide-react'
import SectionHeader from '@/ui/SectionHeader'

// The tints, badges and section chrome the review surfaces share.

export const FORM_TINT: Record<string, string> = {
  fact: 'text-success',
  skill: 'text-accent-text',
  agent: 'text-accent-text',
  contract: 'text-warn',
  core_candidate: 'text-warn',
}

export function StageBadge({ status, className = '' }: { status: string; className?: string }) {
  const tint =
    status === 'drafted' ? 'bg-accent-soft text-accent-text'
    : status === 'writing' ? 'bg-warn/15 text-warn'
    : 'bg-hover text-muted'
  return <span className={`rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wide ${tint} ${className}`}>{status}</span>
}

export function PSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <SectionHeader className="mb-1.5">{title}</SectionHeader>
      {children}
    </div>
  )
}

// "Agent at work" indicator — a robot head + label that blink together as one unit. Wrap the icon
// AND its text so the whole thing pulses in sync (callers pass the label as children).
export function AgentWorking({ size = 14, className = '', children }: { size?: number; className?: string; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 text-accent-text animate-pulse ${className}`} style={{ animationDuration: '1.4s' }}>
      <Bot size={size} className="shrink-0" />
      {children}
    </span>
  )
}

// Narrate the forge run's phase from elapsed time (the run is author → evaluate → brush up). We don't
// have per-step events, so this is time-anchored: forging first, then the long eval, then polish.
export function forgePhase(form: string, elapsed: number): string {
  const f = form || 'artifact'
  if (elapsed < 14) return `Forging the ${f}…`
  return Math.floor((elapsed - 14) / 18) % 2 === 0 ? `Evaluating the ${f}…` : `Brushing up the ${f}…`
}

// Compact phase word for the queue card (no form noun — the card already shows the form).
export function forgePhaseShort(elapsed: number): string {
  if (elapsed < 14) return 'forging'
  return Math.floor((elapsed - 14) / 18) % 2 === 0 ? 'evaluating' : 'polishing'
}
