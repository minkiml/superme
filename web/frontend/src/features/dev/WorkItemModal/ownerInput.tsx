import { useState } from 'react'
import { Loader2, Plus, Trash2, PenLine } from 'lucide-react'
import SectionHeader from '@/ui/SectionHeader'
import { getWorkItemOwnerInput, saveWorkItemOwnerInput, type OwnerInput, type OwnerReference, type OwnerNote } from '@/lib/api'
import { invalidate, useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'

// From you: the owner's own words on an item — composed before plan, read after.

// ── the owner's own section ──
//
// The ONE section of any report the owner writes, and the only place their words reach plan as
// instruction.
//
// COMPOSE lives on Now and only during TRIAGE.
const FROM_YOU_HINT = 'Whatever is here when plan starts is authority it follows, not input it weighs.'

function useOwnerInput(itemId: string, contextId: string) {
  const q = useLive(K.itemOwnerInput(contextId, itemId),
                    () => getWorkItemOwnerInput(itemId, contextId), 30000)
  async function save(next: { references: OwnerReference[]; notes: OwnerNote[] }) {
    await saveWorkItemOwnerInput(itemId, next.references, next.notes, contextId)
    invalidate(K.itemOwnerInput(contextId, itemId), K.itemReport(contextId, itemId, 'triage'))
  }
  return { saved: q.data as OwnerInput | undefined, save }
}

const FY_BOX = 'w-full rounded border border-line bg-panel px-2 py-1 text-[13px] text-fg '
             + 'outline-none transition placeholder:text-faint focus:border-accent'

// Add stays inert until every field it needs is filled: an empty slot reads to plan as an
// instruction with nothing in it.
function AddClear({ ready, busy, onAdd, onClear }: {
  ready: boolean; busy: boolean; onAdd: () => void; onClear: () => void
}) {
  return (
    <div className="flex items-center gap-2">
      <button onClick={onAdd} disabled={!ready || busy}
              className="inline-flex items-center gap-1 rounded bg-accent px-2.5 py-1 text-[11px]
                         font-medium text-on-accent transition hover:opacity-90 disabled:opacity-40">
        {busy ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />} Add
      </button>
      <button onClick={onClear} disabled={busy}
              className="rounded border border-line px-2.5 py-1 text-[11px] text-muted transition
                         hover:text-fg disabled:opacity-40">
        Clear
      </button>
    </div>
  )
}

export function FromYouCompose({ itemId, contextId }: { itemId: string; contextId: string }) {
  const { saved, save } = useOwnerInput(itemId, contextId)
  const [ref, setRef] = useState({ source: '', description: '' })
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  if (!saved) return null

  async function add(next: { references: OwnerReference[]; notes: OwnerNote[] }, reset: () => void) {
    setBusy(true); setErr('')
    try { await save(next); reset() } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  const refReady = !!ref.source.trim() && !!ref.description.trim()
  const noteReady = !!note.trim()

  return (
    <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
      <SectionHeader className="mb-0.5 flex items-center gap-1.5">
        <PenLine size={13} /> From you
      </SectionHeader>
      <p className="mb-2 text-[11px] leading-snug text-faint">
        {saved.exists ? FROM_YOU_HINT
                      : 'Triage hasn’t written the brief yet — there is nothing to write into.'}
      </p>
      {saved.exists && (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <div className="text-[11px] font-medium text-muted">Useful imported references</div>
            <input className={FY_BOX} value={ref.source} disabled={busy}
                   placeholder="Source — a doc, URL or path"
                   onChange={(e) => setRef({ ...ref, source: e.target.value })} />
            <input className={FY_BOX} value={ref.description} disabled={busy}
                   placeholder="Description — what it governs"
                   onChange={(e) => setRef({ ...ref, description: e.target.value })} />
            <AddClear ready={refReady} busy={busy}
                      onClear={() => setRef({ source: '', description: '' })}
                      onAdd={() => add({ references: [...saved.references, ref], notes: saved.notes },
                                       () => setRef({ source: '', description: '' }))} />
          </div>
          {/* One card, not two: they are the same act at two grains. */}
          <div className="space-y-1.5 border-t border-line pt-2.5">
            <div className="text-[11px] font-medium text-muted">Verification notes</div>
            <input className={FY_BOX} value={note} disabled={busy}
                   placeholder="Description — something you want proven; each becomes one check"
                   onChange={(e) => setNote(e.target.value)} />
            <AddClear ready={noteReady} busy={busy} onClear={() => setNote('')}
                      onAdd={() => add({ references: saved.references,
                                         notes: [...saved.notes, { description: note }] },
                                       () => setNote(''))} />
          </div>
          {/* A count, not the list: the slots live with the report they belong to. */}
          {(saved.references.length > 0 || saved.notes.length > 0) && (
            <p className="border-t border-line pt-2 text-[11px] text-faint">
              {saved.references.length} reference{saved.references.length === 1 ? '' : 's'} ·{' '}
              {saved.notes.length} note{saved.notes.length === 1 ? '' : 's'} in — see them under
              Reports → Triage, where they can be removed.
            </p>
          )}
          {err && <p className="text-[11px] text-danger-text">{err}</p>}
        </div>
      )}
    </section>
  )
}

// Removal is the only act here; composing belongs beside the phase that reads it. `editable` is the
// triage window.
export function FromYouSlots({ itemId, contextId, editable }: {
  itemId: string; contextId: string; editable: boolean
}) {
  const { saved, save } = useOwnerInput(itemId, contextId)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  if (!saved?.exists) return null
  const empty = !saved.references.length && !saved.notes.length

  async function drop(key: string, next: { references: OwnerReference[]; notes: OwnerNote[] }) {
    setBusy(key); setErr('')
    try { await save(next) } catch (e) { setErr(String(e)) } finally { setBusy('') }
  }

  const row = 'flex items-start gap-2 border-t border-line py-1.5 text-[13px] first:border-t-0'
  const del = 'shrink-0 rounded p-0.5 text-faint transition hover:bg-hover hover:text-danger '
            + 'disabled:opacity-40'
  return (
    <div className="mt-4 rounded border border-line bg-sunken px-3 py-2.5">
      <SectionHeader className="mb-0.5 flex items-center gap-1.5">
        <PenLine size={13} /> From you
      </SectionHeader>
      <p className="mb-2 text-[11px] leading-snug text-faint">
        {empty
          ? (editable
              ? 'Nothing yet. Add references and verification notes from Quick View → Now.'
              : 'You added nothing here — plan designed from the brief alone.')
          : editable ? FROM_YOU_HINT
                     : 'What the plan phase was handed. Read-only now that triage has passed.'}
      </p>
      {saved.references.length > 0 && (
        <div className="mb-2">
          <div className="text-[11px] font-medium text-muted">Useful imported references</div>
          <ul>
            {saved.references.map((r, i) => (
              <li key={`r${i}`} className={row}>
                <span className="min-w-0 flex-1 leading-snug text-fg">
                  <span className="font-medium">{r.source}</span>
                  {r.source && r.description ? ' — ' : ''}
                  <span className="text-muted">{r.description}</span>
                </span>
                {editable && (
                  <button className={del} title="Remove this reference" disabled={!!busy}
                          onClick={() => drop(`r${i}`, {
                            references: saved.references.filter((_, j) => j !== i),
                            notes: saved.notes })}>
                    {busy === `r${i}` ? <Loader2 size={12} className="animate-spin" />
                                      : <Trash2 size={12} />}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {saved.notes.length > 0 && (
        <div>
          <div className="text-[11px] font-medium text-muted">Verification notes</div>
          <ul>
            {saved.notes.map((n, i) => (
              <li key={`n${i}`} className={row}>
                <span className="min-w-0 flex-1 leading-snug text-fg">{n.description}</span>
                {editable && (
                  <button className={del} title="Remove this note" disabled={!!busy}
                          onClick={() => drop(`n${i}`, {
                            references: saved.references,
                            notes: saved.notes.filter((_, j) => j !== i) })}>
                    {busy === `n${i}` ? <Loader2 size={12} className="animate-spin" />
                                      : <Trash2 size={12} />}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {err && <p className="mt-1 text-[11px] text-danger-text">{err}</p>}
    </div>
  )
}

// ── Trace ───────────────────────────────────────────────────────────────────────────────────────
