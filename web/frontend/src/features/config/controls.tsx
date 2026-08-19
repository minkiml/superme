import { useEffect, useState, type ReactNode } from 'react'
import { Check, Loader2 } from 'lucide-react'

// The shared control vocabulary of the System config popup.
//
// Two things live here rather than in each section, and both are alignment rules: a settings pane
// is read down its RIGHT edge, so every control in it must start at the same x. A picker sized to
// its own word ("Opus 5" vs "System default") puts every row's control somewhere different, which
// is exactly what made the first draft look unfinished. Widths are fixed per ROLE, not per value.

/** The primary picker on a row (model, review mode, anchor). */
export const W_MAIN = 'w-36'
/** The secondary picker beside it (effort). */
export const W_SUB = 'w-32'
/** Wider, for pickers whose options include a sentence-ish value ("System default"). */
export const W_WIDE = 'w-44'

/**
 * One labeled setting: name + hint on the left, its control cluster on the right.
 *
 * MODULE-LEVEL on purpose. Defining this inside a card component gives it a new identity every
 * render, and a poll re-renders the pane every few seconds — React then REMOUNTS the row's subtree
 * each cycle, resetting inputs and stealing focus mid-typing.
 */
export function ConfigRow({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <div className="text-[14px] text-fg">{title}</div>
        {hint && <div className="text-[12px] text-faint">{hint}</div>}
      </div>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
    </div>
  )
}

/** A settings card — rows separated by hairlines. */
export function Card({ children }: { children: ReactNode }) {
  return <div className="space-y-3 rounded-xl border border-line bg-surface p-4">{children}</div>
}

export const Divider = () => <div className="h-px bg-line" />

/** A section heading inside a pane, with an optional explanatory line under it. */
export function SectionLabel({ title, hint }: { title: string; hint?: string }) {
  return (
    <>
      <div className="mb-1 mt-7 text-[12px] font-semibold uppercase tracking-wider text-muted first:mt-0">{title}</div>
      {hint && <p className="mb-3 text-[12px] leading-relaxed text-faint">{hint}</p>}
    </>
  )
}

/** The pane's own header: what you are configuring, and at what scope. */
export function PaneHead({ title, scope, lede }: { title: string; scope: string; lede: string }) {
  const system = scope === 'System' || scope === 'System artifacts'
  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <h2 className="text-[17px] font-semibold text-fg">{title}</h2>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ${
            system ? 'bg-hover text-muted' : 'bg-dev/15 text-dev'
          }`}
        >
          {scope}
        </span>
      </div>
      <p className="mb-5 mt-1 text-[12.5px] leading-relaxed text-faint">{lede}</p>
    </>
  )
}

/** The draft footer for a card whose edits stage before they are written. */
export function ApplyBar({ dirty, saving, onReset, onApply }: {
  dirty: boolean; saving: boolean; onReset: () => void; onApply: () => void
}) {
  return (
    <div className="flex items-center justify-end gap-2 border-t border-line pt-3">
      {dirty && !saving && (
        <button onClick={onReset} className="rounded-md px-2.5 py-1.5 text-[13px] text-muted hover:text-fg">
          Reset
        </button>
      )}
      <button
        onClick={onApply}
        disabled={!dirty || saving}
        className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-[13px] font-medium text-on-accent transition enabled:hover:opacity-90 disabled:opacity-40"
      >
        {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Apply
      </button>
    </div>
  )
}

export const Loading = () => (
  <div className="flex items-center gap-2 text-sm text-muted">
    <Loader2 size={14} className="animate-spin" /> Loading…
  </div>
)

/**
 * A compact labeled integer stepper with a unit suffix.
 *
 * Two rules, and they fought each other twice:
 *
 * 1. The text is FREE while typing — CLAMPING per keystroke makes a field with a high `min`
 *    untypeable (typing "60" into min=26 became 26 → 260 → 95).
 * 2. But an in-range keystroke must still REACH the parent, because the parent's `dirty` flag is
 *    what enables Apply. Deferring every commit to blur/Enter (the fix for rule 1) broke that: the
 *    owner typed 55, Apply stayed grey, and clicking it did nothing — the mousedown blurs the input
 *    and commits, but the browser does not dispatch a click to a button that was disabled when the
 *    press began, so the first click was always swallowed.
 *
 * So: propagate on every keystroke that parses INTO range, and clamp only on blur/Enter. An
 * out-of-range draft (mid-typing "6" toward "60") stays local until then, which is rule 1 intact.
 */
export function NumberField({ value, min, max, unit, onChange }: {
  value: number; min: number; max: number; unit: string; onChange: (v: number) => void
}) {
  const clamp = (v: number) => Math.max(min, Math.min(max, v))
  const [text, setText] = useState(String(value))
  useEffect(() => { setText(String(value)) }, [value])  // ± / reset / apply re-sync the draft
  function type(raw: string) {
    setText(raw)
    const n = parseInt(raw, 10)
    if (!Number.isNaN(n) && n >= min && n <= max && n !== value) onChange(n)
  }
  function commit() {
    const n = parseInt(text, 10)
    const v = clamp(Number.isNaN(n) ? value : n)
    setText(String(v))  // explicit — the effect won't fire when the clamped value is unchanged
    if (v !== value) onChange(v)
  }
  const btn = 'grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line text-muted hover:bg-hover hover:text-fg'
  return (
    // Fixed widths so the −/value/unit/+ columns line up across every row.
    <div className="flex shrink-0 items-center gap-1.5">
      <button onClick={() => onChange(clamp(value - 1))} className={btn}>−</button>
      <div className="flex w-[5.25rem] items-baseline gap-1 rounded-md border border-line bg-sunken px-2 py-1">
        <input
          type="number"
          value={text}
          min={min}
          max={max}
          onChange={(e) => type(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
          className="min-w-0 flex-1 bg-transparent text-right text-[13px] tabular-nums text-fg outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
        />
        <span className="w-8 shrink-0 text-left text-[11px] text-faint">{unit}</span>
      </div>
      <button onClick={() => onChange(clamp(value + 1))} className={btn}>+</button>
    </div>
  )
}

/** The deputy's four rungs. Filled up to and including the selected one; click a rung to set it. */
const STRICTNESS_ORDER = ['low', 'medium', 'high', 'extra'] as const

export function GaugeBar({ level, onPick }: { level: string; onPick: (l: string) => void }) {
  const idx = Math.max(0, STRICTNESS_ORDER.indexOf(level as (typeof STRICTNESS_ORDER)[number]))
  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-1">
        {STRICTNESS_ORDER.map((l, i) => (
          <button
            key={l}
            type="button"
            onClick={() => onPick(l)}
            title={l}
            aria-label={l}
            className={`h-4 w-7 rounded-sm transition-colors ${
              i <= idx ? (i === 3 ? 'bg-warn' : 'bg-accent') : 'bg-line hover:bg-hover'
            }`}
          />
        ))}
      </div>
      <span className="w-12 text-[11px] capitalize text-faint">{level}</span>
    </div>
  )
}
