// Shared chat-feature types.

// 'deputy' = the owner's stand-in acting on autopiloted items (autopilot slice 4). It speaks IN the
// work-item thread, attributed and visually distinct, never silent under the system.
export type Msg = { role: 'you' | 'superme' | 'deputy'; text: string }

export type Approval = { id: string; tool_name: string; tool_input: any }

// Per-turn run metadata the daemon reports on `result` (shown in the header).
export type RunMeta = { model: string | null; pct: number | null; window: number | null }
