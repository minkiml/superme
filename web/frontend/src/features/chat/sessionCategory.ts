import { Hammer, Stethoscope, Sparkles, MessageSquareText, type LucideIcon } from 'lucide-react'
import type { SessionMeta } from '@/lib/api'

// A session's display CATEGORY — the chip shown in the collapsed chat rail. Derivation mirrors the
// daemon's session_kind resolution (session-kinds-diagnose): a work-item stamp (item_id) wins, then
// the durable `kind` (diagnosis / onboarding), else general. `kind` may be null on legacy sessions
// (pre-stamp) → general.
export type SessionCategory = {
  key: 'work_item' | 'diagnosis' | 'onboarding' | 'general'
  label: string
  Icon: LucideIcon
  color: string // text-color token class for the glyph
}

export function sessionCategory(s: Pick<SessionMeta, 'item_id' | 'kind'>): SessionCategory {
  if (s.item_id) return { key: 'work_item', label: 'Work-item', Icon: Hammer, color: 'text-dev' }
  if (s.kind === 'diagnosis') return { key: 'diagnosis', label: 'Diagnosis', Icon: Stethoscope, color: 'text-warn' }
  if (s.kind === 'onboarding') return { key: 'onboarding', label: 'Onboarding', Icon: Sparkles, color: 'text-universal' }
  return { key: 'general', label: 'General', Icon: MessageSquareText, color: 'text-muted' }
}
