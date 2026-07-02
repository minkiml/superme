import { fmtModel } from '@/lib/format'

// Seed so the "/" palette isn't empty before the first turn populates the real list.
// Includes our shared command (/model) + the headless built-ins.
export const SEED_COMMANDS = ['model', 'compact', 'clear']

// Canonical "Name Version" label (e.g. "claude-opus-4-8" → "Opus 4.8") — one source of truth.
export function formatModel(id: string | null): string {
  return fmtModel(id)
}
