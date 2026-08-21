import { useCallback, useEffect, useState } from 'react'
import { Sparkles, X, Trash2, Loader2, Bot, Pencil, FileText } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import Toggle from '@/ui/Toggle'
import ArtifactTabs from '@/ui/ArtifactTabs'
import SourceEditor from '@/ui/SourceEditor'
import { useEditGate, EditActions } from '@/ui/EditGate'
import { getPublished, togglePublished, deletePublished, getPublishedFile, savePublishedFile, type PublishedItem, type PublishedForm } from '@/lib/api'
import { Empty } from '@/features/dev/common'

// What the loop published, and the file view that edits one.

// Disable suspends a published artifact without losing it; delete removes it for good. Effective on
// the next dev turn.
const PUB_FORM_META: Record<PublishedForm, { label: string; icon: typeof Bot; blurb: string }> = {
  constitution: { label: 'Constitution', icon: FileText, blurb: 'Always-on rules in the system prompt.' },
  skill: { label: 'Skills', icon: Sparkles, blurb: 'Loaded via the dev plugin.' },
  agent: { label: 'Agents', icon: Bot, blurb: 'Delegate workers.' },
}

const pubScopeLabel = (s: string) =>
  s === 'universal_dev' ? 'universal' : s === 'repo_dev' ? 'repo' : s

