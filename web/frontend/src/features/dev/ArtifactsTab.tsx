import { useEffect, useState } from 'react'
import { ScrollText, Loader2 } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Toggle from '@/ui/Toggle'
import { getConstitutions, toggleConstitution, type ManagedConstitution } from '@/lib/api'
import { Empty } from './common'

// Artifacts — per-repo LOCAL operational artifacts for one host (Dev workspace tab, after Learning).
// Mirrors Foundations' universal artifact management, but scoped to THIS host's own local harness.
// Today: the host's local constitutions, each with enable/disable + a collapsible body. (Local
// skills & agents management is the next step.) Disabling flips the `enabled` flag the always-on
// catalog and `pull_constitution` both honor — a disabled item is fully inert on the next dev turn.

export default function ArtifactsTab({ contextId }: { contextId: string }) {
  const [items, setItems] = useState<ManagedConstitution[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  function load() {
    getConstitutions(contextId)
      .then((d) => setItems(d.constitutions.filter((c) => c.origin === 'repo')))
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
          Local constitutions for this host — enable/disable to control what loads. A disabled item leaves
          the catalog and can't be pulled. (Local skills &amp; agents management is coming next.)
        </p>

        {err ? (
          <div className="text-sm text-danger">Couldn’t load artifacts — {err}</div>
        ) : items === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Loading…
          </div>
        ) : items.length === 0 ? (
          <Empty>No local constitutions for this host yet — forge one, and it lands here.</Empty>
        ) : (
          <div className="space-y-2">
            {items.map((c) => (
              <ConstitutionRow key={c.slug} c={c} contextId={contextId} onToggled={load} />
            ))}
          </div>
        )}
      </div>
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
