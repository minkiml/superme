import { useEffect, useState } from 'react'
import { Layers, FileText, X, Loader2, ScrollText, Pencil, Save, Sparkles, Bot } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import { getFoundation, saveFoundationFile, getPublished, getHarnessPlugins, type FoundationFile, type FoundationConstitution, type PublishedItem } from '@/lib/api'
import { HarnessPlugins } from '@/features/dev/HarnessPlugins'
import { Empty } from '@/features/dev/common'

// Foundations — SuperMe's repo-agnostic identity + machinery (Tier-2 nav). Sections: the
// hand-authored identity/charter files (SELF.md + per-mode charters, view + edit), the LEARNED
// universal constitution (always-on rules), and the universal skills & agents that ship with the
// harness. Everything here is universal — nothing per-repo (that lives in each repo's Dev workspace).

const SCOPE_COLOR: Record<string, string> = {
  universal: 'text-universal',
  dev: 'text-dev',
  core: 'text-core',
}

function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

// One-line preview: the first non-empty, non-heading line of the body.
function preview(body: string): string {
  const line = stripFrontmatter(body)
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l && !l.startsWith('#') && !l.startsWith('---'))
  return line ?? ''
}

type Tab = 'constitution' | 'skills' | 'agents'

const TABS: { key: Tab; label: string; icon: typeof ScrollText }[] = [
  { key: 'constitution', label: 'Constitution', icon: ScrollText },
  { key: 'skills', label: 'Skills', icon: Sparkles },
  { key: 'agents', label: 'Agents', icon: Bot },
]

