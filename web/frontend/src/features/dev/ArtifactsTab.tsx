import { useEffect, useState } from 'react'
import { ScrollText, Loader2, Bot, Sparkles, Pencil, Save, X, Plus, Trash2, Check } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import Toggle from '@/ui/Toggle'
import ArtifactTabs from '@/ui/ArtifactTabs'
import {
  getConstitutions, toggleConstitution, getLocalPlugins, getHarnessFile, saveHarnessFile,
  getAssets, assetAction, type AssetItem, type AssetAction,
  type ManagedConstitution, type HarnessEntry,
} from '@/lib/api'
import ConstitutionModal from './ConstitutionModal'
import { Empty } from './common'

// Artifacts — a host's OWN local-harness operational artifacts (Dev workspace tab, after Learning).
// Mirrors Foundations' universal artifact management (Constitution / Skills / Agents) — same underline
// tabs, same popups, same toggle — but scoped to THIS host's local harness. No dev/core split: the
// dev workspace is already mode-scoped. Disabling a constitution flips the `enabled` flag the always-on
// catalog and `pull_constitution` both honor.

// Drop the YAML frontmatter block for the preview (edit mode keeps the raw file).
function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

type Sub = 'constitution' | 'skills' | 'agents'

export default function ArtifactsTab({ contextId }: { contextId: string }) {
  const [sub, setSub] = useState<Sub>('constitution')
  const [consts, setConsts] = useState<ManagedConstitution[] | null>(null)
  const [assets, setAssets] = useState<AssetItem[] | null>(null)
  const [skills, setSkills] = useState<HarnessEntry[] | null>(null)
  const [agents, setAgents] = useState<HarnessEntry[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [openConst, setOpenConst] = useState<ManagedConstitution | null>(null)
  const [openPlugin, setOpenPlugin] = useState<HarnessEntry | null>(null)

  function load() {
    getConstitutions(contextId)
      .then((d) => setConsts(d.constitutions.filter((c) => c.origin === 'repo')))
      .catch((e) => setErr(String(e)))
    getAssets(contextId)
      .then((r) => setAssets(r.assets))
      .catch((e) => setErr(String(e)))
    getLocalPlugins(contextId)
      .then((d) => { setSkills(d.skills); setAgents(d.agents) })
      .catch((e) => setErr(String(e)))
  }
  useEffect(load, [contextId])

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl p-6">
        <header className="mb-2 flex items-center gap-2.5">
          <ScrollText size={18} className="text-dev" />
          <h1 className="text-[17px] font-semibold text-fg">Artifacts</h1>
          <span className="text-[13px] text-faint">this host's own local operational artifacts</span>
        </header>
        <p className="mb-4 text-[12px] text-faint">
          Local to this host — enable/disable a constitution to control what loads; preview or edit any skill or agent.
        </p>
        <ArtifactTabs
          className="mb-5"
          tint="dev"
          value={sub}
          onChange={setSub}
          tabs={[
            { key: 'constitution', label: 'Constitution', icon: ScrollText, count: consts?.length ?? null },
            { key: 'skills', label: 'Skills', icon: Sparkles, count: skills?.length ?? null },
            { key: 'agents', label: 'Agents', icon: Bot, count: agents?.length ?? null },
          ]}
        />
        {err && <div className="mb-3 text-sm text-danger">Couldn’t load — {err}</div>}

        {sub === 'constitution' && (
          <div className="space-y-6">
            {/* Repo-authored — constitutions forged for this host */}
            <section>
              <SectionLabel title="Local" hint="Local (project) constitutions" />
              <ListOrState list={consts} empty="No local constitutions for this host yet — forge one, and it lands here.">
                {(items) => (
                  <div className="space-y-2">
                    {items.map((c) => (
                      <ConstitutionRow key={c.slug} c={c} contextId={contextId} onToggled={load} onOpen={() => setOpenConst(c)} />
                    ))}
                  </div>
                )}
              </ListOrState>
            </section>

            {/* Pooled knowledge — shared asset pool; onboarding auto-adopts, owner curates per-repo */}
            <section>
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="flex items-baseline gap-2">
                  <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted">Expertise</h2>
                  <span className="text-[11px] text-faint">Expertise adopted for this repo</span>
                </div>
                {assets && <AddAsset pool={assets.filter((a) => !a.adopted)} contextId={contextId} onAdded={load} />}
              </div>
              <ListOrState list={assets ? assets.filter((a) => a.adopted) : null} empty="No expertise adopted for this repo yet.">
                {(items) => (
                  <div className="space-y-2">
                    {items.map((a) => (
                      <AssetRow key={a.slug} it={a} contextId={contextId} onChanged={load} />
                    ))}
                  </div>
                )}
              </ListOrState>
            </section>
          </div>
        )}
        {sub === 'skills' && (
          <ListOrState list={skills} empty="No local skills for this host yet.">
            {(items) => <PluginRows entries={items} onOpen={setOpenPlugin} />}
          </ListOrState>
        )}
        {sub === 'agents' && (
          <ListOrState list={agents} empty="No local agents for this host yet.">
            {(items) => <PluginRows entries={items} onOpen={setOpenPlugin} />}
          </ListOrState>
        )}
      </div>

      {openConst && (
        <ConstitutionModal
          slug={openConst.slug}
          scope={openConst.scope}
          title={openConst.title}
          description={openConst.description}
          body={openConst.body}
          enabled={openConst.enabled}
          contextId={contextId}
          tint="dev"
          onClose={() => setOpenConst(null)}
          onToggled={load}
        />
      )}
      {openPlugin && <LocalFileModal contextId={contextId} entry={openPlugin} onClose={() => setOpenPlugin(null)} />}
    </div>
  )
}

