import { useCallback, useEffect, useState } from 'react'
import { Bot, Loader2, Sparkles, X } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import SourceEditor from '@/ui/SourceEditor'
import ScopeColumns, { type ScopeCard, type ScopeColumn, type ScopeGroup } from '@/ui/ScopeColumns'
import { useEditGate, EditActions } from '@/ui/EditGate'
import { PublishedFileModal } from '@/features/dev/LearningGovernance'
import {
  getHarnessPlugins, getHarnessFile, saveHarnessFile,
  type HarnessEntry, type HarnessScope, type PublishedItem,
} from '@/lib/api'
import { Loading, PaneHead } from '../controls'
import { useUniversalPublished } from './published'

// System artifacts › Skills and › Agents — SuperMe's own universal plugins, one column per loading
// scope, grouped by their `category` frontmatter. Both sections are this component with a different
// `only`, because they differ by one field and nothing else; per-repo plugins are a different scope
// and live under Project.

const FALLBACK_CATEGORY = 'uncategorized'
// Shared reads as the universal scope (purple), then dev, then core — the same order and tints the
// charters use, so scope means one thing across the popup.
const SCOPE_ORDER: Record<string, number> = { shared: 0, dev: 1, core: 2 }
const SCOPE_TINT: Record<string, 'universal' | 'dev' | 'core'> = { shared: 'universal', dev: 'dev', core: 'core' }
// Learned artifacts leave their category for one bucket of their own, whatever their scope: what the
// loop wrote should stand apart from what shipped.
const LEARNED = 'learned'

function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

export default function Plugins({ only }: { only: 'skill' | 'agent' }) {
  const [scopes, setScopes] = useState<HarnessScope[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [openFile, setOpenFile] = useState<{ scope: string; entry: HarnessEntry } | null>(null)
  const [openPub, setOpenPub] = useState<PublishedItem | null>(null)
  const pub = useUniversalPublished()

  const load = useCallback(() => {
    getHarnessPlugins().then((d) => setScopes(d.scopes)).catch((e) => setErr(String(e)))
  }, [])
  useEffect(() => { load() }, [load])

  const noun = only === 'skill' ? 'skills' : 'sub-agents'
  const Icon = only === 'skill' ? Sparkles : Bot

  const columns: ScopeColumn[] = [...(scopes ?? [])]
    .sort((a, b) => (SCOPE_ORDER[a.scope] ?? 9) - (SCOPE_ORDER[b.scope] ?? 9))
    .map((s) => {
      const entries = only === 'agent' ? s.agents : s.skills
      // Category → entries, with learned ones pulled aside.
      const byCat = new Map<string, HarnessEntry[]>()
      for (const e of entries) {
        const key = pub.learned.has(`${e.kind}:${e.name}`) ? LEARNED : e.category || FALLBACK_CATEGORY
        if (!byCat.has(key)) byCat.set(key, [])
        byCat.get(key)!.push(e)
      }
      // Real categories first (alphabetical), then uncategorized, then learned.
      const rank = (c: string) => (c === LEARNED ? 3 : c === FALLBACK_CATEGORY ? 2 : 1)
      const groups: ScopeGroup[] = [...byCat.keys()]
        .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
        .map((cat) => ({
          label: cat,
          tone: cat === LEARNED ? ('learned' as const) : ('scope' as const),
          cards: byCat.get(cat)!
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((e): ScopeCard => {
              const published = pub.byKey.get(`${e.kind}:${e.name}`)
              return {
                key: e.name,
                name: e.name,
                // A published artifact opens its GOVERNOR (edit · disable · delete), not the plain
                // file editor: what the loop wrote is governed, not just edited.
                onClick: () => (published ? setOpenPub(published) : setOpenFile({ scope: s.scope, entry: e })),
                badges: e.kind === 'agent' && e.model
                  ? <span className="shrink-0 text-[11px] text-faint">{e.model}</span>
                  : undefined,
              }
            }),
        }))
      return { key: s.scope, name: s.label, note: s.note, tint: SCOPE_TINT[s.scope] ?? 'universal', icon: Icon, groups }
    })

  return (
    <>
      <PaneHead
        title={only === 'skill' ? 'Skills' : 'Agents'}
        scope="System artifacts"
        lede={`SuperMe's own universal ${noun}, grouped by the scope that loads them. Click any to preview or edit.`}
      />
      {err ? (
        <div className="text-sm text-danger">Couldn’t load the harness — {err}</div>
      ) : scopes === null ? (
        <Loading />
      ) : (
        <ScopeColumns columns={columns} />
      )}
      {openFile && <PluginFile scope={openFile.scope} entry={openFile.entry} onClose={() => setOpenFile(null)} />}
      {openPub && (
        <PublishedFileModal
          item={openPub}
          contextId="global"
          showScope={false}
          onClose={() => setOpenPub(null)}
          onSaved={() => { setOpenPub(null); load(); pub.reload() }}
          onGovernanceChange={() => { load(); pub.reload() }}
        />
      )}
    </>
  )
}

// Preview + edit one plugin's raw markdown. Saving takes effect on the next dev turn.
function PluginFile({ scope, entry, onClose }: { scope: string; entry: HarnessEntry; onClose: () => void }) {
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
    <Modal onClose={onClose} column maxW={editing ? 'max-w-4xl' : 'max-w-3xl'} z="z-[60]" dismissable={!editing}>
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
          <Markdown text={stripFrontmatter(content)} variant="doc" tone={SCOPE_TINT[scope]} />
        )}
      </div>
    </Modal>
  )
}
