import { useState } from 'react'
import { ScrollText, X } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import Toggle from '@/ui/Toggle'
import { toggleConstitution } from '@/lib/api'

// The preview popup for one constitution — universal or local, learned or hand-authored. Mirrors the
// skill/agent file modals: opens over the row, shows the description + body, and carries the same
// enable/disable Toggle used everywhere else. Keyed by (scope, slug) via toggleConstitution.
export default function ConstitutionModal({
  slug, scope, title, mode, description, body, enabled, learned = false, contextId = 'global', tint = 'dev',
  onClose, onToggled,
}: {
  slug: string
  scope: string
  title: string
  mode?: string
  description?: string | null
  body: string
  enabled: boolean
  learned?: boolean
  contextId?: string
  tint?: 'universal' | 'dev' | 'core'
  onClose: () => void
  onToggled?: () => void
}) {
  const [on, setOn] = useState(enabled)
  const [busy, setBusy] = useState(false)

  async function toggle(v: boolean) {
    setBusy(true)
    try {
      await toggleConstitution(slug, scope, v, contextId)
      setOn(v)
      onToggled?.()
    } finally {
      setBusy(false)
    }
  }

  const modeTint = mode === 'core' ? 'text-core' : mode ? 'text-dev' : 'text-universal'
  const onColor = tint === 'core' ? 'bg-core' : tint === 'universal' ? 'bg-universal' : 'bg-dev'
  return (
    <Modal onClose={onClose} column maxW="max-w-3xl">
      <div className={`flex shrink-0 items-center gap-2 border-b border-line px-4 py-3 ${on ? '' : 'opacity-60'}`}>
        <ScrollText size={15} className="text-muted" />
        <span className="text-sm font-semibold text-fg">{title}</span>
        {mode && <span className={`text-[10px] font-medium uppercase tracking-wider ${modeTint}`}>{mode}</span>}
        {learned && <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-warn">learned</span>}
        <div className="ml-auto flex items-center gap-2">
          <Toggle on={on} onChange={toggle} onColor={onColor} disabled={busy} title={on ? 'Disable' : 'Enable'} />
          <button onClick={onClose} className="rounded p-1 text-muted hover:bg-hover hover:text-fg"><X size={16} /></button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {description && <p className="mb-3 text-[12px] italic leading-relaxed text-faint">{description}</p>}
        <Markdown text={body} variant="doc" />
      </div>
    </Modal>
  )
}
