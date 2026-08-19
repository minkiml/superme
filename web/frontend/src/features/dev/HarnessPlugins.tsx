import { useCallback, useEffect, useState } from 'react'
import { Bot, Loader2, Pencil, Sparkles, X } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import SourceEditor from '@/ui/SourceEditor'
import { useEditGate, EditActions } from '@/ui/EditGate'
import { getHarnessPlugins, getHarnessFile, saveHarnessFile, type HarnessScope, type HarnessEntry, type PublishedItem } from '@/lib/api'
import { PublishedFileModal } from './LearningGovernance'

// SuperMe's OWN universal skills & agents inventory (WI-8) — hosted in Foundations. Groups the
// shipped harness plugins by loading scope (Dev / Core / Shared) and sub-groups by `category`;
// learned+published items pull into a distinct "LEARNED" group and open the published-artifact
// governor (PublishedFileModal, owned by LearningGovernance). Was split out of the old ManageHarness.
// --- Skills & Agents (WI-8) -----------------------------------------------------------------
// Inventory of SuperMe's OWN universal skills + agents, grouped by the scope that loads them
// (Dev / Core / Shared) and sub-grouped by the `category` frontmatter field. Click a row to open
// a preview/edit popup over its source. Per-repo operational artifacts are deliberately excluded.
const CATEGORY_FALLBACK = 'uncategorized'

// Scope column order + tint — matches the Identity & charters ordering (UNIVERSAL · DEV · CORE):
// shared reads as the universal scope (purple), dev = blue, core = green.
const SCOPE_ORDER: Record<string, number> = { shared: 0, dev: 1, core: 2 }
const SCOPE_TONE: Record<string, 'universal' | 'dev' | 'core'> = { shared: 'universal', dev: 'dev', core: 'core' }
// Category chip tint per scope — matches Identity & charters (shared = purple, dev = blue, core = green).
const SCOPE_CHIP: Record<string, string> = {
  shared: 'bg-universal/10 text-universal',
  dev: 'bg-dev/10 text-dev',
  core: 'bg-core/10 text-core',
}

// For the preview, drop the YAML frontmatter block (it's shown as chips in the header + editable in
// edit mode) so the rendered markdown is just the body.
function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

// Learned+published items are pulled out of their normal category into a single amber "LEARNED"
// group (regardless of scope), so learned machinery stands apart from the shipped harness.
const LEARNED_CAT = 'LEARNED'

