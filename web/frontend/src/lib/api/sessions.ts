import { getJSON, sendJSON } from './client'
import type { Schema } from './generated'

// Sessions: list + replay. History comes from the SDK transcripts, not a parallel log.
// Transport shapes derive from the daemon OpenAPI; the FE narrows the loose `surface`/`mode`/
// `role` strings (R5 tightens these server-side later).

export type ChatMode = 'core' | 'dev'

export type SessionMeta = Omit<Schema<'SessionSummary'>, 'surface' | 'mode'> & {
  surface: 'slack' | 'web'
  mode: ChatMode
}

export type ChatBubble = Omit<Schema<'SessionBubble'>, 'role'> & { role: 'you' | 'superme' }

// readSession returns the SessionDetail envelope with narrowed message bubbles.
export type SessionDetail = Omit<Schema<'SessionDetail'>, 'messages'> & { messages: ChatBubble[] }

const q = encodeURIComponent

export function listSessions(contextId = 'global', mode?: ChatMode): Promise<SessionMeta[]> {
  const m = mode ? `&mode=${q(mode)}` : ''
  return getJSON(`/api/sessions?context_id=${q(contextId)}${m}`)
}

export function readSession(id: string, contextId = 'global'): Promise<SessionDetail> {
  return getJSON(`/api/sessions/${q(id)}?context_id=${q(contextId)}`)
}

// Delete a session — one tier only (session-deletion-trace-model): a hard delete of its row + its
// transcript JSONL from disk (irreversible; it can't be reworked). Its run + token trace is
// PRESERVED server-side and labeled `session_fate='deleted'`.
export function deleteSession(id: string, contextId = 'global'): Promise<void> {
  return sendJSON(`/api/sessions/${q(id)}?context_id=${q(contextId)}`, 'DELETE')
}
