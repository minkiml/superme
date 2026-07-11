import { useEffect, useState } from 'react'
import { Sparkles, FolderGit2, Folder, ArrowLeft, ArrowUp, Loader2, Check, Home, Lock, CornerDownRight } from 'lucide-react'
import { browseFs, connectRepo, type FsBrowse, type ConnectedRepo } from '@/lib/api'

// Connect a domain (S5) — link a directory as a new repo/project, Finder-style: a sidebar of quick
// locations + a folder list you navigate. Two kinds: NEW (greenfield → project-init) creates a folder
// you name; EXISTING (has code → retrofit) selects a folder you're in. The kind is stored on the repo
// and drives its onboarding front door. Registration seeds the knowledge home; the orbit poll then
// surfaces the node, from which the owner opens onboarding.

type Kind = 'new' | 'existing'

export default function ConnectModal({ onClose, onConnected }: {
  onClose: () => void
  onConnected: (repo: ConnectedRepo) => void
}) {
  const [kind, setKind] = useState<Kind | null>(null)
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-6 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-2xl overflow-hidden rounded-2xl border border-line bg-app shadow-2xl" onClick={(e) => e.stopPropagation()}>
        {kind === null ? <KindStep onPick={setKind} onClose={onClose} /> : <BrowseStep kind={kind} onBack={() => setKind(null)} onConnected={onConnected} />}
      </div>
    </div>
  )
}

function KindStep({ onPick, onClose }: { onPick: (k: Kind) => void; onClose: () => void }) {
  return (
    <div className="p-6">
      <h2 className="text-[16px] font-semibold text-fg">Connect a domain</h2>
      <p className="mt-1 text-[13px] text-muted">Point SuperMe at a project directory. It becomes a node in the orbit, ready to onboard.</p>
      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <button onClick={() => onPick('new')} className="flex flex-col rounded-xl border border-line bg-surface p-4 text-left hover:border-dev">
          <Sparkles size={18} className="text-dev" />
          <div className="mt-2 text-[14px] font-semibold text-fg">New project</div>
          <p className="mt-1 text-[12px] leading-relaxed text-muted">Create a fresh folder. SuperMe will run <span className="font-mono text-[11px]">project-init</span> to establish its memory.</p>
        </button>
        <button onClick={() => onPick('existing')} className="flex flex-col rounded-xl border border-line bg-surface p-4 text-left hover:border-dev">
          <FolderGit2 size={18} className="text-dev" />
          <div className="mt-2 text-[14px] font-semibold text-fg">Existing project</div>
          <p className="mt-1 text-[12px] leading-relaxed text-muted">Link a codebase you already have. SuperMe will <span className="font-mono text-[11px]">retrofit</span> its memory from the code.</p>
        </button>
      </div>
      <div className="mt-5 flex justify-end">
        <button onClick={onClose} className="rounded-lg border border-line bg-surface px-4 py-2 text-[13px] text-muted hover:text-fg">Cancel</button>
      </div>
    </div>
  )
}

// Quick-location shortcuts, derived from the home dir the first browse reports.
const QUICK: { name: string; sub: string | null }[] = [
  { name: 'Home', sub: null },
  { name: 'Developer', sub: 'Developer' },
  { name: 'Desktop', sub: 'Desktop' },
  { name: 'Documents', sub: 'Documents' },
  { name: 'Downloads', sub: 'Downloads' },
]

