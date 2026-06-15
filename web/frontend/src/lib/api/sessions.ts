import { getJSON, sendJSON } from './client'

// Sessions: list + replay. History comes from the SDK transcripts, not a parallel log.

export type SessionMeta = {
  id: string
  title: string
  surface: 'slack' | 'web'
  updated_at: string
  message_count: number
}

export type ChatBubble = { role: 'you' | 'superme'; text: string }

const q = encodeURIComponent

export function listSessions(contextId = 'global'): Promise<SessionMeta[]> {
  return getJSON(`/api/sessions?context_id=${q(contextId)}`)
}

export function readSession(
  id: string,
  contextId = 'global',
): Promise<{ id: string; title: string; messages: ChatBubble[]; total: number; truncated: boolean }> {
  return getJSON(`/api/sessions/${q(id)}?context_id=${q(contextId)}`)
}

export function deleteSession(id: string, contextId = 'global'): Promise<void> {
  return sendJSON(`/api/sessions/${q(id)}?context_id=${q(contextId)}`, 'DELETE')
}
