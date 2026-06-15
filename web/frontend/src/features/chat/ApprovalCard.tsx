import type { Approval } from './types'

// The inline tool-approval prompt (interactive permissions). Allow/Deny answer back over
// the WebSocket via `onAnswer`.
export default function ApprovalCard({
  approval,
  onAnswer,
}: {
  approval: Approval
  onAnswer: (approved: boolean) => void
}) {
  return (
    <div className="rounded-lg border border-warn bg-surface p-3 text-sm">
      <div className="mb-2 text-warn">
        Approve <span className="font-semibold">{approval.tool_name}</span>?
      </div>
      <pre className="mb-2 max-h-32 overflow-auto rounded bg-sunken p-2 text-xs text-fg">
        {JSON.stringify(approval.tool_input, null, 2)}
      </pre>
      <div className="flex gap-2">
        <button className="rounded bg-success px-3 py-1 text-on-accent" onClick={() => onAnswer(true)}>
          Allow
        </button>
        <button className="rounded bg-danger px-3 py-1 text-on-accent" onClick={() => onAnswer(false)}>
          Deny
        </button>
      </div>
    </div>
  )
}
