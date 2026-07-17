import { useCallback, useEffect, useMemo, useState } from 'react'
import { ReactFlow, Background, Controls, Handle, Position, type Node, type Edge, type NodeProps } from '@xyflow/react'
import dagre from 'dagre'
import { GitBranch, GitMerge, Loader2, Send } from 'lucide-react'
import '@xyflow/react/dist/style.css'
import ConfirmDialog from '@/ui/ConfirmDialog'
import WorkItemModal from './WorkItemModal'
import { PHASE_LABEL } from './common'
import {
  getWorkGraph, getDev, getAttention, pushInbox, planWorkItem, deleteWorkItem,
  type WorkGraphData, type WorkGraphNode, type DevData, type AttentionData, type WorkItem,
} from '@/lib/api'

// The WorkGraph view (S7/D3/D10) — "how does everything relate". Renders the DERIVED graph
// projection (repo root → deliverables → work-items, with spawned_from/supersedes edges) via
// React Flow + a dagre layout. Spawn rows waiting in the inbox appear as TRANSPARENT pushable
// nodes (push + start right here); pure inbox items stay invisible. Impl nodes carry git
// decoration (branch / merged); node click opens the same drilldown as the kanban.

const NODE_SIZE: Record<string, { w: number; h: number }> = {
  repo_root: { w: 150, h: 40 },
  deliverable: { w: 170, h: 44 },
  work_item: { w: 210, h: 62 },
  inbox_spawn: { w: 190, h: 50 },
}

function layout(gnodes: WorkGraphNode[], gedges: WorkGraphData['edges']): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'TB', nodesep: 28, ranksep: 56 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of gnodes) {
    const s = NODE_SIZE[n.kind] ?? NODE_SIZE.work_item
    g.setNode(n.id, { width: s.w, height: s.h })
  }
  for (const e of gedges) {
    // Layout direction: containment flows down; provenance/supersedes point AT the parent, so
    // reverse them for rank placement (children sit below what they spawned from).
    if (e.kind === 'contains') g.setEdge(e.src, e.dst)
    else g.setEdge(e.dst, e.src)
  }
  dagre.layout(g)
  const nodes: Node[] = gnodes.map((n) => {
    const pos = g.node(n.id)
    const s = NODE_SIZE[n.kind] ?? NODE_SIZE.work_item
    return {
      id: n.id,
      type: n.kind,
      position: { x: (pos?.x ?? 0) - s.w / 2, y: (pos?.y ?? 0) - s.h / 2 },
      data: n as unknown as Record<string, unknown>,
    }
  })
  const edges: Edge[] = gedges.map((e, i) => {
    const rel = e.relation
    const spawned = e.kind === 'spawned_from'
    return {
      id: `e${i}`,
      // spawned_from/supersedes edges point child → parent; render parent-on-top → child.
      source: e.kind === 'contains' ? e.src : e.dst,
      target: e.kind === 'contains' ? e.dst : e.src,
      animated: spawned && rel === 'blocking',
      style: {
        stroke: spawned
          ? rel === 'blocking' ? 'rgb(var(--c-warn))' : rel === 'parallel' ? 'rgb(var(--c-accent))' : 'rgb(var(--c-faint))'
          : e.kind === 'supersedes' ? 'rgb(var(--c-danger))' : 'rgb(var(--c-line))',
        strokeDasharray: e.kind === 'supersedes' || rel === 'spawn' ? '4 3' : undefined,
      },
      label: spawned ? rel : undefined,
      labelStyle: { fontSize: 9, fill: 'rgb(var(--c-faint))' },
      labelBgStyle: { fill: 'transparent' },
    }
  })
  return { nodes, edges }
}

