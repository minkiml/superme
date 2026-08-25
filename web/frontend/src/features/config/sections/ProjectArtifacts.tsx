import { useEffect, useState } from 'react'
import { ScrollText, Loader2, Bot, Sparkles, Pencil, X, Plus, Trash2, Check, ShieldCheck, ClipboardCheck, ArrowUp, ArrowDown, Gavel, ChevronRight, Package } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import Toggle from '@/ui/Toggle'
import ArtifactTabs from '@/ui/ArtifactTabs'
import SourceEditor from '@/ui/SourceEditor'
import { useEditGate, EditActions } from '@/ui/EditGate'
import {
  getConstitutions, toggleConstitution, getLocalPlugins, getHarnessFile, saveHarnessFile,
  getAssets, assetAction, getDeputyMandate, saveDeputyMandate, type AssetItem, type AssetAction,
  getVerificationLibrary, moveLibraryEntry, dropLibraryEntry, type LibraryEntry,
  getDecisions, type DecisionEntry,
  type ManagedConstitution, type HarnessEntry,
} from '@/lib/api'
import ScopeColumns, { type ScopeCard } from '@/ui/ScopeColumns'
import ConstitutionModal from '@/features/dev/ConstitutionModal'
import { Empty } from '@/features/dev/common'
import { PaneHead } from '../controls'

// A host's OWN local-harness artifacts, mirroring the universal surface but scoped to this host.
//
// No mode split: the dev workspace is already mode-scoped.

// Drop the YAML frontmatter block for the preview (edit mode keeps the raw file).
function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

type Sub = 'constitution' | 'skills' | 'agents' | 'verification' | 'decisions' | 'deputy'

