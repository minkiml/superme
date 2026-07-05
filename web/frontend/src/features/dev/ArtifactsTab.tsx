import { useEffect, useState } from 'react'
import { ScrollText, Loader2, Bot, Sparkles, Pencil, Save, X } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import Toggle from '@/ui/Toggle'
import TabBar from '@/ui/TabBar'
import {
  getConstitutions, toggleConstitution, getLocalPlugins, getHarnessFile, saveHarnessFile,
  type ManagedConstitution, type HarnessEntry,
} from '@/lib/api'
import { Empty } from './common'

// Drop the YAML frontmatter block for the preview (edit mode keeps the raw file).
function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

// Artifacts — a host's OWN local-harness operational artifacts (Dev workspace tab, after Learning).
// Mirrors Foundations' universal artifact management (Constitution / Skills / Agents), but scoped to
// THIS host's local harness. No dev/core split: the dev workspace is already mode-scoped. Disabling a
// constitution flips the `enabled` flag the always-on catalog and `pull_constitution` both honor.

type Sub = 'constitution' | 'skills' | 'agents'

export default function ArtifactsTab({ contextId }: { contextId: string }) {
  const [sub, setSub] = useState<Sub>('constitution')
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
        <TabBar
          className="mb-5"
          variant="outlined"
          value={sub}
          onChange={setSub}
          tabs={[['constitution', 'Constitution'], ['skills', 'Skills'], ['agents', 'Agents']] as const}
        />
        {sub === 'constitution' && <ConstitutionSection contextId={contextId} />}
        {sub === 'skills' && <PluginSection contextId={contextId} kind="skill" />}
        {sub === 'agents' && <PluginSection contextId={contextId} kind="agent" />}
      </div>
    </div>
  )
}

// --- Constitution (enable/disable local constitutions) --------------------------------------
function ConstitutionSection({ contextId }: { contextId: string }) {
  const [items, setItems] = useState<ManagedConstitution[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  function load() {
    getConstitutions(contextId)
      .then((d) => setItems(d.constitutions.filter((c) => c.origin === 'repo')))
      .catch((e) => setErr(String(e)))
  }
  useEffect(load, [contextId])

  if (err) return <div className="text-sm text-danger">Couldn’t load constitutions — {err}</div>
  if (items === null) return <Loading />
  if (items.length === 0) return <Empty>No local constitutions for this host yet — forge one, and it lands here.</Empty>
  return (
    <div className="space-y-2">
      {items.map((c) => (
        <ConstitutionRow key={c.slug} c={c} contextId={contextId} onToggled={load} />
      ))}
    </div>
  )
}

function ConstitutionRow({ c, contextId, onToggled }: { c: ManagedConstitution; contextId: string; onToggled: () => void }) {
  const [open, setOpen] = useState(false)
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
        <button onClick={() => setOpen((o) => !o)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <span className="text-[10px] font-medium uppercase tracking-wider text-dev">local</span>
          <span className="min-w-0 flex-1 truncate text-[14px] text-fg">{c.title}</span>
          {!c.enabled && <span className="text-[10px] uppercase tracking-wide text-faint">disabled</span>}
        </button>
        <Toggle on={c.enabled} onChange={toggle} onColor="bg-dev" disabled={busy} title={c.enabled ? 'Disable' : 'Enable'} />
      </div>
      {open && (
        <div className="border-t border-line px-3.5 py-3 text-[13px] text-muted">
          {c.description && <p className="mb-2 text-[12px] italic text-faint">{c.description}</p>}
          <Markdown text={c.body} variant="doc" />
        </div>
      )}
    </div>
  )
}

// --- Skills / Agents (preview + edit local plugin files) ------------------------------------
function PluginSection({ contextId, kind }: { contextId: string; kind: 'skill' | 'agent' }) {
  const [entries, setEntries] = useState<HarnessEntry[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<HarnessEntry | null>(null)

  function load() {
    getLocalPlugins(contextId)
      .then((d) => setEntries(kind === 'skill' ? d.skills : d.agents))
      .catch((e) => setErr(String(e)))
  }
  useEffect(load, [contextId, kind])

  if (err) return <div className="text-sm text-danger">Couldn’t load {kind}s — {err}</div>
  if (entries === null) return <Loading />
  if (entries.length === 0) return <Empty>No local {kind}s for this host yet.</Empty>
  return (
    <div className="space-y-2">
      {entries.map((e) => (
        <button
          key={e.name}
          onClick={() => setOpen(e)}
          className="group flex w-full items-center gap-2 rounded-lg border border-line bg-surface p-3 text-left transition hover:border-accent hover:bg-hover"
        >
          {kind === 'agent' ? <Bot size={14} className="text-muted" /> : <Sparkles size={14} className="text-muted" />}
          <span className="min-w-0 shrink-0 truncate font-mono text-sm text-fg">{e.name}</span>
          {e.category && (
            <span className="shrink-0 rounded bg-dev/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-dev">{e.category}</span>
          )}
          <span className="min-w-0 flex-1 truncate text-[12px] text-faint">{e.description}</span>
          <Pencil size={12} className="ml-auto shrink-0 text-faint opacity-0 transition group-hover:opacity-100" />
        </button>
      ))}
      {open && <LocalFileModal contextId={contextId} entry={open} onClose={() => setOpen(null)} />}
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
