// Shared chat-feature types.

export type Msg = { role: 'you' | 'superme'; text: string }

export type Approval = { id: string; tool_name: string; tool_input: any }

// Per-turn run metadata the daemon reports on `result` (shown in the header).
export type RunMeta = { model: string | null; pct: number | null; window: number | null }
