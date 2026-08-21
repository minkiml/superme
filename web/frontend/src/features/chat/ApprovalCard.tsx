import type { Approval } from './types'

// The inline tool-approval prompt, and the ONLY human check on a general session's shell.
//
// The command renders as itself, wrapped and whole: the dangerous half is usually its end.

// What the tool will actually do, in the owner's words, plus the one word that tells them most
// (the program / the file). An unknown tool keeps the honest generic phrasing.
function headline(tool: string, input: Record<string, any>): { ask: string; subject: string } {
  const cmd = String(input?.command ?? '')
  switch (tool) {
    case 'Bash':
      return { ask: 'Run a shell command?', subject: cmd.trim().split(/\s+/)[0] ?? '' }
    case 'Write':
    case 'Edit':
    case 'MultiEdit':
    case 'NotebookEdit':
      return { ask: 'Write to a file?', subject: String(input?.file_path ?? '').split('/').pop() ?? '' }
    case 'WebFetch':
      return { ask: 'Fetch a web page?', subject: String(input?.url ?? '') }
    default:
      // The wire name is the least useful half, and makes our own controls read as foreign
      // machinery.
      if (tool.startsWith('mcp__')) {
        return { ask: 'Run a SuperMe action?', subject: tool.split('__').slice(2).join('__') || tool }
      }
      return { ask: `Run ${tool}?`, subject: '' }
  }
}

// For a shell command that IS the command, never the JSON wrapper; anything else falls back to the
// pretty-printed input.
function body(tool: string, input: Record<string, any>): string {
  if (tool === 'Bash' && input?.command) return String(input.command)
  return JSON.stringify(input, null, 2)
}

export default function ApprovalCard({
  approval,
  onAnswer,
}: {
  approval: Approval
  onAnswer: (approved: boolean) => void
}) {
  const input = (approval.tool_input ?? {}) as Record<string, any>
  const { ask, subject } = headline(approval.tool_name, input)
  // A CLAIM, not a fact, which is why it sits above the command in muted type rather than replacing
  // it.
  const why = String(input?.description ?? '').trim()
  return (
    // A full border, not a left edge: the left edge already means the owner's own bubble.
    <div className="rounded-lg border border-warn bg-surface p-3">
      <div className="mb-1.5 text-[13px] text-warn">
        {ask}
        {subject && <span className="ml-1 text-xs text-muted">· {subject}</span>}
      </div>
      {why && <div className="mb-1.5 text-xs text-muted">{why}</div>}
      {/* No horizontal scroll and no height cap: the whole command is on screen, or the card is
          lying. */}
      <div className="mb-2.5 rounded bg-sunken px-2.5 py-2 font-mono text-xs leading-relaxed
                      whitespace-pre-wrap [overflow-wrap:anywhere] text-fg">
        {body(approval.tool_name, input)}
      </div>
      <div className="flex gap-2">
        <button
          className="rounded-md border border-accent px-3 py-1 text-xs font-medium text-accent
                     hover:bg-accent/10 focus-visible:outline focus-visible:outline-2
                     focus-visible:outline-offset-2 focus-visible:outline-accent"
          onClick={() => onAnswer(true)}
        >
          Allow
        </button>
        <button
          className="rounded-md border border-danger px-3 py-1 text-xs font-medium text-danger
                     hover:bg-danger/10 focus-visible:outline focus-visible:outline-2
                     focus-visible:outline-offset-2 focus-visible:outline-danger"
          onClick={() => onAnswer(false)}
        >
          Deny
        </button>
      </div>
    </div>
  )
}
