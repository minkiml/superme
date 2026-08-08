import { useLayoutEffect, useRef, useState } from 'react'

// The raw-markdown editor behind every "Edit" button — skills, agents, constitutions, anchor docs,
// work-item artifacts. One component, because it was five copies of the same class string and the
// problem below had to be solved in all of them.
//
// These files are hand-authored HARD-wrapped at ~100 columns, so the newlines in them are the
// author's own. That leaves a box narrower than 100 columns with only bad options: soft-wrap and
// every authored line gets re-broken a second time (a three-line paragraph arrives as six ragged
// ones, and you can no longer tell an authored break from a rendered one), or refuse to wrap and
// read it through a horizontal scrollbar.
//
// So take the third option: FIT THE COLUMN, not the text. The font is scaled so that 100 columns
// occupy exactly the width available, which means no body line ever reaches the wrap point — the
// file reads exactly as written — while wrapping stays ON, so the occasional genuinely long line
// (a YAML `description:`, a URL) folds instead of pushing out a scrollbar. Re-measured on resize,
// so it holds in a widened modal and in a narrow drilldown column alike.
//
// Rewrapping the text itself was the other candidate and is rejected: these files are prompts under
// version control, and an editor that reflows paragraphs on open would rewrite lines the person
// never touched.

/** The column these files are hand-wrapped at. Fitting it is the whole trick. */
const TARGET_COLS = 100
// Floor and ceiling. Below the floor the text stops being comfortable to read, and a very narrow
// frame is allowed to wrap rather than shrink to nothing; above the ceiling a wide modal would
// blow the source up larger than the surrounding chrome.
const MIN_PX = 9.5
const MAX_PX = 13.5

export default function SourceEditor({
  value, onChange, tone = 'accent', surface = 'bg-surface', className = '',
}: {
  value: string
  onChange: (next: string) => void
  /** Focus-ring colour — matches the surrounding surface's accent (`dev` inside the dev tabs). */
  tone?: 'accent' | 'dev'
  /** Background token, so the editor sits on the same plane as the panel that owns it. */
  surface?: string
  className?: string
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [fontPx, setFontPx] = useState<number | null>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const fit = () => {
      const cs = getComputedStyle(el)
      // Measure the target column in THIS textarea's own font at a large reference size, so the
      // ratio is exact for whatever monospace stack actually resolved — never a guessed 0.6em.
      const probe = document.createElement('span')
      probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font-size:100px'
      probe.style.fontFamily = cs.fontFamily
      probe.textContent = '0'.repeat(TARGET_COLS)
      document.body.appendChild(probe)
      const widthAt100px = probe.getBoundingClientRect().width
      probe.remove()
      if (!widthAt100px) return
      const avail = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
      setFontPx(Math.max(MIN_PX, Math.min(MAX_PX, (avail / widthAt100px) * 100)))
    }
    fit()
    // Font size never changes the element's width (it is 100%), so this cannot feed back on itself.
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <textarea
      ref={ref}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      spellCheck={false}
      // Until the first measurement lands, fall back to the size this editor used before it
      // learned to fit — one frame of slightly-wrong text beats one frame of none.
      style={{ fontSize: `${fontPx ?? 12.5}px` }}
      className={`h-[60vh] w-full resize-none overflow-y-auto whitespace-pre-wrap break-words
        rounded-md border border-line ${surface} p-3 font-mono leading-relaxed text-fg outline-none
        ${tone === 'dev' ? 'focus:border-dev' : 'focus:border-accent'} ${className}`}
    />
  )
}
