import type { Approval } from './types'

// The inline tool-approval prompt (interactive permissions). Allow/Deny answer back over
// the WebSocket via `onAnswer`.
//
// This card is now the ONLY human check on a general session's shell (permissions.py: a command
// that can't be PROVEN read-only asks instead of refusing), so what it shows has to be what you'd
// judge on. It used to render `JSON.stringify(tool_input)` inside a non-wrapping, height-capped
// `<pre>` — you read braces and escaped quotes, and a long command ran off to the right behind a
// scrollbar. The dangerous half of a command is usually its end.
//
// So: the command renders as itself, wrapped, whole. Both buttons carry colour at equal weight —
// either answer is normal here, and a red block shouting at a `SELECT` teaches you to stop reading.

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
      // SuperMe's own in-process tools arrive as `mcp__<server>__<name>`. Asking "Run
      // mcp__dev__push_inbox_item?" makes the app's own controls read like foreign machinery, and
      // the wire name is the least useful half of the string. Name the act, keep the tool as the
      // subject — the arguments below say which item.
      if (tool.startsWith('mcp__')) {
        return { ask: 'Run a SuperMe action?', subject: tool.split('__').slice(2).join('__') || tool }
      }
      return { ask: `Run ${tool}?`, subject: '' }
  }
}

// The body a person reads. For a shell command that IS the command — never the JSON wrapper around
// it. Anything else falls back to the pretty-printed input, which is at least honest about being
// raw; a per-tool view earns its keep once a tool other than Bash actually reaches this card.
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
  // The agent's own one-line account of what it's for. A CLAIM, not a fact — which is why it sits
  // above the command in muted type rather than replacing it: you read the intent, then check it
  // against what will actually run.
  const why = String(input?.description ?? '').trim()
  return (
    // A FULL warn border, not a left edge. The left edge is already the owner's own bubble
    // (TALKER.you in MessageList), so an accent rail here read as "another message" rather than
    // "the app is stopping to ask you". Enclosing the card is what says it is a different kind of
    // thing, and it's the one place in the rail that interrupts.
    <div className="rounded-lg border border-warn bg-surface p-3">
      <div className="mb-1.5 text-[13px] text-warn">
        {ask}
        {subject && <span className="ml-1 text-xs text-muted">· {subject}</span>}
      </div>
      {why && <div className="mb-1.5 text-xs text-muted">{why}</div>}
      {/* `whitespace-pre-wrap` + `break-words`: no horizontal scroll, no height cap — the whole
          command is on screen or the card is lying about what you approved. */}
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
