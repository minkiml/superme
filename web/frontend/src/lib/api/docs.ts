// SuperMe Docs — the markdown reference under superme-docs/. `overview` is README.md.
import { getJSON } from './client'
import type { Schema } from './generated'

// Transport types, derived from the daemon OpenAPI (regenerate with `npm run gen:api`).
export type DocMeta = Schema<'DocSummary'>
export type DocsList = Schema<'DocsListResponse'>
export type Doc = Schema<'DocResponse'>

export function listDocs(): Promise<DocsList> {
  return getJSON('/api/docs')
}
export function readDoc(slug: string): Promise<Doc> {
  return getJSON(`/api/docs/${encodeURIComponent(slug)}`)
}
