import { useEffect, useState } from 'react'
import { Pin, ScrollText } from 'lucide-react'
import Toggle from '@/ui/Toggle'
import ScopeColumns, { type ScopeCard, type ScopeColumn } from '@/ui/ScopeColumns'
import ConstitutionModal from '@/features/dev/ConstitutionModal'
import { getFoundation, toggleConstitution, type FoundationConstitution } from '@/lib/api'
import { Loading, PaneHead } from '../controls'
import { useUniversalPublished } from './published'

// System artifacts › Constitution — the universal always-on rules, in the two columns that decide
// when they load. There is no Shared column and there never will be: a constitution is mode-scoped
// by construction, so a rule with no mode has nowhere to be loaded from.
//
// Within a column, foundational rules come first and carry a PIN instead of a switch. A charter
// consults them by name, so they cannot be turned off — and a toggle that refuses to move is a lie
// about what the owner controls.

export default function Constitution() {
  const [rows, setRows] = useState<FoundationConstitution[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<FoundationConstitution | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const pub = useUniversalPublished()

  function load() {
    getFoundation().then((d) => setRows(d.constitutions)).catch((e) => setErr(String(e)))
  }
  useEffect(load, [])

  async function toggle(c: FoundationConstitution, on: boolean) {
    const id = `${c.mode}-${c.slug}`
    setBusy(id)
    try {
      await toggleConstitution(c.slug, `universal_${c.mode}`, on)
      load()
    } finally {
      setBusy(null)
    }
  }

  const columns: ScopeColumn[] = (['dev', 'core'] as const).map((mode) => {
    const mine = (rows ?? []).filter((c) => c.mode === mode)
    const card = (c: FoundationConstitution): ScopeCard => {
      const id = `${c.mode}-${c.slug}`
      const learned = pub.learned.has(`constitution:${c.slug}`)
      return {
        key: id,
        name: c.title,
        onClick: () => setOpen(c),
        badges: learned ? (
          <span className="shrink-0 rounded bg-warn/15 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-warn">
            learned
          </span>
        ) : undefined,
        trailing: c.foundational ? (
          <Pin size={13} className="text-faint" aria-label="Foundational — always on" />
        ) : (
          <Toggle
            on={c.enabled}
            onChange={(v) => toggle(c, v)}
            onColor={mode === 'core' ? 'bg-core' : 'bg-dev'}
            disabled={busy === id}
            title={c.enabled ? 'Disable' : 'Enable'}
          />
        ),
      }
    }
    return {
      key: mode,
      name: mode === 'dev' ? 'Dev' : 'Core',
      note: `loaded in ${mode} mode`,
      tint: mode,
      icon: ScrollText,
      groups: [
        { label: 'Foundational', cards: mine.filter((c) => c.foundational).map(card) },
        { label: 'General', cards: mine.filter((c) => !c.foundational).map(card) },
      ],
    }
  })

  return (
    <>
      <PaneHead
        title="Constitution"
        scope="System artifacts"
        lede="Always-on rules, grouped by the mode that loads them. Toggle to control what loads."
      />
      {err ? (
        <div className="text-sm text-danger">Couldn’t load the constitution — {err}</div>
      ) : rows === null ? (
        <Loading />
      ) : (
        <ScopeColumns columns={columns} />
      )}
      {open && (
        <ConstitutionModal
          slug={open.slug}
          scope={`universal_${open.mode}`}
          title={open.title}
          mode={open.mode}
          body={open.body}
          enabled={open.enabled}
          foundational={open.foundational}
          learned={pub.learned.has(`constitution:${open.slug}`)}
          tint={open.mode === 'core' ? 'core' : 'dev'}
          onClose={() => setOpen(null)}
          onToggled={() => { load(); pub.reload() }}
        />
      )}
    </>
  )
}
