import { getJSON } from './client'
import type { ContextRef } from '@/lib/contexts'

// Live contexts (global + connected domains), read from the daemon's workspace registry.
export function listContexts(): Promise<ContextRef[]> {
  return getJSON('/api/contexts')
}
