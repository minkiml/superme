import { Coins, FolderKanban, Cpu, GraduationCap, ChevronRight } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { fmtTokens } from '@/lib/format'
import type { CommandStats } from './useCommandStats'

// The global stats strip — chrome atop System & Dev, fed by real spine data (token aggregation
// + live counts + learning). Values show '—' until the first load lands.
type Tile = { id: string; label: string; icon: LucideIcon; hint: string; dot: string; value: (s: CommandStats) => string }

const TILES: Tile[] = [
  { id: 'tokens', label: 'Tokens', icon: Coins, hint: 'per repo · feature · scope', dot: 'bg-dev', value: (s) => fmtTokens(s.tokensTotal) },
  { id: 'projects', label: 'Projects', icon: FolderKanban, hint: 'connected', dot: 'bg-core', value: (s) => String(s.projects) },
  { id: 'ops', label: 'Agents', icon: Cpu, hint: 'running / live', dot: 'bg-universal', value: (s) => `${s.opsRunning}/${s.opsLive}` },
  { id: 'learning', label: 'Learning', icon: GraduationCap, hint: 'candidates / pending / learned', dot: 'bg-warn', value: (s) => `${s.learn.candidates}/${s.learn.pending}/${s.learn.learned}` },
]

export default function GlobalStrip({ stats, onDetails }: { stats: CommandStats; onDetails?: (id: string) => void }) {
  return (
    <div className="flex shrink-0 items-stretch border-b border-line bg-surface/40">
      {TILES.map((t) => {
        const Icon = t.icon
        return (
          <button
            key={t.id}
            onClick={() => onDetails?.(t.id)}
            className="group flex flex-1 flex-col gap-1.5 border-r border-line px-5 py-3 text-left last:border-r-0 hover:bg-hover/40"
          >
            <div className="flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wider text-muted">
              <span className={`h-1.5 w-1.5 rounded-full ${t.dot}`} />
              {t.label}
            </div>
            <div className="font-mono text-[34px] font-medium leading-none tabular-nums text-fg">
              {stats.loading ? '—' : t.value(stats)}
            </div>
            <div className="flex items-center gap-1 text-[13px] text-faint">
              {t.hint}
              <ChevronRight size={11} className="opacity-0 transition-opacity group-hover:opacity-100" />
            </div>
          </button>
        )
      })}
    </div>
  )
}
