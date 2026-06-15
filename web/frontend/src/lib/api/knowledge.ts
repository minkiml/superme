import { getJSON, sendJSON } from './client'

export type TreeNode = {
  name: string
  type: 'dir' | 'file'
  path: string
  children?: TreeNode[]
}

const q = encodeURIComponent

export function getTree(contextId = 'global'): Promise<TreeNode> {
  return getJSON(`/api/knowledge/tree?context_id=${q(contextId)}`)
}

export async function readFile(path: string, contextId = 'global'): Promise<string> {
  const j = await getJSON<{ content: string }>(
    `/api/knowledge/file?path=${q(path)}&context_id=${q(contextId)}`,
  )
  return j.content
}

export function writeFile(path: string, content: string, contextId = 'global'): Promise<void> {
  return sendJSON('/api/knowledge/file', 'PUT', { path, content, context_id: contextId })
}

export function injectNote(
  title: string,
  content: string,
  folder = 'knowledge',
  contextId = 'global',
): Promise<{ path: string }> {
  return sendJSON('/api/knowledge/inject', 'POST', { title, content, folder, context_id: contextId })
}
