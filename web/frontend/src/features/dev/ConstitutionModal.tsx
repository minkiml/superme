import { useState } from 'react'
import { ScrollText, X, Pencil, Save, Loader2 } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import Toggle from '@/ui/Toggle'
import { toggleConstitution, getConstitutionFile, saveConstitutionFile } from '@/lib/api'

function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

// The preview popup for one constitution — universal or local, learned or hand-authored. Mirrors the
// skill/agent file modals: opens over the row, shows the description + body, carries the same
// enable/disable Toggle, and now an Edit mode. Edit pulls the RAW file (frontmatter intact) so
// `enabled`/`description` survive the save. Keyed by (scope, slug) via the constitution-file routes.
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
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('') // the RAW file (frontmatter + body) while editing
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // After a save, show the freshly-saved (stripped) body instead of the now-stale `body` prop.
  const [savedBody, setSavedBody] = useState<string | null>(null)

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

  async function startEdit() {
    setErr(null)
    setBusy(true)
    try {
      const { content } = await getConstitutionFile(slug, scope, contextId)
      setDraft(content)
      setEditing(true)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    setSaving(true)
    setErr(null)
    try {
      await saveConstitutionFile(slug, scope, draft, contextId)
      setSavedBody(stripFrontmatter(draft).trim())
      setEditing(false)
      onToggled?.() // reload the parent list so its cached body refreshes too
    } catch (e) {
      setErr(String(e))
    } finally {
      setSaving(false)
    }
  }

  const modeTint = mode === 'core' ? 'text-core' : mode ? 'text-dev' : 'text-universal'
  const onColor = tint === 'core' ? 'bg-core' : tint === 'universal' ? 'bg-universal' : 'bg-dev'
  const display = savedBody ?? body
  return (
    <Modal onClose={onClose} column maxW="max-w-3xl">
      <div className={`flex shrink-0 items-center gap-2 border-b border-line px-4 py-3 ${on ? '' : 'opacity-60'}`}>
        <ScrollText size={15} className="text-muted" />
        <span className="text-sm font-semibold text-fg">{title}</span>
        {mode && <span className={`text-[10px] font-medium uppercase tracking-wider ${modeTint}`}>{mode}</span>}
        {learned && <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-warn">learned</span>}
        <div className="ml-auto flex items-center gap-2">
          {!editing ? (
            <>
              <button
                onClick={startEdit}
                disabled={busy}
                className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
              >
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Pencil size={12} />} Edit
              </button>
              <Toggle on={on} onChange={toggle} onColor={onColor} disabled={busy} title={on ? 'Disable' : 'Enable'} />
            </>
          ) : (
            <>
              <button
                onClick={() => { setEditing(false); setErr(null) }}
                disabled={saving}
                className="rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={save}
                disabled={saving || !draft.trim()}
                className="flex items-center gap-1 rounded-md bg-accent px-2 py-1 text-xs text-on-accent hover:opacity-90 disabled:opacity-50"
              >
                {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} Save
              </button>
            </>
          )}
          <button onClick={onClose} className="rounded p-1 text-muted hover:bg-hover hover:text-fg"><X size={16} /></button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {err && <div className="mb-2 text-sm text-danger">{err}</div>}
        {editing ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            className="h-[60vh] w-full resize-none rounded-md border border-line bg-sunken p-3 font-mono text-[12.5px] leading-relaxed text-fg outline-none focus:border-accent"
          />
        ) : (
          <>
            {description && <p className="mb-3 text-[12px] italic leading-relaxed text-faint">{description}</p>}
            <Markdown text={display} variant="doc" />
          </>
        )}
      </div>
    </Modal>
  )
}
