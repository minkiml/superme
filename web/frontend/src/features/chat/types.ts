// Shared chat-feature types.

// `deputy` is the owner's stand-in, speaking IN the thread, attributed and visually distinct.
//
// `system` is the app talking ABOUT the conversation, not a talker in it: dressed as the agent, it
// put words in a mouth no run produced.
export type Msg = { role: 'you' | 'superme' | 'deputy' | 'system'; text: string }

export type Approval = { id: string; tool_name: string; tool_input: any }

// Per-turn run metadata the daemon reports on `result` (shown in the header).
export type RunMeta = { model: string | null; pct: number | null; window: number | null }
