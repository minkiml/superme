import { useEffect, useState } from 'react'
import { Boxes, Database, Wrench, KeyRound, Loader2 } from 'lucide-react'
import ArtifactTabs from '@/ui/ArtifactTabs'
import { getInventory, type Inventory, type InvTable, type InvTool } from '@/lib/api'

// TEMPORARY "Internals" surface — one read-only snapshot of SuperMe's live data model (every SQLite
// table + columns + row counts) and the agent-facing tool surface (base + dev, with params). All of
// it is introspected live by /system/inventory, so nothing here is hand-maintained. This whole
// feature (tab + component + api + router) is a scaffold; delete it once it's served its purpose.

type Tab = 'schema' | 'tools'

export default function Internals() {
  const [inv, setInv] = useState<Inventory | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('schema')

  useEffect(() => {
    getInventory().then(setInv).catch((e) => setErr(String(e)))
  }, [])

  const tableCount = inv?.databases.reduce((n, d) => n + d.tables.length, 0) ?? null
  const toolCount = inv?.tools.length ?? null

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl p-6">
        <header className="mb-1 flex items-center gap-2.5">
          <Boxes size={18} className="text-universal" />
          <h1 className="text-[17px] font-semibold text-fg">Internals</h1>
          <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-warn">temporary</span>
        </header>
        <p className="mb-5 text-[13px] text-faint">
          A live snapshot of SuperMe's data model &amp; tool surface — introspected fresh from the running daemon.
        </p>

        {err ? (
          <div className="text-sm text-danger">Couldn’t load inventory — {err}</div>
        ) : inv === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Loading…
          </div>
        ) : (
          <>
            <ArtifactTabs
              className="mb-4"
              tint="universal"
              value={tab}
              onChange={setTab}
              tabs={[
                { key: 'schema', label: 'Data model', icon: Database, count: tableCount },
                { key: 'tools', label: 'Tools', icon: Wrench, count: toolCount },
              ]}
            />

            {tab === 'schema' &&
              inv.databases.map((db) => (
                <section key={db.name} className="mb-6">
                  <div className="mb-2 flex items-baseline gap-2">
                    <Database size={13} className="text-muted" />
                    <h2 className="text-[13px] font-semibold text-fg">{db.name}</h2>
                    <span className="font-mono text-[11px] text-faint">{db.tables.length} tables</span>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {db.tables.map((t) => <TableCard key={t.name} t={t} />)}
                  </div>
                </section>
              ))}

            {tab === 'tools' && <ToolsView tools={inv.tools} />}
          </>
        )}
      </div>
    </div>
  )
}

function TableCard({ t }: { t: InvTable }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-3.5">
      <div className="mb-2 flex items-baseline gap-2">
        <span className="font-mono text-[13px] font-semibold text-fg">{t.name}</span>
        <span className="ml-auto font-mono text-[11px] text-faint">
          {t.rows != null ? `${t.rows} row${t.rows === 1 ? '' : 's'}` : ''}
        </span>
      </div>
      <div className="space-y-0.5">
        {t.columns.map((c) => (
          <div key={c.name} className="flex items-center gap-2 text-[12px]">
            {c.pk ? (
              <KeyRound size={11} className="shrink-0 text-warn" />
            ) : (
              <span className="w-[11px] shrink-0" />
            )}
            <span className="font-mono text-fg">{c.name}</span>
            <span className="font-mono text-[11px] text-faint">{c.type || '—'}</span>
            {c.notnull && !c.pk && <span className="text-[10px] uppercase tracking-wide text-faint">not null</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

const SURFACE_TINT: Record<string, string> = {
  base: 'text-universal',
  'dev · main': 'text-dev',
  'dev · learning': 'text-warn',
}

function ToolsView({ tools }: { tools: InvTool[] }) {
  const surfaces = [...new Set(tools.map((t) => t.surface))]
  return (
    <>
      {surfaces.map((surface) => (
        <section key={surface} className="mb-6">
          <div className="mb-2 flex items-baseline gap-2">
            <Wrench size={13} className="text-muted" />
            <h2 className={`text-[13px] font-semibold ${SURFACE_TINT[surface] ?? 'text-fg'}`}>{surface}</h2>
            <span className="font-mono text-[11px] text-faint">
              {tools.filter((t) => t.surface === surface).length} tools
            </span>
          </div>
          <div className="space-y-2">
            {tools.filter((t) => t.surface === surface).map((t) => (
              <div key={t.name} className="rounded-lg border border-line bg-surface px-3.5 py-2.5">
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-[13px] font-semibold text-fg">{t.name}</span>
                  {t.params.length > 0 && (
                    <span className="flex flex-wrap gap-1">
                      {t.params.map((p) => (
                        <span
                          key={p.name}
                          className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${p.required ? 'bg-warn/15 text-warn' : 'bg-hover text-muted'}`}
                          title={p.required ? 'required' : 'optional'}
                        >
                          {p.name}{p.required ? '*' : ''}
                        </span>
                      ))}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-[12px] leading-relaxed text-muted">{t.description}</p>
              </div>
            ))}
          </div>
        </section>
      ))}
    </>
  )
}
