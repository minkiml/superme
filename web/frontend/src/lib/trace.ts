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
// Rows render in CALL ORDER, so a fan-out's children arrive braided and the ones sitting under a
// spawn row mostly belong to other readers. Two fields carry the attribution the position destroys:
//
//   `agent`    — which sub-agent this is. On a spawn row it is that agent's own number; on a child
//                row it is the number of the spawn it belongs to. Same number, both ends, so a row
//                can be matched to its agent by eye without the nesting having to be true.
//   `children` — a spawn's own total, wherever those calls appear. Without it a reader that made
//                seventy calls but only three of them adjacent reads as one that stopped after three.
export type PairedCall<T extends TraceRow> = {
  call: T; result: T | null; depth: 0 | 1; children?: number; agent?: number
}

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
  // Attribute every child to the spawn it actually belongs to — over the WHOLE list, not the rows
  // that happen to follow it, which is the point.
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

  // GROUP each sub-agent's calls under its own spawn. Indentation is read as "belongs to the row
  // above"; leaving the list in call order made that bracket false, because concurrent readers
  // interleave and a child lands under whichever spawn ran most recently.
  //
  // The parent's own calls keep their place, so before-the-fan-out and after-it still read in order.
  // What is given up is cross-agent ordering INSIDE the fan-out — which reader got a slot first is
  // scheduler noise, and each row still carries its own timestamp.
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
