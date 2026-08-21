import { useState } from 'react'
import { X, Loader2, Check } from 'lucide-react'
import Modal from '@/ui/Modal'
import { PALETTE, colorFor } from '@/lib/palette'
import { REPO_ICONS, REPO_ICON_KEYS, RepoIcon } from '@/lib/repoIcons'
import { setRepoMeta } from '@/lib/api'
import type { OrbitRepo } from './useCommandStats'

// A repo's coloured square is an owner-defined TAG: a colour and optionally an icon shown in its
// place.
//
// Saving is optimistic and persists in the background.

export default function TagEditor({
  repo,
  onClose,
  onSaved,
}: {
  repo: OrbitRepo
  onClose: () => void
  onSaved?: (patch: { color: string; icon: string | null }) => void
}) {
  const isHub = repo.id === 'global'
  const [color, setColor] = useState(repo.color)
  const [icon, setIcon] = useState<string | null>(repo.icon)
  const [saving, setSaving] = useState(false)

  const defaultColor = colorFor(repo.id)
  // Same diff rule as every editor. The gate itself does not apply — a swatch picker IS the edit
  // mode.
  const dirty = color !== repo.color || icon !== repo.icon

  async function save() {
    setSaving(true)
    onSaved?.({ color, icon }) // optimistic — the orbit/inspector update instantly
    onClose()
    // Persist in the background: '' clears back to defaults, a value sets it.
    setRepoMeta(repo.id, { color: color === defaultColor ? '' : color, icon: icon ?? '' }).catch(() => {})
  }

  return (
    <Modal onClose={onClose} maxW="max-w-xs" z="z-[60]" scrim="bg-black/60">
        <div className="flex items-center gap-2.5 border-b border-line px-4 py-3">
          <span
            className="grid h-8 w-8 place-items-center rounded-lg text-[13px] font-semibold text-app"
            style={isHub ? { backgroundImage: 'var(--grad-iris)' } : { backgroundColor: color }}
          >
            {icon ? <RepoIcon name={icon} size={16} className="text-app" /> : isHub ? '' : repo.label.slice(0, 2).toUpperCase()}
          </span>
          <span className="flex-1 truncate text-[14px] font-semibold text-fg">{isHub ? 'SuperMe Hub' : repo.label}</span>
          <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-hover hover:text-fg">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-4 p-4">
          {/* color */}
          <div>
            <div className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-muted">Tag color</div>
            <div className="flex flex-wrap gap-2">
              {[defaultColor, ...PALETTE.filter((c) => c !== defaultColor)].map((c) => (
                <button
                  key={c}
                  onClick={() => setColor(c)}
                  style={{ backgroundColor: c }}
                  className={`grid h-7 w-7 place-items-center rounded-[6px] transition ${color === c ? 'ring-2 ring-fg ring-offset-2 ring-offset-app' : ''}`}
                >
                  {color === c && <Check size={14} className="text-app" />}
                </button>
              ))}
            </div>
          </div>

          {/* icon */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[12px] font-semibold uppercase tracking-wider text-muted">Icon</span>
              {icon && (
                <button onClick={() => setIcon(null)} className="text-[12px] text-faint hover:text-fg">Remove</button>
              )}
            </div>
            <div className="grid grid-cols-8 gap-1.5">
              {REPO_ICON_KEYS.map((key) => {
                const Icon = REPO_ICONS[key]
                const on = icon === key
                return (
                  <button
                    key={key}
                    onClick={() => setIcon(key)}
                    style={on ? { borderColor: color, color } : undefined}
                    className={`grid h-8 place-items-center rounded-lg border transition ${on ? 'bg-hover' : 'border-line text-muted hover:bg-hover hover:text-fg'}`}
                  >
                    <Icon size={15} />
                  </button>
                )
              })}
            </div>
            <p className="mt-1.5 text-[11px] text-faint">An icon shows in place of the color — the tag color stays underneath.</p>
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-4 py-3">
          <button onClick={onClose} className="rounded-lg border border-line px-3 py-1.5 text-[13px] text-muted hover:bg-hover hover:text-fg">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={saving || !dirty}
            className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-on-accent hover:opacity-90 disabled:opacity-40"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Save
          </button>
        </div>
    </Modal>
  )
}
