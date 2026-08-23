import { useState, useEffect, type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import Dropdown from '@/ui/Dropdown'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import SectionHeader from '@/ui/SectionHeader'
import TabBar from '@/ui/TabBar'
import Toggle from '@/ui/Toggle'
import { useEditGate, EditActions } from '@/ui/EditGate'
import { getInboxBrief, saveInboxBrief, type InboxEntry, type InboxKind, type InboxBrief } from '@/lib/api'
import { fmtLocal, toModelKey } from '@/lib/format'
import { navigate, useRoute } from '@/lib/router'
import { KIND_OPTS } from './inbox'
import { DEFAULT_RUN_EFFORT, DEFAULT_RUN_MODEL, InboxConfigPatch, RUN_EFFORTS, RUN_MODELS, RUN_ROLES, RoleDefaults, WORK_KIND_OPTS, optLabel, roleField } from './runConfig'

// One inbox item opened: its text, the kind it proposes, and the run config it carries.

// The inspector for one inbox row, in THREE tabs: what it says, the context it hands on, and how it
// is worked.
//
// Two artifacts, two edit gates; the action row shows the gate for whichever tab is open.
export function InboxItemModal({
  e,
  roleDefaults,
  onSave,
  onClose,
}: {
  e: InboxEntry
  roleDefaults: RoleDefaults   // per role, what an unset row already runs — its picker's start
  onSave: (patch: InboxConfigPatch) => Promise<void>
  onClose: () => void
}) {
  const [tab, setTab] = useState<'content' | 'brief' | 'setting'>('content')

  // Every pick is CONCRETE. `work_kind` is the exception: an empty value means triage decides
  // alone.
  const roleSaved = Object.fromEntries(RUN_ROLES.flatMap((r) => {
    const d = roleDefaults[r.key] ?? { model: DEFAULT_RUN_MODEL, effort: DEFAULT_RUN_EFFORT }
    return [
      [roleField(r.key, 'model'), toModelKey(e[roleField(r.key, 'model')]) || d.model],
      [roleField(r.key, 'effort'), e[roleField(r.key, 'effort')] || d.effort],
    ]
  })) as Pick<InboxConfigPatch, 'model' | 'effort' | 'vet_model' | 'vet_effort' | 'deputy_model' | 'deputy_effort'>
  const saved: InboxConfigPatch = {
    title: e.title ?? '',
    text: e.text,
    kind: e.kind,
    autopilot: !!e.autopilot,
    work_kind: e.work_kind ?? '',
    ...roleSaved,
  }
  const row = useEditGate<InboxConfigPatch>({
    saved,
    valid: (d) => !!d.text.trim(),
    commit: (d) => onSave({ ...d, title: (d.title ?? '').trim() || null, text: d.text.trim() }),
  })
  const d = row.draft
  const set = (patch: Partial<InboxConfigPatch>) => row.setDraft({ ...d, ...patch })
  // Outside edit mode the tabs read the ROW: an abandoned draft must not decide which tabs exist.
  const kind = row.editing ? d.kind : e.kind

  // Loaded when its tab first opens: most opens never look at it, and most rows have none.
  const [brief, setBrief] = useState<InboxBrief | null>(null)
  const [briefErr, setBriefErr] = useState<string | null>(null)
  useEffect(() => {
    if (tab !== 'brief' || brief) return
    let alive = true
    getInboxBrief(e.id)
      .then((b) => { if (alive) setBrief(b) })
      .catch((err) => alive && setBriefErr(String(err)))
    return () => { alive = false }
  }, [tab, brief, e.id])
  const briefGate = useEditGate({
    saved: brief?.content ?? '',
    valid: (t) => !!t.trim(),
    commit: async (t) => { await saveInboxBrief(e.id, t); setBrief({ ...brief!, content: t }) },
  })

  // A note is never pushed, so both tabs are withheld rather than shown empty.
  const tabs = kind === 'note'
    ? ([['content', 'Content'], ['setting', 'Info']] as const)
    : ([['content', 'Content'], ['brief', 'Brief'], ['setting', 'Setting']] as const)
  const gate = tab === 'brief' ? briefGate : row
  const err = tab === 'brief' ? (briefGate.err ?? briefErr) : row.err

  return (
    // Contained, so it overlays the dashboard column and leaves the chat rail interactive.
    <Modal onClose={onClose} title="Inbox item" maxW="max-w-lg" z="z-40" contain dismissable={false}>
      <div className="p-4">
        <TabBar
          tabs={tabs}
          value={tab === 'brief' && kind === 'note' ? 'content' : tab}
          onChange={setTab}
          size="sm"
          className="mb-3"
        />

        {/* One fixed body height, so switching tabs cannot resize the dialog under the cursor. */}
        <div className="h-[21rem] overflow-y-auto">
        {tab === 'content' ? (
          row.editing ? (
            <div className="flex h-full flex-col gap-2">
              <div className="flex items-center gap-2">
                <Dropdown value={d.kind} options={KIND_OPTS} onChange={(v) => set({ kind: v as InboxKind })} />
                <input
                  className="min-w-0 flex-1 rounded border border-line bg-sunken px-2 py-1.5 text-[13px] font-medium text-fg outline-none focus:border-accent placeholder:text-faint"
                  placeholder="Title (optional)"
                  value={d.title ?? ''}
                  onChange={(ev) => set({ title: ev.target.value })}
                  autoFocus
                />
              </div>
              <textarea
                className="w-full flex-1 resize-none rounded border border-line bg-sunken px-2 py-1.5 text-[13px] leading-relaxed text-fg outline-none focus:border-accent"
                value={d.text}
                onChange={(ev) => set({ text: ev.target.value })}
              />
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-baseline gap-2">
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${e.kind === 'note' ? 'bg-hover text-faint' : 'bg-accent-soft text-accent-text'}`}>{e.kind}</span>
                {e.title && <span className="min-w-0 text-[14px] font-medium leading-snug text-fg [overflow-wrap:anywhere]">{e.title}</span>}
              </div>
              <Markdown text={e.text} variant="doc" tone="dev" />
            </div>
          )
        ) : tab === 'brief' ? (
          brief === null ? (
            briefErr ? null : <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>
          ) : briefGate.editing ? (
            <textarea
              className="h-full w-full resize-none rounded border border-line bg-sunken px-2 py-1.5 font-mono text-[12px] leading-relaxed text-fg outline-none focus:border-accent"
              value={briefGate.draft}
              onChange={(ev) => briefGate.setDraft(ev.target.value)}
              spellCheck={false}
              autoFocus
            />
          ) : brief.content ? (
            <Markdown text={stripFrontmatter(brief.content)} variant="doc" tone="dev" />
          ) : (
            <div className="text-[12px] leading-relaxed text-faint">
              No brief was filed, so this row&rsquo;s whole cold-start context is its own text. Write
              one here and the work-item it becomes reads it first.
            </div>
          )
        ) : (
          <div className="space-y-4">
            {/* ── how this item will be worked ── All four describe a RUN, and what its runs
                spend. */}
            {kind !== 'note' && (
            <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
              <SectionHeader>Setting</SectionHeader>
              <div className="mt-1 text-[11px] leading-snug text-faint">
                Set here while the row is open. Push freezes them onto the work-item.
              </div>
              {row.editing ? (
                <div className="mt-2.5 space-y-2.5">
                  <ConfigRow label="Autopilot" hint="Drives its own gates; the deputy judges each one for you.">
                    <Toggle on={d.autopilot} onChange={(v) => set({ autopilot: v })} onColor="bg-accent" />
                  </ConfigRow>
                  <ConfigRow label="Work kind" hint="Implementation changes code; research answers a question. Triage confirms it.">
                    <Dropdown value={d.work_kind} options={WORK_KIND_OPTS} onChange={(v) => set({ work_kind: v })} width="w-36" align="right" />
                  </ConfigRow>
                  <RoleGrid draft={d} onSet={set} />
                </div>
              ) : (
                <>
                  <dl className="mt-2 space-y-1.5">
                    <MetaRow label="Autopilot">{saved.autopilot ? 'On' : 'Off'}</MetaRow>
                    <MetaRow label="Work kind">{optLabel(WORK_KIND_OPTS, saved.work_kind)}</MetaRow>
                  </dl>
                  <RoleGrid draft={saved} />
                </>
              )}
            </section>
            )}

            {/* ── what this row is ─────────────────────────────────────────────────────────── */}
            <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
              <SectionHeader>Meta info</SectionHeader>
              <dl className="mt-2 space-y-1.5">
                <MetaRow label="Id">#{e.id}</MetaRow>
                <MetaRow label="Status">{e.status}</MetaRow>
                <MetaRow label="Created by">{(e.origin ?? []).join(' · ') || '—'}</MetaRow>
                <MetaRow label="Captured">{fmtLocal(e.created_at)}</MetaRow>
                <MetaRow label="Updated">{fmtLocal(e.updated_at)}</MetaRow>
                {e.spawned_from && (
                  <MetaRow label="Branched from">
                    <ItemLink id={e.spawned_from.item} onLeave={onClose} />
                    <span className="ml-1.5 text-muted">({e.spawned_from.relation})</span>
                  </MetaRow>
                )}
                {e.routed_to && (
                  <MetaRow label="Work-item"><ItemLink id={e.routed_to} onLeave={onClose} /></MetaRow>
                )}
              </dl>
            </section>
          </div>
        )}
        </div>

        {err && <div className="mt-2 text-[12px] text-danger">{err}</div>}
        <div className="mt-3 flex items-center justify-end gap-2">
          <EditActions
            gate={gate}
            readOnly={tab === 'brief' && (brief === null || !brief.editable)}
            readOnlyNote={tab === 'brief' && brief && !brief.editable
              ? 'Pushed — the brief is the work-item’s provenance now'
              : undefined}
          />
        </div>
      </div>
    </Modal>
  )
}

// A TABLE, so the question is asked once per column and answered once per row, and a new role is
// one more row.
function RoleGrid({ draft, onSet }: {
  draft: InboxConfigPatch
  onSet?: (patch: Partial<InboxConfigPatch>) => void
}) {
  const cell = 'grid grid-cols-[1fr_auto_auto] items-center gap-x-2'
  return (
    <div className="mt-3 border-t border-line pt-2.5">
      <div className={`${cell} pb-1 text-[10px] font-semibold uppercase tracking-wide text-faint`}>
        <span>Runs on</span>
        <span className="w-28 pl-2">Model</span>
        <span className="w-[6.5rem] pl-2">Effort</span>
      </div>
      {RUN_ROLES.map((r) => {
        const mKey = roleField(r.key, 'model')
        const eKey = roleField(r.key, 'effort')
        return (
          <div key={r.key || 'work'} className={`${cell} py-1`}>
            <div className="min-w-0">
              <div className="text-[12.5px] leading-tight text-fg">{r.label}</div>
              <div className="text-[10.5px] leading-tight text-faint">{r.hint}</div>
            </div>
            {onSet ? (
              <>
                <Dropdown value={draft[mKey]} options={RUN_MODELS} onChange={(v) => onSet({ [mKey]: v })} width="w-28" align="right" />
                <Dropdown value={draft[eKey]} options={RUN_EFFORTS} onChange={(v) => onSet({ [eKey]: v })} width="w-[6.5rem]" align="right" />
              </>
            ) : (
              <>
                <span className="w-28 pl-2 text-[12px] text-muted">{optLabel(RUN_MODELS, draft[mKey])}</span>
                <span className="w-[6.5rem] pl-2 text-[12px] text-muted">{optLabel(RUN_EFFORTS, draft[eKey])}</span>
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}

function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

// One labelled control in the Config section: name + one-line why on the left, the control right.
function ConfigRow({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[13px] text-fg">{label}</div>
        <div className="text-[11px] leading-snug text-faint">{hint}</div>
      </div>
      <div className="shrink-0 pt-0.5">{children}</div>
    </div>
  )
}

// A work-item id is an address, so reading one here is one click from opening it.
function ItemLink({ id, onLeave }: { id: string; onLeave: () => void }) {
  const route = useRoute()
  const repoId = 'repoId' in route ? route.repoId : null
  if (!repoId) return <span className="font-mono">{id}</span>
  return (
    <button
      type="button"
      title="Open this work-item"
      onClick={() => {
        onLeave()
        navigate({ name: 'item', repoId, itemId: id, tab: null, sub: null })
      }}
      className="font-mono text-accent underline-offset-2 hover:text-accent-text hover:underline"
    >
      {id}
    </button>
  )
}

// One fact in the Meta section — the label names it, the value IS it (colour rule: muted label,
// fg fact).
function MetaRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2 text-[13px] leading-snug">
      <dt className="w-[6.5rem] shrink-0 text-[11px] leading-[1.45] text-muted">{label}</dt>
      <dd className="min-w-0 break-words text-fg">{children}</dd>
    </div>
  )
}
