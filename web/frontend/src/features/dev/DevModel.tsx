import { useEffect, useState } from 'react'
import { Network, RefreshCw, ArrowRight, ArrowLeftRight } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'
import {
  getModel,
  type DevModel as DevModelData,
  type ModelEntity,
  type ModelEdge,
  type ModelLifecycleSpec,
  type ContextStack,
  type SkillsSpec,
  type SkillEntry,
  type OperatingModel,
} from '@/lib/api'
import { STATUS_COLOR, STATUS_LABEL, Empty } from './common'

// The Model sub-page — the owner's quick lookup screen. From model.yaml; kept in sync with the
// implemented models. Sections: data schemas · context stack · skills/sub-agents · high-level
// workflow (reserved). No prose beyond what the lookup needs.

export default function DevModel({ contextId = 'global' }: { contextId?: string }) {
  const [model, setModel] = useState<DevModelData | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setErr(null)
    try {
      setModel(await getModel(contextId))
    } catch (e) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextId])

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        icon={Network}
        title="Model"
        subtitle="Quick reference — data schemas · context stack · skills"
        badge="prototype"
        right={
          <button
            onClick={load}
            disabled={loading}
            title="Refresh"
            aria-label="Refresh"
            className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        }
      />

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-4xl p-6">
          {loading && !model ? (
            <div className="text-sm text-muted">Loading…</div>
          ) : err ? (
            <Empty>Couldn’t load the model — {err}</Empty>
          ) : !model?.exists ? (
            <Empty>No model manifest for this context yet.</Empty>
          ) : (
            <Overview model={model} />
          )}
        </div>
      </div>
    </div>
  )
}

function Overview({ model }: { model: DevModelData }) {
  return (
    <div className="space-y-8">
      <Section title="Data schemas" hint="the implemented models & their fields">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {model.entities.map((e) => (
            <EntitySchema key={e.id} e={e} />
          ))}
        </div>
        <div className="mt-3">
          <ModelLifecycle lifecycle={model.lifecycle} />
        </div>
      </Section>

      {model.context_stack && (
        <Section title="Context stack" hint="what fills a turn — pinned · carried · pulled">
          <ContextStackView cs={model.context_stack} />
        </Section>
      )}

      {model.skills && (
        <Section title="Skills & sub-agents" hint="SuperMe's own (native ~/.claude skills → context stack) · dashed = planned">
          <SkillsView skills={model.skills} />
        </Section>
      )}

      {model.operating_model && (
        <Section title="High-level workflow" hint="reserved — not in use yet · dashed = reserved">
          <WorkflowGuide om={model.operating_model} lifecycle={model.lifecycle} />
        </Section>
      )}
    </div>
  )
}

// --- data schemas ---------------------------------------------------------------

