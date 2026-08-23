// Whether agent work can run at all, for every surface that offers it.
//
// One question, answered by the daemon: a surface that decided for itself would drift from the
// route that actually refuses the run. While the first answer is in flight nothing is greyed —
// a lock that flashes on every load reads as breakage.

import { getAuthStatus, type AuthStatus } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'

// The daemon caches its own answer for a minute; polling faster only spawns processes there.
const POLL_MS = 60_000

export type AuthGate = {
  ready: boolean
  /** The sentence to show — where to put a credential. Null while everything is fine. */
  reason: string | null
  status: AuthStatus | undefined
}

export function useAuthGate(): AuthGate {
  const { data } = useLive(K.authStatus, () => getAuthStatus(), POLL_MS)
  const ready = data?.ready ?? true
  return { ready, reason: ready ? null : data?.detail ?? null, status: data }
}

// "Look around first" on the setup page. Session-scoped on purpose: a new tab should meet the
// guide again, and a reload is the cheapest way back to it.
const DISMISSED = 'superme.auth.lookingAround'

export function lookingAround(): boolean {
  try {
    return sessionStorage.getItem(DISMISSED) === '1'
  } catch {
    return false
  }
}

export function setLookingAround(on: boolean): void {
  try {
    on ? sessionStorage.setItem(DISMISSED, '1') : sessionStorage.removeItem(DISMISSED)
  } catch {
    /* private mode — the guide simply shows again */
  }
}
