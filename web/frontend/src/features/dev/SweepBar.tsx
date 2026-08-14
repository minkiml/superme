import { useEffect, useRef, useState } from 'react'
import * as Icons from 'lucide-react'
import { Loader2 } from 'lucide-react'
import { getSweepFamilies, launchSweep, type SweepFamily } from '@/lib/api/sweeps'
import { topicRepo } from '@/lib/live/keys'
import { invalidate } from '@/lib/live'

// The standing-sweep launch bar. Sits at the right of the workspace tab strip: the tabs are places
// you GO, these are things you START, so they read as a separate act rather than a seventh tab.
//
// A standing sweep's subject is the codebase itself and its question never stops being worth
// asking. Which families appear is the harness's registry (`core/kind_profiles`), not this file —
// adding one grows a button here with no edit.
//
// Every launch spends real money on a full investigate run, so nothing fires on a single click:
// the icon opens a small panel that states what is about to happen, takes the scope, and only then
// offers Launch.

// The list state, distinct from any family slug.
const MENU = '__menu__'

const AUDIT_INTERESTS = ['coverage', 'performance', 'logic', 'promises']

// The registry's slugs are lowercase because they are identifiers; every place a human reads one
// it is a proper name.
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function FamilyIcon({ name, size = 15 }: { name: string; size?: number }) {
  // The registry names a lucide icon; an unknown name falls back rather than crashing the strip.
  const C = (Icons as unknown as Record<string, typeof Loader2>)[name] ?? Icons.Radar
  return <C size={size} />
}

export default function SweepBar({ contextId }: { contextId: string }) {
  const [families, setFamilies] = useState<SweepFamily[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [area, setArea] = useState('')
  const [interest, setInterest] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [hover, setHover] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)

  useEffect(() => { getSweepFamilies().then(setFamilies).catch(() => setFamilies([])) }, [])

  // Click-away closes the panel — a half-filled launch form left hanging over the board is worse
  // than one that quietly goes away, because it looks like something is still pending.
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(null)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const fam = open === MENU ? null : families.find((f) => f.family === open) ?? null
  const scope = area.trim() || 'the whole repo'
  const blocked = !!fam?.asks_interest && !interest

  function pick(slug: string) {
    setOpen(open === slug ? null : slug)
    setArea(''); setInterest(''); setError('')
  }

  async function go() {
    if (!fam || blocked || busy) return
    setBusy(true); setError('')
    try {
      await launchSweep(contextId, fam.family, area.trim(), interest)
      setOpen(null)
      // One prefix covers the board, the attention feed and every item under this
      // repo — the new sweep has to appear on all three at once.
      invalidate(topicRepo(contextId))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'launch failed')
    } finally {
      setBusy(false)
    }
  }

  if (!families.length) return null

  return (
    <div ref={wrap} className="relative ml-auto flex shrink-0 items-center gap-0.5 self-center">
      <button
        onClick={() => setOpen(open ? null : MENU)}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        aria-label={`System analysis: ${families.map((f) => cap(f.family)).join(', ')}`}
        className={`flex items-center rounded-md p-1.5 transition ${
          open ? 'bg-hover text-fg' : 'text-muted hover:bg-hover hover:text-fg'
        }`}
      >
        <Icons.ScanSearch size={16} />
      </button>

      {/* The button carries no text, so the popover is where it says what it is — and it names the
          four families rather than an umbrella word, because the families are what you came for. */}
      {hover && !open && (
        <div className="absolute right-0 top-full z-30 mt-2 w-max max-w-[19rem] rounded-md border border-line bg-surface px-2.5 py-1.5 text-[11px] leading-snug text-muted shadow-lg">
          <span className="text-fg">System analysis</span>: {families.map((f) => cap(f.family)).join(', ')}
        </div>
      )}

      {open === MENU && (
        <div className="absolute right-0 top-full z-20 mt-2 w-72 rounded-lg border border-line bg-surface p-1.5 shadow-lg">
          {families.map((f) => (
            <button
              key={f.family}
              onClick={() => pick(f.family)}
              className="flex w-full items-start gap-2 rounded-md p-2 text-left hover:bg-hover"
            >
              <span className="mt-0.5 text-muted"><FamilyIcon name={f.icon} size={14} /></span>
              <span>
                <span className="block text-[12px] font-medium text-fg">{cap(f.family)}</span>
                <span className="block text-[11px] leading-snug text-muted">{f.blurb}</span>
              </span>
            </button>
          ))}
        </div>
      )}

      {fam && (
        <div className="absolute right-0 top-full z-20 mt-2 w-72 rounded-lg border border-line bg-surface p-3 shadow-lg">
          <div className="flex items-center gap-2 text-[13px] font-medium text-fg">
            <FamilyIcon name={fam.icon} size={14} /> {cap(fam.family)}
          </div>
          <p className="mt-1 text-[12px] leading-snug text-muted">{fam.blurb}</p>

          {fam.asks_interest && (
            <div className="mt-3">
              <div className="text-[11px] uppercase tracking-wider text-muted">Sound in what?</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {AUDIT_INTERESTS.map((i) => (
                  <button
                    key={i}
                    onClick={() => setInterest(i)}
                    className={`rounded-md px-2 py-1 text-[12px] ${
                      interest === i ? 'bg-hover text-fg' : 'text-muted hover:text-fg'
                    }`}
                  >
                    {i}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="mt-3">
            <div className="text-[11px] uppercase tracking-wider text-muted">Where</div>
            <input
              value={area}
              onChange={(e) => setArea(e.target.value)}
              placeholder="the whole repo"
              className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1 text-[12px] text-fg placeholder:text-muted"
            />
          </div>

          {/* What the click costs, said before it is spent — a sweep is a full investigate run. */}
          <p className="mt-3 text-[12px] leading-snug text-muted">
            Starts a research item on <span className="text-fg">{scope}</span> and runs its
            investigation now. It stops at your review gate.
          </p>
          {error && <p className="mt-2 text-[12px] text-rose-400">{error}</p>}

          <div className="mt-3 flex justify-end gap-2">
            <button onClick={() => setOpen(null)} className="rounded-md px-2 py-1 text-[12px] text-muted hover:text-fg">
              Cancel
            </button>
            <button
              onClick={go}
              disabled={blocked || busy}
              title={blocked ? 'Pick what it should be sound in first' : undefined}
              className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-[12px] text-fg disabled:opacity-40"
            >
              {busy && <Loader2 size={12} className="animate-spin" />} Launch
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
