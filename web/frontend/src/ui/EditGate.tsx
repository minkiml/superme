import { useCallback, useState } from 'react'
import { Loader2, Pencil, Save } from 'lucide-react'

// ── the app's ONE editing pattern ──
//
// Every editable artifact is read far more often than written, so they all open in a READING state.
//
// Save is LIVE ONLY WHILE A DIFF EXISTS, which makes it the diff indicator.

/**
 * Stated apart from the draft, so one `EditActions` serves gates over different draft types.
 */
export type GateControls = {
  editing: boolean
  /** The draft differs from what Edit started with. This is what arms Save. */
  dirty: boolean
  busy: boolean
  start: () => void
  close: () => void
  save: () => void
}

export type EditGate<T> = GateControls & {
  draft: T
  setDraft: (next: T) => void
  err: string | null
}

/**
 * `load` is for surfaces that render one form and edit another: its result, not `saved`, becomes
 * the baseline.
 *
 * `valid` rejects a well-formed-empty draft, independently of whether it differs.
 */
export function useEditGate<T>({ saved, commit, load, valid }: {
  saved: T
  commit: (draft: T) => Promise<void>
  load?: () => Promise<T>
  valid?: (draft: T) => boolean
}): EditGate<T> {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<T>(saved)
  const [base, setBase] = useState<T>(saved)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const start = useCallback(() => {
    if (!load) {
      setDraft(saved); setBase(saved); setErr(null); setEditing(true)
      return
    }
    // Enter edit mode only once the text is in hand; a box that fills later can be typed into.
    setBusy(true); setErr(null)
    load()
      .then((text) => { setDraft(text); setBase(text); setEditing(true) })
      .catch((e) => setErr(String(e)))
      .finally(() => setBusy(false))
  }, [load, saved])

  const close = useCallback(() => { setEditing(false); setDraft(base); setErr(null) }, [base])

  const save = useCallback(() => {
    setBusy(true); setErr(null)
    commit(draft)
      .then(() => { setBase(draft); setEditing(false) })
      .catch((e) => setErr(String(e)))
      .finally(() => setBusy(false))
  }, [commit, draft])

  return {
    editing, draft, setDraft, busy, err, start, close, save,
    dirty: !same(draft, base) && (valid ? valid(draft) : true),
  }
}

// Structural equality by serialisation: the drafts here are strings or flat records, and key order
// is stable.
function same<T>(a: T, b: T): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b)
}

/**
 * Drop it wherever the surface keeps its actions. `readOnly` withholds Edit and says why: an
 * absent button that explains itself beats a live one that fails.
 */
export function EditActions({ gate, tone = 'accent', readOnly = false, readOnlyNote }: {
  gate: GateControls
  /** Save's fill — matches the surface it sits on (`dev` inside the dev tabs). */
  tone?: 'accent' | 'dev'
  readOnly?: boolean
  readOnlyNote?: string
}) {
  if (readOnly) {
    return readOnlyNote ? <span className="text-[11px] text-faint">{readOnlyNote}</span> : null
  }
  if (!gate.editing) {
    return (
      <button
        onClick={gate.start}
        disabled={gate.busy}
        className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
      >
        {gate.busy ? <Loader2 size={12} className="animate-spin" /> : <Pencil size={12} />} Edit
      </button>
    )
  }
  return (
    <>
      <button
        onClick={gate.close}
        disabled={gate.busy}
        className="rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
      >
        Close
      </button>
      <button
        onClick={gate.save}
        disabled={gate.busy || !gate.dirty}
        className={`flex items-center gap-1 rounded-md px-2 py-1 text-xs text-on-accent hover:opacity-90 disabled:opacity-40
          ${tone === 'dev' ? 'bg-dev' : 'bg-accent'}`}
      >
        {gate.busy ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} Save
      </button>
    </>
  )
}
