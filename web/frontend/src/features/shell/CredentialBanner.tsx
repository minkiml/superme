import { useState } from 'react'
import { KeyRound, RefreshCw } from 'lucide-react'
import { getAuthStatus } from '@/lib/api'
import { invalidate } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { useAuthGate } from '@/lib/authGate'

// The reminder for someone who chose "look around first" on the setup page — the guide is what
// an install without a credential lands on, and this is what is left after dismissing it.
export default function CredentialBanner({ onOpenSetup }: { onOpenSetup: () => void }) {
  const { ready, reason } = useAuthGate()
  const [checking, setChecking] = useState(false)
  if (ready || !reason) return null

  // Signing in happens in a terminal, so the fix lands outside this app — the owner needs a way to
  // say "done" without restarting the daemon.
  function recheck() {
    setChecking(true)
    getAuthStatus(true)
      .then(() => invalidate(K.authStatus))
      .catch(() => {})
      .finally(() => setChecking(false))
  }

  return (
    <div className="flex shrink-0 items-start gap-2 border-b border-warn/40 bg-warn/10 px-3 py-1.5 text-[11px] text-fg">
      <KeyRound size={13} className="mt-0.5 shrink-0 text-warn" />
      {/* Wraps rather than truncates: the half that gets cut is the half that says what to run. */}
      <span className="min-w-0 flex-1 leading-relaxed">
        <span className="font-medium">No Anthropic credential.</span> Agent turns, work-item
        phases and distilling are switched off.
      </span>
      <button
        onClick={onOpenSetup}
        className="shrink-0 rounded border border-warn/50 px-1.5 py-0.5 text-[10px] font-medium text-fg transition-colors hover:bg-warn/20"
      >
        Set up
      </button>
      <button
        onClick={recheck}
        disabled={checking}
        className="mt-px flex shrink-0 items-center gap-1 rounded border border-line px-1.5 py-0.5 text-[10px] text-muted transition-colors hover:border-accent hover:text-accent-text disabled:opacity-50"
      >
        <RefreshCw size={11} className={checking ? 'animate-spin' : ''} />
        {checking ? 'checking' : 'I signed in'}
      </button>
    </div>
  )
}