function SectionLabel({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="mb-2 flex items-baseline gap-2">
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted">{title}</h2>
      <span className="text-[11px] text-faint">{hint}</span>
    </div>
  )
}

// A pooled knowledge asset ADOPTED by this repo (shared across repos, local-harness/asset/, no body
// copy). Enable/disable keeps it adopted; Drop un-adopts it (it returns to the + Add picker).
function AssetRow({ it, contextId, onChanged }: { it: AssetItem; contextId: string; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  async function act(action: AssetAction) {
    setBusy(true)
    try {
      await assetAction(it.slug, action, contextId)
      onChanged()
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className={`rounded-lg border border-line bg-surface ${it.enabled ? '' : 'opacity-60'}`}>
      <div className="flex items-start gap-2 px-3.5 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-medium uppercase tracking-wider text-dev">pool</span>
            <span className="min-w-0 truncate text-[14px] text-fg">{it.title}</span>
            <span className="font-mono text-[10px] text-faint">{it.slug}</span>
            {!it.enabled && <span className="text-[10px] uppercase tracking-wide text-faint">off</span>}
          </div>
          {it.description && <p className="mt-1 text-[12px] leading-relaxed text-muted">{it.description}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-3 pt-0.5">
          <button onClick={() => act('drop')} disabled={busy} title="Drop from this repo" className="text-faint hover:text-fg disabled:opacity-40">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
          <Toggle on={it.enabled} onChange={(v) => act(v ? 'enable' : 'disable')} onColor="bg-dev" disabled={busy} title={it.enabled ? 'Disable for this repo' : 'Enable for this repo'} />
        </div>
      </div>
    </div>
  )
}

// The + Add popup — batch-adopt un-adopted assets from the shared pool into this repo (enabled).
// A modal so it scales to a long pool: multi-select, then Add (N) or Cancel.
function AddAsset({ pool, contextId, onAdded }: { pool: AssetItem[]; contextId: string; onAdded: () => void }) {
  const [open, setOpen] = useState(false)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  function toggle(slug: string) {
    setSel((s) => { const n = new Set(s); n.has(slug) ? n.delete(slug) : n.add(slug); return n })
  }
  function close() { setOpen(false); setSel(new Set()) }
  async function add() {
    if (sel.size === 0) return
    setBusy(true)
    try {
      await Promise.all([...sel].map((slug) => assetAction(slug, 'adopt', contextId)))
      close()
      onAdded()
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <button onClick={() => setOpen(true)} className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-muted hover:text-fg" title="Add pooled assets">
        <Plus className="h-3 w-3" /> Add
      </button>
      {open && (
        <Modal onClose={close} title="Add expertise" maxW="max-w-lg" column>
          <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-3 py-3">
            {pool.length === 0 && (
              <div className="px-4 py-10 text-center text-[12px] text-muted">
                All pooled expertise is already adopted or no list is available for this project.
              </div>
            )}
            {pool.map((a) => {
              const on = sel.has(a.slug)
              return (
                <button key={a.slug} onClick={() => toggle(a.slug)} className={`flex w-full items-start gap-2.5 rounded-lg border px-3 py-2 text-left ${on ? 'border-dev bg-hover' : 'border-line bg-surface hover:bg-hover'}`}>
                  <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${on ? 'border-dev bg-dev text-white' : 'border-line'}`}>
                    {on && <Check className="h-3 w-3" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-[13px] text-fg">{a.title}</span>
                      <span className="font-mono text-[10px] text-faint">{a.slug}</span>
                    </span>
                    {a.description && <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{a.description}</p>}
                  </span>
                </button>
              )
            })}
          </div>
          <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
            <button onClick={close} disabled={busy} className="rounded-md px-3 py-1.5 text-[12px] text-muted hover:text-fg">Cancel</button>
            <button onClick={add} disabled={busy || sel.size === 0} className="rounded-md bg-dev px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-40">
              {busy ? 'Adding…' : `Add${sel.size ? ` (${sel.size})` : ''}`}
            </button>
          </div>
        </Modal>
      )}
    </>
  )
}

function ListOrState<T>({ list, empty, children }: { list: T[] | null; empty: string; children: (items: T[]) => React.ReactNode }) {
  if (list === null) return <Loading />
  if (list.length === 0) return <Empty>{empty}</Empty>
  return <>{children(list)}</>
}

function ConstitutionRow({ c, contextId, onToggled, onOpen }: { c: ManagedConstitution; contextId: string; onToggled: () => void; onOpen: () => void }) {
  const [busy, setBusy] = useState(false)
  async function toggle(v: boolean) {
    setBusy(true)
    try {
      await toggleConstitution(c.slug, c.scope, v, contextId)
      onToggled()
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className={`rounded-lg border border-line bg-surface ${c.enabled ? '' : 'opacity-60'}`}>
      <div className="flex items-center gap-2 px-3.5 py-2.5">
        <button onClick={onOpen} className="flex min-w-0 flex-1 items-center gap-2 text-left" title="Preview">
          <span className="text-[10px] font-medium uppercase tracking-wider text-dev">local</span>
          <span className="min-w-0 flex-1 truncate text-[14px] text-fg">{c.title}</span>
          {!c.enabled && <span className="text-[10px] uppercase tracking-wide text-faint">disabled</span>}
        </button>
        <Toggle on={c.enabled} onChange={toggle} onColor="bg-dev" disabled={busy} title={c.enabled ? 'Disable' : 'Enable'} />
      </div>
    </div>
  )
}

function PluginRows({ entries, onOpen }: { entries: HarnessEntry[]; onOpen: (e: HarnessEntry) => void }) {
  return (
    <div className="space-y-2">
      {entries.map((e) => (
        <button
          key={e.name}
          onClick={() => onOpen(e)}
          className="group flex w-full items-center gap-2 rounded-lg border border-line bg-surface p-3 text-left transition hover:border-accent hover:bg-hover"
        >
          {e.kind === 'agent' ? <Bot size={14} className="text-muted" /> : <Sparkles size={14} className="text-muted" />}
          <span className="min-w-0 shrink-0 truncate font-mono text-sm text-fg">{e.name}</span>
          {e.category && (
            <span className="shrink-0 rounded bg-dev/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-dev">{e.category}</span>
          )}
          <span className="min-w-0 flex-1 truncate text-[12px] text-faint">{e.description}</span>
          <Pencil size={12} className="ml-auto shrink-0 text-faint opacity-0 transition group-hover:opacity-100" />
        </button>
      ))}
    </div>
  )
}

// View + edit one local skill/agent's raw markdown (scope='local', keyed to this host).
function LocalFileModal({ contextId, entry, onClose }: { contextId: string; entry: HarnessEntry; onClose: () => void }) {
  const [content, setContent] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    getHarnessFile('local', entry.kind, entry.name, contextId)
      .then((f) => { if (alive) { setContent(f.content); setDraft(f.content) } })
      .catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [contextId, entry.kind, entry.name])

  async function save() {
    setBusy(true); setErr(null)
    try {
      await saveHarnessFile('local', entry.kind, entry.name, draft, contextId)
      setContent(draft)
      setEditing(false)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  const Icon = entry.kind === 'agent' ? Bot : Sparkles
  return (
    <Modal onClose={onClose} column maxW="max-w-3xl">
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-3">
        <Icon size={15} className="text-dev" />
        <span className="font-mono text-sm text-fg">{entry.name}</span>
        <span className="rounded bg-hover px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-faint">{entry.kind}</span>
        <div className="ml-auto flex items-center gap-1.5">
          {!editing ? (
            <button
              onClick={() => setEditing(true)}
              disabled={content === null}
              className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
            >
              <Pencil size={12} /> Edit
            </button>
          ) : (
            <>
              <button
                onClick={() => { setEditing(false); setDraft(content ?? '') }}
                disabled={busy}
                className="rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={save}
                disabled={busy || draft === content}
                className="flex items-center gap-1 rounded-md bg-accent px-2 py-1 text-xs text-on-accent hover:opacity-90 disabled:opacity-50"
              >
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} Save
              </button>
            </>
          )}
          <button onClick={onClose} className="rounded p-1 text-muted hover:bg-hover hover:text-fg">
            <X size={16} />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {err && <div className="mb-2 text-sm text-danger">{err}</div>}
        {content === null ? (
          <Loading />
        ) : editing ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            className="h-[60vh] w-full resize-none rounded-md border border-line bg-surface p-3 font-mono text-[12.5px] leading-relaxed text-fg outline-none focus:border-accent"
          />
        ) : (
          <Markdown text={stripFrontmatter(content)} variant="doc" tone="dev" />
        )}
      </div>
    </Modal>
  )
}

function Loading() {
  return (
    <div className="flex items-center gap-2 text-sm text-muted">
      <Loader2 size={14} className="animate-spin" /> Loading…
    </div>
  )
}