export default function Foundations() {
  const [files, setFiles] = useState<FoundationFile[] | null>(null)
  const [consts, setConsts] = useState<FoundationConstitution[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<FoundationFile | null>(null)
  const [tab, setTab] = useState<Tab>('constitution')
  // `${form}:${slug}` → published item for learned+published universal artifacts — used to badge
  // them and to open them with edit/enable-disable/delete governance.
  const [learned, setLearned] = useState<Set<string>>(new Set())
  const [pubByKey, setPubByKey] = useState<Map<string, PublishedItem>>(new Map())
  // Universal skill/agent totals for the tab badges — summed across every loading scope.
  const [pluginCounts, setPluginCounts] = useState<{ skills: number; agents: number } | null>(null)

  function load() {
    getFoundation()
      .then((d) => {
        setFiles(d.files)
        setConsts(d.constitutions)
      })
      .catch((e) => setErr(String(e)))
    getHarnessPlugins()
      .then((d) =>
        setPluginCounts({
          skills: d.scopes.reduce((n, s) => n + s.skills.length, 0),
          agents: d.scopes.reduce((n, s) => n + s.agents.length, 0),
        }),
      )
      .catch(() => {})
    getPublished('global')
      .then((d) => {
        const present = d.published.filter((p) => p.present)
        setLearned(new Set(present.map((p) => `${p.form}:${p.slug}`)))
        setPubByKey(new Map(present.map((p) => [`${p.form}:${p.slug}`, p])))
      })
      .catch(() => {})
  }
  useEffect(() => {
    load()
  }, [])

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl p-6">
        <header className="mb-6 flex items-center gap-2.5">
          <Layers size={18} className="text-universal" />
          <h1 className="text-[17px] font-semibold text-fg">Foundations</h1>
          <span className="text-[13px] text-faint">SuperMe's repo-agnostic identity &amp; machinery</span>
        </header>

        {/* Identity & charters — the hand-authored strip, always on top */}
        <section className="mb-8">
          <div className="mb-3 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wider text-muted">
            <FileText size={13} /> Identity &amp; charters
          </div>
          {err ? (
            <div className="text-sm text-danger">Couldn’t load foundation files — {err}</div>
          ) : files === null ? (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 size={14} className="animate-spin" /> Loading…
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {files.map((f) => (
                <button
                  key={f.key}
                  onClick={() => f.present && setOpen(f)}
                  disabled={!f.present}
                  className="group flex flex-col rounded-xl border border-line bg-surface px-4 py-3.5 text-left transition hover:border-faint disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-[14px] font-semibold text-fg">{f.label}</span>
                    <span className={`ml-auto text-[10px] font-medium uppercase tracking-wider ${SCOPE_COLOR[f.scope] ?? 'text-faint'}`}>
                      {f.scope}
                    </span>
                  </div>
                  <p className={`mt-1.5 line-clamp-2 text-[12px] leading-relaxed ${f.present ? 'text-muted' : 'text-faint'}`}>
                    {f.present ? preview(f.body) : 'not present'}
                  </p>
                </button>
              ))}
            </div>
          )}
        </section>

        {/* Learned constitution + universal skills/agents — one segmented workspace so the three
            no longer stack and compete. Each tab gets a clean full canvas. */}
        <div className="mb-4 flex items-center gap-1 border-b border-line">
          {TABS.map((t) => {
            const active = tab === t.key
            const count =
              t.key === 'constitution' ? consts.length : t.key === 'skills' ? pluginCounts?.skills ?? null : pluginCounts?.agents ?? null
            return (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] font-medium transition ${
                  active ? 'border-universal text-fg' : 'border-transparent text-muted hover:text-fg'
                }`}
              >
                <t.icon size={14} className={active ? 'text-universal' : ''} />
                {t.label}
                {count != null && <span className="text-[11px] text-faint">({count})</span>}
              </button>
            )
          })}
        </div>

        {tab === 'constitution' && (
          <section>
            <p className="mb-3 text-[12px] text-faint">Learned universal rules — always-on. Published constitution artifacts land here.</p>
            {consts.length === 0 ? (
              <Empty>No universal constitution learned yet — published constitution artifacts land here.</Empty>
            ) : (
              <div className="space-y-2">
                {consts.map((c) => (
                  <ConstitutionRow key={`${c.mode}-${c.slug}`} c={c} learned={learned.has(`constitution:${c.slug}`)} />
                ))}
              </div>
            )}
          </section>
        )}

        {tab === 'skills' && (
          <section>
            <p className="mb-3 text-[12px] text-faint">SuperMe's own universal skills, grouped by the scope that loads them. Click any to preview or edit.</p>
            <HarnessPlugins only="skill" learned={learned} publishedByKey={pubByKey} onGovernanceChange={load} />
          </section>
        )}

        {tab === 'agents' && (
          <section>
            <p className="mb-3 text-[12px] text-faint">SuperMe's own universal sub-agents, grouped by the scope that loads them. Click any to preview or edit.</p>
            <HarnessPlugins only="agent" learned={learned} publishedByKey={pubByKey} onGovernanceChange={load} />
          </section>
        )}
      </div>

      {open && (
        <FileViewer
          file={open}
          onClose={() => setOpen(null)}
          onSaved={() => {
            load()
            setOpen(null)
          }}
        />
      )}
    </div>
  )
}

function ConstitutionRow({ c, learned = false }: { c: FoundationConstitution; learned?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`rounded-lg border border-line bg-surface ${c.enabled ? '' : 'opacity-60'}`}>
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left">
        <span className={`text-[10px] font-medium uppercase tracking-wider ${c.mode === 'dev' ? 'text-dev' : 'text-core'}`}>{c.mode}</span>
        <span className="min-w-0 flex-1 truncate text-[14px] text-fg">{c.title}</span>
        {learned && <span className="shrink-0 rounded bg-warn/15 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-warn">learned</span>}
        {!c.enabled && <span className="text-[10px] uppercase tracking-wide text-faint">disabled</span>}
      </button>
      {open && (
        <div className="border-t border-line px-3.5 py-3 text-[13px] text-muted">
          <Markdown text={c.body} variant="doc" />
        </div>
      )}
    </div>
  )
}

// View + edit one identity/charter file. Charters are hand-authored system-prompt sources —
// editing is allowed (takes effect next turn); the raw markdown (frontmatter kept) is edited.
function FileViewer({ file, onClose, onSaved }: { file: FoundationFile; onClose: () => void; onSaved: () => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(file.body)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && !editing && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, editing])

  async function save() {
    setBusy(true)
    setErr(null)
    try {
      await saveFoundationFile(file.key, draft)
      onSaved()
    } catch (e) {
      setErr(String(e))
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-line bg-app shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-5 py-3.5">
          <FileText size={15} className={SCOPE_COLOR[file.scope] ?? 'text-muted'} />
          <span className="text-[15px] font-semibold text-fg">{file.label}</span>
          <span className={`text-[10px] font-medium uppercase tracking-wider ${SCOPE_COLOR[file.scope] ?? 'text-faint'}`}>{file.scope}</span>
          <div className="ml-auto flex items-center gap-1.5">
            {!editing ? (
              <button
                onClick={() => { setDraft(file.body); setEditing(true) }}
                className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg"
              >
                <Pencil size={12} /> Edit
              </button>
            ) : (
              <>
                <button
                  onClick={() => setEditing(false)}
                  disabled={busy}
                  className="rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  onClick={save}
                  disabled={busy || draft === file.body || !draft.trim()}
                  className="flex items-center gap-1 rounded-md bg-accent px-2 py-1 text-xs text-on-accent hover:opacity-90 disabled:opacity-50"
                >
                  {busy ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} Save
                </button>
              </>
            )}
            <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-hover hover:text-fg">
              <X size={18} />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {err && <div className="mb-2 text-sm text-danger">{err}</div>}
          {editing ? (
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              className="h-[60vh] w-full resize-none rounded-md border border-line bg-sunken p-3 font-mono text-[12.5px] leading-relaxed text-fg outline-none focus:border-accent"
            />
          ) : (
            <Markdown text={stripFrontmatter(file.body)} variant="doc" tone={file.scope as 'universal' | 'dev' | 'core'} />
          )}
        </div>
      </div>
    </div>
  )
}
