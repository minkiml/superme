import { useEffect, useState } from 'react'
import { ScrollText, Loader2, Bot, Sparkles, Pencil, Save, X } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import Toggle from '@/ui/Toggle'
import ArtifactTabs from '@/ui/ArtifactTabs'
import {
  getConstitutions, toggleConstitution, getLocalPlugins, getHarnessFile, saveHarnessFile,
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
  const [skills, setSkills] = useState<HarnessEntry[] | null>(null)
  const [agents, setAgents] = useState<HarnessEntry[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [openConst, setOpenConst] = useState<ManagedConstitution | null>(null)
  const [openPlugin, setOpenPlugin] = useState<HarnessEntry | null>(null)

  function load() {
    getConstitutions(contextId)
      .then((d) => setConsts(d.constitutions.filter((c) => c.origin === 'repo')))
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
          <ListOrState list={consts} empty="No local constitutions for this host yet — forge one, and it lands here.">
            {(items) => (
              <div className="space-y-2">
                {items.map((c) => (
                  <ConstitutionRow key={c.slug} c={c} contextId={contextId} onToggled={load} onOpen={() => setOpenConst(c)} />
                ))}
              </div>
            )}
          </ListOrState>
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
