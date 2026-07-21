import AttentionCenter from './AttentionCenter'
import type { SystemHold } from '@/lib/api'

// Full-width top bar: the brand, plus the attention center (Pass 2 · Q2) — a bell that surfaces every
// item across all projects that's holding for the owner. Theme lives in the nav footer now.
export default function TopBar({ onGoto }: { onGoto: (repoId: string, hold: SystemHold) => void }) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line bg-sidebar px-4">
      <div className="flex items-center gap-2.5">
        <span className="h-7 w-7 rounded-lg bg-iris shadow-sm" />
        <span className="text-[17px] font-semibold tracking-tight text-fg">superme</span>
      </div>
      <div className="ml-auto flex items-center">
        <AttentionCenter onGoto={onGoto} />
      </div>
    </header>
  )
}