export function HarnessPlugins({ only, learned, publishedByKey, onGovernanceChange }: {
  only?: 'skill' | 'agent'
  learned?: Set<string>
  publishedByKey?: Map<string, PublishedItem> // `${kind}:${name}` → published item (enables edit/toggle/delete)
  onGovernanceChange?: () => void             // parent reloads its published/learned state after a toggle/delete
} = {}) {
  const [scopes, setScopes] = useState<HarnessScope[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<{ scope: string; entry: HarnessEntry } | null>(null)
  const [openPub, setOpenPub] = useState<PublishedItem | null>(null) // a learned item opened for govern/edit

  const load = useCallback(() => {
    getHarnessPlugins()
      .then((d) => setScopes(d.scopes))
      .catch((e) => setErr(String(e)))
  }, [])
  useEffect(() => { load() }, [load])

  const isLearned = (e: HarnessEntry) => learned?.has(`${e.kind}:${e.name}`) ?? false
  const govChanged = () => { load(); onGovernanceChange?.() }

  if (err) return <div className="text-sm text-danger">Could not load: {err}</div>
  if (!scopes) return <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>

  return (
    <div className="space-y-4">
      {!only && (
        <p className="text-sm text-muted">
          SuperMe's own skills and agents, grouped by the scope that loads them and labelled by
          category. These ship with the harness (universal) — per-project additions aren't shown here.
          Click any one to preview or edit it.
        </p>
      )}
      {/* One column per scope (Dev · Core · Shared) rather than a long vertical list. */}
      <div className="grid cols-mid gap-4">
        {[...scopes].sort((a, b) => (SCOPE_ORDER[a.scope] ?? 9) - (SCOPE_ORDER[b.scope] ?? 9)).map((s) => {
          const entries = only === 'agent' ? [...s.agents] : only === 'skill' ? [...s.skills] : [...s.agents, ...s.skills]
          // Group by category — learned items collapse into the LEARNED bucket.
          const byCat = new Map<string, HarnessEntry[]>()
          for (const e of entries) {
            const cat = isLearned(e) ? LEARNED_CAT : (e.category || CATEGORY_FALLBACK)
            if (!byCat.has(cat)) byCat.set(cat, [])
            byCat.get(cat)!.push(e)
          }
          // Order: real categories (alpha), then uncategorized, then LEARNED last.
          const rank = (c: string) => (c === LEARNED_CAT ? 3 : c === CATEGORY_FALLBACK ? 2 : 1)
          const cats = [...byCat.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
          return (
            <section key={s.scope} className="rounded-xl border border-line bg-surface p-3.5">
              <div className="mb-3">
                <h2 className="text-[13px] font-semibold text-fg">{s.label}</h2>
                <span className="text-[11px] text-faint">{s.note}</span>
              </div>
              {entries.length === 0 ? (
                <p className="text-[12px] text-faint">None in this scope.</p>
              ) : (
                <div className="space-y-3">
                  {cats.map((cat) => {
                    const chipCls = cat === LEARNED_CAT
                      ? 'bg-warn/15 text-warn'
                      : (SCOPE_CHIP[s.scope] ?? 'bg-accent-soft text-accent-text')
                    return (
                      <div key={cat}>
                        <div className="mb-1.5 flex items-center gap-2">
                          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${chipCls}`}>
                            {cat}
                          </span>
                          <span className="text-[10px] text-faint">{byCat.get(cat)!.length}</span>
                        </div>
                        <div className="space-y-2">
                          {byCat.get(cat)!
                            .sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'agent' ? -1 : 1))
                            .map((e) => {
                              const pub = publishedByKey?.get(`${e.kind}:${e.name}`)
                              return (
                                <HarnessRow
                                  key={`${e.kind}-${e.name}`} entry={e} showKind={!only}
                                  onClick={() => (pub ? setOpenPub(pub) : setOpen({ scope: s.scope, entry: e }))}
                                />
                              )
                            })}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </section>
          )
        })}
      </div>
      {open && <HarnessFileModal scope={open.scope} entry={open.entry} onClose={() => setOpen(null)} />}
      {openPub && (
        <PublishedFileModal
          item={openPub} contextId="global" showScope={false}
          onClose={() => setOpenPub(null)}
          onSaved={() => { setOpenPub(null); govChanged() }}
          onGovernanceChange={govChanged}
        />
      )}
    </div>
  )
}

function HarnessRow({ entry, onClick, showKind = true, learned = false }: { entry: HarnessEntry; onClick: () => void; showKind?: boolean; learned?: boolean }) {
  const isAgent = entry.kind === 'agent'
  const Icon = isAgent ? Bot : Sparkles
  return (
    <button
      onClick={onClick}
      className="group w-full rounded-lg border border-line bg-surface p-3 text-left transition hover:border-accent hover:bg-hover"
    >
      <div className="flex items-center gap-2">
        <Icon size={14} className="text-muted" />
        <span className="min-w-0 truncate font-mono text-sm text-fg">{entry.name}</span>
        {learned && (
          <span className="shrink-0 rounded bg-warn/15 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-warn" title="Learned + published by the learning loop">
            learned
          </span>
        )}
        {showKind && (
          <span className="rounded bg-hover px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-faint group-hover:bg-surface">
            {entry.kind}
          </span>
        )}
        {isAgent && entry.model && <span className="text-[11px] text-faint">{entry.model}</span>}
        <Pencil size={12} className="ml-auto shrink-0 text-faint opacity-0 transition group-hover:opacity-100" />
      </div>
    </button>
  )
}

// Preview + edit one skill/agent's raw markdown. Loads the file, renders it; "Edit" swaps to a
// textarea and "Save" writes it back (takes effect on the next dev turn).
function HarnessFileModal({ scope, entry, onClose }: { scope: string; entry: HarnessEntry; onClose: () => void }) {
  const [content, setContent] = useState<string | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const gate = useEditGate({
    saved: content ?? '',
    commit: async (d) => { await saveHarnessFile(scope, entry.kind, entry.name, d); setContent(d) },
  })
  const { editing, draft } = gate
  const err = gate.err ?? loadErr

  useEffect(() => {
    let alive = true
    getHarnessFile(scope, entry.kind, entry.name)
      .then((f) => { if (alive) setContent(f.content) })
      .catch((e) => alive && setLoadErr(String(e)))
    return () => { alive = false }
  }, [scope, entry.kind, entry.name])

  const Icon = entry.kind === 'agent' ? Bot : Sparkles
  return (
    <Modal onClose={onClose} column maxW={editing ? "max-w-4xl" : "max-w-3xl"} dismissable={!editing}>
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-3">
          <Icon size={15} className="text-accent-text" />
          <span className="font-mono text-sm text-fg">{entry.name}</span>
          <span className="rounded bg-hover px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-faint">{entry.kind}</span>
          {entry.category && (
            <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-accent-text">{entry.category}</span>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            <EditActions gate={gate} readOnly={content === null} />
            <button onClick={onClose} className="rounded p-1 text-muted hover:bg-hover hover:text-fg">
              <X size={16} />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {err && <div className="mb-2 text-sm text-danger">{err}</div>}
          {content === null ? (
            <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>
          ) : editing ? (
            <SourceEditor value={draft} onChange={gate.setDraft} />
          ) : (
            <Markdown text={stripFrontmatter(content)} variant="doc" tone={SCOPE_TONE[scope]} />
          )}
        </div>
    </Modal>
  )
}