export default function WorkGraphView({
  contextId, onBindItem,
}: {
  contextId: string
  onBindItem?: (it: WorkItem, contextId: string) => void
}) {
  const [graph, setGraph] = useState<WorkGraphData | null>(null)
  const [data, setData] = useState<DevData | null>(null)
  const [attn, setAttn] = useState<AttentionData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [reviewId, setReviewId] = useState<string | null>(null)
  const [confirmDel, setConfirmDel] = useState<WorkItem | null>(null)
  const [pushing, setPushing] = useState<number | null>(null)

  const load = useCallback(() => {
    getWorkGraph(contextId).then(setGraph).catch((e) => setErr(String(e)))
    getDev(contextId).then(setData).catch(() => {})
    getAttention(contextId).then(setAttn).catch(() => {})
  }, [contextId])
  useEffect(load, [load])
  // Re-poll while anything runs (mirrors the board's cadence, but lighter: 5s).
  useEffect(() => {
    if (!data?.running?.length) return
    const t = setTimeout(load, 5000)
    return () => clearTimeout(t)
  }, [data?.running, load])

  // Push a spawn row into a real work-item straight from the graph (D10: push + start here).
  const push = useCallback(async (inboxId: number) => {
    setPushing(inboxId)
    try {
      await pushInbox(inboxId, contextId)
    } catch (e) {
      setErr(String(e))
    } finally {
      setPushing(null)
      load()
    }
  }, [contextId, load])

  const bucketOf = useMemo(() => {
    const m: Record<string, string> = {}
    for (const tier of ['needs_you', 'running', 'unread'] as const) {
      for (const r of attn?.buckets?.[tier] ?? []) m[r.id] = tier
    }
    return m
  }, [attn])

  const flow = useMemo(() => {
    if (!graph) return { nodes: [] as Node[], edges: [] as Edge[] }
    const { nodes, edges } = layout(graph.nodes, graph.edges)
    for (const n of nodes) {
      const d = n.data as unknown as WorkGraphNode
      if (d.kind === 'work_item') {
        const iid = d.id.replace(/^item:/, '')
        ;(n.data as Record<string, unknown>).bucket = bucketOf[iid]
        ;(n.data as Record<string, unknown>).onOpen = () => {
          const it = data?.work_items.find((w) => w.id === iid)
          if (!it) return
          setReviewId(iid)
          onBindItem?.(it, contextId)
        }
      }
      if (d.kind === 'inbox_spawn') {
        ;(n.data as Record<string, unknown>).onPush = () => push(d.inbox_id!)
        ;(n.data as Record<string, unknown>).pushing = pushing === d.inbox_id
      }
    }
    return { nodes, edges }
  }, [graph, bucketOf, data, pushing, contextId, onBindItem, push])

  const reviewItem = reviewId ? (data?.work_items.find((w) => w.id === reviewId) ?? null) : null

  return (
    <div className="relative h-full">
      {reviewItem && (
        <WorkItemModal
          it={reviewItem}
          contextId={contextId}
          onClose={() => setReviewId(null)}
          onPlan={async (it, model, effort) => {
            try {
              await planWorkItem(it.id, contextId, model, effort)
            } catch { /* surfaced on reload */ }
            load()
          }}
          onDelete={(it) => setConfirmDel(it)}
          onChanged={load}
        />
      )}
      {confirmDel && (
        <ConfirmDialog
          title="Delete this work-item?"
          body={<>“{confirmDel.title || confirmDel.id}” will be permanently removed — work-item, session, and inbox row. This can’t be undone.</>}
          confirmLabel="Delete"
          z="z-50"
          onCancel={() => setConfirmDel(null)}
          onConfirm={async () => {
            const it = confirmDel
            setConfirmDel(null)
            setReviewId(null)
            try {
              await deleteWorkItem(it.id, contextId)
            } catch (e) {
              setErr(String(e))
            }
            load()
          }}
        />
      )}
      {err && (
        <div className="absolute left-3 top-3 z-10 rounded-md border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-xs text-danger">
          {err}
        </div>
      )}
      {graph?.cycles?.length ? (
        <div className="absolute right-3 top-3 z-10 rounded-md border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-xs text-warn">
          provenance cycle: {graph.cycles[0].join(' → ')}
        </div>
      ) : null}
      {!graph ? (
        <div className="flex h-full items-center justify-center gap-2 text-sm text-muted">
          <Loader2 size={15} className="animate-spin" /> Building the graph…
        </div>
      ) : (
        <ReactFlow
          nodes={flow.nodes}
          edges={flow.edges}
          nodeTypes={NODE_TYPES}
          fitView
          minZoom={0.3}
          maxZoom={1.6}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
        >
          <Background gap={22} size={1} color="rgb(var(--c-line) / 0.6)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      )}
    </div>
  )
}

// --- node renderers -------------------------------------------------------------

const BUCKET_RING: Record<string, string> = {
  needs_you: 'ring-2 ring-warn/80',
  running: 'ring-2 ring-success/70',
  unread: 'ring-2 ring-accent/70',
}

function Dots() {
  return (
    <>
      <Handle type="target" position={Position.Top} className="!h-1 !w-1 !border-0 !bg-transparent" />
      <Handle type="source" position={Position.Bottom} className="!h-1 !w-1 !border-0 !bg-transparent" />
    </>
  )
}

function RepoRootNode({ data }: NodeProps) {
  const d = data as unknown as WorkGraphNode
  return (
    <div className="rounded-lg px-4 py-2 text-center text-[13px] font-semibold text-on-accent" style={{ backgroundImage: 'var(--grad-iris)' }}>
      {d.label}
      <Dots />
    </div>
  )
}

function DeliverableNode({ data }: NodeProps) {
  const d = data as unknown as WorkGraphNode
  return (
    <div className="w-[170px] rounded-lg border border-line bg-sunken px-3 py-2 text-center">
      <div className="truncate text-[12px] font-medium text-fg">{d.label}</div>
      <div className="font-mono text-[9px] text-faint">{d.slug}</div>
      <Dots />
    </div>
  )
}

function WorkItemNode({ data }: NodeProps) {
  const d = data as unknown as WorkGraphNode & { bucket?: string; onOpen?: () => void }
  const terminal = d.status === 'done'
  return (
    <div
      onClick={d.onOpen}
      title="Open drilldown"
      className={`w-[210px] cursor-pointer rounded-lg border border-line bg-surface px-3 py-2 shadow-sm transition hover:border-accent ${
        BUCKET_RING[d.bucket ?? ''] ?? ''
      } ${terminal ? 'opacity-60' : ''}`}
    >
      <div className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${terminal ? 'bg-success' : 'animate-pulse bg-accent'}`} />
        <span className="truncate text-[12px] font-medium text-fg">{d.label}</span>
      </div>
      <div className="mt-1 flex items-center gap-1.5 text-[9.5px] text-faint">
        <span className="rounded bg-hover px-1 py-px uppercase tracking-wide">{PHASE_LABEL[d.phase ?? ''] ?? d.phase}</span>
        <span>{d.item_kind ?? 'impl'}</span>
        {d.git_branch && (
          <span className="ml-auto inline-flex items-center gap-0.5 font-mono" title={d.git_branch ?? undefined}>
            {d.git_merged ? <GitMerge size={10} className="text-success" /> : <GitBranch size={10} />}
            {d.git_merged ? 'merged' : 'branch'}
          </span>
        )}
      </div>
      <Dots />
    </div>
  )
}

// A spawn row still waiting in the inbox — transparent (it isn't real work yet) with the push
// gate right on the node: one click turns it into a work-item and the graph re-renders it solid.
function InboxSpawnNode({ data }: NodeProps) {
  const d = data as unknown as WorkGraphNode & { onPush?: () => void; pushing?: boolean }
  return (
    <div className="w-[190px] rounded-lg border border-dashed border-faint/60 bg-surface/40 px-3 py-2">
      <div className="truncate text-[11.5px] text-muted">{d.label}</div>
      <button
        onClick={d.onPush}
        disabled={d.pushing}
        title="Push into a real work-item and start it (opens at triage)"
        className="mt-1 inline-flex items-center gap-1 rounded bg-accent/90 px-1.5 py-0.5 text-[10px] font-medium text-on-accent hover:opacity-90 disabled:opacity-50"
      >
        {d.pushing ? <Loader2 size={10} className="animate-spin" /> : <Send size={10} />} Push &amp; start
      </button>
      <Dots />
    </div>
  )
}

const NODE_TYPES = {
  repo_root: RepoRootNode,
  deliverable: DeliverableNode,
  work_item: WorkItemNode,
  inbox_spawn: InboxSpawnNode,
}
