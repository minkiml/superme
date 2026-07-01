import { PanelRightClose } from 'lucide-react'
import Dropdown from '@/ui/Dropdown'
import type { ContextRef } from '@/lib/contexts'
import type { ChatMode } from '@/lib/api'
import type { RunMeta } from './types'
import { formatModel } from './util'

// Compact Core | Dev segmented toggle — switches the chat's agent mode (and its session scope).
function ModeToggle({ mode, busy, onChange }: { mode: ChatMode; busy: boolean; onChange: (m: ChatMode) => void }) {
  const opt = (m: ChatMode, label: string) => (
    <button
      onClick={() => !busy && mode !== m && onChange(m)}
      disabled={busy}
      title={m === 'dev' ? 'Dev mode — build SuperMe (dev-knowledge + dev skills)' : 'Core mode — the twin'}
      className={`rounded px-1.5 py-0.5 text-[10.5px] font-medium transition ${
        mode === m ? 'bg-accent text-on-accent' : 'text-muted hover:text-fg'
      } disabled:opacity-50`}
    >
      {label}
    </button>
  )
  return (
    <div className="flex shrink-0 items-center gap-0.5 rounded-md border border-line bg-sunken p-0.5">
      {opt('core', 'Core')}
      {opt('dev', 'Dev')}
    </div>
  )
}

// The chat rail's top bar: connection dot + context selector (when more than one
// SuperMe), a collapse control, and the active-conversation button that opens the
// sessions drawer (with the live model · context% readout on its right).
export default function ChatHeader({
  ready,
  contexts,
  contextId,
  onContextChange,
  busy,
  onCollapse,
  activeTitle,
  meta,
  onOpenDrawer,
  mode,
  onModeChange,
}: {
  ready: boolean
  contexts: ContextRef[]
  contextId: string
  onContextChange?: (id: string) => void
  busy: boolean
  onCollapse?: () => void
  activeTitle: string
  meta: RunMeta | null
  onOpenDrawer: () => void
  mode: ChatMode
  onModeChange: (m: ChatMode) => void
}) {
  const ctxLabel = contexts.find((c) => c.id === contextId)?.label ?? contextId
  return (
    <div className="shrink-0 border-b border-line px-4 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${ready ? 'bg-success' : 'bg-faint'}`}
            title={ready ? 'connected' : 'connecting'}
          />
          {contexts.length > 1 ? (
            <Dropdown
              variant="bare"
              value={contextId}
              disabled={busy}
              title="Which SuperMe this chat talks to"
              options={contexts.map((c) => ({ value: c.id, label: c.label }))}
              onChange={(v) => onContextChange?.(v)}
            />
          ) : (
            <span className="truncate text-sm font-medium text-fg">{ctxLabel}</span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <ModeToggle mode={mode} busy={busy} onChange={onModeChange} />
          {onCollapse && (
          <button
            onClick={onCollapse}
            className="shrink-0 rounded-md p-1.5 text-muted hover:bg-hover hover:text-fg"
            title="Collapse chat"
            aria-label="Collapse chat"
          >
            <PanelRightClose size={17} strokeWidth={1.75} />
          </button>
          )}
        </div>
      </div>
      <button
        onClick={onOpenDrawer}
        className="mt-2 flex w-full items-center justify-between gap-2 rounded-md border border-line bg-sunken px-2.5 py-1.5 text-xs hover:bg-hover"
      >
        <span className="truncate text-fg">{activeTitle}</span>
        <span className="shrink-0 text-muted">
          {meta && (meta.model || meta.pct != null) ? (
            <>
              {meta.model && formatModel(meta.model)}
              {meta.pct != null && meta.window && ` · ${meta.pct}%`}
            </>
          ) : (
            'history'
          )}
        </span>
      </button>
    </div>
  )
}
