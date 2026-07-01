// Ergonomic surface over the openapi-typescript output (`schema.ts`, never hand-edited).
//
// `schema.ts` is GENERATED from the daemon's OpenAPI — regenerate with `npm run gen:api`
// (the daemon must be running on :8787). It is the single source of truth for transport
// shapes; the hand-written `lib/api/*.ts` modules derive their types from it and layer
// FE-only narrowing / view types on top.
//
// Use `Schema<'WorkItem'>` instead of `components['schemas']['WorkItem']`.

import type { components } from './schema'

export type { components, paths } from './schema'

export type Schemas = components['schemas']
export type Schema<K extends keyof Schemas> = Schemas[K]