function BrowseStep({ kind, onBack, onConnected }: { kind: Kind; onBack: () => void; onConnected: (r: ConnectedRepo) => void }) {
  const [fs, setFs] = useState<FsBrowse | null>(null)
  const [home, setHome] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)

  function go(path?: string) {
    setErr(null); setFs(null)
    browseFs(path)
      .then((r) => { setFs(r); if (!home) setHome(r.path) }) // first load lands on home
      .catch((e) => setErr(String(e)))
  }
  useEffect(() => { go() }, [])

  const targetPath = fs ? (kind === 'new' ? `${fs.path}/${newName.trim()}` : fs.path) : ''
  const defaultLabel = kind === 'new' ? newName.trim() : (fs ? fs.path.split('/').pop() || '' : '')
  const canConnect = !!fs && (kind === 'existing' || newName.trim().length > 0) && !busy

  function submit() {
    if (!canConnect) return
    setBusy(true); setErr(null)
    connectRepo({ path: targetPath, label: label.trim() || defaultLabel, kind })
      .then(onConnected)
      .catch((e) => { setErr(String(e)); setBusy(false) })
  }

  return (
    <div className="flex max-h-[80vh] flex-col">
      {/* title bar */}
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <button onClick={onBack} className="rounded-md p-1 text-muted hover:bg-hover hover:text-fg" title="Back"><ArrowLeft size={16} /></button>
        <span className="text-[13.5px] font-semibold text-fg">{kind === 'new' ? 'New project' : 'Existing project'}</span>
        <span className="ml-auto font-mono text-[10.5px] text-faint">{kind === 'new' ? 'project-init' : 'retrofit'}</span>
      </div>

      <div className="flex min-h-[16rem]">
        {/* sidebar — quick locations */}
        <div className="w-40 shrink-0 border-r border-line bg-surface/50 p-2">
          <div className="px-2 pb-1 pt-0.5 text-[10px] font-medium uppercase tracking-wider text-faint">Locations</div>
          {QUICK.map((q) => {
            const path = home ? (q.sub ? `${home}/${q.sub}` : home) : null
            const active = !!fs && !!path && fs.path === path
            return (
              <button
                key={q.name}
                disabled={!home}
                onClick={() => path && go(path)}
                className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] ${active ? 'bg-dev/10 text-dev' : 'text-muted hover:bg-hover hover:text-fg'}`}
              >
                {q.name === 'Home' ? <Home size={14} className="shrink-0" /> : <Folder size={14} className="shrink-0" />}
                <span className="truncate">{q.name}</span>
              </button>
            )
          })}
        </div>

        {/* main — path bar + list */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <button onClick={() => fs?.parent && go(fs.parent)} disabled={!fs?.parent}
              className="rounded-md p-1 text-muted enabled:hover:bg-hover enabled:hover:text-fg disabled:opacity-30" title="Up one level"><ArrowUp size={15} /></button>
            <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-muted" dir="rtl" title={fs?.path}>{fs?.path ?? '…'}</span>
          </div>
          <div className="min-h-[10rem] flex-1 overflow-y-auto px-2 py-2">
            {err && <div className="px-3 py-2 text-[12px] text-danger">{err}</div>}
            {!fs ? (
              <div className="flex items-center gap-2 px-3 py-4 text-[13px] text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>
            ) : fs.locked ? (
              <div className="flex items-center gap-2 px-3 py-4 text-[12px] text-faint"><Lock size={14} className="shrink-0" /> macOS blocks access to this folder. Pick another, or grant SuperMe Full Disk Access.</div>
            ) : fs.entries.length === 0 ? (
              <div className="px-3 py-4 text-[12px] italic text-faint">No sub-folders here.</div>
            ) : (
              fs.entries.map((e) => (
                <button key={e.path} onClick={() => go(e.path)} className="flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left hover:bg-hover">
                  {e.is_git ? <FolderGit2 size={15} className="shrink-0 text-dev" /> : <Folder size={15} className="shrink-0 text-faint" />}
                  <span className="min-w-0 flex-1 truncate text-[13px] text-fg">{e.name}</span>
                  {e.is_git && <span className="shrink-0 text-[10px] uppercase tracking-wide text-dev">git</span>}
                  {!e.is_git && e.empty && <span className="shrink-0 text-[10px] uppercase tracking-wide text-faint">empty</span>}
                </button>
              ))
            )}
          </div>
        </div>
      </div>

      {/* footer — action */}
      <div className="border-t border-line px-4 py-3">
        {kind === 'new' && (
          <label className="mb-2 flex items-center gap-2 text-[12px] text-muted">
            <CornerDownRight size={13} className="shrink-0" />
            <span className="shrink-0">New folder</span>
            <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="my-project"
              className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1 font-mono text-[12px] text-fg outline-none focus:border-dev" />
          </label>
        )}
        <div className="flex items-center gap-2">
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={defaultLabel || 'Project name'}
            className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2.5 py-1.5 text-[13px] text-fg outline-none focus:border-dev" />
          <button onClick={submit} disabled={!canConnect}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-dev px-3.5 py-1.5 text-[13px] font-medium text-white enabled:hover:opacity-90 disabled:opacity-40">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Connect
          </button>
        </div>
        <p className="mt-2 truncate font-mono text-[10.5px] text-faint" title={targetPath}>
          {kind === 'new' ? (newName.trim() ? targetPath : 'name the folder to create…') : targetPath}
        </p>
      </div>
    </div>
  )
}
