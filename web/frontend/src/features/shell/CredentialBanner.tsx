import { KeyRound } from 'lucide-react'
import { useAuthGate } from '@/lib/authGate'

// The reminder for someone who chose "look around first" on the setup page. It says the state and
// points back at the guide — re-checking belongs there, where the instructions are, rather than
// being a second door onto the same act.
export default function CredentialBanner({ onOpenSetup }: { onOpenSetup: () => void }) {
  const { ready, reason } = useAuthGate()
  if (ready || !reason) return null

  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-warn/40 bg-warn/10 px-3 py-1.5 text-[11px] text-fg">
      <KeyRound size={13} className="shrink-0 text-warn" />
      <span className="min-w-0 flex-1 leading-relaxed">
        <span className="font-medium">No Claude credential.</span> Agent turns, work-item phases
        and distilling are off.
      </span>
      <button
        onClick={onOpenSetup}
        className="shrink-0 rounded border border-warn/50 px-1.5 py-0.5 text-[10px] font-medium text-fg transition-colors hover:bg-warn/20"
      >
        Set up
      </button>
    </div>
  )
}
