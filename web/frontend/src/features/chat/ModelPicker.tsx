import { useState } from 'react'
import { Check, ChevronLeft } from 'lucide-react'
import { MODELS, EFFORTS, toModelKey } from '@/lib/format'

// The model and effort picker, in two steps, firing ONE combined command on the effort pick.
// Default resets the override.
export default function ModelPicker({
  currentModel,
  currentEffort,
  onPick,
}: {
  currentModel: string | null // the context's model-override alias, or null for the system default
  currentEffort: string | null // the context's effort-override level, or null for the system default
  onPick: (model: string, effort: string) => void // both values ('reset' for either = clear)
}) {
  const [model, setModel] = useState<string | null>(null) // chosen in step 1, pending effort

  const modelRows = [
    { value: 'reset', label: 'Default', blurb: 'Inherit the system / repo default', selected: !currentModel },
    ...MODELS.map((m) => ({ value: m.key, label: m.label, blurb: m.blurb, selected: toModelKey(currentModel) === m.key })),
  ]
  const effortRows = [
    { value: 'reset', label: 'Default', blurb: 'Inherit the system / repo default', selected: !currentEffort },
    ...EFFORTS.map((e) => ({ value: e.key, label: e.label, blurb: e.blurb, selected: currentEffort === e.key })),
  ]

  const inEffort = model !== null
  const rows = inEffort ? effortRows : modelRows
  const modelLabel = model === 'reset' ? 'Default' : MODELS.find((m) => m.key === model)?.label ?? model

  return (
    <div className="absolute bottom-full left-0 mb-2 w-full overflow-hidden rounded-lg border border-line bg-surface shadow-lg">
      <div className="flex items-center gap-1.5 border-b border-line px-3 py-1 text-[10px] uppercase tracking-wide text-muted">
        {inEffort ? (
          <>
            <button onClick={() => setModel(null)} className="flex items-center gap-0.5 rounded text-muted hover:text-fg" title="Back to model">
              <ChevronLeft size={12} /> {modelLabel}
            </button>
            <span className="text-faint">·</span> select effort
          </>
        ) : (
          'select a model'
        )}
      </div>
      {rows.map((r) => (
        <button
          key={r.value}
          onClick={() => (inEffort ? onPick(model!, r.value) : setModel(r.value))}
          className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-hover"
        >
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-medium text-fg">{r.label}</span>
            <span className="block truncate text-[11px] text-muted">{r.blurb}</span>
          </span>
          {r.selected && <Check size={14} className="shrink-0 text-[var(--chat-accent)]" />}
        </button>
      ))}
    </div>
  )
}
