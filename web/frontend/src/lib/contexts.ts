// Live contexts (SuperMes the daemon can resolve): global "Me" + connected domains.
//
// The list is fetched from the daemon (GET /api/contexts), which reads the workspace
// registry — so the FE never drifts from the backend. `global` is always present (used
// as the seed before the fetch returns); domains append once projects are connected and
// bridged into global knowledge. App owns the fetched list and threads it everywhere
// that needs it (chat rail, Manage Knowledge, Domains).

export type ContextRef = {
  id: string // daemon context_id (knowledge root, sessions, chat all key off this)
  label: string // display name
  layer?: 'global' | 'local'
  cwd?: string // working dir (for the domain overview scene)
}

export const GLOBAL: ContextRef = { id: 'global', label: 'Me', layer: 'global' }
