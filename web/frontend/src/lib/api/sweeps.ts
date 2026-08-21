import { getJSON, sendJSON } from './client'
import type { Schema } from './generated'

// The standing-sweep launch bar. A standing sweep's subject is the codebase itself, and it is born
// already classified.
//
// NOT the learning capture sweep, which mines a conversation; the paths are deliberately far apart.

export type SweepFamily = Schema<'SweepFamily'>

export async function getSweepFamilies(): Promise<SweepFamily[]> {
  const r = await getJSON<Schema<'SweepFamiliesResponse'>>('/api/dev/research/sweeps/families')
  return r.families ?? []
}

export type SweepLaunch = Schema<'SweepLaunchResponse'>

// Costs real money, so every caller confirms first. Empty `area` means the whole repo, the honest
// default.
export async function launchSweep(
  contextId: string, family: string, area = '', interest = '',
): Promise<SweepLaunch> {
  return sendJSON<SweepLaunch>('/api/dev/research/sweeps', 'POST', {
    family, area, interest, context_id: contextId,
  })
}
