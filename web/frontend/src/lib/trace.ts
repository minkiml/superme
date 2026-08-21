// Execution-trace pairing, shared by both trace surfaces so they read identically.
//
// A result pairs back to its call by `tool_id`, which is REQUIRED because concurrent tools return
// out of call order. Legacy rows with no id fall back to FIFO.

export const TRACE_CALL_KINDS = new Set(['tool', 'mcp', 'skill', 'agent', 'subagent'])

// The minimal shape both RunEvent (run_event) and RunArtifact (run_artifact) share.
export type TraceRow = {
  kind: string; name?: string | null; description?: string | null; tool_id?: string | null
  // The tool_use id of the sub-agent SPAWN this row happened inside (null = the agent's own call).
  parent_tool_id?: string | null
}

// `depth` separates the agent's own calls from a sub-agent's, since a fan-out interleaves children
// into one stream.
//
// Rows arrive braided, so `agent` numbers both ends of a spawn and `children` is that spawn's own
// total.
export type PairedCall<T extends TraceRow> = {
  call: T; result: T | null; depth: 0 | 1; children?: number; agent?: number
}

// Pair each call with its result, in call order, so the badge and the last row number always agree.
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
  // Attribute every child over the WHOLE list, not the rows that happen to follow its spawn.
  const byParent = new Map<string, number>()
  for (const r of rows) {
    const p = r.call.parent_tool_id
    if (p) byParent.set(p, (byParent.get(p) ?? 0) + 1)
  }
  // Number the spawns in the order they appear, so the label is short and reads the same at both ends.
  const agentNo = new Map<string, number>()
  for (const r of rows) {
    const id = r.call.tool_id
    if (id && byParent.has(id) && !agentNo.has(id)) agentNo.set(id, agentNo.size + 1)
  }
  for (const r of rows) {
    const id = r.call.tool_id
    if (id && byParent.has(id)) { r.children = byParent.get(id); r.agent = agentNo.get(id) }
    else if (r.call.parent_tool_id) r.agent = agentNo.get(r.call.parent_tool_id)
  }

  // Indentation reads as "belongs to the row above", which call order makes false when readers
  // interleave.
  const kids = new Map<string, PairedCall<T>[]>()
  for (const r of rows) {
    const p = r.call.parent_tool_id
    if (p && byParent.has(p)) kids.set(p, [...(kids.get(p) ?? []), r])
  }
  const grouped: PairedCall<T>[] = []
  for (const r of rows) {
    const p = r.call.parent_tool_id
    // An orphan (its spawn is not in this slice of the trace) keeps its place rather than vanishing.
    if (p && byParent.has(p)) continue
    grouped.push(r)
    const id = r.call.tool_id
    if (id && kids.has(id)) grouped.push(...kids.get(id)!)
  }
  return grouped
}
