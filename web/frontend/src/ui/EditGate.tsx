import { useCallback, useState } from 'react'
import { Loader2, Pencil, Save } from 'lucide-react'

// ── The app's ONE editing pattern ────────────────────────────────────────────────────────────
// Every editable artifact in the dashboard — anchor docs, skills, agents, constitutions, published
// files, work-item artifacts, an inbox row and its brief — is read far more often than it is
// written, and every one of them is a file some agent reads next turn. So they all open in a
// READING state and one button changes that:
//
//   View → a single `Edit`. Nothing else competes with reading, and nothing can be changed by a
//   stray keystroke on a surface the owner opened to look at.
//
//   Edit → `Close` and `Save`, and Save is LIVE ONLY WHILE A DIFF EXISTS. A Save that is always
//   clickable says nothing about whether anything happened; one that lights up when the text
//   diverges and goes dark again when it matches IS the diff indicator, so there is never a
//   pointless write, and re-typing a value back to what it was is correctly a no-op.
//
// `Close` leaves edit mode and drops the draft — the artifact on disk is untouched, which is
// exactly what a dark Save already told you.
//
// The baseline is whatever was in the box when Edit was pressed, not the prop the modal renders
// from. Several surfaces render a stripped body but edit the RAW file (frontmatter kept), and
// diffing a draft against the wrong text would light Save the instant edit mode opened.

/** Everything the buttons need. Stated apart from the draft so one `<EditActions>` can serve
 *  gates over different draft types — a raw markdown string here, a whole row record there. */
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
 * `saved` is the current on-disk value. `commit` writes a draft and resolves when it landed.
 * `load` is for surfaces that render one form and edit another (a stripped body vs the raw file):
 * it fetches the editable text at Edit-time, and its result — not `saved` — becomes the baseline.
 * `valid` rejects a draft that is well-formed-empty (a blank required field), independently of
 * whether it differs.
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
    // A loading Edit still enters edit mode only once the text is in hand — an empty textarea that
    // fills a moment later is a box the owner can type into and lose.
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

// Structural equality by serialisation — the drafts here are strings or flat records of scalars,
// and both compare correctly this way. Key order is stable because every draft is built from the
// same object literal each render.
function same<T>(a: T, b: T): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b)
}

/**
 * The gate's buttons. Drop it wherever the surface keeps its actions — a modal header, a footer
 * row — and the two states render themselves. `readOnly` withholds Edit for an artifact that can
 * no longer be written, with `readOnlyNote` saying why: an absent button that explains itself
 * beats a live one that fails at the route.
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
