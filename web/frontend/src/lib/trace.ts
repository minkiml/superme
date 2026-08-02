// Execution-trace pairing — shared by the Activity RunTraceModal and the work-item Execution tab so
// both read identically. A run's trail is a flat, ordered list of `prompt | reply | <call> | result`
// events. Each tool CALL and its `result` carry the same `tool_id` (the SDK tool_use id), so a result
// pairs back to its call by ID — REQUIRED because concurrent tools return out of call order (Read
// called before Glob can still return after it). Legacy rows (no tool_id) fall back to FIFO order.
// Non-call/non-result events (prompt, reply) are dropped.

export const TRACE_CALL_KINDS = new Set(['tool', 'mcp', 'skill', 'agent', 'subagent'])

// The minimal shape both RunEvent (run_event) and RunArtifact (run_artifact) share.
export type TraceRow = {
  kind: string; name?: string | null; description?: string | null; tool_id?: string | null
  // The tool_use id of the sub-agent SPAWN this row happened inside (null = the agent's own call).
  parent_tool_id?: string | null
}

// `depth` is 0 for the agent's own calls and 1 for anything a sub-agent did — a fan-out interleaves
// several children into one ordered stream, so without it three parallel readers render as one
// confused agent. Only two levels: the SDK's own nesting can go deeper, but a spawn inside a spawn
// is not something SuperMe's skills do, and a generic tree would cost more than it explains.
export type PairedCall<T extends TraceRow> = { call: T; result: T | null; depth: 0 | 1 }

// Pair each call with its result (null if none), in call order. Callers render one row per pair and
// number them 1..N — so the badge (N) and the last row number always agree, on either surface.
export function pairTrace<T extends TraceRow>(events: T[]): PairedCall<T>[] {
  const rows: PairedCall<T>[] = []
  const pending: PairedCall<T>[] = [] // calls still awaiting their result
  for (const e of events) {
    if (e.kind === 'result') {
      // Match by tool_id; fall back to the oldest pending call for legacy rows without one.
      let idx = e.tool_id ? pending.findIndex((p) => p.call.tool_id === e.tool_id) : -1
      if (idx < 0) idx = 0
      const [slot] = pending.splice(idx, 1)
      if (slot) slot.result = e
    } else if (TRACE_CALL_KINDS.has(e.kind)) {
      const slot: PairedCall<T> = { call: e, result: null, depth: e.parent_tool_id ? 1 : 0 }
      rows.push(slot)
      pending.push(slot)
    }
  }
  return rows
}
