// The contexts the daemon can resolve, fetched from it so the FE never drifts from the registry.
//
// `global` is always present and seeds the list before the fetch returns.

export type ContextRef = {
  id: string // daemon context_id (knowledge root, sessions, chat all key off this)
  label: string // display name
  layer?: 'global' | 'local'
  cwd?: string // working dir (for the domain overview scene)
}

export const GLOBAL: ContextRef = { id: 'global', label: 'Me', layer: 'global' }
