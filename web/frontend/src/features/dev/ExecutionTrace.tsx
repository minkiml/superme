import { useState } from 'react'
import {
  Terminal, BookOpen, FolderSearch, Search, SquareTerminal, FilePen, Bot, Globe, Sparkles, Wrench,
  ChevronRight,
} from 'lucide-react'
import { pairTrace, type PairedCall, type TraceRow } from '@/lib/trace'

// The execution call-trail, shared by both trace surfaces. One row per tool CALL, numbered so the
// count and the last row agree.
//
// A call that returned output is clickable, revealing the capped result inline.

// Icon + color per call, resolved by tool NAME first then by kind.
const CALL_STYLE: Record<string, { icon: typeof Terminal; color: string }> = {
  read: { icon: BookOpen, color: '#60a5fa' },
  glob: { icon: FolderSearch, color: '#a78bfa' },
  grep: { icon: Search, color: '#fbbf24' },
  bash: { icon: SquareTerminal, color: '#34d399' },
  edit: { icon: FilePen, color: '#fb7185' },
  write: { icon: FilePen, color: '#fb7185' },
  agent: { icon: Bot, color: '#818cf8' },
  task: { icon: Bot, color: '#818cf8' },
  webfetch: { icon: Globe, color: '#22d3ee' },
  websearch: { icon: Globe, color: '#22d3ee' },
}
const KIND_STYLE: Record<string, { icon: typeof Terminal; color: string }> = {
  agent: { icon: Bot, color: '#818cf8' },
  subagent: { icon: Bot, color: '#818cf8' }, // a spawned sub-agent (Task/Agent) — named by its type
  skill: { icon: Sparkles, color: '#e0a35a' },
  mcp: { icon: Wrench, color: '#8b93a1' }, // an in-process dev tool (read_dev_log, read_run, …)
  tool: { icon: Terminal, color: '#8b93a1' },
}
function callVisual(e: TraceRow) {
  return CALL_STYLE[(e.name ?? '').toLowerCase()] ?? KIND_STYLE[e.kind] ?? KIND_STYLE.tool
}

// Cut at the row's width, the part that IDENTIFIES a command — the filename at the end of a long
// path — is what is lost.
//
// Elide the middle instead; the untouched command stays in the tooltip.
const LONG_PATH = /(?:~|\/[\w.@-]+)(?:\/[\w.@-]+){3,}/g
function shortenPaths(cmd: string): string {
  return cmd.replace(LONG_PATH, (m) => '…/' + m.split('/').slice(-2).join('/'))
}

// Numbered from `start`. `time(row)` renders the per-row timestamp, since each surface formats its
// own field.
export function TraceRows<T extends TraceRow & { id?: number | string }>({
  rows, start = 1, time,
}: {
  rows: PairedCall<T>[]
  start?: number
  time?: (row: T) => string
}) {
  return (
    <ol className="space-y-0.5">
      {rows.map((p, i) => <CallRow key={p.call.id ?? i} n={start + i} pair={p} time={time} />)}
    </ol>
  )
}

function CallRow<T extends TraceRow>({ n, pair, time }: { n: number; pair: PairedCall<T>; time?: (row: T) => string }) {
  const [open, setOpen] = useState(false)
  const { call, result, depth, children, agent } = pair
  const { icon: Icon, color } = callVisual(call)
  const output = result?.description?.trim() || ''
  const hasOutput = output.length > 0
  // Only the shell needs it: every other call's `detail` is already an elided path or a short name.
  const shown = call.name === 'Bash' ? shortenPaths(call.description ?? '') : call.description
  // Numbering stays continuous with the parent's: the rows really did happen in this order.
  const nested = depth === 1
  return (
    <li className={nested ? 'ml-4 border-l border-line pl-2' : undefined}>
      <button
        type="button"
        disabled={!hasOutput}
        onClick={() => setOpen((v) => !v)}
        title={hasOutput ? (open ? 'Hide output' : 'Show output') : undefined}
        className={`flex w-full items-baseline gap-2 rounded px-1 py-0.5 text-left text-[13px] ${hasOutput ? 'hover:bg-hover' : 'cursor-default'}`}
      >
        <span className="w-5 shrink-0 text-right font-mono text-[10px] text-faint">{n}</span>
        {/* WHOSE call this is: concurrent sub-agents interleave, so a child row sits under
            whichever spawn scrolled past last. */}
        {agent !== undefined && (
          <span className="shrink-0 rounded-sm bg-hover px-1 font-mono text-[9px] leading-4 text-faint"
                title={nested ? `Called by sub-agent A${agent}` : `Sub-agent A${agent}`}>
            A{agent}
          </span>
        )}
        {/* The per-tool icon is the row's identity; a chevron is the disclosure affordance. */}
        <Icon size={12} className="shrink-0 translate-y-0.5" style={{ color }} />
        <span className="min-w-0 flex-1 truncate" title={`${call.name}${call.description ? ' - ' + call.description : ''}`}>
          <span className="text-fg">{call.name}</span>
          {call.description && <span className="text-faint"> - {shown}</span>}
        </span>
        {/* A spawn's own total: without it a reader that made seventy calls looks like it made
            three. */}
        {children !== undefined && (
          <span className="shrink-0 rounded bg-hover px-1.5 py-px font-mono text-[10px] text-muted"
                title={`${children} tool call${children === 1 ? '' : 's'} by this sub-agent, wherever they appear below`}>
            {children} calls
          </span>
        )}
        {hasOutput && (
          <ChevronRight size={11} className={`shrink-0 translate-y-0.5 text-faint transition-transform ${open ? 'rotate-90' : ''}`} />
        )}
        {time && <span className="shrink-0 font-mono text-[10px] text-faint">{time(call)}</span>}
      </button>
      {open && hasOutput && (
        <pre className="ml-7 mt-0.5 mb-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-hover/60 px-2 py-1.5 font-mono text-[11px] leading-relaxed text-muted">{output}</pre>
      )}
    </li>
  )
}

// Convenience: pair a flat event list and render it (single-run surfaces).
export default function ExecutionTrace<T extends TraceRow & { id?: number | string }>({
  events, time,
}: {
  events: T[]
  time?: (row: T) => string
}) {
  return <TraceRows rows={pairTrace(events)} time={time} />
}