export default function ProjectArtifacts({ contextId, repoLabel }: { contextId: string; repoLabel: string }) {
  const [sub, setSub] = useState<Sub>('constitution')
  const [consts, setConsts] = useState<ManagedConstitution[] | null>(null)
  const [assets, setAssets] = useState<AssetItem[] | null>(null)
  const [skills, setSkills] = useState<HarnessEntry[] | null>(null)
  const [agents, setAgents] = useState<HarnessEntry[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [openConst, setOpenConst] = useState<ManagedConstitution | null>(null)
  const [openPlugin, setOpenPlugin] = useState<HarnessEntry | null>(null)
  const [library, setLibrary] = useState<LibraryEntry[] | null>(null)
  const [decisions, setDecisions] = useState<DecisionEntry[] | null>(null)

  function load() {
    getVerificationLibrary(contextId)
      .then((r) => setLibrary([...r.standing, ...r.available]))
      .catch((e) => setErr(String(e)))
    getDecisions(contextId)
      .then((r) => setDecisions(r.decisions))
      .catch((e) => setErr(String(e)))
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
    <>
      <PaneHead
        title="Artifacts"
        lede="This project's own artifacts. Toggle what loads, or open one to edit."
      />
    <ArtifactTabs
        className="mb-5"
        tint="dev"
        value={sub}
        onChange={setSub}
        tabs={[
          { key: 'constitution', label: 'Constitution', icon: ScrollText, count: consts?.length ?? null },
          { key: 'skills', label: 'Skills', icon: Sparkles, count: skills?.length ?? null },
          { key: 'agents', label: 'Agents', icon: Bot, count: agents?.length ?? null },
          { key: 'verification', label: 'Verification', icon: ClipboardCheck, count: library?.length ?? null },
          { key: 'decisions', label: 'Decisions', icon: Gavel, count: decisions?.length ?? null },
          { key: 'deputy', label: 'Deputy', icon: ShieldCheck, count: null },
        ]}
      />
      {err && <div className="mb-3 text-sm text-danger">Couldn’t load — {err}</div>}

      {sub === 'constitution' && (
        consts === null || assets === null ? <Loading /> : (
          <ScopeColumns
            columns={[
              {
                key: 'local',
                name: 'Local',
                note: 'Forged for this host',
                tint: 'dev',
                icon: ScrollText,
                empty: 'No local constitutions yet. Forge one and it lands here.',
                groups: [{ cards: consts.map((c) => constitutionCard(c, contextId, load, setOpenConst)) }],
              },
              {
                key: 'expertise',
                name: 'Expertise',
                note: 'Adopted for this repo from the shared pool',
                tint: 'universal',
                icon: Package,
                empty: 'No expertise adopted for this repo yet.',
                action: <AddAsset pool={assets.filter((a) => !(a.adopted && onOffer(a)))} contextId={contextId} onAdded={load} />,
                groups: [{ cards: assets.filter((a) => a.adopted && onOffer(a)).map((a) => assetCard(a, contextId, load)) }],
              },
            ]}
          />
        )
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
      {sub === 'verification' && (
        <div className="space-y-6">
          <p className="text-[12px] text-faint">
            Checks this project has proven. <b className="text-muted">Standing</b> ones are attached to every plan.
            The rest are cited by name when they fit. Promoting is yours.
          </p>
          {(['standing', 'available'] as const).map((tier) => (
            <section key={tier}>
              <SectionLabel
                title={tier === 'standing' ? 'Standing' : 'Available'}
                hint={tier === 'standing' ? 'Attached to every plan in this repo' : 'Cited by name when it fits'}
              />
              <ListOrState
                list={library ? library.filter((e) => e.tier === tier) : null}
                empty={tier === 'standing'
                  ? 'Nothing standing yet. No check is charged to every item here.'
                  : 'Nothing yet. Entries land here when close writes in what vet nominated.'}
              >
                {(items) => (
                  <div className="space-y-2">
                    {items.map((e) => <LibraryRow key={e.id} e={e} contextId={contextId} onChanged={load} />)}
                  </div>
                )}
              </ListOrState>
            </section>
          ))}
        </div>
      )}
      {sub === 'decisions' && (
        <div className="space-y-4">
          <p className="text-[12px] text-faint">
            Calls you ruled on, newest first. Every entry here is yours, never an agent's.
            Later runs read this before asking, so the same question stops coming back.
          </p>
          <ListOrState
            list={decisions}
            empty="Nothing ruled yet. A decision lands here when you answer at a gate."
          >
            {(items) => (
              <div className="space-y-2">
                {items.map((d) => <DecisionRow key={d.id} d={d} />)}
              </div>
            )}
          </ListOrState>
        </div>
      )}
      {sub === 'deputy' && <DeputyPanel contextId={contextId} />}

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
    </>
  )
}

// A local constitution as one card: click to preview, switch to control whether it loads.
function constitutionCard(
  c: ManagedConstitution,
  contextId: string,
  reload: () => void,
  open: (c: ManagedConstitution) => void,
): ScopeCard {
  return {
    key: c.slug,
    name: c.title,
    sub: c.description || undefined,
    onClick: () => open(c),
    trailing: (
      <Toggle
        on={c.enabled}
        onChange={(v) => { toggleConstitution(c.slug, c.scope, v, contextId).then(reload).catch(() => {}) }}
        onColor="bg-dev"
        title={c.enabled ? 'Disable' : 'Enable'}
      />
    ),
  }
}

// A withdrawn or restricted asset has no working toggle, so it leaves the adopted list and greys in
// the picker. Its adoption record survives and reappears the moment the shelf offers it again.
function onOffer(a: AssetItem): boolean {
  return a.available && !a.restricted
}

function offerNote(a: AssetItem): string {
  if (!a.available) return 'Turned off for every project'
  return 'Reserved for SuperMe itself'
}

// An adopted pool asset. Disabling keeps it adopted; Drop un-adopts it and returns it to the picker.
function assetCard(a: AssetItem, contextId: string, reload: () => void): ScopeCard {
  const act = (action: AssetAction) => { assetAction(a.slug, action, contextId).then(reload).catch(() => {}) }
  return {
    key: a.slug,
    name: a.title,
    sub: a.description || undefined,
    badges: <span className="shrink-0 font-mono text-[10px] text-faint">{a.slug}</span>,
    trailing: (
      <span className="flex items-center gap-1.5">
        <button
          onClick={() => act('drop')}
          title="Drop. Un-adopts it for this project."
          className="rounded p-1 text-faint hover:bg-hover hover:text-danger"
        >
          <Trash2 size={13} />
        </button>
        <Toggle
          on={a.enabled}
          onChange={(v) => act(v ? 'enable' : 'disable')}
          onColor="bg-universal"
          title={a.enabled ? 'Disable' : 'Enable'}
        />
      </span>
    ),
  }
}

function SectionLabel({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="mb-2 flex items-baseline gap-2">
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted">{title}</h2>
      <span className="text-[11px] text-faint">{hint}</span>
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
              const off = !onOffer(a)
              return (
                <button key={a.slug} onClick={() => toggle(a.slug)} disabled={off} title={off ? offerNote(a) : undefined} className={`flex w-full items-start gap-2.5 rounded-lg border px-3 py-2 text-left ${off ? 'cursor-not-allowed border-line bg-surface opacity-50' : on ? 'border-dev bg-hover' : 'border-line bg-surface hover:bg-hover'}`}>
                  <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${on ? 'border-dev bg-dev text-white' : 'border-line'}`}>
                    {on && <Check className="h-3 w-3" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-[13px] text-fg">{a.title}</span>
                      <span className="font-mono text-[10px] text-faint">{a.slug}</span>
                      {off && <span className="shrink-0 rounded border border-line px-1 text-[10px] text-faint">{offerNote(a)}</span>}
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

// Promote and demote is the owner's only lever over what every future plan inherits, so it sits on
// the row itself.
function DecisionRow({ d }: { d: DecisionEntry }) {
  const [open, setOpen] = useState(false)
  const live = d.status === 'accepted'
  return (
    <div className="rounded-lg border border-line bg-surface">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left hover:bg-hover"
      >
        <ChevronRight size={12} className={`shrink-0 text-faint transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="shrink-0 font-mono text-[13px] text-fg">{d.id}</span>
        <span className="min-w-0 flex-1 truncate text-[12.5px] text-fg">{d.title}</span>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
          live ? 'bg-success/10 text-success' : 'bg-hover text-faint'}`}>
          {live ? 'accepted' : d.status}
        </span>
      </button>
      {open && (
        <div className="border-t border-line px-3.5 py-2.5 text-[12px]">
          <Markdown text={d.body} tone="dev" />
        </div>
      )}
    </div>
  )
}

function LibraryRow({ e, contextId, onChanged }: { e: LibraryEntry; contextId: string; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const standing = e.tier === 'standing'
  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    try { await fn(); onChanged() } finally { setBusy(false) }
  }
  const Move = standing ? ArrowDown : ArrowUp
  return (
    <div className="rounded-lg border border-line bg-surface px-3.5 py-2.5">
      <div className="flex items-center gap-2">
        <span className="shrink-0 font-mono text-[13px] text-fg">{e.id}</span>
        {e.mode && <span className="shrink-0 rounded bg-dev/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-dev">{e.mode}</span>}
        <span className="min-w-0 flex-1 truncate text-[12px] text-faint">{e.scenario || e.traces}</span>
        <button
          onClick={() => act(() => moveLibraryEntry(e.id, standing ? 'available' : 'standing', contextId))}
          disabled={busy}
          title={standing ? 'Demote to available' : 'Promote to standing. Every later plan carries it.'}
          className="flex shrink-0 items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
        >
          {busy ? <Loader2 size={11} className="animate-spin" /> : <Move size={11} />}
          {standing ? 'Demote' : 'Promote'}
        </button>
        <button
          onClick={() => act(() => dropLibraryEntry(e.id, contextId))}
          disabled={busy}
          title="Drop. It did not generalise."
          className="shrink-0 rounded-md border border-line p-1 text-faint hover:border-danger hover:text-danger disabled:opacity-50"
        >
          <Trash2 size={12} />
        </button>
      </div>
      {e.run && <div className="mt-1.5 truncate font-mono text-[11px] text-faint">$ {e.run}</div>}
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
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const gate = useEditGate({
    saved: content ?? '',
    commit: async (d) => {
      await saveHarnessFile('local', entry.kind, entry.name, d, contextId)
      setContent(d)
    },
  })
  const { editing, draft } = gate
  const err = gate.err ?? loadErr

  useEffect(() => {
    let alive = true
    getHarnessFile('local', entry.kind, entry.name, contextId)
      .then((f) => { if (alive) setContent(f.content) })
      .catch((e) => alive && setLoadErr(String(e)))
    return () => { alive = false }
  }, [contextId, entry.kind, entry.name])

  const Icon = entry.kind === 'agent' ? Bot : Sparkles
  return (
    <Modal onClose={onClose} column maxW="max-w-3xl" dismissable={!editing}>
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-3">
        <Icon size={15} className="text-dev" />
        <span className="font-mono text-sm text-fg">{entry.name}</span>
        <span className="rounded bg-hover px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-faint">{entry.kind}</span>
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
          <Loading />
        ) : editing ? (
          <SourceEditor value={draft} onChange={gate.setDraft} />
        ) : (
          <Markdown text={stripFrontmatter(content)} variant="doc" tone="dev" />
        )}
      </div>
    </Modal>
  )
}

// This repo's standing acceptance bar. One file, so an inline preview and edit panel rather than a
// list.
function DeputyPanel({ contextId }: { contextId: string }) {
  const [content, setContent] = useState<string | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const gate = useEditGate({
    saved: content ?? '',
    commit: async (d) => { await saveDeputyMandate(d, contextId); setContent(d) },
  })
  const { editing, draft } = gate
  const err = gate.err ?? loadErr

  useEffect(() => {
    let alive = true
    setContent(null); gate.close()
    getDeputyMandate(contextId)
      .then((d) => { if (alive) setContent(d.content) })
      .catch((e) => alive && setLoadErr(String(e)))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextId])

  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted">Mandate</h2>
          <span className="text-[11px] text-faint">the standing bar the deputy judges gates against while you’re away</span>
        </div>
        <div className="flex items-center gap-1.5">
          <EditActions gate={gate} tone="dev" readOnly={content === null} />
        </div>
      </div>
      {err && <div className="mb-2 text-sm text-danger">{err}</div>}
      {content === null ? (
        <Loading />
      ) : editing ? (
        <SourceEditor value={draft} onChange={gate.setDraft} tone="dev" className="rounded-lg" />
      ) : (
        <div className="rounded-lg border border-line bg-surface px-4 py-3">
          <Markdown text={stripFrontmatter(content)} variant="doc" tone="dev" />
        </div>
      )}
      <p className="mt-2 text-[11px] text-faint">
        Read alongside <span className="font-mono">project-prd.md</span> — the deliverables’ success signals are the real bar; this adds what the PRD can’t say. Effective on the next deputy dispatch.
      </p>
    </section>
  )
}

function Loading() {
  return (
    <div className="flex items-center gap-2 text-sm text-muted">
      <Loader2 size={14} className="animate-spin" /> Loading…
    </div>
  )
}