function EntitySchema({ e }: { e: ModelEntity }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="border-b border-line px-3 py-2">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-sm font-semibold text-fg">{e.label}</span>
          {e.storage && <span className="shrink-0 text-[10px] uppercase tracking-wide text-faint">{e.storage}</span>}
        </div>
        <code className="text-[10.5px] text-faint">{e.store}</code>
      </div>
      {e.schema ? (
        <table className="w-full border-collapse text-[12px]">
          <tbody>
            {e.schema.map((row) => (
              <tr key={row.f} className="border-b border-line/60 last:border-b-0 align-top">
                <td className="whitespace-nowrap py-1 pl-3 pr-2 font-mono text-fg">{row.f}</td>
                <td className="whitespace-nowrap py-1 pr-2 font-mono text-[11px] text-accent-text">{row.t}</td>
                <td className="py-1 pr-3 text-[11px] text-faint">{row.note ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="flex flex-wrap gap-1.5 p-3">
          {(e.fields ?? []).map((f) => (
            <span key={f} className="rounded bg-hover px-1.5 py-0.5 font-mono text-[11px] text-muted">{f}</span>
          ))}
        </div>
      )}
    </div>
  )
}

// --- context stack --------------------------------------------------------------

const CLASS_COLOR: Record<string, string> = {
  PINNED: 'text-accent-text',
  CARRIED: 'text-warn',
  LIVE: 'text-fg',
  PULLED: 'text-faint',
}

function ScopeChip({ scope, small }: { scope?: string; small?: boolean }) {
  const varies = scope === 'mode' || scope === 'mixed'
  const label = scope === 'mode' ? 'dev / core' : scope === 'mixed' ? 'mixed' : 'native'
  const size = small ? 'px-1 py-0 text-[8.5px]' : 'px-1.5 py-0.5 text-[9px]'
  return (
    <span
      className={`shrink-0 rounded font-medium uppercase tracking-wide ${size} ${
        varies ? 'bg-accent-soft text-accent-text' : 'text-faint'
      }`}
    >
      {label}
    </span>
  )
}

function ContextStackView({ cs }: { cs: ContextStack }) {
  return (
    <div className="space-y-3">
      <div className="overflow-hidden rounded-lg border border-line bg-surface">
        {cs.layers.map((l) => {
          const varies = l.scope === 'mode' || l.scope === 'mixed'
          return (
            <div key={l.n} className="border-b border-line last:border-b-0">
              <div
                className={`flex items-center gap-2 py-1.5 pr-3 ${
                  varies ? 'border-l-2 border-l-accent bg-hover pl-[10px]' : 'pl-3'
                }`}
              >
                <span className="w-5 shrink-0 text-right font-mono text-[11px] text-faint">{l.n}</span>
                <span className={`w-16 shrink-0 text-[10px] font-medium uppercase tracking-wide ${CLASS_COLOR[l.cls] ?? 'text-muted'}`}>
                  {l.cls}
                </span>
                <span className="shrink-0 text-[13px] text-fg">{l.name}</span>
                <ScopeChip scope={l.scope} />
                {l.src && <span className="ml-auto truncate font-mono text-[10.5px] text-faint">{l.src}</span>}
              </div>
              {l.sources && (
                <div className="space-y-0.5 pb-1.5 pl-[78px] pr-3">
                  {l.sources.map((s) => (
                    <div key={s.label} className="flex items-center gap-2 text-[11px]">
                      <span className="text-faint">└</span>
                      <span className="text-muted">{s.label}</span>
                      <ScopeChip scope={s.scope} small />
                      {s.note && <span className="ml-auto truncate font-mono text-[10px] text-faint">{s.note}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <p className="text-[11px] text-faint">
        <span className="text-muted">native</span> = Claude Code, identical for any agent (incl. the owner's
        own <span className="text-muted">~/.claude</span> + project <span className="text-muted">.claude</span>{' '}
        skills/commands) · <span className="text-accent-text">dev / core</span> = differs by mode ·{' '}
        <span className="text-accent-text">mixed</span> = both (see sources)
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {cs.agents.map((a) => (
          <div
            key={a.id}
            className="rounded-lg border bg-surface px-3 py-2.5"
            style={{ borderStyle: a.reserved ? 'dashed' : 'solid', borderColor: 'var(--c-line)' }}
          >
            <div className="mb-1.5 flex items-center gap-2">
              <span className="text-sm font-semibold text-fg">{a.label}</span>
              {a.reserved && <span className="text-[10px] uppercase tracking-wide text-faint">planned</span>}
            </div>
            <Kv k="knowledge" v={a.knowledge} />
            <Kv k="append" v={a.append} />
            <Kv k="skills" v={a.skills} />
          </div>
        ))}
      </div>
    </div>
  )
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2 text-[12px] leading-relaxed">
      <span className="w-16 shrink-0 text-faint">{k}</span>
      <span className="font-mono text-[11px] text-muted">{v}</span>
    </div>
  )
}

// --- skills ---------------------------------------------------------------------

function SkillsView({ skills }: { skills: SkillsSpec }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <SkillColumn title="dev-mode" items={skills.dev} />
      <SkillColumn title="core-mode" items={skills.core} />
      {skills.shared && skills.shared.length > 0 && (
        <SkillColumn title="shared" items={skills.shared} />
      )}
    </div>
  )
}

function SkillColumn({ title, items }: { title: string; items: SkillEntry[] }) {
  return (
    <div className="rounded-lg border border-line bg-surface">
      <div className="border-b border-line px-3 py-1.5 text-[11px] uppercase tracking-wide text-faint">{title}</div>
      <div className="space-y-1 p-2">
        {items.length === 0 ? (
          <div className="px-1.5 py-1 text-[11px] text-faint">—</div>
        ) : (
          items.map((s) => (
            <div key={s.name} className="flex items-center gap-2 px-1.5 py-0.5" style={{ opacity: s.reserved ? 0.6 : 1 }}>
              <span className="font-mono text-[12px] text-fg">{s.name}</span>
              <span className="rounded bg-hover px-1.5 py-0.5 text-[9.5px] uppercase tracking-wide text-muted">
                {s.kind}
              </span>
              {s.reserved && <span className="text-[10px] text-faint">planned</span>}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

// --- high-level workflow (reserved, non-interactive) ----------------------------

function WorkflowGuide({ om, lifecycle }: { om: OperatingModel; lifecycle: ModelLifecycleSpec }) {
  const actorLabel = (raw?: string) =>
    (raw ?? '')
      .split('+')
      .map((a) => om.actors.find((x) => x.id === a.trim())?.label ?? a.trim())
      .filter(Boolean)
      .join(' + ')
  const phaseCaption = (lifecycle.phase?.states ?? []).join(' → ')
  const nodes: GNode[] = om.nodes.map((n) => ({
    id: n.id,
    label: n.label,
    x: n.x,
    y: n.y,
    sublabel: actorLabel(n.actor),
    tag: n.kind === 'gate' ? 'gate' : undefined,
    reserved: n.reserved,
    caption: n.lifecycle ? phaseCaption : undefined,
  }))
  return (
    <>
      <FlowGraph nodes={nodes} edges={om.edges} vw={820} vh={330} />
      {om.note && <p className="mt-2 text-[11px] text-faint">{om.note}</p>}
    </>
  )
}

// --- generic flow graph ---------------------------------------------------------

type GNode = {
  id: string
  label: string
  x: number
  y: number
  sublabel?: string
  tag?: string
  reserved?: boolean
  caption?: string
}

const NW = 128
const NH = 46

function gBorder(c: { x: number; y: number }, toward: { x: number; y: number }) {
  const dx = toward.x - c.x
  const dy = toward.y - c.y
  if (!dx && !dy) return c
  const s = Math.min((NW / 2 + 3) / Math.abs(dx || 1e-6), (NH / 2 + 3) / Math.abs(dy || 1e-6))
  return { x: c.x + dx * s, y: c.y + dy * s }
}

function FlowGraph({ nodes, edges, vw, vh }: { nodes: GNode[]; edges: ModelEdge[]; vw: number; vh: number }) {
  const pos = (id: string) => nodes.find((n) => n.id === id)
  const cross = edges.filter((e) => e.from !== e.to && pos(e.from) && pos(e.to))

  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface p-2">
      <svg viewBox={`0 0 ${vw} ${vh}`} className="mx-auto block w-full min-w-[540px]" role="img">
        <defs>
          <marker id="fg-arrow" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6 Z" fill="var(--c-faint)" />
          </marker>
        </defs>

        {cross.map((e, i) => {
          const S = pos(e.from)!
          const T = pos(e.to)!
          const a = gBorder(S, T)
          const b = gBorder(T, S)
          const mx = (a.x + b.x) / 2
          const my = (a.y + b.y) / 2
          const lo = e.from < e.to ? S : T
          const hi = e.from < e.to ? T : S
          const pdx = hi.x - lo.x
          const pdy = hi.y - lo.y
          const plen = Math.hypot(pdx, pdy) || 1
          const off = 30 * (e.from < e.to ? 1 : -1)
          const cx = mx + (-pdy / plen) * off
          const cy = my + (pdx / plen) * off
          const w = e.label ? Math.max(30, e.label.length * 6.2) : 0
          return (
            <g key={i}>
              <path
                d={`M ${a.x} ${a.y} Q ${cx} ${cy} ${b.x} ${b.y}`}
                fill="none"
                stroke="var(--c-faint)"
                strokeWidth={1.4}
                strokeDasharray={e.reserved ? '4 3' : undefined}
                markerEnd="url(#fg-arrow)"
                opacity={e.reserved ? 0.55 : 0.9}
              />
              {e.label && (
                <>
                  <rect x={cx - w / 2} y={cy - 9} width={w} height={18} rx={4} fill="var(--c-surface)" />
                  <text
                    x={cx}
                    y={cy + 1}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fontSize={10.5}
                    fontFamily="ui-monospace, monospace"
                    fill={e.reserved ? 'var(--c-faint)' : 'var(--c-muted)'}
                  >
                    {e.label}
                  </text>
                </>
              )}
            </g>
          )
        })}

        {nodes.map((n) => (
          <g key={n.id} opacity={n.reserved ? 0.72 : 1}>
            {n.tag && (
              <text x={n.x} y={n.y - NH / 2 - 4} textAnchor="middle" fontSize={8} fill="var(--c-faint)" letterSpacing="0.06em">
                {n.tag.toUpperCase()}
              </text>
            )}
            <rect
              x={n.x - NW / 2}
              y={n.y - NH / 2}
              width={NW}
              height={NH}
              rx={10}
              fill="var(--c-surface)"
              stroke={'var(--c-line)'}
              strokeWidth={1.2}
              strokeDasharray={n.reserved ? '4 3' : undefined}
            />
            <text
              x={n.x}
              y={n.sublabel ? n.y - 6 : n.y + 1}
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={12.5}
              fontWeight={500}
              fill="var(--c-fg)"
            >
              {n.label}
            </text>
            {n.sublabel && (
              <text x={n.x} y={n.y + 11} textAnchor="middle" fontSize={9.5} fill="var(--c-muted)">
                {n.sublabel}
              </text>
            )}
            {n.caption && (
              <text x={n.x} y={n.y + NH / 2 + 13} textAnchor="middle" fontSize={9.5} fontFamily="ui-monospace, monospace" fill="var(--c-faint)">
                {n.caption}
              </text>
            )}
          </g>
        ))}
      </svg>
    </div>
  )
}

// --- lifecycle (status & phase) -------------------------------------------------

function ModelLifecycle({ lifecycle }: { lifecycle: ModelLifecycleSpec }) {
  const phase = lifecycle.phase
  const status = lifecycle.status
  const loops = phase?.loops ?? []
  const isLoop = (a: string, b: string) =>
    loops.some((l) => (l.from === a && l.to === b) || (l.from === b && l.to === a))
  return (
    <div className="space-y-3">
      {phase && (
        <div className="rounded-lg border border-line bg-surface px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <span className="mr-1 uppercase tracking-wide text-faint">work-item · phase</span>
            {phase.states.map((p, i) => {
              const next = phase.states[i + 1]
              const Sep = next && isLoop(p, next) ? ArrowLeftRight : ArrowRight
              return (
                <span key={p} className="flex items-center gap-1.5">
                  <span className="rounded-full bg-hover px-2 py-0.5 uppercase tracking-wide text-muted">{p}</span>
                  {next && <Sep size={13} className={isLoop(p, next) ? 'text-accent-text' : 'text-faint'} />}
                </span>
              )
            })}
          </div>
        </div>
      )}
      {status && (
        <div className="rounded-lg border border-line bg-surface px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <span className="mr-1 uppercase tracking-wide text-faint">status</span>
            {status.states.map((s) => (
              <span
                key={s}
                className={`rounded-full bg-hover px-2 py-0.5 uppercase tracking-wide ${STATUS_COLOR[s] ?? 'text-muted'}`}
              >
                {STATUS_LABEL[s] ?? s}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-2.5 flex items-baseline gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-faint">{title}</h3>
        {hint && <span className="text-[11px] text-faint/80">{hint}</span>}
      </div>
      {children}
    </div>
  )
}
