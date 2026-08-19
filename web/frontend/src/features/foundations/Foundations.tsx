import { useEffect, useRef, useState } from 'react'
import { Layers, FileText, X, Loader2, ScrollText, Sparkles, Bot, Pin } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Toggle from '@/ui/Toggle'
import ArtifactTabs from '@/ui/ArtifactTabs'
import SourceEditor from '@/ui/SourceEditor'
import { useEditGate, EditActions } from '@/ui/EditGate'
import { getFoundation, saveFoundationFile, getPublished, getHarnessPlugins, toggleConstitution, type FoundationFile, type FoundationConstitution, type PublishedItem } from '@/lib/api'
import { HarnessPlugins } from '@/features/dev/HarnessPlugins'
import ConstitutionModal from '@/features/dev/ConstitutionModal'
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

export default function Foundations() {
  const [files, setFiles] = useState<FoundationFile[] | null>(null)
  const [consts, setConsts] = useState<FoundationConstitution[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<FoundationFile | null>(null)
  const [openConst, setOpenConst] = useState<FoundationConstitution | null>(null)
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
        <header className="mb-6 flex flex-wrap items-center gap-x-2.5 gap-y-1">
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
            <div className="grid cols-narrow gap-3">
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
        <ArtifactTabs
          className="mb-4"
          tint="universal"
          value={tab}
          onChange={setTab}
          tabs={[
            { key: 'constitution', label: 'Constitution', icon: ScrollText, count: consts.length },
            { key: 'skills', label: 'Skills', icon: Sparkles, count: pluginCounts?.skills ?? null },
            { key: 'agents', label: 'Agents', icon: Bot, count: pluginCounts?.agents ?? null },
          ]}
        />

        {tab === 'constitution' && (
          <section>
            <p className="mb-3 text-[12px] text-faint">Universal constitution — always-on rules, grouped by the mode that loads them. Toggle to control what loads.</p>
            {/* One column per mode (Dev · Core) — matches the Skills/Agents split; no shared column
                (a constitution is always mode-scoped). */}
            <div className="grid cols-wide gap-4">
              {(['dev', 'core'] as const).map((mode) => {
                const rows = consts.filter((c) => c.mode === mode)
                return (
                  <section key={mode} className="rounded-xl border border-line bg-surface p-3.5">
                    <div className="mb-3">
                      <h2 className={`text-[13px] font-semibold ${mode === 'dev' ? 'text-dev' : 'text-core'}`}>{mode === 'dev' ? 'Dev' : 'Core'}</h2>
                      <span className="text-[11px] text-faint">loaded in {mode} mode</span>
                    </div>
                    {rows.length === 0 ? (
                      <p className="text-[12px] text-faint">None in this scope.</p>
                    ) : (
                      <div className="space-y-2">
                        {rows.map((c) => (
                          <ConstitutionRow
                            key={`${c.mode}-${c.slug}`}
                            c={c}
                            learned={learned.has(`constitution:${c.slug}`)}
                            onToggled={load}
                            onOpen={() => setOpenConst(c)}
                          />
                        ))}
                      </div>
                    )}
                  </section>
                )
              })}
            </div>
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
      {openConst && (
        <ConstitutionModal
          slug={openConst.slug}
          scope={`universal_${openConst.mode}`}
          title={openConst.title}
          mode={openConst.mode}
          body={openConst.body}
          enabled={openConst.enabled}
          foundational={openConst.foundational}
          learned={learned.has(`constitution:${openConst.slug}`)}
          tint={openConst.mode === 'core' ? 'core' : 'dev'}
          onClose={() => setOpenConst(null)}
          onToggled={load}
        />
      )}
    </div>
  )
}

function ConstitutionRow({ c, learned = false, onToggled, onOpen }: { c: FoundationConstitution; learned?: boolean; onToggled: () => void; onOpen: () => void }) {
  const [busy, setBusy] = useState(false)

  async function toggle(v: boolean) {
    setBusy(true)
    try {
      await toggleConstitution(c.slug, `universal_${c.mode}`, v)
      onToggled()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`rounded-lg border border-line bg-surface ${c.enabled ? '' : 'opacity-60'}`}>
      <div className="flex items-center gap-2 px-3.5 py-2.5">
        <button onClick={onOpen} className="flex min-w-0 flex-1 items-center gap-2 text-left" title="Preview">
          <span className={`text-[10px] font-medium uppercase tracking-wider ${c.mode === 'dev' ? 'text-dev' : 'text-core'}`}>{c.mode}</span>
          <span className="min-w-0 flex-1 truncate text-[14px] text-fg">{c.title}</span>
          {c.foundational && <span className="shrink-0 rounded bg-universal/15 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-universal" title="A charter consults this by name — always on">foundational</span>}
          {learned && <span className="shrink-0 rounded bg-warn/15 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-warn">learned</span>}
          {!c.enabled && <span className="text-[10px] uppercase tracking-wide text-faint">disabled</span>}
        </button>
        {c.foundational ? (
          <Pin size={14} className="shrink-0 text-faint" aria-label="Foundational — always on" />
        ) : (
          <Toggle on={c.enabled} onChange={toggle} onColor={c.mode === 'core' ? 'bg-core' : 'bg-dev'} disabled={busy} title={c.enabled ? 'Disable' : 'Enable'} />
        )}
      </div>
    </div>
  )
}

// View + edit one identity/charter file. Charters are hand-authored system-prompt sources —
// editing is allowed (takes effect next turn); the raw markdown (frontmatter kept) is edited.
function FileViewer({ file, onClose, onSaved }: { file: FoundationFile; onClose: () => void; onSaved: () => void }) {
  const gate = useEditGate({
    saved: file.body,
    valid: (d) => !!d.trim(),
    commit: async (d) => { await saveFoundationFile(file.key, d); onSaved() },
  })
  const { editing, draft, err } = gate
  // Close only on a TRUE backdrop click — press AND release both on the scrim (not a drag that
  // starts inside and ends out). While editing, an outside click never closes (only the X does).
  const downOnScrim = useRef(false)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && !editing && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, editing])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onMouseDown={(e) => { downOnScrim.current = e.target === e.currentTarget }}
      onMouseUp={(e) => {
        if (!editing && downOnScrim.current && e.target === e.currentTarget) onClose()
        downOnScrim.current = false
      }}
    >
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-line bg-app shadow-2xl">
        <div className="flex items-center gap-2 border-b border-line px-5 py-3.5">
          <FileText size={15} className={SCOPE_COLOR[file.scope] ?? 'text-muted'} />
          <span className="text-[15px] font-semibold text-fg">{file.label}</span>
          <span className={`text-[10px] font-medium uppercase tracking-wider ${SCOPE_COLOR[file.scope] ?? 'text-faint'}`}>{file.scope}</span>
          <div className="ml-auto flex items-center gap-1.5">
            <EditActions gate={gate} />
            <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-hover hover:text-fg">
              <X size={18} />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {err && <div className="mb-2 text-sm text-danger">{err}</div>}
          {editing ? (
            <SourceEditor value={draft} onChange={gate.setDraft} surface="bg-sunken" />
          ) : (
            <Markdown text={stripFrontmatter(file.body)} variant="doc" tone={file.scope as 'universal' | 'dev' | 'core'} />
          )}
        </div>
      </div>
    </div>
  )
}
