import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { getPortrait, type Portrait } from '@/lib/api'
import { Empty } from './common'

// The PORTRAIT — what this project IS. Six bands, each fed by exactly one anchor doc, so the view
// can never become a place knowledge secretly lives: it renders what the docs say, or nothing.
//
// Deliberately absent: decisions (volatile, often work-item specific), work in motion, lint
// findings, status. The dashboard already answers what the project is DOING; this answers what it
// is. The test it has to pass is that reading it end to end tells you the project in under a
// minute — so every band that failed that test in the benchmarks (file trees, component
// inventories, session history) is gone. See general_docs/general-knowledge-content-design.md.

function Band({ title, src, children }: { title: string; src: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <div className="mb-2.5 flex items-baseline gap-2">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted">{title}</h2>
        <span className="ml-auto font-mono text-[10px] text-faint">{src}</span>
      </div>
      {children}
    </section>
  )
}

function Field({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null
  return (
    <div className="min-w-0 flex-1">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-faint">{label}</div>
      <div className="mt-1 text-[12.5px] leading-relaxed text-muted">{value}</div>
    </div>
  )
}

// A plain bullet list. `tone` carries the one bit of meaning a list has here: pursued vs refused.
function Bullets({ items, tone = 'go' }: { items: string[]; tone?: 'go' | 'no' | 'inv' }) {
  const mark = tone === 'no' ? '✕' : tone === 'inv' ? '▪' : '▸'
  const color = tone === 'no' ? 'text-faint' : tone === 'inv' ? 'text-dev' : 'text-success'
  return (
    <div className="space-y-2">
      {items.map((t, i) => (
        <div key={i} className="flex gap-2.5 text-[12.5px] leading-relaxed">
          <span className={`shrink-0 pt-px text-[11px] ${color}`}>{mark}</span>
          <span className={tone === 'no' ? 'text-muted' : 'text-fg'}>{t}</span>
        </div>
      ))}
    </div>
  )
}

export default function ProjectPortrait({ contextId }: { contextId: string }) {
  const [p, setP] = useState<Portrait | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setP(null); setErr(null)
    getPortrait(contextId)
      .then((d) => alive && setP(d))
      .catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [contextId])

  if (err) return <div className="p-6 text-sm text-danger">Couldn’t load — {err}</div>
  if (!p) return (
    <div className="flex items-center gap-2 p-6 text-sm text-muted">
      <Loader2 size={14} className="animate-spin" /> Loading…
    </div>
  )

  const { identity, goals, build } = p
  const hasGoals = goals.now.length || goals.direction.length || goals.non_goals.length
  const hasBuild = build.stack.length || build.invariants.length || build.not_here.length

  return (
    <div className="mx-auto max-w-3xl p-6">
      {/* 1 — identity. Always first, never scrolled for. */}
      <header className="mb-8">
        <h1 className="text-[22px] font-semibold tracking-tight text-fg">{identity.name}</h1>
        {identity.one_liner && (
          <p className="mt-2.5 max-w-[52em] text-[14px] leading-relaxed text-fg">{identity.one_liner}</p>
        )}
        {(identity.who || identity.why) && (
          <div className="mt-4 flex flex-wrap gap-x-10 gap-y-4">
            <Field label="Who it's for" value={identity.who} />
            <Field label="Why it exists" value={identity.why} />
          </div>
        )}
      </header>

      {/* 2 — goals. Near-term must be specific; direction is allowed to be directional. Non-goals
          get equal weight: what a project refuses is as defining as what it pursues. */}
      {hasGoals ? (
        <Band title="Goals & non-goals" src="project-prd">
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <div className="space-y-5">
              {goals.now.length > 0 && (
                <div>
                  <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-faint">Now</div>
                  <Bullets items={goals.now} />
                </div>
              )}
              {goals.direction.length > 0 && (
                <div>
                  <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-faint">Direction</div>
                  <Bullets items={goals.direction} />
                </div>
              )}
            </div>
            {goals.non_goals.length > 0 && (
              <div>
                <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-faint">Deliberately not</div>
                <Bullets items={goals.non_goals} tone="no" />
              </div>
            )}
          </div>
        </Band>
      ) : null}

      {/* 3 — what it can do NOW. Empty is the honest answer for a project that hasn't shipped;
          never a placeholder, and never the plan restated — that's the roadmap's job. */}
      <Band title="What it can do now" src="capabilities">
        {p.capabilities.length === 0 ? (
          <Empty>Nothing shipped yet — the first entry lands when a deliverable closes.</Empty>
        ) : (
          <div className="rounded-lg border border-line bg-surface">
            {p.capabilities.map((c, i) => (
              <div key={i} className={`flex gap-2.5 px-4 py-2.5 text-[12.5px] ${i ? 'border-t border-line-soft' : ''}`}>
                <span className="shrink-0 font-medium text-fg">{c.name}</span>
                {c.detail && <span className="min-w-0 text-muted">{c.detail}</span>}
              </div>
            ))}
          </div>
        )}
      </Band>

      {/* 4 — how it's built. Only what code CAN'T say: stack, invariants, and the refusals. */}
      {hasBuild ? (
        <Band title="How it's built" src="architecture">
          {build.stack.length > 0 && (
            <div className="mb-5 flex flex-wrap gap-2">
              {build.stack.map((s, i) => (
                <span key={i} className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-[12px]">
                  <b className="font-semibold text-fg">{s.key}</b>
                  <span className="text-muted"> — {s.value}</span>
                </span>
              ))}
            </div>
          )}
          {build.invariants.length > 0 && (
            <div className="mb-5">
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-faint">Invariants</div>
              <Bullets items={build.invariants} tone="inv" />
            </div>
          )}
          {build.not_here.length > 0 && (
            <div>
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-faint">
                What’s deliberately not here
              </div>
              <Bullets items={build.not_here} tone="no" />
            </div>
          )}
        </Band>
      ) : null}

      {/* 5 — deliverables as VALUE. No slugs, no waves, no work-item status: each line completes
          "once this lands, I can ___". Sequencing and progress live in the pipeline. */}
      {p.deliverables.length > 0 && (
        <Band title="What it will deliver" src="project-prd">
          <div className="rounded-lg border border-line bg-surface">
            {p.deliverables.map((d, i) => (
              <div key={d.id} className={`flex items-baseline gap-3 px-4 py-2.5 ${i ? 'border-t border-line-soft' : ''}`}>
                <span className={`shrink-0 text-[11px] ${d.delivered ? 'text-success' : 'text-faint'}`}>
                  {d.delivered ? '●' : '○'}
                </span>
                <span className={`min-w-0 text-[13px] ${d.delivered ? 'text-fg' : 'text-muted'}`}>
                  {d.value || d.title}
                </span>
              </div>
            ))}
          </div>
        </Band>
      )}

      {/* 6 — where the external things are. */}
      {p.resources.length > 0 && (
        <Band title="Where things are" src="resources">
          <div className="space-y-2">
            {p.resources.map((r, i) => (
              <div key={i} className="flex gap-3 text-[12.5px] leading-relaxed">
                {r.key && <span className="w-28 shrink-0 text-[11.5px] text-faint">{r.key}</span>}
                <span className="min-w-0 text-muted">{r.value}</span>
              </div>
            ))}
          </div>
        </Band>
      )}
    </div>
  )
}
