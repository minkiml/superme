import { useState } from 'react'
import { ScrollText, X, Pin } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import Toggle from '@/ui/Toggle'
import SourceEditor from '@/ui/SourceEditor'
import { useEditGate, EditActions } from '@/ui/EditGate'
import { toggleConstitution, getConstitutionFile, saveConstitutionFile } from '@/lib/api'

function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

// The preview popup for one constitution, mirroring the skill and agent modals.
//
// Edit pulls the RAW file, frontmatter intact, so `enabled` and `description` survive the save.
export default function ConstitutionModal({
  slug, scope, title, mode, description, body, enabled, foundational = false, learned = false, contextId = 'global', tint = 'dev',
  onClose, onToggled,
}: {
  slug: string
  scope: string
  title: string
  mode?: string
  description?: string | null
  body: string
  enabled: boolean
  foundational?: boolean
  learned?: boolean
  contextId?: string
  tint?: 'universal' | 'dev' | 'core'
  onClose: () => void
  onToggled?: () => void
}) {
  const [on, setOn] = useState(enabled)
  const [busy, setBusy] = useState(false)
  // After a save, show the freshly-saved (stripped) body instead of the now-stale `body` prop.
  const [savedBody, setSavedBody] = useState<string | null>(null)
  // It renders the stripped body but EDITS the raw file, so the gate loads its own baseline at
  // Edit-time.
  const gate = useEditGate({
    saved: '',
    valid: (d) => !!d.trim(),
    load: async () => (await getConstitutionFile(slug, scope, contextId)).content,
    commit: async (d) => {
      await saveConstitutionFile(slug, scope, d, contextId)
      setSavedBody(stripFrontmatter(d).trim())
      onToggled?.() // reload the parent list so its cached body refreshes too
    },
  })
  const { editing, draft, err } = gate

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
  const display = savedBody ?? body
  return (
    <Modal onClose={onClose} column maxW={editing ? "max-w-4xl" : "max-w-3xl"} dismissable={!editing}>
      <div className={`flex shrink-0 items-center gap-2 border-b border-line px-4 py-3 ${on ? '' : 'opacity-60'}`}>
        <ScrollText size={15} className="text-muted" />
        <span className="text-sm font-semibold text-fg">{title}</span>
        {mode && <span className={`text-[10px] font-medium uppercase tracking-wider ${modeTint}`}>{mode}</span>}
        {foundational && <span className="rounded bg-universal/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-universal">foundational</span>}
        {learned && <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-warn">learned</span>}
        <div className="ml-auto flex items-center gap-2">
          <EditActions gate={gate} />
          {!editing && (foundational ? (
            <Pin size={15} className="text-faint" aria-label="Foundational — always on, can't be disabled" />
          ) : (
            <Toggle on={on} onChange={toggle} onColor={onColor} disabled={busy} title={on ? 'Disable' : 'Enable'} />
          ))}
          <button onClick={onClose} className="rounded p-1 text-muted hover:bg-hover hover:text-fg"><X size={16} /></button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {err && <div className="mb-2 text-sm text-danger">{err}</div>}
        {editing ? (
          <SourceEditor value={draft} onChange={gate.setDraft} surface="bg-sunken" />
        ) : (
          <>
            {description && <p className="mb-3 text-[12px] italic leading-relaxed text-faint">{description}</p>}
            <Markdown text={display} variant="doc" tone={tint} />
          </>
        )}
      </div>
    </Modal>
  )
}
