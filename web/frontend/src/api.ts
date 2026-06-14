// All calls go to the same-origin /api surface (Vite proxies to the BFF → daemon).

export type TreeNode = {
  name: string
  type: 'dir' | 'file'
  path: string
  children?: TreeNode[]
}

export async function getTree(contextId = 'global'): Promise<TreeNode> {
  const r = await fetch(`/api/knowledge/tree?context_id=${encodeURIComponent(contextId)}`)
  if (!r.ok) throw new Error(`tree: ${r.status}`)
  return r.json()
}

export async function readFile(path: string, contextId = 'global'): Promise<string> {
  const r = await fetch(
    `/api/knowledge/file?path=${encodeURIComponent(path)}&context_id=${encodeURIComponent(contextId)}`,
  )
  if (!r.ok) throw new Error(`read: ${r.status}`)
  const j = await r.json()
  return j.content
}

export async function writeFile(path: string, content: string, contextId = 'global'): Promise<void> {
  const r = await fetch('/api/knowledge/file', {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ path, content, context_id: contextId }),
  })
  if (!r.ok) throw new Error(`write: ${r.status}`)
}

export async function injectNote(
  title: string,
  content: string,
  folder = 'knowledge',
  contextId = 'global',
): Promise<{ path: string }> {
  const r = await fetch('/api/knowledge/inject', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title, content, folder, context_id: contextId }),
  })
  if (!r.ok) throw new Error(`inject: ${r.status}`)
  return r.json()
}

export function agentSocketUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/ws/agent`
}

// --- sessions: list + replay (history comes from the SDK transcripts) ----------

export type SessionMeta = {
  id: string
  title: string
  surface: 'slack' | 'web'
  updated_at: string
  message_count: number
}

export type ChatBubble = { role: 'you' | 'superme'; text: string }

export async function listSessions(contextId = 'global'): Promise<SessionMeta[]> {
  const r = await fetch(`/api/sessions?context_id=${encodeURIComponent(contextId)}`)
  if (!r.ok) throw new Error(`sessions: ${r.status}`)
  return r.json()
}

export async function readSession(
  id: string,
  contextId = 'global',
): Promise<{ id: string; title: string; messages: ChatBubble[]; total: number; truncated: boolean }> {
  const r = await fetch(
    `/api/sessions/${encodeURIComponent(id)}?context_id=${encodeURIComponent(contextId)}`,
  )
  if (!r.ok) throw new Error(`session: ${r.status}`)
  return r.json()
}

export async function deleteSession(id: string, contextId = 'global'): Promise<void> {
  const r = await fetch(
    `/api/sessions/${encodeURIComponent(id)}?context_id=${encodeURIComponent(contextId)}`,
    { method: 'DELETE' },
  )
  if (!r.ok) throw new Error(`delete session: ${r.status}`)
}
