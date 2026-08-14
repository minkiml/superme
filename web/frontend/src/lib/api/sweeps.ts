import { getJSON, sendJSON } from './client'
import type { Schema } from './generated'

// The standing-sweep launch bar (research-sweep-model §7). A STANDING sweep is a research
// work-item whose subject is the codebase itself and whose question never stops being worth
// asking — `audit · refactoring · housekeeping · security`. It is launched from a button rather
// than raised as a ticket, and the item is born already classified, at `investigate`.
//
// Which families exist is a property of the harness, not the repo, so this list is the same on
// every project. Adding one is a row in `core/kind_profiles.RESEARCH_FAMILIES` — the button
// appears with no change here.
//
// Every FE call goes through the same-origin `/api` prefix — Vite proxies it to the BFF,
// which reverse-proxies the daemon. A bare `/dev/...` path 200s against Vite's SPA
// fallback and parses as HTML, so the bar silently renders nothing (caught live).
//
// NOT the learning capture sweep (`/dev/sweep`), which mines a conversation for durable
// learnings. Two live meanings of one word; the paths are deliberately far apart.

export type SweepFamily = Schema<'SweepFamily'>

export async function getSweepFamilies(): Promise<SweepFamily[]> {
  const r = await getJSON<Schema<'SweepFamiliesResponse'>>('/api/dev/research/sweeps/families')
  return r.families ?? []
}

export type SweepLaunch = Schema<'SweepLaunchResponse'>

// Costs real money — every caller confirms first. `area` empty = the whole repo, which is the
// honest default for a standing sweep. `interest` is required by the families whose
// `asks_interest` is set (audit today): its question is meaningless until you say sound in WHAT.
export async function launchSweep(
  contextId: string, family: string, area = '', interest = '',
): Promise<SweepLaunch> {
  return sendJSON<SweepLaunch>('/api/dev/research/sweeps', 'POST', {
    family, area, interest, context_id: contextId,
  })
}
