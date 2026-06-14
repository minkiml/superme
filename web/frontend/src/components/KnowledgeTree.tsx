import { useEffect, useState } from 'react'
import { getTree, type TreeNode } from '../api'

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
  const pad = { paddingLeft: `${depth * 12 + 8}px` }

  if (node.type === 'dir') {
    return (
      <div>
        {node.path !== '' && (
          <button
            className="flex w-full items-center gap-1 py-1 text-left text-sm text-slate-300 hover:bg-slate-800"
            style={pad}
            onClick={() => setOpen((o) => !o)}
          >
            <span className="text-slate-500">{open ? '▾' : '▸'}</span>
            <span className="font-medium">{node.name}</span>
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
      className={`flex w-full items-center gap-1 py-1 text-left text-sm hover:bg-slate-800 ${
        isSel ? 'bg-slate-800 text-white' : 'text-slate-400'
      }`}
      style={pad}
      onClick={() => onSelect(node.path)}
    >
      <span className="text-slate-600">·</span>
      {node.name}
    </button>
  )
}

export default function KnowledgeTree({
  selected,
  onSelect,
  refreshKey,
}: {
  selected: string | null
  onSelect: (path: string) => void
  refreshKey: number
}) {
  const [tree, setTree] = useState<TreeNode | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    getTree()
      .then(setTree)
      .catch((e) => setErr(String(e)))
  }, [refreshKey])

  return (
    <div className="flex h-full flex-col">
      <div className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Knowledge
      </div>
      <div className="flex-1 overflow-auto pb-4">
        {err && <div className="px-3 text-sm text-red-400">{err}</div>}
        {tree && <Node node={tree} depth={0} selected={selected} onSelect={onSelect} />}
      </div>
    </div>
  )
}
