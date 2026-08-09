// Shared chat-feature types.

// 'deputy' = the owner's stand-in acting on autopiloted items (autopilot slice 4). It speaks IN the
// work-item thread, attributed and visually distinct, never silent under the system.
// 'system' = the app talking ABOUT the conversation, not a talker in it — a turn that errored, a
// connection that dropped. It used to be dressed as SuperMe, which put words in the agent's mouth
// that no run ever produced; the owner reads a transcript to know who said what, so the machine's
// own notices must not be able to pass for the agent's.
export type Msg = { role: 'you' | 'superme' | 'deputy' | 'system'; text: string }

export type Approval = { id: string; tool_name: string; tool_input: any }

// Per-turn run metadata the daemon reports on `result` (shown in the header).
export type RunMeta = { model: string | null; pct: number | null; window: number | null }
