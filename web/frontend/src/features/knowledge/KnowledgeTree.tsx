import { useEffect, useState } from 'react'
import { ChevronRight, Folder, FolderOpen, FileText, PanelLeftClose } from 'lucide-react'
import { getTree, type TreeNode } from '@/lib/api'

function Node({
  node,
  depth,
  selected,
  onSelect,
}: {
  node: TreeNode
  depth: number
  selected: string | null
  onSelect: (path: string) => void
}) {
  const [open, setOpen] = useState(true)
  const pad = { paddingLeft: `${depth * 14 + 10}px` }

  if (node.type === 'dir') {
    return (
      <div>
        {node.path !== '' && (
          <button
            className="flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left text-[13px] font-medium text-fg hover:bg-hover"
            style={pad}
            onClick={() => setOpen((o) => !o)}
          >
            <ChevronRight
              size={13}
              className="shrink-0 text-faint transition-transform"
              style={{ transform: open ? 'rotate(90deg)' : 'none' }}
            />
            {open ? (
              <FolderOpen size={14} className="shrink-0 text-accent-text" />
            ) : (
              <Folder size={14} className="shrink-0 text-accent-text" />
            )}
            <span className="truncate">{node.name}</span>
          </button>
        )}
        {open &&
          node.children?.map((c) => (
            <Node key={c.path} node={c} depth={node.path === '' ? 0 : depth + 1} selected={selected} onSelect={onSelect} />
          ))}
      </div>
    )
  }

  const isSel = selected === node.path
  return (
    <button
      className={`flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left text-[13px] ${
        isSel ? 'bg-accent-soft text-accent-text' : 'text-muted hover:bg-hover hover:text-fg'
      }`}
      style={{ paddingLeft: `${depth * 14 + 27}px` }}
      onClick={() => onSelect(node.path)}
    >
      <FileText size={13} className="shrink-0 opacity-70" />
      <span className="truncate">{node.name}</span>
    </button>
  )
}

export default function KnowledgeTree({
  selected,
  onSelect,
  refreshKey,
  contextId = 'global',
  onCollapse,
}: {
  selected: string | null
  onSelect: (path: string) => void
  refreshKey: number
  contextId?: string
  onCollapse?: () => void
}) {
  const [tree, setTree] = useState<TreeNode | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setErr(null)
    getTree(contextId)
      .then(setTree)
      .catch((e) => setErr(String(e)))
  }, [refreshKey, contextId])

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-3 py-2.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-faint">Files</span>
        {onCollapse && (
          <button
            onClick={onCollapse}
            title="Hide files panel"
            aria-label="Hide files panel"
            className="rounded p-0.5 text-faint hover:bg-hover hover:text-fg"
          >
            <PanelLeftClose size={15} />
          </button>
        )}
      </div>
      <div className="flex-1 space-y-0.5 overflow-auto px-2 pb-4">
        {err && <div className="px-2 text-xs text-danger">Couldn’t load files.</div>}
        {tree && tree.children?.length === 0 && (
          <div className="px-2 text-xs text-muted">No files yet — inject one below.</div>
        )}
        {tree && <Node node={tree} depth={0} selected={selected} onSelect={onSelect} />}
      </div>
    </div>
  )
}
