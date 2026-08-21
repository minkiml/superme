import { Plus } from 'lucide-react'
import { fmtTokens } from '@/lib/format'
import { RepoIcon } from '@/lib/repoIcons'
import type { CommandStats, OrbitRepo } from './useCommandStats'

// The orbit command centre: the hub IS SuperMe, and connected projects orbit it carrying their
// token signal.
//
// Positions are on a percentage grid, so the connectors line up with the absolutely-placed cards.
const RX = 33
const RY = 34

function pos(i: number, n: number): { x: number; y: number } {
  const a = -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(1, n)
  return { x: 50 + RX * Math.cos(a), y: 50 + RY * Math.sin(a) }
}

// A pulsing ring while an agent is actively running, a static one when the repo is live but idle.
//
// The colour follows the repo's tag, and `shape` matches the node it wraps.
function LiveRing({ color, running, live, shape }: { color: string; running: boolean; live: boolean; shape: string }) {
  if (!running && !live) return null
  return (
    <span
      aria-hidden
      className={`pointer-events-none absolute inset-[-5px] border-2 ${shape} ${running ? 'animate-heartbeat' : ''}`}
      style={{ borderColor: color, transformOrigin: 'center', opacity: running ? undefined : 0.4 }}
    />
  )
}

function SplitBar({ dev, core }: { dev: number; core: number }) {
  const total = dev + core || 1
  return (
    <div className="mt-1.5 flex h-1 w-full overflow-hidden rounded-full bg-hover">
      <div className="h-full bg-dev" style={{ width: `${(dev / total) * 100}%` }} />
      <div className="h-full bg-core" style={{ width: `${(core / total) * 100}%` }} />
    </div>
  )
}

function NodeCard({ repo, selected, onSelect }: { repo: OrbitRepo; selected: boolean; onSelect: (id: string) => void }) {
  const c = repo.color
  return (
    <button
      onClick={() => onSelect(repo.id)}
      // The card outline always carries the repo's own tag color (dim when idle, full + glow on select).
      style={{
        borderColor: selected ? c : `${c}66`,
        boxShadow: selected ? `0 0 0 1px ${c}, 0 0 26px -4px ${c}` : undefined,
      }}
      className="relative w-44 rounded-xl border bg-surface px-3.5 py-2.5 text-left transition-all hover:shadow-lg"
    >
      <LiveRing color={c} running={repo.running > 0} live={repo.agents > 0} shape="rounded-[14px]" />
      <div className="flex items-center gap-2">
        {repo.icon ? (
          <RepoIcon name={repo.icon} size={14} color={c} className="shrink-0" />
        ) : (
          <span className="h-2.5 w-2.5 shrink-0 rounded-[3px]" style={{ backgroundColor: c }} />
        )}
        <span className="flex-1 truncate text-[14px] font-medium text-fg">{repo.label}</span>
        <span className="font-mono text-[13px] text-muted">{fmtTokens(repo.tokens)}</span>
      </div>
      <SplitBar dev={repo.dev} core={repo.core} />
    </button>
  )
}

export default function Nexus({
  stats,
  selectedId,
  onSelectRepo,
  onConnect,
}: {
  stats: CommandStats
  selectedId: string | null
  onSelectRepo: (id: string) => void
  onConnect?: () => void
}) {
  const nodes = stats.nodes
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-end px-6 py-3">
        <button
          onClick={onConnect}
          className="flex items-center gap-2 rounded-lg border border-dashed border-line px-4 py-2 text-[14px] font-medium text-muted hover:border-faint hover:text-fg"
        >
          <Plus size={16} /> Connect domain
        </button>
      </div>

      <div className="relative flex-1">
        {/* connectors — the selected node's line lights up */}
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
          {nodes.map((repo, i) => {
            const p = pos(i, nodes.length)
            const on = selectedId === repo.id
            return (
              <line
                key={repo.id}
                x1={50}
                y1={50}
                x2={p.x}
                y2={p.y}
                stroke={on ? repo.color : 'rgb(var(--c-line))'}
                strokeWidth={on ? 0.5 : 0.15}
              />
            )
          })}
        </svg>

        {/* hub = SuperMe — a gradient ring, less flat; token usage sits inside the circle. */}
        <button
          onClick={() => onSelectRepo('global')}
          className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center"
        >
          <span
            className={`relative grid place-items-center rounded-full bg-iris p-[2px] transition-shadow ${
              selectedId === 'global' ? 'shadow-[0_0_34px_-2px_var(--c-select)]' : 'shadow-[0_0_22px_-6px_#a78bfa]'
            }`}
          >
            {/* hub runs on the owner's own iris tag; the heartbeat/live ring uses a lavender accent. */}
            {stats.hub && (
              <LiveRing color="#a78bfa" running={stats.hub.running > 0} live={stats.hub.agents > 0} shape="rounded-full" />
            )}
            <span className="grid h-[116px] w-[116px] place-items-center rounded-full bg-app text-center leading-tight">
              <span className="flex flex-col items-center gap-1">
                <span className="text-[13px] font-semibold uppercase tracking-wide text-fg">
                  superme
                  <br />
                  hub
                </span>
                {stats.hub && <span className="font-mono text-[12px] text-faint">{fmtTokens(stats.hub.tokens)}</span>}
              </span>
            </span>
          </span>
        </button>

        {/* project nodes */}
        {nodes.map((repo, i) => {
          const p = pos(i, nodes.length)
          return (
            <div key={repo.id} className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: `${p.x}%`, top: `${p.y}%` }}>
              <NodeCard repo={repo} selected={selectedId === repo.id} onSelect={onSelectRepo} />
            </div>
          )
        })}

      </div>
    </div>
  )
}
