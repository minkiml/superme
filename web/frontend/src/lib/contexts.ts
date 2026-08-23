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

// Who a chat is addressed to. A label that already names SuperMe must not have it appended —
// the hub's is "SuperMe hub", and "Talk to SuperMe hub SuperMe" is the first sentence a fresh
// install shows.
export const addressee = (label: string) => (/superme/i.test(label) ? label : `${label} SuperMe`)