export function PublishedInventory({ contextId }: { contextId: string }) {
  const [items, setItems] = useState<PublishedItem[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)        // proposal_id mid-flight
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [open, setOpen] = useState<PublishedItem | null>(null) // item open in the preview/edit modal
  const [form, setForm] = useState<PublishedForm>('constitution')

  const load = useCallback(() => {
    getPublished(contextId)
      .then((d) => setItems(d.published))
      .catch((e) => setErr(String(e)))
  }, [contextId])
  useEffect(() => { load() }, [load])

  // With no manual refresh, returning to the window is the refresh.
  useEffect(() => {
    const onVisible = () => document.visibilityState === 'visible' && load()
    window.addEventListener('focus', load)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.removeEventListener('focus', load)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load])

  async function toggle(it: PublishedItem) {
    setBusy(it.proposal_id); setErr(null)
    try { await togglePublished(it.proposal_id, !it.enabled, contextId); load() }
    catch (e) { setErr(String(e)) } finally { setBusy(null) }
  }
  async function remove(it: PublishedItem) {
    setBusy(it.proposal_id); setErr(null)
    try { await deletePublished(it.proposal_id, contextId); setConfirmDel(null); load() }
    catch (e) { setErr(String(e)) } finally { setBusy(null) }
  }

  if (err && !items) return <div className="text-sm text-danger">Could not load: {err}</div>
  if (!items) return <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>

  const present = items.filter((i) => i.present)
  const forms: PublishedForm[] = ['constitution', 'skill', 'agent']
  const rows = present.filter((i) => i.form === form)
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        What the learning loop published into the live harness. Click one to preview or edit.
        Disable suspends it, delete removes it for good. Both take effect on the next dev turn.
      </p>
      {err && <div className="text-sm text-danger">{err}</div>}
      {present.length === 0 ? (
        <Empty>Nothing published yet. Approve a forged artifact at gate 2 and it lands here.</Empty>
      ) : (
        // Constitution · Skills · Agents as tabs (matches Foundations + the Artifacts tab).
        <>
          <ArtifactTabs
            tint="dev"
            value={form}
            onChange={setForm}
            tabs={forms.map((f) => ({ key: f, label: PUB_FORM_META[f].label, icon: PUB_FORM_META[f].icon, count: present.filter((i) => i.form === f).length }))}
          />
          <p className="text-[12px] text-faint">{PUB_FORM_META[form].blurb}</p>
          {rows.length === 0 ? (
            <p className="text-[12px] text-faint">None published in this form.</p>
          ) : (
            <div className="space-y-2">
              {rows.map((it) => {
                const rowBusy = busy === it.proposal_id
                const confirming = confirmDel === it.proposal_id
                return (
                  <div key={it.proposal_id} className={`rounded-lg border border-line bg-surface ${it.enabled ? '' : 'opacity-60'}`}>
                    <div className="flex items-center gap-2 px-3.5 py-2.5">
                      <button onClick={() => setOpen(it)} className="group flex min-w-0 flex-1 items-center gap-1.5 text-left" title="Preview / edit">
                        <span className="min-w-0 truncate text-[14px] text-fg">{it.title}</span>
                        <span className="shrink-0 rounded bg-hover px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-faint">
                          {pubScopeLabel(it.scope)}
                        </span>
                        <Pencil size={11} className="shrink-0 text-faint opacity-0 transition group-hover:opacity-100" />
                      </button>
                      {rowBusy ? (
                        <Loader2 size={15} className="animate-spin text-muted" />
                      ) : (
                        <Toggle on={it.enabled} onChange={() => toggle(it)} onColor="bg-dev" title={it.enabled ? 'Disable' : 'Enable'} />
                      )}
                      {!confirming && (
                        <button
                          onClick={() => setConfirmDel(it.proposal_id)}
                          disabled={rowBusy}
                          title="Delete from everywhere"
                          className="shrink-0 rounded p-1 text-muted hover:text-danger disabled:opacity-50"
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                    {confirming && (
                      <div className="flex items-center gap-2 border-t border-line px-3.5 py-1.5 text-[11px]">
                        <span className="text-muted">Delete everywhere?</span>
                        <button onClick={() => remove(it)} disabled={rowBusy} className="ml-auto rounded bg-danger/10 px-2 py-0.5 text-danger hover:bg-danger/20 disabled:opacity-50">Delete</button>
                        <button onClick={() => setConfirmDel(null)} className="rounded px-1.5 py-0.5 text-muted hover:bg-hover">Cancel</button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
      {open && (
        <PublishedFileModal
          item={open}
          contextId={contextId}
          onClose={() => setOpen(null)}
          onSaved={() => { setOpen(null); load() }}
          onGovernanceChange={load}
        />
      )}
    </div>
  )
}

// Preview + edit one published artifact's raw markdown (constitution / SKILL.md / agent.md). Loads
// the file, renders it; "Edit" swaps to a textarea; "Save" writes it back (next dev turn).
export function PublishedFileModal({ item, contextId, onClose, onSaved, onGovernanceChange, showScope = true }: {
  item: PublishedItem; contextId: string; onClose: () => void; onSaved: () => void
  onGovernanceChange?: () => void // called after an enable/disable or delete so the opener can refresh
  showScope?: boolean // hide the scope chip where it's redundant (Foundations = always universal)
}) {
  const [content, setContent] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [enabled, setEnabled] = useState(item.enabled)
  const [confirmDel, setConfirmDel] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const gate = useEditGate({
    saved: content ?? '',
    valid: (d) => !!d.trim(),
    commit: async (d) => { await savePublishedFile(item.proposal_id, d, contextId); onSaved() },
  })
  const { editing, draft } = gate

  useEffect(() => {
    let alive = true
    getPublishedFile(item.proposal_id, contextId)
      .then((f) => { if (alive) setContent(f.content) })
      .catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [item.proposal_id, contextId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && !editing && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, editing])

  const Icon = PUB_FORM_META[item.form].icon
  const body = content ? content.replace(/^---\n[\s\S]*?\n---\n?/, '') : ''

  async function toggleEnabled() {
    setBusy(true); setErr(null)
    try { await togglePublished(item.proposal_id, !enabled, contextId); setEnabled(!enabled); onGovernanceChange?.() }
    catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  async function del() {
    setBusy(true); setErr(null)
    try { await deletePublished(item.proposal_id, contextId); onGovernanceChange?.(); onClose() }
    catch (e) { setErr(String(e)); setBusy(false) }
  }

  return (
    <Modal onClose={onClose} column maxW={editing ? "max-w-4xl" : "max-w-3xl"} dismissable={!editing}>
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-3">
          <Icon size={15} className="text-muted" />
          <span className="text-sm font-semibold text-fg">{item.title}</span>
          <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-warn">learned</span>
          {showScope && (
            <span className="rounded bg-hover px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-faint">{pubScopeLabel(item.scope)}</span>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            {!editing && (
              <>
                <Toggle on={enabled} onChange={() => toggleEnabled()} onColor="bg-dev" disabled={busy} title={enabled ? 'Disable' : 'Enable'} />
                {confirmDel ? (
                  <span className="flex items-center gap-1">
                    <button onClick={del} disabled={busy} className="rounded bg-danger/10 px-2 py-1 text-[11px] text-danger hover:bg-danger/20 disabled:opacity-50">Delete</button>
                    <button onClick={() => setConfirmDel(false)} className="rounded px-1.5 py-1 text-[11px] text-muted hover:bg-hover">Cancel</button>
                  </span>
                ) : (
                  <button onClick={() => setConfirmDel(true)} disabled={busy} title="Delete from everywhere"
                    className="rounded p-1 text-muted hover:bg-hover hover:text-danger disabled:opacity-50">
                    <Trash2 size={13} />
                  </button>
                )}
                <span className="mx-0.5 h-4 w-px bg-line" />
              </>
            )}
            <EditActions gate={gate} readOnly={content === null} />
            <button onClick={onClose} className="rounded p-1 text-muted hover:bg-hover hover:text-fg"><X size={16} /></button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {(gate.err ?? err) && <div className="mb-2 text-sm text-danger">{gate.err ?? err}</div>}
          {content === null ? (
            <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>
          ) : editing ? (
            <SourceEditor value={draft} onChange={gate.setDraft} />
          ) : (
            <Markdown text={body} variant="doc" tone="dev" />
          )}
        </div>
    </Modal>
  )
}

// --- Memory governance ----------------------------------------------------------------------
