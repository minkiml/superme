import { useState } from 'react'
import MeDashboard from './components/MeDashboard'

// The extensible shell: a sidebar of dashboards. Today only "Me" (global) is real;
// Projects / per-sub-SuperMe dashboards plug in here later without a rewrite.
const DASHBOARDS = [
  { id: 'me', label: 'Me', sub: 'global', enabled: true },
  { id: 'projects', label: 'Projects', sub: 'soon', enabled: false },
]

export default function App() {
  const [active] = useState('me')

  return (
    <div className="flex h-full bg-slate-950 text-slate-100">
      <aside className="flex w-52 flex-col border-r border-slate-800 bg-slate-900">
        <div className="px-4 py-4 text-lg font-bold tracking-tight">
          Super<span className="text-sky-400">Me</span>
        </div>
        <nav className="flex-1 space-y-1 px-2">
          {DASHBOARDS.map((d) => (
            <button
              key={d.id}
              disabled={!d.enabled}
              className={`flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm ${
                d.enabled
                  ? active === d.id
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-300 hover:bg-slate-800'
                  : 'cursor-default text-slate-600'
              }`}
            >
              <span>{d.label}</span>
              <span className="text-xs text-slate-500">{d.sub}</span>
            </button>
          ))}
        </nav>
        <div className="px-4 py-3 text-xs text-slate-600">localhost · single-owner</div>
      </aside>
      <main className="min-w-0 flex-1 overflow-hidden">
        <MeDashboard />
      </main>
    </div>
  )
}
